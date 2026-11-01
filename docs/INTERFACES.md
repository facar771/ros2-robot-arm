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

## Robot API v1 (`api_version: 1`) — Hub, LLM ve otomasyon için sözleşme
Robot tarafının dışa açtığı tek kapı: `robot_viz` `web_view commands:=true` (HTTP, JSON). **Yalnızca
`127.0.0.1`'e bağlanır ve kimlik doğrulaması yoktur**; ağa açma, kimlik doğrulama, telefon arayüzü, `.exe`
ve ESP32 girişi bu API'nin *üstündeki* Hub'ın işidir (`apps/control_hub`), robot tarafı bunları yapmaz.

**Sürümleme.** `/api/state` hem üst düzeyde (`api_version`) hem `robot.api_version` olarak sürümü taşır.
v1 içinde değişiklikler yalnızca *eklemelidir* (yeni alan, yeni uç nokta, yeni olay). Bir alanı silmek,
anlamını ya da bir durum kodunu değiştirmek `api_version: 2` demektir. İstemci bilinmeyen alanları
yok saymalıdır. Hata yanıtları her zaman JSON'dur ve `error` (Türkçe, insan okur) alanı taşır.

| Uç nokta | Durum | Anlam |
|---|---|---|
| `GET /api/state` | **var** | tüm durum (aşağıda); `robot` bloğu Hub'ın birincil okuması |
| `POST /api/command` | **var, KULLANIMDAN KALDIRILDI** (çalışmaya devam eder) | komutu çalıştırır, **bitince** döner (bloklar); yanıtta `Deprecation: true` başlığı ve `deprecated` alanı; içeride bir iş olarak çalışır (kimliği `/api/jobs`'ta görünür) |
| `POST /api/stop` | **var** (PR-6a) | ACİL DUR, hiçbir şey tarafından engellenmez |
| `POST /api/reset` | **var** (PR-6a) | kilidi açar (kol duruyorken) |
| `POST /api/jobs` | **var** (PR-6b) | işi başlatır, **hemen** `202` döner |
| `GET /api/jobs`, `GET /api/jobs/<id>` | **var** (PR-6b) | son işler (en yeni önce, ≤ 50 tutulur) / tek iş belgesi |
| `POST /api/jobs/<id>/cancel` | **var** (PR-6b) | çalışan işi iptal eder (kilitlemez) |

### `robot` bloğu (`GET /api/state` → `robot`; her zaman aynı anahtarlar)
| Alan | Anlam |
|---|---|
| `api_version` | `1` |
| `mode` | `mock` \| `gazebo` \| `real` (`web_view mode:=...`, varsayılan `gazebo`) |
| `state` | `idle` hazır · `busy` bir komut çalışıyor · `estopped` acil dur kilitli · `error` son komut **iç hatayla** bitti (bir sonraki başarılı komut ya da `reset` temizler; ulaşılamayan hedef gibi 422'ler `error` DEĞİLDİR) |
| `current_job` | çalışan komut: `id` (`j1`, `j2`...), `command`, `started_at` (epoch s), `elapsed_s`; yoksa `null` |
| `last_error` | son başarısız komut: `command`, `message`, `time`, `internal` (iç hata mı); yoksa `null` |
| `moving` | herhangi bir eklem hızı > 0.05 rad/s ise `true`; hız verisi yoksa ya da 1 s'den eskiyse `null` ("bilmiyorum", asla "duruyor" sayılmaz) |
| `estop` | `tripped`, `reason`, `since` (epoch s) |
| `safety` | `zone` (`frame`, `box` {x,y,z: [alt, üst]} m), `max_velocity_scale`, `velocity_scale` (MoveIt'e gerçekten istenen = min(0.5, tavan)); `web_view` komutsuz başlatıldıysa `null` |

Öncelik: `estopped` > `busy` > `error` > `idle`. Her şey bilgi amaçlıdır; kararı sunucu verir
(`423` / `409` yanıtları).

### `POST /api/stop` — acil dur
Gövde isteğe bağlıdır (donanım düğmesi/`curl -X POST` yeterli); varsa `{"reason": "<≤64 karakter>"}`.
Çözülemeyen gövde bile reddedilmez. `Host` loopback değilse ya da yabancı `Origin` /
`Sec-Fetch-Site: cross-site` varsa `403` (başka bir sayfa tetikleyemez). Sırasıyla:
1. kilidi **hemen** koyar (bundan sonra hiçbir komut hareket başlatamaz, çalışan senaryo sonraki adımda biter);
2. MoveIt'e `move_action` hedefini iptal ettirir ve `/trajectory_execution_event`'e `stop` yayımlar
   (kol bu noktada tutma konumuna geçer); istek `0.5 s`'de bir tekrarlanır, kol durana kadar;
3. **kolun gerçekten durduğunu `/joint_states` hızlarından doğrular**: tüm eklem hızları 0.25 s boyunca
   ≤ 0.05 rad/s; en fazla 5 s bekler.

Yanıt `200` (kilit her durumda konur): `{"ok": true, "state": "estopped", "latched": true,
"stopped": bool, "stop_time_s": float, "max_joint_speed": float|null, "was_running": bool,
"message": ...}`. **`stopped: false` ise robotun durduğu doğrulanamadı** (5 s içinde durmadı ya da hız
okunamadı) ve istemci bunu bir arıza olarak ele almalıdır; kilit yine de geçerlidir. Kilitliyken
çalışan komutun yanıtı `422`'dir (`PREEMPTED` / "acil dur etkin"), `last_error`'a düşer. Her stop bir
`estop` olayıdır (ve `error_reports` satırı).

**Kapsam (dürüst sınır):** bu, web_view'in başlattığı ve MoveIt üzerinden giden hareketlerin yazılım
acil durudur. `/trajectory_execution_event` `stop`'u MoveIt'in tüm yürütmesini durdurur, ama kilit ve
`423` yalnızca bu sunucuda geçerlidir (ör. ayrıca çalışan bir `cli_moveit` kilitlenmez). `mode: real`
için **donanım acil durunun yerine geçmez**.

### `POST /api/reset`
Gövde `{}` (`Content-Type: application/json`). Kilidi (ve `error` durumunu) temizler. Yalnızca **kol
duruyorken**: komut çalışıyorsa (kilitliyse sonlanması için ≤ 3 s beklenir) ya da `moving` `true`/`null`
ise `409` + `error` (kilit korunur). Zaten hazırsa `200` + `"changed": false`. Yanıt:
`{"ok", "changed", "message", "state"}`.

### İşler: `POST /api/jobs` (Hub/otomasyonun asıl kapısı)
Gövde (`Content-Type: application/json`): `{"command": "above green 0.35", "timeout_s": 60}`; komut aynı
beyaz listeden geçer (`home`, `objects`, `above <renk> [m]`, `demo_scenario`), `timeout_s` isteğe bağlı
(1-600 s; varsayılan: home 90, objects 30, above 120, demo_scenario 300). **Aynı anda tek iş.**

| Durum | Anlam |
|---|---|
| `202` | iş başladı; gövde iş belgesi, `Location: /api/jobs/<id>` |
| `400` | geçersiz komut/gövde/`timeout_s` (iş açılmaz) |
| `409` | başka iş çalışıyor: `{"error", "job_id"}` |
| `423` | acil dur kilitli |
| `403`/`411`/`413`/`415` | `/api/command` ile aynı |

İş belgesi (`GET /api/jobs/<id>`; bilinmeyen kimlik `404`):

| Alan | Anlam |
|---|---|
| `id`, `command` | `j7`, normalleştirilmiş komut |
| `state` | `running` · `done` (komut başardı) · `failed` (robot yapamadı, iç hata ya da zaman aşımı; neden `error`) · `cancelled` (`cancelled_by`: `user` ya da `estop`) |
| `created_at`, `finished_at`, `elapsed_s`, `timeout_s` | zamanlar (epoch s) ve süreler |
| `cancel_requested`, `cancelled_by`, `timed_out` | iptal/zaman aşımı bilgisi |
| `progress` | `step`/`steps` (senaryonun `[k/n]` anlatımından; diğer komutlarda `null`), `say` (son anlatım), `log` (komutun canlı anlatımı) |
| `result` | iş bitince `{ok, message, data, duration_s}` (`/api/command` yanıtıyla aynı `data`); öncesinde `null` |
| `error` | `failed`/`cancelled` için Türkçe neden, aksi halde `null` |

**İptal:** `POST /api/jobs/<id>/cancel` (gövde `{}`): çalışan MoveIt hedefi iptal edilir (kol durur),
senaryo bir sonraki adıma geçmez, iş `cancelled` olur; yanıt işin bitmesini ≤ 3 s bekler (`200`,
`{"ok", "job"}`). **Kilitlemez**: `state` `idle` olur ve yeni iş hemen kabul edilir (acil durdaki
`reset` gerekmez). Bitmiş iş için `409`, bilinmeyen iş `404`. **Zaman aşımı** aynı yolu kullanır ve
işi `failed` (`timed_out: true`) yapar. **Acil dur** (`/api/stop`) çalışan işi `cancelled` +
`cancelled_by: "estop"` ile bitirir. Dürüst sınır: `cancel` ve zaman aşımı kolun durduğunu *hız
doğrulamasıyla* kanıtlamaz (bunu yalnızca `/api/stop` yapar); `moving` alanını izle.

### Güvenli çalışma kutusu ve hız tavanı (`robot_moveit_config/config/safe_zone.yaml`)
`Skills`, **hedef noktası** kutunun dışında kalan her kartezyen hareketi (`go_to_position`,
`go_above`) açık hatayla (`code_name: SAFE_ZONE`, "hedef güvenli çalışma kutusunun dışında (z=...,
izin verilen aralık [...]), robot hareket etmedi") reddeder; robot oynamaz. Yalnızca hedef denetlenir,
yol MoveIt'in çarpışma denetimindedir; adlandırılmış eklem pozları (`home`) denetlenmez. Varsayılan
kutu (m, world): x [0.05, 0.75], y [-0.5, 0.5], z [0.02, 0.70]; `max_velocity_scale` (0.5) MoveIt'e
istenebilecek hız/ivme ölçeğinin üst sınırıdır. Dosya eksik/bozuksa `web_view` ve `cli_moveit`
**başlamaz** ("sınırsız" varsayılmaz). Yol `ROBOT_SAFETY` ortam değişkeniyle değiştirilir. Geçerli
değerler `/api/state` → `robot.safety`'de görünür.

### Durum kodları (`/api/command` ve `/api/jobs`; v1)
`200` yapıldı · `400` geçersiz/beyaz liste dışı · `403` kapalı/yabancı Origin-Host · `405` yöntem ·
`409` başka komut çalışıyor · `411/413` gövde uzunluğu · `415` içerik türü · `422` geçerli komut, robot
yapamadı (neden `message`'da) · **`423` acil dur kilitli: komut hiç çalışmadı, gövde
`{"error", "state": "estopped"}`; önce `/api/reset`** · `500` iç hata.

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
| `api_version` | `1` (sözleşme sürümü) |
| `time` | sunucu zamanı (epoch s) |
| `robot` | durum makinesi, acil dur, çalışan iş: yukarıdaki "`robot` bloğu" |
| `camera` | `width`, `height`, `rgb_age_s`, `depth_age_s` (son kareden beri geçen süre), `overlay` (bindirme çizilebiliyor mu) |
| `objects[]` | `name` (`red#0`), `label`, `index`, `position` [x,y,z] ve `size` (world, m), `pixel`: `center` [u,v] (kutu merkezinin izdüşümü), `bbox` (8 köşenin izdüşüm zarfı), `rect` (PNG'de çizilen tam sayı kutu) |
| `objects_age_s` | son `/perception/objects` mesajından beri geçen süre; 2 s'den eskiyse `objects` boş sayılır |
| `joints[]` | `name`, `rad`, `deg` (robot tanımındaki sırayla; eksik eklem varsa `null`) |
| `tool` | `link` (`link_6`), `position` [x,y,z] (TF, world) |
| `commands` | `enabled` (komut ucu açık mı), `allowed` (beyaz liste, kapalıysa `[]`) |
| `command` | `running`, `name` (çalışan komut), `job` (`id`, `command`, `started_at`, `elapsed_s`; yoksa `null`), `log` (o komutun anlatımı, son 60 satır, canlı), `last` (`command`, `ok`, `message`, `finished_at`), `last_error` (`command`, `message`, `time`, `internal`) |

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
| `demo_scenario` | `home` → `above green 0.25` → `above red 0.35` → `home`; her adımdan sonra TF ile uç konum ölçülür: hedefe ≤ 1.5 cm, ikinci `home` birincisine ≤ 3 cm (kol yukarıda ~1 cm sallanır); ilk başarısız/doğrulanamayan adımda durur; `data.steps[]`: `step`, `say`, `ok`, `message`, `target`, `measured`, `error_cm` |

Yanıt: `{"ok", "command" (normalleştirilmiş), "message", "data", "duration_s"}`; komut **bitince**
döner (planlama + yürütme birkaç saniye-dakika sürebilir; istemci zaman aşımını uzun tut).

| Durum | Ne zaman |
|---|---|
| `200` | komut yapıldı (`ok: true`) |
| `400` | beyaz liste dışı komut, geçersiz argüman, geçersiz/fazlalıklı JSON; gövdede `error` + `allowed`; robot oynamaz |
| `403` | komut ucu kapalı; `Host` loopback değil; yabancı `Origin`; `Sec-Fetch-Site` ≠ same-origin/none |
| `405` | `POST` dışı yöntem (`Allow: POST`) |
| `409` | başka bir komut çalışıyor (aynı anda tek komut) |
| `423` | acil dur kilitli (`/api/stop` sonrası); önce `/api/reset` |
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
| `command_rejected` | cli_moveit, web_view | `line`, `reason` (isteğe bağlı); acil dur kilitliyken reddedilen komut için de |
| `estop` | web_view | `reason`, `latched`, `stopped` (doğrulandı mı), `stop_time_s`, `max_joint_speed`, `was_running`, `message` |
| `estop_reset` | web_view | `had_error` |
| `objects_changed` | perception | `objects`: `[{label, index, position, size, pixels}]` — görünen nesne kümesi değişince (her karede DEĞİL) |
| `skill_result` | cli_moveit, web_view (Skills) | `skill`, `args`, `ok`, `message`, `code_name` |
| `scenario_result` | cli_moveit, web_view (`demo_scenario`) | `name`, `ok`, `message`, `steps[]` (`step`, `say`, `ok`, `message`, `target`, `measured`, `error_cm`) |
| `selftest_check` | selftest | `name`, `ok`, `detail` |
| `error` | herhangi | `component`, `code`, `message` |

`error_reports` satırı otomatik oluşan olaylar: `motion_result` (`ok=false`), `command_rejected`, `estop` (her acil dur),
`selftest_check` (`ok=false`), `skill_result` (`ok=false`), `scenario_result` (`ok=false`), `error`.

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
pencereler (nesne üstünden, m): mavi ≈0.08-0.42, kırmızı ≈0.30-0.42, yeşil ≈0.20-0.40; **0.35 üçü için de
çalışır** (arayüz varsayılanı). Pencerenin *kenarı* güvenilmezdir: `red 0.30` eklem 3 limitine ~0.01 rad
kalır, simülasyonda kol limiti hafifçe aşınca MoveIt sonraki planı `START_STATE_INVALID` ile reddeder;
bu yüzden demo ve varsayılan pencerenin içinden seçilir (limit payı ≥ 0.05 rad). `go_above` pencere dışında açık hatayla reddeder, robot oynamaz. Poz hedefi MoveIt'e konum + yönelim kısıtı olarak verilir (IK: KDL,
`kinematics_solver_timeout` 0.05 s); alet ekseni etrafındaki dönüş serbesttir.

CLI: `objects`, `above <nesne> [yükseklik_m]`, `goto x y z`, `demo_scenario` (Skills üstünde, uç konum
TF ile doğrulanır; ayrıntı yukarıdaki `POST /api/command` tablosunda). Doğrulama:
`ros2 run robot_controller pose_check`.

