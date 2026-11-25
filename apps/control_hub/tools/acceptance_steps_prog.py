"""Kabul adımları 2/2: Algoritmalar → Program, Parametreler, Günlük, ACİL DUR.
(Kontrol ve Parçalar: acceptance_steps.py.)"""
from __future__ import annotations

import time
from pathlib import Path

from tools.acceptance_core import Ctx, step
from tools.acceptance_ui import (
    enabled,
    fill_until,
    open_page,
    set_field,
    set_range,
    wait_for,
    wait_scene,
)

HOME_DEG = [0, 88.8, 71.6, 0, -17.2, 0]                  # SİM home eklemleri (karma_robot)


def algo_kind(c: Ctx, kind_id: str) -> dict:
    """Üreteç tanımı sunucudan (arayüzün tek doğruluk kaynağı): başlık, alan etiketleri, varsayılanlar."""
    kinds = c.hub.api('/api/algo/kinds')[1]['kinds']
    return next(k for k in kinds if k['id'] == kind_id)


def settle_ok(c: Ctx) -> None:
    """Algoritmalar özeti '… adım · ✓ …' (ihlalsiz) olana dek bekler."""
    js = r'() => /adım · ✓/.test(document.querySelector(".al-sum").textContent)'
    wait_for(c, js, "Algoritmalar özeti 'adım · ✓' (ihlalsiz) olmadı", 30000)
    c.page.wait_for_timeout(600)


def wait_run_done(c: Ctx, timeout: float = 180.0) -> dict:
    """Program koşucusu bitene dek bekler (sunucudan okunur); son koşucu durumunu döndürür."""
    t0 = time.monotonic()
    seen_running = False
    while time.monotonic() - t0 < timeout:
        run = c.hub.api('/api/program/state')[1]['runner']
        seen_running = seen_running or bool(run.get('running'))
        if seen_running and not run.get('running'):
            return run
        time.sleep(0.3)
    raise TimeoutError(f'program {timeout:.0f} sn içinde bitmedi')


# ================================== Algoritmalar → Program → Çalıştır ==================================
@step('algo.program', 'Algoritmalar → Programa ekle → Çalıştır (bitiş TCP ölçülür)', 'Algoritmalar')
def algo_program(c: Ctx) -> None:
    h = c.hub
    kind = algo_kind(c, 'arc')
    sweep = next(f for f in kind['fields'] if f['key'] == 'sweep_deg')
    open_page(c, 'algo', '.al-sum')
    wait_scene(c, 'algo:')
    c.page.click(f'button:text-is("{kind["title"]}")')
    settle_ok(c)
    set_field(c, f'#page-algo [title="{sweep["label"]}"]', '180')   # yarım yay: bitiş başlangıçtan FARKLI
    c.page.wait_for_timeout(500)
    settle_ok(c)
    c.shot('yay_onizleme')
    params = {f['key']: f['default'] for f in kind['fields']} | {'sweep_deg': 180}
    st, gen = h.api('/api/algo/generate', 'POST', {'kind': 'arc', 'params': params, 'speed_pct': 25})
    c.expect(st == 200, f'üreteç yanıtı HTTP {st}')
    end = [s for s in gen['steps'] if 'pos_m' in s][-1]['pos_m']        # beklenen bitiş: son hareket adımı
    c.measure('üretilen adım', len(gen['steps']))
    c.page.click('button:text-is("Programa ekle")')
    c.page.click('#page-algo a:text-is("Program sayfasına git")')
    c.page.wait_for_selector('#page-program .pg-step', timeout=20000)
    n = c.page.locator('#page-program .pg-step').count()
    c.expect(n == len(gen['steps']), f'Program sayfasında {n} adım var, üreteç {len(gen["steps"])} üretti')
    c.measure('Program adım sayısı', n)
    set_range(c, '.pg-speed input[type="range"]', 100)  # Program hızı %100 (varsayılan %25: ~3 dk)
    c.page.click('button:text-is("Önizle")')
    c.page.wait_for_selector('.pg-prev.ok', timeout=30000)
    enabled(c, 'button:text-is("Çalıştır")')
    c.shot('program_onizleme')
    c.page.click('button:text-is("Çalıştır")')
    run = wait_run_done(c)
    c.expect(run.get('state') in ('done', 'finished', 'idle') and not run.get('error'),
             f'koşucu durumu {run.get("state")!r}, hata: {run.get("error")}')
    c.measure('koşucu', f'{run.get("state")} · {run.get("done")}/{run.get("total")} adım')
    h.wait_idle()
    t = h.tcp_mm()
    for k in range(3):
        c.near(f'bitiş TCP {"XYZ"[k]} (mm)', t[k], end[k] * 1000, 2.0, 'mm')
    c.shot('bitis')


