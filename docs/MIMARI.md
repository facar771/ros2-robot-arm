# Control Hub mimarisi (`apps/control_hub`): denetim ve R1–R12 durumu

Tarih: 2026-11-27 · Güncel durum: `main` `ee31893` · İlk denetim: 2026-11-25, `main` `3557e1a` (Müdür, Robot_9) ·
Güncelleyen: Robot_13. **Bu belge kod değiştirmez.** Rakamlar komutla ölçüldü (yöntem §8; ilk denetimin rakamları
aynı yöntemle `3557e1a` üzerinde yeniden üretilip doğrulandı). Ölçüm olmayan her şey "yorum" diye işaretlidir.

**Bu sürümde ne değişti:** ilk denetimin 12 maddelik refaktör listesi (R1–R12) uygulandı: **11'i kapandı, 1'i (R11) açık,
2'si kısmen kapandı** (R7, R8; kalan işler §7'de). Kapanan maddelerin bulguları bu belgeden SİLİNDİ; yalnız hâlâ geçerli
olanlar kaldı. Denetimden sonra giren özellik PR'ları (P1c klavye jog #115, P4b SİM profil #116, T1/T1b kabul turu
#117/#121) rakamlara dahildir.

**Çalıştırma kanıtı:** `main` `ee31893` için 5 CI işinin hepsi yeşil; "Control Hub (lint, tests, Docker smoke test)" işi
`1350 passed, 15 skipped, 1 warning` (günlükten okundu). Bu belgenin rakamları CI'dan değil, §8'deki komutlardan gelir. Test
boşlukları (§6) yalnız dosya/uç nokta adlarının test dosyalarında geçip geçmediğine bakar, kapsama yüzdesi DEĞİLDİR.

## 1. Genel görünüm

| Ölçü | İlk denetim (`3557e1a`) | Şimdi (`ee31893`) |
|---|---|---|
| Python (`control_hub/`) | 64 dosya, 11 082 satır, 101 sınıf, 770 işlev/yöntem | 74 dosya, 11 553 satır, 106 sınıf, 803 işlev/yöntem |
| Web (vendor hariç; JS+CSS+HTML) | 33 dosya, 5 280 satır; yalnız `.js` 24 dosya, 4 689 satır | 36 dosya, 5 808 satır; yalnız `.js` 27 dosya, 5 206 satır |
| Vendor (three.js, yerel kopya, `web/vendor/`) | 6 dosya, ~2,2 MB (denetim dışı) | 6 dosya, ~2,2 MB (denetim dışı) |
| Test | 57 Python dosyası, 9 522 satır, 635 test işlevi; 16 node betiği | 75 Python dosyası, 12 108 satır, 778 test işlevi; 23 node betiği (`tests/js/`, yardımcılar dahil) |
| Rota | 69 dekoratörlü rota (`server/*.py`) | 72 (+3: `GET /api/params/sim`, `POST /api/params/sim/apply`, `POST /api/params/sim/reset`; P4b, R-listesinden değil) |

