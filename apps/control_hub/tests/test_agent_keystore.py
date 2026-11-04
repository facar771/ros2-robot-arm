import pytest

from control_hub.agent.keystore import SERVICE, KeyStore
from tests.helpers_agent import CANARY, MemoryKeyring


def test_default_is_memory_only_and_never_touches_the_store():
    kr = MemoryKeyring()
    ks = KeyStore(kr)
    remembered, warning = ks.set('openai', CANARY)
    assert (remembered, warning) == (False, None)
    assert ks.get('openai') == CANARY
    assert kr.data == {} and not any(c[0] == 'set' for c in kr.calls)
    assert ks.status()['openai'] == {'set': True, 'remembered': False}


def test_remember_persists_and_survives_a_new_store():
    kr = MemoryKeyring()
    remembered, _ = KeyStore(kr).set('anthropic', CANARY, remember=True)
    assert remembered and kr.data[(SERVICE, 'anthropic')] == CANARY
    again = KeyStore(kr)                                  # "yeniden başlatma"
    assert again.get('anthropic') == CANARY and again.status()['anthropic']['remembered']


def test_remember_without_a_backend_or_with_a_failing_one_keeps_memory_and_warns():
    ks = KeyStore(use_default_backend=False)
    assert ks.set('openai', CANARY, remember=True) == (False, 'anahtar deposu (keyring) yok: '
                                                       'anahtar yalnızca bellekte tutuldu')
    assert ks.get('openai') == CANARY
    failing = KeyStore(MemoryKeyring(fail_set=True))
    remembered, warning = failing.set('openai', CANARY, remember=True)
    assert not remembered and 'yalnızca bellekte' in warning and CANARY not in warning
    assert failing.get('openai') == CANARY


def test_resetting_without_remember_removes_the_persisted_copy_and_clear_forgets():
    kr = MemoryKeyring()
    ks = KeyStore(kr)
    ks.set('openai', CANARY, remember=True)
    ks.set('openai', CANARY, remember=False)
    assert kr.data == {}
    ks.set('anthropic', CANARY, remember=True)
    ks.clear('anthropic')
    assert kr.data == {} and ks.get('anthropic') is None
    assert ks.status()['anthropic'] == {'set': False, 'remembered': False}


@pytest.mark.parametrize('bad', ['', '   ', 'a b', 'x' * 600, 'sk-é', 'sk-\n1'])
def test_bad_keys_are_rejected_without_echoing_them(bad):
    with pytest.raises(ValueError) as exc:
        KeyStore(MemoryKeyring()).set('openai', bad)
    assert bad.strip() == '' or bad not in str(exc.value)


def test_unknown_provider_and_no_leak_in_repr_or_status():
    ks = KeyStore(MemoryKeyring())
    with pytest.raises(ValueError):
        ks.set('gemini', CANARY)
    ks.set('openai', CANARY)
    assert CANARY not in repr(ks) and CANARY not in str(ks.status())
