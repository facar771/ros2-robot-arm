# Yol Haritası (tek doğruluk kaynağı)

Son güncelleme: 2026-11-06 (akşamüstü) · main 95b4c4e (PR #64; #62, #63, #64 birleşti) · Güncelleyen: Müdür

## Kullanım kuralları (herkes için)
1. **Her görev bu dosyadaki bir ID'ye bağlıdır** (örn. `L3`). PR başlığına/gövdesine "Yol haritası: L3" yaz. ID'si olmayan iş yapılmaz; yeni iş eklemek Ferhat ya da Müdür kararıdır.
2. **Bu dosyayı yalnızca Müdür günceller** (merge sonrası, toplu ve küçük bir docs PR'ı ile). Çalışanlar kutuları işaretlemez; böylece paralel PR'lar bu dosyada çakışmaz.
3. Göreve başlamadan önce: ilgili bölümü, bağımlılıkları ve "Değişmez ilkeler"i oku. Bağımlılığı bitmemiş maddeye başlama, dur ve Ferhat'a söyle.
4. Kapsamın dışına çıkma. Bulduğun ek iş/risk varsa PR gövdesinde "Yol haritasına öneri" başlığıyla yaz, yapma.
5. Her PR'da "Denenmeyenler" bölümü zorunlu. Doğrulayamadığını "çalışıyor" yazma.
6. **PR başlığı yol haritası ID'siyle başlar:** `L3: Hub jog paneli`. Eski adlandırma karışıklığı: #42 (başlıkta "J4") = **L4**, #44 (başlıkta "J3") = **L3**. Bu dosyadaki J3/J4 profil işleridir (J3 bitti, J4 Hub'da profil görüntüleme). Karışırsa bu dosyadaki ID geçerlidir.
7. Bu dosya Ferhat ve Müdür içindir. Çalışan yalnızca kendi görevini okur, değiştirmez.

## Müdür iş akışı
1. Göreve başlamadan önce `main` ve açık PR'ları kontrol et, bu dosyayı oku. Önerilen iş dosyada yoksa uydurma görev yazma; Ferhat'a sor.
2. Görev verirken çalışana: ID, sahiplik alanı, bağımlılıklar, kabul ölçütü, "Denenmeyenler" zorunluluğu, yasak alanlar (başkasının dosyaları).
3. PR birleşince: ilgili satırı `[x]` yap, PR numarasını yaz, "Sıradaki işler"i ve "Son güncelleme"yi (tarih + main SHA) düzelt, değişiklik günlüğüne bir satır ekle. Hepsini tek küçük docs PR'ında topla.
4. Bir madde yarım kalırsa `[x]` yapma. Kısmi bitişi satıra yaz ("kısmen: ...").
5. Ferhat'ın söylediği yeni iş: önce buraya madde olarak eklenir, sonra çalışana verilir. Ferhat'a her güncellemede kısa durum ver: bitenler, açık PR'lar, sıradaki iş, onun yapacakları.

Durum: `[x]` bitti ve main'de · `[ ]` yapılmadı · 🟡 PR açık · ⏸ bekliyor (neden yazılı)

## Sahiplik
| Sohbet | Alan |
|---|---|
| Robot_2 | `src/robot_core` (profil, hardware profil, jog çekirdeği), `src/robot_description/profile`, `docs/ROBOT_PROFILE.md`, `docs/JOG.md` |
| Robot_3 | Robot API (`src/robot_viz`, supervisor/jobs/webview), `src/robot_agent`, `src/robot_hw_bridge`, paylaşılan dosyalar (`ci.yml`, `nightly.yml`, `compose.yaml`, `docker/run_selftest.sh`, `pyproject.toml`) |
| Robot_4 | `apps/control_hub/**`, `hub-exe.yml`, `docs/HARDWARE.md` (B1 protokolü) |
| Robot_5 | `src/robot_vision*`, `src/robot_plugins`, `plugins/**` |
| Robot_7 | `research/rl` |
| Robot_1 / yaşlı kurt | Derin inceleme ve mimari karar (Ferhat soruları ona götürür) |

## Değişmez ilkeler
- **Robot kendi tarafı son sözü söyler:** Robot API (güvenli kutu, ACİL DUR) ve ESP32 (kendi limitleri, watchdog). LLM, Hub, arayüz yalnızca öneri yapar.
- Yazılım ACİL DUR'u donanım acil durdurmanın yerine geçmez. Gerçek donanımda NC buton sürücü/ENABLE hattını yazılımdan bağımsız keser.
- Robot API v1'de yalnızca eklemeli değişiklik. `reset` aracı ajanda yok.
- Jog zinciri: istemci anında < sunucu TTL 250 ms < ESP32 watchdog 500 ms. Dead-man sunucuda.
- Varsayılanlar her zaman daha dar (hız, kutu, limit). Açılış durumu: motorlar kapalı, unhomed.
- API anahtarı yalnızca Hub'da, bellekte; "beni hatırla" işaretlenirse Windows Credential Manager. Dosyaya/log'a asla.
- Her merge sonrası: WSL'de `cd ~/ros2-robot-arm && git checkout main && git pull` (+ kod/imaj değiştiyse `docker compose build`). Selftest: `docker compose run --rm selftest-<mod>`.

## Sıradaki işler (öncelik sırasıyla)
1. L3, O1, H6, Q1, O2, L9a, L9b, F4a, F4a-2 bitti; L7 kısmen; H4 ve L8 yalnız API kısmı (#57, #64). **Ferhat önceliği: robotu Hub'da görmek: L9c (Robot_4, tek kalan parça).** Robot_3: **F4b** (güvenlik, gerçek robottan önce zorunlu). Robot_4: L9c → L8 Hub sihirbazı → O3 → H4 arayüz. Robot_2: **J5 çekirdeği** (şema/doğrulayıcı), sonra K6. Robot_5: P1–P3 hazırlığı (Q1, L8 API hazır; P1 profil tarafı Robot_2'de).
2. ~~Eski paralel liste~~ (hepsi bitti: H6 #52, H4 API #57, L7 çekirdek #50, O1 #51):
   - **H6** hareketli kamerada algı doğruluğu (Robot_5)
   - **H4 (API kısmı)** izleme kamerasını Robot API'ye bağla (Robot_3). Arayüz kısmı L3 sonrası (Robot_4).
   - **L7** küresel çerçeve çekirdeği (Robot_2)
   - **O1** nokta deposu çekirdeği (Robot_3, yeni dosya)
3. L4 (#42) ve Q1 (#53) bitti. Sırada **O2** API, **O3** Hub "Algoritmalar" sayfası, **L8** küresel çerçeve API+Hub, **H4 arayüz**.
4. **J5** esnek profil (K8'den önce), **K6–K8** karma robot (2 step + SG90 servo) için aktüatör türü ve profil. Donanım malzemeleri elde, firmware (K3) buna bağlı.
5. **P1–P4** tool kamerası + nişan (Q1, L7/L8, H6 sonrası). **J4** Hub'da robot profili. **N** bölümü doğrulama borçları.

---

## A. Temel altyapı ve simülasyon
- [x] A1 Temel altyapı: çekirdek, Docker, CI (#1)
- [x] A2 Selftest kararlılığı (#2)
- [x] A3 Gazebo fiziği, başsız (#3)
- [x] A4 Derinlik kamerası (#4)
- [x] A5 Güvenilirlik: digest/kilit dosyaları, gecelik koşu, CI annotation, ARIZA_GIDERME (#20)
- [ ] A6 Gazebo GUI ve WSLg'de RViz kamera görüntüsü denenmedi (N1'e bak)

## B. Algı ve sahne
- [x] B1 Renkli nesne algısı (#5)
- [x] B2 Algı → MoveIt çarpışma nesneleri (#6)

## C. Veri katmanı
- [x] C1 SQLAlchemy 2 + Alembic, PostgreSQL (#7)

## D. Hareket ve Skills
- [x] D1 Poz hedefli hareket ve Skills katmanı (#8)
- [ ] D2 Aşama 8: tutucu + al-bırak ⏸ tutucu/profil kararı ve donanım gelince (Robot_3)

## E. Web görünüm ve komut
- [x] E1 Tarayıcıda canlı görünüm (#9), RViz yapılandırması (#10)
- [x] E2 Tarayıcıdan komut (beyaz liste) + demo senaryosu (#11, #12)

## F. Robot API v1 ve güvenlik
- [x] F1 Robot API v1 sözleşmesi + ACİL DUR (/api/stop, /api/reset, 423) (#14)
- [x] F2 Asenkron işler (/api/jobs) + güvenli kutu + hız tavanı (#15)
- [ ] F3 Çalışma zamanı self-collision kontrolü (v2, ~10 Hz durum geçerlilik sorgusu)
- [ ] F4 **Yol boyunca güvenli kutu denetimi** (Robot_1 kararı, 6 Kasım; çarpışma denetiminin yerine geçmez, ayrı kuraldır). Bugün kutu yalnız hedef noktada denetleniyor (`go_to_position`, `go_above`, `goto_waypoint`); iki kutu-içi nokta arasındaki eklem uzayı yolu kutudan çıkabilir. Çözüm: planla, doğrula, çalıştır; yolu kartezyen kısıtlama (Pilz LIN gibi çizgi kısıtı tekillikte başarısız olur, waypoint'in anlamını değiştirir). **MoveIt planı yürütülmeden önce** ROS'suz yol doğrulayıcı ardışık plan noktaları arasını eklem uzayında örnekler, her örnekte FK ile TCP'yi kutuya karşı denetler; örnekleme yoğunluğu, iki örnek arası TCP yer değiştirmesi (eklem farkı × kolun azami erişimi) üstten sınırlanacak biçimde (en kötü durum), yürütücünün enterpolasyonuyla aynı yoğunlukta. Kurallar: bir örnek dışarıdaysa plan reddedilir, hareket başlamaz, hata ihlal noktasını ve koordinatını söyler; reddedilen plan en çok 2–3 kez yeniden planlanır, olmazsa hata (kutu sessizce genişletilmez); başlangıç kutu dışındaysa (örn. home) yalnız kutuya yaklaşırken (uzaklık azalıyor) izin verilir, sonra tamamen içeride kalmalı; denetim ortak yürütme katmanında (GuardedMotion) tek kez yazılır, `goto_waypoint`, `go_to_position`, `go_above` ve adlandırılmış eklem pozları (home, ready) aynı koddan geçer. **İlk iş:** home/ready hareketinin kutu dışından başlayıp başlamadığını testle gör. **Not (kullanıcıya açıkça yazılır):** kutu yalnız uç noktayı (TCP) sınırlar, dirsek/bilek kutu dışına çıkabilir; bu ayrı bir karar, F4 kapsamı değil. **F4 bitmeden `mode: real` açılmaz** (J5 kapısına eklendi). **BULGU (F4a, #59):** etkin kutu x[0,05;0,75] y[-0,5;0,5] z[0,02;0,7]; `home` (0, 0, 1,24), `cnc`, `pc`, `dik` kutu DIŞINDA (home kutuya 542 mm), yalnız `ready` içinde. `home→ready` yaklaşma kuralıyla geçer; **`ready→home`, `ready→cnc`, `ready→pc`, `ready→dik` reddedilir** (hedef dışarıda; "Eve dön" bozulur). **Karar Robot_1'de (kutu mu genişler, adlandırılmış pozlar için güvenli muafiyet mi); F4b karar gelene kadar başlamaz.** **KARAR (Robot_1, 6 Kasım, home/cnc/pc/dik bulgusuna): kutuyu GENİŞLETME (a) hayır; (b)'nin sıkı biçimi.** İç kutu aynen kalır (görev hareketleri), yanına dar kapsamlı **park zarfı** gelir. Muafiyet güvenlik tarafında, PROFİLDE DEĞİL (Hub profil düzenleyicisi J4/J5 kendi muafiyetini yazamasın); profil yalnız pozları tanımlar; her park pozunun doğrulanmış eklem değeri güvenlik dosyasında durur, çalışma zamanında profildeki değer farklıysa poz muaf sayılmaz ve reddedilir. Yalnız merkez (hub-and-spoke): muafiyet sadece `go_to_named_pose` ile ve bir ucu `ready` olan harekette; jog, waypoint, `go_to_position`, ajan iç kutuya tabi. Yol MoveIt'e bırakılmaz: ready'den park pozuna düz eklem enterpolasyonu, `check_path` park zarfına karşı doğrular; başlangıç `ready`'ye ~0,02 rad yakın olmalı; hız tavanı azami hızın %30'u (öneri); çarpışma ve ACİL DUR aynen. Park zarfı ölçülerden türetilir (doğrulanmış geçiş yollarının sınırı + 5 cm marj), "geçecek kadar" ayarlanmaz, tolerans eklenmez. **"Eve dön" makrosu** = (ready'de değilse önce ready'ye, normal ve kutu denetimli) + ready→home park geçişi; home'dan çıkış zaten izinli (yaklaşma kuralı). **Park listesine yalnız `home` girer** (cnc/pc/dik yalnız geliştirici selftest'lerinde ve SRDF'te var, Hub/ajan kullanmıyor; gerçekten kullanılırsa tek tek doğrulanıp eklenir; Ferhat onayı bekleniyor). **Müdür notu (Robot_1 ONAYLADI, 6 Kasım; üç ek şart: dosya elle yazılır ve üretilmez, başlığı "üretilmez, profil/Hub tarafından değiştirilemez" der; Hub ve Robot API'de bu dosyaya yazma yolu yoktur, değişiklik yalnız kod gözden geçirmesiyle (PR); profildeki poz değişirse muafiyet kendiliğinden düşer; CI testleri: `profile_gen` park dosyasını değiştirmez, park pozunu değiştiren profil reddedilir; yetki tek yerde: profilin tanımlayabildiği ile robotun izin verdiği ayrı dosyalarda):** `safe_zone.yaml` `profile_gen` ile profilden ÜRETİLİYOR; `park:` bölümü bu dosyaya yazılırsa muafiyeti yine profil (ve Hub düzenleyicisi) belirler, Robot_1'in amacını bozar. Bu yüzden park bölümü üretilmeyen ayrı güvenlik dosyasında (örn. `safe_zone.park.yaml`) olacak, `profile_gen` ona dokunmaz (testle kanıtlanır).
  - [x] F4a (#59) ROS'suz `check_path` + testler (Robot_2, `robot_core`): kutuya giren-çıkan ara yol, başlangıç dışarıda, örnekleme sınırı, "kaçak yok" kanıtı. **Doğrulama durumu:** yalnız yerel pytest (Docker/ROS'ta koşulmadı); yürütücünün gerçek enterpolasyonunun düz eklem enterpolasyonuyla aynı olduğu doğrulanmadı; yaklaşma kuralı yalnız örneklerde; duvara 0,05 mm'den yakın yollarda ≤0,05 mm kaçak payı. Bağlama yok (F4b).
  - [x] F4a-2 (#63) Park muafiyeti şeması + `check_path`'e park zarfı desteği + testler (Robot_2): üretilmeyen güvenlik dosyası (yalnız `home`, doğrulanmış eklem değerleri, park zarfı = doğrulanmış geçiş sınırı + 5 cm); testler: `ready→home` geçer, rastgele nokta→home reddedilir, profildeki `home` değişirse reddedilir, jog/waypoint/`go_to_position` park zarfını kullanamaz, `profile_gen` güvenlik dosyasına dokunmaz, Hub/profil yolu muafiyeti yazamaz. **Doğrulama durumu:** yalnız `robot_core` seviyesi (CI'da birim testler + mock selftest yeşil, kütüphane düzeyinde); dosya izni/dosya sistemi ile korunma kanıtlanmadı (Hub/profil API'sinin dosyayı yazmadığı kaynak taramasıyla gösterildi; ben de `src` ve `apps` içinde başka yazan yer bulamadım); bağlama yok (F4b); yürütücünün gerçek enterpolasyonu doğrulanmadı; zarf dikey 0,41–1,29 m, park hareketi sırasında çalışma alanı boş olmalı (operatör sorumluluğu).
  - [ ] F4b Yürütme katmanına bağla + selftest (Robot_3; F4a-2'den sonra; O2'nin devamı): `GuardedMotion` tek ortak yer; `goto_waypoint`/`go_to_position`/`go_above`/adlandırılmış pozlar aynı kodu kullanır; park geçişi `go_to_named_pose` ile, bir ucu `ready`, düz eklem enterpolasyonu, hız %30; "Eve dön" makrosu. Selftest: iç kutudan çıkan iki içeride nokta REDDEDİLİR, `ready→home` GEÇER. `INTERFACES.md`'deki "bilinen açık" notu kalkar. Selftest/CLI'nın ham `MoveItMotion` yolu (örn. `scene_check` home→cnc) kutu denetimsiz kalır (geliştirici aracı), belgelenir.

## G. LLM ajanı
- [x] G1 Güvenli LLM araç katmanı `robot_agent` (#24)
- [x] G2 `run_plugin` aracı + `gazebo-integration` selftest (#29)
- [x] G3 OpenAI adaptörü + anahtar sızıntı koruması + enjeksiyon testleri (#33)
- [x] G4 Hub sohbet paneli, onay kuyruğu, anahtar deposu (#37)
- [x] G5 `.exe`'ye ajan paketleme + Windows smoke (#38)
- [ ] G6 Gerçek LLM anahtarıyla ilk deneme (önce `--dry-run`); Anthropic güncel API ayrıntıları resmî dokümandan teyit (N2)

## H. Görüntü eklentileri ve kamera
- [x] H1 Görüntü çekirdeği, takip, eklenti SDK'sı, tarama (V1–V4, #25 içinde)
- [x] H2 Eklenti tek giriş noktası `run_plugin` (#26), şema NaN/inf reddi (#30)
- [x] H3 Kamera platformu elle kontrol (pan/tilt/konum) (#32). **Not:** Bu, kolun ucundaki kamera DEĞİL; `tracking.sdf`'teki ayrı yaw/slide/pitch platformu. Yalnız ROS düğümü (`camera_manual`); Robot API ve Hub arayüzü yok (H4). Kol ucu kamerası için R1'e bak.
- [ ] H4 **İzleme kamerasını hareket ettirilebilir yap**: robotu ve masadaki parçaları tam görmek için ROS/Gazebo'daki kamera platformunu (H3: pan/tilt/slide) Robot API'ye ve Hub'a bağla; Hub'da canlı görüntü + kamera tuşları. API kısmı hemen (Robot_3), arayüz L3'ten sonra (Robot_4). Kamera hareketi robot güvenliğini etkilemez, ACİL DUR kamera platformunu da durdurur. **Kamera kareleri (Robot_1 önerisi):** Hub yalnız `/frame/rgb.png` ve `/frame/depth.png` yollarını, GET, sorgusuz, PIN oturumuyla sunucu tarafında aktarır; telefonda PNG bant genişliği doğrulanmadı (JPEG gerekebilir). **Durum (6 Kasım):** API kısmı bitti (#57: `/api/camera`, selftest; kapanış çökmesi düzeltildi); Hub arayüzü (Robot_4) yok, bu yüzden kutu işaretlenmedi.
- [x] H6 (#52) **Hareketli kamerada algı (Robot_5):** `INTERFACES.md` canlı kamera notu "kamera pozu workspace.sdf'ten sabit, hareketli platform için kare başına poz yok" diyor. Platform dönünce nesne konumları dünya çerçevesinde yanlış çıkabilir. Doğrula (ROS algısı TF `world←camera_optical_frame` kullanıyor mu?), kare başına poz kullan, testle kanıtla (kamera pan/tilt'ten sonra sabit nesnenin World konumu ≤ 1 cm kayar). Tool kamerası (P) için de önkoşul. **Doğrulama durumu:** kare başına poz (RosFrameSource platform modu) ve birim testi (3 mm derinlik gürültüsü, mükemmel eklem durumu varsayımı) var; canlı Gazebo'da platform durduktan sonra (1,5 s bekleyerek) denendi. Platform hareket ederken canlı, gerçek derinlik kamerası ve saat senkronu denenmedi; `perception_check` CI'da çalışmıyor.
- [ ] H5 Eklentiler Hub'da (`run_plugin` Hub'da şimdilik kapalı)

## I. Control Hub
- [x] I1 Çekirdek + web arayüzü, sahte robot (#13)
- [x] I2 Gerçek Robot API v1'e bağlantı (#27)
- [x] I3 Windows `.exe` + masaüstü penceresi + hub-exe.yml (#28)
- [ ] I4 Hub: ESP32 yardımcı cihazlar (Wi-Fi) + kural motoru

## J. Robot profili (kinematik + donanım)
- [x] J1 Tasarım notu + bugünkü robotun profili (#31)
- [x] J2 Okuyucu + doğrulayıcı + üretici; üretilen = mevcut (#34)
- [x] J3 `hardware.profile.yaml` + eklem↔motor dönüşümü + firmware C limit başlığı (#36)
- [ ] J4 Hub'da profil salt-okunur gösterim, sonra düzenleme arayüzü (eksen yönü/tipi, limit, link uzunluğu, motor yön/ofset). Üretilen dosyalar elle düzenlenmez.

## K. Donanım hattı (ESP32, step motor, encoder isteğe bağlı)
- [x] K1 Protokol (`HARDWARE.md`, COBS+CRC+seq), codec, ESP32 simülatörü, Hub COM↔TCP köprüsü (#35)
- [x] K2 `robot_hw_bridge` ROS düğümü, iki yönlü watchdog, `selftest-hw` (#39)
- [ ] K3 Firmware iskeleti (PlatformIO, derleme CI) ⏸ K6'ya bağlı (malzemeler elde); gerçek donanımda denenmediğini yaz. **İki hedef kart:** `uno` (PCA9685 varsa I2C ile; yoksa servolar doğrudan Uno pinlerinden `Servo` kütüphanesiyle, Timer1 kullanır, step için zamanlayıcı kullanmayan `AccelStepper`) (küçük karma robot: 2 step + 6 SG90 PCA9685 üzerinden, ek sensör yok) ve `esp32` (büyük robot). Uno hedefi için bellek/CPU bütçesi (2 KB RAM) baştan sınanır: protokol çerçeve tamponu, hareket tamponu, adım üretim hızı ile I2C yazımının birbirini bozmaması
- [ ] J5 **Eksik bilgiyle çalışan, sonradan doldurulan robot profili** (Robot_2 şema/doğrulayıcı, Robot_4 Hub; J4 ile birlikte tasarlanır). Fiziksel değerler (kol uzunluğu, kütle, motor/sürücü modeli, dişli, encoder/limit anahtarı, servo darbe aralığı, tork) sonradan girilebilir. Kurallar:
  1. Her değer bir **kalite etiketi** taşır: `measured` (ölçüldü) · `estimated` (tahmin) · `placeholder` (yer tutucu). Boş bırakılan değer yok, ya etiketli varsayılan ya da `null`.
  2. Profil üç olgunluk seviyesinde doğrulanır: **taslak** (şema geçerli) → **simülasyona hazır** (kinematik ve limitler tutarlı) → **donanıma hazır** (kritik alanların hepsi `measured`: link uzunlukları, motor türü, adım/dişli, eksen limitleri, home/limit anahtarı, servo aralığı, tork denetimi geçti).
  3. **`mode: real` ve ESP32'ye hareket komutu, yalnız "donanıma hazır" profille açılır.** Placeholder/estimated değer gerçek donanımda sessizce kullanılmaz, robot reddeder ve eksik alanları söyler. **F4 (yol boyunca kutu denetimi) bitmeden de açılmaz.**
  4. Hub'da profil sihirbazı: eksik/tahmini alanları listeler, adım adım doldurulur, kaydedince üretilen dosyalar yeniden üretilir (elle düzenleme yok). Değişiklik günlüğü kalır (kim, ne zaman, eski → yeni).
  5. Testler: eksik alanlı profil simülasyonda açılır ama real'de reddedilir; etiket düşürme (measured → estimated) olgunluğu geri düşürür.
- [ ] K4 İlk bring-up prosedürü (motorlar yükten ayrık, düşük hız, homing, sınır anahtarı, NC acil dur)
- [ ] K6 **Aktüatör türü `hobby_servo` (SG90) + iki robot** (Robot_2 `hardware.py`, Robot_4 `HARDWARE.md`/protokol/simülatör). Bugünkü donanım profili ve protokol yalnız step motor varsayıyor (`motor_type: 'step'`, MOVE hedefi adım, HOME/ENABLE durum makinesi). Servo için: eksen türü alanı `step | hobby_servo`; servo alanları: darbe µs ↔ açı, açı sınırı, yazılım hız (slew) sınırı; **geri besleme yok** → konum = komut, "kestirim" olarak işaretlenir (step encoder'sız kuralı gibi); homing yerine bilinen başlangıç pozu; açılışta ani sıçramayı önlemek için rest açısına yavaş yaklaşma; ENABLE = PWM çıkışı aç/kapa; simülatöre servo modeli. Karışık (step + servo) robotta her eksen kendi türünde. **Referans modu `manual_zero` (elle sıfır):** limit anahtarı ve encoder olmayan eksen için operatör robotu bilinen mekanik duruşa (sert dayanak/işaretli poz) koyup "burası sıfır" onayı verir; bugünkü `HARDWARE.md` kuralı (referanslanamayan eksen asla HOMED olamaz, `NO_REFERENCE`) bu mod için açıkça genişletilir. Her açılış ve ACİL DUR sonrası yeniden onay şart, konum "kestirim" olarak işaretli kalır. Mevcut step davranışı ve testleri değişmez.
- [ ] K7 **Servo güç ve güvenlik kuralları** (`HARDWARE.md`): servolar için ayrı 5 V besleme (akım payı: SG90 durma akımı datasheet'ten doğrulanır), ortak GND, brown-out önlemi; donanım acil durda servo gücü kesilir (PCA9685'in OE pini yoksa, yani servolar doğrudan Uno pinlerindeyse, **tek donanım yolu servo besleme hattını NC butonla/röle ile kesmektir**; sinyal pinleri yazılımsız kapatılamaz; servo gücü firmware açılıp ilk pozlar yazıldıktan sonra verilir, açılışta sıçramayı önler) (NC hat röle/MOSFET üzerinden) ve **kol yerçekimiyle düşebilir**, bunun için mekanik/duruş notu; **tork denetimi:** profil doğrulayıcı URDF kütle ve kol uzunluğundan statik tork hesaplar, motor/servo tork sınırını aşıyorsa profili reddeder (SG90 tork değeri datasheet'ten doğrulanır, bu belgede varsayım yok).
- [ ] K8 **İki robot profili** (Robot_2): `profile/` altında birden çok robot (şimdi tek dosya `six_axis_robot.yaml`). Önce **karma robot** (2 step + geri kalanı SG90, basit olan, malzemeler elde), sonra **tam step robot**. Profil seçimi, her profilin CI'da üretim=commit kontrolü, simülasyonda profile göre robot yükleme. Ferhat gerçek ölçüleri (eksen sayısı, link uzunlukları, kütle, hangi eksenler step/servo, motor/sürücü modeli, encoder/limit anahtarı) **sonra** verecek; o zamana kadar profil J5 kurallarıyla `placeholder`/`estimated` etiketli değerlerle simülasyonda çalışır, gerçek donanıma kapalıdır.
- [ ] K5 Wi-Fi kanalı (HMAC + seq, OTA kapalı/imzalı), USB-seri sonrası

## L. Jog (elle kontrol, FANUC tarzı frame'ler)

> **Terimler:** "Düzlem" (plane) = 3 noktayla öğretilen User frame'in XY düzlemi (ya da World, yani robot tabanına göre zemin). "Yere/düzleme paralel ileri-geri" = o düzlemde jog. Joint = tek eksen, World = tabana sabit, Tool = takım ucunun kendi eksenleri, User = öğretilen düzlem.

- [x] L1 Jog çekirdeği: SE3/W-P-R, 3 noktadan çerçeve, FK/Jacobian, DLS motoru (#40)
- [x] L2 Robot API: /api/jog (kiralama+TTL), /api/frames, jogging durumu, selftest-jog (#41)
- [x] L3 Hub jog paneli + 3 nokta öğretme sihirbazı (#44, #46): çerçeve seçici (Joint, World, Tool, User), artımlı adım, basılı-tut (real/telefonda kapalı), "Hazır pozuna git", 409/423/403/502 metinleri; sihirbaz: orijin, +X yönü (≥20 mm), XY düzlemi (+Y tarafı), "Şu anki TCP'yi kaydet", düzlem listeleme/seçme/silme (yeniden adlandırma yok). **Doğrulama durumu:** Ferhat arayüz akışını sahte robotla denedi ve kabul etti; sahte robot dönüş hesaplamaz (arayüzde uyarı çıkar). Gerçek Robot API, gerçek FK ve gerçek robotta TCP kaydı (kol durduktan sonra kaydedilmeli, Hub önbelleği ≤~200 ms bayat) denenmedi, bunlar L5'e ve L9'a kalır.
- [x] L4 TCP profilde + `FrameStore.replace_all` + ready pozu (#42)
- [ ] L6 **Düzleme paralel jog ("düzlem modu")**: seçili çerçevenin (World ya da 3 noktayla öğretilen User) XY düzleminde ileri-geri/sağ-sol hareket; Z ve takım yönelimi kilitli (Z ayrı tuş). Önce L1/L2'deki World/User jog'unun bunu zaten karşılayıp karşılamadığı Robot_2/Robot_3 tarafından doğrulanır, eksik varsa eklenir; hesap çok tekilliğe duyarlı olmalı (L1 DLS'i kullan). Arayüz tuşları L3'ün parçası olabilir, sahibi L6 ilk adımında belirlenir.
- [ ] L7 **kısmen** (#50 birleşti: çekirdek, `docs/JOG.md` §3b; Gazebo/MoveIt/gerçek robotla HİÇ denenmedi; "takım +z ile taban ekseni C" bu kutuda bulunamadığı için taban ekseni testleri `tool_axis='-z'` ile yapıldı; yükselme tavanı yalnız 15° ayarıyla testlendi; `FrameStore` kalıcılığı/API/Hub = L8). **Küresel çerçeve (merkez noktalı, "kubbe kamera")** (Robot_2, `FrameStore` + `jog`): çerçeve türü `sphere`. Tanım: merkez noktası C (World'de, ya da taban orijinine göre ofset, örn. taban ekseninin 10 cm üstü) ve yarıçap r. Jog girdileri: **sağ-sol = azimut** (C'den geçen düşey eksen etrafında), **yukarı-aşağı = yükselme açısı** (TCP, C'ye sabit r uzaklıkta kalır), **içeri-dışarı = r** (ayrı tuş). Seçilen takım ekseni (varsayılan tool z) **her zaman C'ye bakar** (look-at). Hesap yeni çözücü değil: türdeki girdi, anlık Kartezyen twist'e çevrilir ve mevcut L1 DLS motoruna verilir (güvenli kutu, tekillik, TTL aynen geçerli). Bilinmesi gerekenler: C taban ekseni üzerindeyse azimut hareketi saf 1. eksen dönüşü olur (beklenen davranış, testle doğrulanır); zirvede (yükselme ±90°) azimut tanımsız, bu yüzden yükselme ≤85° ile sınırlanır; kolun erişemediği (kutu/limit/tekillik) bölgeye girilince jog durur ve nedeni söyler, çerçeve oluştururken (C, r) erişilebilir yükselme/azimut aralığı hesaplanıp kullanıcıya gösterilir. Çerçeve türleri envanteri (bu iş sonunda `docs/JOG.md`'de listelenir): Joint, World, Tool, User (3 nokta), Sphere (yeni). Silindirik vb. türler ihtiyaç doğarsa ayrı madde.
- [ ] L8 Küresel çerçeve: Robot API (`/api/frames` türü `sphere`) + Hub çerçeve sihirbazı (C'yi sayıyla ya da "şu anki TCP'yi merkez al/10 cm yukarı" ile gir, r gir, erişim aralığını göster) (Robot_3, Robot_4; L3 ve L7'den sonra) **Karar (Robot_1, 6 Kasım):** sphere `type` alanıyla EKLEMELİ eklenir (varsayılan `user`, mevcut kayıtlar aynen geçerli); `frames_version: 1` KALIR (dosya şu an 1'e kesin eşitlik istiyor, artırmak eski dosyaları kırar, kazancı yok); bilinmeyen `type` gören okuyucu sessizce ATLAMAZ, net hata verir (atlarsa sonraki yazmada çerçeve silinir). `GET /api/frames` yanıtına desteklenen türler (`types: [user, sphere]`) eklenir; Hub bunu bilmiyorsa sphere oluşturmayı gizler ve yazarken diğer türleri korur (eski Hub .exe `replace_all` ile sphere'leri silmesin). Yeni istemciler için çerçeve başına ekleme/silme uçları (tam değiştirmeyi bırakmak) L8 kapsamında düşünülür (Robot_1 koda bakmadı). **Durum (6 Kasım):** API kısmı bitti (#64: `type: sphere` eklemeli, `frames_version` 1 kaldı, bilinmeyen tür net hata, `types` listesi, eski Hub PUT'u sphere'leri silemez/bozamaz, erişim aralığı, küre jog); Hub sihirbazı (Robot_4) yok, bu yüzden `[x]` değil. **Denenmeyenler:** Gazebo/gerçek robot, çerçeve başına ekle/sil uçları (yalnız toplu PUT), ~85° yükselme ve çok küçük r, erişim hesabının CPU yükü.
- [ ] L9 **Hub'da robotun canlı görünümü** (Ferhat isteği). **Karar (Robot_1, 5 Kasım): (b) hafif sürüm.** Hub kendi salt-okunur robot modeli görünümünü çizer. `robot_viz` sayfasını iframe/ters vekil ile gömmek YOK: Hub CSP'si (`default-src 'self'`) zaten engeller, robot_viz JS'i kimliksiz Robot API'ye POST atar (ters vekil PIN/CSRF/onay kuyruğunu delmek olur), telefon `127.0.0.1:8080`'e erişemez, gösterdiği şey zaten 3B model değil kamera PNG'si. Doğrulandı: Robot API durumundaki `joints` (ad, rad, deg) Hub snapshot'ına geçiyor, Hub WS 0.2 s; URDF yalnız `box` ve `cylinder` kullanıyor, mesh yok. Görmek komut vermek değildir: hiçbir komut yolu eklenmez, ACİL DUR Hub'ın kendi düğmesinde kalır. Üç küçük PR:
  - [x] L9a (#58) **Profil üreticisi `view_model.json`** (Robot_2): eklem origin/eksen/limit, link boyutları (box/cylinder); tek doğruluk kaynağı profil; üretilen dosya elle düzenlenmez, CI'da üretim=commit. Şemayı PR gövdesinde yaz (L9b ve L9c ona dayanır). **Doğrulama durumu:** şema yalnız testle doğrulandı (`src/robot_description/profile/generated/view_model.json`, v1); L9b/L9c henüz okumadı; `joints` adlarının model eklem adlarıyla eşleşmesi denetlenmedi; aktif takımlı model yalnız yapay profille denendi.
  - [x] L9b (#61) **`GET /api/model`** (Robot_3, Robot API v1'e yalnız eklemeli): `view_model.json`'u verir. Kabul: salt-okunur, kimlik bilgisi/yol sızdırmaz, `INTERFACES.md`'ye eklemeli bölüm. **Doğrulama durumu:** CI'da `model` adımı (mock yığın) yeşil; Hub istemcisi (L9c) henüz okumadı; Gazebo modunda ve gerçek robotta koşulmadı; dosya çalışırken değişirse yeniden okunmaz (yeniden başlatma); telefon üzerinden erişim ve yük denenmedi.
  - [ ] L9c **Hub Canvas 2D görünümü** (Robot_4, jog panelinin yanında): bağımlılıksız, ortografik, sürükleyerek döndür, linkler çubuk/kapsül; üzerinde güvenli kutu, TCP, aktif çerçeve eksenleri (World/User/Sphere). **Şart: bayat veri uyarısı**, durum 1 s'den eskiyse görünüm griye döner ve ACİL DUR bandı çıkar (donmuş model yanıltır). Gerçek modda servo/açık çevrim eksenler "kestirim" etiketiyle (çizim ölçüm değildir). İstemci tarafı yumuşatma (5 Hz akıcı değil), görünüm güvenlik işlevi değil. L9b gelene kadar `fake_backend`'in sahte modeliyle başlanabilir. Kamera karesi bu maddede YOK (H4).
- [ ] L5 Jog'u Gazebo'da elle dene (Ferhat); Windows↔WSL jitter ölç, gerekirse TTL 300 ms

## O. Nokta öğretme ve algoritma yönetimi (Hub "Algoritmalar" sayfası)
> Hedef: robotu elle (jog) bir noktaya götür → "kaydet" → başka noktaya götür → "kaydet" → "A'dan B'ye git". Çekirdekte `go_to_named_pose`/`go_to_position`/senaryo yorumlayıcısı var, ama kullanıcı noktayı arayüzden öğretemiyor ve çalıştıramıyor.
- [x] O1 (#51) Nokta deposu çekirdeği (Robot_3; `robot_core`'da yeni dosya `waypoints.py`, mevcut dosyalara dokunmaz; ROS'suz): `waypoints.yaml` (ad, eklem değerleri, TCP pozu ve hangi çerçevede, oluşturma zamanı). `frames.yaml` ile aynı kurallar: atomik yazma, şema, sürüm, yükte doğrulama. **Kaydederken** nokta eklem limitleri ve güvenli kutu içinde değilse reddedilir. **Doğrulama durumu:** ROS'suz birim testi; Windows/WSL bind-mount üzerinde `os.replace` atomikliği ve süreçler arası eşzamanlı yazma denenmedi; kayıt anındaki FK gerçek TCP ile karşılaştırılmadı.
- [x] O2 (#55) Robot API: `GET/PUT/DELETE /api/waypoints`, `POST /api/waypoints/record` (şu anki eklem/TCP'yi kaydeder), `POST /api/jobs` komutu `goto_waypoint` (Robot_3). **Kural:** noktaya gidiş MoveIt planı ve çarpışma denetimiyle yapılır; adlandırılmış eklem pozlarındaki gibi denetimsiz eklem hedefi değil. Güvenli kutu, ACİL DUR, 423 aynen geçerli. **Doğrulama durumu:** CI'da Gazebo selftest (`waypoint` modu) yeşil, toleranslar kum havuzunda ayarlı (eklem 0,03 rad, TCP 1,5 cm). **Güvenli kutu yalnız hedef noktada denetleniyor**, eklem uzayı yolunda iki kutu-içi nokta arasında kutudan çıkılabilir (mevcut `go_to_position` ile aynı; Robot_1'e soruldu). Gerçek donanım, B2 köprüsü üzerinden `goto_waypoint`, homing sorunu (`record` homed bilmez) ve Windows↔WSL elle denenmedi.
- [ ] O3 Hub "Algoritmalar" sayfası (Robot_4; L3'ten sonra): noktaları listele/yeniden adlandır/sil, "şu anki konumu nokta olarak kaydet" (jog panelinin yanında), iki nokta seç → "git". Gösterilen her hareket ACİL DUR ile birlikte.
- [ ] O4 Sıra (algoritma) editörü: kayıtlı noktalardan adım listesi (git, bekle, döngü, kamera/eklenti çalıştır), mevcut senaryo YAML biçimine derlenir; "öğret ve oynat" (kaydet → oynat); yürütme sırasında durdur/acil dur (Robot_4 + Robot_2)

## Q. Takımlar (tool)
> Farklı tool'lar tasarlanırken içlerine kamera da (ve lazer) eklenebilmeli. Takım = kolun ucuna takılan, geometrisi, TCP'si ve isteğe bağlı sensörleri olan parça.
- [x] Q1 (#53) **Takım tanımı** (Robot_2; L4/#42'den sonra): `robot.profile.yaml`'da `tools:` listesi. Her takım: ad, flanşa göre TCP ofseti, kütle/atalet, çarpışma geometrisi, isteğe bağlı **sensörler** (kamera: flanşa göre poz, çözünürlük, kamera_info/FOV; lazer: çıkış noktası ve ekseni). URDF/SDF buradan üretilir (elle düzenlenmez). Takım değişince TCP, jog, güvenli kutu ve çarpışma geometrisi birlikte güncellenir; tork denetimi (K7) takım kütlesini içerir. **Doğrulama durumu:** profil, `tools`, TCP/kutu/çarpışma birlikte güncelleme birim testli; aktif takımlı URDF/SDF Gazebo/MoveIt/RViz'de yüklenmedi (yalnız XML ayrıştırma); sensörler URDF/SDF'e yazılmıyor (P1); takım kütlesi tork denetimine girmiyor (K7); atalet/COM kaba tahmin; takımlı kapalı döngü jog ve çarpışma denetimi denenmedi.
- [ ] Q2 İlk takım kütüphanesi (simülasyon): sivri uçlu pointer, kamera, kamera+lazer, tutucu (D2 için yer tutucu). Her takım için gidiş-dönüş ve FK testi.
- [ ] Q3 Hub'da takım seç/ekle/düzenle (Robot_4; J4 ile aynı sayfa), Robot API'de aktif takım seçimi (Robot_3). Takım değişimi yalnız robot durgunken, ACİL DUR ve 423 kuralları aynen.

## P. Tool kamerası ve nişan alma (L7/L8'e bağlı)
> Hedef: kol ucundaki kamera (ve lazer göstergesi) küresel çerçeve üzerinde, görüntü işlemeyle bulunan hedefi (örn. yüz/alın) görüntü merkezinde tutar.
- [ ] P1 Simülasyonda kol ucuna kamera (+ lazer ışını göstergesi): Q1'in "kamera+lazer" takımı olarak, Gazebo'da kamera konularıyla birlikte (Robot_2 profil, Robot_5 algı)
- [ ] P2 (H6 sonrası) El-göz kalibrasyonu: kamera↔tool dönüşümü, lazer↔kamera ofseti; kalibrasyon dosyası ve doğrulama ölçümü (Robot_5)
- [ ] P3 "Nişan al" eklentisi: hedef görüntü merkezinden saptıkça küresel çerçevede azimut/yükselme düzeltmesi; güvenli kutu ve TTL aynen geçerli, kaybolan hedefte robot durur (Robot_5 + Robot_3)
- [ ] P4 **Lazer güvenliği (donanımdan önce zorunlu):** yalnız Sınıf 1/2 düşük güçlü lazer; açma/kapama yazılımdan bağımsız donanım kilidiyle (NC acil dur hattıyla birlikte kesilir); varsayılan kapalı, her oturumda elle açılır; yüz/göz bölgesine ışın **tutulmaz** (ilk denemeler kağıt/duvar hedefine); lazer açıkken hedef kaybı ya da ACİL DUR'da anında kapanır. Bu madde bitmeden gerçek lazer takılmaz.

## M. RL araştırma
- [x] M1 URDF→MJCF, Gymnasium ortamları, klasik IK/RRT tabanı (#16)
- [x] M2 PPO/SAC/TD3 karşılaştırması + REPORT.md (#25 içinde). Sonuç: bu 3 görevde klasik IK ≫ RL.
- [ ] M3 ⏸ Karar Ferhat'ta: RL'e devam ya da bekle

## N. Doğrulama borcu (denenmemişler)
- [ ] N1 Gazebo GUI, WSLg'de RViz kamera görüntüsü
- [ ] N2 Gerçek OpenAI/Anthropic yanıtı; Windows'ta gerçek Credential Manager
- [ ] N3 Windows `.exe` elle denemesi (ACİL DUR, ajan kartı, telefon, güvenlik duvarı düğmesi)
- [ ] N4 Gerçek donanım (ESP32, sürücü, step motor, encoder)
- [ ] N5 Nightly workflow GitHub'da ilk koşu doğrulaması
- [ ] N6 **`hw` selftest 8 aralıklı kırılıyor** (Robot_3, `robot_hw_bridge`/ESP32 simülatörü): "hat koptu: MCU kendi watchdog'uyla durdu (<= 0,8 s)" `main`'de bir kez 3,01 s ölçtü (`d376abb`, 31/32; PR-head'lerde ve diğer main koşularında geçti). 3 s, ölçüm zaman aşımına benziyor; CI yükünden gelen bir zamanlama sorunu da olabilir, gerçek bir watchdog gecikmesi de olabilir, hangisi olduğu BİLİNMİYOR. Bu bir dead-man güvenlik testi, "flaky" diye yok sayılmaz: tekrar eder mi, simülatör mü test mi ölçüyor, ≤0,8 s sınırı ne kadar pay bırakıyor. Kabul: nedeni yazılır; test aynı koşulda 20 ardışık geçer ya da gerçek hata düzeltilir; sınır gevşetilerek "düzeltilmez".

## Değişiklik günlüğü
- 2026-11-06 (akşamüstü): #62, #63, #64 birleşti. F4a-2 `[x]` (Robot_1 onayı + üç ek şart işlendi); L8 yalnız API kısmı (#64). Birleşme sonrası `main`'de "Control Hub (lint, tests, Docker smoke test)" kontrolü kırmızıydı (adım: Docker imaj smoke testi, #63/#64 Hub'a dokunmuyor, PR-head ve önceki main'de yeşildi; nedeni doğrulanmadı).
- 2026-11-06: CI notu: `ci.yml` `concurrency: cancel-in-progress` açık; ardışık merge'ler (#63 → #64) ya da koşu SÜRERKEN "Re-run" önceki koşuyu iptal eder ("Canceling since a higher priority waiting request"). Bu hata değil; son commit'in CI'ı tamamlanana kadar beklenir. `d376abb`'de `hw` selftest 8 bir kez 3,01 s ölçtü (31/32), N6'ya yazıldı.
- 2026-11-06 (öğleden sonra): #60, #61 birleşti; L9b `[x]`. Robot_1 kararı (iki kez soruldu, cevaplar çelişmiyor): F4 için kutu genişletilmez, yalnız `home` için sıkı park muafiyeti (F4a-2 yeni, F4b buna bağlı). Müdür bulgusu: `safe_zone.yaml` üretilmiş dosya, park bölümü ayrı dosyada olmalı (Robot_1 onayı bekleniyor).
- 2026-11-06: #57 (H4 API, CI kapanış çökmesi düzeltildikten sonra), #58 (L9a), #59 (F4a) birleşti. L9a, F4a `[x]`; H4 kısmen. F4a bulgusu: `home` ve `cnc/pc/dik` kutu dışında, `ready→home` F4 kuralıyla reddedilir; Robot_1 kararı bekleniyor, F4b durduruldu.
- 2026-11-06: Robot_1 kararları işlendi: F4 (yol boyunca kutu denetimi, F4a/F4b, `mode: real` kapısına eklendi) ve L8 için sphere `type` kararı (`frames_version` 1 kalır). INTERFACES.md'ye O2 için "bilinen açık" notu eklendi.
- 2026-11-06: #54, #55 birleşti. O2 `[x]` (kutu yalnız hedefte denetleniyor, açık güvenlik sorusu Robot_1'de). Robot_2 Q1'i, Robot_3 O2'yi L9'dan önce yaptı; L9a/b/c hâlâ yok.
- 2026-11-05 gece: #47–#53 birleşti. O1, H6, Q1 `[x]` (doğrulama sınırları satırlarda); L7 kısmen (çekirdek var, hiç simülasyonda denenmedi). #49 (Uno hedefi, K3/K6/K7) başka bir sohbetten geldi, Ferhat birleştirdi; K7'nin servo besleme kesme maddesi güvenlik içerir, K7 başlamadan Robot_1 gözden geçirmeli. L9a/b/c henüz başlamadı.
- 2026-11-05: L9 Robot_1 kararıyla (b) hafif sürüm olarak L9a/L9b/L9c'ye bölündü (robot_viz gömme reddedildi); H4'e kamera karesi notu eklendi.
- 2026-11-05: #46 birleşti, L3 `[x]` (Ferhat arayüzü sahte robotla denedi ve kabul etti; gerçek robot doğrulaması L5/L9'a kaldı). L9 (Hub'da robot görünümü) eklendi, mimari kararı Robot_1'de.
- 2026-11-05: #42 (L4) ve #44 (L3) birleşti. L4 `[x]`. L3 kısmen: 3 nokta öğretme sihirbazı yok, `[x]` yapılmadı. Main CI bu güncelleme sırasında tamamlanmamıştı (Lint + unit tests koşuyordu).
- 2026-11-05: J5 (eksik bilgiyle çalışan, sonradan doldurulan profil: kalite etiketi + olgunluk seviyeleri + real kapısı) eklendi; K8 artık Ferhat'ın ölçülerini beklemez.
- 2026-11-05: H4 genişletildi (izleme kamerası), H6 (hareketli kamerada algı), Q1–Q3 (takımlar), K6–K8 (SG90 servo + iki robot: karma ve tam step) eklendi; "Sıradaki işler" yeniden sıralandı; O1 sahibi Robot_3.
- 2026-11-05: L7/L8 (küresel çerçeve), O1–O4 (nokta öğretme + algoritma sayfası), P1–P4 (tool kamerası + nişan, lazer güvenliği) eklendi (Ferhat isteği); H3 satırına "kol kamerası değil" notu.
- 2026-11-05: İlk sürüm (Müdür). Kaynak: birleşen PR'lar #1–#41, açık #42.
- 2026-11-05: Kural 6-7 ve "Müdür iş akışı" eklendi (PR başlığı ↔ ID eşlemesi: #42=L4, #44=L3); L3 🟡 #44 olarak işaretlendi.
- 2026-11-05: L3 genişletildi (düzlem seçimi + 3 nokta sihirbazı), L6 eklendi (düzleme paralel jog), terimler notu (Ferhat'ın düzlem tarifi).
