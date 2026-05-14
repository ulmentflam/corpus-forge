"""${message}  # noqa: D

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, tuple[str, ...], None] = ${repr(branch_labels)}
depends_on: Union[str, tuple[str, ...], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Apply forward migrations."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Apply reverse migrations."""
    ${downgrades if downgrades else "pass"}
