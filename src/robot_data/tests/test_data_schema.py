import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from data_support import backend_params, fresh_url
from robot_data.migrate import current_revision, upgrade_to_head
from robot_data.models import Base
from robot_data.sink import make_engine
from sqlalchemy import inspect


@pytest.fixture(params=backend_params())
def url(request, tmp_path):
    return fresh_url(request.param, tmp_path)


def test_upgrade_creates_all_tables(url):
    upgrade_to_head(url)
    engine = make_engine(url)
    assert {'runs', 'events', 'error_reports', 'alembic_version'} <= set(
        inspect(engine).get_table_names())
    engine.dispose()
    assert current_revision(url) == '0001'


def test_upgrade_is_idempotent(url):
    upgrade_to_head(url)
    upgrade_to_head(url)
    assert current_revision(url) == '0001'


def test_migrations_match_the_models(url):
    """If someone edits models.py without a migration, this fails (autogenerate finds a diff)."""
    upgrade_to_head(url)
    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
