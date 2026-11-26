"""SİM kabul betiğinin çekirdeği (tools/sim_acceptance.py): sonuç modeli, adım kaydı, rapor, 3B izdüşüm.

Bu dosya SAF mantıktır: tarayıcı, Hub ya da ağ yok; bu yüzden birim testlenir (tests/test_sim_acceptance.py).
Yeni bir kabul adımı (ör. P1c klavye) eklemek = bir işlevi `@step('kimlik', 'Başlık', 'Bölüm')` ile süslemek
(docs/KABUL.md).
"""
from __future__ import annotations

import base64
import html
import json
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

PASS, FAIL, SKIP = 'GEÇTİ', 'KALDI', 'ATLANDI'


class Fail(AssertionError):
    """Adımın beklentisi tutmadı (ölçülen değer eşiğin dışında)."""


class Skip(Exception):
    """Adım bu ortamda SINANAMAZ (ör. donanım/görünüm gereği): ATLANDI + SEBEBİ raporlanır, KALDI sayılmaz.
    Yalnız gerçekten sınanamayan durumda kullanılır; sınanabilen ama kırılan şey Fail'dir."""


@dataclass
class Result:
    id: str
    title: str
    status: str
    measured: Dict[str, str] = field(default_factory=dict)
    shots: List[str] = field(default_factory=list)
    note: str = ''
    seconds: float = 0.0


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    fn: Callable
    section: str = ''
    last: bool = False                                   # True: hep en sonda koşar (ör. ACİL DUR)


STEPS: List[Step] = []                       # kayıt sırası = koşum sırası (`last` adımlar sona alınır)


def step(step_id: str, title: str, section: str = '', last: bool = False) -> Callable:
    """Adım kaydı: `@step('kontrol.jog', 'Jog …', 'Kontrol')`. Kimlikler tekil olmalı (`--skip` kullanır)."""
    def deco(fn: Callable) -> Callable:
        if any(s.id == step_id for s in STEPS):
            raise ValueError(f'adım kimliği tekrarlandı: {step_id}')
        STEPS.append(Step(step_id, title, fn, section, last))
        return fn
    return deco


def ordered(steps: Sequence[Step]) -> List[Step]:
    """Bölümler ilk göründükleri sırayla gruplanır (ayrı dosyadan eklenen adım kendi bölümüne oturur; bölüm
    içinde kayıt sırası korunur). `last=True` adımlar (ACİL DUR) hep sona alınır: sonradan eklenenler
    onu bozmaz."""
    first: Dict[str, int] = {}
    for s in steps:
        first.setdefault(s.section, len(first))
    body = sorted((s for s in steps if not s.last), key=lambda s: first[s.section])   # sorted kararlı
    return body + [s for s in steps if s.last]


