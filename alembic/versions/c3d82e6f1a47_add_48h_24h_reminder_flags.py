"""add per-reminder sent flags (48h / 24h) to appointments

Revision ID: c3d82e6f1a47
Revises: b7c41d9e5a10
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d82e6f1a47"
down_revision = "b7c41d9e5a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("reminder_48h_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("reminder_24h_sent_at", sa.DateTime(timezone=True), nullable=True))
    # Appointments that already got the old single day-before reminder must
    # not receive another one when the new job starts running.
    op.execute(
        "UPDATE appointments SET reminder_24h_sent_at = reminder_sent_at, "
        "reminder_48h_sent_at = reminder_sent_at WHERE reminder_sent_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("appointments", "reminder_24h_sent_at")
    op.drop_column("appointments", "reminder_48h_sent_at")