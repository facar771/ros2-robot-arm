"""Ortak yardımcılar (P4 testleri): depo profil dosyalarının özeti, veri klasörü ve kaynak fixture'ları."""
import hashlib
import math  # noqa: F401
from pathlib import Path

import pytest

pytest.importorskip('numpy')
pytest.importorskip('yaml')

from control_hub.hub_core import profile_view as pv  # noqa: E402
from tests.conftest import J  # noqa: E402,F401

REPO = Path(__file__).resolve().parents[3]
PROFILE_ROOT = REPO / 'src' / 'robot_description' / 'profile'


def repo_hashes():
    return {str(p.relative_to(PROFILE_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(PROFILE_ROOT.rglob('*')) if p.is_file()}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / 'hubdata'
    monkeypatch.setenv('HUB_DATA_DIR', str(d))
    monkeypatch.delenv('HUB_PROFILE_DIR', raising=False)
    return d


@pytest.fixture
def src(data_dir):
    return pv.load_source()


def fields_of(issues):
    return {i.field for i in issues if i.severity == 'error'}


def box(x=(60, 220), y=(-100, 100), z=(20, 300)):
    return {'safety.x_min_mm': x[0], 'safety.x_max_mm': x[1], 'safety.y_min_mm': y[0],
            'safety.y_max_mm': y[1], 'safety.z_min_mm': z[0], 'safety.z_max_mm': z[1]}


def pot(n='j', ch=1, lo=100, hi=900, a0=-90, a1=90):
    return {f'pot.{n}.channel': ch, f'pot.{n}.raw_min': lo, f'pot.{n}.raw_max': hi,
            f'pot.{n}.angle_at_min_deg': a0, f'pot.{n}.angle_at_max_deg': a1}


def login(c):
    assert c.post('/api/login', json={'pin': '123456'}, headers=J).status_code == 200
