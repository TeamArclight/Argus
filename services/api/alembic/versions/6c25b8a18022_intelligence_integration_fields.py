"""intelligence_integration_fields

Revision ID: 6c25b8a18022
Revises: 5b14a9f27911
Create Date: 2026-09-07 15:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c25b8a18022'
down_revision: Union[str, None] = '5b14a9f27911'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tender_requirements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_approved', sa.Boolean(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('document_id', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('metadata_json', sa.JSON(), server_default='{}', nullable=False))
        batch_op.create_index(batch_op.f('ix_tender_requirements_document_id'), ['document_id'], unique=False)
        batch_op.create_foreign_key('fk_tender_requirements_document_id_documents', 'documents', ['document_id'], ['id'])

    with op.batch_alter_table('extracted_facts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('metadata_json', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('extracted_facts', schema=None) as batch_op:
        batch_op.drop_column('metadata_json')

    with op.batch_alter_table('tender_requirements', schema=None) as batch_op:
        batch_op.drop_constraint('fk_tender_requirements_document_id_documents', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_tender_requirements_document_id'))
        batch_op.drop_column('metadata_json')
        batch_op.drop_column('document_id')
        batch_op.drop_column('is_approved')
