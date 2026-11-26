"""R9: pages.js sayfa YÜKLEME SIRASI ve pages/robot.js için node testleri.

Gerçek shell.js + pages.js sahte tarayıcıda çalışır; tests/js/page_env.js betikleri tarayıcı gibi
(async=false: eklenme sırasıyla) yürütür ve yükleme olaylarını sırayla günlüğe yazar.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')


def run_node(script):
    r = subprocess.run(['node', str(JS / script), str(WEB)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@needs_node
def test_page_load_order_in_node():
    """Sayfa dosyaları, tembel bağımlılık (stil, sonra betikler; sıralı, tekrarsız), hata yalıtımı, 3B
    yükleyici, adresle açılış ve web/pages/*.js gerçek bildirimleri (dosya var, X_logic.js X.js'ten önce)."""
    assert 'PAGE_LOAD_ORDER_OK' in run_node('page_load_order_check.js')


@needs_node
def test_robot_page_in_node():
    """Robot sayfası /api/pages yolundan yüklenir: kayıt, panel düzeni, durum/robot/olay, iptal, 3B."""
    assert 'ROBOT_PAGE_OK' in run_node('robot_page_check.js')
