# Control Hub arayüz sözleşmesi (Aşama 0 iskeleti + §9 sayfalar arası arayüzler)

Hedef: **yeni bir sayfa yalnızca yeni dosya ekleyerek gelir.** `app.js`, `index.html`, `style.css` (ortak
iskelet), `server/app.py` ve `hub.spec`'e dokunmak gerekmez. Sayfalar kendi dosyalarında yaşar; biri bozulursa
yalnızca kendi kutusunda hata yazar, ACİL DUR ve diğer sayfalar çalışmaya devam eder.

Son eşitleme: 2026-11-26, `main` `ee31893` (R1–R12 refaktörü, P1c klavye jog, P4b SİM profil, kabul turu sonrası).
Modül haritası ve ölçümler: `docs/MIMARI.md`. Ortak yardımcılar (`guard_motion`, `fail`, `HubPages.say`, `MotionBackend`,
`atomic`…) ve yeni dosyalar: **§10**.

## 1. Kabuk ne verir (sayfa DEĞİŞTİRMEZ)

| Parça | Nerede | Not |
|---|---|---|
| Üst çubuk: **ACİL DUR**, bağlantı, mod (SİM / TEZGÂH / GERÇEK), erişim kipi (PIN / AÇIK / SALT OKUNUR) | `index.html` + `app.js` + `shell.js` | Her ekranda (giriş dahil) sabit. ACİL DUR oturumsuz da çalışır. |
| Sol menü + sayfa yönlendirme (`#/ad`) | `pages.js` | Menü `web/pages/*.js` dosyalarından kurulur. |
| 3B robot görünümü (Three.js, **yerel**) | `scene3d.js`, `scene_logic.js`, `vendor/three/` | Sayfa `HubPages.mountView(kutu)` ile ister; tek tuval, sayfalar arası taşınır. |
| Oturum, canlı akış (WS), uyarı bandı | `app.js` | Sayfa `onState(snap)` ile durum alır. |

## 2. Dosyalar (bir sayfa = en çok 3 dosya)

| Dosya | Zorunlu | İçerik |
|---|---|---|
| `control_hub/web/pages/<ad>.js` | evet | `HubPages.register({...})`; **dosya adı = `id`** (`[a-z][a-z0-9_]*`, en çok 32). |
| `control_hub/server/<ad>_routes.py` | rota gerekiyorsa | `install(app, ctx)`; otomatik yüklenir. `install` yoksa Hub açılışta HATA verir. |
| `tests/test_<ad>_page.py` | evet | Rota + sayfa sözleşmesi testi (aşağıda şablon). |
| `control_hub/web/<ad>_logic.js` + `tests/js/<ad>_logic_check.js` | mantık varsa | Sayfanın SAF mantığı (§3 "Mantık dosyası"). |

Yeni **yazma (POST/PUT/DELETE) ya da WS rotası** eklerken `tests/test_route_contract.py`'deki `UNAUTH` / `MOTION` /
`OTHER` kümelerinden TAM birine de eklenir; eklenmezse o test KIRMIZI olur (bilerek: korumasız rota sessizce girmesin).
Ayrıntı §4.

Sayfa kendi yardımcı betiklerini `scripts: ['/x.js']` ile **ilk gösterimde** yükler (başka bir yere eklenmez).
Menüde yalnızca bazen görünmesi gereken sayfa `available()` döndürür (ör. Tezgâh yalnız `--bench` ile).

## 3. Şablon: sayfa (`web/pages/hello.js`)

```js template:page
/* Sayfa: Merhaba (örnek şablon). Veri textContent ile yazılır (innerHTML YOK). */
'use strict';

(() => {
  const R = { out: null };
  HubPages.register({
    id: 'hello', title: 'Merhaba', icon: '👋', order: 90,
    mount(root) {
      R.out = el('div', { class: 'say', id: 'hello-out' });
      root.append(el('div', { class: 'card', id: 'p-hello' }, [el('h2', { text: 'Merhaba' }), R.out]));
      return api('/api/hello').then((r) => { R.out.textContent = r.text; })
        .catch((e) => { R.out.textContent = `Alınamadı: ${e.message}`; });
    },
    onState(snap) { /* her durum mesajında; yalnız mount'tan sonra çağrılır */ },
  });
})();
```

Kayıt alanları: `id`, `title`, `icon`, `order` (küçük önce; eşitlikte `id` alfabetik, `ShellLogic.sortPages`;
bugünkü değerler: Robot 10, Kontrol 20, Parçalar 25, Ajan 30, Program 30, Algoritmalar 35, Tezgâh 40, Parametreler 50,
Günlük 60), `scripts`, `styles` (yalnız `['/pages/<id>.css']`, aşağıya bak), `requires` (yetenekler, aşağıya bak),
`available(it)` (false/Promise&lt;false&gt; ise menüde yok), `mount(root, ctx)` (BİR kez),
`onState(snap)`, `onEvents(events)`, `onShow()`, `onHide()`. Hareket veren bir sayfa `onHide()`'ta bıraktırmalıdır
(Jog sayfası `JogPanel.release()` yapar).

