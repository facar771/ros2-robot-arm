"""SİM kabul betiği: TEK komutla --sim Hub'ı başlatır, headless Chromium ile arayüzü sırayla sınar;
her adım için GEÇTİ/KALDI + ÖLÇÜLEN değer + ekran görüntüsü yazar; sonunda tek HTML + MD rapor ve
çıkış kodu verir.

WSL'de (apps/control_hub içinde):   python -m tools.sim_acceptance            # çıkış 0 = hepsi GEÇTİ
                                    python -m tools.sim_acceptance --list     # adımlar
Gerekli: `pip install playwright` + sistem Chrome/Chromium (HUB_CHROME ile yol verilebilir). STEP adımı için
`pip install -r requirements-step.txt` (cascadio). Uygulama koduna DOKUNMAZ: arayüzü dışarıdan sürer, ölçümü
Hub'ın /api/state'inden alır. Ayrıntı: docs/KABUL.md. Yeni adım (ör. P1c klavye): tools/acceptance_steps*.py.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import (  # noqa: E402,F401  (içe aktarma adımları STEPS'e kaydeder)
    acceptance_steps,
    acceptance_steps_klavye,
    acceptance_steps_prog,
    acceptance_steps_simprofil,
)
from tools.acceptance_core import (  # noqa: E402
    STEPS,
    Ctx,
    check_hub_log,
    exit_code,
    kaldi,
    ordered,
    run_steps,
    summary_line,
    write_reports,
)
from tools.acceptance_hub import HubProc  # noqa: E402

ARGS = ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
        '--use-gl=angle']
VIEWPORTS = {'desktop': {'width': 1280, 'height': 800}, 'phone': {'width': 390, 'height': 844}}
# Bilinen eksik uç: Parametreler sayfası canlı pot için sorar (kodda belgeli, İSTEK Robot_3/Robot_10).
# Hata sayılmaz; adımın notuna yazılır. Başka her console.error/pageerror adımı KALDI yapar.
KNOWN_404 = ('/api/pot',)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m tools.sim_acceptance', description=__doc__.split('\n\n')[0])
    p.add_argument('--out', type=Path, help='rapor klasörü (varsayılan: geçici klasörde sim_kabul_<zaman>)')
    p.add_argument('--port', type=int, default=0, help='Hub portu (varsayılan: boş bir port)')
    p.add_argument('--viewport', choices=sorted(VIEWPORTS), default='desktop',
                   help='masaüstü 1280×800 | telefon 390×844 (varsayılan: masaüstü)')
    p.add_argument('--skip', default='', metavar='ID,…',
                   help='adım kimlikleri / bölüm adları ATLANDI olur (ör. parcalar.step)')
    p.add_argument('--only', default='', metavar='ID,…', help='yalnız bu adımlar / bölümler koşar')
    p.add_argument('--chrome', default=os.environ.get('HUB_CHROME'),
                   help='Chrome/Chromium yolu (varsayılan: PATH’te google-chrome, chromium)')
    p.add_argument('--list', action='store_true', help='adımları listele ve çık')
    return p


def _csv(text: str) -> List[str]:
    return [t.strip() for t in text.split(',') if t.strip()]


def _on_console(c: Ctx, m) -> None:
    if m.type != 'error':
        return
    url = (m.location or {}).get('url', '')
    if '404' in m.text and url.endswith(KNOWN_404):
        note = f'bilinen eksik uç: {url.rsplit("/api/", 1)[-1]} → 404'
        if note not in c.notes:
            c.notes.append(note)
        return
    c.errors.append(f'console: {m.text} {url}'.strip())


def _default_out() -> Path:
    return Path(tempfile.gettempdir()) / f'sim_kabul_{time.strftime("%Y%m%d_%H%M%S")}'


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for s in ordered(STEPS):
            print(f'{s.id:22} [{s.section}] {s.title}')
        return 0
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright yok. WSL'de:  pip install playwright   (Chrome/Chromium sistemde kurulu olmalı)",
              file=sys.stderr)
        return 2
    out = args.out or _default_out()
    out.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out / 'shots', ignore_errors=True)     # eski koşumdan kalan görüntüler rapora karışmasın
    hub = HubProc(out, port=args.port)
    print(f'Hub başlatılıyor (SİM, geçici veri klasörü)… rapor: {out}')
    hub.start()
    # Koşum kısa sürsün: SİM hız tavanı 100 (eşikleri etkilemez, yalnız süre).
    hub.api('/api/sim/speed', 'POST', {'speed_pct': 100})
    vp, tag = VIEWPORTS[args.viewport], '' if args.viewport == 'desktop' else '_telefon'
    try:
        with sync_playwright() as pw:
            exe = args.chrome or shutil.which('google-chrome') or shutil.which('chromium') or None
            try:
                browser = pw.chromium.launch(executable_path=exe, headless=True, args=ARGS)
            except Exception as exc:                     # noqa: BLE001
                why = str(exc).splitlines()[0]
                print(f'Chromium açılamadı ({why}). Chrome kur ya da HUB_CHROME=/yol ver; '
                      'ya da: playwright install chromium', file=sys.stderr)
                return 2

            def make_ctx(step_id: str) -> Ctx:
                bctx = browser.new_context(viewport=vp, device_scale_factor=1,
                                           has_touch=args.viewport == 'phone')
                page = bctx.new_page()
                page.set_default_timeout(20000)
                c = Ctx(page, hub, out / 'shots', tag, closer=bctx.close)
                page.on('pageerror', lambda e: c.errors.append(f'pageerror: {e}'))
                page.on('console', lambda m: _on_console(c, m))
                if step_id != 'acil_dur.kilit':          # ACİL DUR adımı kendi başlangıcını kurar
                    hub.reset_home()
                return c

            results = run_steps(make_ctx, STEPS, skip=_csv(args.skip), only=_csv(args.only))
            browser.close()
    finally:
        hub.stop()
    if not args.only:                                    # tüm koşumda: sunucu tarafı hata izi de ölçülür
        results.append(check_hub_log(hub.log_path.read_text(encoding='utf-8', errors='replace')))
    meta = {'Hub': 'python -m control_hub --sim (temiz geçici veri klasörü)',
            'Görünüm': f'{args.viewport} {vp["width"]}×{vp["height"]}',
            'Zaman': time.strftime('%Y-%m-%d %H:%M:%S'),
            'Not': 'SİM ölçümleri gerçek robot ölçümü DEĞİLDİR (tahmini).'}
    paths = write_reports(results, meta, out)
    for r in results:
        first = '; '.join(f'{k}={v}' for k, v in list(r.measured.items())[:2])
        print(f'  {r.status:8} {r.id:22} {r.seconds:5.1f} sn  {first}')
    print(f'\n{summary_line(results)}\nRapor: {paths["html"]}  ({paths["md"].name}, {paths["json"].name})')
    bad = kaldi(results)
    if bad:
        print(f'\nKALDI listesi ({len(bad)}):')
        for r in bad:
            print(f'  - {r.id}: {r.note}')
    return exit_code(results)


if __name__ == '__main__':
    sys.exit(main())
