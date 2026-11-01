"""Add optional per-tenant first-contact welcome text.

Revision ID: 0002
Revises: 0001_platform
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_settings", sa.Column("welcome_message", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_welcome_message_length", "agent_settings", "char_length(welcome_message) <= 1000"
    )


def downgrade() -> None:
    op.drop_constraint("ck_welcome_message_length", "agent_settings", type_="check")
    op.drop_column("agent_settings", "welcome_message")
