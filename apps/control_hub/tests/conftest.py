import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, FakeBackend, Hub, load_scenarios, load_ui_config
from control_hub.server import create_app

CONFIG = Path(__file__).resolve().parents[1] / 'config'


@pytest.fixture
def scenarios():
    return load_scenarios(CONFIG / 'scenarios.yaml')


@pytest.fixture
def ui(scenarios):
    return load_ui_config(CONFIG / 'ui.config.json', scenarios)


@pytest.fixture
def cancel():
    return threading.Event()


def make_hub(scenarios, ui, mode='open', pin='123456', scale=0.02):
    return Hub(FakeBackend(time_scale=scale), scenarios, ui, AccessControl(mode, pin))


@pytest.fixture
def hub(scenarios, ui):
    h = make_hub(scenarios, ui)
    yield h
    h.close()


@pytest.fixture
def make_client(scenarios, ui):
    made = []

    def _make(mode='open', scale=0.02, base='http://localhost'):
        h = make_hub(scenarios, ui, mode, scale=scale)
        made.append(h)
        return TestClient(create_app(h), base_url=base), h

    yield _make
    for h in made:
        h.close()


J = {'content-type': 'application/json'}


# CI log'u okunamadığı için: başarısız testlerin özetini son satırlara (annotation'a sığacak şekilde) yaz.
_FAILED = []


def pytest_runtest_logreport(report):
    if report.failed:
        _FAILED.append((report.nodeid, report.when, (report.longreprtext or '')))


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter):
    if not _FAILED:
        return
    terminalreporter.write_sep('=', 'BAŞARISIZ TESTLER (özet)')
    for nodeid, when, text in _FAILED[:8]:
        lines = [ln for ln in text.splitlines() if ln.startswith('E ')]
        reason = ' | '.join(ln[2:].strip() for ln in lines[:2])[:230] or text.strip().splitlines()[-1][:230]
        terminalreporter.write_line(f'{nodeid} [{when}]: {reason}')