class Ctx:
    """Bir adımın araçları: ölçüm kaydı, beklenti, ekran görüntüsü. `page`/`hub` dışarıdan verilir."""

    def __init__(self, page, hub, shot_dir: Path, tag: str = '', closer: Optional[Callable] = None) -> None:
        self.page, self.hub, self.shot_dir, self.tag, self._closer = page, hub, Path(shot_dir), tag, closer
        self.measured: Dict[str, str] = {}
        self.shots: List[str] = []
        self.step_id = ''
        self.notes: List[str] = []
        self.errors: List[str] = []                      # tarayıcı: yakalanmamış hata + console.error (CSP)
        self.allowed: List[str] = []                     # BEKLENEN konsol hataları (düzenli ifade)

    def close(self) -> None:
        if self._closer:
            self._closer()

    def measure(self, key: str, value) -> None:
        txt = f'{value:.3f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
        self.measured[key] = txt

    def expect(self, ok: bool, message: str) -> None:
        if not ok:
            raise Fail(message)

    def allow_error(self, pattern: str) -> None:
        """Bu adımda BEKLENEN tarayıcı konsol hatası (düzenli ifade). Ör. ACİL DUR'da sayfanın kendi isteğine
        dönen 423: tarayıcı her 4xx'i `console.error` yazar. Eşleşmeyen her hata yine KALDI yapar."""
        self.allowed.append(pattern)

    def unexpected_errors(self) -> List[str]:
        """Beklenenler süzülür; süzülenlerin sayısı adımın notuna yazılır (gizli kalmaz)."""
        bad = [e for e in self.errors if not any(re.search(p, e) for p in self.allowed)]
        if len(bad) < len(self.errors):
            self.notes.append(f'{len(self.errors) - len(bad)} beklenen tarayıcı hatası süzüldü')
        return bad

    def skip(self, reason: str) -> None:
        """Adımı ATLANDI yap; `reason` raporda yazar (sebepsiz atlama olmaz)."""
        raise Skip(reason)

    def near(self, key: str, got: float, want: float, tol: float, unit: str = '') -> None:
        """Ölçülen `got`, beklenen `want` ± `tol` içinde olmalı; ölçüm raporda HER ZAMAN görünür."""
        self.measure(key, f'{got:.2f} {unit} (beklenen {want:.2f} ± {tol:g})'.replace('  ', ' '))
        if not abs(got - want) <= tol:
            raise Fail(f'{key}: ölçülen {got:.3f}{unit} beklenen {want:.3f}{unit} ± {tol:g}{unit} dışında')

    def shot(self, name: str) -> str:
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        rel = f'{self.step_id.replace(".", "_")}__{name}{self.tag}.png'
        self.page.screenshot(path=str(self.shot_dir / rel), full_page=False)
        self.shots.append(rel)
        return rel


def run_steps(make_ctx: Callable[[str], Ctx], steps: Sequence[Step], skip: Sequence[str] = (),
              only: Sequence[str] = ()) -> List[Result]:
    """Adımları sırayla koşar. Bir adımın istisnası yalnız O adımı KALDI yapar; sonrakiler koşar."""
    results: List[Result] = []
    for s in ordered(steps):
        if only and s.id not in only and s.section not in only:
            continue
        if s.id in skip or s.section in skip:
            results.append(Result(s.id, s.title, SKIP, note='--skip ile atlandı'))
            continue
        t0 = time.monotonic()
        try:
            ctx = make_ctx(s.id)                         # hazırlık hatası KALDI olur, koşum çökmez
        except Exception as exc:                         # noqa: BLE001
            first = str(exc).splitlines()[0] if str(exc) else ''
            note = f'adım başlatılamadı: {type(exc).__name__}: {first}'
            results.append(Result(s.id, s.title, FAIL, note=note, seconds=round(time.monotonic() - t0, 1)))
            continue
        ctx.step_id = s.id
        status, note = PASS, ''
        try:
            s.fn(ctx)
            bad = ctx.unexpected_errors()
            ctx.measure('tarayıcı hataları', len(bad))
            if bad:
                raise Fail(f'tarayıcıda {len(bad)} hata: {bad[0][:160]}')
        except Skip as exc:
            status, note = SKIP, str(exc) or 'sebep belirtilmedi'
        except Fail as exc:
            status, note = FAIL, str(exc)
        except Exception as exc:                         # tarayıcı/Hub hatası da KALDI (sessizce geçmez)
            first = str(exc).splitlines()[0] if str(exc) else ''
            status, note = FAIL, f'{type(exc).__name__}: {first}'
        if status == FAIL:
            try:
                ctx.shot('kaldi')                        # kanıt: kırıldığı andaki ekran
            except Exception:                            # noqa: BLE001 — ekran alınamıyorsa adım zaten KALDI
                pass
        results.append(Result(s.id, s.title, status, dict(ctx.measured), list(ctx.shots),
                              note or '; '.join(ctx.notes), round(time.monotonic() - t0, 1)))
        ctx.close()
    return results


