"""Kabul adımı: Parametreler → "SİM'e uygula / Sıfırla" (P4b).

Taslakta J1 üst limitini düşür → SİM'e uygula → limit dışı hedef `409 joint_limit` ile reddedilir (limit içi
hedef kabul edilir) → Sıfırla → eski limit geri gelir. Ölçüm Hub'dan (`/api/state`, `/api/move`);
arayüz yalnız sürülür. Profil geçersiz kılması SİM belleğindedir: adım HER durumda sıfırlayarak çıkar."""
from __future__ import annotations

import time

from tools.acceptance_core import Ctx, step
from tools.acceptance_ui import enabled, fill_until, open_page, wait_for

KEY = 'axes.joint_1.max_deg'                             # "joint_1 · en büyük açı" (°)
NEW_MAX_DEG = 10.0                                       # düşürülen üst limit
INSIDE_DEG = 8.0                                         # yeni limit İÇİNDE: kabul edilmeli
OUTSIDE_DEG = 20.0                                       # eski limit içinde, yeni limit DIŞINDA: reddedilmeli
POSE = [88.8, 71.6, 0.0, -17.2, 0.0]                     # J2..J6: SİM home (karma_robot)
APPLY = 'button:text-is("SİM\'e uygula")'
RESET = 'button:text-is("Sıfırla (gerçek profile dön)")'
SAVE_ENABLED = ('() => [...document.querySelectorAll("button")]'
                '.some((b) => b.textContent === "Taslak kaydet" && !b.disabled)')
PILL_ON = '() => { const p = document.getElementById("pill-profile"); return !!p && !p.hidden; }'
PILL_OFF = '() => { const p = document.getElementById("pill-profile"); return !p || p.hidden; }'


def _j1_max(c: Ctx) -> float:
    return float(c.hub.robot()['joints'][0]['max_deg'])


def _override_active(c: Ctx) -> bool:
    return bool((c.hub.robot().get('profile_override') or {}).get('active'))


def _wait_override(c: Ctx, active: bool, what: str) -> None:
    t0 = time.monotonic()
    while _override_active(c) != active:
        c.expect(time.monotonic() - t0 < 10, what)
        time.sleep(0.1)


def _move_j1(c: Ctx, deg: float):
    body = {'type': 'joint', 'joints_deg': [deg, *POSE], 'speed_pct': 100}
    return c.hub.api('/api/move', 'POST', body)


def _clean(c: Ctx) -> None:
    """Taslak + SİM geçersiz kılması yok (adımın başı ve sonu)."""
    c.hub.api('/api/params/draft', 'DELETE')
    if _override_active(c):
        c.hub.api('/api/params/sim/reset', 'POST', {})


@step('parametreler.sim_uygula',
      "Parametreler: taslakta J1 üst limiti düşür → SİM'e uygula → limit dışı hedef reddedilir → Sıfırla",
      'Parametreler')
def parametreler_sim_uygula(c: Ctx) -> None:
    h = c.hub
    _clean(c)
    try:
        before = _j1_max(c)
        c.near('J1 üst limit (başlangıç, °)', before, 170.0, 0.01, '°')
        field = f'input.pr-in[data-key="{KEY}"]'
        open_page(c, 'params', field)
        fill_until(c, field, f'{NEW_MAX_DEG:g}', SAVE_ENABLED,
                   f'J1 üst limit {NEW_MAX_DEG:g}° geçerli taslak: "Taslak kaydet" etkinleşmedi')
        c.page.click('button:text-is("Taslak kaydet")')
        wait_for(c, r'() => /Taslak kaydedildi/.test(document.body.textContent)',
                 "'Taslak kaydedildi' iletisi çıkmadı", 15000)
        draft = h.api('/api/params')[1]['draft']
        c.expect(draft.get('edits', {}).get(KEY) == NEW_MAX_DEG, f'kayıtlı taslak yanlış: {draft}')
        c.measure('kayıtlı taslak', f'{KEY} = {draft["edits"][KEY]:g}')
        c.expect(_j1_max(c) == before, 'taslak kaydı robotun limitini DEĞİŞTİRDİ (yalnız uygulayınca)')
        # --- SİM'e uygula ---
        enabled(c, APPLY, 15000)
        c.page.click(APPLY)
        _wait_override(c, True, "SİM'e uygula: 10 sn içinde taslak profil etkinleşmedi")
        c.near('J1 üst limit (uygulanınca, °)', _j1_max(c), NEW_MAX_DEG, 0.01, '°')
        wait_for(c, PILL_ON, "üst çubukta 'TASLAK PROFİL (SİM)' rozeti çıkmadı", 10000)
        c.measure('üst çubuk rozeti', c.page.inner_text('#pill-profile'))
        c.shot('uygulandi')
        # --- limit dışı hedef reddedilir, limit içi kabul edilir ---
        st, doc = _move_j1(c, OUTSIDE_DEG)
        c.expect(st == 409 and doc.get('error') == 'joint_limit',
                 f'J1={OUTSIDE_DEG:g}° (limit {NEW_MAX_DEG:g}° dışı): HTTP {st} {doc.get("error")} '
                 '(409 joint_limit bekleniyordu)')
        c.measure(f'limit dışı hedef J1={OUTSIDE_DEG:g}°', f'HTTP {st} {doc["error"]}')
        st_in, doc_in = _move_j1(c, INSIDE_DEG)
        c.expect(st_in == 202, f'limit içi hedef J1={INSIDE_DEG:g}° reddedildi: HTTP {st_in} {doc_in}')
        h.wait_idle()
        c.near('limit içi hedefe varış J1 (°)', h.joints_deg()[0], INSIDE_DEG, 0.5, '°')
        # --- Sıfırla ---
        enabled(c, RESET, 15000)
        c.page.click(RESET)
        _wait_override(c, False, 'Sıfırla: 10 sn içinde gerçek profile dönülmedi')
        c.near('J1 üst limit (sıfırlanınca, °)', _j1_max(c), before, 0.01, '°')
        wait_for(c, PILL_OFF, "Sıfırla sonrası 'TASLAK PROFİL' rozeti kalktı olmalıydı", 10000)
        st2, doc2 = _move_j1(c, OUTSIDE_DEG)
        c.expect(st2 == 202, f'sıfırlanınca J1={OUTSIDE_DEG:g}° hâlâ reddediliyor: HTTP {st2} {doc2}')
        h.wait_idle()
        c.near('sıfırlanınca varış J1 (°)', h.joints_deg()[0], OUTSIDE_DEG, 0.5, '°')
        c.measure('sıfırlanınca hedef', f'J1={OUTSIDE_DEG:g}° kabul edildi (HTTP {st2})')
        c.shot('sifirlandi')
    finally:
        _clean(c)
