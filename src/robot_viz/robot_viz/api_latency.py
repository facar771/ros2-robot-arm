"""Robot API response times while a jog is running (N6 diagnostic, no ROS needed).

    API_LATENCY=1 docker/run_selftest.sh gazebo-jog      # runs this instead of jog_check
    ros2 run robot_viz api_latency --url http://127.0.0.1:8080 --rounds 40 --load 2

Per round: a jog is held (POST /api/jog at 20 Hz on its own connection, like a browser tab); while
it runs the check times ``POST /api/jobs`` (must answer 409, jog excludes jobs), ``GET /api/state``
and ``POST /api/jog`` itself; then ``POST /api/stop`` (E-stop) is timed, and ``POST /api/reset``
unlocks again. Prints p50 / p99 / max per request and exits 1 if any request exceeds --limit s or
fails to answer (a hang is a failure, never retried).
"""
import argparse
import json
import multiprocessing
import sys
import time

from robot_viz.jog_check import JogClient
from robot_viz.stop_check import Api

TWIST = [0.1, 0, 0, 0, 0, 0]


def _burn(stop):
    while not stop.is_set():
        sum(i * i for i in range(20000))


def pct(values, q):
    if not values:
        return float('nan')
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def timed(fn):
    t0 = time.monotonic()
    out = fn()
    return time.monotonic() - t0, out


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--rounds', type=int, default=30)
    parser.add_argument('--load', type=int, default=0, help='CPU burner processes')
    parser.add_argument('--limit', type=float, default=1.0, help='max allowed response time, s')
    parser.add_argument('--timeout', type=float, default=15.0)
    opts, _ = parser.parse_known_args(args)
    api, jog, probe = Api(opts.url), JogClient(opts.url), JogClient(opts.url)  # 2 connections
    stop = multiprocessing.Event()
    burners = [multiprocessing.Process(target=_burn, args=(stop,), daemon=True)
               for _ in range(opts.load)]
    for p in burners:
        p.start()
    lat = {'POST /api/jobs (jog active)': [], 'GET /api/state': [], 'POST /api/jog': [],
           'POST /api/stop (E-stop)': []}
    stop_times, hung, wrong = [], 0, 0
    try:
        for _ in range(opts.rounds):
            api.post('/api/reset', {}, timeout=opts.timeout)
            jog.hold(TWIST)
            time.sleep(0.4)
            for _ in range(5):
                dt, (status, _, _b) = timed(
                    lambda: api.post('/api/jobs', {'command': 'home'}, timeout=opts.timeout))
                lat['POST /api/jobs (jog active)'].append(dt)
                hung += status is None
                wrong += status not in (409, None)
                dt, _s = timed(lambda: api.http.state())
                lat['GET /api/state'].append(dt)
                dt, _r = timed(lambda: probe.post({'twist': TWIST}))
                lat['POST /api/jog'].append(dt)
                time.sleep(0.05)
            dt, (status, _, body) = timed(
                lambda: api.post('/api/stop', {'reason': 'api_latency'}, timeout=opts.timeout))
            lat['POST /api/stop (E-stop)'].append(dt)
            hung += status is None
            if isinstance(body, dict) and body.get('stop_time_s') is not None:
                stop_times.append(body['stop_time_s'])
            jog.stop_sending()
    finally:
        stop.set()
        jog.release()
        api.post('/api/reset', {}, timeout=opts.timeout)
    print(f'\n{"istek":32s} {"n":>4s} {"p50 ms":>8s} {"p99 ms":>8s} {"max ms":>8s}')
    worst = 0.0
    for name, values in lat.items():
        worst = max([worst] + values)
        print(f'{name:32s} {len(values):4d} {pct(values, .5) * 1e3:8.1f} '
              f'{pct(values, .99) * 1e3:8.1f} {max(values, default=float("nan")) * 1e3:8.1f}')
    if stop_times:
        print(f'E-stop stop_time_s (API): p50 {pct(stop_times, .5):.3f}  max {max(stop_times):.3f}')
    ok = hung == 0 and wrong == 0 and worst <= opts.limit
    print(json.dumps({'yanıtsız': hung, 'beklenmeyen_durum': wrong, 'en_yavaş_s': round(worst, 3)}))
    print('[PASS]' if ok else '[FAIL]', f'tüm istekler <= {opts.limit} s ve yanıtlı')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
