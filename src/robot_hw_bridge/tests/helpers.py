"""Test yardımcıları: sahte saat + host tarafını taklit eden sürücü (simülatörü doğrudan, gerçek zaman
beklemeden sınar)."""
from __future__ import annotations

from typing import List, Optional

from robot_hw_bridge.codec import Frame, FrameDecoder, encode_frame
from robot_hw_bridge.protocol import Ack, Fault, Hello, MsgType, Result, Status, pack_cmd
from robot_hw_bridge.sim import Esp32Sim, SimAxis


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class Host:
    """Simülatöre bayt gönderir, yanıt çerçevelerini toplar. ``adv(dt)`` zamanı ilerletir."""

    def __init__(self, axes: Optional[List[SimAxis]] = None, **kw) -> None:
        self.clock = Clock()
        self.sim = Esp32Sim(axes, clock=self.clock, **kw)
        self.dec = FrameDecoder()
        self.seq = 0
        self.cid = 0
        self.frames: List[Frame] = []
        self.acks: dict = {}

    def raw(self, data: bytes) -> None:
        self.sim.feed(data)
        self._collect()

    def send(self, mtype: MsgType, *, cid: Optional[int] = None, flags: int = 0, **kw) -> Optional[Ack]:
        if mtype in (MsgType.HEARTBEAT, MsgType.HELLO_REQ):
            payload = b'' if mtype == MsgType.HEARTBEAT else bytes([1, 0, 0, 0, 0])
        else:
            if cid is None:
                self.cid += 1
                cid = self.cid
            payload = pack_cmd(mtype, cid, **kw)
        self.raw(encode_frame(Frame(mtype, self.seq, 0, payload, flags)))
        self.seq = (self.seq + 1) & 0xFFFF
        return self.acks.get(cid) if mtype not in (MsgType.HEARTBEAT, MsgType.HELLO_REQ) else None

    def adv(self, dt: float, beat: bool = False, step: float = 0.05) -> None:
        """``dt`` saniye ilerle; ``beat``: her ``step``'te kalp atışı gönder (sağlıklı host)."""
        end = self.clock.t + dt
        while self.clock.t < end - 1e-9:
            self.clock.t = min(end, self.clock.t + step)
            if beat:
                self.send(MsgType.HEARTBEAT)
            else:
                self._collect()

    def _collect(self) -> None:
        for f in self.dec.feed(self.sim.poll()):
            self.frames.append(f)
            if f.type == MsgType.ACK:
                a = Ack.unpack(f.payload)
                self.acks[a.cmd_id] = a

    # --- okuma
    def status(self) -> Status:
        self._collect()
        for f in reversed(self.frames):
            if f.type == MsgType.STATUS:
                return Status.unpack(f.payload)
        raise AssertionError('STATUS yok')

    def faults(self) -> List[Fault]:
        return [Fault.unpack(f.payload) for f in self.frames if f.type == MsgType.FAULT]

    def hello(self) -> Hello:
        self.send(MsgType.HELLO_REQ)
        for f in reversed(self.frames):
            if f.type == MsgType.HELLO:
                return Hello.unpack(f.payload)
        raise AssertionError('HELLO yok')

    def result(self, mtype: MsgType, **kw) -> Result:
        a = self.send(mtype, **kw)
        assert a is not None, 'ACK gelmedi'
        return a.result

    def home_all(self, mask: int = 0x3F) -> None:
        assert self.result(MsgType.CMD_HOME, mask=mask) == Result.OK
        self.adv(3.0, beat=True)

    def ready(self, mask: int = 0x3F) -> None:
        """Açılıştan ENABLED'a: home + enable (sağlıklı host)."""
        self.send(MsgType.HEARTBEAT)
        self.home_all(mask)
        assert self.result(MsgType.CMD_ENABLE) == Result.OK
