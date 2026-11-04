import threading
import time

from control_hub.agent.approvals import ApprovalQueue
from tests.helpers_agent import wait_for


def ask(q, results, tool='go_home'):
    t = threading.Thread(target=lambda: results.append(q.request(tool, f'{tool} -> home', {})))
    t.start()
    return t


def test_approve_and_deny():
    q = ApprovalQueue(timeout_s=5)
    for decision in (True, False):
        out = []
        t = ask(q, out)
        assert wait_for(lambda: q.pending())
        pid = q.pending()[0]['id']
        assert q.decide(pid, decision) is True
        t.join(2)
        assert out == [decision] and q.pending() == []
        assert q.decide(pid, True) is False                    # bitmiş onaya ikinci karar yok


def test_timeout_is_a_rejection():
    events = []
    q = ApprovalQueue(timeout_s=0.2, on_event=lambda k, m, lv: events.append(k))
    started = time.monotonic()
    assert q.request('go_home', 'go_home', {}) is False
    assert time.monotonic() - started < 3
    assert 'agent_approval_expired' in events and q.pending() == []


def test_deny_all_and_close_reject_waiting_and_future_requests():
    q = ApprovalQueue(timeout_s=10)
    out = []
    t = ask(q, out)
    assert wait_for(lambda: q.pending())
    assert q.deny_all('ACİL DUR') == 1
    t.join(2)
    assert out == [False]
    q.close()
    assert q.request('go_home', 'x', {}) is False              # kapalı kuyruk bekletmeden reddeder
    q.open()
    out2 = []
    t2 = ask(q, out2)
    assert wait_for(lambda: q.pending())
    q.close()
    t2.join(2)
    assert out2 == [False]


def test_decision_after_expiry_is_refused():
    q = ApprovalQueue(timeout_s=0.1)
    out = []
    t = ask(q, out)
    assert wait_for(lambda: q.pending())
    pid = q.pending()[0]['id']
    t.join(3)
    assert out == [False] and q.decide(pid, True) is False
