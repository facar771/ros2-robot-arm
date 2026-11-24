# Control Hub arayüz sözleşmesi (Aşama 0 iskeleti + §9 sayfalar arası arayüzler)

Hedef: **yeni bir sayfa yalnızca yeni dosya ekleyerek gelir.** `app.js`, `index.html`, `style.css` (ortak
iskelet), `server/app.py` ve `hub.spec`'e dokunmak gerekmez. Sayfalar kendi dosyalarında yaşar; biri bozulursa
yalnızca kendi kutusunda hata yazar, ACİL DUR ve diğer sayfalar çalışmaya devam eder.

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

Kayıt alanları: `id`, `title`, `icon`, `order` (küçük önce; Robot 10, Jog 20, Ajan 30, Tezgâh 40),
`scripts`, `styles` (yalnız `['/pages/<id>.css']`, aşağıya bak), `requires` (yetenekler, aşağıya bak),
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
* Robota **hareket** gidecekse `hub.estopped` iken `423 estop_latched` dön (bkz. `jog_routes.py`); ACİL DUR'u
  atlayan yol açma. Hub robotun kurallarını gevşetemez, yalnız daraltabilir.
* Hata gövdesi: `{ok: false, error: kod, message: metin}`. Dosya yolu, PIN, anahtar metne girmez.
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
denetler), mantığı varsa saf işlevlere ayırıp `tests/js/` altında node ile sına (`tests/js/pages_check.js` örnek).

## 6. Yerel doğrulama

WSL'de (depo kökünden):

```bash
cd apps/control_hub
python3 -m pytest -q                 # tüm Hub testleri (rota/sayfa keşfi, mesh kapısı, 3B mantığı dahil)
ruff check control_hub tests tools   # CI ile aynı lint
python3 -m control_hub --fake --check-config --no-agent   # iskelet öz denetimi: rota + sayfa keşfi, içe aktarma
python3 -m control_hub --fake --access-mode open --no-agent   # http://localhost:8765
python3 -m tools.ui_demo             # karma_robot CAD mesh'li 3B demo: http://localhost:8767
```