# ============================================== Parametreler ==============================================
@step('parametreler.taslak', 'Parametreler: geçersiz değer reddedilir, taslak kaydedilir (robota UYGULANMAZ)',
      'Parametreler')
def parametreler_taslak(c: Ctx) -> None:
    h = c.hub
    h.api('/api/params/draft', 'DELETE')
    zone0 = h.robot()['safe_zone']
    key, field = 'safety.z_max_mm', 'input.pr-in[data-key="safety.z_max_mm"]'
    open_page(c, 'params', field)
    # z_max < z_min (20): sunucu doğrulaması reddeder
    fill_until(c, field, '10', r'() => /hata var/.test(document.body.textContent)',
               "z_max=10 < z_min=20 için 'hata var' uyarısı çıkmadı")
    save = c.page.locator('button:text-is("Taslak kaydet")')
    c.expect(save.is_disabled(), 'geçersiz değerde "Taslak kaydet" kapalı olmalıydı')
    c.measure('geçersiz değer (z_max=10 < z_min=20)', 'reddedildi, kaydet kapalı')
    c.shot('gecersiz')
    c.page.fill(field, '210')
    enabled(c, 'button:text-is("Taslak kaydet")', 15000)
    c.page.click('button:text-is("Taslak kaydet")')
    wait_for(c, r'() => /Taslak kaydedildi/.test(document.body.textContent)',
             "Geçerli değerde 'Taslak kaydedildi' iletisi çıkmadı", 15000)
    draft = h.api('/api/params')[1]['draft']
    c.expect(bool(draft.get('exists')) and draft.get('edits', {}).get(key) == 210,
             f'taslak sunucuda yok/yanlış: {draft}')
    c.measure('kayıtlı taslak', f'{key} = {draft["edits"][key]}')
    with c.page.expect_download(timeout=15000) as dl:
        c.page.click('button:text-is("YAML indir")')
    text = Path(dl.value.path()).read_text(encoding='utf-8')
    c.expect('210' in text, 'indirilen YAML değeri içermiyor')
    c.measure('YAML', f'{len(text)} bayt, 210 içeriyor')
    zone1 = h.robot()['safe_zone']
    c.expect(zone1 == zone0, f'taslak robota UYGULANMIŞ: güvenli kutu {zone0} → {zone1}')
    c.measure('robotun güvenli kutusu (taslaktan sonra)', 'değişmedi')
    c.shot('kaydedildi')
    h.api('/api/params/draft', 'DELETE')


# ============================================== Günlük ==============================================
@step('gunluk.olaylar', 'Günlük: ACİL DUR olayları görünür, süzgeç/arama/CSV doğru, sızıntı yok', 'Günlük')
def gunluk_olaylar(c: Ctx) -> None:
    h = c.hub
    h.api('/api/estop', 'POST')                          # bilinen olaylar: kritik + (sıfırlayınca) uyarı
    h.api('/api/estop/reset', 'POST', {})
    open_page(c, 'log', 'tr.lg-row')
    total = c.page.locator('tr.lg-row').count()
    srv = h.api('/api/log?limit=500')[1]
    c.measure('satır (arayüz / sunucu)', f'{total} / {srv["shown"]}')
    c.expect(total == srv['shown'] and total >= 2, f'arayüz {total} satır gösterdi, sunucu {srv["shown"]}')
    first = c.page.locator('tr.lg-row').first.inner_text().replace('\t', ' · ').replace('\n', ' ')
    c.measure('en üstteki (en yeni) olay', first[:90])
    c.expect('kilidi açıldı' in first, f'en yeni olay "kilidi açıldı" olmalı: {first[:80]!r}')
    c.page.select_option('select[data-role="level"]', 'critical')
    wait_for(c, '() => document.querySelectorAll("tr.lg-row:not(.critical)").length === 0',
             "'Kritik' süzgecinden sonra kritik olmayan satırlar kaldı", 10000)
    crit = c.page.locator('tr.lg-row').count()
    srv_c = h.api('/api/log?level=critical&limit=500')[1]['shown']
    c.expect(crit == srv_c and crit >= 1, f'kritik süzgeç: arayüz {crit}, sunucu {srv_c}')
    c.measure('kritik süzgeç (arayüz / sunucu)', f'{crit} / {srv_c}')
    c.page.select_option('select[data-role="level"]', 'info')
    c.page.fill('input[data-role="q"]', 'kilidi')
    wait_for(c, '() => { const r = [...document.querySelectorAll("tr.lg-row")]; '
                'return r.length > 0 && r.every((x) => /kilidi/i.test(x.textContent)); }',
             "Arama 'kilidi' sonucu boş ya da eşleşmeyen satır içeriyor", 10000)
    c.measure('arama "kilidi"', f'{c.page.locator("tr.lg-row").count()} satır, hepsi eşleşiyor')
    c.page.fill('input[data-role="q"]', '')
    c.shot('suzgec')
    with c.page.expect_download(timeout=15000) as dl:
        c.page.click('a[data-role="csv"]')
    csv = Path(dl.value.path()).read_text(encoding='utf-8-sig')
    c.expect(csv.splitlines()[0].startswith('zaman,sira,seviye') and 'ACİL DUR' in csv,
             f'CSV başlığı/içeriği beklenen gibi değil: {csv[:80]!r}')
    c.measure('CSV', f'{len(csv.splitlines()) - 1} olay satırı')
    body = c.page.inner_text('body')
    leak = [p for p in (str(h.data_dir), str(Path.home())) if p and p in body]
    c.expect(not leak, f'ekranda dosya yolu sızıntısı: {leak}')
    c.measure('yol sızıntısı (ekranda)', 'yok')


