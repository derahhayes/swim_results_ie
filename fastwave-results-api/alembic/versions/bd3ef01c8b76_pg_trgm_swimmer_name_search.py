"""pg_trgm swimmer name search

Revision ID: bd3ef01c8b76
Revises: 8aa9ffeee218
Create Date: 2026-07-21 15:44:39.102585

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bd3ef01c8b76'
down_revision: Union[str, None] = '8aa9ffeee218'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate can't produce either of these (extensions and expression
    # indexes aren't reflected the same way plain tables/columns are).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        'CREATE INDEX ix_swimmers_name_trgm ON swimmers '
        'USING gin (("lastName" || \' \' || "firstName") gin_trgm_ops)'
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_swimmers_name_trgm")
    # Not dropping the extension itself: it's server/database-wide, cheap to
    # leave installed, and dropping it would break any other trgm index
    # that happened to exist for an unrelated reason.
