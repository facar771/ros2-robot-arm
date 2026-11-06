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

## Ajan araç katmanı (`robot_agent`, Aşama 7): bir LLM'in robota güvenli erişimi
Paket `robot_agent`; mantığın tamamı ROS'suz, robota **yalnızca Robot API v1 üzerinden (HTTP, 127.0.0.1)**
ulaşır: istemci `127.0.0.1`/`localhost` dışındaki adresleri reddeder. **Serbest kod çalıştırma yoktur**:
bir LLM'in yapabildiği tek şey aşağıdaki beyaz listeli araçları çağırmaktır.

**Araçlar** (`robot_agent.tools.build_tools()`; şemalar `robot_core.webcmd` sabitlerinden ve
`Skills` docstring'lerinden üretilir, Anthropic `input_schema` biçimindedir):

| Araç | Risk | Ne yapar |
|---|---|---|
| `list_objects` | read | `objects` komutu (nesne adı/renk/konum/boyut) |
| `get_robot_state` | read | `/api/state` → `robot` bloğu |
| `go_home` | motion | `home` |
| `go_above_object` `{color: red\|green\|blue, height_m: 0-0.5 (0.35)}` | motion | `above <renk> <h>` |
| `run_demo_scenario` | long | `demo_scenario` |
| `run_plugin` `{plugin, tool, arguments}` | plugin (çağrı başına eklentinin `motion` sınıfı) | beyaz listeli bir **eklenti aracı** (aşağıda) |
| `cancel_current_job` | safety | çalışan işi iptal eder (kilitlemez) |
| `emergency_stop` `{reason?}` | safety | `POST /api/stop` |

**Bilerek yok:** `reset` (acil durdan sonra kilidi **yalnızca insan** açar), ham koordinat/`goto`, kabuk, kod,
`addbox`. Tüm şemalar `additionalProperties: false`; sayılar sonlu olmalı, bool sayı sayılmaz.

**`ToolExecutor` boru hattı** (her çağrı bundan geçer; `safety` araçları orta kısmı atlar, hiçbir şey
durdurmayı geciktiremez): 1) araç beyaz listede ve açık mı (`rejected`) → 2) argümanlar şemaya uyuyor mu
(`rejected`, robot'a hiçbir şey gitmez) → 3) robot durumu: `estopped` → `estopped` (hareket yok), `busy` →
`refused`, API yok → `refused` (körlemesine hareket verilmez) → 4) hareket çağrısı kotası
(`Policy.max_motion_calls`, 10) → 5) `go_above_object` için **gerçek nesne konumundan hedef hesaplanır ve
robotun bildirdiği güvenli kutuyla (`robot.safety.zone`) denetlenir** (`refused`, iş açılmaz) → 6) **kuru
çalıştırma** (`dry_run`) burada biter ve ne çalışacağını söyler → 7) **onay** (`Policy.confirm_risks`, varsayılan
`motion` ve `long`; onaylayan yoksa `refused`) → 8) iş olarak gönderilir ve sonuna kadar izlenir. Sunucu da
kendi güvenli kutusunu/kilidini ayrıca uygular (savunma derinliği).

