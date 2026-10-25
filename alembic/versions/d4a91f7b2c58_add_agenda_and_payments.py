"""agenda and payments: appointment duration, therapists, price list, payments

Revision ID: d4a91f7b2c58
Revises: c3d82e6f1a47
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4a91f7b2c58"
down_revision = "c3d82e6f1a47"
branch_labels = None
depends_on = None


def _uuid_pk():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _tenant_fk():
    return sa.Column(
        "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.add_column("appointments", sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"))

    op.create_table(
        "therapists",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_therapist_tenant_name"),
    )
    op.create_index("ix_therapists_tenant_id", "therapists", ["tenant_id"])

    op.create_table(
        "price_items",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_price_item_tenant_name"),
    )
    op.create_index("ix_price_items_tenant_id", "price_items", ["tenant_id"])

    op.create_table(
        "payments",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column(
            "appointment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("method", sa.String(16), nullable=False, server_default="TRANSFER"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(120), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("appointment_id", name="uq_payment_appointment"),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_appointment_id", "payments", ["appointment_id"])
    op.create_index("ix_payments_status", "payments", ["status"])


def downgrade() -> None:
    op.drop_table("payments")
    op.drop_table("price_items")
    op.drop_table("therapists")
    op.drop_column("appointments", "duration_minutes")