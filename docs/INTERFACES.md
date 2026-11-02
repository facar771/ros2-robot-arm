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
HTTP sunucusu (yalnızca `http.server`); varsayılan **salt okunur**, `commands:=true` ile beyaz listeli
komutlar açılır (aşağıda). Başlatma: `ros2 launch robot_viz web_view.launch.py`
(`host:=127.0.0.1`, `port:=8080`; konteynerde `host:=0.0.0.0`, port yalnızca ana makinenin 127.0.0.1'ine
yayımlanır). Abone olduğu konular: `/camera/{image,depth_image,camera_info}`, `/perception/objects`,
`/joint_states`; TF: `world <- camera_optical_frame` (bindirme), `world <- link_6` (uç konum).
Hiçbir şey yayımlamaz (komutlar açıksa hareketi MoveIt'e `cli_moveit` ile aynı Skills katmanı üstünden
yaptırır).

| Uç nokta | İçerik |
|---|---|
| `GET /`, `/app.js`, `/style.css` | sayfa (RGB + bindirme, derinlik, nesne tablosu, eklemler, uç konum; ~5 Hz yeniler) |
| `GET /frame/rgb.png` | kamera karesi + algılanan her nesne için beyaz kutu, `+` işareti ve etiket (`red#0`) |
| `GET /frame/depth.png` | derinlik renk haritası: sarı = yakın (`depth_near`, 0.5 m), mor = uzak (`depth_far`, 1.7 m), gri = ölçüm yok |
| `GET /api/state` | JSON, aşağıda |
| `POST /api/command` | beyaz listeli komut (aşağıda); `commands:=true` değilse `403` |

Kare yoksa görüntü uç noktaları `503` döner; `/api/state` her zaman `200` ve aynı anahtarlarla gelir.
Diğer yollarda `GET`/`HEAD` dışındaki yöntemler `405` (`Allow: GET, HEAD`; `/api/command` için `Allow: POST`); `Host` başlığı `localhost`, `127.0.0.1` veya
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
| `commands` | `enabled` (komut ucu açık mı), `allowed` (beyaz liste, kapalıysa `[]`) |
| `command` | `running`, `name` (çalışan komut), `log` (o komutun anlatımı, son 60 satır, canlı), `last` (`command`, `ok`, `message`, `finished_at`) |

Piksel konumu, `robot_core.workspace.project` ile aynı kuralı kullanır (piksel *indeksi*, merkez +0.5).
Doğrulama: `ros2 run robot_viz viz_check` (`run_selftest gazebo-viz`, komutlar kapalı) ve
`ros2 run robot_viz cmd_check` (`run_selftest gazebo-cmd`, komutlar açık).

### `POST /api/command` (yalnızca `web_view commands:=true`)
Gövde: `{"command": "<komut>"}` (başka anahtar yok, ≤ 512 bayt), `Content-Type: application/json`.
Komut metni katı bir beyaz listeden geçer (`robot_core.webcmd.parse_command`; harf, rakam, boşluk,
nokta, virgül, alt çizgi dışındaki karakter, ≤ 64 karakter); büyük/küçük harf fark etmez.

| Komut | Anlam |
|---|---|
| `home` | `Skills.go_to_named_pose('home')` |
| `objects` | `Skills.list_objects()`; `data.objects[]`: `name`, `label`, `position`, `size` |
| `above <red\|green\|blue> [yükseklik_m]` | `Skills.go_above(renk, yükseklik)`, yükseklik 0-0.5 (varsayılan 0.10; `0,25` de olur); `kırmızı`, `yeşil`, `mavi` takma adları; `data.target` [x,y,z] |
| `demo_scenario` | `home` → `above green 0.25` → `above red 0.30` → `home`; her adımdan sonra TF ile uç konum ölçülür: hedefe ≤ 1.5 cm, ikinci `home` birincisine ≤ 3 cm (kol yukarıda ~1 cm sallanır); ilk başarısız/doğrulanamayan adımda durur; `data.steps[]`: `step`, `say`, `ok`, `message`, `target`, `measured`, `error_cm` |

Yanıt: `{"ok", "command" (normalleştirilmiş), "message", "data", "duration_s"}`; komut **bitince**
döner (planlama + yürütme birkaç saniye-dakika sürebilir; istemci zaman aşımını uzun tut).

