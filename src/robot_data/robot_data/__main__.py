"""``python3 -m robot_data summary [--min-events N]``: what is in the database.

Applies pending migrations first, so it also works as a connectivity check. Exit code 1 if the
database is unreachable or holds fewer events than --min-events.
"""
from __future__ import annotations

import argparse
import os
import sys

from robot_data.factory import ENV_VAR


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python3 -m robot_data')
    sub = parser.add_subparsers(dest='cmd', required=True)
    summary = sub.add_parser('summary', help='row counts, runs and error reports')
    summary.add_argument('--min-events', type=int, default=0)
    args = parser.parse_args(argv)

    url = os.environ.get(ENV_VAR, '').strip()
    if not url:
        print(f'{ENV_VAR} ayarlı değil')
        return 1
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from robot_data.migrate import upgrade_to_head
    from robot_data.models import ErrorReport, Event, Run
    from robot_data.sink import make_engine, normalize_url
    try:
        upgrade_to_head(normalize_url(url))
        engine = make_engine(url)
        with Session(engine) as session:
            runs = session.scalars(select(Run).order_by(Run.id)).all()
            n_events = session.scalar(select(func.count()).select_from(Event))
            n_errors = session.scalar(select(func.count()).select_from(ErrorReport))
            print(f'runs: {len(runs)}  events: {n_events}  error_reports: {n_errors}')
            for run in runs[-10:]:
                count = session.scalar(select(func.count()).select_from(Event)
                                       .where(Event.run_id == run.id))
                state = 'bitti' if run.ended_at else 'açık'
                print(f'  run {run.id:>4} {run.source:<22} {count:>5} olay  [{state}]')
            for rep in session.scalars(select(ErrorReport).order_by(ErrorReport.id.desc())
                                       .limit(5)):
                print(f'  hata: [{rep.component}] {rep.code}: {rep.message[:100]}')
        engine.dispose()
    except Exception as exc:    # noqa: BLE001
        print(f'veritabanına erişilemedi: {type(exc).__name__}: {exc}')
        return 1
    if n_events < args.min_events:
        print(f'beklenen en az {args.min_events} olay, bulunan {n_events}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
