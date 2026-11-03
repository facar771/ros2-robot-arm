import json

from control_hub.desktop import settings as st


def test_defaults_are_valid():
    assert st.Settings().problems() == []
    assert st.Settings(fake=True, robot_url='saçma').problems() == []      # sahtede adres önemsiz


def test_validation():
    p = st.Settings(robot_url='ftp://x', port=80, access_mode='x', pin='12').problems()
    assert len(p) == 4
    assert st.Settings(robot_url='127.0.0.1:8080').problems() == []       # şemasız adres kabul
    assert st.Settings(port=True).problems()                               # bool, int değildir
    assert st.Settings(pin='').problems() == [] and st.Settings(pin='123456').problems() == []


def test_save_load_roundtrip_and_pin_privacy(tmp_path):
    path = tmp_path / 'ControlHub' / 'settings.json'
    st.save(st.Settings(robot_url='http://127.0.0.1:9000', port=9100, pin='111111'), path)
    got = st.load(path)
    assert got.robot_url == 'http://127.0.0.1:9000' and got.port == 9100
    assert got.pin == ''                                                   # hatırlama kapalı: PIN yazılmaz
    assert '111111' not in path.read_text(encoding='utf-8')
    st.save(st.Settings(pin='222222', remember_pin=True), path)
    assert st.load(path).pin == '222222'
    assert not list(path.parent.glob('*.tmp'))                             # geçici dosya kalmaz


def test_load_is_forgiving(tmp_path):
    p = tmp_path / 's.json'
    assert st.load(p) == st.Settings()                                     # yok
    p.write_text('{bozuk', encoding='utf-8')
    assert st.load(p) == st.Settings()                                     # bozuk
    p.write_text('[1,2]', encoding='utf-8')
    assert st.load(p) == st.Settings()                                     # yanlış tür
    p.write_text(json.dumps({'port': 'sekiz', 'fake': 1, 'robot_url': 'http://a:1', 'yeni': 5}),
                 encoding='utf-8')
    got = st.load(p)
    assert got.port == 8765 and got.fake is False and got.robot_url == 'http://a:1'


def test_bridge_settings_validation():
    ok = st.Settings(serial_port='COM5')
    assert ok.problems() == []
    assert st.Settings().problems() == []                                   # köprü yoksa alanlar önemsiz
    assert st.Settings(serial_port='COM5', serial_baud=5).problems()
    assert st.Settings(serial_port='COM5', bridge_port=80).problems()
    assert st.Settings(serial_port='COM5', bridge_port=8765, port=8765).problems()   # Hub portuyla çakışma
    assert st.Settings(serial_port='', bridge_port=1).problems() == []
