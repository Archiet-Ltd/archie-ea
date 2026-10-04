"""add_index_to_kanban_fk_columns

Revision ID: prod021_kanban_fk_indexes
Revises: encrypt_api_keys
Create Date: 2026-03-09

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = 'prod021_kanban_fk_indexes'
down_revision = 'encrypt_api_keys'
branch_labels = None
depends_on = None


def upgrade():
    # KanbanBoard: created_by_id
    op.create_index('ix_kanban_boards_created_by_id', 'kanban_boards', ['created_by_id'], unique=False)

    # KanbanCard: adm_phase_id, board_id, assigned_to_id, created_by_id, arb_review_id,
    #             closes_gap_id, target_plateau_id
    op.create_index('ix_kanban_cards_adm_phase_id', 'kanban_cards', ['adm_phase_id'], unique=False)
    op.create_index('ix_kanban_cards_board_id', 'kanban_cards', ['board_id'], unique=False)
    op.create_index('ix_kanban_cards_assigned_to_id', 'kanban_cards', ['assigned_to_id'], unique=False)
    op.create_index('ix_kanban_cards_created_by_id', 'kanban_cards', ['created_by_id'], unique=False)
    op.create_index('ix_kanban_cards_arb_review_id', 'kanban_cards', ['arb_review_id'], unique=False)
    op.create_index('ix_kanban_cards_closes_gap_id', 'kanban_cards', ['closes_gap_id'], unique=False)
    op.create_index('ix_kanban_cards_target_plateau_id', 'kanban_cards', ['target_plateau_id'], unique=False)

    # KanbanCardComment: card_id, user_id
    op.create_index('ix_kanban_card_comments_card_id', 'kanban_card_comments', ['card_id'], unique=False)
    op.create_index('ix_kanban_card_comments_user_id', 'kanban_card_comments', ['user_id'], unique=False)

    # KanbanCardAttachment: card_id, uploaded_by_id
    op.create_index('ix_kanban_card_attachments_card_id', 'kanban_card_attachments', ['card_id'], unique=False)
    op.create_index('ix_kanban_card_attachments_uploaded_by_id', 'kanban_card_attachments', ['uploaded_by_id'], unique=False)

    # ADMPhaseStep: phase_id
    op.create_index('ix_adm_phase_steps_phase_id', 'adm_phase_steps', ['phase_id'], unique=False)


def downgrade():
    op.drop_index('ix_adm_phase_steps_phase_id', table_name='adm_phase_steps')
    op.drop_index('ix_kanban_card_attachments_uploaded_by_id', table_name='kanban_card_attachments')
    op.drop_index('ix_kanban_card_attachments_card_id', table_name='kanban_card_attachments')
    op.drop_index('ix_kanban_card_comments_user_id', table_name='kanban_card_comments')
    op.drop_index('ix_kanban_card_comments_card_id', table_name='kanban_card_comments')
    op.drop_index('ix_kanban_cards_target_plateau_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_cards_closes_gap_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_cards_arb_review_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_cards_created_by_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_cards_assigned_to_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_cards_board_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_cards_adm_phase_id', table_name='kanban_cards')
    op.drop_index('ix_kanban_boards_created_by_id', table_name='kanban_boards')
