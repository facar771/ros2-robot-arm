"""ESP32 yazılım simülatörü: firmware'in (B3) uyacağı davranışın **çalıştırılabilir belirtimi**.

Gerçek donanım yok; bu sınıf aynı çerçeve şemasını, durum makinesini (docs/HARDWARE.md §4), iki yönlü
watchdog'u ve NC-döngü acil durunu taklit eder. Saf mantık: ``feed(bayt)`` girdi alır, ``poll()`` zamanı
ilerletip gönderilecek baytları döndürür (saat enjekte edilebilir: testler gerçek zaman beklemez).
Fiziğin tamamı basittir (hız/ivme sınırlı, adım kaybı enjekte edilebilir); amaç protokol ve güvenlik
davranışıdır, motor dinamiği değil.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from robot_hw_bridge.codec import Frame, FrameDecoder, SeqTracker, encode_frame
from robot_hw_bridge.protocol import (
    CMD_TYPES,
    WATCHDOG_DEFAULT_MS,
    WATCHDOG_MAX_MS,
    WATCHDOG_MIN_MS,
    Ack,
    AxisFlag,
    AxisInfo,
    AxisStatus,
    ConfigKey,
    EncoderMode,
    EstopReason,
    Fault,
    FaultCode,
    Hello,
    MsgType,
    ProtocolError,
    Result,
    State,
    Status,
    StatusFlag,
    axes_in_mask,
    cmd_id_of,
    unpack_cmd,
)

MAX_SPEED = 20000            # adım/sn (sim tavanı)
HOME_SPEED = 2000.0
SUBSTEP_S = 0.005


@dataclass
class SimAxis:
    """Bir eksenin donanım tanımı (HELLO'ya yansır) ve fiziksel sınırları."""
    encoder: EncoderMode = EncoderMode.NONE
    has_home_switch: bool = True
    phys_min: float = -500.0          # mekanik/limit anahtarı konumu (fiziksel adım)
    phys_max: float = 100000.0
    home_pos: float = 0.0             # home anahtarının fiziksel konumu
    follow_err_steps: int = 200
    follow_err_ms: int = 100

    @property
    def info(self) -> AxisInfo:
        return AxisInfo(self.encoder, self.has_home_switch, True, True)


class _Axis:
    def __init__(self, cfg: SimAxis):
        self.cfg = cfg
        self.phys = cfg.home_pos + 1000.0      # güç açıldığında konum bilinmiyor: homing anahtarından uzak
        self.origin = 0.0
        self.loss = 0.0                        # fiziksel gecikme (kaybolan adımlar)
        self.cmd = 0.0                         # mantıksal komut konumu
        self.vel = 0.0
        self.vmax = 0.0
        self.accel = 0.0
        self.target: Optional[float] = None
        self.mode = 'idle'                     # idle | move | stop | homing
        self.homed = False
        self.soft_min = 0
        self.soft_max = int(cfg.phys_max - cfg.home_pos - 100)
        self.follow_steps = cfg.follow_err_steps
        self.follow_ms = cfg.follow_err_ms
        self.follow_since: Optional[float] = None
        self.limit_min = False
        self.limit_max = False

    @property
    def moving(self) -> bool:
        return self.mode != 'idle'

    def measured(self) -> float:
        return self.phys - self.origin


def default_axes() -> List[SimAxis]:
    """6 eksen, karışık donanım (belgedeki örnek): 3 encoder'sız (anahtarlı), 2 artımlı, 1 mutlak."""
    return [SimAxis(EncoderMode.NONE), SimAxis(EncoderMode.NONE), SimAxis(EncoderMode.INCREMENTAL),
            SimAxis(EncoderMode.INCREMENTAL), SimAxis(EncoderMode.ABSOLUTE, has_home_switch=False),
            SimAxis(EncoderMode.NONE)]


class Esp32Sim:
    def __init__(self, axes: Optional[Sequence[SimAxis]] = None, *,
                 clock: Callable[[], float] = time.monotonic, status_period_s: float = 0.05,
                 watchdog_ms: int = WATCHDOG_DEFAULT_MS, key: Optional[bytes] = None,
                 device_id: bytes = b'SIM-ESP32', boot_count: int = 1,
                 hw_loop_closed: bool = True, fw: tuple = (0, 1)):
        self._clock = clock
        self.axes = [_Axis(a) for a in (axes if axes is not None else default_axes())]
        self.status_period = status_period_s
        self.watchdog_ms = watchdog_ms
        self.key = key
        self.device_id, self.boot_count, self.fw = device_id, boot_count, fw
        self.hw_loop_closed = hw_loop_closed
        self.state = State.UNHOMED
        self.reason = EstopReason.NONE
        if not hw_loop_closed:
            self.state, self.reason = State.ESTOPPED, EstopReason.BOOT
        self._t0 = clock()
        self._last = self._t0
        self._next_status = self._t0
        self._decoder = FrameDecoder(key)
        self._rx_seq = SeqTracker(strict=key is not None)
        self._tx_seq = 0
        self._last_host_rx: Optional[float] = None
        self._link_ok = False
        self._out = bytearray()
        self._acks: Dict[int, Ack] = {}               # cmd_id -> yanıt (yinelenen komutlar yeniden ACK'lenir)
        self._ack_order: List[int] = []
        self.counters = {'estops': 0, 'watchdog_trips': 0, 'cmds': 0, 'dup_cmds': 0, 'frames_rx': 0}
        self.events: List[Fault] = []

    # ------------------------------------------------------------------ test/geliştirme kancaları
    def press_estop(self) -> None:
        """NC döngü açılır (buton basıldı ya da kablo koptu): sürücüler DONANIM düzeyinde kesilir."""
        self.hw_loop_closed = False
        self._enter_estop(EstopReason.HW_BUTTON, FaultCode.HW_ESTOP_PRESSED)

    def release_estop(self) -> None:
        """Döngü kapanır. Durum ESTOPPED kalır: kilit yalnızca CMD_CLEAR ile açılır."""
        self.hw_loop_closed = True

    def inject_step_loss(self, axis: int, steps: float) -> None:
        """Eksen fiziksel olarak ``steps`` adım geride kalır (encoder'lı eksen görür, encoder'sız görmez)."""
        self.axes[axis].loss += steps
        self.axes[axis].phys -= steps

    def true_position(self, axis: int) -> float:
        """Gerçek (fiziksel) mantıksal konum: testlerde açık döngü hatasını göstermek için."""
        return self.axes[axis].measured()

    # ------------------------------------------------------------------ G/Ç
    def feed(self, data: bytes) -> None:
        self._advance()
        for frame in self._decoder.feed(data):
            self.counters['frames_rx'] += 1
            gap, fresh = self._rx_seq.observe(frame.seq)
            if not fresh:
                continue
            self._last_host_rx = self._clock()
            if not self._link_ok:
                self._link_ok = True
            self._handle(frame)

    def poll(self) -> bytes:
        """Zamanı ilerletir; gönderilecek baytları (ACK/STATUS/FAULT...) döndürür ve tamponu boşaltır."""
        self._advance()
        now = self._clock()
        if now >= self._next_status:
            self._emit_status()
            self._next_status = now + self.status_period
        out, self._out = bytes(self._out), bytearray()
        return out

    @property
    def decoder_stats(self) -> Dict[str, int]:
        return self._decoder.stats.as_dict()

    @property
    def seq_stats(self) -> Dict[str, int]:
        return {'lost': self._rx_seq.lost, 'duplicates': self._rx_seq.duplicates,
                'replays': self._rx_seq.replays}

    # ------------------------------------------------------------------ gönderim
    def _send(self, mtype: MsgType, payload: bytes) -> None:
        ack = self._rx_seq.last if self._rx_seq.last is not None else 0
        self._out += encode_frame(Frame(mtype, self._tx_seq, ack, payload), self.key)
        self._tx_seq = (self._tx_seq + 1) & 0xFFFF

    def _hello(self) -> Hello:
        return Hello(1, self.fw[0], self.fw[1], self.boot_count, self.device_id,
                     tuple(a.cfg.info for a in self.axes))

    def _status(self) -> Status:
        flags = 0
        if not self.hw_loop_closed:
            flags |= StatusFlag.HW_LOOP_OPEN
        if self.reason == EstopReason.WATCHDOG:
            flags |= StatusFlag.WATCHDOG_TRIPPED
        if any(a.cfg.encoder == EncoderMode.NONE for a in self.axes):
            flags |= StatusFlag.OPEN_LOOP_ANY
        if self._link_ok:
            flags |= StatusFlag.HOST_LINK_OK
        axes = []
        for a in self.axes:
            f = 0
            f |= AxisFlag.MOVING if a.moving else 0
            f |= AxisFlag.HOMED if a.homed else 0
            f |= AxisFlag.ENABLED if self.state == State.ENABLED else 0
            f |= AxisFlag.LIMIT_MIN if a.limit_min else 0
            f |= AxisFlag.LIMIT_MAX if a.limit_max else 0
            f |= AxisFlag.OPEN_LOOP if a.cfg.encoder == EncoderMode.NONE else 0
            enc = 0 if a.cfg.encoder == EncoderMode.NONE else int(round(a.measured()))
            axes.append(AxisStatus(int(round(a.cmd)), enc, f))
        up = int((self._clock() - self._t0) * 1000) & 0xFFFFFFFF
        return Status(self.state, self.reason, flags, up, self.watchdog_ms, tuple(axes))

    def _emit_status(self) -> None:
        self._send(MsgType.STATUS, self._status().pack())

    def _fault(self, code: FaultCode, axis: int = 255, detail: int = 0) -> None:
        up = int((self._clock() - self._t0) * 1000) & 0xFFFFFFFF
        f = Fault(code, axis, detail, up)
        self.events.append(f)
        self._send(MsgType.FAULT, f.pack())

    def _reply(self, cmd_id: int, result: Result, detail: int = 0) -> None:
        ack = Ack(cmd_id & 0xFFFF, result, detail)
        if cmd_id >= 0:
            self._acks[cmd_id] = ack
            self._ack_order.append(cmd_id)
            if len(self._ack_order) > 32:
                self._acks.pop(self._ack_order.pop(0), None)
        self._send(MsgType.ACK, ack.pack())
        self._emit_status()                      # durum değiştiyse host hemen görsün

    # ------------------------------------------------------------------ komutlar
    def _handle(self, f: Frame) -> None:
        try:
            mtype = MsgType(f.type)
        except ValueError:
            if 0x10 <= f.type <= 0x1F:
                self._reply(cmd_id_of(f.payload), Result.UNKNOWN_CMD)
            return                                # bilinmeyen/yanlış yönlü tür: yok say
        if mtype == MsgType.HEARTBEAT:
            return
        if mtype == MsgType.HELLO_REQ:
            self._send(MsgType.HELLO, self._hello().pack())
            return
        if mtype not in CMD_TYPES:
            return                                # MCU->host türü host'tan gelirse yok say
        cid = cmd_id_of(f.payload)
        if cid in self._acks:                     # yineleme: yeniden ACK, yeniden işlem YOK
            self.counters['dup_cmds'] += 1
            self._send(MsgType.ACK, self._acks[cid].pack())
            return
        try:
            args = unpack_cmd(mtype, f.payload)
        except ProtocolError:
            self._reply(cid, Result.BAD_ARG)
            return
        self.counters['cmds'] += 1
        self._advance()
        res = self._exec(mtype, args)
        self._reply(cid, res)

    def _exec(self, t: MsgType, a: dict) -> Result:
        if t == MsgType.CMD_ESTOP:
            self._enter_estop(EstopReason.HOST_CMD, None)
            return Result.OK
        if t == MsgType.CMD_CLEAR:
            return self._clear()
        if self.state == State.ESTOPPED:
            return Result.ESTOP_ACTIVE
        if t == MsgType.CMD_STOP:
            for i in axes_in_mask(a['mask'], len(self.axes)):
                ax = self.axes[i]
                if ax.mode in ('move', 'homing'):
                    ax.mode, ax.target = 'stop', None
            return Result.OK
        if t == MsgType.CMD_CONFIG:
            return self._config(a)
        if t == MsgType.CMD_HOME:
            return self._home(a['mask'])
        if t == MsgType.CMD_ENABLE:
            if self.state == State.ENABLED:
                return Result.OK
            if self.state != State.HOMED:
                if any(not x.cfg.info.referenceable for x in self.axes):
                    return Result.NO_REFERENCE
                return Result.BAD_STATE
            if not self.hw_loop_closed:
                return Result.HW_LOOP_OPEN
            self.state = State.ENABLED
            return Result.OK
        if t == MsgType.CMD_DISABLE:
            if self.state != State.ENABLED:
                return Result.OK if self.state == State.HOMED else Result.BAD_STATE
            if any(x.moving for x in self.axes):
                return Result.BUSY
            self.state = State.HOMED
            return Result.OK
        if t == MsgType.CMD_MOVE:
            return self._move(a)
        return Result.UNKNOWN_CMD

    def _home(self, mask: int) -> Result:
        if self.state not in (State.UNHOMED, State.HOMED):
            return Result.BAD_STATE
        idx = axes_in_mask(mask, len(self.axes))
        if not idx or mask >> len(self.axes):
            return Result.BAD_ARG
        if any(not self.axes[i].cfg.info.referenceable for i in idx):
            return Result.NO_REFERENCE            # anahtarsız+encoder'sız eksen güvenli referanslanamaz
        if not self.hw_loop_closed:
            return Result.HW_LOOP_OPEN
        for i in idx:
            ax = self.axes[i]
            ax.homed = False
            if ax.cfg.encoder == EncoderMode.ABSOLUTE and not ax.cfg.has_home_switch:
                ax.origin, ax.cmd, ax.homed = ax.cfg.home_pos, 0.0, True    # mutlak encoder: anında
                ax.phys = ax.origin
            else:
                ax.mode = 'homing'
        self.state = State.HOMING
        self._maybe_finish_homing()
        return Result.OK

    def _maybe_finish_homing(self) -> None:
        if self.state == State.HOMING and not any(a.mode == 'homing' for a in self.axes):
            self.state = State.HOMED if all(a.homed for a in self.axes) else State.UNHOMED

    def _move(self, a: dict) -> Result:
        if self.state != State.ENABLED:
            return Result.BAD_STATE
        i = a['axis']
        if i >= len(self.axes):
            return Result.BAD_ARG
        ax = self.axes[i]
        if not 0 < a['vmax'] <= MAX_SPEED or not 0 < a['accel'] <= 1_000_000:
            return Result.BAD_ARG
        if not ax.soft_min <= a['target'] <= ax.soft_max:
            return Result.LIMIT
        if ax.mode == 'homing':
            return Result.BUSY
        ax.target, ax.vmax, ax.accel, ax.mode = float(a['target']), float(a['vmax']), \
            float(a['accel']), 'move'
        return Result.OK

    def _config(self, a: dict) -> Result:
        try:
            key = ConfigKey(a['key'])
        except ValueError:
            return Result.BAD_ARG
        if self.state == State.ENABLED and key in (ConfigKey.SOFT_MIN, ConfigKey.SOFT_MAX):
            return Result.BAD_STATE
        v = a['value']
        if key == ConfigKey.WATCHDOG_MS:
            if not WATCHDOG_MIN_MS <= v <= WATCHDOG_MAX_MS:
                return Result.BAD_ARG
            self.watchdog_ms = v
            return Result.OK
        if a['axis'] >= len(self.axes):
            return Result.BAD_ARG
        ax = self.axes[a['axis']]
        if key == ConfigKey.SOFT_MIN:
            ax.soft_min = v
        elif key == ConfigKey.SOFT_MAX:
            ax.soft_max = v
        elif key == ConfigKey.FOLLOW_ERR_STEPS and v > 0:
            ax.follow_steps = v
        elif key == ConfigKey.FOLLOW_ERR_MS and v > 0:
            ax.follow_ms = v
        else:
            return Result.BAD_ARG
        return Result.OK

    def _clear(self) -> Result:
        if self.state != State.ESTOPPED:
            return Result.OK
        if not self.hw_loop_closed:
            return Result.HW_LOOP_OPEN            # NC döngü açıkken yazılımla kilit açılamaz
        for ax in self.axes:
            ax.mode, ax.target, ax.vel = 'idle', None, 0.0
            if ax.cfg.encoder != EncoderMode.ABSOLUTE:
                ax.homed = False                  # adım kaybı bilinemez: yeniden homing şart
        self.reason = EstopReason.NONE
        self.state = State.HOMED if all(a.homed for a in self.axes) else State.UNHOMED
        self._link_ok = True
        return Result.OK

    # ------------------------------------------------------------------ acil dur / fizik
    def _enter_estop(self, reason: EstopReason, fault: Optional[FaultCode], axis: int = 255,
                     detail: int = 0) -> None:
        if self.state != State.ESTOPPED:
            self.counters['estops'] += 1
        self.state, self.reason = State.ESTOPPED, reason
        for ax in self.axes:
            ax.mode, ax.target, ax.vel = 'idle', None, 0.0       # sürücüler kesildi: ramp yok
            if ax.cfg.encoder == EncoderMode.NONE:
                pass                                              # konum bilgisi zaten tahmin
        if fault is not None:
            self._fault(fault, axis, detail)
        self._emit_status()

    def _advance(self) -> None:
        now = self._clock()
        dt = now - self._last
        self._last = now
        # watchdog: host sustu ve sürücüler açık -> ESTOPPED (kilitli)
        if self._last_host_rx is not None:
            silent = now - self._last_host_rx
            if silent * 1000 > self.watchdog_ms:
                self._link_ok = False
                if self.state in (State.ENABLED, State.HOMING) and self.state != State.ESTOPPED:
                    self.counters['watchdog_trips'] += 1
                    self._enter_estop(EstopReason.WATCHDOG, FaultCode.WATCHDOG, 255, int(silent * 1000))
        while dt > 1e-9:
            step = min(dt, SUBSTEP_S)
            self._physics(step, now)
            dt -= step

    def _physics(self, dt: float, now: float) -> None:
        if self.state == State.ESTOPPED:
            return
        for i, ax in enumerate(self.axes):
            if ax.mode == 'idle':
                self._check_follow(i, ax, now)
                continue
            if ax.mode == 'homing':
                ax.phys -= HOME_SPEED * dt
                if ax.phys <= ax.cfg.home_pos:
                    ax.phys, ax.origin, ax.cmd, ax.vel = ax.cfg.home_pos, ax.cfg.home_pos, 0.0, 0.0
                    ax.homed, ax.mode = True, 'idle'
                    self._maybe_finish_homing()
                continue
            if ax.mode == 'stop':
                ax.target = None
                ax.vel = max(0.0, abs(ax.vel) - (ax.accel or 50000.0) * dt) * (1 if ax.vel >= 0 else -1)
                if ax.vel == 0.0:
                    ax.mode = 'idle'
            else:
                d = (ax.target or 0.0) - ax.cmd
                if abs(d) < 0.5 and abs(ax.vel) < 1.0:
                    ax.cmd, ax.vel, ax.mode = ax.target or 0.0, 0.0, 'idle'
                else:
                    brake = math.sqrt(max(0.0, 2 * ax.accel * abs(d)))
                    vt = math.copysign(min(ax.vmax, brake), d)
                    dv = max(-ax.accel * dt, min(ax.accel * dt, vt - ax.vel))
                    ax.vel += dv
            dx = ax.vel * dt
            if ax.mode == 'move' and ax.target is not None and abs(ax.target - ax.cmd) <= abs(dx):
                dx = ax.target - ax.cmd
            ax.cmd += dx
            ax.phys += dx
            self._check_limits(i, ax)
            self._check_follow(i, ax, now)
            if self.state == State.ESTOPPED:
                return

    def _check_limits(self, i: int, ax: _Axis) -> None:
        ax.limit_min = ax.phys <= ax.cfg.phys_min
        ax.limit_max = ax.phys >= ax.cfg.phys_max
        if ax.limit_min or ax.limit_max:
            self._enter_estop(EstopReason.LIMIT_VIOLATION, FaultCode.LIMIT_HIT, i, int(ax.phys))

    def _check_follow(self, i: int, ax: _Axis, now: float) -> None:
        if ax.cfg.encoder == EncoderMode.NONE or not ax.homed or self.state != State.ENABLED:
            ax.follow_since = None
            return
        err = abs(ax.cmd - ax.measured())
        if err <= ax.follow_steps:
            ax.follow_since = None
            return
        if ax.follow_since is None:
            ax.follow_since = now
        elif (now - ax.follow_since) * 1000 >= ax.follow_ms:
            self._enter_estop(EstopReason.FOLLOWING_ERROR, FaultCode.FOLLOWING_ERROR, i, int(err))
