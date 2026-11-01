# ros2-robot-arm — 6 eksenli robot kolu (ROS 2 + MoveIt 2)

Simülasyonda çalışan, çarpışma kontrollü 6 eksenli robot kolu. Yol haritası: algı (kamera) →
LLM katmanı → pekiştirmeli öğrenme → fiziksel robot. Şu an **Aşama 0 (temel)**: temiz yapı, Docker,
otomatik test.

## Yapı
```
src/
  robot_core/            ROS'tan bağımsız çekirdek (saf Python, pytest ile test edilir)
  robot_controller/      ROS 2 adaptörleri + CLI'lar (cli_moveit, move_robot, selftest)
  robot_description/     URDF (görsel + çarpışma geometrisi)
  robot_moveit_config/   MoveIt yapılandırması (sahte donanım ya da Gazebo) ve launch dosyaları
  robot_data/            ROS'suz veri katmanı: SQLAlchemy 2 + Alembic (SQLite testler, PostgreSQL üretim)
  robot_perception/      renkli nesne algısı düğümü + perception_check (mantık robot_core.perception'da)
  robot_viz/             salt okunur tarayıcı görünümü: web_view düğümü + viz_check (mantık robot_core.viz / webview'de)
  robot_sim/             Gazebo dünyaları (nesneli çalışma alanı + RGB-D kamera)
docker/                  Dockerfile (base / ci / dev) ve selftest betiği
compose.yaml             dev ve selftest servisleri
data/routes.json         move_robot için örnek rotalar
.github/workflows/       CI
```
Katmanlar: `robot_core` ROS'u hiç import etmez; `robot_controller/adapters.py` onun tanımladığı
portları (`MotionPort`, `ScenePort`, `StatePort`) ROS 2 ile gerçekler. Eklem adları/limitleri ve
hazır pozlar URDF/SRDF'ten okunur, kodda sabit yazılmaz.

## Çalıştırma (Docker, önerilen)
```bash
docker compose run --rm selftest          # başsız uçtan uca test, sahte donanım (CI ile aynı)
docker compose run --rm selftest-gazebo   # aynı test Gazebo fiziğinde (CI ile aynı)
docker compose run --rm selftest-camera   # Gazebo + derinlik kamerası: hareket testi + sensor_check
docker compose run --rm selftest-perception  # + renkli nesne algısı: tespit edilen konum ↔ dünya dosyası
docker compose run --rm selftest-scene       # + tespitler MoveIt sahnesinde: engel belirir, plan reddedilir, nesne kalkınca plan geçer
docker compose run --rm selftest-skills      # Skills: aleti nesnenin üstüne götür, uç konumu TF ile ölç
docker compose run --rm selftest-viz         # tarayıcı görünümü: sayfa, JSON, PNG'ler, bindirme ↔ dünya dosyası, eklem/TF, salt okunur
docker compose run --rm selftest-db          # selftest olayları PostgreSQL'e yazılır (compose'taki db servisi)
docker compose up demo                       # etkileşimli demo + tarayıcıda canlı görünüm (aşağıya bak)
docker compose run --rm dev           # RViz destekli geliştirme kabuğu (WSLg)
# dev içinde:
colcon build --symlink-install && source install/setup.bash
ros2 launch robot_moveit_config demo.launch.py
ros2 run robot_controller cli_moveit
```
ROS sürümünü değiştirmek için: `docker compose build --build-arg ROS_DISTRO=humble`.

## Simülasyon modları
| Mod | Başlatma | Ne zaman |
|---|---|---|
| mock (varsayılan) | `ros2 launch robot_moveit_config demo.launch.py` | hızlı geliştirme, fizik yok |
| Gazebo fiziği | `ros2 launch robot_moveit_config gazebo.launch.py` | fizik gerektiren her şey (başsız; GUI için `headless:=false`) |
| Gazebo + kamera | `ros2 launch robot_moveit_config gazebo.launch.py world:=workspace` | algı geliştirme (nesneler + sabit RGB-D kamera) |

İkisi de aynı MoveIt yapılandırmasını, aynı denetleyicileri ve aynı `selftest`'i kullanır. Gazebo modu
URDF'i `use_gazebo:=true` ile üretir (`mock_components` yerine `gz_ros2_control`).