`.exe`'de PyInstaller `pkgutil` ile paketi **listeleyemez**; bu yüzden `hub.spec` derleme anında
`server/*_routes.py` dosyalarını tarayıp `routes.manifest` üretir ve rota modüllerini `hiddenimports`'a ekler
(elle liste yok: yeni `<ad>_routes.py` kendiliğinden girer). `web/` dizini olduğu gibi gömülü olduğundan
`pages/*.js` de kendiliğinden girer. `--check-config` (CI'nin `.exe` adımı) bunları doğrular.

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

## 9. Sayfalar arası arayüzler (KESİN; UI_EMIRLER.md EK-1 ile çelişirse BURASI kazanır)

Amaç: Program, Parça, Parametreler, Kontrol sayfaları birbirini beklemeden yazılsın. Her arayüzün yanında
**durum** yazar: `VAR` (main'de çalışıyor), `S1` / `S2` (ilgili emir birleşince gelir). Gelmeyen arayüze karşı kendi
mock'unla çalış; imza ve hata kodları aşağıdaki gibi sabittir. Ortak hata gövdesi her yerde
`{ok:false, error:<kod>, message:<Türkçe metin>}`.

### 9-A Robot durumu (WS `S.snap`; sahibi S1)

**EK-1 A'dan farklar:** mevcut alanlar (`robot.estop` nesnesi, `robot.tcp.position`, `robot.joints[].rad`...) ARAYÜZÜ
KIRMAMAK için olduğu gibi kalır; yenileri **ekle**nir. Yeni sayfa yalnız aşağıdaki tablodaki alanları okur.

| Alan | Tür / birim | Durum | Not |
|---|---|---|---|
| `snap.estop.latched` | bool | VAR | ACİL DUR'un TEK doğru kaynağı (Hub kilidi VEYA robot kilidi). `robot.estop` (nesne) okunmaz. |
| `snap.caps` | `string[]` | S1-0 | Yetenekler. Tanımlı: `sim` (SİM arka ucu). Arka ucun `caps` özniteliğinden gelir; yoksa `[]`. |
| `snap.robot.mode` | `'fake'\|'sim'\|'real'` | VAR (`sim` S1) | Ham değer. Rozet/mod için `ShellLogic.modeInfo(snap).key` (`sim\|bench\|real\|unknown`) kullan; TEZGÂH ayrı moddur (K3-f). |
| `snap.robot.connected` | bool | VAR | |
| `snap.robot.moving` | bool | VAR | |
| `snap.robot.speed_pct` | sayı 1–100 | S1 | Genel hız yüzdesi. |
| `snap.robot.joints[]` | `{name, rad, deg}` | VAR | S1 ekler: `min_deg`, `max_deg`, `near_limit` (bool; limitin %5'i içinde). |
| `snap.robot.tcp` | `{frame, position, wpr_deg, warning}` | VAR | S1 ekler: `pos_m` (= `position`, metre, dünya), `rpy_deg` (= `wpr_deg`: **W-P-R, derece**, `R = Rz(R)·Ry(P)·Rx(W)`; FANUC düzeni, `robot_core.se3`). Yeni sayfa `pos_m`/`rpy_deg` okur. |
| `snap.robot.gripper` | `{open_pct: sayı\|null}` | S1 | `null` = alet yok/bilinmiyor. (`robot.tool` eski alan: uç konumu, okunmaz.) |
| `snap.robot.safe_zone` | `{min:[x,y,z], max:[x,y,z]}` metre | S1 | Robotun kendi güvenli kutusu; Hub yalnız GÖSTERİR (K3-c). |
| `snap.robot.maturity` | `'tahmini'\|'olculdu'` | S1 | Ölçülmemiş her değer "tahmini" etiketi taşır (K3-e). |
| `snap.robot.stale` | bool | S1 | `stale_s > 1`. Bayatken sayfa komut düğmelerini kapatır. |

### 9-B Hareket uçları (sahibi S1: `server/move_routes.py`, `server/sim_routes.py`)

Hepsi `need_control` ister (GET `need_view`). Hareket veren her uç `hub.estopped` iken **`423 estop_latched`** döner;
ACİL DUR sonrası hiçbir iş kendiliğinden devam etmez.

| Uç | Gövde → yanıt | Durum |
|---|---|---|
| `POST /api/move` | `{type:'joint', joints_deg:[...], speed_pct?}` veya `{type:'linear', pos_m:[x,y,z], rpy_deg?:[W,P,R], orientation?:'fixed\|free', speed_pct?}` (`rpy_deg` yoksa yönelim korunur; `orientation:'free'` = yalnız konum hedefi, yönelimi çözücü belirler, `rpy_deg` ile birlikte **400**) → **202** `{ok:true, job_id}` | S1 |
| `POST /api/move/check` | `/api/move` ile AYNI gövde; **hareket ETMEZ** → **200** `{ok, violations:[{kind, at_pct, message}]}` (`ok:false` ise ihlal var; `at_pct` 0–100: yolun neresinde) | S1 |
| `POST /api/move/stop` · `pause` · `resume` | `{}` → `{ok:true}`; çalışan iş yoksa **409** `not_running` | S1 |
| `GET /api/move/{job_id}` | → `{ok, job:{id, type, state:'running\|paused\|done\|failed\|cancelled', progress_pct, error, message}}`; bilinmeyen **404** `unknown_job` | S1 |
| `POST /api/sim/speed` | `{speed_pct}` (1–100) → `{ok, speed_pct}`; robotun genel hızı. Yalnız SİM | S1 |
| `POST /api/sim/reset` | `{}` → home'a döner. Yalnız SİM'de; başka modda **409** `not_sim`; ACİL DUR'da **423** | S1 |
| `POST /api/jog`, `/api/jog` (GET), `/api/frames*`, `/api/model` | mevcut jog uçları | VAR (korunur) |

Ret kodları (`error`): **409** `outside_safe_zone` · `joint_limit` · `path_blocked` · `unreachable` · `busy`;
**423** `estop_latched`; **400** `bad_request` (NaN, boyut, bilinmeyen alan); **401/403** oturum/kilit. `check`'in
`violations[].kind` değerleri ret kodlarının ilk dördüyle AYNIDIR. `speed_pct` yoksa robotun genel hızı kullanılır;
1–100 dışı **400**. Ret/`check` gövdelerinde ek `at_pct` (0–100) bulunabilir; `--sim` ayrıntıları: `docs/SIM.md`. Birim: metre, derece; yönelim W-P-R (§9-A). Programlar/sayfalar hareketi **yalnız bu uçlarla**
ister; arka uca doğrudan gitmez.

### 9-C 3B sahne API'si `HubScene` (sahibi S2; S1-0'da yalnız İSKELET)

Erişim: `const scene = await HubPages.scene();` (3B modülü yüklenir). `scene.implemented === false` iken her işlev
`{ok:false, error:'not_implemented'}` döner (abonelikler iptal işlevi döner); sayfa bunu görünce ilgili düğmeyi kapatır.
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
(geçici dosya + `os.replace`).
