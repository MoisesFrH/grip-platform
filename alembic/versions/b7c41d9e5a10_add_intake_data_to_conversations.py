"""add intake_data to conversations
 
Revision ID: b7c41d9e5a10
Revises: 2a35a4c50b18
Create Date: 2026-10-16 12:00:00.000000
 
"""
from typing import Sequence, Union
 
from alembic import op
import sqlalchemy as sa
 
 
# revision identifiers, used by Alembic.
revision: str = 'b7c41d9e5a10'
down_revision: Union[str, None] = '2a35a4c50b18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
 
 
def upgrade() -> None:
    op.add_column('conversations', sa.Column('intake_data', sa.JSON(), nullable=True))
 
 
def downgrade() -> None:
    op.drop_column('conversations', 'intake_data')