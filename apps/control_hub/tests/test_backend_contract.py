"""Arka uç hareket sözleşmesi (MIMARI.md §7 R6, T6): fake / sim / robot_api AYNI imza.

Rotalar bu yöntemleri ``hub.backend.<ad>(...)`` ile DOĞRUDAN çağırır (``getattr`` yoklaması kalmadı);
imza kayarsa çalışma anında 500 olur. Bu dosya: (1) Protocol'ün kendisini sabitler, (2) üç arka ucun imzasını
Protocol'le karşılaştırır, (3) rotaların çağırdığı adların sözleşmeyle BİRE BİR örtüştüğünü ve rotalarda
``getattr(hub.backend`` kalmadığını doğrular, (4) karşılaştırıcının kaymayı GERÇEKTEN yakaladığını mutantlarla
kanıtlar. Varsayılan sınıfların (``NoJog``/``NoMove``/``NoSim``) davranışı: ``test_backend_defaults.py``.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest

from control_hub.hub_core import backend as contract
from control_hub.hub_core import program_move
from control_hub.hub_core.fake_backend import FakeBackend
from control_hub.hub_core.robot_api_backend import RobotApiBackend
from control_hub.hub_core.sim_backend import SimBackend, SimClock

PKG = Path(__file__).resolve().parents[1] / 'control_hub'
BACKENDS = {'fake': FakeBackend, 'sim': SimBackend, 'robot_api': RobotApiBackend}

MOTION = {
    'jog_command': '(body, *, remote=False)', 'jog_status': '()', 'model': '()',
    'frames_get': '()', 'frames_put': '(body)', 'frames_reach': '(body)',
}
MOVE = {
    'move_submit': '(body)', 'move_check': '(body)', 'move_get': '(job_id)',
    'move_stop': '()', 'move_pause': '()', 'move_resume': '()',
}
SIM = {'sim_reset': '()', 'set_speed': '(pct)'}


# --- karşılaştırıcı (testlerin kendisi; ürün kodu DEĞİL) --------------------------------------------------
def public_functions(proto):
    return {n: f for n, f in vars(proto).items() if inspect.isfunction(f) and not n.startswith('_')}


def contract_of(proto):
    """Protocol + taban Protocol'lerinin açık yöntemleri (``MoveBackend`` -> 12 yöntem)."""
    merged = {}
    for base in reversed(proto.__mro__):
        if getattr(base, '_is_protocol', False):
            merged.update(public_functions(base))
    return merged


def params_of(fn):
    """``self``'siz parametreler; açıklamalar (tip) bilerek yok sayılır: Fake'in dönüşü açıklamasızdır."""
    params = list(inspect.signature(fn).parameters.values())
    assert params and params[0].name == 'self', f'{fn!r}: ilk parametre self olmalı'
    return [p.replace(annotation=inspect.Parameter.empty) for p in params[1:]]


def sig_text(fn):
    return str(inspect.Signature(params_of(fn)))


def drift(cls, proto):
    """``cls``'nin ``proto`` ile uyuşmayan yanlarını insan okuyabilir cümlelerle döner; boş liste = uyumlu."""
    problems = []
    for name, want_fn in contract_of(proto).items():
        have = getattr(cls, name, None)
        if have is None:
            problems.append(f'{name}: yok')
        elif not callable(have):
            problems.append(f'{name}: çağrılabilir değil')
        elif inspect.iscoroutinefunction(have):
            problems.append(f'{name}: async olamaz (rotalar asyncio.to_thread ile çağırır)')
        elif sig_text(have) != sig_text(want_fn):
            problems.append(f'{name}: imza {sig_text(have)} != sözleşme {sig_text(want_fn)}')
    return problems


def mutant(cls, **replacements):
    return type('Mutant' + cls.__name__, (cls,), replacements)


def without(cls, name):
    """``cls``'nin sözleşme yöntemlerinden biri EKSİK kopyası (düz sınıf)."""
    names = [n for n in contract_of(contract.MoveBackend) if n != name]
    return type('Eksik', (), {n: getattr(cls, n) for n in names if hasattr(cls, n)})


# --- 1) Protocol'ün kendisi sabit ---------------------------------------------------------------------
def test_motion_protocol_members_and_signatures_are_pinned():
    got = {n: sig_text(f) for n, f in public_functions(contract.MotionBackend).items()}
    assert got == MOTION                    # rotalar bu adları/argümanları kullanır: kayma = rota işi


def test_move_protocol_extends_motion_with_exactly_the_move_family():
    got = {n: sig_text(f) for n, f in public_functions(contract.MoveBackend).items()}
    assert got == MOVE
    assert {n: sig_text(f) for n, f in contract_of(contract.MoveBackend).items()} == {**MOTION, **MOVE}
    assert issubclass(contract.MoveBackend, contract.MotionBackend)


