# Control Hub mimarisi (`apps/control_hub`): salt-okunur denetim

Tarih: 2026-11-25 · Denetlenen: `main` `3557e1a` · Hazırlayan: Müdür (Robot_9). **Bu belge kod değiştirmez.**
Rakamlar komutla ölçüldü (yöntem sonda). Ölçüm olmayan her şey "yorum" diye işaretlidir.

**Çalıştırılmadı:** `pytest`, `ruff`, node testleri, tarayıcı testleri bu denetimde koşturulmadı; yani "testler yeşil"
diye bir iddia yoktur. Aşağıdaki test boşlukları yalnız dosya/uç nokta adlarının test dosyalarında geçip geçmediğine bakar,
kapsama yüzdesi DEĞİLDİR.

## 1. Genel görünüm

| Ölçü | Değer |
|---|---|
| Python (`control_hub/`) | 64 dosya, 11 082 satır, 101 sınıf, 770 işlev/yöntem |
| Web JS (vendor hariç) | 33 dosya (JS+CSS+HTML), 5 280 satır; yalnız `.js` 4 689 satır |
| Vendor (three.js, yerel kopya) | 3 dosya, ~2,2 MB (`web/vendor/three/`), denetim dışı |
| Test | 57 Python dosyası, 9 522 satır, 635 test işlevi; 16 node betiği (`tests/js/`) |
| Rota | 69 dekoratörlü rota (`server/*.py`) |

Üç katman, bir kabuk:

```
 tarayıcı / telefon                          masaüstü (.exe)
 ┌──────────────────────┐                   ┌─────────────────────┐
 │ web/  (düz JS, derleme│                   │ desktop/ (Tk pencere,│
 │  yok, window.* modül) │                   │  ayar, güvenlik duvarı)
 └──────────┬───────────┘                   └──────────┬──────────┘
            │ HTTP + WS (CSP, Host/Origin/CSRF)         │ başlatır
 ┌──────────▼───────────┐   ┌──────────────┐   ┌───────▼──────┐
 │ server/ (FastAPI,     │──▶│ hub_core/    │◀──│ cli.py        │
 │  rota = ince adaptör) │   │ (çekirdek,   │   │ (birleştirme) │
 └──────────┬───────────┘   │  FastAPI YOK)│   └───────────────┘
            │               └───────┬──────┘
   agent/ (ajan paneli)   hw/ (COM↔TCP, Tezgâh)   robot_core (kütüphane), Robot API (HTTP)
```

**Doğrulanan katman kuralı:** `hub_core/`, `hw/` ve `agent/` içinde `fastapi`/`pydantic`/`starlette`/`uvicorn`
geçmiyor (grep). Web çerçevesi yalnız `server/`, `cli.py` ve `desktop/service.py`'de.

## 2. Katmanlar ve modül sorumlulukları

Paket başına toplam: `hub_core` 29 dosya / 6 030 satır / 60 sınıf / 401 işlev · `server` 14 / 1 824 / 12 / 161 ·
`agent` 6 / 1 074 / 20 / 79 · `desktop` 8 / 1 071 / 5 / 66 · `hw` 3 / 619 / 4 / 41 · kök (`cli`, `paths`) 4 / 464 / 0 / 22.

### 2.1 `hub_core/` (çekirdek; çerçevesiz, çoğu saf ve testli)