def check_hub_log(text: str) -> Result:
    """Hub günlüğünde Python hata izi (Traceback) var mı: arayüz sunucu hatasını gizlese bile görünür."""
    # Hub biçimi: "<zaman> ERROR <ileti>"; uvicorn: "ERROR:    <ileti>"; her ikisi + Python hata izi.
    # CRITICAL sayılmaz: Hub ACİL DUR'u bilerek CRITICAL günlüğe yazar (beklenen olay, hata değil).
    pat = re.compile(r'Traceback \(most recent call last\)|(^|\s)ERROR(:|\s)')
    bad = [ln.strip() for ln in text.splitlines() if pat.search(ln)]
    title = 'Hub günlüğünde sunucu hatası yok'
    if bad:
        return Result('hub.log', title, FAIL, {'hatalı satır': str(len(bad))}, note=f'ilk: {bad[0][:140]}')
    return Result('hub.log', title, PASS, {'hatalı satır': '0'})


def kaldi(results: Sequence[Result]) -> List[Result]:
    return [r for r in results if r.status == FAIL]


def exit_code(results: Sequence[Result]) -> int:
    """0 = hiç KALDI yok. Hiç adım koşmadıysa (hepsi atlandı/boş) da 1: boş geçiş kabul sayılmaz."""
    ran = [r for r in results if r.status != SKIP]
    return 0 if ran and not kaldi(results) else 1


def summary_line(results: Sequence[Result]) -> str:
    n = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP)}
    return f'{n[PASS]} {PASS} · {n[FAIL]} {FAIL} · {n[SKIP]} {SKIP}'


