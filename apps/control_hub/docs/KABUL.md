# SİM kabul betiği — `tools/sim_acceptance.py`

Tek komutla `--sim` Hub'ı başlatır, **headless Chrome** ile arayüzü bir insan gibi sürer ve her adımda
**GEÇTİ / KALDI + ölçülen değer + ekran görüntüsü** yazar. Sonunda tek HTML + MD rapor ve çıkış kodu verir.
Amaç: "çalışıyor gibi görünüyor" yerine **ölçülmüş** kabul (eklem gerçekten 20° döndü mü, STL gerçekten
40 mm mi, ACİL DUR hareketi gerçekten kesti mi).

> SİM **gerçek robot değildir**: ölçümler tahminidir (`docs/SIM.md`). Bu betik arayüz + Hub + SİM zincirini
> sınar; gerçek donanımın yerine geçmez.

## Çalıştırma

WSL'de (`apps/control_hub` içinde; bir kez kurulum):
```
pip install -r requirements-dev.txt -r requirements-step.txt   # STEP adımı için cascadio
pip install playwright                                         # yalnız bu betik ister (CI'da yalnız isteğe bağlı iş)
```
WSL'de:
```
python -m tools.sim_acceptance                    # tüm adımlar, masaüstü 1280×800
python -m tools.sim_acceptance --viewport phone   # telefon 390×844 (dokunmatik)
python -m tools.sim_acceptance --list             # adımlar
python -m tools.sim_acceptance --only Kontrol     # yalnız bir bölüm / adım kimliği (virgülle çoklu)
python -m tools.sim_acceptance --skip parcalar.step --out ./kabul   # atlananlar ATLANDI görünür
```
Docker gerekmez: Hub alt süreç olarak, geçici veri klasörüyle, boş bir portta açılır (`--access-mode open
--no-agent --no-qr`); sizin Hub'ınıza/verinize dokunmaz. Chrome/Chromium sistemde kurulu olmalı
(`HUB_CHROME=/yol` ya da `--chrome`). Süre ≈ 2,5 dk (SİM hızı 100 %).

**Çıkış kodu:** `0` = hiç KALDI yok · `1` = en az bir KALDI (konsola KALDI listesi basılır) **ya da hiç adım
koşmadı** (hepsi atlandı: boş geçiş kabul sayılmaz) · `2` = kurulum hatası (Playwright/Chrome yok).

**Rapor** (`--out` klasörü; varsayılan geçici klasör): `rapor.html` (tek dosya, görüntüler gömülü),
`rapor.md`, `sonuc.json`, `shots/*.png`, `hub.log`. Bir adım KALDI olunca kırıldığı andaki ekran
`<adım>__kaldi.png` olarak eklenir.

## Ne ölçülür

Ölçüm **arayüz metninden değil Hub'dan** (`/api/state`, `/api/parts`, `/api/params`, `/api/log`, …) alınır;
arayüz yalnız sürülür. Böylece "ekranda doğru yazıyor ama robot yanlış yere gitti" yakalanır.