**`styles`**: sayfaya özgü stil `web/pages/<ad>.css` dosyasında yaşar; kayıt `styles: ['/pages/<ad>.css']` der ve
dosya ilk gösterimde `<link>` ile yüklenir (CSP `style-src 'self'` değişmez). `style.css`'e sayfa için TEK satır
yazılmaz. Yalnız KENDİ dosyası olabilir (başkasınınki ya da dış adres kayıtta reddedilir). Dosya yüklenemezse
sayfa kendi kutusunda hata yazar. Seçicileri kendi sayfa kökünle (`#page-<ad> ...`) başlat.

**`requires`**: `requires: ['sim']` yeteneklerin hepsi `snap.caps` içinde değilse sayfa menüde GİZLİ kalır
(durum gelince/yetenek değişince menü kendiliğinden güncellenir; durum henüz yoksa gizli). `available()` ile birlikte
kullanılırsa ikisi de uygun olmalıdır. Yetenek adı `[a-z][a-z0-9_]{0,23}`. Tanımlı yetenekler §9-A'da.

Sayfanın kullanabileceği **genel yardımcılar** (`app.js`): `$`, `el(tag, props, children)`, `api(path, method, body)`
(hata: `ApiError`, `.status`, `.body`), `toast(msg, bad)`, `S` (`S.snap`, `S.ui`, `S.session`, `S.events`),
`fmt`, `hhmmss`. 3B görünüm: `HubPages.mountView(kutu)` (mount ve `onShow`'da çağır). 3B sahne API'si:
`const scene = await HubPages.scene();` (§9-C).

**Mesaj satırı: `HubPages.say(düğüm, metin, bad, seçenek)`** (`pages.js`; R8). Sayfa kendi `say` yazıcısını YAZMAZ.
Metin `textContent` ile yazılır (boş/undefined → `''`); `düğüm` yoksa sessizce döner; `bad` yalnız `bad` sınıfını
açar/kapar (düğümün öteki sınıfları, ör. `say`, `jg-msg`, korunur). `{ list: true }`: `metin` bir SATIR DİZİSİdir,
düğüm `<ul>` olur, her satır bir `<li>` (bad ise `class="bad"`; Tezgâh iletileri). Örnek:
`const say = (t, bad) => HubPages.say(R.out, t, bad);`. Test: `tests/test_hubpages_say.py` +
`tests/js/hubpages_say_check.js`.

**Mantık dosyası (R9/R12 deseni).** Karar veren kod (hangi düğme kapalı, hangi metin, sunucuya hangi gövde gider) sayfa
dosyasından ayrılır: `web/<ad>_logic.js` DOM'suz, `fetch`'siz, saatsiz SAF kod; `window.<Ad>Logic` verir (örnekler:
`agent_logic.js`, `jog_logic.js`, `parts_logic.js`, `program_logic.js`, `pot_logic.js`). İki yükleme biçimi vardır:
(1) kayıtta `scripts: ['/<ad>_logic.js', '/<ad>.js']` (Ajan, Kontrol, Parçalar, Parametreler, Tezgâh): betikler **dizi
sırasıyla, biri bitince öteki** yüklenir (`pages.js` `for … await loadScript`), yani mantık dosyası sayfa betiğinden ÖNCE
gelir; (2) sayfa dosyasında `const ready = HubPages.loadScript('/<ad>_logic.js')` ve `mount` içinde `await ready`
(Program). Sayfa dosyası yalnız çizer ve ağa konuşur.
Node testi: `tests/js/<ad>_logic_check.js` (+ node yoksa atlanan bir `tests/test_<ad>_logic.py` sarmalayıcısı).
Mantıktaki "kapalı/açık" kararı yalnız GÖSTERİMdir; asıl denetim sunucudadır (arayüze güvenilmez).

## 4. Şablon: rota (`server/hello_routes.py`)

```python template:routes
"""GET /api/hello (örnek şablon): salt okunur."""
from fastapi import Request


def install(app, ctx) -> None:
    @app.get('/api/hello')
    async def hello(request: Request):                  # Request ANNOTASYONU şart (yoksa sorgu parametresi sanılır)
        ctx.need_view(request)
        return {'ok': True, 'text': f'merhaba, arka uç: {ctx.hub.backend.name}'}
```

`ctx` (`RouteContext`): `hub`, `need_view(request)` (oturum yoksa 401), `need_control(request)` (kilitliyse 403,
oturum yoksa 401), `token_of(request)`, `api_error(status, kod, mesaj)` (`raise ctx.api_error(404, 'yok', '...')`).

Kurallar:

* **GET** uçları `need_view`; **POST/PUT/DELETE** uçları `need_control` ister (Hub ara katmanı JSON `Content-Type`
  ve aynı-kaynak denetimini zaten uygular).
* Robota **hareket** gidecekse kilidi KENDİN yazma: `guard_motion` çağır (`server/guards.py`, R3). Kilitliyse `423
  estop_latched` yanıtını verir, değilse `None` döner. ACİL DUR'u atlayan yol açma. Hub robotun kurallarını
  gevşetemez, yalnız daraltabilir.
  ```python
  from control_hub.server.guards import guard_motion
  # ... uç işlevinin içinde, ctx.need_control(request)'ten sonra, işe girişmeden ÖNCE:
  locked = guard_motion(ctx.hub, request)
  if locked is not None:
      return locked
  ```
  `guard_motion(hub, request, allow_release=False, body=None)`: `allow_release=True` YALNIZ jog'a aittir: gövde bırakma
  isteğiyse (`is_release(body)`: `ttl_ms == 0`) kilitte de geçer, çünkü ACİL DUR'da süren tutuşun bırakılabilmesi
  gerekir; bırakma hareket BAŞLATMAZ. Varsayılan KAPALI: gövde ne olursa olsun 423. Koşu sırasındaki yarış
  (`EstopLatchedError`) için aynı gövde `estop_latched_response()` ile alınır.
* Hata gövdesi: `{ok: false, error: kod, message: metin}`. Dosya yolu, PIN, anahtar metne girmez. Kendi `fail`/`_fail`/
  `read_json` kopyanı YAZMA; `server/errors.py` (R5) tek yerdir: `fail(status, kod, mesaj, **ek)` (JSONResponse döner),
  `error_body(kod, mesaj, **ek)` (yalnız sözlük), `ApiError` (rota içinden `raise`; `ctx.api_error` bunu üretir),
  `read_json(request, limit=8 KiB)` → `(gövde, hata_yanıtı)` (sınır aşımı 413 `too_large`, geçersiz JSON 400
  `bad_request`, boş gövde `None`), `read_json_stream(request, limit)` (büyük gövde, örn. CAD; sınırda keser, hata
  `ApiError`). Gövde biçimi `tests/test_error_bodies.py` ile bayt bayt sabittir.
* "Yalnız bu bilgisayardan" kuralı (basılı-tut jog, ajan anahtarı) için `server/netcheck.is_local_request(request)`
  (R2): istemci loopback, `Host` loopback adı VE vekil başlığı yok; üçü birlikte. Kendi sürümünü yazma.
* Arka uç yöntemi çağırırken sözleşme `hub_core/backend.py`'dedir (R6): jog/çerçeve/model için `MotionBackend`, `/api/move*`
  ailesi için `MoveBackend` (ikincisini YALNIZ SİM bilir). Rotalar yöntemi hâlâ `getattr(hub.backend, ad, None)` ile
  alır (yoksa 501/409); imzayı `tests/test_backend_contract.py` üç arka uçta (fake / sim / robot_api) sabitler. Yeni
  arka uç yöntemi önce Protocol'e ve o teste eklenir.
* Dosya yazan uç veriyi `hub_core/atomic.py` ile yazar (`write_bytes_atomic`, `write_text_atomic`, `replace_dir`: aynı
  dizinde geçici dosya + TEK `os.replace`); kendi `mkstemp`/`os.replace` kalıbını yazma (R4).
* Yeni POST/PUT/DELETE/PATCH ya da WS rotası `tests/test_route_contract.py` kümelerinden TAM birine girer: `MOTION`
  (hareket üretir; ACİL DUR'da 423 sınanır, değer = geçerli gövde), `OTHER` (yetkili ama hareket üretmez) ya da `UNAUTH`
  (kasıtlı oturumsuz: bugün yalnız `login`, `logout`, `/api/estop`, `/api/bench/estop`, `/ws`; eklemek GÜVENLİK kararıdır,
  gerekçe PR'a yazılır). Test, listede olmayan rotaya ve listede olup artık var olmayan girdiye KIRMIZI verir (R1).
* Dosya yazan/komut çalıştıran uç eklemek mimari karardır: önce Robot_1.

## 5. Şablon: test (`tests/test_hello_page.py`)

```python template:test
from control_hub.server import registry


def test_hello_route_is_auto_installed(make_client):
    assert 'hello_routes' in registry.route_module_names()
    c, _ = make_client('open')
    assert c.get('/api/hello').json()['ok'] is True
    pin, _ = make_client('pin')
    assert pin.get('/api/hello').status_code == 401          # oturumsuz görünmez
    assert 'hello' in [p['id'] for p in c.get('/api/pages').json()['pages']]
```

Ek olarak: sayfada `innerHTML` yok, dış adres yok (`tests/test_ui_skeleton.py` bunları TÜM `web/pages/*.js` için zaten
denetler), mantığı varsa saf işlevlere ayırıp `tests/js/` altında node ile sına (`tests/js/pages_check.js` örnek;
sayfa kaydı/yükleme sırası için `tests/js/page_env.js` gerçek `shell.js` + `pages.js`'i sahte bir tarayıcıda koşturur).
`node` yoksa bu testler ATLANIR (CI'da ve elde node kurulu olmalı). Yeni yazma rotasının sınıflandırması: §4 son maddeler.

## 6. Yerel doğrulama

WSL'de (depo kökünden):

```bash
cd apps/control_hub
python3 -m pytest -q                 # tüm Hub testleri (rota/sayfa keşfi, mesh kapısı, 3B mantığı dahil)
python3 -m pytest -q tests/test_route_contract.py tests/test_guard_motion.py tests/test_backend_contract.py tests/test_package_deps.py   # sözleşme testleri (R1, R3, R6, R7)
ruff check control_hub tests tools   # CI ile aynı lint
python3 -m control_hub --fake --check-config --no-agent   # iskelet öz denetimi: rota + sayfa keşfi, içe aktarma
python3 -m control_hub --sim --check-config               # SİM de açılır, FK robot_core ile karşılaştırılır (numpy ister)
python3 -m control_hub --fake --access-mode open --no-agent   # http://localhost:8765
python3 -m tools.ui_demo             # karma_robot CAD mesh'li 3B demo: http://localhost:8767
python3 -m tools.sim_acceptance --list   # SİM kabul turu adımları (koşturmak: README "SİM'i çalıştır", docs/KABUL.md)
```

`.exe`'de PyInstaller `pkgutil` ile paketi **listeleyemez**; bu yüzden `hub.spec` derleme anında
`server/*_routes.py` dosyalarını tarayıp `routes.manifest` üretir ve rota modüllerini `hiddenimports`'a ekler
(elle liste yok: yeni `<ad>_routes.py` kendiliğinden girer). Paketin geri kalanı `collect_submodules('control_hub')` ile
girer, yani `server/errors.py`, `guards.py`, `netcheck.py` gibi yeni YARDIMCI modüller de `hub.spec`'e dokunmadan paketlenir
(`tests/test_r10_split.py::test_exe_import_list_covers_new_modules` bunu korur). `web/` dizini olduğu gibi gömülü olduğundan
`pages/*.js` ve `*_logic.js` de kendiliğinden girer. `--check-config` (CI'nin `.exe` adımı) bunları doğrular.

## 7. 3B görünüm, mesh, Three.js

* Veri: `GET /api/model` (robotun `view_model.json`'u: eklem zinciri, link şekilleri, güvenli kutu, TCP) +
  `GET /api/meshes` (profildeki `meshes/<robot>/link/<link>.stl`; yoksa primitif şekiller çizilir, hata değildir) +
  Hub durumundaki eklem açıları. **Salt okunur**; komut yolu yok; ölçüm/güvenlik işlevi değil.
* **Bayatlık:** WS'ten 1 s mesaj gelmezse ya da robot verisi 1 s'den eskiyse görünüm griye döner, hareket donar,
  kırmızı bant çıkar (`scene_logic.js: staleReason`).
* Mesh bulma sırası: `HUB_MESH_DIR`, `.exe` içinde `_MEIPASS/robot_description/meshes`, depo `src/robot_description/meshes`.
  Docker'da (depo yok) mesh yok → primitif.
* Three.js **yerel** (`web/vendor/three/`, sürüm ve SHA-256 `README.md`'de, testle denetlenir). CDN YOK; CSP
  `default-src 'self'` değişmez. Yenilemek: `README.md`'deki adımlar. Sunucu `.js` dosyasını `text/javascript` ile
  sunar (Windows kayıt defteri `text/plain` verebilir; ES modülü + `nosniff` bunu reddeder).

## 8. Yapılmaz

* `innerHTML`, satır içi `<script>`/`style=`/`onclick=`, CDN/dış adres, `eval`.
* Ortak iskelet dosyalarını yeni sayfa için düzenlemek (gerekiyorsa iskelet eksiğidir: PR aç, sayfaya sokma).
* ACİL DUR düğmesini sayfaya taşımak/kopyalamak; üst çubuk tek sahiptir (Tezgâh'ın kendi ACİL DUR'u donanım tezgâhı
  içindir ve ek bir güvencedir).
* Robot API / `robot_core` / firmware değiştirmek.
* Ortak yardımcının kopyasını yazmak: kendi ACİL DUR 423 denetimi (`guard_motion`), `fail`/`_fail`/`read_json`
  (`server/errors.py`), "yerel istek" denetimi (`server/netcheck.py`), `mkstemp`+`os.replace` kalıbı
  (`hub_core/atomic.py`), mesaj satırı yazıcısı (`HubPages.say`). Yardımcıda eksik varsa yardımcıyı genişlet (§10).
* `hub_core/`, `hw/` ve `agent/` içinde `fastapi`/`pydantic`/`starlette`/`uvicorn` kullanmak; `hub_core` ya da `agent`'tan
  `desktop`'u içe aktarmak (işlev içindeki tembel içe aktarma dahil). Yalnız ikincisini `tests/test_package_deps.py`
  yakalar; birincisi `docs/MIMARI.md` §1'deki grep kuralıdır (testi yok).

## 9. Sayfalar arası arayüzler (KESİN; UI_EMIRLER.md EK-1 ile çelişirse BURASI kazanır)

Amaç: Program, Parça, Parametreler, Kontrol sayfaları birbirini beklemeden yazılsın. Her arayüzün yanında
**durum** yazar: `VAR` (main'de çalışıyor), `SİM` (main'de var, ama yalnız `--sim` arka ucu doldurur; `--fake`'te yoktur:
sayfa yokluğa hazır olmalı). Eskiden `S1` / `S2` / `P1` etiketli olanların HEPSİ birleşti (`main` `ee31893`); kendi
mock'una artık gerek yok, ama imza ve hata kodları aşağıdaki gibi sabittir. Ortak hata gövdesi her yerde
`{ok:false, error:<kod>, message:<Türkçe metin>}` (`server/errors.py`, §10).

### 9-A Robot durumu (WS `S.snap`; sahibi S1)

**EK-1 A'dan farklar:** mevcut alanlar (`robot.estop` nesnesi, `robot.tcp.position`, `robot.joints[].rad`...) ARAYÜZÜ
KIRMAMAK için olduğu gibi kalır; yenileri **ekle**nir. Yeni sayfa yalnız aşağıdaki tablodaki alanları okur.

| Alan | Tür / birim | Durum | Not |
|---|---|---|---|
| `snap.estop.latched` | bool | VAR | ACİL DUR'un TEK doğru kaynağı (Hub kilidi VEYA robot kilidi). `robot.estop` (nesne) okunmaz. |
| `snap.caps` | `string[]` | VAR | Yetenekler. Tanımlı: `sim` (SİM arka ucu). Arka ucun `caps` özniteliğinden gelir (`hub.py` snapshot); yoksa `[]` (`--fake`). |
| `snap.robot.mode` | `'fake'\|'sim'\|'real'` | VAR | Ham değer. Rozet/mod için `ShellLogic.modeInfo(snap).key` (`sim\|bench\|real\|unknown`) kullan; TEZGÂH ayrı moddur (K3-f). |
| `snap.robot.connected` | bool | VAR | |
| `snap.robot.moving` | bool | VAR | |
| `snap.robot.speed_pct` | sayı 1–100 | SİM | Genel hız yüzdesi. |
| `snap.robot.joints[]` | `{name, rad, deg}` | VAR | SİM ekler: `min_deg`, `max_deg`, `near_limit` (bool; limitin %5'i içinde). |
| `snap.robot.tcp` | `{frame, position, wpr_deg, warning}` | VAR | SİM ekler: `pos_m` (= `position`, metre, dünya), `rpy_deg` (= `wpr_deg`: **W-P-R, derece**, `R = Rz(R)·Ry(P)·Rx(W)`; FANUC düzeni, `robot_core.se3`). Yeni sayfa `pos_m`/`rpy_deg` okur. |
| `snap.robot.gripper` | `{open_pct: sayı\|null}` | SİM | `null` = alet yok/bilinmiyor. (`robot.tool` eski alan: uç konumu, okunmaz.) |
| `snap.robot.home_deg` | `[derece,…]` (isteğe bağlı) | SİM | Başlangıç (home) eklem açıları; yoksa Kontrol sayfasında Home kapalı. Yalnız `--sim` doldurur. |
| `snap.robot.safe_zone` | `{min:[x,y,z], max:[x,y,z]}` metre | SİM | Robotun kendi güvenli kutusu; Hub yalnız GÖSTERİR (K3-c). |
| `snap.robot.maturity` | `'tahmini'\|'olculdu'` | SİM | Ölçülmemiş her değer "tahmini" etiketi taşır (K3-e). |
| `snap.robot.stale` | bool | SİM | `stale_s > 1`. Bayatken sayfa komut düğmelerini kapatır. |

SİM'in `snap.robot` anahtarları (sandbox'ta `SimBackend().snapshot()` ile ölçüldü): `can_interrupt, connected, estop, gripper,
home_deg, job, jog, joints, maturity, mode, moving, name, note, objects, profile_override, safe_zone, speed_pct, stale,
tcp, tool`. `--fake`'inki: `can_interrupt, connected, estop, jog, joints, mode, moving, name, objects, tcp, tool`
(eklemde yalnız `name, rad, deg`; `tcp`'de yalnız `frame, position, wpr_deg, warning`). Gerçek robot arka ucu
(`robot_api_backend.py`) `pos_m`, `near_limit`, `speed_pct`, `safe_zone`, `maturity`, `home_deg`, `gripper` ÜRETMEZ (kodda
yok; yalnız `stale_s`): bu alanlara bağlı her gösterim "veri yok"a düşmeli, sayfa yokluğa hazır olmalıdır.

### 9-B Hareket uçları (sahibi S1: `server/move_routes.py`, `server/sim_routes.py`)

Hepsi `need_control` ister (GET `need_view`). Hareket veren her uç `hub.estopped` iken **`423 estop_latched`** döner;
ACİL DUR sonrası hiçbir iş kendiliğinden devam etmez.

| Uç | Gövde → yanıt | Durum |
|---|---|---|
| `POST /api/move` | `{type:'joint', joints_deg:[...], speed_pct?}` veya `{type:'linear', pos_m:[x,y,z], rpy_deg?:[W,P,R], orientation?:'fixed\|free', speed_pct?}` (`rpy_deg` yoksa yönelim korunur; `orientation:'free'` = yalnız konum hedefi, yönelimi çözücü belirler, `rpy_deg` ile birlikte **400**) → **202** `{ok:true, job_id}` | SİM |
| `POST /api/move/check` | `/api/move` ile AYNI gövde; **hareket ETMEZ** → **200** `{ok, violations:[{kind, at_pct, message}]}` (`ok:false` ise ihlal var; `at_pct` 0–100: yolun neresinde) | SİM |
| `POST /api/move/stop` · `pause` · `resume` | `{}` → `{ok:true}`; çalışan iş yoksa **409** `not_running` | SİM |
| `GET /api/move/{job_id}` | → `{ok, job:{id, type, state:'running\|paused\|done\|failed\|cancelled', progress_pct, error, message}}`; bilinmeyen **404** `unknown_job` | SİM |
| `POST /api/jog/joint` | `{lease, joint, dir:±1, ttl_ms, speed_pct?}`: TEK eklemi basılı-tutla sürer (P1). Komut = heartbeat; `ttl_ms` ≤ 500 (varsayılan 250) içinde yenilenmezse **sunucu** durdurur; `ttl_ms:0` bırakır. 423 ACİL DUR · 403 `hold_local_only` (telefon) · 409 `joint_limit`/`busy` · 501 `jog_unsupported` (arka uç bilmiyor). Klavye jog (`Esc` = ACİL DUR dahil) bunu kullanır | VAR |
| `POST /api/sim/speed` | `{speed_pct?, scale?, accel_scale?, accel_linked?}` → `{ok, speed_pct[, scale, accel_scale, accel_linked]}`; robotun genel hızı. Yalnız SİM; **P1e: sınırsız** — TEK sınır: sayı sonlu, > 0, `speed_pct` ≤ 100000, `scale`/`accel_scale` ≤ 1000 (dışı **400**, sessizce kırpılmaz, hiçbiri uygulanmaz; `null` alan 400). `scale` = hız tavanı ölçeği (varsayılan profil `max_velocity_scale`), `accel_scale` = ivme tavanı ölçeği (`accel_linked:true` iken hız ölçeğiyle birlikte hareket eder; `true` iken `accel_scale` verilemez). `scale`/`accel_scale` ACİL DUR **423**, gerçek/tezgâh/sahte **409** `sim_only` (`speed_pct` ≤ 100 kısıtı gerçek modda aynen); bir SONRAKİ komutta geçerli; Hub yeniden başlayınca varsayılan (diske yazılmaz). Etkin değer profil limitini aşarsa/dönerse Günlük olayı `sim_over_profile`; anlık görüntüde `robot.speed_over_profile` (P1d/P1e) | SİM |
| `GET /api/speed/limits` | `?speed_pct=` (SİM: > 0 ve ≤ 100000; gerçek mod 1–100; yoksa robotun genel hızı) → `{ok, robot, mode, scale, scale_default, scale_min (0, hariç), scale_max (1000), pct_max, accel_scale, accel_linked, scale_locked, speed_pct, above_profile, over_speed, over_accel, jog:{gain, requested_gain, capped}, joints:[{name, limit_rad_s, limit_deg_s, effective_rad_s, effective_deg_s, accel_limit_rad_s2, accel_effective_rad_s2, over_speed, over_accel}], limiting_joint, example_move, example_moves:[{joint, deg, duration_s}, …]}`. Etkin hız = limit × ölçek × %; etkin ivme = profil ivmesi × ivme ölçeği × %. SİM dışında `scale_locked:true`, `locked_reason`, `joints:[]`. Arayüz hesap yapmaz, yalnız gösterir (P1d) | VAR |
| `POST /api/sim/reset` | `{}` → home'a döner. Yalnız SİM'de; başka modda **409** `not_sim`; ACİL DUR'da **423** | SİM |
| `POST /api/jog`, `/api/jog` (GET), `/api/frames*`, `/api/model` | mevcut jog uçları; P1e: `/api/jog` gövdesinde isteğe bağlı `speed_pct` (kartın hız %'si, > 0 ve ≤ 100000, dışı 400) SİM jog tavanını o hıza göre kurar; gerçek/sahte arka uca İLETİLMEZ | VAR (korunur) |

Ret kodları (`error`): **409** `outside_safe_zone` · `joint_limit` · `path_blocked` · `unreachable` · `busy`;
**423** `estop_latched`; **400** `bad_request` (NaN, boyut, bilinmeyen alan); **401/403** oturum/kilit. `check`'in
`violations[].kind` değerleri ret kodlarının ilk dördüyle AYNIDIR. `speed_pct` yoksa robotun genel hızı kullanılır;
1–100 dışı **400**. Ret/`check` gövdelerinde ek `at_pct` (0–100) bulunabilir; `--sim` ayrıntıları: `docs/SIM.md`. Birim: metre, derece; yönelim W-P-R (§9-A). Programlar/sayfalar hareketi **yalnız bu uçlarla**
ister; arka uca doğrudan gitmez.

### 9-C 3B sahne API'si `HubScene` (sahibi S2; VAR)

Erişim: `const scene = await HubPages.scene();` (3B modülü yüklenir). `scene.implemented === false` iken her işlev
`{ok:false, error:'not_implemented'}` döner (abonelikler iptal işlevi döner); sayfa bunu görünce ilgili düğmeyi kapatır.
`implemented` bugün yalnız WebGL açılamazsa `false` olur (`scene_hub.js`: `get implemented() { return !V.glFailed; }`);
eskiden "iskelet" anlamına gelen durum kalmadı.
Dünya çerçevesi Z yukarı, metre, derece, yönelim W-P-R. **Nesne kimlikleri sayfa önekiyle** (`parts:<uuid>`,
`program:wp3`); başka sayfanın kimliğine dokunulmaz.

| İşlev | İmza |
|---|---|
| `addObject(o)` | `{id, kind, layer, geometry, pose:{pos_m, rpy_deg}, color, opacity, label}` · `kind` ∈ `mesh\|box\|sphere\|frame\|polyline\|marker` · `geometry`: mesh `{url}` (Hub'ın sunduğu STL), box `{size_m:[x,y,z]}`, sphere `{radius_m}`, frame `{size_m}`, polyline `{points_m:[[x,y,z],…], closed?}`, marker `{radius_m, number?}` |
| `removeObject(id)` · `setPose(id, pose)` · `setVisible(idVeyaKatman, bool)` | |
| `onPick(cb)` | `cb({objectId, point_m, normal})` → iptal işlevi |
| `enableGizmo(id, {mode:'translate\|rotate', snap, space:'world\|local'})` · `onGizmoChange(cb)` | `cb({id, pose, phase:'start\|move\|end'})` → iptal işlevi |
| `cameraPreset(ad)` · `focus(idVeya'robot')` · `measure(bool)` · `setTrail(bool)` | `ad` ∈ `iso\|top\|front\|side` |

**S2'nin eklediği uçlar** (geriye uyumlu; `implemented === false` iken aynı `not_implemented` kuralı):

| İşlev | Anlam |
|---|---|
| `updateObject(id, patch)` | `patch`: `{color?, opacity?, scale?` (yalnız mesh), `points_m?` (yalnız polyline)`}`; kimlik yoksa `not_found` |
| `select(id\|null)` · `disableGizmo()` | seçili nesneyi vurgular · tutamacı kaldırır (nesne silinirse otomatik) |
| `onMeasure(cb)` | `cb({a_m, b_m, distance_m})` → iptal işlevi; **model** mesafesidir, gerçek ölçüm değildir |
| `ready(id)` | mesh yüklenince `{ok:true}` ya da `{ok:false, error:'load_failed'\|'not_found'}`; asla reddetmez |
| `info(id)` | `{id, kind, layer, pose, attach, visible, loaded, error}` ya da `null` · `list()` → kimlik dizisi |
| `tcpPose()` · `tcpOffset()` | flanş×TCP ofseti dünya pozu `{pos_m, rpy_deg}` · ofset; model yokken `null` |
| `setPickLayers(katmanlar)` | `onPick`'in dikkate alacağı katmanlar (varsayılan `workpiece, tool, environment, markers`) |
| `layerVisible(katman)` · `isGizmoDragging()` | görünürlük · tutamaç sürükleniyor mu |
| `addObject`'te `geometry.scale` | yalnız mesh: pozitif ölçek çarpanı (varsayılan 1; sınırlar `scene_logic.js` `MIN_SCALE..MAX_SCALE`) |
| `addObject`'te `attach:'flange'` | nesne dünyaya değil robot flanşına bağlanır (alet); yalnız `'flange'` ya da `null` |

Katmanlar: `robot, workpiece, tool, environment, path, markers, safebox, grid`. Sabitler `scene.layers/kinds/presets`.
Mevcut bayatlık/ACİL DUR davranışı (kırmızı bant, donma) sahne API'sinden bağımsızdır ve korunur.

### 9-D Kullanıcı verisi klasörü (VAR)

Kullanıcının ürettiği veri (parça, program, profil taslağı) **repo dosyalarına değil** `HUB_DATA_DIR` altına yazılır:
`paths.data_dir()` (`HUB_DATA_DIR` mutlak yol, `..` içermez; yoksa `<kullanıcı klasörü>/data`) ve
`paths.data_subdir('parts'|'programs'|'profiles')` (yalnız bu üç ad; başkası `ValueError`). Dosya adı kullanıcıdan
GELEN metinden türetilmez (uuid dizin + temizlenmiş ad); yükleme kuralları UI_EMIRLER K6'dadır. Yazım atomiktir
(`hub_core/atomic.py`: aynı dizinde geçici dosya + TEK `os.replace`; §10).

## 10. Ortak yardımcılar ve yeni dosyalar (R1–R12 sonrası; sayfa/rota yazarken ÖNCE bunlara bak)

Satır sayıları `main` `ee31893` (ölçüm yöntemi: `docs/MIMARI.md` §8). "Ne zaman" sütunu, kopyasını yazmak yerine hangi
yardımcıyı kullanacağını söyler.

| Dosya (`control_hub/` altında) | Satır | Ne verir | Ne zaman · testi |
|---|---|---|---|
| `server/guards.py` | 45 | `guard_motion(hub, request, allow_release=False, body=None)`, `estop_latched_response()`, `is_release(body)` | Hareket üreten HER rota · `tests/test_guard_motion.py` |
| `server/errors.py` | 62 | `fail`, `error_body`, `ApiError` + `api_error_response`, `read_json`, `read_json_stream`, `DEFAULT_MAX_BODY` (8 KiB) | Hata yanıtı / gövde okuma · `tests/test_error_bodies.py` (altın gövde) |
| `server/netcheck.py` | 22 | `is_local_request(request)`, `LOOPBACK_CLIENTS`, `LOOPBACK_HOSTS`, `PROXY_HEADERS` | "Yalnız bu bilgisayardan" uçları · `tests/test_agent_routes.py` (vekil/Host) |
| `hub_core/atomic.py` | 85 | `write_bytes_atomic`, `write_text_atomic`, `replace_dir` | Dosya yazan her yer · `tests/test_atomic.py` |
| `hub_core/backend.py` | 117 | `RobotBackend`, `MotionBackend` (jog/çerçeve/model, 6 yöntem), `MoveBackend` (+ move ailesi, 6 yöntem), `Reply` | Arka uç yöntemi eklerken · `tests/test_backend_contract.py` (üç arka ucun imzası) |
| `hub_core/sim_profile.py`, `profile_apply.py` | 132 · 54 | SİM profil geçersiz kılma (P4b): kayıtlı TASLAK yalnız SİM belleğine yüklenir; `GET /api/params/sim`, `POST /api/params/sim/apply`, `POST /api/params/sim/reset` | Parametreler → "SİM'e uygula"; gerçek robota ve profil dosyasına YAZMAZ · `tests/test_params_sim.py` |
| `web/pages.js` → `HubPages.say` | — | Ortak mesaj satırı (§3) | Sayfada durum/hata metni · `tests/test_hubpages_say.py` |
| `web/agent_logic.js`, `parts_logic.js`, `program_logic.js` | 94 · 182 · 153 | Ajan / Parçalar / Program sayfalarının saf mantığı (§3 "Mantık dosyası") | Kararlar burada, çizim sayfada · `tests/js/*_logic_check.js` |
| `agent/config.py`, `errors.py`, `guards.py` | 45 · 18 · 65 | `agent/service.py`'den ayrıldı (R10): ayarlar, hatalar, günlük LLM sayacı / iptal denetimli LLM / ACİL DUR portu | Ajan kodu · `tests/test_r10_split.py` |
| `desktop/gui_sections.py`, `desktop/selftest.py` | 167 · 314 | Tk bölümleri (`gui.py`'den, R10); `--agent-selftest` (`agent/`'ten taşındı, R7) | Pencere / `.exe` öz denetimi · `tests/test_r10_split.py`, `tests/test_package_deps.py` |
| `tools/sim_acceptance.py` + `tools/acceptance_*.py` | — | SİM kabul turu (README "SİM'i çalıştır", `docs/KABUL.md`); `.github/workflows/sim-acceptance.yml` isteğe bağlıdır, 5 zorunlu CI işinden BİRİ DEĞİL | Elle / isteğe bağlı CI · `tests/test_sim_acceptance*.py` |

Sözleşme testleri (hepsi `apps/control_hub/tests/`): `test_route_contract.py` (R1: 5 oturumsuz + 12 hareket + 33 diğer =
50 yazma/WS rotası sınıflandırılmış), `test_package_deps.py` (R7), `test_page_load_order.py` + `tests/js/page_load_order_check.js`
(R9: sayfa yükleme sırası), `test_jog_keys_browser.py` (P1c klavye jog, tarayıcıda; `playwright` yoksa atlanır).
