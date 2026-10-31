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
  robot_moveit_config/   MoveIt yapılandırması (mock_components ile sahte donanım)
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

İkisi de aynı MoveIt yapılandırmasını, aynı denetleyicileri ve aynı `selftest`'i kullanır. Gazebo modu
URDF'i `use_gazebo:=true` ile üretir (`mock_components` yerine `gz_ros2_control`).

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
- Sonraki aşamalar: derinlik kamerası (Gazebo'da), veritabanı (metaveri + hata raporları).
