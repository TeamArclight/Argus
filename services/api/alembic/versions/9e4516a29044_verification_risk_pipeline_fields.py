"""verification_risk_pipeline_fields

Revision ID: 9e4516a29044
Revises: 7f3416a29033
Create Date: 2026-09-07 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e4516a29044'
down_revision: Union[str, None] = '7f3416a29033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('risk_signals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reason_code', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('verification_ids', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('source_mode', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('metadata_json', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('risk_signals', schema=None) as batch_op:
        batch_op.drop_column('metadata_json')
        batch_op.drop_column('source_mode')
        batch_op.drop_column('verification_ids')
        batch_op.drop_column('reason_code')