(İlk denetim tablosunda vendor "3 dosya" yazıyordu; aynı betik `3557e1a`'da da 6 dosya sayıyor, yani eski rakam eksikti.)

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

**Doğrulanan katman kuralı:** `hub_core/`, `hw/` ve `agent/` içinde `fastapi`/`pydantic`/`starlette`/`uvicorn` geçmiyor
(grep). Web çerçevesi yalnız `server/` (yeni `errors.py`, `guards.py`, `netcheck.py` dahil), `cli.py` (`uvicorn`) ve
`desktop/service.py` (`uvicorn`)'de. Bu kural testle korunmuyor, yalnız grep ile doğrulanıyor.

## 2. Katmanlar ve modül sorumlulukları

Paket başına toplam (dosya / satır / sınıf / işlev): `hub_core` 32 / 6 303 / 64 / 424 · `server` 17 / 1 957 / 12 / 164 ·
`desktop` 10 / 1 418 / 12 / 92 · `agent` 8 / 792 / 14 / 60 · `hw` 3 / 619 / 4 / 41 · kök (`cli`, `paths`) 4 / 464 / 0 / 22.

### 2.1 `hub_core/` (çekirdek; çerçevesiz, çoğu saf ve testli)

| Modül | Tek sorumluluk | Satır |
|---|---|---|
| `hub.py` | Çekirdek parçaları birleştirir: arka uç, koşucu, erişim, günlük, **ACİL DUR kilidi**; `snapshot` `caps` üretir | 178 |
| `backend.py` | `RobotBackend` + **`MotionBackend`** (jog/çerçeve/model) + **`MoveBackend`** (`/api/move*` ailesi) Protocol'leri | 117 |
| `atomic.py` | **Tek** atomik yazım yardımcısı: `write_bytes_atomic`, `write_text_atomic`, `replace_dir` | 85 |
| `fake_backend.py`, `fake_jog.py`, `fake_model.py` | ROS'suz sahte robot (uç nokta oynatır; jog/çerçeve; görünüm modeli) | 177 / 356 / 99 |
| `sim_backend.py` + `sim_plan.py`, `sim_jobs.py`, `sim_assets.py`, `sim_profile.py` | Gerçek kinematikli SİM: arka uç, planlama (saf), iş modeli (saf), profil yolu bulma, **SİM profil geçersiz kılma** (yalnız bellek) | 365 / 291 / 63 / 62 / 132 |
| `robot_api_backend.py` | Hub'ı robotun HTTP API v1'ine bağlar | 278 |
| `access.py` | `open/pin/locked` erişim kipi, PIN, oturum, deneme sınırı | 140 |
| `runner.py`, `scenarios.py`, `commands.py`, `events.py` | Senaryo koşucusu; senaryo/arayüz yapılandırması; komut beyaz listesi; olay halka tamponu | 114 / 231 / 74 / 63 |
| `joint_hold.py` | Eklem basılı-tut jog (bırakınca/kopunca DURUR) | 130 |
| `program_store.py`, `program_runner.py`, `program_move.py` | Program modeli+doğrulama+dosya; sunucu tarafı koşucu; hareket istemcileri | 340 / 373 / 282 |
| `algo_gen.py` | Algoritma üreteçleri → program adımı (saf) | 268 |
| `parts_store.py`, `mesh_tools.py`, `meshes.py`, `step_convert.py`, `step_child.py` | Parça deposu; STL/OBJ ayrıştırma (saf); robot mesh bulma; STEP dönüşümü (alt süreç) | 273 / 302 / 100 / 177 / 267 |
| `profile_view.py`, `profile_fields.py`, `profile_rules.py`, `profile_apply.py` | Parametreler sayfası: oku→kopyada düzenle→doğrula→fark→taslak; alan kataloğu; kurallar (saf); taslağı SİM'e uygulanabilir profile çevirme | 327 / 376 / 185 / 54 |

### 2.2 `server/` (HTTP/WS adaptörü; mantık yok, ilke olarak)

| Modül | Sorumluluk | Satır |
|---|---|---|
| `app.py` | FastAPI kurulumu, **güvenlik katmanları** (Host beyaz listesi, Origin/CSRF, CSP, oturum çerezi), `/api/estop`, WS | 288 |
| `registry.py` | `server/*_routes.py` ve `web/pages/*.js` otomatik keşfi (`.exe`'de manifest) | 116 |
| `guards.py` | **`guard_motion`**: ACİL DUR kilidinde 423, jog "bırak" istisnası (R3) | 45 |
| `errors.py` | **`fail`, `error_body`, `ApiError`, `read_json`, `read_json_stream`** (R5): hata gövdesi tek yerde | 62 |
| `netcheck.py` | **`is_local_request`** + loopback/vekil sabitleri (R2) | 22 |
| `move_routes`, `sim_routes`, `jog_routes`, `program_routes`, `parts_routes`, `mesh_routes`, `params_routes`, `log_routes`, `algo_routes`, `bench_routes`, `agent_routes` | Her biri bir sayfa/özellik alanının uçları; `install(app, ctx)` (11 modül) | 49–249 |

### 2.3 Yan paketler

| Paket | Sorumluluk |
|---|---|
| `agent/` | Ajan sohbet paneli: `service` (313; robot_agent'ı sarar), `config`, `errors`, `guards` (R10'da `service`'ten ayrıldı), `approvals` (insan onayı), `keystore` (anahtar asla günlüğe/yanıta girmez), `fakeport` |
| `hw/` | `bridge` (COM↔TCP, tek istemci), `bench_service` (Tezgâh: `tools/bench` mantığını sürer; gerçek robot değil) |
| `desktop/` | Tk penceresi (`gui` 331 + `gui_sections` 167), `service` (çekirdek+sunucu arka plan iş parçacığı), `settings`, `firewall`, `launcher`, `winconsole`, **`selftest`** (`--agent-selftest`; R7'de `agent/`'ten taşındı) |
| kök | `cli.py` (komut satırı ve **birleştirme noktası**: her şeyi tembel içe aktarır), `paths.py` (kaynak/kullanıcı yolları, `.exe` dahil) |

### 2.4 `web/` (düz JS, derleme yok)

* Yükleme: `index.html` yalnız `shell.js`, `pages.js`, `app.js` yükler; geri kalan her şey `HubPages.loadScript`
  ile sayfa kaydından dinamik yüklenir. Modül deseni: `window.<Ad>` (23 global: `HubPages`, `HubScene`, `SceneHub`,
  `SceneLogic`, `ShellLogic`, `JogLogic`, `ParamsLogic`, `PotLogic`, `LogLogic`, `BenchLogic`, `AgentLogic`, `PartsLogic`,
  `ProgramLogic`, `PotPanel`, `BenchPanel`, `JogPanel`, `AgentPanel`, `FrameWizard`, `SphereWizard`, `FrameTypes`,
  `ApproachMove`, `HubProgram`, `Scene3D`).
* **Mantık/görünüm ayrımı** artık Ajan, Parçalar ve Program sayfalarında da var (R9/R12: `agent_logic.js`, `parts_logic.js`,
  `program_logic.js`; DOM'suz, node ile test edilir) ve ayrıca `jog_logic.js`, `scene_logic.js`, `pot_logic.js`,
  `params_form.js`, `approach.js`, `shell.js`'te. Sayfa dosyaları (`pages/*.js`) DOM'u çizer; ortak mesaj satırı
  `HubPages.say`'dir (R8).
* Güvenlik: kodda `innerHTML`, `eval`, `insertAdjacentHTML`, `document.write` **yok** (grep; yorum olmayan satırda
  sıfır isabet). `fetch(` yalnız 6 dosyada (`app.js`, `agent.js`, `pages/agent.js`, `pages/params.js`, `scene3d.js`,
  `scene_hub.js`).
* Sayfalar (9): robot 10, jog (Kontrol) 20, parts 25, agent 30, program 30, algo 35, bench 40, params 50, log 60
  (`order`; eşitlikte `id` alfabetik).

## 3. Bağımlılık yönü

Hedeflenen yön: `web → (HTTP) → server → hub_core`; `desktop`/`cli` en üstte; `hub_core` hiçbir üst katmanı bilmez.
**`hub_core` ve `agent`'ın `desktop`'u içe aktarmaması testle korunur** (`tests/test_package_deps.py`, tembel içe
aktarmalar dahil; R7).

Ölçülen paket arası içe aktarmalar (ast ile, iç içe/tembel dahil; 65 girintili içe aktarma var, yani birçok bağ çalışma
anında kurulur ve satır başı grep'ten farklı sayılır, bkz. §8):

| Kimden | Kime | Yorum |
|---|---|---|
| `server` | `hub_core` (7 dosya), `agent` (`agent_routes`), `hw` (`bench_routes`), `paths` (3 dosya) | Beklenen yön. |
| `desktop` | `server`, `agent`, `hub_core`, `hw`, `cli`/`paths` | Beklenen (en üst katman). |
| `agent` | yalnız `hub_core` (3 dosya: `fakeport`, `guards`, `service`) | Temiz. |
| `hw` | (hiçbiri) | Temiz. |
| `hub_core` | yalnız `paths`: `parts_store.py:25`, `profile_view.py:20` (`from control_hub import paths`) | **Açık:** iki yukarı bağ kaldı (§7, R7b). |
| `cli.py` | `agent`, `desktop`, `hub_core`, `hw`, `server` | Beklenen (birleştirme noktası). |

Dış bağımlılıklar (içe aktarma ifadesi olan dosya sayısı): `robot_core` 4 (`hub_core/profile_view`, `sim_backend`, `sim_plan`,
`sim_profile`), `robot_agent` 2 (`agent/keystore`, `agent/service`), `numpy` 3 (SİM'in 3 modülü), `yaml` 2 (`profile_view`,
`scenarios`), `cascadio` 1 (`step_child`), `tkinter` 3 (yalnız `desktop/`), `fastapi` 15 (yalnız `server/*`), `pydantic` 3,
`starlette` 1, `uvicorn` 2 (`cli`, `desktop/service`).

**Arka uç sözleşmesi:** `MotionBackend` (6 yöntem) ve `MoveBackend` (+6 yöntem) artık Protocol olarak yazılı ve
`tests/test_backend_contract.py` üç arka ucun (fake/sim/robot_api) imzasını bunlarla karşılaştırır (R6). **Açık kalan:**
rotalar yöntemi hâlâ `getattr(hub.backend, ad, None)` ile çağırır (`move_routes.py:28`, `sim_routes.py:28`,
`mesh_routes.py:32`, `jog_routes.py:35,47,53`, `params_routes.py:44-45`, `program_move.py:33,139`); imza kayması testte
yakalanır, ama yöntem adı yazım hatası hâlâ yalnız çalışma anında 501 olur (§7, R6b).

## 4. Tekrarlar: durum

| # | Tekrar | Durum | Kalan |
|---|---|---|---|
| T1 | Hata yanıtı `fail()` (6 kopya + elle) | **Kapandı** (R5, `server/errors.py`; `tests/test_error_bodies.py` bayt bayt sabitler) | 2 elle gövde: `sim_routes.py:24` (`not_sim`), `app.py:129` (`unsupported_media_type`); ikisi de aynı biçimde ama altın gövde testinde YOK (grep) |
| T2 | Arka uç hata yanıtı `_err(...)` | **Açık** | 3 kopya, imzalar farklı: `fake_jog.py:37`, `joint_hold.py:49`, `sim_backend.py:153` (`program_store.py:56` `_err` başka bir şey: doğrulama listesine ekler) |
| T3 | `read_json` | **Kapandı** (R5; `read_json_stream` büyük gövde için) | — |
| T4 | Atomik dosya yazımı | **Kapandı** (R4, `hub_core/atomic.py`; çağıranlar: `program_store`, `parts_store`, `profile_view`, `desktop/settings`; `tests/test_atomic.py`) | — |
| T5 | ACİL DUR kilit denetimi + 423 | **Rotalarda kapandı** (R3, `guard_motion`: `move_routes` 2, `sim_routes` 1, `jog_routes` 2, `program_routes` 1 çağrı) | Elle 423 kalanlar: `agent_routes.py:139` (ajan onayı), `program_routes.py:220` (koşu yarışı), `params_routes.py:47-48` (SİM uygula), `app.py:214` (senaryo çalıştır: HAREKET rotası; kilidi Hub koşucusu `EstopLatchedError` ile atar ve gövde `guard_motion`'dan FARKLI: mesaj "önce sıfırla", `state` alanı yok). Arka uç içi savunma da sürüyor (çift katman): `sim_backend.py:158,214,227`, `fake_jog.py:106,317` |
| T6 | Jog/çerçeve arka uç yöntemleri örtük sözleşme | **Kapandı** (R6, Protocol + sözleşme testi) | `getattr` çağrıları (§3) |
| T7 | JS `say(text, bad)` durum satırı (8 kopya) | **6'sı kapandı** (R8, `HubPages.say`; `bench`, `frames`, `sphere`, `jog`, `pages/algo`, `pages/jog` ince sarmalayıcı) | `pages/parts.js:22` (farklı imza: sınıf adı yazar, DOM'a yazan yerel kopya), `scene_hub.js:28` (DOM'a değil 3B sahne notuna yazar) |

## 5. 400 satıra yakın / aşan dosyalar ve büyük işlevler

`UI_EMIRLER` K12 "her dosya < 400 satır" der. **Kod dosyalarında (Python + JS) sınırı aşan yok**; en büyükleri:
`hub_core/profile_fields.py` 376, `program_runner.py` 373, `sim_backend.py` 365, `web/pages/params.js` 360, `fake_jog.py` 356,
`program_store.py` 340, `cli.py` 340. Test dosyalarında tek aşan: `tests/test_sim_backend.py` **427** (ilk denetimde de 427'ydi,
tabloya alınmamıştı; K12'nin testleri kapsayıp kapsamadığı belgede yazmıyor, bu yüzden karar notu §7'de).

Tek işlev olarak 100 satırı aşanlar (ast ile): `server/app.py` `create_app` **207**, `server/program_routes.py` `install`
**199**, `server/params_routes.py` `install_params_routes` **122** (yeni: ilk denetimde listede yoktu; P4b SİM uçları),
`server/agent_routes.py` `install_agent_routes` 110, `server/jog_routes.py` `install_jog_routes` 109, `desktop/selftest.py`
`agent_selftest` 107. İlk denetimdeki `desktop/gui.py` `_build` (139) ve `parts_routes.install` (115) listeden çıktı. Kalanların
çoğu iç içe rota kapanışlarıdır (rota başına kısa, toplamda uzun); `create_app` güvenlik katmanlarını da taşıdığı için
bölünmesi sıra-bağımlıdır (§7, R11).

## 6. Test boşlukları (ad eşlemesi; kapsama ölçülmedi)

* **Rota sözleşmesi** artık var (R1: `tests/test_route_contract.py`, 11 test): 50 yazma/WS rotasının hepsi `UNAUTH` (5) /
  `MOTION` (12) / `OTHER` (33) kümelerinden TAM birinde; yeni sınıflandırılmamış rota, korumasız rota ve kümede olup artık
  var olmayan girdi kırmızı verir (5 mutasyon testiyle kanıtlı). Sınır: sınıflandırma ELLE; yeni rota eklemek o listeyi de
  güncellemeyi gerektirir (bilerek).
* **Tarayıcı testleri CI'da çalışmıyor.** `playwright.sync_api` için `importorskip` kullanan 5 dosya var
  (`test_ui_browser.py`, `test_jog_keys_browser.py`, `test_params_page.py`, `test_log_page.py`, `test_params_sim_page.py`);
  `ci.yml`'deki hub işi yalnız `requirements-dev.txt` + `requirements-step.txt` kurar (ikisinde de `playwright` yok), yani
  bu dosyalar CI'da ATLANIR. Sandbox'ta `playwright` gizlenerek 5 dosyanın 5'inin atlandığı ölçüldü. CI günlüğündeki 15
  atlamanın geri kalan 10'unun nedeni günlükten çıkarılamadı (`-rs` yok; doğrulanmadı). Arayüzü gerçekten sürmek için
  elle kabul turu var (`tools/sim_acceptance.py`; isteğe bağlı `sim-acceptance.yml` 5 zorunlu CI işinden biri DEĞİL).
* **Uç nokta başına test dosyası sayısı** (`grep -rlF`): `/api/mesh` **1**, `/api/bench` 2, `/api/agent/` 3, `/api/params` 5,
  `/api/sim` 5, `/api/parts` 6, `/api/program` 6, `/api/log` 8, `/api/jog` 10, `/api/move` 12. Yalnız `/api/mesh` tek dosyada;
  bu "az" demektir, "yetersiz" demek değildir. ACİL DUR `estop_latched` 12 test dosyasında geçiyor.
* **Adı hiçbir test dosyasında kelime olarak geçmeyen modüller:** `agent/fakeport.py`, `hub_core/fake_model.py` (adı yalnız
  `test_fake_model_…` test adında alt dizgi), `server/bench_routes.py`, `server/sim_routes.py`, `server/netcheck.py` (son üçü
  HTTP uçları üzerinden dolaylı sınanıyor). Doğrudan birim testi yoktur.
* Her `web/*.js` ve `web/pages/*.js` dosyasının adı en az 2 test dosyasında geçiyor (ilk denetimin "JS test boşluğu" bulgusu
  kapandı: R9).
* `node`, `cascadio`, `numpy`, `yaml`, `tkinter`, `playwright` yoksa ilgili testler atlanır. CI hub işinde `numpy` (dev),
  `cascadio` (step) ve `yaml` (`requirements.txt`) kuruludur; `playwright` kurulu değildir. `node`: bire bir
  günlükte yok, ama `@needs_node`/`skipif(which('node'))` taşıyan en az 21 test işlevi var ve CI'da toplam atlama 15; node
  olmasaydı atlama ≥ 21 olurdu, yani node'un CI'da bulunduğu **çıkarılır** (doğrulanmış değil).

## 7. REFAKTÖR LİSTESİ: durum

İlke korundu: **yalnız davranışı değiştirmeyen küçük PR'lar**, her biri önce test, sonra taşıma.

| # | Madde | Durum · PR | Ne kapandı (kanıt) | Ne açık kaldı |
|---|---|---|---|---|
| R1 | Rota sözleşmesi testi | **Kapandı** · #118 | `tests/test_route_contract.py` (§6) | — |
| R2 | `is_local_request` nötr modüle | **Kapandı** · #122 | `server/netcheck.py` (22 satır); `agent_routes` ve `jog_routes` oradan alır | — |
| R3 | ACİL DUR denetimi tek yardımcıda | **Kapandı** · #127 | `server/guards.py` `guard_motion`; 6 çağrı yeri; `tests/test_guard_motion.py` (423 gövdesi birebir, jog "bırak" istisnası) | Elle yazılmış 4 423 yeri duruyor, biri hareket rotasında (`app.py:214`, gövde farklı; §4 T5): birleştirmek karar ister (gövde değişir) |
| R4 | Atomik yazım tek yardımcı | **Kapandı** · #119 | `hub_core/atomic.py`; 4 çağıran; `tests/test_atomic.py` | — |
| R5 | `fail`/`read_json` tek yer | **Kapandı** · #122 | `server/errors.py`; `tests/test_error_bodies.py` (altın gövde) | 2 elle gövde (§4 T1) |
| R6 | `MotionBackend` Protocol | **Kapandı** · #123 | `hub_core/backend.py`; `tests/test_backend_contract.py` (14 test, mutasyonlu) | **R6b:** `getattr(hub.backend, ad)` çağrıları (§3) |
| R7 | Bağımlılık döngüsü | **Kısmen** · #126 | `agent/selftest.py` → `desktop/selftest.py`; `agent→desktop` bağı yok; `program_store` `paths`'ten çıktı; `tests/test_package_deps.py` | **R7b:** `hub_core/parts_store.py:25`, `profile_view.py:20` hâlâ `paths` alır |
| R8 | JS ortak `say()` | **Kısmen** · #128 | `HubPages.say`; 6 kopya taşındı; `tests/test_hubpages_say.py` + `tests/js/hubpages_say_check.js` | **R8b:** `pages/parts.js:22`, `scene_hub.js:28` (§4 T7) |
| R9 | JS test boşluğu | **Kapandı** · #120 | `agent_logic.js` + `tests/js/agent_logic_check.js`; `robot_page_check.js`; `page_load_order_check.js` | — |
| R10 | `gui.py` / `agent/service.py` 400 altı | **Kapandı** · #124 | `gui.py` 462 → 331 (+`gui_sections.py`), `service.py` 414 → 313 (+`config`, `errors`, `guards`); `tests/test_r10_split.py` | — |
| R11 | `create_app` ve `program_routes.install` böl | **AÇIK** | Hiçbiri | `create_app` 207, `program_routes.install` 199 satır; ayrıca `params_routes` 122 (§5). **R1 (route contract) ve `test_server.py` var, yani başlamak için önkoşul hazır.** Risk: middleware SIRASI |
| R12 | `parts.js`/`program.js` mantığını ayır | **Kapandı** · #125 + #129 | `program_logic.js` (`program.js` 398 → 323), `parts_logic.js` (`parts.js` 399 → 333); node testleri | — |

**Açık işler ve önerilen sıra** (her biri ayrı küçük PR; hiçbiri ürün davranışı eklemez; açılması Ferhat'ın kararı):
1. **R11:** `create_app` önce "katman kaydı / rota kaydı / WS" olarak sıra korunarak bölünür; `tests/test_route_contract.py`
   ve `test_server.py` yeşil kalmalı. En büyük risk middleware sırası (CSP/Host/CSRF).
2. **R7b:** `parts_store.py` ve `profile_view.py` de `paths` bağını bırakıp yolu dışarıdan alsın (`program_store` bunu yaptı;
   `tests/test_package_deps.py::test_program_store_no_longer_imports_paths` örnek).
3. **T2:** `fake_jog.py`, `joint_hold.py`, `sim_backend.py`'deki 3 `_err`'i tek `hub_core` yardımcısına indirmek (imzalar
   farklı: önce altın yanıt testi).
4. **R6b / R8b / T1 kalanı:** `getattr` kaldırma; `pages/parts.js` yerel `say`; 2 elle hata gövdesini altın teste almak.
5. **Karar notu:** `tests/test_sim_backend.py` (427) K12'yi aşıyor; K12 testleri kapsıyorsa bölünmeli, kapsamıyorsa belgede
   yazılmalı. Ayrıca `agent` ve `program` sayfaları ikisi de `order: 30` (eşitlikte `id` sırası belirler): bilerek mi, karar gerek.

**Bu listede olmayanlar (bilerek):** `web/vendor/` (üçüncü taraf, `docs/THIRD_PARTY.md`), `window.*` modül deseninin
ES modüllerine çevrilmesi (davranış değişir, `.exe` ve CSP etkilenir, ayrı karar), arka uç yeni özelliği.

## 8. Ölçüm yöntemi (yeniden üretilebilir)

WSL'de, depo kökünden `cd apps/control_hub` sonrası. Önce basit komutlar:

```bash
# WSL'de (apps/control_hub içinde)
find control_hub -name '*.py' | xargs wc -l | sort -rn | head                # en büyük Python dosyaları (son satır: toplam)
grep -rln "fastapi\|pydantic\|uvicorn\|starlette" control_hub --include=*.py  # çerçeve yalnız server/, cli.py, desktop/service.py
grep -rn "innerHTML\|eval(\|insertAdjacentHTML" control_hub/web --include=*.js --exclude-dir=vendor   # yalnız YORUM satırları çıkmalı
grep -rnE "^\s{4,}(from|import) " control_hub --include=*.py | wc -l        # girintili (tembel/yerel) içe aktarma, satır başı grep
grep -rlF "/api/move" tests --include=*.py | wc -l                          # uç nokta başına test dosyası (örnek: /api/move)
grep -rhE "^\s*(async )?def test" tests --include=*.py | wc -l              # test işlevi sayısı
ls tests/js/*.js | wc -l                                                    # node betiği (yardımcılar dahil)
```

Python rakamları (dosya/satır/sınıf/işlev ve 100 satırı aşan işlevler), tek betik, depoya konmadı:

```bash
# WSL'de (apps/control_hub içinde)
python3 - <<'EOF'
import ast, pathlib
pkg = pathlib.Path('control_hub'); tot = {}; big = []
for f in sorted(pkg.rglob('*.py')):
    text = f.read_text(encoding='utf-8'); parts = f.relative_to(pkg).parts
    s = tot.setdefault(parts[0] if len(parts) > 1 else '(kök)', [0, 0, 0, 0]); s[0] += 1; s[1] += len(text.splitlines())
    for n in ast.walk(ast.parse(text)):
        s[2] += isinstance(n, ast.ClassDef); is_fn = isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)); s[3] += is_fn
        if is_fn and n.end_lineno - n.lineno + 1 > 100: big.append((n.end_lineno - n.lineno + 1, f.relative_to(pkg).as_posix(), n.name))
for k, v in tot.items(): print('%-9s dosya %2d satır %5d sınıf %3d işlev %3d' % (k, *v))
print('TOPLAM', [sum(v[i] for v in tot.values()) for i in range(4)]); print(*sorted(big, reverse=True), sep='\n')
EOF
```

Paketler arası içe aktarma (§3), tek betik:

```bash
# WSL'de (apps/control_hub içinde)
python3 - <<'EOF'
import ast, pathlib, collections
pkg = pathlib.Path('control_hub'); edges = collections.defaultdict(set)
for f in pkg.rglob('*.py'):
    p = f.relative_to(pkg).parts; src = p[0] if len(p) > 1 else '(kök)'
    for n in ast.walk(ast.parse(f.read_text(encoding='utf-8'))):
        names = [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else [a.name for a in n.names] if isinstance(n, ast.Import) else []
        for m in names:
            q = m.split('.')
            if q[0] == 'control_hub':
                dst = q[1] if len(q) > 1 and q[1] not in ('cli', 'paths') else '(kök)'
                if dst != src: edges[(src, dst)].add(f.name)
for (a, b), fs in sorted(edges.items()): print('%-9s -> %-9s %s' % (a, b, ', '.join(sorted(fs))))
EOF
```

Sınırlar: "satır" `splitlines()` sayısıdır (yorum/boş dahil); "işlev" iç içe işlevleri de sayar; içe aktarma analizi
`from control_hub...` ve mutlak içe aktarmaları görür, göreli (`from . import`) ve `importlib` ile yapılanları görmez
(grep ile ast arasında 67 ↔ 65 fark: grep docstring/metin içindeki satırları da sayar). Sandbox'ta ek olarak şu CI
adımları koşturuldu: `python3 -m pytest` (`-rs` ile atlama nedenleri), `ruff check`, `python3 -m control_hub --fake
--check-config` ve `--sim --check-config`.
