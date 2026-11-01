"""``event_sink_from_env``: the one function robot programs call."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping, Optional

from robot_core.ports import EventSink, NullEventSink

ENV_VAR = 'ROBOT_DB_URL'
log = logging.getLogger('robot_data')


def event_sink_from_env(source: str, meta: Optional[Dict[str, Any]] = None, *,
                        environ: Optional[Mapping[str, str]] = None) -> EventSink:
    """A DatabaseEventSink if ROBOT_DB_URL is set, otherwise a NullEventSink. Never raises.

    No connection is made here: the schema is migrated and the run row is created on the first
    event, so a database that starts a few seconds after the robot program is fine. The caller
    should call ``close()`` (if present) on shutdown to stamp the end of the run.
    """
    url = (environ if environ is not None else os.environ).get(ENV_VAR, '').strip()
    if not url:
        return NullEventSink()
    try:
        from robot_data.sink import DatabaseEventSink  # imports SQLAlchemy: only when needed
        return DatabaseEventSink(url, source, meta)
    except Exception as exc:    # noqa: BLE001 - bad URL, missing driver/SQLAlchemy ...
        log.error('%s ayarlı ama veritabanı kullanılamıyor (%s: %s); olaylar kaydedilmeyecek',
                  ENV_VAR, type(exc).__name__, exc)
        return NullEventSink()


def close_sink(sink: EventSink) -> None:
    """Stamp the end of the run and release connections (no-op for sinks without ``close``)."""
    close = getattr(sink, 'close', None)
    if close is not None:
        try:
            close()
        except Exception as exc:    # noqa: BLE001 - shutdown must not fail because of the database
            log.warning('olay alıcısı kapatılamadı: %s', exc)
