"""Alembic, driven from code so no alembic.ini is needed (the migrations ship with the package)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

MIGRATIONS = Path(__file__).parent / 'migrations'


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option('script_location', str(MIGRATIONS))
    cfg.set_main_option('sqlalchemy.url', url.replace('%', '%%'))   # configparser interpolation
    return cfg


def upgrade_to_head(url: str) -> None:
    command.upgrade(alembic_config(url), 'head')


def current_revision(url: str) -> Optional[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
