"""add_idempotency_records

Revision ID: 9f5627b30055
Revises: 9e4516a29044
Create Date: 2026-09-07 22:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f5627b30055'
down_revision: Union[str, None] = '9e4516a29044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'idempotency_records',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('principal_id', sa.String(), nullable=False),
        sa.Column('resource_type', sa.String(), nullable=False),
        sa.Column('resource_id', sa.String(), nullable=False),
        sa.Column('operation', sa.String(), nullable=False),
        sa.Column('request_hash', sa.String(), nullable=False),
        sa.Column('job_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='PROCESSING'),
        sa.Column('response_code', sa.Integer(), nullable=True),
        sa.Column('response_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('principal_id', 'resource_type', 'resource_id', 'operation', 'key', name='uq_idempotency_scope_key')
    )
    op.create_index(op.f('ix_idempotency_records_key'), 'idempotency_records', ['key'], unique=False)
    op.create_index(op.f('ix_idempotency_records_principal_id'), 'idempotency_records', ['principal_id'], unique=False)

    with op.batch_alter_table('job_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('seq', sa.Integer(), autoincrement=True, nullable=True))
        batch_op.create_index(op.f('ix_job_events_seq'), ['seq'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('job_events', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_job_events_seq'))
        batch_op.drop_column('seq')

    op.drop_index(op.f('ix_idempotency_records_principal_id'), table_name='idempotency_records')
    op.drop_index(op.f('ix_idempotency_records_key'), table_name='idempotency_records')
    op.drop_table('idempotency_records')
