# Arıza giderme: belirti → neden → çözüm

Mesajlar kodun gerçekte yazdığı metinlerdir; ararken ilk birkaç kelimeyi kopyala. Komutlar Ferhat'ın
makinesi içindir (**WSL'de** çalıştırılır, repo `~/ros2-robot-arm`). İşin yazılımı değil *kolun geometrisi*
olduğu durumlar ayrıca işaretli.

## 1. Başlatma (Docker / WSL)

| Belirti | Neden | Çözüm |
|---|---|---|
| `docker compose up --build demo` hemen `port is already allocated` / `8080 ... in use` | `127.0.0.1:8080` başka bir şeyde (eski demo, başka uygulama) | `# WSL'de` → `docker compose down` ; hâlâ doluysa `ss -ltnp \| grep 8080` ile bul ve kapat |
| Tarayıcıda `localhost:8080` açılmıyor, terminalde "Hazır." yok | simülasyon hâlâ ayağa kalkıyor (ilk açılış 1-3 dk; yavaş makinede daha uzun) ya da launch ölmüş | Terminal 1 çıktısındaki son satırlara bak; `[ERROR]` varsa onu ara. Gerekirse `docker compose down && docker compose up --build demo` |
| Terminal 1'de `[!] launch process died while waiting for: ...` | simülasyon başlarken çöktü (Gazebo/MoveIt) | Önceki satırlardaki `[ERROR]`/`Traceback`; çoğu zaman bellek/CPU yetmiyor: Docker Desktop → Settings → Resources'ta bellek ≥ 6 GB ver |
| `[!] timeout waiting for: move_group node` / `controllers active` | simülasyon beklenenden yavaş ya da kontrolcüler yüklenemedi | Aynı: günlük sonunu oku; makine yoğunsa tekrar dene |
| `Gazebo GUI`/`RViz` penceresi açılmıyor | WSLg çalışmıyor ya da GUI denenmedi (varsayılan başsız) | `wsl --update` (**PowerShell'de**), sonra `README.md` → "Görsel çalıştırma" |
| `docker: command not found` (WSL'de) | Docker Desktop WSL entegrasyonu kapalı | Docker Desktop → Settings → Resources → WSL integration → dağıtımını aç |
| `git pull` sonrası eski davranış | imaj eski kodla derlenmiş | `# WSL'de` → `docker compose up --build demo` (`--build` şart) |

## 2. Hareket komutları (`422` ve `message`)

| `message` içinde | Neden | Çözüm |
|---|---|---|
| `hedef büyük ihtimalle engel ya da robotun kendi gövdesiyle çarpışıyor ya da ulaşılamaz` + `ulaşılabilir yükseklik aralığı dardır` | **Kol geometrisi.** Alet düz aşağı bakarken eklem 3 (±2.0) ve eklem 5 (±1.57) her nesne için dar bir yükseklik penceresi bırakır (ayrıntı: `docs/INTERFACES.md` → "Erişim kısıtı") | Yüksekliği pencerenin içine al: **0.35** üç renk için de güvenli. Pencere *kenarı* (ör. `red 0.30`) kararsızdır |
| `START_STATE_INVALID` (önceki hareketten sonra) | Simülasyonda kol eklem limitini hafifçe aştı (`joint_3 ... outside bounds`), MoveIt sonraki planı reddetti | Pencerenin kenarından kaçın (yukarıdaki satır). Takılı kalırsa `home` komutu eklem limitleri içindeki poza döndürür |
| `hedef güvenli çalışma kutusunun dışında (x=..., izin verilen aralık [...])` | `SAFE_ZONE`: hedef `config/safe_zone.yaml` kutusunun dışında; robot oynamadı | Hedefi kutuya al ya da kutuyu bilerek değiştir (`robot_moveit_config/config/safe_zone.yaml`, sonra yeniden başlat) |
| `hedef (...) tabandan ... m uzakta; erişim alanı dışında (en fazla 1.50 m)` | Hedef MoveIt'e sorulmadan reddedildi (yarıçap) | Hedefi tabana yaklaştır |
| `hareket başarısız (PREEMPTED)` | Komut iptal edildi (iş iptali, zaman aşımı ya da acil dur) | Beklenen: bkz. 4 ve 5 |
| `MoveIt 'move_action' sunucusu bulunamadı (demo.launch.py çalışıyor mu?)` | `move_group` yok/ölmüş | Simülasyonu yeniden başlat; Terminal 1'de `move_group` hatalarına bak |
| `Hedef gönderimi zaman aşımı` / `Sonuç zaman aşımı (planlama/yürütme bitmedi)` | MoveIt yanıt vermedi (yoğun CPU, kilitlenmiş planlayıcı) | Yeniden dene; sürerse simülasyonu yeniden başlat |
| `planlama sahnesi okunamadı (move_group çalışıyor mu?)` / `bilinen nesne yok (algı çalışıyor mu? perception_scene:=true)` | Algı çalışmıyor ya da henüz nesne görmedi | `demo` bunları açık başlatır; kameranın kare verdiğini sayfadaki "canlı" rozetinden doğrula, birkaç sn bekle |

## 3. API durum kodları

| Kod / belirti | Neden | Çözüm |
|---|---|---|
| `409` `başka bir iş çalışıyor: j7 (...)` | Aynı anda tek iş | `GET /api/jobs/j7` ile izle; bitmesini bekle ya da `POST /api/jobs/j7/cancel` |
| `423` `acil dur etkin: hareket reddedildi (önce POST /api/reset)` | Acil dur kilitli | Kol durduktan sonra `POST /api/reset` (sayfada **Sıfırla**) |
| `403` `host izinli degil` | `Host` başlığı `localhost`/`127.0.0.1` değil | `http://localhost:8080` kullan (ağ adresi/alan adı ile açma) |
| `403` `yabancı Origin` / `Sec-Fetch-Site` | İstek başka bir web sayfasından geliyor (CSRF koruması) | Betik/`curl` kullan ya da sayfayı `localhost:8080`'den aç |
| `403` `komut ucu kapalı` | `web_view` `commands:=true` olmadan başladı | `demo` bunu açık başlatır; elle başlatıyorsan `commands:=true` ekle |
| `415` | `Content-Type: application/json` yok | `curl ... -H 'Content-Type: application/json'` |
| `411` / `413` | `Content-Length` yok (chunked) ya da gövde > 512 bayt | Normal JSON gövdesi gönder |
| `400` + `allowed` listesi | Beyaz liste dışı komut ya da geçersiz argüman | `allowed` listesindeki komutlardan birini kullan |
| Sayfada `kamera akmıyor` | Kamera karesi 2 sn'den eski | Simülasyon henüz açılıyor ya da algı düşmüş; Terminal 1'de `gz`/`camera` hataları |
| `'NoneType' object is not subscriptable` / `nan` çıkan kontrol betiği | `/joint_states` henüz gelmeden ölçüm (kontrol betiğinin başlangıç yarışı; betikler artık bekliyor) | Yeniden çalıştır; sürerse `joint_state_broadcaster` yüklenmemiştir (Terminal 1) |

## 4. Acil dur

| Belirti | Neden | Çözüm |
|---|---|---|
| `/api/stop` yanıtında `stopped: false`, mesaj `... durmadı` | Kol 5 sn içinde durmadı (hız verisi sürüyor) | **Arıza say**: simülasyonu/robotu kapat. Kilit yine de geçerli; sebebi `max_joint_speed`'de |
| `stopped: false`, mesaj `... eklem hızı okunamıyor` | `/joint_states` hız bilgisi yok ya da 1 sn'den eski | Durma doğrulanamadı; simülasyonun ayakta olduğunu kontrol et. `reset` bu durumda bilerek reddedilir |
| `/api/reset` → `409 robot hâlâ hareket ediyor` | Kol hâlâ 0.05 rad/s üstünde hareketli | Birkaç sn bekle, tekrar dene |
| `/api/reset` → `409 bir komut hâlâ çalışıyor` | İptal edilen komut henüz sonlanmadı | ≤ 3 sn bekle, tekrar dene |
| Acil dur yalnızca web_view'in hareketlerini durdurur | `cli_moveit` ayrı süreç; kilidi görmez | CLI'dan hareket sürerken web'den stop `trajectory_execution_event`'i yine de yürütmeyi durdurur ama CLI kilitlenmez. **Gerçek donanımda donanım acil durunun yerine geçmez** |

## 5. İşler

| Belirti | Neden | Çözüm |
|---|---|---|
| İş `failed`, `timed_out: true` | `timeout_s` doldu (varsayılan: home 90, objects 30, above 120, demo 300 s) | Daha uzun `timeout_s` ver (≤ 600) ya da hareketin neden uzadığına bak |
| İş `cancelled`, `cancelled_by: estop` | İş sırasında acil dur | `reset`, sonra işi yeniden ver |
| `GET /api/jobs/j9` → `404` | Geçmiş bellekte son 50 iş; süreç yeniden başladıysa silinir | Yeniden iş ver |

## 6. Veritabanı

| Belirti | Neden | Çözüm |
|---|---|---|
| Olaylar/`error_reports` yok | `ROBOT_DB_URL` ayarlı değil (kayıt yok, hata da değil) | `# WSL'de` → `docker compose up -d db` ve `ROBOT_DB_URL=postgresql://robot:robot@localhost:5432/robot` |

## 7. CI ve gecelik koşular

| Belirti | Neden | Çözüm |
|---|---|---|
| CI kırmızı, ham günlüğü açamıyorum | Normal; selftest başarısızlığında **annotation** yazılır | PR → **Checks** ya da koşunun **Summary** sayfası: kırmızı kutular `selftest <mod>: başarısız` (başarısız `[FAIL]` satırları), `kontrol çıktısının sonu` ve sarı `launch günlüğünün sonu` içerir |
| Gecelik koşuda `KARARSIZ (2/3)` | Aynı kod bazen geçip bazen düşüyor (zamanlama yarışı, limit kenarı, ağır CPU) | Sarı `gecelik <mod>: KARARSIZ` ve `[koşu k/3]` etiketli kırmızı annotation'daki `[FAIL]` satırı nedeni verir; kararsızlığı düzelt, testi gevşetme |
| Gecelik koşuda tüm modlar bir gecede aynı anda kırmızı | Pin'lenmeyen kısım kaydı: apt paketleri (ROS/MoveIt/Gazebo) değişmiştir | İlk kırmızı annotation'da paket/sürüm izi; gerekirse taban imajı güncelle (aşağıda) |
| `pip install --require-hashes` hatası: `THESE PACKAGES DO NOT MATCH THE HASHES` | `requirements/*.txt` ile `.in` ayrışmış ya da elle düzenlenmiş | `.txt`'yi aşağıdaki komutla yeniden üret, elle düzenleme |
| Docker build: `manifest unknown` / `not found` (taban imaj) | Pin'li digest kaldırılmış ya da yanlış | Aşağıda "Taban imajı güncelleme" |

### Taban imajı güncelleme (bilerek)
`docker/Dockerfile` taban imajı **digest ile sabit** (`ARG ROS_BASE_IMAGE=ros:jazzy-ros-base@sha256:...`);
CI'ın Postgres imajları da öyle. Yeni taban için:

```bash
# WSL'de
docker buildx imagetools inspect ros:jazzy-ros-base | grep -m1 Digest
```
Çıkan `sha256:...` değerini `docker/Dockerfile`'daki `ROS_BASE_IMAGE` satırına yaz, CI'ın yeşil olduğunu
gör, sonra merge et. (`postgres:16`/`16-alpine` için `.github/workflows/ci.yml` ve `compose.yaml`.)
**Sabitlenmeyen:** imajın içindeki apt paketleri; bunların kaymasını gecelik koşu yakalar.

### Python bağımlılıklarını güncelleme (bilerek)
`requirements/*.in` istediğimiz paketleri, `*.txt` tam sürüm + hash kilidini taşır.

```bash
# WSL'de (Python 3.12 için kilitler; CI ve Docker imajı da 3.12)
pip install uv
cd ~/ros2-robot-arm/requirements
for f in docker ci-core ci-data; do
  python3 -m uv pip compile --python-version 3.12 --python-platform linux --generate-hashes \
    --no-header "$f.in" -o "$f.txt"
done
```
