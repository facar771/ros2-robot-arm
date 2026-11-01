# Arayüzler

## ROS topic'leri

### `/perception/objects` — `visualization_msgs/MarkerArray` (robot_perception)
Kameradaki renkli nesneler, `world` çerçevesinde. Her mesaj **o an görünen tüm nesnelerin** tam
listesidir (mesajda olmayan nesne yok demektir). Marker başına:

| Alan | Anlam |
|---|---|
| `ns` | renk etiketi: `red`, `green`, `blue` |
| `id` | aynı renkteki blob sırası (0 = görüntüde en büyük) — kalıcı kimlik DEĞİL |
| `pose.position` | nesne merkezi tahmini (m, world) |
| `scale` | sınırlayıcı kutu (x, y, z; m) |
| `lifetime` | 1 s (RViz'de kaybolan nesne silinir) |

Varsayımlar: nesneler zemine oturuyor, dışbükey ve yaklaşık simetrik; kamera üstten bakıyor
(merkez = görünen üst yüzün ortası, merkez yüksekliği = nesne yüksekliğinin yarısı). Yuvarlak
nesnelerin arka yüzü görünmediği için boyut ~1 cm eksik çıkabilir.

Başlatma: `ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true`
(ya da çalışan simülasyona `ros2 launch robot_perception perception.launch.py`).
Doğrulama: `ros2 run robot_perception perception_check`.

## MoveIt planlama sahnesi (robot_controller `perception_scene_sync`)
`/perception/objects` içindeki nesneler `world` çerçevesinde `obj_<renk>_<n>` adlı çarpışma kutuları
olarak MoveIt sahnesine eklenir/güncellenir/silinir (`robot_core.scene_sync.SceneSynchronizer`):

| Konu | Davranış |
|---|---|
| Kimlik | Aynı renkte, `gate` (8 cm) içindeki en yakın izle eşleşir; yoksa yeni iz. `<n>` asla yeniden kullanılmaz |
| Onay | Nesne art arda 3 karede görülmeden sahneye eklenmez (tek karelik yanlış tespit sahneye girmez) |
| Titreşim | Poz/boyut üstel ortalamayla süzülür; sahne yalnızca süzülmüş değer yayımlanandan 1 cm'den fazla sapınca güncellenir |
| Boyut | tespit edilen sınırlayıcı kutu + her yönde 5 mm pay |
| Silme | 2 s görülmeyen nesne silinir; yalnızca `obj_` önekli, kendi eklediği nesneler silinir (`addbox` ile elle eklenenlere dokunmaz) |
| Taşınan nesne | 8 cm içindeki hareket aynı kimlikle izlenir; daha büyük sıçrama yeni iz olur, eskisi 2 s sonra silinir |

Başlatma: `... gazebo.launch.py world:=workspace perception:=true perception_scene:=true`.
Algı düğümü tamamen susarsa nesneler bayat kalır (boş liste yayımlandığı sürece silinir).
Doğrulama: `ros2 run robot_controller perception_scene_check`.

## Tarayıcı görünümü (`robot_viz` `web_view`, mantık `robot_core.webview` / `robot_core.viz`)
Salt okunur HTTP sunucusu (yalnızca `http.server`). Başlatma: `ros2 launch robot_viz web_view.launch.py`
(`host:=127.0.0.1`, `port:=8080`; konteynerde `host:=0.0.0.0`, port yalnızca ana makinenin 127.0.0.1'ine
yayımlanır). Abone olduğu konular: `/camera/{image,depth_image,camera_info}`, `/perception/objects`,
`/joint_states`; TF: `world <- camera_optical_frame` (bindirme), `world <- link_6` (uç konum).
Hiçbir şey yayımlamaz.

| Uç nokta | İçerik |
|---|---|
| `GET /`, `/app.js`, `/style.css` | sayfa (RGB + bindirme, derinlik, nesne tablosu, eklemler, uç konum; ~5 Hz yeniler) |
| `GET /frame/rgb.png` | kamera karesi + algılanan her nesne için beyaz kutu, `+` işareti ve etiket (`red#0`) |
| `GET /frame/depth.png` | derinlik renk haritası: sarı = yakın (`depth_near`, 0.5 m), mor = uzak (`depth_far`, 1.7 m), gri = ölçüm yok |
| `GET /api/state` | JSON, aşağıda |

Kare yoksa görüntü uç noktaları `503` döner; `/api/state` her zaman `200` ve aynı anahtarlarla gelir.
`GET`/`HEAD` dışındaki yöntemler `405` (`Allow: GET, HEAD`); `Host` başlığı `localhost`, `127.0.0.1` veya
`[::1]` değilse `403`; bilinmeyen yol `404`. Yanıtlar `Cache-Control: no-store`.

`/api/state` alanları (veri yoksa `null` / `[]`):

| Alan | Anlam |
|---|---|
| `time` | sunucu zamanı (epoch s) |
| `camera` | `width`, `height`, `rgb_age_s`, `depth_age_s` (son kareden beri geçen süre), `overlay` (bindirme çizilebiliyor mu) |
| `objects[]` | `name` (`red#0`), `label`, `index`, `position` [x,y,z] ve `size` (world, m), `pixel`: `center` [u,v] (kutu merkezinin izdüşümü), `bbox` (8 köşenin izdüşüm zarfı), `rect` (PNG'de çizilen tam sayı kutu) |
| `objects_age_s` | son `/perception/objects` mesajından beri geçen süre; 2 s'den eskiyse `objects` boş sayılır |
| `joints[]` | `name`, `rad`, `deg` (robot tanımındaki sırayla; eksik eklem varsa `null`) |
| `tool` | `link` (`link_6`), `position` [x,y,z] (TF, world) |

Piksel konumu, `robot_core.workspace.project` ile aynı kuralı kullanır (piksel *indeksi*, merkez +0.5).
Doğrulama: `ros2 run robot_viz viz_check` (`run_selftest gazebo-viz`).

## Olaylar (`EventSink`) ve veritabanı (`robot_data`)
Robot programları `event_sink_from_env(kaynak)` ile bir olay alıcısı alır. `ROBOT_DB_URL` yoksa
`NullEventSink` (kayıt yok); varsa `DatabaseEventSink`. URL örnekleri:
`sqlite:////tmp/robot.db`, `postgresql://robot:robot@localhost:5432/robot`.

- **Asla exception fırlatmaz, robotu yavaşlatmaz.** Veritabanına yazılamazsa olay düşer (`dropped`
  sayacı), hata loglanır, 30 s boyunca yeniden denenmez (devre kesici), sonra tekrar denenir. Diske
  tampon yoktur: erişilemezken üretilen olaylar kaybolur.
- Bağlantı ve şema ilk olayda kurulur (Alembic `upgrade head`; PostgreSQL'de `advisory lock` ile
  eşzamanlı ilk başlatmalar sıraya girer), yani veritabanı program başladıktan birkaç saniye sonra
  açılsa da sorun olmaz.
- Bağımlılıklar (pip): `sqlalchemy>=2`, `alembic`, `psycopg[binary]>=3` (yalnız PostgreSQL).
  Kurulu değilse program çalışmaya devam eder, olaylar kaydedilmez (log'da hata görünür).
- Görüntüler veritabanına gömülmez: dosya yolu (`error_reports.artifact_path`) + metaveri (`meta`/`details`).

### Tablolar
| Tablo | Sütunlar |
|---|---|
| `runs` | `id`, `source` (cli_moveit, selftest, perception, perception_scene_sync), `started_at`, `ended_at`, `meta` (JSON) |
| `events` | `id`, `run_id`, `ts`, `kind`, `payload` (JSON) |
| `error_reports` | `id`, `run_id`, `event_id`, `ts`, `component`, `code`, `message`, `details` (JSON), `artifact_path` |

Şema değişikliği: `models.py`'yi düzenle + `robot_data/migrations/versions/` altına yeni revizyon ekle
(`test_migrations_match_the_models` ikisi uyuşmazsa kırılır). Özet: `python3 -m robot_data summary`.

### Olay türleri (`events.kind` → `payload`)
| `kind` | Kaynak | `payload` |
|---|---|---|
| `motion_result` | cli_moveit | `target` [rad], `label`, `ok`, `code`, `code_name`, `message` |
| `obstacle_added` | cli_moveit, perception_scene_sync | `name`, `pose` [x,y,z], `size` [sx,sy,sz] (taşınan nesne için yeniden gönderilir) |
| `obstacle_removed` | cli_moveit, perception_scene_sync | `name` |
| `command_rejected` | cli_moveit | `line`, `reason` (isteğe bağlı) |
| `objects_changed` | perception | `objects`: `[{label, index, position, size, pixels}]` — görünen nesne kümesi değişince (her karede DEĞİL) |
| `skill_result` | cli_moveit (Skills) | `skill`, `args`, `ok`, `message`, `code_name` |
| `selftest_check` | selftest | `name`, `ok`, `detail` |
| `error` | herhangi | `component`, `code`, `message` |

`error_reports` satırı otomatik oluşan olaylar: `motion_result` (`ok=false`), `command_rejected`,
`selftest_check` (`ok=false`), `skill_result` (`ok=false`), `error`.

## Beceriler (`robot_core.skills.Skills`) — sonradan LLM'in araç arayüzü
Tek sınıf, tip belirtilmiş ve belgeli; docstring'ler araç açıklamasıdır (`Skills.catalog()` ad,
imza ve açıklamayı düz veri olarak verir). Kurallar: **hiçbir beceri exception fırlatmaz**; her çağrı
bir `SkillResult(ok, skill, message, code_name, data)` döner ve bir `skill_result` olayı üretir.
Birimler metre/radyan, konumlar `world` çerçevesinde (taban orijinde, z yukarı).

| Beceri | Açıklama |
|---|---|
| `list_objects()` | Bilinen nesneler: ad (`obj_red_1`), renk, merkez, boyut |
| `go_to_named_pose(name)` | Robot tanımındaki hazır poz (`home`, `cnc`, ...) |
| `go_to_position(x, y, z)` | Aleti (x,y,z)'ye götür, alet aşağı bakar |
| `go_above(object_name, height=0.10)` | Aleti nesnenin üst yüzeyinin `height` m üstüne götür; ad ya da benzersiz renk (`red`) |

Ulaşılamayan hedefte robot hareket etmez ve mesaj nedeni söyler: tabandan 1.5 m'den uzak hedefler
MoveIt'e hiç sorulmadan reddedilir; MoveIt'in reddettiği hedeflerde hata kodu ve ipucu mesajdadır.
**Erişim kısıtı:** eklem 3 limiti ±2.0 rad olduğundan alet, tabana yakın nesnelerde (mavi top, kırmızı
kutu) düşük yükseklikte düz aşağı bakamaz; `go_above` bu durumda açık hatayla reddeder ve daha büyük
`height` önerir. Poz hedefi MoveIt'e konum + yönelim kısıtı olarak verilir (IK: KDL,
`kinematics_solver_timeout` 0.05 s); alet ekseni etrafındaki dönüş serbesttir.

CLI: `objects`, `above <nesne> [yükseklik_m]`, `goto x y z`. Doğrulama:
`ros2 run robot_controller pose_check`.