| Modül | Tek sorumluluk | Satır |
|---|---|---|
| `hub.py` | Çekirdek parçaları birleştirir: arka uç, koşucu, erişim, günlük, **ACİL DUR kilidi** | 178 |
| `backend.py` | `RobotBackend` Protocol'ü (execute / emergency_stop / reset / snapshot / close) | 56 |
| `fake_backend.py`, `fake_jog.py`, `fake_model.py` | ROS'suz sahte robot (uç nokta oynatır; jog/çerçeve; görünüm modeli) | 177 / 356 / 99 |
| `sim_backend.py` + `sim_plan.py`, `sim_jobs.py`, `sim_assets.py` | Gerçek kinematikli SİM: arka uç, planlama (saf), iş modeli (saf), profil yolu bulma | 399 / 291 / 63 / 62 |
| `robot_api_backend.py` | Hub'ı robotun HTTP API v1'ine bağlar | 278 |
| `access.py` | `open/pin/locked` erişim kipi, PIN, oturum, deneme sınırı | 140 |
| `runner.py`, `scenarios.py`, `commands.py`, `events.py` | Senaryo koşucusu; senaryo/arayüz yapılandırması; komut beyaz listesi; olay halka tamponu | 114 / 231 / 74 / 63 |
| `joint_hold.py` | Eklem basılı-tut jog (bırakınca/kopunca DURUR) | 130 |
| `program_store.py`, `program_runner.py`, `program_move.py` | Program modeli+doğrulama+dosya; sunucu tarafı koşucu; hareket istemcileri | 350 / 373 / 282 |
| `algo_gen.py` | Algoritma üreteçleri → program adımı (saf) | 268 |
| `parts_store.py`, `mesh_tools.py`, `meshes.py`, `step_convert.py`, `step_child.py` | Parça deposu; STL/OBJ ayrıştırma (saf); robot mesh bulma; STEP dönüşümü (alt süreç) | 275 / 302 / 100 / 177 / 267 |
| `profile_view.py`, `profile_fields.py`, `profile_rules.py` | Parametreler sayfası: oku→kopyada düzenle→doğrula→fark→taslak; alan kataloğu; kurallar (saf) | 340 / 376 / 185 |

### 2.2 `server/` (HTTP/WS adaptörü; mantık yok, ilke olarak)

