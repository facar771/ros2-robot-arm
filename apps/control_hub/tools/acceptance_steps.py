"""Kabul adımları 1/2: Kontrol ve Parçalar (sıra = koşum sırası).
Her adım bir işlevdir: `@step(kimlik, başlık, bölüm)`; `c` = Ctx. Ölçüm kaynağı Hub'ın /api/state'idir
(c.hub); arayüz yalnız SÜRÜLÜR. Yeni adım (ör. P1c klavye): yeni bir `tools/acceptance_steps_<ad>.py` yaz ve
sim_acceptance.py'de içe aktar (docs/KABUL.md)."""
from __future__ import annotations

import base64
import re

from tools.acceptance_core import Ctx, step, stl_cube
from tools.acceptance_ui import (
    click_world,
    enabled,
    open_page,
    set_field,
    set_range,
    wait_for,
    wait_scene,
)

AXES = 'XYZ'


# ============================================== Kontrol ==============================================
@step('kontrol.eklem', 'Kontrol: eklem Git (J1 +20°) ve Home', 'Kontrol')
def kontrol_eklem(c: Ctx) -> None:
    h = c.hub
    j0, tcp0 = h.joints_deg(), h.tcp_mm()
    open_page(c, 'jog', '#jp-t0')
    enabled(c, '#jp-go')
    set_range(c, '#jp-t0', j0[0] + 20)
    c.page.click('#jp-go')
    h.wait_idle()
    j1, tcp1 = h.joints_deg(), h.tcp_mm()
    c.near('J1 sonra (°)', j1[0], j0[0] + 20, 0.5, '°')
    c.expect(abs(tcp1[1] - tcp0[1]) > 20, f'TCP y değişmedi (Δy={tcp1[1] - tcp0[1]:.1f} mm): hareket yok')
    c.measure('TCP Δy (mm)', round(tcp1[1] - tcp0[1], 1))
    c.shot('j1_20')
    enabled(c, '#jp-home')
    c.page.click('#jp-home')
    h.wait_idle()
    c.near('Home sonrası J1 (°)', h.joints_deg()[0], j0[0], 0.5, '°')
    c.near('Home sonrası TCP x (mm)', h.tcp_mm()[0], tcp0[0], 1.0, 'mm')
    c.shot('home')


@step('kontrol.jog', 'Kontrol: jog adımı (X −10 mm, Z +10 mm)', 'Kontrol')
def kontrol_jog(c: Ctx) -> None:
    h = c.hub
    open_page(c, 'jog', '.jp-b[data-axis="0"]')
    enabled(c, '.jp-b[data-axis="0"][data-sign="-1"]')
    c.page.click('.jp-step[data-step="10"]')
    t0 = h.tcp_mm()
    c.page.click('.jp-b[data-axis="0"][data-sign="-1"]')
    h.wait_idle()
    t1 = h.tcp_mm()
    c.near('X Δ (mm)', t1[0] - t0[0], -10.0, 1.0, 'mm')
    c.near('Y Δ (mm)', t1[1] - t0[1], 0.0, 1.0, 'mm')
    enabled(c, '.jp-b[data-axis="2"][data-sign="1"]')
    c.page.click('.jp-b[data-axis="2"][data-sign="1"]')
    h.wait_idle()
    t2 = h.tcp_mm()
    c.near('Z Δ (mm)', t2[2] - t1[2], 10.0, 1.0, 'mm')
    c.shot('jog')


