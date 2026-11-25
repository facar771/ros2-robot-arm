"""Kabul betiği için --sim Hub alt süreci + ölçüm kaynağı olarak REST. Ölçümler arayüzden DEĞİL, Hub'ın kendi
durumundan (/api/state) okunur: arayüz yalan söylese bile ölçüm onu yakalar. Yalnız standart kütüphane."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]               # apps/control_hub (`python -m control_hub` cwd)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class HubProc:
    """`python -m control_hub --sim` alt süreci. Geçici veri klasörü: gerçek kullanıcı verisine dokunulmaz."""

    def __init__(self, out_dir: Path, port: int = 0) -> None:
        self.port = port or free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.out_dir = Path(out_dir)
        self.data_dir = Path(tempfile.mkdtemp(prefix='hub_kabul_'))
        self.proc: Optional[subprocess.Popen] = None
        self.log_path = self.out_dir / 'hub.log'

    def start(self, timeout: float = 60.0) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'HUB_DATA_DIR': str(self.data_dir), 'PYTHONUNBUFFERED': '1'}
        cmd = [sys.executable, '-m', 'control_hub', '--sim', '--host', '127.0.0.1', '--port', str(self.port),
               '--access-mode', 'open', '--no-agent', '--no-qr']
        out = self.log_path.open('wb')
        self.proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f'Hub başlamadı (çıkış {self.proc.returncode}); günlük: {self.log_path}')
            try:
                if self.api('/health')[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f'Hub {timeout:.0f} sn içinde /health vermedi; günlük: {self.log_path}')

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ---- REST (hata gövdesini de döndürür: 4xx/5xx istisna DEĞİL, ölçülecek şey) ----
    def api(self, path: str, method: str = 'GET', body: Any = None, timeout: float = 15.0) -> Tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        headers = {'Content-Type': 'application/json'} if data is not None or method != 'GET' else {}
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers)
        if method != 'GET' and data is None:
            req.data = b'{}'
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b'null')
            except ValueError:
                return e.code, {'raw': raw[:200].decode('utf-8', 'replace')}

    def state(self) -> dict:
        return self.api('/api/state')[1]

    def robot(self) -> dict:
        return self.state()['robot']

    def tcp_mm(self) -> List[float]:
        return [v * 1000.0 for v in self.robot()['tcp']['pos_m']]

    def joints_deg(self) -> List[float]:
        return [j['deg'] for j in self.robot()['joints']]

    def wait_idle(self, timeout: float = 40.0, settle: float = 0.35) -> float:
        """Robot hareketsiz olana dek bekler (art arda iki okuma `moving=False`); geçen süreyi döndürür."""
        t0 = time.monotonic()
        calm_since = None
        while time.monotonic() - t0 < timeout:
            rb = self.robot()
            if rb.get('moving') or rb.get('job'):
                calm_since = None
            elif calm_since is None:
                calm_since = time.monotonic()
            elif time.monotonic() - calm_since >= settle:
                return time.monotonic() - t0
            time.sleep(0.1)
        raise TimeoutError(f'robot {timeout:.0f} sn içinde durmadı')

    def reset_home(self) -> None:
        """Her bölüm temiz home'dan başlasın (önceki adımın izi ölçümü bozmasın)."""
        if self.state()['estop']['latched']:             # önceki adım kilitli bıraktıysa: temizle
            self.api('/api/estop/reset', 'POST', {})
        self.wait_idle()
        st, doc = self.api('/api/sim/reset', 'POST', {})
        if st != 200:
            raise RuntimeError(f'SİM sıfırlanamadı: HTTP {st} {doc}')
        self.wait_idle()
