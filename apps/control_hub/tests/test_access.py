import pytest

from control_hub.hub_core import AccessControl, Decision


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_modes():
    tok = AccessControl('pin', '1234').login('a', '1234').token
    pin, op, lk = AccessControl('pin', '1234'), AccessControl('open'), AccessControl('locked')
    assert pin.check_view(None) is Decision.NEED_LOGIN
    assert pin.check_control('forged') is Decision.NEED_LOGIN
    assert op.check_view(None) is op.check_control(None) is Decision.ALLOWED
    assert lk.check_view(None) is Decision.ALLOWED
    assert lk.check_control(tok) is Decision.LOCKED       # geçerli oturum olsa bile
    with pytest.raises(ValueError):
        AccessControl('bogus')


def test_pin_validation_and_generation():
    for bad in ('12', 'abcd', '1' * 13, '12 34'):
        with pytest.raises(ValueError):
            AccessControl('pin', bad)
    pins = {AccessControl('pin').pin for _ in range(20)}
    assert all(len(p) == 6 and p.isdigit() for p in pins) and len(pins) > 1


def test_login_session_and_expiry():
    clk = Clock()
    ac = AccessControl('pin', '4321', session_ttl_s=100, clock=clk)
    tok = ac.login('c', '4321').token
    assert tok and ac.check_control(tok) is Decision.ALLOWED
    clk.t += 101
    assert ac.check_view(tok) is Decision.NEED_LOGIN
    ac2 = AccessControl('pin', '4321')
    t2 = ac2.login('c', '4321').token
    ac2.logout(t2)
    assert ac2.check_view(t2) is Decision.NEED_LOGIN


def test_rate_limit_per_client_then_recovers():
    clk = Clock()
    ac = AccessControl('pin', '4321', max_failures=3, lockout_s=60, clock=clk)
    assert ac.login('x', '0000').attempts_left == 2
    assert ac.login('x', '0000').attempts_left == 1
    r = ac.login('x', '0000')
    assert r.token is None and r.retry_after_s == 60
    assert ac.login('x', '4321').token is None            # doğru PIN bile kilitliyken geçmez
    assert ac.login('y', '4321').token                    # başka istemci etkilenmez
    clk.t += 61
    assert ac.login('x', '4321').token


def test_global_limit_stops_rotating_clients():
    clk = Clock()
    ac = AccessControl('pin', '4321', max_failures=5, global_max_failures=6, global_lockout_s=300,
                       clock=clk)
    for i in range(6):
        ac.login(f'c{i}', '0000')                         # her istemci 1 hata: istemci limiti yok
    r = ac.login('c99', '4321')
    assert r.token is None and r.retry_after_s > 200


def test_rotate_pin_invalidates_sessions():
    ac = AccessControl('pin', '1111')
    tok = ac.login('a', '1111').token
    new = ac.rotate_pin()
    assert ac.check_view(tok) is Decision.NEED_LOGIN and ac.login('a', new).token
