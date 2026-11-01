"""Helpers for the data-layer tests: SQLite always, PostgreSQL when ROBOT_TEST_PG_URL is set."""
import os

import pytest
from sqlalchemy import create_engine, text

PG_URL = os.environ.get('ROBOT_TEST_PG_URL', '')


def backend_params():
    return ['sqlite', pytest.param('postgres', marks=pytest.mark.skipif(
        not PG_URL, reason='ROBOT_TEST_PG_URL not set'))]


def fresh_url(backend, tmp_path):
    """A database URL with no tables in it."""
    if backend == 'sqlite':
        return f'sqlite:///{tmp_path / "robot.db"}'
    engine = create_engine(PG_URL.replace('postgresql://', 'postgresql+psycopg://', 1))
    with engine.begin() as conn:
        conn.execute(text('DROP SCHEMA public CASCADE'))
        conn.execute(text('CREATE SCHEMA public'))
    engine.dispose()
    return PG_URL
