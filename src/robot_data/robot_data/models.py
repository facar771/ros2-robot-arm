"""Tables. Images and other large artefacts are never stored in the database: keep the file on
disk and store its path plus metadata (``error_reports.artifact_path`` / ``meta`` columns; a future
``observations`` table follows the same pattern)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), 'postgresql')
BigId = BigInteger().with_variant(Integer(), 'sqlite')   # SQLite only autoincrements INTEGER keys


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One process lifetime of a robot program (cli_moveit, selftest, perception, ...)."""
    __tablename__ = 'runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    meta: Mapped[Dict[str, Any]] = mapped_column(JsonType, default=dict)

    events: Mapped[List['Event']] = relationship(back_populates='run', cascade='all, delete-orphan')


class Event(Base):
    """Structured event emitted through the EventSink port (see docs/INTERFACES.md)."""
    __tablename__ = 'events'

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id', ondelete='CASCADE'), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JsonType, default=dict)

    run: Mapped[Run] = relationship(back_populates='events')


class ErrorReport(Base):
    """A failure worth looking at later: derived from an event (``event_id``) or reported alone."""
    __tablename__ = 'error_reports'

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        BigId, ForeignKey('events.id', ondelete='SET NULL'))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    component: Mapped[str] = mapped_column(String(64), index=True)   # motion, command, selftest...
    code: Mapped[Optional[str]] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text, default='')
    details: Mapped[Dict[str, Any]] = mapped_column(JsonType, default=dict)
    artifact_path: Mapped[Optional[str]] = mapped_column(Text)       # screenshot/log on disk
