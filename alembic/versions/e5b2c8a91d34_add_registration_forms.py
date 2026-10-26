"""registration forms (Patronato / SeNaSa / Privado) filled in person

Revision ID: e5b2c8a91d34
Revises: d4a91f7b2c58
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5b2c8a91d34"
down_revision = "d4a91f7b2c58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_forms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "contact_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("form_type", sa.String(16), nullable=False),
        sa.Column("child_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("answers", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("filled_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_registration_forms_tenant_id", "registration_forms", ["tenant_id"])
    op.create_index("ix_registration_forms_contact_id", "registration_forms", ["contact_id"])


def downgrade() -> None:
    op.drop_table("registration_forms")