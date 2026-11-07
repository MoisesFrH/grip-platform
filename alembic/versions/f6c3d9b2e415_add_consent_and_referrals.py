"""consent records (QR signing) and patient referrals

Revision ID: f6c3d9b2e415
Revises: e5b2c8a91d34
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6c3d9b2e415"
down_revision = "e5b2c8a91d34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signer_name", sa.String(200), nullable=True),
        sa.Column("signer_relation", sa.String(80), nullable=True),
        sa.Column("child_name", sa.String(200), nullable=True),
        sa.Column("text_version", sa.String(32), nullable=True),
        sa.Column("text_snapshot", sa.Text(), nullable=True),
        sa.Column("signature_png", sa.LargeBinary(), nullable=True),
        sa.UniqueConstraint("contact_id", name="uq_consent_records_contact_id"),
    )
    op.create_index("ix_consent_records_tenant_id", "consent_records", ["tenant_id"])
    op.create_index("ix_consent_records_token", "consent_records", ["token"], unique=True)

    op.create_table(
        "referrals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("referred_contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("referrer_contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(300), nullable=True),
        sa.Column("reward_status", sa.String(20), nullable=False, server_default="NONE"),
        sa.Column("discount_note", sa.String(300), nullable=True),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_by", sa.String(120), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("referred_contact_id", name="uq_referrals_referred_contact_id"),
    )
    op.create_index("ix_referrals_tenant_id", "referrals", ["tenant_id"])
    op.create_index("ix_referrals_referrer_contact_id", "referrals", ["referrer_contact_id"])


def downgrade() -> None:
    op.drop_table("referrals")
    op.drop_table("consent_records")