def test_sim_protocol_is_pinned_and_program_move_uses_the_contract_supports_move():
    assert {n: sig_text(f) for n, f in public_functions(contract.SimControl).items()} == SIM
    assert program_move.supports_move is contract.supports_move       # tek tanım: kopya yok
    assert not hasattr(program_move, 'MOVE_METHODS')                   # ad listesi yerine Protocol


# --- 2) üç arka uç aynı imza --------------------------------------------------------------------------
@pytest.mark.parametrize('kind', sorted(BACKENDS))
def test_every_backend_has_the_motion_contract_with_identical_signatures(kind):
    cls = BACKENDS[kind]
    assert drift(cls, contract.MotionBackend) == []
    assert issubclass(cls, contract.MotionBackend)


def test_the_three_backends_have_literally_the_same_motion_signatures():
    """Protocol'e bakmadan da: üç arka ucun altı yöntemi birbirinin AYNISI (metinsel)."""
    shapes = {kind: {n: sig_text(getattr(cls, n)) for n in MOTION} for kind, cls in BACKENDS.items()}
    assert shapes['fake'] == shapes['sim'] == shapes['robot_api'] == MOTION


def test_sim_backend_has_the_move_family_with_identical_signatures():
    assert drift(SimBackend, contract.MoveBackend) == []
    assert issubclass(SimBackend, contract.MoveBackend)


@pytest.mark.parametrize('kind', ['fake', 'robot_api'])
def test_backends_without_move_inherit_the_nomove_default_whole_and_override_none_of_it(kind):
    """Ya TÜM move_* gerçektir (SİM) ya hiçbiri: ``--fake`` ve Robot API ``NoMove`` varsayılanını DEVRALIR.
    Yöntemlerin hiçbirini kendi gövdesinde tanımlamaz (yarım gerçek / yarım varsayılan olamaz)."""
    cls = BACKENDS[kind]
    assert issubclass(cls, contract.NoMove) and issubclass(cls, contract.NoSim)
    assert [n for n in (*MOVE, *SIM) if n in vars(cls)] == [], f'{kind}: varsayılan ezildi'
    assert drift(cls, contract.MoveBackend) == []                      # varsayılanın imzası da sözleşme
    assert drift(cls, contract.SimControl) == []


def test_sim_backend_owns_the_whole_move_and_sim_family_and_inherits_no_default():
    assert not issubclass(SimBackend, (contract.NoMove, contract.NoSim, contract.NoJog))
    assert [n for n in (*MOVE, *SIM) if n not in vars(SimBackend)] == []
    assert drift(SimBackend, contract.SimControl) == []


def test_instances_satisfy_the_protocols_at_runtime():
    backends = [FakeBackend(), SimBackend(clock=SimClock(), autostart=False),
                RobotApiBackend('http://127.0.0.1:9', poll_s=5, autostart=False)]
    try:
        assert all(isinstance(b, contract.MotionBackend) for b in backends)
        assert all(isinstance(b, contract.MoveBackend) for b in backends)     # yapısal: 12 yöntem var
        assert [isinstance(b, contract.NoMove) for b in backends] == [True, False, True]
        assert [contract.supports_move(b) for b in backends] == [False, True, False]    # GERÇEK mi?
        assert [program_move.supports_move(b) for b in backends] == [False, True, False]
    finally:
        for b in backends:
            getattr(b, 'close', lambda: None)()


@pytest.mark.parametrize('kind', ['fake', 'sim'])
def test_motion_replies_are_status_and_json_body(kind):
    """Gövde biçimi (ağsız iki arka uç): ``(int, dict)``; RobotApi'ninki robota gider, burada ölçülmez."""
    b = FakeBackend() if kind == 'fake' else SimBackend(clock=SimClock(), autostart=False)
    try:
        for status, doc in (b.jog_status(), b.model(), b.frames_get()):
            assert isinstance(status, int) and isinstance(doc, dict)
        assert b.model()[0] == 200 and isinstance(b.model()[1].get('links'), list)
    finally:
        getattr(b, 'close', lambda: None)()


# --- 3) rotalar yalnız sözleşmedeki adları çağırır -----------------------------------------------------
def backend_names(path):
    """Koddaki ``hub.backend.<ad>`` erişimlerinin adları (AST: docstring/yorum metni sayılmaz)."""
    return {n.attr for n in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
            if isinstance(n, ast.Attribute) and ast.unparse(n.value) == 'hub.backend'}


