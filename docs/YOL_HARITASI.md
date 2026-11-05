# Yol Haritası (tek doğruluk kaynağı)

Son güncelleme: 2026-11-05 · main 4af72ea (PR #46; #42, #44, #46 birleşti) · Güncelleyen: Müdür

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
1. L3 bitti (#44, #46). **Hemen başlayacaklar (üçü farklı sahip, dosya kesişimi yok):** L7 (Robot_2), O1 (Robot_3), H6 (Robot_5). Sonra Q1 (Robot_2), H4 API (Robot_3), L9 (Hub'da robot görünümü, mimari kararı Robot_1'den sonra).
2. **Hemen paralel başlayabilir** (farklı dosyalar):
   - **H6** hareketli kamerada algı doğruluğu (Robot_5)
   - **H4 (API kısmı)** izleme kamerasını Robot API'ye bağla (Robot_3). Arayüz kısmı L3 sonrası (Robot_4).
   - **L7** küresel çerçeve çekirdeği (Robot_2)
   - **O1** nokta deposu çekirdeği (Robot_3, yeni dosya)
3. L4 birleşti (#42), **Q1** takım (tool) tanımı başlayabilir. Sonra **O2** API, **O3** Hub "Algoritmalar" sayfası, **L8** küresel çerçeve API+Hub, **H4 arayüz**.
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
- [ ] H4 **İzleme kamerasını hareket ettirilebilir yap**: robotu ve masadaki parçaları tam görmek için ROS/Gazebo'daki kamera platformunu (H3: pan/tilt/slide) Robot API'ye ve Hub'a bağla; Hub'da canlı görüntü + kamera tuşları. API kısmı hemen (Robot_3), arayüz L3'ten sonra (Robot_4). Kamera hareketi robot güvenliğini etkilemez, ACİL DUR kamera platformunu da durdurur.
- [ ] H6 **Hareketli kamerada algı (Robot_5):** `INTERFACES.md` canlı kamera notu "kamera pozu workspace.sdf'ten sabit, hareketli platform için kare başına poz yok" diyor. Platform dönünce nesne konumları dünya çerçevesinde yanlış çıkabilir. Doğrula (ROS algısı TF `world←camera_optical_frame` kullanıyor mu?), kare başına poz kullan, testle kanıtla (kamera pan/tilt'ten sonra sabit nesnenin World konumu ≤ 1 cm kayar). Tool kamerası (P) için de önkoşul.
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
- [ ] K3 Firmware iskeleti (PlatformIO, derleme CI) ⏸ K6'ya bağlı (malzemeler elde); gerçek donanımda denenmediğini yaz
- [ ] J5 **Eksik bilgiyle çalışan, sonradan doldurulan robot profili** (Robot_2 şema/doğrulayıcı, Robot_4 Hub; J4 ile birlikte tasarlanır). Fiziksel değerler (kol uzunluğu, kütle, motor/sürücü modeli, dişli, encoder/limit anahtarı, servo darbe aralığı, tork) sonradan girilebilir. Kurallar:
  1. Her değer bir **kalite etiketi** taşır: `measured` (ölçüldü) · `estimated` (tahmin) · `placeholder` (yer tutucu). Boş bırakılan değer yok, ya etiketli varsayılan ya da `null`.
  2. Profil üç olgunluk seviyesinde doğrulanır: **taslak** (şema geçerli) → **simülasyona hazır** (kinematik ve limitler tutarlı) → **donanıma hazır** (kritik alanların hepsi `measured`: link uzunlukları, motor türü, adım/dişli, eksen limitleri, home/limit anahtarı, servo aralığı, tork denetimi geçti).
  3. **`mode: real` ve ESP32'ye hareket komutu, yalnız "donanıma hazır" profille açılır.** Placeholder/estimated değer gerçek donanımda sessizce kullanılmaz, robot reddeder ve eksik alanları söyler.
  4. Hub'da profil sihirbazı: eksik/tahmini alanları listeler, adım adım doldurulur, kaydedince üretilen dosyalar yeniden üretilir (elle düzenleme yok). Değişiklik günlüğü kalır (kim, ne zaman, eski → yeni).
  5. Testler: eksik alanlı profil simülasyonda açılır ama real'de reddedilir; etiket düşürme (measured → estimated) olgunluğu geri düşürür.
- [ ] K4 İlk bring-up prosedürü (motorlar yükten ayrık, düşük hız, homing, sınır anahtarı, NC acil dur)
- [ ] K6 **Aktüatör türü `hobby_servo` (SG90) + iki robot** (Robot_2 `hardware.py`, Robot_4 `HARDWARE.md`/protokol/simülatör). Bugünkü donanım profili ve protokol yalnız step motor varsayıyor (`motor_type: 'step'`, MOVE hedefi adım, HOME/ENABLE durum makinesi). Servo için: eksen türü alanı `step | hobby_servo`; servo alanları: darbe µs ↔ açı, açı sınırı, yazılım hız (slew) sınırı; **geri besleme yok** → konum = komut, "kestirim" olarak işaretlenir (step encoder'sız kuralı gibi); homing yerine bilinen başlangıç pozu; açılışta ani sıçramayı önlemek için rest açısına yavaş yaklaşma; ENABLE = PWM çıkışı aç/kapa; simülatöre servo modeli. Karışık (step + servo) robotta her eksen kendi türünde. Mevcut step davranışı ve testleri değişmez.
- [ ] K7 **Servo güç ve güvenlik kuralları** (`HARDWARE.md`): servolar için ayrı 5 V besleme (akım payı: SG90 durma akımı datasheet'ten doğrulanır), ortak GND, brown-out önlemi; donanım acil durda servo gücü kesilir (NC hat röle/MOSFET üzerinden) ve **kol yerçekimiyle düşebilir**, bunun için mekanik/duruş notu; **tork denetimi:** profil doğrulayıcı URDF kütle ve kol uzunluğundan statik tork hesaplar, motor/servo tork sınırını aşıyorsa profili reddeder (SG90 tork değeri datasheet'ten doğrulanır, bu belgede varsayım yok).
- [ ] K8 **İki robot profili** (Robot_2): `profile/` altında birden çok robot (şimdi tek dosya `six_axis_robot.yaml`). Önce **karma robot** (2 step + geri kalanı SG90, basit olan, malzemeler elde), sonra **tam step robot**. Profil seçimi, her profilin CI'da üretim=commit kontrolü, simülasyonda profile göre robot yükleme. Ferhat gerçek ölçüleri (eksen sayısı, link uzunlukları, kütle, hangi eksenler step/servo, motor/sürücü modeli, encoder/limit anahtarı) **sonra** verecek; o zamana kadar profil J5 kurallarıyla `placeholder`/`estimated` etiketli değerlerle simülasyonda çalışır, gerçek donanıma kapalıdır.
- [ ] K5 Wi-Fi kanalı (HMAC + seq, OTA kapalı/imzalı), USB-seri sonrası

## L. Jog (elle kontrol, FANUC tarzı frame'ler)

> **Terimler:** "Düzlem" (plane) = 3 noktayla öğretilen User frame'in XY düzlemi (ya da World, yani robot tabanına göre zemin). "Yere/düzleme paralel ileri-geri" = o düzlemde jog. Joint = tek eksen, World = tabana sabit, Tool = takım ucunun kendi eksenleri, User = öğretilen düzlem.

- [x] L1 Jog çekirdeği: SE3/W-P-R, 3 noktadan çerçeve, FK/Jacobian, DLS motoru (#40)
- [x] L2 Robot API: /api/jog (kiralama+TTL), /api/frames, jogging durumu, selftest-jog (#41)
- [x] L3 Hub jog paneli + 3 nokta öğretme sihirbazı (#44, #46): çerçeve seçici (Joint, World, Tool, User), artımlı adım, basılı-tut (real/telefonda kapalı), "Hazır pozuna git", 409/423/403/502 metinleri; sihirbaz: orijin, +X yönü (≥20 mm), XY düzlemi (+Y tarafı), "Şu anki TCP'yi kaydet", düzlem listeleme/seçme/silme (yeniden adlandırma yok). **Doğrulama durumu:** Ferhat arayüz akışını sahte robotla denedi ve kabul etti; sahte robot dönüş hesaplamaz (arayüzde uyarı çıkar). Gerçek Robot API, gerçek FK ve gerçek robotta TCP kaydı (kol durduktan sonra kaydedilmeli, Hub önbelleği ≤~200 ms bayat) denenmedi, bunlar L5'e ve L9'a kalır.
- [x] L4 TCP profilde + `FrameStore.replace_all` + ready pozu (#42)
- [ ] L6 **Düzleme paralel jog ("düzlem modu")**: seçili çerçevenin (World ya da 3 noktayla öğretilen User) XY düzleminde ileri-geri/sağ-sol hareket; Z ve takım yönelimi kilitli (Z ayrı tuş). Önce L1/L2'deki World/User jog'unun bunu zaten karşılayıp karşılamadığı Robot_2/Robot_3 tarafından doğrulanır, eksik varsa eklenir; hesap çok tekilliğe duyarlı olmalı (L1 DLS'i kullan). Arayüz tuşları L3'ün parçası olabilir, sahibi L6 ilk adımında belirlenir.
- [ ] L7 **Küresel çerçeve (merkez noktalı, "kubbe kamera")** (Robot_2, `FrameStore` + `jog`): çerçeve türü `sphere`. Tanım: merkez noktası C (World'de, ya da taban orijinine göre ofset, örn. taban ekseninin 10 cm üstü) ve yarıçap r. Jog girdileri: **sağ-sol = azimut** (C'den geçen düşey eksen etrafında), **yukarı-aşağı = yükselme açısı** (TCP, C'ye sabit r uzaklıkta kalır), **içeri-dışarı = r** (ayrı tuş). Seçilen takım ekseni (varsayılan tool z) **her zaman C'ye bakar** (look-at). Hesap yeni çözücü değil: türdeki girdi, anlık Kartezyen twist'e çevrilir ve mevcut L1 DLS motoruna verilir (güvenli kutu, tekillik, TTL aynen geçerli). Bilinmesi gerekenler: C taban ekseni üzerindeyse azimut hareketi saf 1. eksen dönüşü olur (beklenen davranış, testle doğrulanır); zirvede (yükselme ±90°) azimut tanımsız, bu yüzden yükselme ≤85° ile sınırlanır; kolun erişemediği (kutu/limit/tekillik) bölgeye girilince jog durur ve nedeni söyler, çerçeve oluştururken (C, r) erişilebilir yükselme/azimut aralığı hesaplanıp kullanıcıya gösterilir. Çerçeve türleri envanteri (bu iş sonunda `docs/JOG.md`'de listelenir): Joint, World, Tool, User (3 nokta), Sphere (yeni). Silindirik vb. türler ihtiyaç doğarsa ayrı madde.
- [ ] L8 Küresel çerçeve: Robot API (`/api/frames` türü `sphere`) + Hub çerçeve sihirbazı (C'yi sayıyla ya da "şu anki TCP'yi merkez al/10 cm yukarı" ile gir, r gir, erişim aralığını göster) (Robot_3, Robot_4; L3 ve L7'den sonra)
- [ ] L9 **Hub'da robotun canlı görünümü** (Ferhat isteği; L3 sonrası): jog ve sihirbaz kullanılırken robotun hareketini Hub arayüzünde gözle görmek. Seçenekler: `robot_viz` web görünümünü Hub'a gömmek ya da Hub'a kendi 3B görünümünü eklemek. **Mimari karar yaşlı kurt'a (Robot_1) sorulur, karar verilmeden görev verilmez.** Sahip adayları: Robot_4 (Hub), Robot_3 (görünüm/API). Kabul ölçütü: jog tuşuna basınca Hub'da robotun hareketi görünür; salt-okunur, ACİL DUR'u ve güvenlik kurallarını etkilemez.
- [ ] L5 Jog'u Gazebo'da elle dene (Ferhat); Windows↔WSL jitter ölç, gerekirse TTL 300 ms

## O. Nokta öğretme ve algoritma yönetimi (Hub "Algoritmalar" sayfası)
> Hedef: robotu elle (jog) bir noktaya götür → "kaydet" → başka noktaya götür → "kaydet" → "A'dan B'ye git". Çekirdekte `go_to_named_pose`/`go_to_position`/senaryo yorumlayıcısı var, ama kullanıcı noktayı arayüzden öğretemiyor ve çalıştıramıyor.
- [ ] O1 Nokta deposu çekirdeği (Robot_3; `robot_core`'da yeni dosya `waypoints.py`, mevcut dosyalara dokunmaz; ROS'suz): `waypoints.yaml` (ad, eklem değerleri, TCP pozu ve hangi çerçevede, oluşturma zamanı). `frames.yaml` ile aynı kurallar: atomik yazma, şema, sürüm, yükte doğrulama. **Kaydederken** nokta eklem limitleri ve güvenli kutu içinde değilse reddedilir.
- [ ] O2 Robot API: `GET/PUT/DELETE /api/waypoints`, `POST /api/waypoints/record` (şu anki eklem/TCP'yi kaydeder), `POST /api/jobs` komutu `goto_waypoint` (Robot_3). **Kural:** noktaya gidiş MoveIt planı ve çarpışma denetimiyle yapılır; adlandırılmış eklem pozlarındaki gibi denetimsiz eklem hedefi değil. Güvenli kutu, ACİL DUR, 423 aynen geçerli.
- [ ] O3 Hub "Algoritmalar" sayfası (Robot_4; L3'ten sonra): noktaları listele/yeniden adlandır/sil, "şu anki konumu nokta olarak kaydet" (jog panelinin yanında), iki nokta seç → "git". Gösterilen her hareket ACİL DUR ile birlikte.
- [ ] O4 Sıra (algoritma) editörü: kayıtlı noktalardan adım listesi (git, bekle, döngü, kamera/eklenti çalıştır), mevcut senaryo YAML biçimine derlenir; "öğret ve oynat" (kaydet → oynat); yürütme sırasında durdur/acil dur (Robot_4 + Robot_2)

## Q. Takımlar (tool)
> Farklı tool'lar tasarlanırken içlerine kamera da (ve lazer) eklenebilmeli. Takım = kolun ucuna takılan, geometrisi, TCP'si ve isteğe bağlı sensörleri olan parça.
- [ ] Q1 **Takım tanımı** (Robot_2; L4/#42'den sonra): `robot.profile.yaml`'da `tools:` listesi. Her takım: ad, flanşa göre TCP ofseti, kütle/atalet, çarpışma geometrisi, isteğe bağlı **sensörler** (kamera: flanşa göre poz, çözünürlük, kamera_info/FOV; lazer: çıkış noktası ve ekseni). URDF/SDF buradan üretilir (elle düzenlenmez). Takım değişince TCP, jog, güvenli kutu ve çarpışma geometrisi birlikte güncellenir; tork denetimi (K7) takım kütlesini içerir.
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

## Değişiklik günlüğü
- 2026-11-05: #46 birleşti, L3 `[x]` (Ferhat arayüzü sahte robotla denedi ve kabul etti; gerçek robot doğrulaması L5/L9'a kaldı). L9 (Hub'da robot görünümü) eklendi, mimari kararı Robot_1'de.
- 2026-11-05: #42 (L4) ve #44 (L3) birleşti. L4 `[x]`. L3 kısmen: 3 nokta öğretme sihirbazı yok, `[x]` yapılmadı. Main CI bu güncelleme sırasında tamamlanmamıştı (Lint + unit tests koşuyordu).
- 2026-11-05: J5 (eksik bilgiyle çalışan, sonradan doldurulan profil: kalite etiketi + olgunluk seviyeleri + real kapısı) eklendi; K8 artık Ferhat'ın ölçülerini beklemez.
- 2026-11-05: H4 genişletildi (izleme kamerası), H6 (hareketli kamerada algı), Q1–Q3 (takımlar), K6–K8 (SG90 servo + iki robot: karma ve tam step) eklendi; "Sıradaki işler" yeniden sıralandı; O1 sahibi Robot_3.
- 2026-11-05: L7/L8 (küresel çerçeve), O1–O4 (nokta öğretme + algoritma sayfası), P1–P4 (tool kamerası + nişan, lazer güvenliği) eklendi (Ferhat isteği); H3 satırına "kol kamerası değil" notu.
- 2026-11-05: İlk sürüm (Müdür). Kaynak: birleşen PR'lar #1–#41, açık #42.
- 2026-11-05: Kural 6-7 ve "Müdür iş akışı" eklendi (PR başlığı ↔ ID eşlemesi: #42=L4, #44=L3); L3 🟡 #44 olarak işaretlendi.
- 2026-11-05: L3 genişletildi (düzlem seçimi + 3 nokta sihirbazı), L6 eklendi (düzleme paralel jog), terimler notu (Ferhat'ın düzlem tarifi).