| Adım | Arayüzde yapılan | Ölçülen / eşik |
|---|---|---|
| `kontrol.eklem` | Kontrol → J1 +20° → Git → Home | J1 = önce + 20° ± 0,5°; TCP y değişti (> 20 mm); Home sonrası J1 ± 0,5°, TCP x ± 1 mm |
| `kontrol.jog` | Adım 10 mm; X− sonra Z+ düğmesi | X Δ = −10 ± 1 mm, Y Δ = 0 ± 1 mm, Z Δ = +10 ± 1 mm |
| `kontrol.3b` | 3B sahnede gerçek fare tıklaması → Git | seçilen nokta (140, 40) mm ± 5 mm (piksel nicemlemesi ≈ 0,7 mm/px); bitiş TCP = seçilen ± 2 mm |
| `parcalar.stl` | 40 mm STL küp yükle, yerleştir (140, 0, 60) mm | 12 üçgen; boyut 40 ± 0,5 mm (3 eksen); kayıtlı konum ± 0,5 mm |
| `parcalar.step` | 40 mm STEP kutu yükle (`cascadio`) | ≥ 12 üçgen; boyut 40 ± 0,5 mm (birim tuzağı: mm) |
| `parcalar.dokun` | 3B'de parçanın üstüne dokun → Git | dokunulan nokta = üst yüz ± 4 mm; bitiş TCP = hedef ± 2 mm (yüzeye değer) |
| `algo.program` | Algoritmalar (yay) → Programa ekle → Program → Çalıştır | Program sayfası adım sayısı = üreteç adımı (13); koşucu 13/13 bitirdi; bitiş TCP = yay sonu ± 2 mm |
| `parametreler.taslak` | z_max=10 (< z_min) yaz → hata; 210 yaz → Taslak kaydet → YAML indir | geçersiz değerde Kaydet kapalı; taslak sunucuda 210; YAML 210 içerir; **robota uygulanmadı** (güvenli kutu aynı) |
| `gunluk.olaylar` | Günlük: ACİL DUR olaylarını süz / ara / CSV | arayüz satırı = sunucu `shown`; en yeni olay "kilidi açıldı"; kritik süzgeç = sunucu; CSV başlığı + ACİL DUR; ekranda dosya yolu sızıntısı yok |
| `acil_dur.kilit` (hep **son**) | Hareket sırasında ACİL DUR düğmesi | ≤ 5 sn'de keser (ölçülen ≈ 0,3 sn); 2 sn sonra J1 sabit ± 0,05°; `/api/move` **423**; `/api/sim/reset` **423**; üst çubuk "AKTİF", Git kapalı; kilit açılınca `/api/move` **202** |
| `hub.log` | (tüm koşumda) | günlükte `Traceback` / `ERROR` yok (arayüz sunucu hatasını gizlese bile; ACİL DUR'un bilinçli `CRITICAL` satırı hata sayılmaz). `--only` ile koşulmaz |

Her adım ayrıca **tarayıcı hatası** ölçer: yakalanmamış istisna ya da `console.error` (ör. CSP ihlali) adımı
KALDI yapar. Tek bilinen istisna: `/api/pot` 404 (aşağıda) — hata sayılmaz, adımın notuna yazılır.

## Yeni adım ekleme (ör. P1c klavye)

Bir adım = `@step(kimlik, başlık, bölüm)` ile süslenmiş bir işlev. Çekirdeğe dokunmadan **yeni dosya** eklenir:

```python
# tools/acceptance_steps_klavye.py
from tools.acceptance_core import Ctx, step
from tools.acceptance_ui import enabled, open_page


@step('kontrol.klavye', 'Kontrol: klavye ile jog (P1c)', 'Kontrol')
def kontrol_klavye(c: Ctx) -> None:
    h = c.hub
    open_page(c, 'jog', '.jp-b[data-axis="0"]')
    t0 = h.tcp_mm()
    c.page.keyboard.press('ArrowLeft')          # İSKELET: tuş eşlemesini P1c'nin sözleşmesinden al
    h.wait_idle()
    c.near('X Δ (mm)', h.tcp_mm()[0] - t0[0], -10.0, 1.0, 'mm')   # ölçüm Hub'dan; raporda HER ZAMAN görünür
    c.shot('klavye')
```
Sonra `tools/sim_acceptance.py` içindeki içe aktarmaya `acceptance_steps_klavye` eklenir (bir satır). Kurallar:
- Kimlik tekil olmalı (`--skip/--only` kimlik ya da bölüm adı alır); tekrarında `ValueError`.
- `c.near(ad, ölçülen, beklenen, tolerans, birim)` ölçümü geçse de kaydeder; `c.expect(koşul, ileti)` KALDI üretir.
- Her adım **taze tarayıcı bağlamıyla** ve (ACİL DUR hariç) **Home'a sıfırlanmış SİM'le** başlar.
- Kilidi açık bırakmayan sırayı bozmaması için kilit/ACİL DUR gibi adımlar `last=True` alır (hep sona gider).
- Beklemede `wait_for(c, js, 'ne bekleniyordu')` kullan: zaman aşımı "neyi beklerken takıldı" diye okunur.
- Uygulama kodunu (`control_hub/**`) değiştirme: betik dışarıdan sürer; bulduğun hatayı rapora/PR'a yaz.

## Mutasyon kanıtı

"Temiz main'de GEÇER" tek başına zayıftır; **hata varken KALDI** olduğu da gösterilmelidir:

WSL'de (çalışma ağacı temiz; araç dosyayı her durumda `git checkout` ile geri alır):
```
python -m tools.acceptance_mutate --step kontrol.jog \
    --edit control_hub/web/jog.js "amount: sign * J.step }" "amount: -sign * J.step }"
```
Çıkış `0` = **YAKALANDI** (adım KALDI oldu) · `1` = **SAĞ KALDI** (sınama zayıf) · `2` = kurulum hatası
(kalıp yok/tekil değil, dosya kirli). Aşağıdaki tablo `3557e1a` (main) üzerinde, her biri ayrı koşumla alındı;
uygulama dizini her seferinde `git status` temiz kaldı. Kalıplar kod değişince eskir (araç "kalıp bulunamadı"
der) — o durumda yeni bir eşdeğer hata seçilir.

| # | Bozulan | Dosya | KALDI olan adım ve ölçülen |
|---|---|---|---|
| 0 | Git J1'i yarım açıyla yollar | `web/pages/jog.js` | `kontrol.eklem` — J1 sonra: ölçülen 10,000° beklenen 20,000° ± 0,5° |
| 1 | jog işareti ters | `web/jog.js` | `kontrol.jog` — X Δ: ölçülen 9,900 mm beklenen −10,000 mm ± 1 mm |
| 2 | 3B tıklamada +12 mm x kayması | `web/jog_logic.js` | `kontrol.3b` — seçilen X: ölçülen 153,300 mm beklenen 140,000 mm ± 5 mm |
| 3 | mm birim çarpanı %5 yanlış | `hub_core/mesh_tools.py` | `parcalar.stl` — boyut X: ölçülen 42,000 mm beklenen 40,000 mm ± 0,5 mm |
| 4 | dokun-git hedefi yüzeyden 10 mm uzak | `web/scene_logic.js` | `parcalar.dokun` — bitiş TCP Z: ölçülen 90,000 mm beklenen 80,000 mm ± 2 mm |
| 5 | Programa ekle son adımı düşürür | `web/pages/algo.js` | `algo.program` — Program sayfasında 12 adım var, üreteç 13 üretti |
| 6 | hatalı değerde Kaydet açık | `web/params_form.js` | `parametreler.taslak` — z_max=10 < z_min=20 için 'hata var' uyarısı çıkmadı |
| 7 | seviye süzgeci çalışmıyor | `web/pages/log.js` | `gunluk.olaylar` — 'Kritik' süzgecinden sonra kritik olmayan satırlar kaldı |
| 8 | ACİL DUR'da hareket kabul (route + SİM kilidi) | `server/move_routes.py`, `hub_core/sim_backend.py` | `acil_dur.kilit` — /api/move ACİL DUR sırasında HTTP 202 döndü (423 bekleniyordu) |
| 9 | ACİL DUR düğmesi sunucuya gitmez | `web/app.js` | `acil_dur.kilit` — ACİL DUR 5 sn içinde hareketi kesmedi |

(#8'de iki koruma birlikte kaldırılır: yalnız biri kalkınca öbürü 423'ü yine verir — savunma iki katmanlı.)

## CI

Zorunlu 5 işe **eklenmedi**. `.github/workflows/sim-acceptance.yml` ayrı, **isteğe bağlı** bir iş akışıdır:
Actions → "SİM kabul (isteğe bağlı)" → Run workflow (masaüstü/telefon seçilir). Rapor `sim-kabul-raporu`
artifact'ı + iş özetinde MD olarak çıkar. Playwright yalnız bu iş akışında kurulur (`playwright==1.63.0`,
tarayıcı indirmez; runner'daki `google-chrome` kullanılır). Dal koruması bunu beklemez; `pull_request`
tetiği yalnız iş akışı dosyasının kendisi değişince çalışır.

## Bilinen gürültü ve gözlemler (uygulama koduna dokunulmadı)

- **`/api/pot` 404:** Parametreler sayfası canlı potansiyometre değerini bu uçtan ister, uç henüz yok; konsola
  404 düşer. Bilinen gürültü olarak süzülür, adımın notuna yazılır. Uç gelince süzgeç (`KNOWN_404`) kaldırılır.
- **STEP düğmesi yarışı (Parçalar):** dosya, sayfanın ilk `/api/parts` yanıtından ÖNCE seçilirse STEP düğmesi
  kapalı kalabiliyor. Betik ilk yanıtı bekler; insan bu kadar hızlı değildir ama gözlem olarak Robot_3'e not.
- **Parametreler formu iki kez kurulabilir:** ilk açılışta (mount + onShow) yazılan değer kaybolabiliyor;
  betik `fill_until` ile yeniden yazar, notta "form yeniden kuruldu" görünür.
- **Telefonda Parçalar tuvali ekran dışında** açılır; betik tuvali görünür alana kaydırıp
  `elementFromPoint` ile gerçekten tuval olduğunu doğrular, sonra tıklar.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| `Playwright yok` (çıkış 2) | WSL'de: `pip install playwright` |
| `Chromium açılamadı` (çıkış 2) | Chrome/Chromium kur ya da `HUB_CHROME=/usr/bin/google-chrome` |
| 3B adımları zaman aşımı | WebGL (yazılım) kapalı: bayraklar betikte (`--use-angle=swiftshader …`); eski/çok kısıtlı Chrome olabilir |
| `parcalar.step` KALDI: "dönüştürücü kurulu değil" | `pip install -r requirements-step.txt` (cascadio) ya da `--skip parcalar.step` |
| Port dolu | `--port 0` (varsayılan) boş port seçer; elle verdiysen değiştir |