def probes_on_backend(path):
    """``getattr/hasattr`` yoklamalarından ilk argümanı bir ``backend`` olanlar (yorum/docstring sayılmaz)."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        is_probe = isinstance(node, ast.Call) and getattr(node.func, 'id', None) in ('getattr', 'hasattr')
        if is_probe and node.args and 'backend' in ast.unparse(node.args[0]).lower():
            found.append(f'{path.name}:{node.lineno} {ast.unparse(node)}')
    return found


def test_jog_routes_call_exactly_the_motion_contract_plus_clock():
    used, want = backend_names(PKG / 'server' / 'jog_routes.py'), set(MOTION) | {'clock'}
    assert used == want, f'jog_routes sözleşmeden sapıyor: {sorted(used ^ want)}'


def test_move_routes_call_exactly_the_move_contract():
    used = backend_names(PKG / 'server' / 'move_routes.py')
    assert used == set(MOVE), f'move_routes sözleşmeden sapıyor: {sorted(used ^ set(MOVE))}'


def test_sim_routes_call_exactly_the_sim_contract():
    used = backend_names(PKG / 'server' / 'sim_routes.py')
    assert used == set(SIM), f'sim_routes sözleşmeden sapıyor: {sorted(used ^ set(SIM))}'


def test_program_move_reads_only_time_scale_from_the_hub_backend():
    assert backend_names(PKG / 'hub_core' / 'program_move.py') == {'time_scale'}


@pytest.mark.parametrize('where', ['server/move_routes.py', 'server/sim_routes.py', 'server/jog_routes.py',
                                   'hub_core/program_move.py'])
def test_no_getattr_probing_of_the_backend_is_left_in_the_converted_files(where):
    assert probes_on_backend(PKG / where) == []


def test_mesh_routes_ask_the_backend_for_model_only():
    text = (PKG / 'server' / 'mesh_routes.py').read_text(encoding='utf-8')
    asked = set(re.findall(r"getattr\(hub\.backend,\s*'(\w+)'", text))
    assert asked == {'model'} and asked <= set(MOTION)       # kapsam dışı: ayrı iş (PR'da işaretli)


# --- 4) MUTASYON KANITI: karşılaştırıcı kaymayı gerçekten yakalar --------------------------------------
MUTATIONS = {
    'parametre adı değişti': {'frames_put': lambda self, payload: (200, {})},
    'zorunlu parametre eklendi': {'jog_status': lambda self, verbose: (200, {})},
    'isteğe bağlı parametre eklendi': {'jog_status': lambda self, verbose=False: (200, {})},
    'parametre silindi': {'frames_reach': lambda self: (200, {})},
    'remote anahtar-sözcüklü olmaktan çıktı': {'jog_command': lambda self, body, remote=False: (200, {})},
    'remote varsayılanı değişti': {'jog_command': lambda self, body, *, remote=True: (200, {})},
    'remote silindi': {'jog_command': lambda self, body: (200, {})},
    '*args/**kwargs ile gizlendi': {'model': lambda self, *a, **k: (200, {})},
    'yöntem None yapıldı': {'frames_get': None},
}


@pytest.mark.parametrize('what', sorted(MUTATIONS))
@pytest.mark.parametrize('kind', sorted(BACKENDS))
def test_signature_drift_in_any_backend_is_caught(kind, what):
    mutated = mutant(BACKENDS[kind], **MUTATIONS[what])
    problems = drift(mutated, contract.MotionBackend)
    assert problems, f'{kind}: "{what}" yakalanmadı'
    assert len(problems) == 1                      # yalnız bozulan yöntem raporlanır, sahte alarm yok


def test_async_method_is_caught():
    async def frames_get(self):
        return 200, {}
    assert drift(mutant(FakeBackend, frames_get=frames_get), contract.MotionBackend) == [
        'frames_get: async olamaz (rotalar asyncio.to_thread ile çağırır)']


@pytest.mark.parametrize('missing', sorted(MOTION))
def test_missing_motion_method_is_caught(missing):
    for cls in BACKENDS.values():
        assert drift(without(cls, missing), contract.MotionBackend) == [f'{missing}: yok']


@pytest.mark.parametrize('missing', sorted(MOVE))
def test_half_implemented_move_family_is_caught_and_not_a_move_backend(missing):
    half = without(SimBackend, missing)
    assert drift(half, contract.MoveBackend) == [f'{missing}: yok']
    assert not issubclass(half, contract.MoveBackend)
    assert contract.supports_move(half()) is False          # yarım küme: sessizce "desteklenmiyor"


def test_drift_message_names_the_method_and_both_signatures():
    mutated = mutant(SimBackend, frames_put=lambda self, payload: (200, {}))
    (msg,) = drift(mutated, contract.MotionBackend)
    assert msg == 'frames_put: imza (payload) != sözleşme (body)'
