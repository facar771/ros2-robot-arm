"""Kayıtlı TASLAĞI SİM'e uygulanabilir ``Profile``'a çevirir (P4b). Hiçbir şey yazılmaz, robota gitmez.

Hakem yine Parametreler sayfasıyla AYNI doğrulama hattıdır (``profile_view.validate``: ``robot_core`` +
sağduyu kuralları + home kutu içi): taslak dosyasındaki ``edits`` güncel kaynak profile uygulanır, sonuç
hatalıysa ``ViewError(422, 'invalid', ...)`` ile NEDENİYLE reddedilir (limit min>max, bozuk kutu...).
Taslak, profil dosyası değiştikten sonra kaydedildiyse (eski sürüme göre) uygulanmaz: yeniden kaydedilmeli.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from control_hub.hub_core import profile_view as pv

MAX_FIELDS_SHOWN = 8


@dataclass
class Prepared:
    profile: Any                    # robot_core.profile.Profile (yalnız bellekte kullanılır)
    info: Dict[str, Any]            # anlık görüntüye girecek küçük özet: robot, saved_at, changes, fields


def _labels(src: pv.Source, changes: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for c in changes[:MAX_FIELDS_SHOWN]:
        spec = src.catalog.get(c['field'])
        out.append(spec.label if spec is not None else str(c['field']))
    return out


def prepare(robot: str) -> Prepared:
    src = pv.load_source(robot)
    draft = pv.read_draft(src)
    if not draft.get('exists'):
        raise pv.ViewError(404, 'no_draft', 'Kayıtlı taslak yok: önce "Taslak kaydet" ile bir taslak oluştur')
    if draft.get('error'):
        raise pv.ViewError(422, 'draft_unreadable', 'Taslak okunamadı (bozuk dosya); yeniden kaydet')
    if draft.get('profile_stale'):
        raise pv.ViewError(409, 'draft_stale', 'Taslak, profil dosyası değişmeden önceki sürüme göre '
                           'kaydedilmiş: Parametreler sayfasında yeniden doğrula ve "Taslak kaydet" de')
    edits = draft.get('edits') or {}
    if not edits:
        raise pv.ViewError(400, 'no_changes', 'Taslakta değişiklik yok; uygulanacak bir şey yok')
    val = pv.validate(src, edits)
    if not val.ok:
        raise pv.ViewError(422, 'invalid', 'Taslak geçersiz: SİM\'e uygulanmadı', **val.payload())
    try:
        profile = src.eng.parse_profile(val.docs.profile)
    except ValueError as exc:                       # doğrulama geçtiyse olmaz; yine de açıkça reddet
        raise pv.ViewError(422, 'invalid', f'Taslak profile çevrilemedi: {exc}') from None
    info = {'robot': src.robot, 'saved_at': draft.get('saved_at'), 'changes': len(val.changes),
            'fields': _labels(src, val.changes)}
    return Prepared(profile, info)
