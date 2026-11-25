"""Rota sözleşmesi (docs/MIMARI.md §7 R1): korumasız ya da ACİL DUR'a kayıtsız yeni yazma rotası = KIRMIZI.

Üç sözleşme, hepsi ``app.routes``'tan türetilir (elle rota listesi yok; yalnız SINIFLANDIRMA elle ve açık):

1. **Sınıflandırma tam:** her POST/PUT/DELETE/PATCH rotası ve her WS rotası ``UNAUTH`` (kasıtlı oturumsuz
küme),
   ``MOTION`` (hareket üretir: ACİL DUR kilidinde 423) ya da ``OTHER`` (yetkili ama hareket üretmez)
   kümelerinden
   TAM birindedir. Listede olmayan rota = sınıflandırılmamış = test kırmızı; kümede olup artık var olmayan
   rota da kırmızı.
2. **Oturumsuz küme dışındaki her yazma rotası yetki kuralına bağlı:** uç işlevi gövde doğrulamasından
BAĞIMSIZ olarak
   (``model_construct``) oturumsuz çağrılır; PIN kipinde 401, salt-okunur (locked) kipte 403 beklenir. Gövde
   doğrulaması
   yetki denetiminden önce koşsaydı korumasız rota da 400 verip gizlenirdi; bu yüzden HTTP değil doğrudan
   çağrı.
3. **Hareket üreten rotalar ACİL DUR kilidinde 423 ``estop_latched``** verir (gerçek HTTP, geçerli gövdeyle).

Mutasyon testleri sahte korumasız/korumasız-hareket rotası ekleyip bu denetimlerin gerçekten yakaladığını
kanıtlar.
Ürün kodu değişmez. Yeni rota eklerken: ilgili kümeye (gerekçesiyle) ekle; korumasız olacaksa neden
olduğunu yaz.
"""
import asyncio
import inspect

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from control_hub.server.app import ApiError
from tests.conftest import CONFIG

WRITE_METHODS = {'POST', 'PUT', 'DELETE', 'PATCH'}

# Kasıtlı oturumsuz küme (docs/MIMARI.md §7 R1). Yeni öğe eklemek GÜVENLİK kararıdır; gerekçesi PR'da
# yazılmalı.
UNAUTH = {
    ('POST', '/api/login'),  # giriş yapmak için oturum olamaz (hız sınırı + PIN denetimi kendi içinde)
    ('POST', '/api/logout'),  # çıkış herkese açık; yalnızca verilen belirteci düşürür
    ('POST', '/api/estop'),  # ACİL DUR oturum/kilit/içerik türü denetiminin DIŞINDA: acilde curl yetmeli
    ('POST', '/api/bench/estop'),  # tezgâh ACİL DUR'u aynı nedenle açık
    ('WS', '/ws'),  # kendi içinde check_view yapar (PIN kipinde 4401 ile kapatır; aşağıda test edilir)
}

# Hareket üretenler: ACİL DUR kilidinde 423 ``estop_latched``. Değer = o rotanın GEÇERLİ gövdesi (423 gövde
# doğrulamasından sonra döndüğü için geçerli olmalı). ``{...}`` yer tutucular testte doldurulur.
MOTION = {
    ('POST', '/api/scenarios/{scenario_id}/run'): {},
    ('POST', '/api/jog'): {'lease': 'kontrattest01', 'twist': [0.01, 0, 0, 0, 0, 0], 'ttl_ms': 250},
    ('POST', '/api/jog/joint'): {'lease': 'kontrattest01', 'joint': 0, 'dir': 1, 'speed_pct': 50,
                                 'ttl_ms': 250},
    ('POST', '/api/move'): {'type': 'linear', 'pos_m': [0.15, 0.0, 0.12], 'orientation': 'free',
                            'speed_pct': 50},
    ('POST', '/api/move/resume'): {},
    ('POST', '/api/params/sim/apply'): {},
    ('POST', '/api/params/sim/reset'): {},
    ('POST', '/api/program/goto'): {'step': {'kind': 'move_j', 'joints_deg': [0, 90, 74, 0, -18, 0]},
                                    'speed_pct': 50},
    ('POST', '/api/program/run'): {},
    ('POST', '/api/program/step'): {},
    ('POST', '/api/program/resume'): {},
    ('POST', '/api/sim/reset'): {},
}