@step('kontrol.3b', 'Kontrol: 3B’de tıkla-git (zemin düzlemine fare ile)', 'Kontrol')
def kontrol_3b(c: Ctx) -> None:
    h = c.hub
    open_page(c, 'jog', '#jp-pick')
    enabled(c, '#jp-pick')
    c.page.click('#jp-pick')
    wait_scene(c, 'jog:')
    z_mm = float(c.page.input_value('#jp-tz'))
    want = [0.14, 0.04, z_mm / 1000.0]                   # güvenli kutunun içinde (x 60-220, y ±100 mm)
    click_world(c, want)
    wait_for(c, "() => /^Hedef: X/.test(document.querySelector('#jp-tinfo').textContent)",
             "3B tıklama hedef seçmedi (#jp-tinfo 'Hedef: X…' olmadı)", 10000)
    enabled(c, '#jp-tgo', 15000)                         # kuru kontrol geçti
    info = c.page.inner_text('#jp-tinfo')
    c.shot('hedef')
    nums = [float(x) for x in re.findall(r'[XYZ] (-?\d+(?:\.\d+)?)', info)]
    c.expect(len(nums) == 3, f'hedef metni okunamadı: {info!r}')
    for k, (got, w) in enumerate(zip(nums, [v * 1000 for v in want])):
        c.near(f'seçilen {AXES[k]} (mm)', got, w, 5.0, 'mm')       # piksel nicemlemesi: ~0.7 mm/px
    c.page.click('#jp-tgo')
    h.wait_idle()
    t = h.tcp_mm()
    for k in range(3):
        c.near(f'bitiş TCP {AXES[k]} (mm)', t[k], nums[k], 2.0, 'mm')
    c.shot('bitis')


# ============================================== Parçalar ==============================================
def clear_parts(c: Ctx) -> None:
    """Her Parçalar adımı boş listeyle başlar (önceki adımın izi ölçümü bozmasın)."""
    for p in c.hub.api('/api/parts')[1].get('parts', []):
        c.hub.api(f'/api/parts/{p["id"]}', 'DELETE')


def parts_loaded(c: Ctx) -> None:
    """İlk /api/parts yanıtı işlendi ("0 / 20 parça" yazısı). Dönüştürücü durumu bu yanıtla gelir; dosya
    ondan ÖNCE seçilirse STEP düğmesi yanlışlıkla kapalı kalır (gözlem: insan bu kadar hızlı değildir)."""
    wait_for(c, r'() => /\d+ \/ \d+ parça/.test(document.body.textContent)',
             "Parçalar listesi 'N / M parça' göstermedi", 20000)


def upload_file(c: Ctx, name: str, data: bytes) -> dict:
    """Yükleme alanına dosya verir, 'Yükle'ye basar; sunucudaki parça kaydını döndürür."""
    parts_loaded(c)
    c.page.set_input_files('input[data-f="file"]',
                           files=[{'name': name, 'mimeType': 'application/octet-stream', 'buffer': data}])
    c.page.click('button[data-act="upload"]')
    c.page.wait_for_selector(f'.part-row:has-text("{name.rsplit(".", 1)[0]}")', timeout=30000)
    parts = c.hub.api('/api/parts')[1]['parts']
    c.expect(len(parts) == 1, f'{name}: yükleme sonrası {len(parts)} parça var (1 bekleniyordu)')
    return parts[0]


def size_mm(part: dict) -> list:
    return [v * 1000.0 for v in part['size_m']]


def wait_part_in_scene(c: Ctx) -> None:
    wait_for(c, '() => HubScene.list().some((i) => i.startsWith("parts:"))',
             "3B sahnede 'parts:' nesnesi çizilmedi", 30000)


@step('parcalar.stl', 'Parçalar: STL yükle (40 mm küp) ve yerleştir', 'Parçalar')
def parcalar_stl(c: Ctx) -> None:
    clear_parts(c)
    open_page(c, 'parts', 'input[data-f="file"]')
    part = upload_file(c, 'kup40.stl', stl_cube(40.0))
    c.expect(part['triangles'] == 12, f'üçgen sayısı {part["triangles"]} (12 bekleniyordu)')
    c.measure('üçgen', part['triangles'])
    for k, s in enumerate(size_mm(part)):
        c.near(f'boyut {AXES[k]} (mm)', s, 40.0, 0.5, 'mm')
    c.page.wait_for_selector('input[data-f="x"]', state='visible')
    for key, val in (('x', '140'), ('y', '0'), ('z', '60')):
        set_field(c, f'input[data-f="{key}"]', val)
    wait_for(c, '() => /Kaydedildi/.test(document.body.textContent)',
             "Parça konumu kaydı 'Kaydedildi' iletisini göstermedi", 10000)
    pose = c.hub.api('/api/parts')[1]['parts'][0]['pose']['pos_m']
    for k, want in enumerate((0.14, 0.0, 0.06)):
        c.near(f'kayıtlı konum {AXES[k].lower()} (mm)', pose[k] * 1000, want * 1000, 0.5, 'mm')
    wait_scene(c)
    wait_part_in_scene(c)
    c.shot('stl')


