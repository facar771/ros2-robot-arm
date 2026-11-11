"""Host test yardımcıları: Uno çekirdeğini (k9_host_driver) derler ve stdin/stdout ile sürer. Test dosyaları paylaşır."""
import subprocess
from pathlib import Path

from robot_hw_bridge.codec import Frame, FrameDecoder, encode_frame
from robot_hw_bridge.protocol import Ack, MsgType, Status, pack_cmd

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / 'uno' / 'lib' / 'k9core'


def build(out: Path, *defs: str) -> Path:
    """Sürücüyü g++ ile derler (``-DK9_BENCH=1`` gibi ek tanımlarla)."""
    subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror', '-O1', '-DK9_HOST_TEST', *defs,
                    f'-I{CORE}', str(HERE / 'k9_host_driver.cpp'), str(CORE / 'k9_mcu.cpp'), '-o', str(out)],
                   check=True)
    return out


class Dev:
    """Sürücü sürecini sarar; host gibi seq'li çerçeve üretir, MCU çıktısını çözer."""

    def __init__(self, binary, *args):
        self.p = subprocess.Popen([str(binary), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.tx_seq = 0
        self.dec = FrameDecoder()
        self.t = 0
        self.cid = 0

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)

    def _io(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()
        resp = self.p.stdout.readline().strip()
        assert resp.startswith('O'), resp
        return bytes.fromhex(resp[2:])

    def raw(self, data: bytes):
        return self.dec.feed(self._io('F ' + data.hex()))

    def send(self, mtype, payload=b'', flags=0, seq=None):
        if seq is None:
            seq = self.tx_seq
            self.tx_seq = (self.tx_seq + 1) & 0xFFFF
        return self.raw(encode_frame(Frame(mtype, seq, 0, payload, flags)))

    def at(self, ms):
        self.t = ms
        return self.dec.feed(self._io(f'T {ms}'))

    def counters(self):
        self.p.stdin.write('S\n')
        self.p.stdin.flush()
        line = self.p.stdout.readline().strip()
        assert line.startswith('C ')
        return {k: int(v) for k, v in (kv.split('=') for kv in line[2:].split())}

    def cmd(self, mtype, **kw):
        self.cid += 1
        frames = self.send(mtype, pack_cmd(mtype, self.cid, **kw))
        return frames, self.cid

    def result(self, mtype, **kw):
        frames, cid = self.cmd(mtype, **kw)
        acks = [Ack.unpack(f.payload) for f in frames if f.type == MsgType.ACK]
        assert len(acks) == 1 and acks[0].cmd_id == cid, frames
        return acks[0]

    def status(self, frames):
        sts = [Status.unpack(f.payload) for f in frames if f.type == MsgType.STATUS]
        return sts[-1] if sts else None

    # --- yalnız K9_BENCH sürücüsü (sahte HAL)
    def loop(self, closed: bool):
        self.dec.feed(self._io(f'L {1 if closed else 0}'))

    def step(self, n: int):
        self.dec.feed(self._io(f'P {n}'))

    def hal(self):
        """[(pos, hedef, güçlü, vmax, accel)] x (J1, J2, J7) ve halt sayısı."""
        self.p.stdin.write('Q\n')
        self.p.stdin.flush()
        v = [int(x) for x in self.p.stdout.readline().split()[1:]]
        return [tuple(v[i * 5:i * 5 + 5]) for i in range(3)], v[15]