| Modül | Sorumluluk | Satır |
|---|---|---|
| `app.py` | FastAPI kurulumu, **güvenlik katmanları** (Host beyaz listesi, Origin/CSRF, CSP, oturum çerezi), `/api/estop`, WS | 298 |
| `registry.py` | `server/*_routes.py` ve `web/pages/*.js` otomatik keşfi (`.exe`'de manifest) | 116 |
| `move_routes`, `sim_routes`, `jog_routes`, `program_routes`, `parts_routes`, `mesh_routes`, `params_routes`, `log_routes`, `algo_routes`, `bench_routes`, `agent_routes` | Her biri bir sayfa/özellik alanının uçları; `install(app, ctx)` | 48–252 |

### 2.3 Yan paketler

| Paket | Sorumluluk |
|---|---|
| `agent/` | Ajan sohbet paneli: `service` (robot_agent'ı sarar), `approvals` (insan onayı), `keystore` (anahtar asla günlüğe/yanıta girmez), `selftest`, `fakeport` |
| `hw/` | `bridge` (COM↔TCP, tek istemci), `bench_service` (Tezgâh: `tools/bench` mantığını sürer; gerçek robot değil) |
| `desktop/` | Tk penceresi (`gui`), `service` (çekirdek+sunucu arka plan iş parçacığı), `settings`, `firewall`, `launcher`, `winconsole` |
| kök | `cli.py` (komut satırı ve **birleştirme noktası**: her şeyi tembel içe aktarır), `paths.py` (kaynak/kullanıcı yolları, `.exe` dahil) |

### 2.4 `web/` (düz JS, derleme yok)

* Yükleme: `index.html` yalnız `shell.js`, `pages.js`, `app.js` yükler; geri kalan her şey `HubPages.loadScript`
  ile sayfa kaydından dinamik yüklenir (`pages.js:50-61,108`). Modül deseni: `window.<Ad>` (20 global: `HubPages`,
  `HubScene`, `SceneHub`, `SceneLogic`, `ShellLogic`, `JogLogic`, `ParamsLogic`, `PotLogic`, `LogLogic`, `BenchLogic`,
  `PotPanel`, `BenchPanel`, `JogPanel`, `AgentPanel`, `FrameWizard`, `SphereWizard`, `FrameTypes`, `ApproachMove`,
  `HubProgram`, `Scene3D`).
* **Mantık/görünüm ayrımı** çoğunda var ve iyi: `*_logic.js`, `shell.js`, `scene_logic.js`, `params_form.js`,
  `pot_logic.js`, `approach.js` DOM'suz, node ile test edilir. Sayfa dosyaları (`pages/*.js`) DOM'u çizer.
* Güvenlik: kodda `innerHTML`, `eval`, `insertAdjacentHTML`, `document.write` **yok** (grep; yalnız yorumlarda
  "innerHTML yok" geçer). `fetch(` yalnız 6 dosyada (`app.js`, `agent.js`, `pages/agent.js`, `pages/params.js`,
  `scene3d.js`, `scene_hub.js`).
* Sayfalar: robot, jog (Kontrol), program, parts, params, log, algo, bench, agent.

## 3. Bağımlılık yönü

Hedeflenen yön: `web → (HTTP) → server → hub_core`; `desktop`/`cli` en üstte; `hub_core` hiçbir üst katmanı bilmez.

Ölçülen paket arası içe aktarmalar (tembel/yerel dahil; 68 girintili içe aktarma var, yani birçok bağ çalışma anında
kurulur ve statik analizde görünmez):

| Kimden | Kime | Yorum |
|---|---|---|
| `server` | `hub_core`, `agent`, `hw`, `paths` | Beklenen yön. |
| `desktop` | `server`, `agent`, `hw`, `hub_core`, `cli`, `paths` | Beklenen (en üst katman). |
| `hub_core` | yalnız `paths` (`program_store.py:24`) | Neredeyse temiz; tek yukarı bağ. |
| `hw` | (hiçbiri) | Temiz. |
| `agent` | `hub_core`, **`desktop`** (`selftest.py:207-208`) | **Yukarı bağ**: `desktop` → `agent` zaten var (`settings.py:74`, `gui.py:259`, `service.py:96`), yani paket düzeyinde **döngü**; tembel içe aktarma gizliyor. |
| `server` içinde | `jog_routes` → `agent_routes.is_local_request` (`jog_routes.py:26`); `move_routes.read_json` iki dosyadan | Rota modülleri birbirinden bağımsız olmalı (`registry.py` ilkesi); bunlar ortak yardımcı eksikliği. |

Dış bağımlılıklar: `robot_core` (11 dosya: `paths`, `cli`, `agent/service`, `hub_core` altında 8), `robot_agent`
(`agent/`), `numpy` (2), `fastapi/pydantic/starlette/uvicorn` (yalnız §1'deki 3 yer). Arka uç yöntemleri
(`jog_command`, `frames_*`, `model`, `move`) `RobotBackend` Protocol'ünde **yok**; rotalar `getattr(hub.backend, ad, None)`
ile çağırır (`move_routes.py:43`, `jog_routes.py:33,45`, `mesh_routes.py:32`). Üç arka ucun (fake/sim/robot_api) aynı 5
jog/çerçeve yöntemi var (`jog_command`, `jog_status`, `frames_get/put/reach`) ama sözleşme yalnızca ortak adlardan ibaret.

## 4. Tekrarlar (ölçülen)

| # | Tekrar | Yerler | Not |
|---|---|---|---|
| T1 | Hata yanıtı `{'ok': False, 'error', 'message', **extra}` üreten `fail()` | `log_routes:113`, `program_routes:48`, `algo_routes:33`, `params_routes:29`, `jog_routes:47`, `move_routes:24` (`_fail`) ; ayrıca `sim_routes:36-37` elle `JSONResponse`, `app.py` `ApiError` | Gövde biçimi altısında satır satır aynı. |
| T2 | Arka uç hata yanıtı `_err(...)` | `fake_jog:37`, `joint_hold:49`, `sim_backend:189` | İmzalar farklı (kod/ tür alanı). |
| T3 | `read_json` | `move_routes:28`, `parts_routes:56` (iç işlev) | `program_routes`/`jog_routes` `move_routes`'tan içe aktarıyor. |
| T4 | Atomik dosya yazımı (`mkstemp`/`.tmp` + `os.replace`) | `program_store:266`, `parts_store:137,238`, `settings:110`, `profile_view:262` | Dört dosya, küçük farklarla (geçici ad, dizin). |
| T5 | ACİL DUR kilit denetimi + 423 yanıtı | `move_routes:50,55,79`, `sim_routes:36`, `jog_routes:79-80,96-98`, `program_routes:106`; arka uç içi: `sim_backend:160,193,249,262`, `fake_jog:106,317`, `fake_backend:73` | Tek giriş noktası yok; her rota/arka uç kendisi koruyor. |
| T6 | Jog/çerçeve arka uç yöntemleri | `fake_backend:101-116` (→`fake_jog`), `sim_backend:369-395`, `robot_api_backend:229-244` | Örtük sözleşme (bkz. §3). |
| T7 | JS `say(text, bad)` durum satırı | `bench.js:92`, `frames.js:48`, `sphere.js:143`, `jog.js:18`, `scene_hub.js:28`, `pages/algo.js:62`, `pages/jog.js:81`, `pages/parts.js:36` | 8 kopya; hepsi `textContent` yazar, imzalar az farklı. |
| T8 | Aynı ad, farklı sayfada aynı kalıp (`build`, `render`, `onState`, `mount`, `save`, `refresh`) | `web/**` (11/7/7/6/4/3 tanım) | Sayfa sözleşmesinin doğal sonucu; tekrar sayılmaz, yalnız bilgi. |

## 5. 400 satıra yakın / aşan dosyalar ve büyük işlevler

`UI_EMIRLER` K12 "her dosya < 400 satır" der.

| Dosya | Satır | Durum |
|---|---|---|
| `desktop/gui.py` | **462** | Sınırı aşıyor; `_build` tek işlev 139 satır |
| `agent/service.py` | **414** | Sınırı aşıyor (9 sınıf) |
| `web/pages/parts.js` | 399 | Sınırda |
| `hub_core/sim_backend.py` | 399 | Sınırda |
| `web/pages/program.js` | 398 | Sınırda |
| `hub_core/profile_fields.py`, `program_runner.py`, `fake_jog.py`, `program_store.py`, `cli.py`, `profile_view.py` | 376 / 373 / 356 / 350 / 340 / 340 | Yakın |

Tek işlev olarak 100 satırı aşanlar (ast ile): `server/app.py` `create_app` **213**, `server/program_routes.py`
`install` **201**, `desktop/gui.py` `_build` 139, `server/jog_routes.py` `install_jog_routes` 119,
`server/parts_routes.py` `install` 115, `server/agent_routes.py` `install_agent_routes` 110, `agent/selftest.py`
`agent_selftest` 107. Bunların çoğu iç içe rota kapanışlarıdır (rota başına kısa, toplamda uzun); `create_app`
güvenlik katmanlarını da taşıdığı için bölünmesi sıra-bağımlıdır.

## 6. Test boşlukları (ad eşlemesi; kapsama ölçülmedi)

* **ACİL DUR + yetki rota sözleşmesi yok.** 423 `estop_latched` 13 test dosyasında sınanıyor, ama "kayıtlı her yazma
  rotası kilit/yetki kuralına bağlı" diye rota listesini dolaşan test yok (`app.routes` taraması testlerde geçmiyor).
  Koruma üç biçimde: doğrudan `need_control`, rota-içi `guard()` (`bench_routes`), arka uç içi 423. Bu denetimin AST
  taraması 11 yazma rotasında doğrudan `need_control` bulmadı; elle bakınca hepsi kasıtlı çıktı
  (`login`, `logout`, `/api/estop`, `/api/bench/estop`, `/ws` oturum dışı olacak şekilde; `bench/open|close|command`
  `guard()` ile; `agent/cancel` `need_view`+`need_csrf`; `params/validate|yaml` salt-hesap `need_view`).
  **Açık bulunmadı**, ama bu sonuç testle değil, bu elle incelemeyle; yeni bir rota unutulursa hiçbir şey yakalamaz.
* **JS:** `web/agent.js` ve `web/pages/agent.js` adına test dosyalarında hiç rastlanmadı; `pages/robot.js` 1, `pages.js` 2
  referans (sayfa kaydı/yükleme sırası dolaylı sınanıyor). Tarayıcı testi (`test_ui_browser.py`) `playwright` ve Chrome
  yoksa **atlanır** (`importorskip`/`skip`), yani bazı ortamlarda arayüz hiç sınanmayabilir.
* **Uç nokta başına tek test dosyası:** `/api/mesh` (1), `/api/params` (1), `/api/agent/` (1), `/api/bench*` (1 dosya).
  Bu "az" demektir, "yetersiz" demek değildir.
* `agent/fakeport.py`, `hub_core/fake_model.py` adıyla hiçbir test dosyasında geçmiyor (dolaylı kullanılıyor olabilir).
* `node`, `cascadio`, `numpy`, `yaml`, `playwright` yoksa ilgili testler atlanır: CI'da hangi ortamda koştukları
  `ci.yml`'den ayrıca doğrulanmalı (bu denetim yalnızca `ruff` ve `--fake --check-config` adımlarını gördü).

## 7. REFAKTÖR LİSTESİ

İlke: **yalnız davranışı değiştirmeyen küçük PR'lar**, her biri önce test, sonra taşıma. Öncelik: güvenlik > tekrar >
okunabilirlik. Boyut: XS < 50, S < 150, M < 400 değişen satır (tahmin).

| # | Madde | Dosya | Neden | Risk | Boyut | Öncelik |
|---|---|---|---|---|---|---|
| R1 | **Rota sözleşmesi testi:** kayıtlı tüm POST/PUT/DELETE/WS rotalarını listele; kasıtlı oturumsuz kümeyi (`login, logout, estop, bench/estop, ws`) açıkça sabitle; hareket üretenlerin ACİL DUR kilidinde 423 verdiğini doğrula | `tests/` (yeni `test_route_contract.py`) | Yeni rota korumasız eklenirse bugün hiçbir şey yakalamıyor (§6) | Yok (yalnız test, ürün kodu değişmez) | S | **1 (güvenlik)** |
| R2 | `is_local_request` ve `LOOPBACK_*`/`PROXY_HEADERS` sabitlerini nötr modüle taşı (`server/netcheck.py` ya da `hub_core/access.py`); `jog_routes`/`agent_routes` oradan içe aktarsın | `server/agent_routes.py`, `server/jog_routes.py` | Anahtar girişi ve jog kilidi bu yüklemeye bağlı; güvenlik yüklemi bir "ajan" rota dosyasında yaşıyor, `jog` ona bağımlı | Düşük (ad taşıma; `test_agent_routes.py` parametreli vekil/Host testleri var) | XS | **2 (güvenlik)** |
| R3 | ACİL DUR kilit denetimini rotalarda tek yardımcıya topla (`guard_motion(hub, request, allow_release=False)`), 423 gövdesi aynı kalsın | `server/move_routes.py`, `sim_routes.py`, `jog_routes.py`, `program_routes.py` | T5: aynı denetim 4 rotada elle; `jog_routes:79,96` bırakma istisnası var, kopyalanırken kaçırılabilir | Orta (jog "bırak" istisnası ve yanıt gövdesi birebir korunmalı); **R1 yeşil olmadan başlama** | S | 3 (güvenlik/tekrar) |
| R4 | Atomik yazım için tek yardımcı (`hub_core/atomic.py`: `write_bytes_atomic`, `replace_dir`) | `program_store.py`, `parts_store.py`, `settings.py`, `profile_view.py` | T4: veri kaybı yolu dört yerde dört biçimde | Orta (parts dizin değiştirme `os.replace` sırası; yarım parça görünmemeli), mevcut `test_parts_store`/`test_program_store` var | S | 4 (tekrar/veri güvenliği) |
| R5 | `server/errors.py`: `fail()` ve `read_json` tek yer; beş+bir kopya silinir; `ApiError` işleyicisi de aynı gövdeyi üretsin | `log_/program_/algo_/params_/jog_/move_routes.py`, `sim_routes.py`, `app.py` | T1+T3: 6 kopya + 1 elle yazım | Düşük (gövde biçimi altısında aynı, §4); önce "altın" gövde testi | S | 5 (tekrar) |
| R6 | Arka uç hareket sözleşmesi: `MotionBackend` Protocol (jog/çerçeve/move/model) + "üç arka uç aynı imza" testi; `getattr` çağrıları Protocol kontrolüne dönsün (davranış aynı) | `hub_core/backend.py`, `move_routes.py`, `jog_routes.py`, `mesh_routes.py` | T6: örtük sözleşme; imza kayarsa yalnız çalışma anında 404/500 | Düşük (yalnız tip+test); `getattr` kaldırma ayrı adım | S | 6 (tekrar) |
| R7 | Bağımlılık döngüsü: `agent/selftest.py`'yi `desktop/` altına taşı (ya da `selftests.py`); `program_store`'un `paths.user_dir` bağını dışarıdan enjekte et | `agent/selftest.py`, `desktop/launcher.py`, `hub_core/program_store.py` | §3: `agent`↔`desktop` döngüsü, `hub_core`→kök yukarı bağı | Düşük (`.exe` `hub.spec` içeri alma listesini ve `--agent-selftest` yolunu bozmamalı; `test_agent_selftest`, `test_launcher` var) | S | 7 (okunabilirlik/yapı) |
| R8 | JS ortak `say()`: `shell.js`/`pages.js`'te tek `HubPages.say(node, text, bad)`; 8 kopya tek çağrı olur | `web/{bench,frames,sphere,jog,scene_hub}.js`, `web/pages/{algo,jog,parts}.js` | T7 | Düşük-orta (her kopyanın CSS sınıfı/boş metin davranışı farklı olabilir); node testi ekle | S | 8 (tekrar) |
| R9 | JS test boşluğu: `agent.js` ve `pages/agent.js` mantığını `agent_logic.js`'e ayır + node testi; `pages/robot.js` ve `pages.js` yükleme sırası için node testi | `web/agent.js`, `web/pages/agent.js`, `tests/js/` | §6: sıfır referans | Düşük | S | 9 (test) |
| R10 | `desktop/gui.py` (462) ve `agent/service.py` (414) 400 satır sınırına in; `gui._build` işlevini bölümlere ayır | `desktop/gui.py`, `agent/service.py` | K12 sınırı aşılmış | Düşük (`test_desktop_gui`, `test_agent_service` var; Tk görünümü elle bakılmalı) | S–M | 10 (okunabilirlik) |
| R11 | `create_app` (213 satır) ve `program_routes.install` (201) işlevlerini sıra korunarak parçala: katman kaydı / rota kaydı / WS ayrı işlevler | `server/app.py`, `server/program_routes.py` | §5; `create_app` güvenlik katmanlarının **sırasını** taşıyor | Orta (middleware sırası değişirse CSP/Host/CSRF davranışı kayar); `test_server.py` + R1 şart | M | 11 (okunabilirlik) |
| R12 | `parts.js` (399) ve `program.js` (398) mantığını `*_logic.js`'e ayır (`params_form.js`/`jog_logic.js` deseni) | `web/pages/parts.js`, `web/pages/program.js`, `tests/js/` | Bir sonraki özellik 400'ü aşırır; mantık DOM'da test edilemiyor | Düşük | M | 12 (okunabilirlik) |

**Önerilen sıra ve bağımlılık:** R1 → R2 → R3 (R1'e bağlı). R4, R5, R6 birbirinden bağımsız, R1'den sonra herhangi bir
sırayla. R7 R10'dan önce. R11, R1+R5'ten sonra. Hepsi ayrı PR; hiçbiri ürün davranışı eklemez.

**Bu listede olmayanlar (bilerek):** `web/vendor/` (üçüncü taraf, `docs/THIRD_PARTY.md`), `window.*` modül deseninin
ES modüllerine çevrilmesi (davranış değişir, `.exe` ve CSP etkilenir, ayrı karar), arka uç yeni özelliği.

## 8. Ölçüm yöntemi (yeniden üretilebilir; WSL'de `apps/control_hub` içinde)

```
# satır/sınıf/işlev/paket bağımlılığı: ast ile (tek betik, depoya konmadı)
find control_hub -name '*.py' | xargs wc -l | sort -rn | head
grep -rln "fastapi\|pydantic\|uvicorn\|starlette" control_hub --include=*.py
grep -rn "innerHTML\|eval(\|insertAdjacentHTML" control_hub/web --include=*.js --exclude-dir=vendor
grep -rnE "^\s{4,}(from|import) " control_hub --include=*.py | wc -l      # tembel içe aktarmalar
grep -rlF "/api/move" tests --include=*.py | wc -l                        # uç nokta başına test dosyası
```

Sınırlar: "satır" `splitlines()` sayısıdır (yorum/boş dahil); "işlev" iç içe işlevleri de sayar; içe aktarma
analizi `from control_hub...` ve göreli içe aktarmaları görür, `importlib` ile yapılanları görmez.
