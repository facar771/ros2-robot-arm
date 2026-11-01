"""ROS-independent data layer (SQLAlchemy 2 + Alembic; SQLite for tests, PostgreSQL in production).

Importing this package does not import SQLAlchemy: ``event_sink_from_env`` loads it only when
``ROBOT_DB_URL`` is set, so robots without the database dependencies keep working.
"""
from robot_data.factory import ENV_VAR, close_sink, event_sink_from_env

__all__ = ['ENV_VAR', 'close_sink', 'event_sink_from_env']
