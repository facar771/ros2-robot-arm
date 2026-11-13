"""Tezgâh (K9_BENCH) simülatörü: `Esp32Sim` + firmware'in `benchCommand()` anlamları
(firmware/uno/lib/k9core/k9_mcu.cpp).

Hub "Tezgâh" sayfası ve `tools/bench` mantığı bununla denenir; GERÇEK firmware ya da motor DEĞİLDİR.
Farklar/varsayımlar: zaman sanaldır (sim saati), adım kaybı yoktur, bobin akımı/ısınma modellenmez.

* `STATUS.flags` `BENCH_MODE` taşır (HARDWARE.md §7).
* 7 eksen; yalnız J1=0, J2=1, J7=6 hareket eder (`BENCH_MASK`), diğerleri komut almaz (`BAD_ARG`).
* `HOME` = tezgâh sıfırı (anında, hareket yok); `ENABLE` yalnız sıfırlanan eksenleri enerjiler;
  gömülü limitler: J1/J2 ±256 adım ≤300 adım/sn ≤300 adım/sn², J7 ±50 adım ≤100/100.
* ACİL DUR / watchdog / DISABLE sıfırı düşürür (`benchAllOff`): yeniden `zero` gerekir.
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence, Set

from robot_hw_bridge.protocol import (
    AxisFlag,
    AxisStatus,
    EncoderMode,
    MsgType,
    Result,
    State,
    Status,
    StatusFlag,
    axes_in_mask,
)
from robot_hw_bridge.sim import Esp32Sim, SimAxis

BENCH_AXES = (0, 1, 6)
BENCH_MASK = 0x43
# eksen -> (alt, üst, azami hız, azami ivme)  (firmware k9_config.h ile AYNI)
HARD: Dict[int, tuple] = {0: (-256, 256, 300, 300), 1: (-256, 256, 300, 300), 6: (-50, 50, 100, 100)}


def bench_axes() -> Sequence[SimAxis]:
    return [SimAxis(EncoderMode.NONE, False, phys_min=-1e6, phys_max=1e6) for _ in range(7)]


class BenchSim(Esp32Sim):
    def __init__(self, axes: Optional[Sequence[SimAxis]] = None, *, bench_flag: bool = True, **kw):
        super().__init__(axes if axes is not None else bench_axes(), **kw)
        self.bench_flag = bench_flag             # False: tezgâh olmayan (hareketsiz) derleme gibi davranır
        self.enabled_axes: Set[int] = set()
        for i, (lo, hi, _, _) in HARD.items():
            self.axes[i].soft_min, self.axes[i].soft_max = lo, hi
            self.axes[i].phys = 0.0

    # --- durum -------------------------------------------------------------------------------------
    def _status(self) -> Status:
        st = super()._status()
        flags = int(st.flags) | (int(StatusFlag.BENCH_MODE) if self.bench_flag else 0)
        axes = []
        for i, a in enumerate(st.axes):
            f = int(a.flags) & ~int(AxisFlag.ENABLED)
            if i in self.enabled_axes and self.state == State.ENABLED:
                f |= int(AxisFlag.ENABLED)
            axes.append(AxisStatus(a.pos, a.enc, f))
        return Status(st.state, st.reason, flags, st.uptime_ms, st.watchdog_ms, tuple(axes))

    def _enter_estop(self, *a, **kw) -> None:
        super()._enter_estop(*a, **kw)
        self.enabled_axes.clear()
        for ax in self.axes:
            ax.homed = False                     # benchAllOff: sıfır düşer

    # --- komutlar ----------------------------------------------------------------------------------
    def _exec(self, t: MsgType, a: dict) -> Result:
        if not self.bench_flag or t in (MsgType.CMD_ESTOP, MsgType.CMD_CLEAR, MsgType.CMD_STOP,
                                        MsgType.CMD_CONFIG) or self.state == State.ESTOPPED:
            return super()._exec(t, a)
        if t == MsgType.CMD_HOME:
            return self._bench_home(a['mask'])
        if t == MsgType.CMD_ENABLE:
            if self.state == State.ENABLED:
                return Result.OK
            homed = {i for i, x in enumerate(self.axes) if x.homed}
            if self.state != State.HOMED or not homed:
                return Result.NO_REFERENCE
            if not self.hw_loop_closed:
                return Result.HW_LOOP_OPEN
            self.enabled_axes = homed
            self.state = State.ENABLED
            return Result.OK
        if t == MsgType.CMD_DISABLE:
            if self.state == State.HOMED:
                return Result.OK
            if self.state != State.ENABLED:
                return Result.BAD_STATE
            if any(self.axes[i].moving for i in BENCH_AXES):
                return Result.BUSY
            self.enabled_axes.clear()
            self.state = State.HOMED
            return Result.OK
        if t == MsgType.CMD_MOVE:
            return self._bench_move(a)
        return Result.UNKNOWN_CMD

    def _bench_home(self, mask: int) -> Result:
        if self.state not in (State.UNHOMED, State.HOMED):
            return Result.BAD_STATE
        if mask == 0 or mask & ~BENCH_MASK:
            return Result.BAD_ARG
        idx = axes_in_mask(mask, len(self.axes))
        if any(self.axes[i].moving for i in idx):
            return Result.BUSY
        for i in idx:
            ax = self.axes[i]
            ax.cmd, ax.origin, ax.homed = 0.0, ax.phys, True      # mevcut konum 0 olur (KESTİRİM)
        self.state = State.HOMED
        return Result.OK

    def _bench_move(self, a: dict) -> Result:
        if self.state != State.ENABLED:
            return Result.BAD_STATE
        i = a['axis']
        if i not in BENCH_AXES:
            return Result.BAD_ARG
        if i not in self.enabled_axes:
            return Result.BAD_STATE
        lo, hi, vmax, accel = HARD[i]
        if a['vmax'] == 0 or a['accel'] == 0:
            return Result.BAD_ARG
        if a['vmax'] > vmax or a['accel'] > accel:
            return Result.LIMIT
        if not lo <= a['target'] <= hi:
            return Result.LIMIT
        ax = self.axes[i]
        if ax.moving:
            return Result.BUSY
        ax.target, ax.vmax, ax.accel, ax.mode = float(a['target']), float(a['vmax']), \
            float(a['accel']), 'move'
        return Result.OK
