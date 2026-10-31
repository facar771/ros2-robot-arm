"""initial schema: runs, events, error_reports

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(JSONB(), 'postgresql')
BIG_ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source', sa.String(64), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('meta', JSON_TYPE, nullable=False),
    )
    op.create_index('ix_runs_source', 'runs', ['source'])
    op.create_table(
        'events',
        sa.Column('id', BIG_ID, primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('runs.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(64), nullable=False),
        sa.Column('payload', JSON_TYPE, nullable=False),
    )
    op.create_index('ix_events_run_id', 'events', ['run_id'])
    op.create_index('ix_events_ts', 'events', ['ts'])
    op.create_index('ix_events_kind', 'events', ['kind'])
    op.create_table(
        'error_reports',
        sa.Column('id', BIG_ID, primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('runs.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('event_id', BIG_ID, sa.ForeignKey('events.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('component', sa.String(64), nullable=False),
        sa.Column('code', sa.String(128), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('details', JSON_TYPE, nullable=False),
        sa.Column('artifact_path', sa.Text(), nullable=True),
    )
    op.create_index('ix_error_reports_run_id', 'error_reports', ['run_id'])
    op.create_index('ix_error_reports_ts', 'error_reports', ['ts'])
    op.create_index('ix_error_reports_component', 'error_reports', ['component'])


def downgrade() -> None:
    op.drop_table('error_reports')
    op.drop_table('events')
    op.drop_table('runs')
