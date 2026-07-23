"""gender mixed enum value

Revision ID: cea421c67009
Revises: 5fca10e63ac1
Create Date: 2026-07-23 20:02:51.641648

Adds 'X' (Gender.MIXED) to the Postgres "gender" enum type, so mixed-
gender relay events (medley/freestyle relays swum as mixed teams, common
at development/community meets) can be mapped instead of rejected as
event_unmapped. See KNOWN_ISSUES.md.

ADD VALUE runs fine inside a transaction on modern Postgres (12+) as long
as the new value isn't *used* in the same transaction - this migration
only adds it, nothing else, so op.execute() here (inside Alembic's normal
per-upgrade transaction) is safe without any special autocommit handling.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cea421c67009'
down_revision: Union[str, None] = '5fca10e63ac1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE gender ADD VALUE IF NOT EXISTS 'X'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE - removing an enum value
    # requires recreating the type (and every column using it) from
    # scratch, which is out of proportion to what a downgrade needs to
    # guarantee here. Left as a no-op: harmless (an unused enum value)
    # unless some future upgrade also needs a *different* type named
    # "gender" with fewer values, which nothing currently does.
    pass