# ---- test dosyası üreteci (STL küp; STEP için tests/helpers_step.step_box kullanılır) ----
def stl_cube(size_mm: float, center: bool = True) -> bytes:
    """İkili STL: `size_mm` kenarlı küp (12 üçgen, birim mm). `center=True` → orijin küpün ORTASINDA."""
    o = -size_mm / 2 if center else 0.0
    pts = [(o + size_mm * (i & 1), o + size_mm * ((i >> 1) & 1), o + size_mm * ((i >> 2) & 1))
           for i in range(8)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = [t for a, b, c, d in quads for t in ((pts[a], pts[b], pts[c]), (pts[a], pts[c], pts[d]))]
    out = b'sim_acceptance'.ljust(80, b'\0') + struct.pack('<I', len(tris))
    for t in tris:
        out += struct.pack('<12fH', 0, 0, 0, *[v for p in t for v in p], 0)   # normal 0: sunucu hesaplar
    return out


# ---- 3B izdüşüm: kamera matrislerinden dünya noktası → ekran pikseli (headless tıklama için) ----
def project(point: Sequence[float], view: Sequence[float], proj: Sequence[float],
            rect: Dict[str, float]) -> Tuple[float, float, float]:
    """three.js matrisleri sütun-öncelikli (16 sayı). Dönüş: (px, py, ndc_z); ndc_z ∉ [-1, 1] → görüş dışı."""
    v = [point[0], point[1], point[2], 1.0]
    eye = [sum(view[c * 4 + r] * v[c] for c in range(4)) for r in range(4)]
    clip = [sum(proj[c * 4 + r] * eye[c] for c in range(4)) for r in range(4)]
    if clip[3] == 0:
        raise Fail('izdüşüm: w=0 (nokta kamera düzleminde)')
    nx, ny, nz = clip[0] / clip[3], clip[1] / clip[3], clip[2] / clip[3]
    return rect['l'] + (nx * 0.5 + 0.5) * rect['w'], rect['t'] + (1 - (ny * 0.5 + 0.5)) * rect['h'], nz


# ---- rapor ----
def render_md(results: Sequence[Result], meta: Dict[str, str]) -> str:
    out = ['# SİM kabul raporu', '', *(f'- **{k}:** {v}' for k, v in meta.items()), '',
           f'**Sonuç: {summary_line(results)}**', '']
    bad = kaldi(results)
    if bad:
        out += [f'## {FAIL} listesi', *(f'- `{r.id}` — {r.title}: {r.note}' for r in bad), '']
    out += ['| Adım | Durum | Ölçülen | Ekran |', '|---|---|---|---|']
    for r in results:
        meas = '<br>'.join(f'{k}: {v}' for k, v in r.measured.items()) or (r.note if r.status != PASS else '')
        if r.measured and r.note and r.status == SKIP:
            meas += f'<br>**{r.note}**'
        shots = ' '.join(f'[{Path(s).stem.split("__")[-1]}](shots/{s})' for s in r.shots)
        out.append(f'| `{r.id}` {r.title} | {r.status} | {meas.replace("|", "/")} | {shots} |')
    return '\n'.join(out) + '\n'


_CSS = ('body{font:14px system-ui,sans-serif;margin:24px;max-width:1100px;background:#0d1117;color:#e6edf3}'
        '.ok{color:#3fb950}.bad{color:#f85149}.skip{color:#d29922}table{border-collapse:collapse;width:100%}'
        'td,th{border:1px solid #30363d;padding:6px 8px;vertical-align:top;text-align:left}'
        'img{max-width:320px;border:1px solid #30363d;margin:2px}code{color:#79c0ff}')


def render_html(results: Sequence[Result], meta: Dict[str, str], shot_dir: Path) -> str:
    """Tek dosya: görüntüler base64 ile gömülür (rapor tek başına gönderilebilir). Tüm metin kaçışlanır."""
    esc = html.escape
    cls = {PASS: 'ok', FAIL: 'bad', SKIP: 'skip'}
    head = '<!doctype html><html lang="tr"><meta charset="utf-8"><title>SİM kabul raporu</title>'
    parts = [f'{head}<style>{_CSS}</style>',
             '<h1>SİM kabul raporu</h1><ul>',
             *(f'<li><b>{esc(k)}:</b> {esc(v)}</li>' for k, v in meta.items()), '</ul>',
             f'<h2>Sonuç: {esc(summary_line(results))}</h2>']
    bad = kaldi(results)
    if bad:
        parts += [f'<h3 class="bad">{FAIL} listesi</h3><ul>',
                  *(f'<li><code>{esc(r.id)}</code> — {esc(r.title)}: {esc(r.note)}</li>'
                    for r in bad), '</ul>']
    parts.append('<table><tr><th>Adım</th><th>Durum</th><th>Ölçülen</th><th>Ekran</th></tr>')
    for r in results:
        meas = '<br>'.join(f'{esc(k)}: {esc(v)}' for k, v in r.measured.items())
        meas = meas or esc(r.note if r.status != PASS else '')
        if r.status in (FAIL, SKIP) and r.measured and r.note:
            meas += f'<br><b class="bad">{esc(r.note)}</b>'
        imgs = []
        for s in r.shots:
            p = Path(shot_dir) / s
            if p.is_file():
                data = base64.b64encode(p.read_bytes()).decode()
                imgs.append(f'<img alt="{esc(s)}" src="data:image/png;base64,{data}">')
        parts.append(f'<tr><td><code>{esc(r.id)}</code><br>{esc(r.title)}<br>'
                     f'<small>{r.seconds} sn</small></td>'
                     f'<td class="{cls[r.status]}"><b>{esc(r.status)}</b></td><td>{meas}</td>'
                     f'<td>{"".join(imgs)}</td></tr>')
    parts.append('</table></html>')
    return ''.join(parts)


def write_reports(results: Sequence[Result], meta: Dict[str, str], out: Path) -> Dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = {'md': out / 'rapor.md', 'html': out / 'rapor.html', 'json': out / 'sonuc.json'}
    paths['md'].write_text(render_md(results, meta), encoding='utf-8')
    paths['html'].write_text(render_html(results, meta, out / 'shots'), encoding='utf-8')
    doc = {'meta': meta, 'summary': summary_line(results), 'results': [r.__dict__ for r in results]}
    paths['json'].write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    return paths
