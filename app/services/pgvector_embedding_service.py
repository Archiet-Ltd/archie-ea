"""
pgvector Embedding Service

Efficient vector embedding generation and storage using PostgreSQL pgvector extension.
Replaces ChromaDB + FAISS with native PostgreSQL vector search.

Key Features:
- Lazy-loaded embedding model (uses sentence-transformers)
- Batch embedding generation
- pgvector similarity search with HNSW indexing
- Automatic embedding metadata tracking
- Fallback to text search if embeddings unavailable
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import or_, text
from sqlalchemy.orm import Session






def _cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two vectors."""
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(dot / norm) if norm > 0 else 0.0

# Try to import sentence_transformers - may fail due to torch circular import
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except Exception as e:
    logger = logging.getLogger(__name__)
    logger.warning(f"Sentence transformers not available: {e}")
    SentenceTransformer = None
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from app import db
from app.middleware.tenant_context import current_org_id
from app.models.vector_embeddings import (
    ApplicationComponentEmbedding,
    BusinessCapabilityEmbedding,
    ChatMessageEmbedding,
    ProcessEmbedding,
    SolutionEmbedding,
    VendorOrganizationEmbedding,
    VendorProductEmbedding,
)

logger = logging.getLogger(__name__)

# Global embedding model (lazy-loaded)
_embedding_model = None
EMBEDDING_DIMENSION = 384
MODEL_NAME = "all-MiniLM-L6-v2"


