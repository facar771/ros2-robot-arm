# Yol Haritası (tek doğruluk kaynağı)

Son güncelleme: 2026-11-05 · main 916204f (PR #41) · Güncelleyen: Müdür (Robot_6)

## Kullanım kuralları (herkes için)
1. **Her görev bu dosyadaki bir ID'ye bağlıdır** (örn. `L3`). PR başlığına/gövdesine "Yol haritası: L3" yaz. ID'si olmayan iş yapılmaz; yeni iş eklemek Ferhat ya da Müdür kararıdır.
2. **Bu dosyayı yalnızca Müdür günceller** (merge sonrası, toplu ve küçük bir docs PR'ı ile). Çalışanlar kutuları işaretlemez; böylece paralel PR'lar bu dosyada çakışmaz.
3. Göreve başlamadan önce: ilgili bölümü, bağımlılıkları ve "Değişmez ilkeler"i oku. Bağımlılığı bitmemiş maddeye başlama, dur ve Ferhat'a söyle.
4. Kapsamın dışına çıkma. Bulduğun ek iş/risk varsa PR gövdesinde "Yol haritasına öneri" başlığıyla yaz, yapma.
5. Her PR'da "Denenmeyenler" bölümü zorunlu. Doğrulayamadığını "çalışıyor" yazma.

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
1. 🟡 **L4** TCP profilde (#42): incele, birleştir.
2. **L3** Hub jog paneli (Robot_4), main CI yeşil olunca.
3. **H4** Kamera elle kontrolünü Robot API + Hub'a bağla (Robot_5 çekirdek, Robot_3 API, Robot_4 arayüz; L3'ten sonra).
4. **J4** Hub'da robot profili görüntüleme, sonra düzenleme (eksen yönü/tipi, limit, link uzunluğu, motor yön/ofset).
5. **K3** Firmware iskeleti ⏸ donanım gelince. **N** bölümü doğrulama borçları.

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
- [x] H3 Kamera platformu elle kontrol (pan/tilt/konum) (#32)
- [ ] H4 Kamera elle kontrolü Robot API + Hub arayüzüne bağla (L3'ten sonra)
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
- [ ] K3 Firmware iskeleti (PlatformIO, derleme CI) ⏸ donanım gelince; gerçek donanımda denenmediğini yaz
- [ ] K4 İlk bring-up prosedürü (motorlar yükten ayrık, düşük hız, homing, sınır anahtarı, NC acil dur)
- [ ] K5 Wi-Fi kanalı (HMAC + seq, OTA kapalı/imzalı), USB-seri sonrası

## L. Jog (elle kontrol, FANUC tarzı frame'ler)
- [x] L1 Jog çekirdeği: SE3/W-P-R, 3 noktadan çerçeve, FK/Jacobian, DLS motoru (#40)
- [x] L2 Robot API: /api/jog (kiralama+TTL), /api/frames, jogging durumu, selftest-jog (#41)
- [ ] L3 Hub jog paneli: çerçeve seçici, artımlı adım, basılı-tut (varsayılan kapalı real/telefonda), 3 nokta öğretme sihirbazı (Robot_4)
- [ ] L4 🟡 TCP profilde + ready pozu (#42)
- [ ] L5 Jog'u Gazebo'da elle dene (Ferhat); Windows↔WSL jitter ölç, gerekirse TTL 300 ms

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
- 2026-11-05: İlk sürüm (Müdür). Kaynak: birleşen PR'lar #1–#41, açık #42.