# Yetkili ama hareket ÜRETMEYENLER (durdurma/kontrol/veri/ayar). Gerekçe kısa; 423 denetimi dışındadırlar.
OTHER = {
    ('POST', '/api/scenarios/cancel'): 'durdurma',
    ('POST', '/api/estop/reset'): 'kilidi açar; hareket başlatmaz (ayrı onay ister)',
    ('POST', '/api/agent/message'): 'sohbet; araç çağrıları hub rotalarından geçer (423 orada)',
    ('POST', '/api/agent/approvals/{approval_id}'): 'onay; kilitteyken kendi 423 kuralı var (agent_routes)',
    ('POST', '/api/agent/cancel'): 'durdurma',
    ('POST', '/api/agent/key'): 'ayar',
    ('POST', '/api/agent/key/clear'): 'ayar',
    ('POST', '/api/agent/config'): 'ayar',
    ('POST', '/api/algo/generate'): 'saf hesap',
    ('POST', '/api/bench/open'): 'tezgâh: kendi PIN kuralı ve kendi ACİL DUR yolu (/api/bench/estop)',
    ('POST', '/api/bench/close'): 'tezgâh bağlantısını kapatır',
    ('POST', '/api/bench/command'): 'tezgâh: Hub kilidine DEĞİL tezgâh kuralına bağlı (PR notu)',
    ('PUT', '/api/frames'): 'veri (çerçeve kaydı)',
    ('POST', '/api/frames/reach'): 'saf hesap',
    ('POST', '/api/move/check'): 'kuru kontrol; robotu oynatmaz',
    ('POST', '/api/move/stop'): 'durdurma',
    ('POST', '/api/move/pause'): 'durdurma',
    ('POST', '/api/params/validate'): 'saf hesap',
    ('POST', '/api/params/yaml'): 'saf hesap',
    ('POST', '/api/params/draft'): 'taslak kaydı',
    ('DELETE', '/api/params/draft'): 'taslak silme',
    ('POST', '/api/parts'): 'veri (parça yükleme)',
    ('PUT', '/api/parts/{part_id}'): 'veri',
    ('DELETE', '/api/parts/{part_id}'): 'veri',
    ('POST', '/api/program/save'): 'veri',
    ('DELETE', '/api/program/{slug}'): 'veri',
    ('POST', '/api/program/import'): 'veri',
    ('POST', '/api/program/check'): 'kuru kontrol',
    ('POST', '/api/program/check_move'): 'kuru kontrol',
    ('POST', '/api/program/pause'): 'durdurma',
    ('POST', '/api/program/heartbeat'): 'canlılık (hareket başlatmaz)',
    ('POST', '/api/program/stop'): 'durdurma',
    ('POST', '/api/sim/speed'): 'hız ayarı; hareket başlatmaz',
}

# OTHER içinde, salt-okunur izleyiciye de AÇIK olanlar (``need_view``): yan etkisiz saf hesap. Başka rota
# buraya
# eklenirse locked kipte 403 verme zorunluluğundan kurtulur; bu yüzden küme küçük ve gerekçeli tutulur.
VIEWER_OK = {
    ('POST', '/api/params/validate'): 'saf doğrulama; durum/robot değişmez',
    ('POST', '/api/params/yaml'): 'saf YAML üretimi; durum/robot değişmez',
}


# --- rota envanteri -----------------------------------------------------------------------------
def write_routes(app):
    """{(yöntem, yol): rota}; WS rotaları ('WS', yol). Mount (statik dosya) yazma rotası değildir."""
    found = {}
    for r in app.routes:
        if isinstance(r, APIWebSocketRoute):
            found[('WS', r.path)] = r
        elif isinstance(r, APIRoute):
            for m in sorted(r.methods & WRITE_METHODS):
                found[(m, r.path)] = r
    return found


def classification_problems(app, unauth=UNAUTH, motion=MOTION, other=OTHER):
    """Sınıflandırma tam mı? (her yazma rotası TAM bir kümede; kümede hayalet rota yok)."""
    routes = set(write_routes(app))
    sets = {'UNAUTH': set(unauth), 'MOTION': set(motion), 'OTHER': set(other)}
    problems = []
    for key in sorted(routes - set().union(*sets.values())):
        problems.append(f'SINIFLANDIRILMAMIŞ yazma rotası {key}: UNAUTH/MOTION/OTHER kümesine ekle')
    for name, s in sets.items():
        for key in sorted(s - routes):
            problems.append(f'{name} kümesinde artık var olmayan rota {key}: kümeden çıkar')
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for key in sorted(sets[a] & sets[b]):
                problems.append(f'{key} hem {a} hem {b} kümesinde')
    return problems


