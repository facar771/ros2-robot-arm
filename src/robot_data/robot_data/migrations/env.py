"""Alembic environment (online mode only; the URL comes from robot_data.migrate)."""
from alembic import context
from sqlalchemy import engine_from_config, pool, text

config = context.config
target_metadata = None   # revisions are explicit; tests compare them with robot_data.models

PG_MIGRATION_LOCK = 7351001   # serialises concurrent first starts (selftest + perception + ...)


def run_migrations_online() -> None:
    engine = engine_from_config(config.get_section(config.config_ini_section, {}),
                                prefix='sqlalchemy.', poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            if connection.dialect.name == 'postgresql':
                connection.execute(text(f'SELECT pg_advisory_xact_lock({PG_MIGRATION_LOCK})'))
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError('offline migrations are not supported')
run_migrations_online()
