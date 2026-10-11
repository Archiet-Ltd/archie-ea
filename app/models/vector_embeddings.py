"""
Vector Embeddings Models for pgvector Integration.

Provides database models for storing and retrieving vector embeddings
for various entities (vendors, capabilities, products, chat messages).
Uses PostgreSQL pgvector extension for efficient similarity search.
"""

# Try to import pgvector - use JSON fallback if not available or extension not in DB
# Set USE_PGVECTOR=true environment variable to enable pgvector (requires extension in DB)
import os
from datetime import datetime
from typing import Optional  # dead-code-ok: used by type hints

from sqlalchemy import Index, Text, UniqueConstraint

from app import db
from app.models.mixins.core import TenantMixin

HAS_PGVECTOR = False
Vector = None

if os.environ.get("USE_PGVECTOR", "").lower() == "true":
    try:
        from pgvector.sqlalchemy import Vector

        HAS_PGVECTOR = True
    except ImportError:
        pass


def get_vector_column(dimensions=384):
    """Get appropriate column type for vector storage."""
    if HAS_PGVECTOR and Vector is not None:
        return Vector(dimensions)
    # Fallback: store as JSON array (works without pgvector extension)
    return db.JSON


class VendorProductEmbedding(db.Model):
    """
    Vector embeddings for vendor products.
    Used for semantic similarity search and vendor discovery.
    """

    __tablename__ = "vendor_product_embeddings"
    __table_args__ = (
        UniqueConstraint("vendor_product_id", name="uq_vendor_product_embedding"),
        Index("ix_vendor_product_embedding_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    vendor_product_id = db.Column(db.Integer, db.ForeignKey("vendor_products.id"), nullable=False)
    embedding = db.Column(get_vector_column(384))  # all-MiniLM-L6-v2 uses 384 dimensions
    embedding_text = db.Column(Text)  # Original text used for embedding
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    vendor_product = db.relationship(
        "VendorProduct",
        backref=db.backref("embeddings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<VendorProductEmbedding vendor_product_id={self.vendor_product_id}>"


class BusinessCapabilityEmbedding(db.Model):
    """
    Vector embeddings for business capabilities.
    Used for capability-based search and matching.
    """

    __tablename__ = "business_capability_embeddings"
    __table_args__ = (
        UniqueConstraint("business_capability_id", name="uq_capability_embedding"),
        Index("ix_capability_embedding_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_capability_id = db.Column(
        db.Integer, db.ForeignKey("business_capability.id"), nullable=False
    )
    embedding = db.Column(get_vector_column(384))
    embedding_text = db.Column(Text)
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship - explicitly specify foreign_keys to resolve join condition
    capability = db.relationship(
        "BusinessCapability",
        foreign_keys=[business_capability_id],
        backref=db.backref("embeddings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<BusinessCapabilityEmbedding capability_id={self.business_capability_id}>"


class ProcessEmbedding(db.Model):
    """
    Vector embeddings for APQC processes and industry processes.
    Used for process discovery and mapping.
    """

    __tablename__ = "process_embeddings"
    __table_args__ = (
        UniqueConstraint("process_id", name="uq_process_embedding"),
        Index("ix_process_embedding_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    process_id = db.Column(db.Integer, db.ForeignKey("industry_apqc_process.id"), nullable=False)
    embedding = db.Column(get_vector_column(384))
    embedding_text = db.Column(Text)
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    process = db.relationship(
        "IndustryAPQCProcess",
        backref=db.backref("embeddings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<ProcessEmbedding process_id={self.process_id}>"


class ChatMessageEmbedding(db.Model):
    """
    Vector embeddings for chat messages.
    Used for semantic search, context retrieval, and conversation memory.
    """

    __tablename__ = "chat_message_embeddings"
    __table_args__ = (
        Index("ix_chat_message_user", "user_id"),
        Index("ix_chat_message_created", "created_at"),
        Index("ix_chat_message_session", "chat_session_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    chat_session_id = db.Column(db.String(255), nullable=False)  # Session identifier
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    message_text = db.Column(Text, nullable=False)
    embedding = db.Column(get_vector_column(384))
    message_role = db.Column(db.String(20))  # 'user', 'assistant', 'system'
    domain = db.Column(db.String(100), nullable=True)  # Chat domain (vendor, capability, etc)
    metadata_json = db.Column(db.JSON, default={})  # Additional metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ChatMessageEmbedding session={self.chat_session_id} role={self.message_role}>"


class SolutionEmbedding(db.Model):
    """
    Vector embeddings for solutions.
    Used for solution discovery and recommendation.
    """

    __tablename__ = "solution_embeddings"
    __table_args__ = (
        UniqueConstraint("solution_id", name="uq_solution_embedding"),
        Index("ix_solution_embedding_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=False)
    embedding = db.Column(get_vector_column(384))
    embedding_text = db.Column(Text)
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    solution = db.relationship(
        "Solution",
        backref=db.backref("embeddings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<SolutionEmbedding solution_id={self.solution_id}>"


class VendorOrganizationEmbedding(db.Model):
    """
    Vector embeddings for vendor organizations.
    Used for vendor discovery and similarity matching.
    """

    __tablename__ = "vendor_organization_embeddings"
    __table_args__ = (
        UniqueConstraint("vendor_organization_id", name="uq_vendor_org_embedding"),
        Index("ix_vendor_org_embedding_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    vendor_organization_id = db.Column(
        db.Integer, db.ForeignKey("vendor_organizations.id"), nullable=False
    )
    embedding = db.Column(get_vector_column(384))
    embedding_text = db.Column(Text)  # Company description, capabilities, etc
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    vendor = db.relationship(
        "VendorOrganization",
        backref=db.backref("embeddings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<VendorOrganizationEmbedding vendor_id={self.vendor_organization_id}>"


class ApplicationComponentEmbedding(db.Model):
    """
    Vector embeddings for application components.
    Used for application discovery and matching.
    """

    __tablename__ = "application_component_embeddings"
    __table_args__ = (
        UniqueConstraint("application_component_id", name="uq_app_component_embedding"),
        Index("ix_app_component_embedding_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False
    )
    embedding = db.Column(get_vector_column(384))
    embedding_text = db.Column(Text)
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    application = db.relationship(
        "ApplicationComponent",
        backref=db.backref("embeddings", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<ApplicationComponentEmbedding app_id={self.application_component_id}>"


class DocumentChunkEmbedding(TenantMixin, db.Model):
    """
    Vector embeddings for document chunks.
    Used for RAG retrieval over uploaded documents in AI Chat.
    Each row stores a ~512-token chunk of an uploaded document together
    with its embedding vector for cosine-similarity search.

    Pre-TenantMixin, this table had no tenant boundary at all: the actual
    text content of every tenant's uploaded documents, chunked, sat in one
    unscoped table. Its only current read path (retrieve_document_chunks in
    document_processing_service.py) is unused by any live caller today, but
    a `document_id`-less call there does a cosine-similarity search across
    ALL organisations' chunks with no filter -- fixing this now, before that
    method gets a caller, closes the leak at its source rather than trusting
    every future caller to remember to scope it. See
    app/commands/reconcile_schema.py's `_backfill_document_chunk_organizations`.
    """

    __tablename__ = "document_chunk_embeddings"
    __table_args__ = (
        Index("ix_doc_chunk_document_id", "document_id"),
        Index("ix_doc_chunk_created", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, index=True)  # FK to ai_chat_document_uploads
    chunk_index = db.Column(db.Integer, nullable=False)
    chunk_text = db.Column(Text, nullable=False)
    embedding = db.Column(get_vector_column(384))  # Match other embedding tables
    model_version = db.Column(db.String(50), default="all-MiniLM-L6-v2")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<DocumentChunkEmbedding doc={self.document_id} chunk={self.chunk_index}>"