def get_embedding_model() -> Optional[SentenceTransformer]:
    """Get or initialize the embedding model (lazy-loaded)."""
    global _embedding_model
    if _embedding_model is None:
        try:
            _embedding_model = SentenceTransformer(MODEL_NAME)
            logger.info(f"Loaded embedding model: {MODEL_NAME}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            return None
    return _embedding_model


class PgvectorEmbeddingService:
    """
    Service for managing vector embeddings using PostgreSQL pgvector extension.
    """

    def __init__(self, session: Optional[Session] = None):
        """Initialize with optional database session."""
        self.session = session or db.session
        self.model = get_embedding_model()

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            List of floats or None if failed
        """
        if not self.model:
            logger.warning("Embedding model not available")
            return None

        try:
            embedding = self.model.encode(text, convert_to_numpy=False)
            if hasattr(embedding, "tolist"):
                return embedding.tolist()
            return embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding for text: {e}")
            return None

    def generate_embeddings_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        Generate embeddings for multiple texts in batch.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding lists or None if failed
        """
        if not self.model or not texts:
            return None

        try:
            embeddings = self.model.encode(texts, convert_to_numpy=False, batch_size=32)
            return [e.tolist() if hasattr(e, "tolist") else e for e in embeddings]
        except Exception as e:
            logger.error(f"Failed to generate batch embeddings: {e}")
            return None

    # ============================================================================
    # Vendor Product Embeddings
    # ============================================================================

    def create_vendor_product_embedding(
        self, vendor_product_id: int, text: str
    ) -> Optional[VendorProductEmbedding]:
        """Create or update vendor product embedding."""
        try:
            embedding_vector = self.generate_embedding(text)
            if not embedding_vector:
                return None

            # Delete existing embedding
            VendorProductEmbedding.query.filter_by(vendor_product_id=vendor_product_id).delete()

            # Create new embedding
            embedding = VendorProductEmbedding(
                vendor_product_id=vendor_product_id,
                embedding=embedding_vector,
                embedding_text=text,
                model_version=MODEL_NAME,
            )
            self.session.add(embedding)
            self.session.commit()
            logger.debug(f"Created embedding for vendor product {vendor_product_id}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to create vendor product embedding: {e}")
            self.session.rollback()
            return None

    def _search_embeddings_python(self, model_class, id_field, query_embedding, limit, threshold):
        """Generic Python-based cosine similarity search for JSON-stored embeddings."""
        all_rows = model_class.query.all()
        scored = []
        for row in all_rows:
            vec = row.embedding
            if not vec:
                continue
            if isinstance(vec, str):
                import json as _json
                vec = _json.loads(vec)
            sim = _cosine_similarity(query_embedding, vec)
            if sim >= threshold:
                scored.append((getattr(row, id_field), sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def search_vendor_products(
        self, query_text: str, limit: int = 10, threshold: float = 0.3
    ) -> List[Tuple[int, str, float]]:
        """Search vendor products by semantic similarity."""
        try:
            query_embedding = self.generate_embedding(query_text)
            if not query_embedding:
                return self._fallback_text_search_vendor_products(query_text, limit)

            scored = self._search_embeddings_python(
                VendorProductEmbedding, "vendor_product_id", query_embedding, limit, threshold
            )
            output = []
            for vp_id, sim in scored:
                from app.models.vendor.vendor_organization import VendorProduct
                vp = db.session.get(VendorProduct, vp_id)
                if vp:
                    output.append((vp_id, vp.name, sim))
            return output
        except Exception as e:
            logger.error(f"Vector search failed, using fallback: {e}")
            return self._fallback_text_search_vendor_products(query_text, limit)

    # ============================================================================
    # Application Component Embeddings
    # ============================================================================

    def search_applications(
        self, query_text: str, limit: int = 10, threshold: float = 0.3
    ) -> List[Tuple[int, str, float]]:
        """Search applications by semantic similarity."""
        try:
            query_embedding = self.generate_embedding(query_text)
            if not query_embedding:
                return []

            scored = self._search_embeddings_python(
                ApplicationComponentEmbedding, "application_component_id", query_embedding, limit, threshold
            )
            output = []
            for app_id, sim in scored:
                from app.models.application_portfolio import ApplicationComponent
                app = db.session.get(ApplicationComponent, app_id)
                if app:
                    output.append((app_id, app.name, sim))
            return output
        except Exception as e:
            logger.error(f"Application vector search failed: {e}")
            return []

    # ============================================================================
    # Unified Cross-Entity Search
    # ============================================================================

    def search_all(
        self, query_text: str, limit: int = 5, threshold: float = 0.3
    ) -> Dict[str, List[Tuple[int, str, float]]]:
        """Search across all entity types and return grouped results."""
        return {
            "applications": self.search_applications(query_text, limit, threshold),
            "capabilities": self.search_capabilities(query_text, limit, threshold),
            "vendor_products": self.search_vendor_products(query_text, limit, threshold),
        }

    # ============================================================================
    # Business Capability Embeddings
    # ============================================================================

    def create_capability_embedding(
        self, capability_id: int, text: str
    ) -> Optional[BusinessCapabilityEmbedding]:
        """Create or update capability embedding."""
        try:
            embedding_vector = self.generate_embedding(text)
            if not embedding_vector:
                return None

            # Delete existing
            BusinessCapabilityEmbedding.query.filter_by(
                business_capability_id=capability_id
            ).delete()

            embedding = BusinessCapabilityEmbedding(
                business_capability_id=capability_id,
                embedding=embedding_vector,
                embedding_text=text,
                model_version=MODEL_NAME,
            )
            self.session.add(embedding)
            self.session.commit()
            return embedding
        except Exception as e:
            logger.error(f"Failed to create capability embedding: {e}")
            self.session.rollback()
            return None

    def search_capabilities(
        self, query_text: str, limit: int = 10, threshold: float = 0.3
    ) -> List[Tuple[int, str, float]]:
        """Search capabilities by semantic similarity."""
        try:
            query_embedding = self.generate_embedding(query_text)
            if not query_embedding:
                return []

            scored = self._search_embeddings_python(
                BusinessCapabilityEmbedding, "business_capability_id", query_embedding, limit, threshold
            )
            output = []
            for cap_id, sim in scored:
                from app.models.business_capabilities import BusinessCapability
                cap = db.session.get(BusinessCapability, cap_id)
                if cap:
                    output.append((cap_id, cap.name, sim))
            return output
        except Exception as e:
            logger.error(f"Capability search failed: {e}")
            return []

    # ============================================================================
    # Chat Message Embeddings
    # ============================================================================

    def create_chat_message_embedding(
        self,
        chat_session_id: str,
        message_text: str,
        user_id: Optional[int] = None,
        role: str = "user",
        domain: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ChatMessageEmbedding]:
        """Create embedding for chat message (for context retrieval & memory)."""
        try:
            embedding_vector = self.generate_embedding(message_text)
            if not embedding_vector:
                return None

            embedding = ChatMessageEmbedding(
                chat_session_id=chat_session_id,
                user_id=user_id,
                message_text=message_text,
                embedding=embedding_vector,
                message_role=role,
                domain=domain,
                metadata_json=metadata or {},
            )
            self.session.add(embedding)
            self.session.commit()
            logger.debug(f"Created chat message embedding for session {chat_session_id}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to create chat message embedding: {e}")
            self.session.rollback()
            return None

    def search_chat_history(
        self,
        query_text: str,
        chat_session_id: str,
        limit: int = 5,
        threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        Search chat history by semantic similarity for context retrieval.
        Used to find relevant previous messages for context.
        """
        try:
            query_embedding = self.generate_embedding(query_text)
            if not query_embedding:
                return []

            results = (
                self.session.query(ChatMessageEmbedding)
                .filter(
                    ChatMessageEmbedding.chat_session_id == chat_session_id,
                    ChatMessageEmbedding.in_org(current_org_id()),
                )
                .filter(
                    ChatMessageEmbedding.embedding.cosine_distance(query_embedding)
                    < (1 - threshold)
                )
                .order_by(ChatMessageEmbedding.embedding.cosine_distance(query_embedding))
                .limit(limit)
                .all()
            )

            return [
                {
                    "message": r.message_text,
                    "role": r.message_role,
                    "created_at": r.created_at,
                    "domain": r.domain,
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Chat history search failed: {e}")
            return []

    # ============================================================================
    # Generic Embedding Methods
    # ============================================================================

    def generate_and_store(
        self,
        entity_type: str,
        entity_id: int,
        text: str,
        embedding_model_cls: type,
        fk_field: str,
    ) -> Optional[Any]:
        """
        Generate an embedding for arbitrary text and store it in the given
        embedding table.

        Args:
            entity_type: Human-readable label for logging (e.g. 'application').
            entity_id: Primary key of the source entity.
            text: Text to embed.
            embedding_model_cls: SQLAlchemy model class for the embedding table.
            fk_field: Name of the foreign-key column on the embedding model
                      (e.g. 'application_component_id').

        Returns:
            The created embedding record, or None on failure.
        """
        try:
            embedding_vector = self.generate_embedding(text)
            if embedding_vector is None:
                return None

            # Convert tensor/ndarray to plain Python list for pgvector
            if hasattr(embedding_vector, "tolist"):
                embedding_vector = embedding_vector.tolist()

            # Upsert: remove existing row for this entity
            embedding_model_cls.query.filter_by(**{fk_field: entity_id}).delete()

            record = embedding_model_cls(
                **{
                    fk_field: entity_id,
                    "embedding": embedding_vector,
                    "embedding_text": text,
                    "model_version": MODEL_NAME,
                }
            )
            self.session.add(record)
            self.session.commit()
            logger.debug(
                "Created embedding for %s id=%d", entity_type, entity_id
            )
            return record
        except Exception as e:
            logger.error(
                "Failed to create %s embedding for id=%d: %s",
                entity_type,
                entity_id,
                e,
            )
            self.session.rollback()
            return None

    # ============================================================================
    # Utility Methods
    # ============================================================================

    def _fallback_text_search_vendor_products(
        self, query_text: str, limit: int = 10
    ) -> List[Tuple[int, str, float]]:
        """Fallback text-based search when vector search unavailable."""
        try:
            from app.models.vendor.vendor_organization import VendorProduct

            results = (
                self.session.query(VendorProduct)
                .filter(
                    or_(
                        VendorProduct.name.ilike(f"%{query_text}%"),
                        VendorProduct.description.ilike(f"%{query_text}%"),
                    )
                )
                .limit(limit)
                .all()
            )

            return [(p.id, p.name, 0.5) for p in results]  # Neutral confidence score
        except Exception as e:
            logger.error(f"Fallback text search failed: {e}")
            return []

    def ensure_vector_extension(self) -> bool:
        """Ensure pgvector extension is installed."""
        try:
            self.session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # tenant-exempt: system/pgvector extension
            self.session.commit()
            logger.info("pgvector extension is ready")
            return True
        except Exception as e:
            logger.error(f"Failed to ensure pgvector extension: {e}")
            return False

    def get_embedding_stats(self) -> Dict[str, int]:
        """Get statistics on stored embeddings."""
        try:
            stats = {
                "vendor_product_embeddings": VendorProductEmbedding.query.count(),
                "capability_embeddings": BusinessCapabilityEmbedding.query.count(),
                "process_embeddings": ProcessEmbedding.query.count(),
                "chat_message_embeddings": ChatMessageEmbedding.query.count(),  # tenant-scoping-ok: platform-wide embedding statistics
                "solution_embeddings": SolutionEmbedding.query.count(),
                "vendor_org_embeddings": VendorOrganizationEmbedding.query.count(),
                "app_component_embeddings": ApplicationComponentEmbedding.query.count(),
            }
            return stats
        except Exception as e:
            logger.error(f"Failed to get embedding stats: {e}")
            return {}


# Singleton instance
_service_instance = None


def get_pgvector_service() -> PgvectorEmbeddingService:
    """Get or create pgvector embedding service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = PgvectorEmbeddingService()
    return _service_instance
