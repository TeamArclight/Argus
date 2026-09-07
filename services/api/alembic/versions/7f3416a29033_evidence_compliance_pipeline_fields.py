"""evidence_compliance_pipeline_fields

Revision ID: 7f3416a29033
Revises: 6c25b8a18022
Create Date: 2026-09-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3416a29033'
down_revision: Union[str, None] = '6c25b8a18022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('compliance_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('input_snapshot_json', sa.JSON(), server_default='{}', nullable=False))

    with op.batch_alter_table('evidence', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bidder_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('tender_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('document_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('extracted_fact_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('verification_result_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('run_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('source_type', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('source_reference', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('sha256', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('verification_mode', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('verification_status', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('provider_identifier', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True))

        batch_op.create_index(batch_op.f('ix_evidence_bidder_id'), ['bidder_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_tender_id'), ['tender_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_extracted_fact_id'), ['extracted_fact_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_verification_result_id'), ['verification_result_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_evidence_run_id'), ['run_id'], unique=False)

        batch_op.create_foreign_key('fk_evidence_bidder_id_bidders', 'bidders', ['bidder_id'], ['id'])
        batch_op.create_foreign_key('fk_evidence_tender_id_tenders', 'tenders', ['tender_id'], ['id'])
        batch_op.create_foreign_key('fk_evidence_document_id_documents', 'documents', ['document_id'], ['id'])
        batch_op.create_foreign_key('fk_evidence_extracted_fact_id_extracted_facts', 'extracted_facts', ['extracted_fact_id'], ['id'])
        batch_op.create_foreign_key('fk_evidence_verification_result_id_verification_results', 'verification_results', ['verification_result_id'], ['id'])
        batch_op.create_foreign_key('fk_evidence_run_id_compliance_runs', 'compliance_runs', ['run_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('evidence', schema=None) as batch_op:
        batch_op.drop_constraint('fk_evidence_run_id_compliance_runs', type_='foreignkey')
        batch_op.drop_constraint('fk_evidence_verification_result_id_verification_results', type_='foreignkey')
        batch_op.drop_constraint('fk_evidence_extracted_fact_id_extracted_facts', type_='foreignkey')
        batch_op.drop_constraint('fk_evidence_document_id_documents', type_='foreignkey')
        batch_op.drop_constraint('fk_evidence_tender_id_tenders', type_='foreignkey')
        batch_op.drop_constraint('fk_evidence_bidder_id_bidders', type_='foreignkey')

        batch_op.drop_index(batch_op.f('ix_evidence_run_id'))
        batch_op.drop_index(batch_op.f('ix_evidence_verification_result_id'))
        batch_op.drop_index(batch_op.f('ix_evidence_extracted_fact_id'))
        batch_op.drop_index(batch_op.f('ix_evidence_document_id'))
        batch_op.drop_index(batch_op.f('ix_evidence_tender_id'))
        batch_op.drop_index(batch_op.f('ix_evidence_bidder_id'))

        batch_op.drop_column('observed_at')
        batch_op.drop_column('provider_identifier')
        batch_op.drop_column('verification_status')
        batch_op.drop_column('verification_mode')
        batch_op.drop_column('sha256')
        batch_op.drop_column('source_reference')
        batch_op.drop_column('source_type')
        batch_op.drop_column('run_id')
        batch_op.drop_column('verification_result_id')
        batch_op.drop_column('extracted_fact_id')
        batch_op.drop_column('document_id')
        batch_op.drop_column('tender_id')
        batch_op.drop_column('bidder_id')

    with op.batch_alter_table('compliance_runs', schema=None) as batch_op:
        batch_op.drop_column('input_snapshot_json')