# --- yetki sözleşmesi ---------------------------------------------------------------------------
def _fake_request(app, method, path):
    scope = {'type': 'http', 'method': method, 'path': path, 'query_string': b'', 'app': app,
             'headers': [(b'host', b'localhost')], 'client': ('127.0.0.1', 50000), 'scheme': 'http',
             'server': ('localhost', 80)}

    async def receive():  # gövde okunursa boş; yetki denetimi öncesinde okunmamalı
        return {'type': 'http.request', 'body': b'{}', 'more_body': False}
    return Request(scope, receive)


def _kwargs(route, request):
    """Uç işlevi için doğrulamasız argümanlar: Request, model_construct() gövdeleri, yol parametresi 'x'."""
    kwargs = {}
    for name, p in inspect.signature(route.endpoint).parameters.items():
        ann = p.annotation
        if isinstance(ann, str):  # `from __future__ import annotations`: ad -> tür
            try:
                ann = eval(ann, route.endpoint.__globals__)  # noqa: S307  yalnız kendi uç işlevlerimizin açıklamaları
            except Exception:  # noqa: BLE001
                pass
        if ann is Request:
            kwargs[name] = request
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            kwargs[name] = ann.model_construct()
        else:
            kwargs[name] = 'x'
    return kwargs


def _anonymous_status(app, key, route):
    """Oturumsuz çağrının (HTTP durumu, hata kodu); beklenmedik bir istisna = yetki denetimi atlandı."""
    request = _fake_request(app, key[0], key[1])
    try:
        res = asyncio.run(route.endpoint(**_kwargs(route, request)))
    except ApiError as exc:
        return exc.status, exc.code
    except Exception as exc:  # noqa: BLE001  korumasız uç işlev başka yere ulaştı
        return None, f'yetki denetimi atlandı: {type(exc).__name__}: {exc}'
    status = getattr(res, 'status_code', 200)
    return status, None


def auth_problems(app, unauth=UNAUTH, expect=401, skip=()):
    problems = []
    for key, route in sorted(write_routes(app).items()):
        if key in unauth or key in skip or key[0] == 'WS':
            continue
        status, code = _anonymous_status(app, key, route)
        if status != expect:
            problems.append(f'{key}: oturumsuz çağrı {status} ({code}), {expect} beklenirdi: yetkisiz')
    return problems


def make_hub(mode, backend=None):
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    backend = backend or SimBackend(clock=SimClock(), autostart=False)
    return Hub(backend, scen, ui, AccessControl(mode, '123456')), scen


@pytest.fixture
def pin_app():
    hub, _ = make_hub('pin')
    yield create_app(hub)
    hub.close()


@pytest.fixture
def locked_app():
    hub, _ = make_hub('locked')
    yield create_app(hub)
    hub.close()


@pytest.fixture
def open_env():
    """SİM arka ucu (jog/move/sim destekler); sözleşme GÖZLENEN davranıştır (SİM'in kendi reddi de var)."""
    hub, scen = make_hub('open')
    app = create_app(hub)
    client = TestClient(app, base_url='http://localhost', client=('127.0.0.1', 50000))
    yield app, hub, client, next(iter(scen))
    hub.close()


def _add_front(app, method, path, handler):
    """Sahte rota ekler; statik Mount'un ÖNÜNE alır (yoksa Mount yakalardı)."""
    router = APIRouter()
    router.add_api_route(path, handler, methods=[method])
    app.router.routes.insert(0, router.routes[0])


# --- 423 sözleşmesi -----------------------------------------------------------------------------
def estop_problems(app, hub, client, scenario_id, motion=MOTION):
    problems = []
    for (method, path), body in sorted(motion.items()):
        url = path.replace('{scenario_id}', scenario_id)
        client.post('/api/estop/reset', json={})
        client.post('/api/estop')
        assert hub.estopped
        resp = client.request(method, url, json=body)
        is_json = resp.headers.get('content-type', '').startswith('application/json')
        err = resp.json().get('error') if is_json else None
        if resp.status_code != 423 or err != 'estop_latched':
            problems.append(f'{method} {path}: ACİL DUR kilidinde {resp.status_code} ({err}), 423 beklenirdi')
    return problems


