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
        'active_operation_locks',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('resource_type', sa.String(), nullable=False),
        sa.Column('resource_id', sa.String(), nullable=False),
        sa.Column('operation', sa.String(), nullable=False),
        sa.Column('job_id', sa.String(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('owner_principal_id', sa.String(), nullable=False),
        sa.Column('acquired_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('resource_type', 'resource_id', 'operation', name='uq_active_operation_locks_resource')
    )
    op.create_index(op.f('ix_active_operation_locks_resource_type'), 'active_operation_locks', ['resource_type'], unique=False)
    op.create_index(op.f('ix_active_operation_locks_resource_id'), 'active_operation_locks', ['resource_id'], unique=False)

    op.create_table(
        'idempotency_records',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('principal_id', sa.String(), nullable=False),
        sa.Column('resource_type', sa.String(), nullable=False),
        sa.Column('resource_id', sa.String(), nullable=False),
        sa.Column('operation', sa.String(), nullable=False),
        sa.Column('request_hash', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='PROCESSING'),
        sa.Column('response_code', sa.Integer(), nullable=True),
        sa.Column('response_json', sa.JSON(), nullable=True),
        sa.Column('job_id', sa.String(), nullable=True),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('principal_id', 'resource_type', 'resource_id', 'operation', 'key', name='uq_idempotency_records_scope')
    )
    op.create_index(op.f('ix_idempotency_records_key'), 'idempotency_records', ['key'], unique=False)
    op.create_index(op.f('ix_idempotency_records_principal_id'), 'idempotency_records', ['principal_id'], unique=False)

    with op.batch_alter_table('job_events', schema=None) as batch_op:
        batch_op.add_column(sa.Column('seq', sa.Integer(), nullable=False, server_default='1'))
        batch_op.create_unique_constraint('uq_job_events_job_seq', ['job_id', 'seq'])
        batch_op.create_index(batch_op.f('ix_job_events_seq'), ['seq'], unique=False)

    # Deterministically backfill existing historical JobEvent rows per job_id
    bind = op.get_bind()
    events = bind.execute(sa.text("SELECT id, job_id FROM job_events ORDER BY job_id, timestamp ASC, id ASC")).fetchall()
    current_job_id = None
    current_seq = 0
    for ev in events:
        ev_id = ev._mapping["id"] if hasattr(ev, "_mapping") else ev[0]
        ev_job_id = ev._mapping["job_id"] if hasattr(ev, "_mapping") else ev[1]
        if ev_job_id != current_job_id:
            current_job_id = ev_job_id
            current_seq = 1
        else:
            current_seq += 1
        bind.execute(
            sa.text("UPDATE job_events SET seq = :seq WHERE id = :id"),
            {"seq": current_seq, "id": ev_id}
        )


def downgrade() -> None:
    with op.batch_alter_table('job_events', schema=None) as batch_op:
        batch_op.drop_constraint('uq_job_events_job_seq', type_='unique')
        batch_op.drop_index(batch_op.f('ix_job_events_seq'))
        batch_op.drop_column('seq')

    op.drop_index(op.f('ix_idempotency_records_principal_id'), table_name='idempotency_records')
    op.drop_index(op.f('ix_idempotency_records_key'), table_name='idempotency_records')
    op.drop_table('idempotency_records')

    op.drop_index(op.f('ix_active_operation_locks_resource_id'), table_name='active_operation_locks')
    op.drop_index(op.f('ix_active_operation_locks_resource_type'), table_name='active_operation_locks')
    op.drop_table('active_operation_locks')