# ============================================== ACİL DUR ==============================================
@step('acil_dur.kilit', 'ACİL DUR: hareketi keser, 423 kilidi, kendiliğinden devam yok, sıfırlayınca açılır',
      'ACİL DUR', last=True)
def acil_dur(c: Ctx) -> None:
    h = c.hub
    h.reset_home()
    open_page(c, 'jog', '#jp-t0')
    enabled(c, '#jp-go')
    target = 25.0                                        # güvenli kutuda kalan en büyük J1'e yakın
    set_range(c, '#jp-speed', 30)                        # orta hız: birkaç sn sürer, ortada kesilebilir
    set_range(c, '#jp-t0', target)
    c.page.click('#jp-go')
    t0 = time.monotonic()
    while h.joints_deg()[0] < 2.0:                       # hareket gerçekten başladı (ivmelenme bitti)
        c.expect(time.monotonic() - t0 < 20, 'hareket 20 sn içinde başlamadı (J1 < 2°)')
        time.sleep(0.05)
    before = h.joints_deg()[0]
    c.expect(h.robot().get('moving') and before < target - 8,
             f'ACİL DUR için hareket ortada olmalı (J1={before:.1f}°, hedef {target:g}°)')
    t_click = time.monotonic()
    c.page.click('#btn-estop')
    while h.robot().get('moving') or not h.state()['estop']['latched']:
        c.expect(time.monotonic() - t_click < 5, 'ACİL DUR 5 sn içinde hareketi kesmedi')
        time.sleep(0.05)
    c.measure('kesme gecikmesi (sn)', round(time.monotonic() - t_click, 2))
    stop = h.joints_deg()[0]
    c.expect(before <= stop < target - 5,
             f'hareket ortada kesilmeliydi: J1 {before:.1f}° → {stop:.1f}° (hedef {target:g}°)')
    c.measure('J1: ACİL DUR anı / durduğu yer (°)', f'{before:.1f} / {stop:.1f} (hedef {target:g})')
    time.sleep(2.0)
    c.near('2 sn sonra J1 (kendiliğinden devam yok)', h.joints_deg()[0], stop, 0.05, '°')
    st, doc = h.api('/api/move', 'POST', {'type': 'joint', 'joints_deg': HOME_DEG, 'speed_pct': 50})
    c.expect(st == 423, f'/api/move ACİL DUR sırasında HTTP {st} döndü (423 bekleniyordu): {doc}')
    c.measure('/api/move', f'HTTP {st} {doc.get("error", "")}')
    st2, _ = h.api('/api/sim/reset', 'POST', {})
    c.expect(st2 == 423, f'/api/sim/reset HTTP {st2} (423 bekleniyordu)')
    c.measure('/api/sim/reset', f'HTTP {st2}')
    wait_for(c, '() => document.querySelector("#jp-go").disabled',
             "ACİL DUR sırasında Git düğmesi (#jp-go) kapanmadı", 10000)
    label = c.page.inner_text('#estop-label')
    c.expect('AKTİF' in label, f'üst çubuk "ACİL DUR AKTİF" göstermeli: {label!r}')
    c.measure('arayüz', f'"{label}", Git düğmesi kapalı')
    c.shot('kilitli')
    c.page.click('#btn-reset')
    t0 = time.monotonic()
    while h.state()['estop']['latched']:
        c.expect(time.monotonic() - t0 < 10, 'kilit 10 sn içinde açılmadı')
        time.sleep(0.1)
    st3, doc3 = h.api('/api/move', 'POST', {'type': 'joint', 'joints_deg': HOME_DEG, 'speed_pct': 100})
    c.expect(st3 == 202, f'kilit açıldıktan sonra /api/move HTTP {st3} (202 bekleniyordu): {doc3}')
    c.measure('kilit açıldıktan sonra /api/move', f'HTTP {st3}')
    h.wait_idle()
    c.shot('acildi')
