"""SİM kabul testlerinin ortak yardımcıları (sahte page/ctx, örnek sonuçlar)."""
from __future__ import annotations

import base64
from types import SimpleNamespace

from tools.acceptance_core import (
    FAIL,
    PASS,
    SKIP,
    Ctx,
    Result,
    Step,
)

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHnOcQAAAAABJRU5ErkJggg==')
IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
RECT = {'l': 100.0, 't': 50.0, 'w': 400.0, 'h': 200.0}


class FakePage:
    def __init__(self, fail_shot: bool = False) -> None:
        self.fail_shot = fail_shot

    def screenshot(self, path: str, full_page: bool = False) -> None:
        if self.fail_shot:
            raise RuntimeError('ekran alınamadı')
        with open(path, 'wb') as fh:
            fh.write(PNG)


def make_runner(tmp_path, fail_shot: bool = False):
    made = []

    def make_ctx(step_id: str) -> Ctx:
        c = Ctx(FakePage(fail_shot), None, tmp_path / 'shots', '', closer=lambda: made.append(step_id))
        return c

    return make_ctx, made


def S(step_id, fn, section='A', last=False) -> Step:
    return Step(step_id, f'Başlık {step_id}', fn, section, last)


def ok(c: Ctx) -> None:
    c.measure('değer', 1.5)


def bad_fail(c: Ctx) -> None:
    c.measure('J1 (°)', 10.0)
    c.near('J1 sonra (°)', 10.0, 20.0, 0.5, '°')


def bad_exc(c: Ctx) -> None:
    raise TimeoutError('düğme 20 sn içinde etkinleşmedi\nikinci satır')


def R(status: str) -> Result:
    return Result('i', 't', status)


def sample_results():
    return [Result('a.ok', 'Tamam', PASS, {'J1': '20.00 ° (beklenen 20.00 ± 0.5)'},
                   ['a_ok__son.png'], '', 1.2),
            Result('b.bad', 'Bozuk <b>', FAIL, {'x | y': '<script>alert(1)</script>'}, [],
                   'ölçülen 10 beklenen 20 <img src=x onerror=alert(1)>', 0.4),
            Result('c.skip', 'Atlanan', SKIP, note='--skip ile atlandı')]


META = {'Hub': 'python -m control_hub --sim', 'Not': '<tahmini> & SİM'}


def console_msg(text: str, url: str, kind: str = 'error'):
    return SimpleNamespace(type=kind, text=text, location={'url': url})
