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
  robot_perception/      renkli nesne algısı düğümü + perception_check (mantık robot_core.perception'da)
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

## Testler
```bash
pip install pytest ruff pyyaml numpy python-fcl
ruff check src && python -m pytest        # ROS gerektirmez
```
Model bütünlük testleri, geçmişte yaşanan hataların geri dönmesini engeller (çarpışma geometrisi
eksikliği, çarpışma matrisi eksikliği, YAML'da tekrarlanan anahtar, ölü ikinci SRDF).

## cli_moveit komutları
`status`, `move <home|cnc|pc|dik>`, `move j1..j6` (radyan), `addbox ad x y z sx sy sz`,
`rmbox ad`, `clear`, `list`

## Notlar
- URDF'teki kütle/atalet değerleri tahminidir; gerçek CAD değerleriyle değiştirilmeli.
- `move_robot` doğrudan trajectory gönderir, **çarpışma kontrolü yoktur**.
- Sonraki aşamalar: algı → MoveIt çarpışma nesnesi, veritabanı (metaveri + hata raporları), poz hedefli hareket.
