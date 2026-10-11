"""Add archimate_element_id to application_compliance_controls

Revision ID: 20261009_app_compliance_element
Revises: 20261010_arb_change_requests_rls
Create Date: 2026-10-09 11:41:33.126014

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20261009_app_compliance_element'
down_revision = '20261010_arb_change_requests_rls'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c['name'] for c in inspector.get_columns('application_compliance_controls')]

    if 'archimate_element_id' not in cols:
        op.add_column(
            'application_compliance_controls',
            sa.Column('archimate_element_id', sa.Integer(), nullable=True),
        )
        op.create_index(
            op.f('ix_application_compliance_controls_archimate_element_id'),
            'application_compliance_controls',
            ['archimate_element_id'],
            unique=False,
        )
        op.create_foreign_key(
            None,
            'application_compliance_controls',
            'archimate_elements',
            ['archimate_element_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade():
    op.drop_constraint(
        'application_compliance_controls_archimate_element_id_fkey',
        'application_compliance_controls',
        type_='foreignkey',
    )
    op.drop_index(
        op.f('ix_application_compliance_controls_archimate_element_id'),
        table_name='application_compliance_controls',
    )
    op.drop_column('application_compliance_controls', 'archimate_element_id')