## Kamera (world:=workspace)
Topic'ler: `/camera/image` (rgb8), `/camera/depth_image` (32FC1, **z-derinliği**, metre),
`/camera/camera_info`; `camera_points:=true` ile `/camera/points` (büyük, varsayılan kapalı).
Çerçeveler: `world` (robot tabanı) → `camera_link` (x ileri, y sol, z yukarı; görüntü mesajlarının
`frame_id`'si) → `camera_optical_frame` (z ileri; piksel izdüşümü için bunu kullan).
Kamera pozu, odak uzaklığı ve nesneler **tek yerden** gelir: `src/robot_sim/worlds/workspace.sdf`.
Statik TF ve `sensor_check` beklentileri bu dosyadan türetilir (`robot_core.workspace`).

`ros2 run robot_controller sensor_check`: görüntü gelmesini yetmez sayar; dünya modelinden analitik
ışın izlemeyle hesaplanan derinliği (zemin, kutu, silindir, küre) kameranın ölçtüğüyle ve TF'i kamera
pozuyla karşılaştırır. GPU yoksa görüntüleme yazılımla (Mesa) yapılır, bu yüzden CI'da yavaştır.

## Algı (Aşama 2a)
`perception:=true` ile başlatılınca `robot_perception` kameradan kırmızı/yeşil/mavi nesneleri bulur
(HSV eşikleme → bağlı bileşenler → derinlikten 3B geri-izdüşüm → `camera_optical_frame` → `world`) ve
`/perception/objects` yayınlar (ayrıntı: `docs/INTERFACES.md`). Mantık ROS'suz ve yalnızca numpy:
`robot_core/perception.py`. `perception_check`, tespitleri `workspace.sdf`'teki gerçek konumlarla
karşılaştırır (konum ≤ 2 cm, yükseklik ≤ 1.5 cm, kareler arası kararlılık).

## Algı → MoveIt (Aşama 2b)
`perception_scene:=true` (ve `perception:=true`) ile `perception_scene_sync` tespitleri MoveIt planlama
sahnesine `obj_<renk>_<n>` çarpışma kutuları olarak yansıtır: kararlı kimlik, titreşim süzgeci, 3 kare
onay, 2 s görülmeyince silme (`robot_core/scene_sync.py`, ayrıntı `docs/INTERFACES.md`).
`perception_scene_check` bunu uçtan uca doğrular (nesne belirir; içinden geçen hedef reddedilir;
Gazebo'da taşınan nesnenin kutusu taşınır; kaldırılınca aynı hedef için plan başarılı olur).

## Poz hedefli hareket ve beceriler (Aşama 4)
`MoveItMotion.move_to_pose` kartezyen hedefi (konum + yönelim kısıtı, KDL IK) planlayıp yürütür.
`robot_core.skills.Skills` bunun üstünde LLM'in sonradan çağıracağı araç arayüzüdür: `list_objects`,
`go_to_named_pose`, `go_to_position`, `go_above(nesne, yükseklik)`; hiçbiri exception fırlatmaz,
hepsi `SkillResult` döner (ayrıntı, kısıtlar ve erişim sınırı: `docs/INTERFACES.md`).
`cli_moveit` komutları: `objects`, `above <nesne> [yükseklik_m]`, `goto x y z`.
`pose_check`, uç işlevcinin TF ile ölçülen konumunu hedefle ve dünya dosyasıyla karşılaştırır.

## Tarayıcıda canlı görünüm (Aşama 5a)
Sistemi gözle görmek için: kamera görüntüsü (algılanan nesneler kutu + etiketle işaretli), derinlik
haritası, nesne tablosu, eklem açıları ve uç işlevci konumu, ~5 Hz canlı. Yalnızca standart kütüphane
(`http.server`), ek kurulum yok. Varsayılan **salt okunur**; `demo` servisinde komut düğmeleri açıktır
(aşağıdaki "Tarayıcıdan komut" bölümü).

```bash
# Terminal 1 — WSL'de (simülasyon + görünüm; ilk açılış 1-3 dk, "Hazır." yazısını bekle)
cd ~/ros2-robot-arm
docker compose up --build demo
```
Windows tarayıcında **http://localhost:8080** aç (WSL2 + Docker Desktop'ta `localhost` aynıdır).
```bash
# Terminal 2 — WSL'de (komut satırı; aynı çalışan konteynere bağlanır)
cd ~/ros2-robot-arm
./docker/cli.sh
# moveit_cli> objects
# moveit_cli> above green 0.25      -> tarayıcıda kolun hareket ettiğini izle
# moveit_cli> move home
```
Durdurmak için Terminal 1'de **Ctrl-C** (süreçler sırayla kapanır, port serbest kalır). 8080 doluysa:
`VIEW_PORT=8081 docker compose up demo` (WSL'de) ve `http://localhost:8081`.

Tarayıcıdan komut ve demo senaryosu için aşağıdaki bölüme bak. Güvenlik: konteyner içinde `0.0.0.0`'da dinler ama `compose.yaml` portu yalnızca ana makinenin
`127.0.0.1`'ine yayımlar (ağdaki başka makineler erişemez); `Host` başlığı localhost değilse `403`,
`GET`/`HEAD` dışındaki her yöntem `405`. Docker'sız (ör. `dev` kabuğunda):
`ros2 launch robot_viz web_view.launch.py` (varsayılan `127.0.0.1:8080`). Uç noktalar ve JSON
şeması: `docs/INTERFACES.md`.

## Tarayıcıdan komut + demo senaryosu (Aşama 5c)
`docker compose up demo` ile açılan sayfada **Komutlar** paneli görünür (yalnızca `web_view`
`commands:=true` ile başlatılmışsa; varsayılan kapalı). Düğmeler `POST /api/command`'ı çağırır:

| Komut | Ne yapar |
|---|---|
| `home` | robot `home` pozuna |
| `objects` | bilinen nesneleri (ad, renk, konum, boyut) listeler |
| `above <red\|green\|blue> [yükseklik_m]` | aleti o renkteki nesnenin üstüne götürür (varsayılan 0.10 m, en çok 0.5 m); `kırmızı/yeşil/mavi` de olur |
| `demo_scenario` | `home` → yeşil silindirin 25 cm üstü → kırmızı kutunun 35 cm üstü → `home`; her adım Türkçe anlatılır, her hareketten sonra uç konum **TF ile ölçülüp** hedefle karşılaştırılır (≤ 1.5 cm; `home` tekrarı ≤ 3 cm), ilk başarısız adımda durur |

Anlatım sayfada canlı akar; aynı komutu komut satırında `demo_scenario` yazarak da çalıştırabilirsin.
Aynı anda tek komut çalışır (ikincisi `409`). Erişilemeyen hedef (ör. tabana yakın nesnede çok alçak
`above red 0`) robotu hiç oynatmadan `422` ve nedeniyle reddedilir. Beyaz liste dışındaki her şey
(`goto`, `move`, kabuk karakterleri, bilinmeyen renk, 0-0.5 m dışı yükseklik...) `400`, yanlış içerik
türü `415`, başka bir sitenin isteği `403`. Ayrıntı ve durum kodları: `docs/INTERFACES.md`.

```bash
# WSL'de (komutlar açık simülasyon çalışırken, aynı makineden)
curl -s localhost:8080/api/command -H 'Content-Type: application/json' -d '{"command": "above green 0.25"}'
curl -s localhost:8080/api/command -H 'Content-Type: application/json' -d '{"command": "demo_scenario"}'
```

**ACİL DUR.** Sayfanın üstündeki kırmızı **ACİL DUR** düğmesi (ya da `POST /api/stop`) çalışan hareketi
iptal eder, komutları kilitler (`423`) ve kolun gerçekten durduğunu eklem hızlarından doğrular;
**Sıfırla** (`POST /api/reset`) kol duruyorken kilidi açar. Hub/otomasyon için sürümlü sözleşme
(`api_version: 1`, `robot.state`, `moving`, ...): `docs/INTERFACES.md` → "Robot API v1".

```bash
# WSL'de (komutlar açık simülasyon çalışırken)
curl -s -X POST localhost:8080/api/stop                       # acil dur
curl -s localhost:8080/api/state | python3 -c 'import json,sys; print(json.load(sys.stdin)["robot"])'
curl -s localhost:8080/api/reset -H 'Content-Type: application/json' -d '{}'   # kilidi aç
```
Otomatik doğrulama (kolun hareket ortasında durması ölçülür): `docker compose run --rm selftest-stop`.

**Asenkron işler** (Hub/otomasyon için; `/api/command` kullanımdan kalktı): `POST /api/jobs` hemen `202`
döner, `GET /api/jobs/<id>` ilerlemeyi ve sonucu verir, `POST /api/jobs/<id>/cancel` kolu durdurur
(kilitlemeden). **Güvenli çalışma kutusu** (`robot_moveit_config/config/safe_zone.yaml`): dışındaki kartezyen
hedefler açık hatayla reddedilir, robot oynamaz.

```bash
# WSL'de (komutlar açık simülasyon çalışırken)
curl -s localhost:8080/api/jobs -H 'Content-Type: application/json' -d '{"command": "demo_scenario"}'
curl -s localhost:8080/api/jobs/j1 | python3 -m json.tool
curl -s localhost:8080/api/jobs/j1/cancel -H 'Content-Type: application/json' -d '{}'
```
Otomatik doğrulama: `docker compose run --rm selftest-jobs`.
Docker'sız: `ros2 launch robot_viz web_view.launch.py commands:=true`. Otomatik test (WSL'de):
`docker compose run --rm selftest-cmd` (reddedilmesi gerekenler, TF ile ölçülen hareketler, canlı
anlatım, meşgulken `409`).

## Görsel çalıştırma: RViz + Gazebo penceresi (Aşama 5b)
Tarayıcı görünümü (yukarıda) en kolay yoldur. RViz ve Gazebo penceresi WSLg ister ve `dev` kabuğunda
çalışır (`compose.yaml`: ana makinenin ağını ve WSLg ekranını kullanır).

```bash
# Terminal 1 — WSL'de
cd ~/ros2-robot-arm
docker compose run --rm dev
```
```bash
# Terminal 1'de açılan dev kabuğunda (konteyner içi)
colcon build --symlink-install && source install/setup.bash
ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true \
    perception_scene:=true use_rviz:=true rviz_config:=robot_demo.rviz
# Gazebo penceresi de istersen sona ekle:  headless:=false
```
```bash
# Terminal 2 — WSL'de (aynı ağı kullanır, Terminal 1'deki düğümleri görür)
cd ~/ros2-robot-arm
docker compose run --rm dev
# açılan kabukta:
ros2 run robot_controller cli_moveit     # moveit_cli> objects / above green 0.25 / move home
```
`rviz_config` varsayılanı `moveit.rviz`'dir (eski davranış); `robot_demo.rviz` ya da mutlak yol
verilebilir. `robot_demo.rviz` (Fixed Frame `world`) şunları gösterir: **Kamera (RGB)** ve **Kamera
(derinlik)** panelleri (`/camera/image`, `/camera/depth_image`), **Algılanan nesneler**
(`/perception/objects`, yarı saydam renkli kutular), **TF** (`world`, `base_link`, `link_6`,
`camera_link`, `camera_optical_frame`), **MotionPlanning** (robot + planlama sahnesindeki `obj_*`
engelleri, `Scene Geometry` açık). Kamera panelleri ilk açılışta küçüktür: kenarlarından sürükleyip
büyüt. `MoveGroup ... /recognize_objects not available` satırı zararsızdır.

**Çalışmazsa şu çıktıları yapıştır:**
```bash
# WSL'de (konteyner dışı)
docker compose version; docker version --format '{{.Server.Version}}'; echo $DISPLAY $WAYLAND_DISPLAY; ls /mnt/wslg
```
```powershell
# PowerShell'de
wsl --version; wsl --status
```
```bash
# dev kabuğunda (konteyner içi), simülasyon çalışırken
echo "DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"; ls /mnt/wslg /tmp/.X11-unix
ros2 topic list | grep -E "camera|perception|monitored_planning_scene"
timeout 8 ros2 topic hz /camera/image            # ~10 Hz olmalı
ros2 topic echo /perception/objects --once | head -30
timeout 5 ros2 run tf2_ros tf2_echo world camera_optical_frame
```
Ayrıca Terminal 1'in ilk 60 ve son 40 satırını, RViz'de **Displays** panelinde kırmızı/sarı
(Error/Warning) görünen ekranın adını ve altındaki mesajı ekle. Pencere hiç açılmıyorsa (dev
kabuğunda) `ros2 run rviz2 rviz2` ve `LIBGL_ALWAYS_SOFTWARE=1 ros2 launch ... headless:=false ...`
komutlarını ayrı ayrı dene ve çıktılarını yapıştır.

## Veri katmanı (Aşama 3)
`ROBOT_DB_URL` ayarlıysa `cli_moveit`, `selftest`, algı düğümü ve sahne senkronizasyonu olaylarını
(`motion_result`, `obstacle_added/removed`, `objects_changed`, `selftest_check`...) `runs` / `events` /
`error_reports` tablolarına yazar. Ayarlı değilse hiçbir şey değişmez. Veritabanı kapalıyken robot
etkilenmez (olay düşer, loglanır). `docker compose up -d db` PostgreSQL'i açar; `dev` kabuğunda
`ROBOT_DB_URL` hazırdır. Özet: `python3 -m robot_data summary`. Şema ve olay sözleşmesi:
`docs/INTERFACES.md`.

## Testler
```bash
pip install pytest ruff pyyaml numpy python-fcl "sqlalchemy>=2.0" alembic "psycopg[binary]>=3.1"
ruff check src && python -m pytest        # ROS gerektirmez; PostgreSQL testleri için ROBOT_TEST_PG_URL ver
```
Model bütünlük testleri, geçmişte yaşanan hataların geri dönmesini engeller (çarpışma geometrisi
eksikliği, çarpışma matrisi eksikliği, YAML'da tekrarlanan anahtar, ölü ikinci SRDF).

## cli_moveit komutları
`status`, `move <home|cnc|pc|dik>`, `move j1..j6` (radyan), `addbox ad x y z sx sy sz`,
`rmbox ad`, `clear`, `list`, `objects`, `above nesne [yükseklik]`, `goto x y z`, `demo_scenario`

## Notlar
- URDF'teki kütle/atalet değerleri tahminidir; gerçek CAD değerleriyle değiştirilmeli.
- `move_robot` doğrudan trajectory gönderir, **çarpışma kontrolü yoktur**.
- Sonraki aşamalar: algı → MoveIt çarpışma nesnesi, veritabanı (metaveri + hata raporları), poz hedefli hareket.
