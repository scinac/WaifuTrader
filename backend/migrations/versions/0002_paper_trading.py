"""Paper trading: balances, risk limits, orders, fills, positions, watchlists, drawings.

Revision ID: 0002
"""
from alembic import op

from tt.db.models import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ["paper_balances", "risk_limits", "orders", "fills", "positions", "watchlists", "drawings"]


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[n] for n in TABLES])


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables[n] for n in reversed(TABLES)])
