# Control Hub arayüz sözleşmesi (Aşama 0 iskeleti)

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
`scripts`, `available(it)` (false/Promise&lt;false&gt; ise menüde yok), `mount(root, ctx)` (BİR kez),
`onState(snap)`, `onEvents(events)`, `onShow()`, `onHide()`. Hareket veren bir sayfa `onHide()`'ta bıraktırmalıdır
(Jog sayfası `JogPanel.release()` yapar).

Sayfanın kullanabileceği **genel yardımcılar** (`app.js`): `$`, `el(tag, props, children)`, `api(path, method, body)`
(hata: `ApiError`, `.status`, `.body`), `toast(msg, bad)`, `S` (`S.snap`, `S.ui`, `S.session`, `S.events`),
`fmt`, `hhmmss`. 3B görünüm: `HubPages.mountView(kutu)` (mount ve `onShow`'da çağır).

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
