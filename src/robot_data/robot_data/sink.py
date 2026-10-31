"""DatabaseEventSink: implements robot_core.ports.EventSink on top of the database.

Contract (from the port): ``emit`` NEVER raises and never blocks the robot for long. A database
that is down must not stop a motion, so failures are logged, the event is dropped, and further
attempts are skipped for ``retry_after`` seconds (circuit breaker) before trying again. There is no
disk buffer: events emitted while the database is unreachable are lost (``dropped`` counts them).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy import create_engine, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from robot_data.migrate import upgrade_to_head
from robot_data.models import ErrorReport, Event, Run

log = logging.getLogger('robot_data')


def normalize_url(url: str) -> str:
    """``postgresql://`` -> the psycopg 3 driver we ship (SQLAlchemy would pick psycopg2)."""
    for prefix in ('postgresql://', 'postgres://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg://' + url[len(prefix):]
    return url


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    kwargs: Dict[str, Any] = {'pool_pre_ping': True}
    if url.startswith('postgresql'):
        kwargs['connect_args'] = {'connect_timeout': 3}
    return create_engine(url, **kwargs)


def to_json(value: Any) -> Any:
    """Make a payload storable as JSON: tuples -> lists, unknown objects -> str, NaN/inf -> None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json(v) for v in value]
    if hasattr(value, 'item'):          # numpy scalar
        try:
            return to_json(value.item())
        except Exception:               # noqa: BLE001 - fall through to str()
            pass
    return str(value)


def error_report_for(kind: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Failure events that also get an error_reports row (None for everything else)."""
    if kind == 'motion_result' and payload.get('ok') is False:
        code = payload.get('code_name') or (None if payload.get('code') is None
                                            else str(payload['code']))
        return {'component': 'motion', 'code': code,
                'message': payload.get('message') or code or 'hareket başarısız'}
    if kind == 'command_rejected':
        reason = payload.get('reason') or f"geçersiz komut: {payload.get('line', '')}"
        return {'component': 'command', 'code': 'rejected', 'message': reason}
    if kind == 'selftest_check' and payload.get('ok') is False:
        return {'component': 'selftest', 'code': str(payload.get('name', ''))[:128],
                'message': payload.get('detail') or str(payload.get('name', ''))}
    if kind == 'error':
        return {'component': str(payload.get('component', 'unknown')),
                'code': payload.get('code'), 'message': str(payload.get('message', ''))}
    return None


class DatabaseEventSink:
    def __init__(self, url: str, source: str, meta: Optional[Dict[str, Any]] = None, *,
                 retry_after: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 migrate: bool = True):
        self._url = normalize_url(url)
        self._source, self._meta = source, to_json(meta or {})
        self._retry_after, self._clock, self._migrate = retry_after, clock, migrate
        self._engine = make_engine(self._url)     # lazy: no connection is made here
        self._lock = threading.Lock()
        self._ready = False
        self._run_id: Optional[int] = None
        self._down_until = 0.0
        self._closed = False
        self.dropped = 0

    @property
    def run_id(self) -> Optional[int]:
        return self._run_id

    # ---- EventSink port ---------------------------------------------------------------------
    def emit(self, kind: str, payload: Dict[str, Any]) -> None:
        try:
            with self._lock:
                if self._closed:
                    return
                if self._clock() < self._down_until:
                    self.dropped += 1
                    return
                self._write(kind, to_json(payload if isinstance(payload, dict)
                                          else {'value': payload}))
        except Exception as exc:    # noqa: BLE001 - the port promises never to raise
            self._fail(exc)

    def close(self) -> None:
        """End the run and release connections. Safe to call twice; never raises."""
        try:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                if self._run_id is not None and self._clock() >= self._down_until:
                    with Session(self._engine) as session, session.begin():
                        session.execute(update(Run).where(Run.id == self._run_id)
                                        .values(ended_at=datetime.now(timezone.utc)))
        except Exception as exc:    # noqa: BLE001
            log.warning('robot_data: run kapatılamadı: %s', exc)
        finally:
            try:
                self._engine.dispose()
            except Exception:       # noqa: BLE001
                pass

    # ---- internals --------------------------------------------------------------------------
    def _write(self, kind: str, payload: Dict[str, Any]) -> None:
        if not self._ready:
            if self._migrate:
                upgrade_to_head(self._url)
            self._ready = True
        with Session(self._engine) as session, session.begin():
            if self._run_id is None:
                run = Run(source=self._source, meta=self._meta)
                session.add(run)
                session.flush()
                self._run_id = run.id
            event = Event(run_id=self._run_id, kind=kind[:64], payload=payload)
            session.add(event)
            report = error_report_for(kind, payload)
            if report is not None:
                session.flush()
                session.add(ErrorReport(run_id=self._run_id, event_id=event.id, details=payload,
                                        **report))

    def _fail(self, exc: Exception) -> None:
        self._down_until = self._clock() + self._retry_after
        self.dropped += 1
        log.error('robot_data: veritabanına yazılamadı (%s: %s); %.0f s sonra tekrar denenecek',
                  type(exc).__name__, str(exc).splitlines()[0] if str(exc) else '',
                  self._retry_after)
