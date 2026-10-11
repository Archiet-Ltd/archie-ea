"""Create NLP tables for semantic search and intent classification

Revision ID: 001
Revises:
Create Date: 2026-01-16 17:30:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "001_create_nlp_tables"
down_revision = None
branch_labels = None


def upgrade():
    """Create NLP tables"""

    # Create document_embeddings table for vector storage
    op.create_table(
        "document_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("embedding", sa.ARRAY(sa.Float), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_document_embeddings"),
    )

    # Create indexes for document_embeddings
    op.create_index("ix_document_embeddings_content_id", "document_embeddings", ["content_id"])
    op.create_index("ix_document_embeddings_domain", "document_embeddings", ["domain"])
    op.create_index("ix_document_embeddings_content_type", "document_embeddings", ["content_type"])
    op.create_index("ix_document_embeddings_created_at", "document_embeddings", ["created_at"])

    # Create vector index for similarity search (PostgreSQL pgvector)
    # This will be created manually after pgvector extension is installed

    # Create intent_interactions table for training data
    op.create_table(
        "intent_interactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("predicted_intent", sa.String(100), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("actual_intent", sa.String(100), nullable=True),
        sa.Column("entities", sa.JSON(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_intent_interactions"),
    )

    # Create indexes for intent_interactions
    op.create_index(
        "ix_intent_interactions_predicted_intent", "intent_interactions", ["predicted_intent"]
    )
    op.create_index(
        "ix_intent_interactions_actual_intent", "intent_interactions", ["actual_intent"]
    )
    op.create_index("ix_intent_interactions_created_at", "intent_interactions", ["created_at"])

    # Create conversation_contexts table for conversation persistence
    op.create_table(
        "conversation_contexts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("context_summary", sa.Text(), nullable=True),
        sa.Column("key_entities", sa.JSON(), nullable=True),
        sa.Column("intent_history", sa.JSON(), nullable=True),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_contexts"),
    )

    # Create indexes for conversation_contexts
    op.create_index("ix_conversation_contexts_session_id", "conversation_contexts", ["session_id"])
    op.create_index("ix_conversation_contexts_user_id", "conversation_contexts", ["user_id"])
    op.create_index("ix_conversation_contexts_domain", "conversation_contexts", ["domain"])
    op.create_index("ix_conversation_contexts_updated_at", "conversation_contexts", ["updated_at"])

    # Create conversation_history table for message tracking
    op.create_table(
        "conversation_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(100), nullable=False),
        sa.Column("entities", sa.JSON(), nullable=True),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_history"),
    )

    # Create indexes for conversation_history
    op.create_index("ix_conversation_history_session_id", "conversation_history", ["session_id"])
    op.create_index("ix_conversation_history_user_id", "conversation_history", ["user_id"])
    op.create_index("ix_conversation_history_intent", "conversation_history", ["intent"])
    op.create_index("ix_conversation_history_domain", "conversation_history", ["domain"])
    op.create_index("ix_conversation_history_created_at", "conversation_history", ["created_at"])

    # Create nlp_models table for model management
    op.create_table(
        "nlp_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column(
            "model_type", sa.String(50), nullable=False
        ),  # 'intent_classifier', 'entity_extractor'
        sa.Column("model_version", sa.String(20), nullable=False),
        sa.Column("model_path", sa.String(255), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("accuracy_score", sa.Float(), nullable=True),
        sa.Column("training_samples", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_nlp_models"),
    )

    # Create indexes for nlp_models
    op.create_index("ix_nlp_models_model_name", "nlp_models", ["model_name"])
    op.create_index("ix_nlp_models_model_type", "nlp_models", ["model_type"])
    op.create_index("ix_nlp_models_is_active", "nlp_models", ["is_active"])

    # Create nlp_search_logs table for analytics
    op.create_table(
        "nlp_search_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("search_type", sa.String(20), nullable=False),  # 'semantic', 'keyword', 'hybrid'
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.Column("avg_similarity", sa.Float(), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_nlp_search_logs"),
    )

    # Create indexes for nlp_search_logs
    op.create_index("ix_nlp_search_logs_domain", "nlp_search_logs", ["domain"])
    op.create_index("ix_nlp_search_logs_search_type", "nlp_search_logs", ["search_type"])
    op.create_index("ix_nlp_search_logs_created_at", "nlp_search_logs", ["created_at"])
    op.create_index("ix_nlp_search_logs_user_id", "nlp_search_logs", ["user_id"])


def downgrade():
    """Remove NLP tables"""

    # Drop tables in reverse order
    op.drop_table("nlp_search_logs")
    op.drop_table("nlp_models")
    op.drop_table("conversation_history")
    op.drop_table("conversation_contexts")
    op.drop_table("intent_interactions")
    op.drop_table("document_embeddings")
