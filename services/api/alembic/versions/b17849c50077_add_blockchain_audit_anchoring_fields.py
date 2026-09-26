"""add_blockchain_audit_anchoring_fields

Revision ID: b17849c50077
Revises: a06738c40066
Create Date: 2026-09-19 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b17849c50077'
down_revision: Union[str, None] = 'a06738c40066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('event_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('blockchain_status', sa.String(length=32), server_default='NOT_ANCHORED', nullable=False))
        batch_op.add_column(sa.Column('blockchain_network', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('blockchain_tx_hash', sa.String(length=66), nullable=True))
        batch_op.add_column(sa.Column('blockchain_block_number', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('anchored_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('blockchain_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('audit_hash_version', sa.String(length=16), server_default='v1', nullable=True))
        batch_op.add_column(sa.Column('blockchain_retry_count', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('blockchain_last_attempt_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('blockchain_next_retry_at', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_audit_events_event_hash', ['event_hash'], unique=False)
        batch_op.create_index('ix_audit_events_blockchain_status', ['blockchain_status'], unique=False)
        batch_op.create_index('ix_audit_events_blockchain_tx_hash', ['blockchain_tx_hash'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_events_blockchain_tx_hash')
        batch_op.drop_index('ix_audit_events_blockchain_status')
        batch_op.drop_index('ix_audit_events_event_hash')
        batch_op.drop_column('blockchain_next_retry_at')
        batch_op.drop_column('blockchain_last_attempt_at')
        batch_op.drop_column('blockchain_retry_count')
        batch_op.drop_column('audit_hash_version')
        batch_op.drop_column('blockchain_error')
        batch_op.drop_column('anchored_at')
        batch_op.drop_column('blockchain_block_number')
        batch_op.drop_column('blockchain_tx_hash')
        batch_op.drop_column('blockchain_network')
        batch_op.drop_column('blockchain_status')
        batch_op.drop_column('event_hash')