# --- testler ------------------------------------------------------------------------------------
def test_every_write_and_ws_route_is_classified(pin_app):
    assert classification_problems(pin_app) == []


def test_unauthenticated_set_is_exactly_the_pinned_one(pin_app):
    """Kasıtlı oturumsuz küme ROTA LİSTESİNDE açıkça sabit; ne eksik ne fazla."""
    routes = write_routes(pin_app)
    assert UNAUTH <= set(routes)
    assert UNAUTH == {('POST', '/api/login'), ('POST', '/api/logout'), ('POST', '/api/estop'),
                      ('POST', '/api/bench/estop'), ('WS', '/ws')}
    assert [k for k in routes if k[0] == 'WS'] == [('WS', '/ws')]  # başka WS rotası yok


def test_all_other_write_routes_require_a_session_in_pin_mode(pin_app):
    assert auth_problems(pin_app, expect=401) == []


def test_all_other_write_routes_refuse_read_only_viewers(locked_app):
    """Salt-okunur kipte yazma rotaları 403 ``locked``: izleme yetkisi kontrol yetkisi değildir."""
    assert auth_problems(locked_app, expect=403, skip=VIEWER_OK) == []
    routes = write_routes(locked_app)
    assert set(VIEWER_OK) <= set(routes) and set(VIEWER_OK) <= set(OTHER)
    for key in VIEWER_OK:  # küme çürümesin: bunlar gerçekten izleyiciye açık
        assert _anonymous_status(locked_app, key, routes[key])[0] not in (401, 403)


def test_ws_rejects_anonymous_in_pin_mode(pin_app):
    client = TestClient(pin_app, base_url='http://localhost', client=('127.0.0.1', 50000))
    headers = {'host': 'localhost'}                      # TestClient WS'te varsayılan 'testserver'
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect('/ws', headers=headers) as ws:
        ws.receive_text()
    assert exc.value.code == 4401  # hiç durum verisi akmadan kapanır


def test_motion_routes_return_423_when_estop_latched(open_env):
    app, hub, client, scenario_id = open_env
    assert estop_problems(app, hub, client, scenario_id) == []


# --- mutasyon: sözleşme gerçekten yakalıyor mu --------------------------------------------------
def test_mutation_unclassified_unprotected_route_is_caught(pin_app):
    async def zz_mutant(request: Request):  # korumasız VE sınıflandırılmamış yazma rotası
        return {'ok': True}
    _add_front(pin_app, 'POST', '/api/zz_mutant', zz_mutant)
    problems = classification_problems(pin_app)
    assert any('/api/zz_mutant' in p and 'SINIFLANDIRILMAMIŞ' in p for p in problems)


def test_mutation_unprotected_route_declared_other_is_caught(pin_app):
    async def zz_open(request: Request):  # yetki denetimi yok; "OTHER" diye gizlenmeye çalışılıyor
        return {'ok': True}
    _add_front(pin_app, 'DELETE', '/api/zz_open', zz_open)
    other = {**OTHER, ('DELETE', '/api/zz_open'): 'sahte'}
    assert classification_problems(pin_app, other=other) == []  # sınıflandırma tek başına yetmez...
    problems = auth_problems(pin_app, expect=401)
    assert any('/api/zz_open' in p for p in problems)  # ...yetki denetimi yakalar


def test_mutation_unprotected_ws_route_is_caught(pin_app):
    async def zz_ws(sock):
        await sock.accept()
    pin_app.router.routes.insert(0, APIWebSocketRoute('/ws2', zz_ws))
    assert any("'/ws2'" in p for p in classification_problems(pin_app))


def test_mutation_motion_route_without_estop_guard_is_caught(open_env):
    app, hub, client, scenario_id = open_env

    async def zz_move(request: Request):  # yetkili ama ACİL DUR kilidine bakmıyor
        return {'ok': True}
    _add_front(app, 'POST', '/api/zz_move', zz_move)
    motion = {**MOTION, ('POST', '/api/zz_move'): {}}
    problems = estop_problems(app, hub, client, scenario_id, motion=motion)
    assert any('/api/zz_move' in p for p in problems)


def test_mutation_stale_set_entry_is_caught(pin_app):
    other = {**OTHER, ('POST', '/api/silinmis_rota'): 'hayalet'}
    assert any('silinmis_rota' in p for p in classification_problems(pin_app, other=other))
