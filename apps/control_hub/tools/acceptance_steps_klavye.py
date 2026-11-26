"""Kabul adımı: klavye jog (P1c). D basılı tut → hareket, bırak → DUR, Esc → ACİL DUR + 423.

Tuşları Playwright `keyboard.down/up` gönderir (fiziksel tuş kodu: D = `KeyD` = Y−, jog_logic.js KEY_CART).
Ölçüm Hub'dan: TCP y (mm) ve kilit durumu; arayüz yalnız sürülür."""
from __future__ import annotations

import time

from tools.acceptance_core import Ctx, step
from tools.acceptance_ui import NAV_MS, wait_for

MOVE_MM = 2.0              # basılıyken en az bu kadar ilerlemeli (jog ≈ 6 mm/s: ≈ 0,4 sn)
STILL_MM = 0.05            # bırakınca / ACİL DUR'da TCP bu kadardan fazla kaymamalı
MOVE_WAIT_S = 6.0          # hareketin başlaması için üst süre
STOP_WAIT_S = 5.0          # ACİL DUR'un kilidi koyması için üst süre
KEY_JOG = 'd'              # KeyD: Y−
KEY_ESTOP = 'Escape'
JOG_PROBE = {'lease': 'kabul-yoklama', 'twist': [0.01, 0, 0, 0, 0, 0], 'ttl_ms': 250}


def _hold_until_moved(c: Ctx, y0: float) -> float:
    """D'ye basar; TCP y, `y0`'dan MOVE_MM kadar azalana dek bekler; geçen süreyi döndürür.
    Sayfa ilk açılışta jog iznini (GET /api/jog) asenkron alır: ilk basış yutulabilir; insan gibi bir kez
    daha basılır (en çok 2 deneme, notta görünür). Hareketsizliği gizlemez: ikincisinde de yoksa KALDI."""
    for _ in range(2):
        c.page.keyboard.down(KEY_JOG)
        t0 = time.monotonic()
        while time.monotonic() - t0 < MOVE_WAIT_S / 2 and y0 - c.hub.tcp_mm()[1] < MOVE_MM:
            time.sleep(0.05)
        if y0 - c.hub.tcp_mm()[1] >= MOVE_MM:
            return time.monotonic() - t0
        c.page.keyboard.up(KEY_JOG)
        c.notes.append('klavye ilk basış hareket üretmedi: yeniden basıldı')
        time.sleep(0.5)
    c.expect(False, f'D basılıyken TCP y {MOVE_MM:g} mm ilerlemedi ({MOVE_WAIT_S:g} sn)')
    return 0.0


@step('kontrol.klavye', 'Kontrol: klavye jog (D basılı = hareket, bırak = DUR, Esc = ACİL DUR + 423)',
      'Kontrol')
def kontrol_klavye(c: Ctx) -> None:
    h = c.hub
    # Esc'te sayfanın kendi kalp atışı/bırakma isteği 423 alır (beklenen); tarayıcı bunu console.error yazar.
    c.allow_error(r'status of 423 \(Locked\).*/api/jog')
    c.page.goto(f'{h.base}/#/jog')
    # Tuş haritası kartı telefonda (≤ 800 px) CSS ile gizlidir; dinleyiciler yine de çalışır (harici klavye):
    # bu yüzden "görünür" değil "DOM'da" beklenir ve kartın durumu ölçüm olarak yazılır.
    c.page.wait_for_selector('#p-keys', state='attached', timeout=NAV_MS)
    shown = c.page.is_visible('#p-keys')
    c.measure('klavye kartı (tuş haritası)', 'görünür' if shown else 'gizli (telefon düzeni)')
    wait_for(c, '() => document.querySelector("#jp-kb-en").checked',
             "klavye jog SİM'de varsayılan açık olmalıydı (#jp-kb-en işaretli değil)", 10000)
    c.page.wait_for_timeout(1500)                        # ilk durum + jog izni gelsin (P1c testindeki gibi)
    # --- 1) D basılı tut → hareket ---
    y0 = h.tcp_mm()[1]
    took = _hold_until_moved(c, y0)
    time.sleep(0.5)                                      # basılı tutmaya devam: hareket sürüyor mu
    moved = h.tcp_mm()[1] - y0
    c.measure('D basılıyken Y Δ (mm)', f'{moved:.2f} (ilk {MOVE_MM:g} mm: {took:.2f} sn; +0,5 sn basılı)')
    c.expect(moved <= -MOVE_MM, f'D basılıyken Y Δ={moved:.2f} mm (≤ −{MOVE_MM:g} bekleniyordu)')
    c.shot('basili')
    # --- 2) bırak → DUR ---
    c.page.keyboard.up(KEY_JOG)
    time.sleep(0.7)                                      # durma payı (sunucu TTL'i ≤ 250 ms + yavaşlama)
    y_stop = h.tcp_mm()[1]
    time.sleep(1.0)
    c.near('bıraktıktan sonra Y kayması (mm)', h.tcp_mm()[1] - y_stop, 0.0, STILL_MM, 'mm')
    jog = h.api('/api/jog')[1]
    c.measure('jog durumu (bırakınca)', f'{jog.get("status")}')
    c.expect(jog.get('status') in ('idle', 'stopped', None) or not jog.get('active'),
             f'bırakınca jog hâlâ etkin: {jog.get("status")!r}')
    # --- 3) hareket sürerken Esc → ACİL DUR + 423 ---
    y1 = h.tcp_mm()[1]
    _hold_until_moved(c, y1)
    t_esc = time.monotonic()
    c.page.keyboard.press(KEY_ESTOP)
    while not h.state()['estop']['latched']:
        c.expect(time.monotonic() - t_esc < STOP_WAIT_S, f'Esc {STOP_WAIT_S:g} sn içinde ACİL DUR koymadı')
        time.sleep(0.05)
    c.measure('Esc → ACİL DUR gecikmesi (sn)', round(time.monotonic() - t_esc, 2))
    time.sleep(0.8)
    y_frozen = h.tcp_mm()[1]
    time.sleep(0.8)
    c.near('ACİL DUR sonrası Y kayması (mm)', h.tcp_mm()[1] - y_frozen, 0.0, STILL_MM, 'mm')
    st, doc = h.api('/api/jog', 'POST', JOG_PROBE)
    c.expect(st == 423, f'/api/jog ACİL DUR sırasında HTTP {st} döndü (423 bekleniyordu): {doc}')
    st2, doc2 = h.api('/api/move', 'POST', {'type': 'joint', 'joints_deg': [0, 88.8, 71.6, 0, -17.2, 0]})
    c.expect(st2 == 423, f'/api/move ACİL DUR sırasında HTTP {st2} döndü (423 bekleniyordu): {doc2}')
    c.measure('/api/jog · /api/move', f'HTTP {st} · HTTP {st2}')
    wait_for(c, '() => /AKTİF/.test(document.querySelector("#estop-label").textContent)',
             'üst çubuk "ACİL DUR AKTİF" göstermedi', 10000)
    c.shot('acildur')
    c.page.keyboard.up(KEY_JOG)                          # basılı tuşu bırak, sonra kilidi aç
    c.page.click('#btn-reset')
    t0 = time.monotonic()
    while h.state()['estop']['latched']:
        c.expect(time.monotonic() - t0 < 10, 'kilit 10 sn içinde açılmadı')
        time.sleep(0.1)
    c.measure('kilit', 'açıldı (düğmeyle)')