**Acil dur önceliği:** (a) `emergency_stop` her denetimden önce, onaysız ve kotadan bağımsız gider;
sonucunda robotun *durduğu hız doğrulamasıyla* bildirilir (`ok: true`) ya da "DOĞRULANAMADI" (`ok: false`);
API'ye 3 deneme yapılır, olmazsa `failed` + "ACİL DUR GÖNDERİLEMEDİ, robotu elle durdur". (b) Bir araç çalışırken
`ToolExecutor.request_stop()` (başka iş parçacığı, CLI'da **Ctrl-C**) acil durdu hemen gönderir. (c) Bir araç
sonucu `estopped` ise (LLM durdurdu, bir insan butona bastı, iş `cancelled_by: estop` ile bitti) **koşu
hemen biter**: modele ikinci tur verilmez, aynı yanıttaki kalan çağrılar yürütülmez ("acil dur: çağrı
yürütülmedi") ve kullanıcıya modelin önceden yazdığı metin değil "Acil dur etkin ..." döner. (d) Acil dur
verdiren yürütücü, insan kilidi açsa bile bir daha hareket vermez; yeni koşu yeni yürütücüyle başlar.

**`run_plugin` (eklentiler; giriş noktası `robot_plugins.invoke.run_plugin`, bkz. "Eklentiler: dışarıdan
çağırma sözleşmesi").** Model yalnızca **izin listesindeki** `eklenti.araç` çiftlerini çağırabilir
(`PluginBridge`; varsayılan `find_object.find_object`, `scan_extent.scan_extent`,
`follow_color.follow_color`; ek izin yalnızca `--allow-plugin eklenti.araç` ya da kodla: **sonradan
yüklenen bir eklenti listeye eklenmedikçe modelce çağrılamaz**). Araç yalnızca eklenti gerçekten kuruluysa ve
manifesti sağlamsa (bilinen `motion`, desteklenen şema) görünür; açıklaması tam olarak bu araçları sayar.
Gerçek sınıf eklentinin kendi `motion` alanından gelir:

| eklenti `motion` | risk | kurallar |
|---|---|---|
| `none` (yalnızca bakar) | `read` | **doğrudan çalışır**: onay yok, kota harcamaz; yalnızca acil dur kilidinde reddedilir |
| `camera` (kamerayı çevirir) | `motion` | onay, hareket kotası, robot durumu denetimi (`estopped`/`busy`), kuru çalıştırma |
| `arm` (kolu oynatır) | `long` | yukarıdakilerin hepsi **+ ilk hedef için güvenli kutu ön denetimi** (`color` verilen araçlarda, ör. `follow_color`: gerçek nesne konumu + `height_m` → robotun bildirdiği kutu; dışındaysa `refused`, eklenti başlamaz). Hedefi önceden bilinemeyen `arm` araçlarında yalnızca robotun kendi kutusu (her iş için sunucuda) geçerlidir; bu `data.zone`'da yazılır |

Argümanlar **eklentinin kendi şemasıyla** doğrulanır (`robot_plugins.schema`), ayrıca NaN/inf ve şemada olmayan
alanlar reddedilir; hatalı çağrı `rejected` olur, süreç hiç başlamaz. Eklenti `robot_url` olarak **gerçek**
Robot API adresini alır (`'fake'` hiç kullanılmaz; hareket ettirenler robot adresi bilinmiyorsa reddedilir) ve
`frames` (varsayılan `ros`, canlı kamera; CLI `--frames`). Acil dur önceliği eklentilerde de geçerlidir:
yürütücünün `stop_event`'i eklentiye verilir, `request_stop()` (Ctrl-C) hem robota acil dur gönderir hem
eklentiyi durdurur; eklenti `code: estop` döner ya da çalışırken robot kilitlenirse sonuç `estopped` olur ve
**koşu biter**. `timeout`/`stopped`/`interrupted` ile biten bir `arm` eklentisinin robotta bıraktığı iş
iptal edilir (`data.job_cleanup`). Sonuç `data.plugin_data`'dadır (6000 karakteri aşarsa kırpılır,
`truncated: true`). `reset` bilerek yine **yok** ve eklentilerin de yok.

**Olay günlüğü:** her çağrı, reddedilenler dahil `agent_tool_call` olayıdır (`ROBOT_DB_URL` varsa
`error_reports`'a da düşer: `status` ok/dry_run değilse).

**LLM'ler** (`robot_agent.llm`): `ScriptedLLM` (testler/CI, anahtarsız), `RuleLLM` (yerleşik Türkçe
ifade çözücü, `--offline`; *LLM değildir*), `AnthropicLLM` (isteğe bağlı; yalnızca `ANTHROPIC_API_KEY`
ortam değişkeni varsa açılır, yoksa katman kapalıdır; model `ROBOT_AGENT_MODEL`, varsayılan
`claude-sonnet-5-5`; stdlib HTTP, anahtar yalnızca başlıkta, hata mesajlarında/günlükte geçmez), `OpenAILLM`
(isteğe bağlı, Chat Completions: `POST {taban}/chat/completions`, `Authorization: Bearer`; `--provider openai`).
**Model adı ve uç adresi koda gömülü değildir:** `ROBOT_AGENT_OPENAI_MODEL` / `--model` ve
`ROBOT_AGENT_OPENAI_BASE_URL` / `--base-url` zorunlu (biri eksikse çıkış `2`); adres `https` olmalı, `http`
yalnızca yerel makinede (anahtar ağda düz metin gitmesin). Araç şeması aynıdır, yalnızca çevrilir.

**Anahtar hijyeni** (`robot_agent.redact`): kaydedilen anahtarlar ve genel anahtar kalıpları (`sk-…`,
`Bearer …`, `x-api-key: …`; sağlayıcılar kötü anahtarı 401 gövdesinde yankılar) hata metinlerinden, CLI
çıktısından, `logging` kayıtlarından, olay günlüğünden, modele giden araç sonuçlarından ve robota giden
araç argümanlarından `[GİZLİ]` ile silinir. İstek başlıkları hiçbir yerde loglanmaz.
**İstem enjeksiyonu:** araç çıktısındaki metin komut değildir; hareketin tek yolu `ToolExecutor` → Robot API,
serbest kod çalıştırma yoktur (kaynak taraması + davranış testi). `emergency_stop` argümanı bozuk/uzun olsa
bile **reddedilmez**, robot durur.
**Robot API girdisi:** JSON gövdelerindeki `NaN`/`Infinity`/`1e999` reddedilir (`400`).

**CLI:** `robot_agent [--offline] [--yes] [--dry-run] [--max-motions N] [--url http://127.0.0.1:8080] "yeşil nesnenin üstüne git"`.
Çıkış: `0` tamam, `1` bir araç reddedildi/başarısız, `2` kurulum sorunu (anahtar yok, kötü URL),
`3` koşu acil durla bitti, `130` Ctrl-C (acil dur gönderilir). Etkileşimli terminalde hareketten önce `[e/H]`
sorar; terminal yoksa `--yes` verilmedikçe hareket **verilmez**.
Doğrulama: `run_selftest gazebo-agent` (`ros2 run robot_viz agent_check`); tüm zincir (Hub → Robot API →
algı → hareket, canlı kamera eklentisi ve ajan aynı kolda): `run_selftest gazebo-integration`
(`ros2 run robot_viz integration_check`; `apps/control_hub` ve `plugins/` imaja bağlanır).

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
| `agent_tool_call` | robot_agent | `tool`, `arguments`, `risk` (`read|motion|long|safety`), `status` (`ok|dry_run|rejected|refused|failed|cancelled|estopped`), `ok`, `message`, `dry_run`, `job_id`, `duration_s` — **her** çağrı, reddedilenler dahil |
| `objects_changed` | perception | `objects`: `[{label, index, position, size, pixels}]` — görünen nesne kümesi değişince (her karede DEĞİL) |
| `skill_result` | cli_moveit, web_view (Skills) | `skill`, `args`, `ok`, `message`, `code_name` |
| `scenario_result` | cli_moveit, web_view (`demo_scenario`) | `name`, `ok`, `message`, `steps[]` (`step`, `say`, `ok`, `message`, `target`, `measured`, `error_cm`) |
| `selftest_check` | selftest | `name`, `ok`, `detail` |
| `error` | herhangi | `component`, `code`, `message` |

`error_reports` satırı otomatik oluşan olaylar: `motion_result` (`ok=false`), `command_rejected`, `estop` (her acil dur), `agent_tool_call` (`status` ok/dry_run değilse),
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
| `permissions` | `camera:read` (kare okuma), `camera:pan` (kamerayı döndürme: `ctx.camera.pan`), `robot:jobs` (Robot API v1 iş kuyruğu: `state`/`submit_job`/`job`/`cancel`). **`robot:stop` istenmez ve elinden alınamaz**: her pakette `ctx.robot.stop()` çalışır; manifestte `deny`/bilinmeyen izin geçersizdir. Paketin `reset` yetkisi yoktur (kilidi yalnızca insan açar) |
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
`robot.cancel(id)` (`robot:jobs`), `robot.stop(neden)` (her zaman), `camera.pan.limits/move_to(yaw)/frame()` (`camera:pan`; `robot_vision.scan.Panner` arayüzü). Hareket **yalnızca** Robot API v1
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
| `scan_extent` (v0.2) | `camera:read`, `camera:pan` | `scan_extent(yaw_min_deg, yaw_max_deg, steps)` → kamerayı döndürerek tarar, görüntüleri birleştirir: nesne listesi + 3B ölçüler. Döndürülebilir kamera yoksa tek bakışa düşer ve `swept: false` + `note` ile söyler |

**Robot API bağımlılığı (dürüst not):** `/api/jobs` ve `/api/stop` bu dalın tabanında (`main`) henüz yoktur;
Robot sohbetinin açık PR'larındadır (#14 Aşama 6a, #15 Aşama 6b). `HttpRobotApi` bu belgedeki v1
sözleşmesine göre yazıldı ve **bu sözleşmeyi uygulayan bir test sunucusuyla** doğrulandı; gerçek `web_view`
ile uçtan uca denenmedi. Camera platformunu (PR-V2) süren bir paket için `platform:rates` izni henüz tanımlı
değildir (ROS bağımlı; ileride).

## Tarama, ONNX dedektörü ve LLM araç katmanı (PR-V4)
**Pan taraması** (`robot_vision.scan`, ROS'suz): `scan(panner, ScanConfig(yaw_min, yaw_max, steps, merge_distance), ...)`.
`Panner` = dönebilen kamera (`limits`, `move_to(yaw)`, `frame()`; kare, o açıdaki kamera pozunu taşır). Her görüntü normal
dedektör + `measure()`'dan geçer; birden çok görüntüde görülen nesneler **etiket + dünya konumu** (`merge_distance`, 5 cm)
ile birleştirilir; görüntü kenarında kesik görünen nesne, bütün göründüğü bir görüntü varsa onun ölçümünü **ezmez**
(`partial` yalnızca hiçbir görüntüde bütün görünmediyse). Sınırlar: kamera nesnelerin üstünü görmeli (V1 varsayımı);
aynı renkte `merge_distance`'tan yakın iki nesne tek sayılır. Doğrulama: `python3 -m robot_vision.scan_validate`
(halka dünyası, 6 nesne, tek görüntü en çok 3 nesne görür; `README.md` "Tarama").

**`OnnxDetector`** (`robot_vision.onnx_detector`, isteğe bağlı `onnxruntime`): `Detector` arayüzünü kullanıcının kendi ONNX
modeliyle sağlar; **depoda model ağırlığı yok**. Girdi `[1,3,H,W]` float32 RGB 0-1 (letterbox), çıktı çözücüleri:
`xyxy` (`[N,6]`), `yolov8` (`[1,4+C,N]`/`[1,N,4+C]`, NMS içeride), `yolox` (`[1,N,5+C]`, nesnelik × sınıf skoru).
Kutu → piksel kümesi: kutunun içinde derinliği olan ve dünya çerçevesinde zeminin (`ground_margin`, 1.2 cm) üstündeki
pikseller; böylece `measure()` zemini değil nesneyi ölçer. Etiketsiz sınıf atlanır. **Lisans:** `docs/vision-model-licenses.md`
(Ultralytics = AGPL-3.0/Enterprise; Apache-2.0 alternatifleri YOLOX, RT-DETR).

**Canlı kamera:** `PluginManager(..., frames='ros')` / `python3 -m robot_plugins --frames ros ...`
(`robot_vision_ros.RosFrameSource`; ROS 2 ortamı gerekir, `/camera/{image,depth_image,camera_info}` abone olunur,
kamera pozu `camera=` ile seçilir: `fixed` = `workspace.sdf`'teki sabit kamera; `platform` = hareketli kamera platformu
(`tracking.sdf`), her karenin pozu **kendi damgasındaki** `/platform/joint_states` (doğrusal ara değer) ve platform ileri
kinematiğinden hesaplanır (`robot_vision.moving_camera`; `tracking_node` ile aynı kinematik). `auto` (varsayılan, `--frames
ros`): `/platform/joint_states` yayımlanıyorsa `platform`, yoksa `fixed`; ilk saniye karar bekler ki ilk kare yanlış pozla
yerleşmesin. `--frames ros:fixed` / `ros:platform` açıkça seçer. Kare, damgasından yeni eklem durumu gelene kadar bekler
(poz **tahmin edilmez/ekstrapole edilmez**); eklem durumunda 0.25 s'den büyük boşluk varsa ya da hiç eklem durumu gelmezse
kare atılır. Eski davranış (platform hareket edince de sabit poz) nesne konumunu metrelerce kaydırıyordu (H6).
Gerçek Gazebo karesiyle `find_object` (sabit kamera): kutu/silindir/küre merkezleri dünya dosyasına ≤ 0.15 cm.
Hareketli platformda doğrulama: `ros2 run robot_vision_ros perception_check` (`tracking.launch.py manual:=true` çalışırken;
kırmızı top sabit, platform 6 pozda; dünya konumu hatası ve pozlar arası kayma ≤ 1 cm ister, karşılaştırma için sabit-poz
hatasını da yazar; CI selftest'i DEĞİL, elle çalıştırılır).

### LLM araç katmanıyla (Robot sohbetinin Aşama 7'si) birleşme noktası
Araçların **tek kaynağı** paket manifestleridir: `PluginManager.manifest(ad).describe()['tools']` listesi (`name`,
`description`, `input_schema`; JSON-Schema alt kümesi) doğrudan bir LLM'in araç tanımı olarak verilebilir. Çağrı:
`PluginManager.call(paket, araç, argümanlar)` her zaman bir `dict` döner: `{"ok": true, "data": {...}}` ya da
`{"ok": false, "code", "error"}` (`bad_arguments`, `not_running`, `permission_denied`, `stopped`, `timeout`,
`robot_423`, `exception`); `error` insan/LLM okur, istisna fırlatmaz. Önerilen akış: katman açılışında
`list()` ile `running` paketlerin araçlarını toplar (`<paket>.<araç>` adlandırması), kullanıcı bir paketi Hub
panelinden (yönetim API) açıp kapattıkça katalog değişir; acil dur (`/api/plugins/estop` ya da `emergency_stop()`)
her zaman tüm paketleri durdurur ve `robot_423` alan araç çağrısı **yeniden denenmemelidir**. Hareket yapan araçlar
(`follow_color`, `motion == 'arm'`) Robot API v1 iş kuyruğundan geçer; bu, sunucu tarafında güvenli kutuyu, hız tavanını ve
acil durumu uygular, ama **ajanın kendi kuralları da aynen geçerlidir**: onay (hareket eden araç kullanıcı onayı olmadan
çalışmaz), güvenli kutu denetimi ve acil dur davranışı, eklenti araçları için de atlanamaz. Eklenti tarafı bunların
YERİNE geçmez, yalnızca ikinci katmandır. Araç açıklamaları "HAREKET ETTİRİR / ETTİRMEZ" ifadesini taşır ki LLM ona
göre seçsin; asıl sınıf `catalog()`'daki `motion` alanıdır.

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


## Eklentiler: dışarıdan çağırma sözleşmesi (PR-V5, `robot_plugins.invoke`)

Ajan katmanı (Robot_3), betikler ve CLI bir eklentiyi **tek bir Python girişinden** çağırır; yönetici,
alt süreç protokolü ya da `PluginContext` ayrıntılarına bakmaz.

```python
from robot_plugins.invoke import run_plugin, catalog

catalog()                                   # tüm araçlar: LLM'e verilecek açıklama + şema + motion
r = run_plugin('find_object', 'find_object', {'color': 'red'},
               robot_url='http://127.0.0.1:8080', frames='ros')
```

Aynısı komut satırından: `python -m robot_plugins [--robot URL] [--frames ros] call <eklenti> [araç]
--args '{...}' [--params '{...}'] [--timeout S]` (JSON basar; çıkış kodu 0 = `ok`, 1 = hata, 2 = JSON bozuk) ve
`python -m robot_plugins catalog`.

### `run_plugin(plugin, tool=None, args=None, *, params, robot_url='fake', frames='none', panner='none', timeout_s=None, frame_wait_s=5.0, grace_s=3.0, stop_event=None)`
Eklentiyi kendi alt sürecinde başlatır, **tek** araç çağrısı yapar ve süreci her durumda (zaman aşımı,
Ctrl-C, hata) kapatır. **İstisna atmaz**; her zaman aynı anahtarlarla bir `dict` döner (`api_version: 1`):

| anahtar | anlam |
|---|---|
| `ok` | çağrı gitti **ve** eklenti kendi içinde `ok: false` demedi (tek seviye; ayrıca bakmaya gerek yok) |
| `plugin`, `tool` | çağrılan; `tool` verilmediyse ve eklentide tek araç varsa o seçilir |
| `motion` | aracın neyi hareket ettirebildiği (aşağıda); bulunamayan araçta `null` |
| `data` | eklentinin kendi sonucu (nesne listesi, iş sayısı, `reason` …); hata olsa da varsa doludur |
| `error`, `code` | `ok` false iken dolu; `code` aşağıdaki listeden |
| `warning` | `null` ya da insan okunur uyarı (benzetim robotu, iptal doğrulanamadı …) |
| `robot`, `frames`, `duration_s` | hangi robot / kare kaynağıyla, kaç saniyede |

`code` değerleri: `unknown_plugin`, `unknown_tool`, `bad_parameters`, `bad_arguments` (şemaya uymayan
`args`), `bad_robot_url`, `start_failed`, `not_running`, `permission_denied`, `timeout`, `stopped`
(`stop_event` ya da eklenti durduruldu), `interrupted` (Ctrl-C), **`estop`**, `robot_<HTTP durumu>`,
`plugin_error` (eklenti `ok: false` döndü; ayrıntı `data`'da, ör. `reason`), `exception`.

* **`estop`**: robotun acil durdurması kilitli (HTTP 423 ya da eklenti `reason: estop`). **Yeniden
  deneme.** Eklentilerin reset yetkisi yoktur; kilidi yalnızca bir insan açar.
* **Zaman aşımı**: `timeout_s` verilmezse aracın `package.yaml`'daki `timeout_s` değeri (1-900 s) geçerlidir.
  Süre dolunca eklentiye önce durdurma belirteci verilir (`follow_color` çalışan robot işini iptal eder),
  `grace_s` sonra süreç öldürülür. `stop_event.set()` aynı yolu işletir; acil dur işleyicisi bunu
  çağırabilir. `run_plugin` robotun **acil dur**unu kendisi göndermez (robotu kilitler; çağıranın kararı).
* **`motion == 'arm'` araçlarda** `timeout` / `stopped` / `interrupted` / `exception` sonuçlarına `warning`
  eklenir: iş iptal edilmiş olmayabilir, `GET /api/state` ile bak, gerekirse acil dur gönder.
* **`robot_url`**: `'fake'` (bellekte benzetim; **hiçbir şey hareket etmez**, `motion == 'arm'` sonuçlarına bu
  `warning` olarak yazılır) ya da yalnızca loopback (`127.0.0.1`, `localhost`) bir Robot API adresi
  (API kimliksizdir; başka adres `bad_robot_url`).
* **İlk kare**: canlı kamerada (`frames='ros'`) süreç başladığında henüz kare yoktur; `latest_frame()`
  `frame_wait_s` (varsayılan 5 s) kadar ilk kareyi bekler. Kare yine gelmezse `plugin_error`
  (`kamera karesi yok`) döner; yeşil bir `ok` asla verilmez.
* **Maliyet**: her çağrı bir süreç başlatır (≈1-3 s, bunun içinde ilk kare). Çok sayıda çağrı için tek bir
  `PluginManager` tut; `run_plugin` onun üstüne kurulu tek-atımlık sarmalayıcıdır.

### Hareket sınıfı (`package.yaml` → `tools[].motion`, **zorunlu**)
| `motion` | anlam | gereken izin | araçlar |
|---|---|---|---|
| `none` | yalnızca **bakar**; hiçbir şeyi hareket ettirmez | `camera:read` | `find_object` |
| `camera` | yalnızca **kamerayı** (pan) çevirebilir, kolu oynatmaz | `camera:pan` | `scan_extent` |
| `arm` | **robot kolunu** Robot API v1 işleriyle hareket ettirir | `robot:jobs` | `follow_color` |

`catalog()` her araç için `motion`, `timeout_s`, `input_schema`, `description` ve izinleri verir. Ajan katmanı
`none` araçları onaysız, `arm` araçları kendi onay/güvenlik kurallarıyla çalıştırmalıdır. `timeout_s` de
manifestte zorunlu değildir (varsayılan 60 s) ama hareket eden araçlarda açık yazılmalıdır.

### Eklenti başına
* `find_object(color: red|green|blue|any, …)` → `data.objects[]` (renk, şekil, merkez, boyut, kenar noktaları,
  tutuş adayları); kare yoksa `plugin_error`. Kameradan çıkan her sayı **tahmindir** (derinlik gürültüsü).
* `scan_extent` → kamerayı süpürüp görünen her nesnenin kapladığı alanı verir. **Yalnızca kamerayı
  çevirir.** ROS tarafında panner yoktur: `frames='ros'` ile tek görünüme düşer (`data.swept: false`);
  gerçek süpürme `panner='synthetic'` ile (benzetim) denenmiştir.
* `follow_color(color, height_m, duration_s, min_move_m)` → `data.reason`: `duration` (başarılı), `stopped`,
  `not_found`, `refused`, `estop`; `data.jobs` kaç `above <renk> <yükseklik>` işi gönderildiği. Hareket
  yalnızca Robot API işlerinden geçer (güvenli kutu, hız tavanı, acil dur geçerli).

## Kamera platformu: elle kontrol (`robot_vision_ros` `camera_manual`, mantık `robot_vision.manual`)
Mevcut kamera platformunu (`tracking.sdf`: yaw / slide / pitch) pan-tilt-konum olarak elle komutlar. Takip düğümünün
kullandığı aynı `/platform/*_joint/cmd_vel` hız komutlarına yazar; yeni eklem ya da düğüm eklemez.
Başlat: `ros2 run robot_vision_ros camera_manual` ya da `ros2 launch robot_vision_ros tracking.launch.py manual:=true`
(varsayılan `false`: mevcut selftest'ler değişmez). **Robot koluna dokunmaz**; HTTP/Hub arayüzü sonraki iştir.

| Konu | Arayüz |
|---|---|
| Komut | `/camera/manual/command` (`std_msgs/String`, JSON). `{"op":"goto","pan":rad,"tilt":rad,"slide":m,"speed":0-1}` (eksenlerin herhangi bir alt kümesi; `speed` elle hız tavanının oranı) · `{"op":"jog","pan_rate":..,"tilt_rate":..,"slide_rate":..,"ttl_s":0.5}` · `{"op":"stop"}` |
| Eksenler | `pan` = yaw (rad), `tilt` = pitch (rad; **artı = kamera AŞAĞI**), `slide` = platformun ileri konumu (m). Sınırlar tracking ile aynı: pan ±1.2, tilt -0.6..1.2, slide 0-2.5 |
| Yanıt | `/camera/manual/reply`: komut başına bir JSON `{accepted, op, code, message}` |
| Durum | `/camera/manual/status` (5 Hz): `state` (`idle`/`goto`/`jog`), `q` {pan, slide, tilt} (taze eklem durumu yoksa `null`), `last` (son yanıt; hareket bitince `done`: `reached`/`jog_expired`/`timeout`/`stopped`/`no_state`), `tracking_busy` |

Güvenlik kuralları (hepsi testli; ihlalde komut **bütünüyle reddedilir**, hareket başlamaz, çalışan hareket olduğu gibi kalır):
* NaN/Infinity (JSON `NaN`/`Infinity` literalleri dahil), sayı olmayan değer (bool, metin, null), bilinmeyen anahtar/op,
  geçersiz JSON → `non_finite` / `bad_command` / `bad_json`.
* `goto` hedefi eklem sınırı dışında → `out_of_limits` (kırpılmaz). `jog` hızı elle tavanın üstünde → `rate_too_high`.
* Elle hız tavanı = platform hız sınırının **%50'si** (pan 0.75 rad/s, tilt 0.5 rad/s, slide 0.4 m/s); hız saniyede en
  çok tavanın 4 katı kadar değişir (basamak yok). `stop` ise anında sıfırdır.
* Sınıra yaklaşırken (pan/tilt 0.15 rad, slide 0.20 m) dışa doğru hız kısılır, sınırda sıfırdır.
* `jog` dead-man: `ttl_s` (varsayılan 0.5 s, en çok 2 s) dolunca durur; sürmesi için komutu tekrar gönder. `goto` 30 s'de
  zaman aşımına uğrar. Taze `/platform/joint_states` (0.5 s) yoksa komut reddedilir (`no_state`), çalışan hareket durur.
* Sahiplik: `/tracking/mode` `idle` değilse ya da `/tracking/status` akıyorsa komut `busy` ile reddedilir; takip devralırsa
  elle hareket **sıfır yayımlanmadan** bırakılır (sıfır takibe karşı savaşırdı). Düğüm boştayken hiçbir şey yayımlamaz.

## Jog (J2): `POST /api/jog`, `GET/PUT /api/frames` — Robot API v1'e EKLEMELİ

Hesap J1'in (`docs/JOG.md`); burası API, kiralama ve dead-man. Uygulama: `robot_core.jogapi` (ROS'suz),
ROS tarafı `robot_viz.jog_ros` (kontrolcü konusuna kısa setpoint). Yalnızca `web_view commands:=true` ile.
**Yazılım dead-man/acil dur donanım acil durdurmanın yerine geçmez.**

| Uç | Anlam |
|---|---|
| `POST /api/jog` | `{"lease": "<8-64 karakter [A-Za-z0-9_-]>", "frame": "World"\|"Tool"\|<ad>, "twist": [vx vy vz wx wy wz] \| "increment": {"axis": 0-5, "amount": ±0.1\|1\|10}, "ttl_ms": 50-500}`. `twist` SI (m/s, rad/s), sunucu kırpar; `increment` mm (eksen 0-2) ya da ° (3-5, W P R **etrafında**). `ttl_ms: 0` = kiralamayı bırak. Komutun kendisi heartbeat'tir |
| `GET /api/jog` | durum (`active`, `mode`, `frame`, `status`, `messages`, `moving`, `hold_enabled`, hız tavanı) + `tcp` |
| `GET /api/frames` | `{"builtin": ["World","Tool"], "max_frames": 10, "frames": [{name, points, pose, wpr_deg, warnings}]}` |
| `PUT /api/frames` | `{"frames": [{"name", "points": {"origin","x","xy"}}]}` **tüm listeyi** değiştirir; önce doğrulanır, atomik yazılır (`400` + neden, dosya bozulmaz); jog etkinken `409` |
| `/api/state` | eklemeli: `tcp` (`position` m, `wpr_deg`, `warning`) ve `robot.jog` (jog durumu ya da `null`); `robot.state` yeni değer `jogging` |
| `POST /api/jobs` | yeni beyaz liste komutu `ready` (güvenli kutunun içinde, bilek tekilliğinden uzak başlangıç pozu) |

**Dead-man SUNUCUDA:** TTL varsayılan 250 ms, en çok 500 ms (istemci daha fazlasını isteyebilir, yanıtta `ttl_clamped`);
TTL dolunca komut hızı en çok 100 ms'de (`stop_ramp_ms`) sıfırlanır ve setpoint akışı durur. Zincir: istemci
anında < sunucu TTL 250 ms < ESP32 watchdog 300 ms (profil 200-500).
**Tek kiralama:** ikinci `lease` `409`; kiralama son komuttan 2 s sonra serbest kalır ya da `ttl_ms: 0` ile bırakılır.
**Kodlar:** `400` biçim, `403` basılı-tut kapalı, `409` başka kiralama / iş çalışıyor / artım sürüyor, `423` kilitli acil dur,
`503` eklem durumu yok. `423` yalnızca `POST /api/jog` için; `/api/stop` jog trafiğinden bağımsızdır ve her zaman önceliklidir.
**Karşılıklı dışlama:** jog (topic) ve işler (action) aynı trajectory controller'a yazar: jog sürerken `POST /api/jobs` `409`,
iş sürerken jog `409`; araya iş girerse jog bir sonraki tickte kapanır.
**Varsayılanlar dar:** hız tavanın %20'si (`jog_speed_scale`), `mode: real` için ayrıca ≤ %10 (`jog_speed_scale_real`);
artımlı mod varsayılan; basılı-tut (`twist`) `mode: real`'de ve loopback dışı istemcilerde (telefon) KAPALI
(`jog_hold: auto|on|off`, `jog_hold_remote`). Her yeni setpoint ≤ 100 ms ufukludur: düğüm ölürse kol kendiliğinden durur.
`frames.yaml` Robot API'nin veri dizinindedir (`data_dir` / `ROBOT_DATA_DIR`, Docker'da volume); Hub dosyaya yazmaz.

## Nokta deposu (O2): `/api/waypoints`, `goto_waypoint` — Robot API v1'e EKLEMELİ

Kayıtlı robot pozları (`robot_core.waypoints`, O1). Dosya `waypoints.yaml`, Robot API'nin veri dizininde
(`data_dir` / `ROBOT_DATA_DIR`); Hub dosyaya yazmaz, yalnızca bu uçları kullanır. Uçlar `commands:=true` ister;
yazan uçlar (PUT/DELETE/POST) diğer POST'larla aynı korumaya sahiptir (host, `Content-Type: application/json`,
yabancı Origin / `Sec-Fetch-Site` reddi; gövde ≤ 1 KiB).

| Uç | Gövde | Yanıt |
|---|---|---|
| `GET /api/waypoints` | – | `{waypoints_version, joint_names, max_waypoints, frames: ["World", …], waypoints: [{name, joints, tcp: {frame, position, quaternion_xyzw}, created}]}` |
| `GET /api/waypoints/<ad>` | – | tek nokta; yoksa `404` |
| `PUT /api/waypoints/<ad>` | `{"joints": [6 rad], "frame": "World"}` | oluşturur (`201`) ya da değiştirir (`200`, `replaced: true`) |
| `PUT /api/waypoints/<ad>` | `{"rename_to": "yeni"}` | yeniden adlandırır; yeni ad varsa `409` |
| `DELETE /api/waypoints/<ad>` | – | `{ok, deleted, count}` |
| `POST /api/waypoints/record` | `{"name", "frame"?, "overwrite"?: false}` | **ölçülen** eklemleri kaydeder (`201`/`200`); ad varsa ve `overwrite` yoksa `409` |

- **Kaydedilirken reddedilir (`400`, dosyaya yazılmaz):** eklem limiti dışı, TCP güvenli kutunun dışında,
  sonlu olmayan/yanlış sayıda değer, geçersiz ad, bilinmeyen çerçeve. `record` ek olarak bir iş ya da jog
  kiralaması sürerken `409` (ölçülen poz hareket halinde olabilir), eklem durumu yoksa `503` verir. `record` adı
  ayrılmıştır. En çok `max_waypoints` nokta (`409`).
- **Düzenleme hareket ettirmez:** PUT/DELETE/record acil dur kilitliyken de çalışır (`423` yalnızca hareket uçlarında).
- **Çerçeve koruması:** bir noktanın dayandığı kullanıcı çerçevesini silen ya da değiştiren `PUT /api/frames`
  `409` döner (aksi halde nokta dosyası açılışta yüklenemezdi). Önce noktaları silin ya da `World`'de yeniden kaydedin.
- **Dosya bozuksa** Robot API yine açılır; nokta uçları `503` (neden metinde), `goto_waypoint` başarısız olur. Hiçbir şey
  sessizce onarılmaz.
- **Noktaya gidiş:** `POST /api/jobs {"command": "goto_waypoint <ad>"}` (komut beyaz listede; `GET /api/state`
  `commands.allowed` listesine **eklendi**). Eklem hedefi **MoveIt'e** verilir: planlama ve çarpışma denetimi vardır,
  denetimsiz eklem hedefi YOKTUR. Nokta, gidişten hemen önce eklem limitlerine ve güvenli kutuya karşı **yeniden**
  denetlenir (kutu/profil sonradan daralmış olabilir). Acil dur `423`, iş/jog dışlaması `409` (`state: jogging`),
  iptal ve zaman aşımı diğer işlerle aynıdır; MoveIt reddederse `failed` + neden, "Robot hareket etmedi.".
- **Bilinen açık (F4):** güvenli kutu yalnız HEDEF noktada denetlenir; eklem uzayı yolunda iki kutu-içi nokta arasında TCP kutudan çıkabilir (`go_to_position` ve `go_above` için de geçerli). Bu belge kutunun yol boyunca garanti edildiğini SÖYLEMEZ. Yalnız simülasyon için kabul edilir, `mode: real` bu kapatılmadan açılmaz. Kutu yalnız uç noktayı (TCP) sınırlar, dirsek ve bilek kutu dışına çıkabilir.
- Durum belgesi (`/api/state`) değişmedi.
