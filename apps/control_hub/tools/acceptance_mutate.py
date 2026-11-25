"""Mutasyon kanıtı: uygulama koduna GEÇİCİ bir hata sok → ilgili kabul adımı KALDI olmalı → dosyayı geri al.

Kabul betiği yalnız "temiz main'de GEÇER" diyorsa kanıt zayıftır; bu araç "hata varken KALDI" der.

WSL'de (apps/control_hub içinde; çalışma ağacı TEMİZ olmalı):
  python -m tools.acceptance_mutate --step kontrol.jog \\
      --edit control_hub/web/jog.js "amount: sign * J.step }" "amount: -sign * J.step }"

Çıkış kodu: 0 = mutasyon YAKALANDI (adım KALDI oldu) · 1 = mutasyon SAĞ KALDI (adım yine GEÇTİ: sınama zayıf)
· 2 = kurulum hatası (dosya kirli, kalıp bulunamadı/tekil değil, rapor yok). Dosya HER durumda `git checkout`
ile geri alınır; araç kendisi hiçbir dosyayı kalıcı değiştirmez.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

HUB = Path(__file__).resolve().parents[1]                # apps/control_hub
Edit = Tuple[str, str, str]                              # (hub'a göre dosya yolu, eski, yeni)


def apply_edit(src: str, old: str, new: str, first: bool = False) -> str:
    """`old` tam bir kez geçmeli (belirsiz mutasyon olmasın); `first=True` → ilk geçeni değiştirir."""
    n = src.count(old)
    if n == 0:
        raise ValueError('kalıp bulunamadı')
    if n > 1 and not first:
        raise ValueError(f'kalıp {n} yerde geçiyor (tek olmalı; ya da --first)')
    return src.replace(old, new, 1)


def _git(*args: str) -> str:
    out = subprocess.run(['git', *args], cwd=HUB, capture_output=True, text=True, check=True)
    return out.stdout


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m tools.acceptance_mutate',
                                description=__doc__.split('\n\n')[0])
    p.add_argument('--step', required=True, help='KALDI olması beklenen adım kimliği (ör. kontrol.jog)')
    p.add_argument('--edit', nargs=3, action='append', required=True, metavar=('DOSYA', 'ESKİ', 'YENİ'),
                   help="mutasyon; DOSYA apps/control_hub'a göre (birden çok --edit verilebilir)")
    p.add_argument('--first', action='store_true', help='kalıp birden çok yerdeyse ilkini boz')
    p.add_argument('--viewport', default='desktop', choices=('desktop', 'phone'))
    p.add_argument('--out', type=Path, help='rapor klasörü (varsayılan: geçici)')
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    edits: List[Edit] = [tuple(e) for e in args.edit]    # type: ignore[misc]
    files = [HUB / e[0] for e in edits]
    for f in files:
        if not f.is_file():
            print(f'dosya yok: {f}', file=sys.stderr)
            return 2
        if _git('status', '--porcelain', '--', str(f)).strip():
            print(f'çalışma ağacı kirli: {f} (önce commit/stash)', file=sys.stderr)
            return 2
    out = args.out or Path(tempfile.mkdtemp(prefix='mutasyon_'))
    touched: List[Path] = []
    try:
        for f, (_, old, new) in zip(files, edits):
            f.write_text(apply_edit(f.read_text(encoding='utf-8'), old, new, args.first), encoding='utf-8')
            touched.append(f)
        run = subprocess.run([sys.executable, '-m', 'tools.sim_acceptance', '--only', args.step,
                              '--viewport', args.viewport, '--out', str(out)],
                             cwd=HUB, capture_output=True, text=True)
        doc = json.loads((out / 'sonuc.json').read_text(encoding='utf-8'))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f'kurulum hatası: {exc}', file=sys.stderr)
        return 2
    finally:
        for f in touched:
            subprocess.run(['git', 'checkout', '--', str(f)], cwd=HUB, check=True)
    row = next((r for r in doc['results'] if r['id'] == args.step), None)
    if row is None:
        print(f'adım raporda yok: {args.step}', file=sys.stderr)
        return 2
    caught = row['status'] == 'KALDI'
    print(f'{"YAKALANDI" if caught else "SAĞ KALDI"}  {args.step}  (betik çıkış kodu {run.returncode})')
    print(f'  not: {row["note"] or "-"}')
    print(f'  rapor: {out / "rapor.html"}')
    return 0 if caught else 1


if __name__ == '__main__':
    sys.exit(main())