| Durum | Ne zaman |
|---|---|
| `200` | komut yapıldı (`ok: true`) |
| `400` | beyaz liste dışı komut, geçersiz argüman, geçersiz/fazlalıklı JSON; gövdede `error` + `allowed`; robot oynamaz |
| `403` | komut ucu kapalı; `Host` loopback değil; yabancı `Origin`; `Sec-Fetch-Site` ≠ same-origin/none |
| `405` | `POST` dışı yöntem (`Allow: POST`) |
| `409` | başka bir komut çalışıyor (aynı anda tek komut) |
| `411` / `413` | `Content-Length` yok (ör. chunked) / geçersiz ya da 512 bayttan büyük |
| `415` | `Content-Type` `application/json` değil |
| `422` | komut geçerli ama robot yapamadı (erişilemeyen hedef, çarpışma, nesne yok...); `message` nedeni söyler, robot hareket etmedi |
| `500` | iç hata (ayrıntı yalnızca sunucu günlüğünde) |

Başka web sayfalarının (CSRF) kullanıcının tarayıcısı üzerinden komut göndermesine karşı: JSON içerik
türü zorunlu (başka sitenin ön uçuşsuz gönderebileceği `text/plain`/form türleri `415`), yabancı
`Origin` ve `Sec-Fetch-Site: cross-site` `403`, `Host` beyaz listesi (DNS rebinding). Kimlik doğrulama
yoktur: erişim, portun yalnızca `127.0.0.1`'e yayımlanmasına dayanır (`compose.yaml`).
Reddedilen istekler `command_rejected` olayı üretir (`ROBOT_DB_URL` varsa `error_reports`'a da düşer).

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
| `command_rejected` | cli_moveit, web_view | `line`, `reason` (isteğe bağlı) |
| `objects_changed` | perception | `objects`: `[{label, index, position, size, pixels}]` — görünen nesne kümesi değişince (her karede DEĞİL) |
| `skill_result` | cli_moveit, web_view (Skills) | `skill`, `args`, `ok`, `message`, `code_name` |
| `scenario_result` | cli_moveit, web_view (`demo_scenario`) | `name`, `ok`, `message`, `steps[]` (`step`, `say`, `ok`, `message`, `target`, `measured`, `error_cm`) |
| `selftest_check` | selftest | `name`, `ok`, `detail` |
| `error` | herhangi | `component`, `code`, `message` |

`error_reports` satırı otomatik oluşan olaylar: `motion_result` (`ok=false`), `command_rejected`,
`selftest_check` (`ok=false`), `skill_result` (`ok=false`), `scenario_result` (`ok=false`), `error`.

## Görüntü çekirdeği (`robot_vision`, ROS'suz)
`robot_vision` yalnızca numpy kullanır; ROS'a ve `robot_*` paketlerinin geri kalanına dokunmaz
(`robot_core.perception`'dan renk sınıfları, `Intrinsics` ve `Transform`'u kullanır).

| Parça | Arayüz |
|---|---|
| `Frame` | `rgb` (H,W,3 uint8), `depth` (H,W, z-derinliği m), `intrinsics`, `world_from_optical`, `stamp` (s) |
| `Detector.detect(frame) -> [Segment]` | `Segment`: `label`, piksel indeksleri `rows`/`cols`, `score`. Hata fırlatmaz. `ColorDetector` HSV + bağlı bileşenler (`robot_core.perception` ile aynı renk sınıfları) |
| `measure(frame, segment, cfg=None, origin=None) -> ObjectMeasure \| None` | Geçerli derinlikli < 20 nokta varsa `None` |
| `Tracker.update(observations, stamp) -> [Track]` | `Observation(label, position, size)`; `Track`: `id` (asla yeniden kullanılmaz), `state` (`confirmed`/`lost`), `position`, `velocity`, `time_since_seen` |

`ObjectMeasure` alanları (hepsi `world`, metre): `shape` (`box`/`cylinder`/`sphere`/`unknown`),
`center`, `size` (eksen hizalı), `height` (= üst - zemin), `radius`, `obb_*` (yönlü kutu: `a ≥ b`,
`yaw` rad), `nearest`/`farthest` (kameraya göre **görünür** en yakın/en uzak nokta),
`edge_points` (kutu: 8 köşe; silindir: üst/alt kenar noktaları; küre: 6 uç nokta; ayrıca `nearest`,
`top_center`), `distances_from(nokta)` (her kenar noktasına mesafe), `grasps` (tutma adayları:
`kind` `top`/`side`, `position`, `approach`, `yaw`, `width`, `fits` = `width ≤ max_aperture`).

Varsayımlar (kodda belgeli, testle sınanır): nesne zemine oturur ve ekseni dikeydir; üst yüz
kameradan görünür; **arka yüz görünmez**, bu yüzden `farthest` nesnenin gerçek uzak ucu DEĞİL, görünür
en uzak noktadır. Şekil tahmini sezgiseldir (küre-uydurma kalıntısı + düz üst yüzün ayak izine oranı +
üst yüzün dolgusu); emin değilse `unknown` döner ve yönlü kutu yine verilir. Eşikler derinlik
gürültüsü σ ≤ 3 mm için ayarlıdır (σ = 5 mm'de kutu bazen silindire kayar; Gazebo kamerası gürültüsüzdür).
Doğrulama: `python3 -m robot_vision.validate` (`README.md`, "Görüntü çekirdeği").

## Kamera platformu ve hedefe kilitlenme (`robot_vision_ros`, `tracking.sdf`)
Kolsuz ayrı dünya: `ros2 launch robot_vision_ros tracking.launch.py [headless:=true] [tracker:=true]`. Hepsi
simüle zamanda (`use_sim_time`).

| Konu | Arayüz |
|---|---|
| Kamera | `/camera/{image,depth_image,camera_info}` (320×240, yatay FOV 1.047, 10 Hz); kamera platformla hareket eder, TF yayımlanmaz (konum `/platform/joint_states`'ten, `robot_vision.platform` ileri kinematiği) |
| Eklem durumu | `/platform/joint_states` (`sensor_msgs/JointState`: `yaw_joint` rad, `slide_joint` m, `pitch_joint` rad; konum + hız; fizik adımı hızında, 100 Hz) |
| Hız komutları | `/platform/{yaw,slide,pitch}_joint/cmd_vel` (`std_msgs/Float64`, rad/s ya da m/s). **Komut yeni komut gelene kadar sürer**: düğüm kapanırken sıfır yayımlar. Sınırlar: yaw ±1.2 rad (1.5 rad/s), slide 0-2.5 m (0.8 m/s), pitch -0.6..1.2 rad (1.0 rad/s); pitch artı = kamera AŞAĞI bakar |
| Hedef | `/world/tracking/set_pose` (`ros_gz_interfaces/srv/SetEntityPose`, köprülü gz servisi); model adı `target`; yalnızca `set_pose` ile hareket eder |
| Mod | `/tracking/mode` (`std_msgs/String`): `idle` (varsayılan, hızlar sıfır) · `track` (kırmızı nesneyi izle) · `home` (platformu başlangıç pozuna götür). Mod mesajını birkaç kez yayımla (tek mesaj keşif yarışında kaybolabilir) |
| Durum | `/tracking/status` (`std_msgs/String`, JSON): `track` modunda her görüntüde `stamp` (görüntü damgası, sim s), `visible`, `pixel_error` (px), `distance_error` (m), `in_window`, `locked`, `timer`, `track`, `q`, `cmd`; `home` modunda `homed`, `q` |
| Mesafe tanımı | Kırmızı segmentin **görünen yüzeyinin** ortalama z-derinliği (kürenin merkezi değil); varsayılan ayar noktası 2.0 m |

Kontrol akışı: renk dedektörü → segmentin 3B merkezi (kamera çerçevesi) → görüntü zamanındaki eklemlerle dünya çerçevesine
→ `robot_vision.Tracker` (dünya çerçevesinde, hedefin kendi hızı) → güncel kamera pozuna göre hata + görüş hattı hızının
ileri beslemesi → PID → hız komutu. Gazebo notları (tasarım kararları, `tracking.sdf` içinde de yazılı): fizik motoru
**bullet-featherstone** (varsayılan DART, tek zincirde üç hız-aktüatörüyle, ilk doygun hızı saniyelerce tutup sonraki komutları
yok sayıyordu; yalın bir komut tekrarıyla yeniden üretildi), **yer çekimi kapalı** (hedef serbest cisim; bullet-featherstone
bağlantı başına `<gravity>false</gravity>`'ı yok sayıp hedefi düşürüyordu), fizik adımı 10 ms ve gerçek zaman faktörü 0.4
(2 CPU + yazılımsal görüntülemede köprü aç kalıp komutları gecikmeli iletiyordu).

## Plugin v1 (paketler) — `robot_plugins`, `plugins/`
Algı/beceri yeteneklerini paket olarak yazmak ve LLM'in çağıracağı **araç** olarak sunmak için (ROS'suz).
Her paket `plugins/<ad>/` altında bir dizindir: `package.yaml` + giriş modülü (`main.py`). **Yalnızca bu
depodan gelen, gözden geçirilmiş paketler** çalıştırılır; zip yükleme/uzaktan kod yüklemesi YOKTUR
(tasarım: `docs/plugin-upload-design.md`). İzinler paketin eline verilen API nesneleriyle uygulanır,
işletim sistemi sandbox'ı değildir: paket *güvenilir kod* sayılır.

### `package.yaml`
| Anahtar | Anlam |
|---|---|
| `name` | `a-z`, rakam, `_`; 2-40 karakter; **dizin adıyla aynı** |
| `version` | `MAJOR.MINOR.PATCH` |
| `description` | insan okur |
| `entry` | `modül:Sınıf` (`main:Plugin`); modül paket dizinindeki `.py`, sınıf `robot_plugins.PluginBase` alt sınıfı |
| `permissions` | `camera:read` (kare okuma), `robot:jobs` (Robot API v1 iş kuyruğu: `state`/`submit_job`/`job`/`cancel`). **`robot:stop` istenmez ve elinden alınamaz**: her pakette `ctx.robot.stop()` çalışır; manifestte `deny`/bilinmeyen izin geçersizdir. Paketin `reset` yetkisi yoktur (kilidi yalnızca insan açar) |
| `parameters` | JSON-Schema alt kümesi (`type object`); varsayılanlar uygulanır, `enable` sırasında doğrulanır |
| `tools[]` | `name`, `description` (LLM bunu okur), `input_schema` (JSON-Schema alt kümesi). **LLM araç katmanı (Robot sohbetinin Aşama 7'si) bu listeyi olduğu gibi alır:** `Manifest.describe()['tools']` |

JSON-Schema alt kümesi (`robot_plugins.schema`): `type` (object/array/string/number/integer/boolean/null),
`properties`, `required`, `additionalProperties: false`, `enum`, `minimum`/`maximum`, `minLength`/`maxLength`,
`pattern`, `items`, `minItems`/`maxItems`, `default`. Başka anahtar **manifestte hata** verir (vaat edilip
uygulanmayan doğrulama olmasın).

### Araç sözleşmesi
`PluginBase.call(tool, args) -> dict`; `args` şemaya göre doğrulanmış ve varsayılanları doldurulmuştur.
Beklenen başarısızlıkta **istisna atma**: `{"ok": false, "error": "..."}` dön (LLM okur). Yönetici her çağrıyı
`{"ok": true, "data": {...}}` ya da `{"ok": false, "code", "error"}` olarak sarar; `code`: `bad_arguments`,
`unknown_tool`, `not_running`, `permission_denied`, `stopped`, `timeout`, `robot_<durum>` (ör. `robot_423`),
`exception`. Uzun işler `ctx.stop` (StopToken) belirtecini en az 0.5 s'de bir yoklamalıdır.

### `PluginContext` (`ctx`)
`params`, `log(msg)`, `stop` (`is_set`/`wait`/`check`), `camera.latest_frame()` → `robot_vision.Frame`
(`camera:read`), `robot.submit_job(komut)`, `robot.wait_for_job(id, ctx.stop)`, `robot.state()`,
`robot.cancel(id)` (`robot:jobs`), `robot.stop(neden)` (her zaman). Hareket **yalnızca** Robot API v1
üzerinden gider (beyaz listeli komutlar, güvenli çalışma kutusu, hız tavanı, acil dur); `423` alan paket
yeniden denemez.

### `PluginManager` ve alt süreçler
Her etkin paket kendi alt sürecinde çalışır (`python -m robot_plugins.runner`; stdin/stdout'ta satır başına
bir JSON: `call`/`stop` → `ready`/`result`/`log`). Durumlar: `stopped`, `starting`, `running`, `crashed`,
`invalid` (manifest hatası; listede görünür). `disable`: önce durdurma belirteci, `grace_s` (3 s) sonra
`SIGTERM`/`SIGKILL`. **Acil dur bağlantısı:** `PluginManager.emergency_stop()` tüm paketlere durdurma
belirtecini koyar, robota `POST /api/stop` gönderir (başarısız olsa bile paketler durdurulur ve hata
raporlanır), sonra süreçleri toplar; ardından insan `clear_estop()`/robot `reset` yapana dek paket
etkinleştirilmez. `watch_robot()` robot `estopped` bildirince (düğme, Hub, herhangi biri) paketleri durdurur;
ulaşılamayan Robot API'si acil dur sayılmaz.

### Yönetim API (`python -m robot_plugins serve`, varsayılan `127.0.0.1:8770`, robot API'sinden AYRI port)
| Uç nokta | Anlam |
|---|---|
| `GET /api/plugins` | `{"api_version": 1, "plugins": [durum...]}` (geçersiz paketler `status: invalid` + `error` ile) |
| `GET /api/plugins/<ad>`, `.../logs` | tek paket (+ manifest özeti, araç kataloğu) / son günlük satırları |
| `POST /api/plugins/<ad>/enable` | gövde `{"params": {...}}` (isteğe bağlı) → durum; `400` parametre, `404` yok, `423` acil dur |
| `POST /api/plugins/<ad>/disable` | → durum |
| `POST /api/plugins/estop` | gövde `{"reason": ...}`; tüm paketleri durdurur **ve** robota `/api/stop` yollar |

Koruma, robotun web görünümüyle aynı: yalnızca loopback'e bağlanır (aksi kurucuda hata), `Host` loopback
olmalı (`403`), yabancı `Origin`/`Sec-Fetch-Site: cross-site` `403`, POST `application/json` (`415`) ve ≤ 4 KB
(`413`), `PUT/DELETE/PATCH` `405`. Kimlik doğrulama yok: erişim = bu makinede olmak. **Araç çağırma bilerek
HTTP'ye açılmadı** (`404`): bu, LLM katmanının işidir (`PluginManager.call`). Hub'ın "paketler" paneli bu
API'yi kullanacak.

### İlk paketler (`plugins/`)
| Paket | İzin | Araç |
|---|---|---|
| `find_object` | `camera:read` | `find_object(color)` → merkez, boyut, şekil, yönlü kutu, kenar noktaları, tutma adayları (`robot_vision`, PR-V1) |
| `follow_color` | `camera:read`, `robot:jobs` | `follow_color(color, height_m, duration_s, min_move_m)` → nesne yer değiştirdikçe `above <renk> <yükseklik>` işi gönderir; acil dur/iptal/süre dolunca biter |
| `scan_extent` | `camera:read` | `scan_extent()` → görünen tüm nesneler + ölçüleri (tek bakış; kamerayı döndüren tarama PR-V4'te) |

**Robot API bağımlılığı (dürüst not):** `/api/jobs` ve `/api/stop` bu dalın tabanında (`main`) henüz yoktur;
Robot sohbetinin açık PR'larındadır (#14 Aşama 6a, #15 Aşama 6b). `HttpRobotApi` bu belgedeki v1
sözleşmesine göre yazıldı ve **bu sözleşmeyi uygulayan bir test sunucusuyla** doğrulandı; gerçek `web_view`
ile uçtan uca denenmedi. Camera platformunu (PR-V2) süren bir paket için `platform:rates` izni henüz tanımlı
değildir (ROS bağımlı; ileride).

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
**Erişim kısıtı:** alet düz aşağı bakarken eklem 3 (±2.0) ve eklem 5 (±1.57) limitleri, her nesne için
yalnızca dar bir yükseklik *penceresi* bırakır: çok alçak da çok yüksek de ulaşılamaz (tabana ~0.30 m
uzaktaki bir hedef için hiç pencere yoktur; mavi top bu yüzden x=0.45'e taşındı). `workspace.sdf`'teki
pencereler (nesne üstünden, m): mavi ≈0.08-0.42, kırmızı ≈0.30-0.42, yeşil ≈0.20-0.40; **0.30 üçü için de
çalışır** (arayüz varsayılanı). `go_above` pencere dışında açık hatayla reddeder, robot oynamaz. Poz hedefi MoveIt'e konum + yönelim kısıtı olarak verilir (IK: KDL,
`kinematics_solver_timeout` 0.05 s); alet ekseni etrafındaki dönüş serbesttir.

CLI: `objects`, `above <nesne> [yükseklik_m]`, `goto x y z`, `demo_scenario` (Skills üstünde, uç konum
TF ile doğrulanır; ayrıntı yukarıdaki `POST /api/command` tablosunda). Doğrulama:
`ros2 run robot_controller pose_check`.