@step('parcalar.step', 'Parçalar: STEP yükle (40 mm kutu → mm ağı)', 'Parçalar')
def parcalar_step(c: Ctx) -> None:
    from tests.helpers_step import step_box
    clear_parts(c)
    open_page(c, 'parts', 'input[data-f="file"]')
    info = c.hub.api('/api/parts')[1].get('step') or {}
    c.expect(bool(info.get('available')),
             "STEP dönüştürücü kurulu değil: WSL'de `pip install -r requirements-step.txt` (cascadio) ya da "
             f"--skip parcalar.step. Sunucu: {str(info.get('message', ''))[:100]}")
    part = upload_file(c, 'kutu40.step', step_box(40.0, 40.0, 40.0))
    c.measure('üçgen', part['triangles'])
    c.expect(part['triangles'] >= 12, f'STEP ağı boş/eksik ({part["triangles"]} üçgen)')
    for k, s in enumerate(size_mm(part)):
        c.near(f'boyut {AXES[k]} (mm)', s, 40.0, 0.5, 'mm')
    wait_part_in_scene(c)
    c.shot('step')


@step('parcalar.dokun', 'Parçalar: 3B’de parçaya dokun → Git (yüzeye değ)', 'Parçalar')
def parcalar_dokun(c: Ctx) -> None:
    h = c.hub
    clear_parts(c)
    body = {'filename': 'kup40.stl', 'data_b64': base64.b64encode(stl_cube(40.0)).decode(),
            'kind': 'workpiece', 'unit': 'mm'}
    st, doc = h.api('/api/parts', 'POST', body)
    c.expect(st in (200, 201) and doc.get('ok'), f'parça API ile yüklenemedi: HTTP {st} {doc}')
    pid = doc['part']['id']
    st, doc = h.api(f'/api/parts/{pid}', 'PUT', {'pose': {'pos_m': [0.14, 0.0, 0.06], 'rpy_deg': [0, 0, 0]}})
    c.expect(st == 200, f'parça yerleştirilemedi: HTTP {st} {doc}')
    open_page(c, 'parts', 'button[data-act="touch"]')
    wait_scene(c)
    wait_part_in_scene(c)
    c.page.click('.part-row .part-main')                 # parçayı seç: kamera ona odaklanır
    top = [0.14, 0.0, 0.08]                              # üst yüz ortası (merkez z 60 mm + yarım kenar 20 mm)
    click_world(c, top)
    c.page.wait_for_selector('text=/^Nokta \\(mm\\)/', timeout=10000)
    info = c.page.inner_text('text=/^Nokta \\(mm\\)/')
    nums = [float(x) for x in re.findall(r'(-?\d+(?:\.\d+)?)', info.split('·  yüzey')[0])]
    c.expect(len(nums) >= 3, f'seçilen nokta okunamadı: {info!r}')
    for k in range(3):
        c.near(f'dokunulan {AXES[k]} (mm)', nums[k], top[k] * 1000, 4.0, 'mm')
    c.page.fill('input[data-f="speed"]', '100')
    c.page.click('button[data-act="touch"]')
    c.page.wait_for_selector('text=/Kontrol geçti/', timeout=20000)
    c.shot('kontrol_gecti')
    c.page.click('button[data-act="confirm"]')
    h.wait_idle()
    t = h.tcp_mm()
    for k in range(3):
        c.near(f'bitiş TCP {AXES[k]} (mm)', t[k], nums[k], 2.0, 'mm')
    c.shot('bitis')
