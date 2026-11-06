"""End-to-end check of ``GET /api/model`` (L9b). Run while the mock stack
(``ros2 launch robot_moveit_config demo.launch.py use_rviz:=false``) and
``ros2 launch robot_viz web_view.launch.py commands:=true mode:=mock use_sim_time:=false`` are up
(``run_selftest model``):

    ros2 run robot_viz model_check [--url http://127.0.0.1:8080]

1. the served model is the installed ``view_model.json`` minus its ``_`` notes, version 1;
2. its ``joints`` names equal the names in the live ``/api/state`` ``joints`` (the robot's own);
3. the body leaks no file path / repository path / secret; no-store; query and credential
   headers change nothing; POST/PUT/DELETE are 405; a foreign Host gets 403;
4. emergency stop: the model is still served while the e-stop is latched, movement is still 423,
   and after ``/api/reset`` everything is as before;
5. a SECOND web_view with a corrupt file, with a file whose joint names differ and with a missing
   file starts anyway: ``/api/model`` is a clear 503 (reason in the text, no path), ``/api/state``
   works.
Exit code 0 only if every check passed.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from robot_viz.stop_check import wait_for
from robot_viz.viz_check import Http

MIN_CHECKS = 13
BAD_PORT = 8082


def run(base: str) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}', flush=True)
        return bool(ok)

    http = Http(base)

    def get(path, headers=None, method='GET'):
        status, hdrs, body = http.request(method, path, headers or {}, None, 20.0)
        try:
            return status, hdrs, json.loads(body) if body else None, body
        except ValueError:
            return status, hdrs, None, body

    def post(path, payload=None):
        body = json.dumps(payload if payload is not None else {}).encode()
        status, hdrs, data = http.request('POST', path, {'Content-Type': 'application/json'},
                                          body, 60.0)
        try:
            return status, json.loads(data) if data else None
        except ValueError:
            return status, None

    share = Path(get_package_share_directory('robot_description'))
    installed = json.loads((share / 'profile' / 'generated' / 'view_model.json')
                           .read_text(encoding='utf-8'))
    wait_for(lambda: get('/api/model')[0] is not None, 60.0, 0.5)
    status, hdrs, model, raw = get('/api/model')
    public = {k: v for k, v in installed.items() if not k.startswith('_')}
    record('1 GET /api/model: 200, kurulu view_model.json (altçizgili notlar hariç), sürüm 1',
           status == 200 and model == public and model.get('view_model_version') == 1
           and not any(k.startswith('_') for k in model))
    record('1 başlıklar: JSON, no-store',
           'json' in hdrs.get('Content-Type', '') and 'no-store' in hdrs.get('Cache-Control', ''))

    state = get('/api/state')[2] or {}
    live = [j['name'] for j in state.get('joints', [])]
    record('2 modelin eklem adları = canlı /api/state joints adları (robotun kendi adları)',
           bool(live) and [j['name'] for j in model['joints']] == live, f'{live}')

    text = raw.decode()
    leaks = [n for n in ('/home', '/workspace', '/opt', '/tmp', 'install/', 'src/', '.yaml',
                         '.json', str(share), 'ÜRETİLDİ') if n in text]
    record('3 gövdede dosya/depo yolu ya da not sızıntısı yok', not leaks, str(leaks))
    s2, _, _, raw2 = get('/api/model?pin=SECRET&x=/etc/passwd',
                         {'Authorization': 'Bearer SECRET', 'Cookie': 'session=SECRET'})
    record('3 sorgu ve kimlik başlıkları yok sayılır, yansıtılmaz',
           s2 == 200 and raw2 == raw and b'SECRET' not in raw2)
    methods = {}
    for m in ('POST', 'PUT', 'DELETE'):
        methods[m] = http.request(m, '/api/model', {'Content-Type': 'application/json'},
                                  b'{}' if m != 'DELETE' else None, 20.0)[0]
    record('3 POST/PUT/DELETE 405 (salt okunur)', set(methods.values()) == {405}, str(methods))
    record('3 yabancı Host 403', get('/api/model', {'Host': 'evil.example'})[0] == 403)

    allowed = (state.get('commands') or {}).get('allowed') or []
    record('4 hazırlık: komutlar açık (beyaz liste dolu)', bool(allowed), str(allowed)[:80])
    s_stop = post('/api/stop')[0]
    during = get('/api/model')
    s_cmd = post('/api/command', {'command': 'home'})[0]
    record('4 acil dur kilitliyken model hâlâ 200, hareket 423',
           s_stop == 200 and during[0] == 200 and during[2] == public and s_cmd == 423,
           f'stop={s_stop} model={during[0]} cmd={s_cmd}')
    s_reset = post('/api/reset')[0]
    after = get('/api/model')
    record('4 reset sonrası model değişmedi', s_reset == 200 and after[2] == public,
           f'reset={s_reset}')

    good = json.dumps(installed)
    names = json.loads(good)
    names['joints'][1]['name'] = 'joint_two'
    cases = {
        'bozuk JSON': ('{kırık', 'geçerli JSON değil'),
        'eklem adı farklı': (json.dumps(names), 'eşleşmiyor'),
        'dosya yok': (None, 'bulunamadı'),
    }
    with tempfile.TemporaryDirectory() as tmp:
        for label, (content, reason) in cases.items():
            path = Path(tmp) / f'secret_dir_{label[:3]}' / 'view_model.json'
            if content is not None:
                path.parent.mkdir()
                path.write_text(content, encoding='utf-8')
            env = dict(os.environ, ROBOT_VIEW_MODEL=str(path))
            proc = subprocess.Popen(
                ['ros2', 'run', 'robot_viz', 'web_view', '--ros-args', '-p', f'port:={BAD_PORT}',
                 '-p', 'commands:=false', '-p', 'use_sim_time:=false',
                 '-r', '__node:=web_view_model_check'],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            try:
                bad = Http(f'http://127.0.0.1:{BAD_PORT}')
                up = wait_for(lambda h=bad: h.request('GET', '/api/state', {}, None, 5.0)[0] == 200,
                              60.0, 0.5)
                status, _, body = bad.request('GET', '/api/model', {}, None, 10.0)
                message = json.loads(body).get('error', '') if status == 503 else ''
                record(f'5 {label}: Robot API açılır, /api/model net 503 (neden metinde)',
                       bool(up) and status == 503 and reason in message
                       and tmp not in message and 'secret_dir' not in message, message[:120])
            finally:
                try:
                    os.killpg(proc.pid, 2)
                    proc.wait(15)
                except Exception:    # noqa: BLE001
                    os.killpg(proc.pid, 9)
            time.sleep(1.0)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts = parser.parse_args(args)
    results = run(opts.url)
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= MIN_CHECKS else 1)


if __name__ == '__main__':
    main()
