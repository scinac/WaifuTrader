"""Phase 1 schema: users, sessions, invites, broker accounts, instruments, bars, audit log.

The initial schema is created from the phase-1 models. Later phases must add
explicit, reviewed migrations (alembic revision --autogenerate, then edit).

Revision ID: 0001
"""
from alembic import op

from tt.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

PHASE1_TABLES = [
    "users", "sessions", "invites", "broker_accounts", "instruments", "bars", "audit_log",
]


def upgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in PHASE1_TABLES]
    Base.metadata.create_all(bind, tables=tables)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute(
            "SELECT create_hypertable('bars', 'ts', chunk_time_interval => INTERVAL '7 days', "
            "if_not_exists => TRUE)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in reversed(PHASE1_TABLES)]
    Base.metadata.drop_all(bind, tables=tables)
