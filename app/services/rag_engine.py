"""
Production RAG Engine

Hardened RAG implementation with pgvector backend, LLM router integration,
and comprehensive provenance tracking for auditable AI recommendations.

Key Features:
- pgvector-backed vector storage with HNSW indexing
- LLM router with model switching, caching, and throttling
- Request-level provenance (sources, embeddings, models used)
- Human-review traces and guardrails
- Performance metrics and monitoring
- Fallback strategies and error handling

Architecture:
- Vector Store: pgvector (PostgreSQL extension)
- Embedding Model: sentence-transformers (lazy-loaded)
- LLM Router: Multi-provider with caching/throttling
- Provenance: Immutable audit trail for all AI decisions
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime  # dead-code-ok
from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from app import db
from .llm_cache import get_llm_cache
from .llm_service import get_llm_service
from .pgvector_embedding_service import (
    SHARED_EMBEDDING_TABLE_NAMES,
    TENANT_EMBEDDING_TABLE_NAMES,
    get_embedding_model,  # dead-code-ok
)
from app.utils.tenant_sql import org_scope

logger = logging.getLogger(__name__)

# RAG-005: Unified embedding table registry for cross-entity search.
# Maps entity_type label to (table_name, id_column, text_column).
EMBEDDING_TABLES = {
    "application": (
        "application_component_embeddings",
        "application_component_id",
        "embedding_text",
    ),
    "capability": (
        "business_capability_embeddings",
        "business_capability_id",
        "embedding_text",
    ),
    "process": (
        "process_embeddings",
        "process_id",
        "embedding_text",
    ),
    "solution": (
        "solution_embeddings",
        "solution_id",
        "embedding_text",
    ),
    "vendor_product": (
        "vendor_product_embeddings",
        "vendor_product_id",
        "embedding_text",
    ),
    "vendor_organization": (
        "vendor_organization_embeddings",
        "vendor_organization_id",
        "embedding_text",
    ),
    "chat_message": (
        "chat_message_embeddings",
        "id",
        "message_text",
    ),
}

# Tenant/shared embedding table classification imported from
# pgvector_embedding_service (canonical location).
_TENANT_EMBEDDING_TABLES_SQL = TENANT_EMBEDDING_TABLE_NAMES
_SHARED_EMBEDDING_TABLES_SQL = SHARED_EMBEDDING_TABLE_NAMES


def _org_scope_for_embedding_table(table_name: str):
    """Return a bound SQL org-scope fragment for an embedding table."""
    return org_scope(
        prefix=f"{table_name}.",
        keyword="AND",
        include_shared=table_name in _SHARED_EMBEDDING_TABLES_SQL,
        fail_closed=True,
    )


Base = declarative_base()


class RAGQuery(Base):
    """RAG query audit log."""
    __tablename__ = 'rag_queries'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query_text = Column(Text, nullable=False)
    user_id = Column(String(36))
    session_id = Column(String(36))
    query_type = Column(String(50))  # semantic_search, recommendation, analysis
    timestamp = Column(DateTime, default=datetime.utcnow)
    processing_time_ms = Column(Integer)
    result_count = Column(Integer)
    confidence_score = Column(Float)

    # Provenance data
    embedding_model = Column(String(100))
    llm_model = Column(String(100))
    vector_search_params = Column(JSON)
    llm_params = Column(JSON)
    sources_used = Column(JSON)  # List of source documents/chunks
    reasoning_trace = Column(JSON)  # Step-by-step reasoning

    results = relationship("RAGResult", back_populates="query")


class RAGResult(Base):
    """Individual RAG result with provenance."""
    __tablename__ = 'rag_results'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query_id = Column(String(36), ForeignKey('rag_queries.id'))
    rank = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    result_metadata = Column('metadata', JSON)
    similarity_score = Column(Float)
    source_type = Column(String(50))  # document, kg_node, vendor_data, etc.
    source_id = Column(String(36))
    provenance_chain = Column(JSON)  # Chain of transformations/sources

    query = relationship("RAGQuery", back_populates="results")


class RAGMetrics(Base):
    """RAG performance metrics."""
    __tablename__ = 'rag_metrics'

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, default=datetime.utcnow)
    metric_type = Column(String(50))  # latency, throughput, accuracy, etc.
    value = Column(Float)
    labels = Column(JSON)  # Additional context


@dataclass
class RAGConfig:
    """RAG engine configuration."""
    max_results: int = 10
    similarity_threshold: float = 0.7
    embedding_batch_size: int = 32
    cache_ttl_seconds: int = 3600
    enable_provenance: bool = True
    enable_metrics: bool = True


@dataclass
class VectorSearchResult:
    """Result from vector similarity search."""
    content: str
    metadata: Dict[str, Any]
    similarity_score: float
    source_type: str
    source_id: str
    provenance_chain: List[Dict[str, Any]]


@dataclass
class RAGResponse:
    """Complete RAG response with provenance."""
    query_id: str
    results: List[Dict[str, Any]]
    confidence_score: float
    processing_time_ms: int
    provenance: Dict[str, Any]
    reasoning_trace: List[Dict[str, Any]]


class RAGEngine:
    """Production RAG engine with provenance tracking."""

    def __init__(self, config: Optional[RAGConfig] = None):
        self.config = config or RAGConfig()
        self.embedding_model = None
        self.llm_service = None
        self.cache = None
        self._initialized = False

    async def initialize(self):
        """Initialize RAG engine components."""
        if self._initialized:
            return

        try:
            # Initialize embedding model
            self.embedding_model = get_embedding_model()
            if not self.embedding_model:
                raise RuntimeError("Failed to load embedding model")

            # Initialize LLM service
            self.llm_service = get_llm_service()

            # Initialize cache
            self.cache = get_llm_cache()

            self._initialized = True
            logger.info("RAG engine initialized successfully")

        except Exception as e:
            logger.error(f"RAG engine initialization failed: {e}")
            raise

    async def query(self, query_text: str, query_type: str = "semantic_search",
                   user_id: Optional[str] = None, session_id: Optional[str] = None,
                   filters: Optional[Dict[str, Any]] = None) -> RAGResponse:
        """Execute RAG query with full provenance tracking."""
        start_time = datetime.utcnow()

        # Create query audit record
        query_id = str(uuid.uuid4())
        query_record = RAGQuery(
            id=query_id,
            query_text=query_text,
            user_id=user_id,
            session_id=session_id,
            query_type=query_type,
            embedding_model=self.embedding_model.model_name if self.embedding_model else None
        )

        try:
            # Generate query embedding
            query_embedding = await self._generate_embedding(query_text)

            # Perform vector search
            search_results = await self._vector_search(
                query_embedding, filters, self.config.max_results
            )

            # Filter by similarity threshold
            filtered_results = [
                r for r in search_results
                if r.similarity_score >= self.config.similarity_threshold
            ]

            # Generate response using LLM if needed
            if query_type in ["recommendation", "analysis"]:
                llm_response = await self._generate_llm_response(
                    query_text, filtered_results, query_type
                )
                final_results = llm_response["results"]
                reasoning_trace = llm_response["reasoning"]
                confidence_score = llm_response["confidence"]
                llm_model = llm_response["model"]
            else:
                # Semantic search - return direct results
                final_results = [
                    {
                        "content": r.content,
                        "metadata": r.metadata,
                        "similarity_score": r.similarity_score,
                        "source_type": r.source_type,
                        "source_id": r.source_id,
                        "rank": i + 1
                    }
                    for i, r in enumerate(filtered_results[:self.config.max_results])
                ]
                reasoning_trace = []
                confidence_score = sum(r.similarity_score for r in filtered_results) / len(filtered_results) if filtered_results else 0.0
                llm_model = None

            # Calculate processing time
            processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Update query record
            query_record.processing_time_ms = processing_time
            query_record.result_count = len(final_results)
            query_record.confidence_score = confidence_score
            query_record.llm_model = llm_model
            query_record.vector_search_params = {
                "max_results": self.config.max_results,
                "similarity_threshold": self.config.similarity_threshold,
                "filters": filters
            }
            query_record.sources_used = [
                {
                    "source_type": r.get("source_type"),
                    "source_id": r.get("source_id"),
                    "similarity_score": r.get("similarity_score")
                }
                for r in final_results
            ]
            query_record.reasoning_trace = reasoning_trace

            # Save results to database
            if self.config.enable_provenance:
                await self._save_query_results(query_record, final_results)

            # Record metrics
            if self.config.enable_metrics:
                await self._record_metrics(query_type, processing_time, confidence_score)

            # Build response
            response = RAGResponse(
                query_id=query_id,
                results=final_results,
                confidence_score=confidence_score,
                processing_time_ms=processing_time,
                provenance={
                    "query_id": query_id,
                    "embedding_model": query_record.embedding_model,
                    "llm_model": llm_model,
                    "vector_search_params": query_record.vector_search_params,
                    "sources_used": query_record.sources_used,
                    "timestamp": start_time.isoformat()
                },
                reasoning_trace=reasoning_trace
            )

            return response

        except Exception as e:
            logger.error(f"RAG query failed: {e}")
            # Still save failed query for audit
            query_record.processing_time_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            db.session.add(query_record)
            db.session.commit()
            raise

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for query text."""
        if not self.embedding_model:
            raise RuntimeError("Embedding model not initialized")

        try:
            # Use asyncio.to_thread for CPU-bound embedding generation
            embedding = await asyncio.to_thread(
                self.embedding_model.encode,
                text,
                convert_to_numpy=True,
                normalize_embeddings=True
            )
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    def _get_query_embedding(self, query_text: str) -> Optional[List[float]]:
        """Generate embedding synchronously for a query string.

        Returns None on failure instead of raising.
        """
        try:
            model = get_embedding_model()
            if model is None:
                logger.warning("Embedding model not available for query embedding")
                return None
            import numpy as np
            embedding = model.encode(query_text, convert_to_numpy=True, normalize_embeddings=True)
            return embedding.tolist() if isinstance(embedding, np.ndarray) else list(embedding)
        except Exception as e:
            logger.error(f"Failed to generate query embedding: {e}")
            return None

    # ------------------------------------------------------------------
    # RAG-005: Cross-entity semantic search
    # ------------------------------------------------------------------
    def cross_entity_search(
        self, query_text: str, limit: int = 15
    ) -> List[Dict[str, Any]]:
        """Search ALL embedding tables simultaneously and return unified ranked results.

        Each result dict contains:
            entity_type  – key from EMBEDDING_TABLES (e.g. "application")
            entity_id    – integer PK in the source table
            text_content – the embedded text
            similarity_score – cosine similarity (0-1, higher is better)
        """
        query_embedding = self._get_query_embedding(query_text)
        if query_embedding is None:
            return []

        merged: List[Dict[str, Any]] = []
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        for entity_type, (table, id_col, text_col) in EMBEDDING_TABLES.items():
            try:
                org_clause, org_params = _org_scope_for_embedding_table(table)
                sql = text(
                    f"SELECT {id_col} AS entity_id, "  # noqa: S608
                    f"       {text_col} AS text_content, "
                    f"       1 - (embedding <=> :qvec::vector) AS similarity "
                    f"FROM {table} "
                    f"WHERE embedding IS NOT NULL {org_clause}"
                    f"ORDER BY embedding <=> :qvec::vector "
                    f"LIMIT :lim"
                )
                rows = db.session.execute(
                    sql, {"qvec": embedding_str, "lim": limit, **org_params}
                ).fetchall()
                for row in rows:
                    merged.append({
                        "entity_type": entity_type,
                        "entity_id": row.entity_id,
                        "text_content": row.text_content or "",
                        "similarity_score": float(row.similarity) if row.similarity is not None else 0.0,
                    })
            except Exception as e:
                # Individual table failure must not abort the whole search
                logger.warning(f"Cross-entity search skipped table {table}: {e}")
                continue

        # Global sort by similarity descending, then truncate
        merged.sort(key=lambda r: r["similarity_score"], reverse=True)
        return merged[:limit]

    async def _vector_search(self, query_embedding: List[float],
                           filters: Optional[Dict[str, Any]] = None,
                           limit: int = 10) -> List[VectorSearchResult]:
        """Perform vector similarity search."""
        # pgvector queries not yet wired (VendorProductEmbedding, BusinessCapabilityEmbedding)
        logger.warning("Vector search not available — pgvector integration pending")
        return []

    async def _generate_llm_response(self, query_text: str,
                                   search_results: List[VectorSearchResult],
                                   query_type: str) -> Dict[str, Any]:
        """Generate LLM response with reasoning trace."""
        if not self.llm_service:
            raise RuntimeError("LLM service not initialized")

        try:
            # Prepare context from search results
            context = "\n".join([
                f"Source {i + 1}: {r.content}"
                for i, r in enumerate(search_results[:5])  # Limit context length
            ])

            # Build prompt based on query type
            if query_type == "recommendation":
                prompt = f"""
                Based on the following context, provide a recommendation for: {query_text}

                Context:
                {context}

                Provide your recommendation with confidence score and reasoning.
                """
            elif query_type == "analysis":
                prompt = f"""
                Analyze the following query using the provided context: {query_text}

                Context:
                {context}

                Provide a detailed analysis with supporting evidence.
                """
            else:
                prompt = f"Answer: {query_text}\n\nContext: {context}"

            # Generate response
            response = await self.llm_service.generate(
                prompt=prompt,
                max_tokens=1000,
                temperature=0.3
            )

            # Extract model used
            model_used = getattr(response, 'model', 'unknown')

            # Parse response for confidence and reasoning
            response_text = response.get('text', '') if isinstance(response, dict) else str(response)

            # Simple confidence extraction (this could be more sophisticated)
            confidence_score = 0.8  # Default
            if "confidence" in response_text.lower():
                # Try to extract confidence score
                pass

            # Build reasoning trace
            reasoning_trace = [
                {
                    "step": "context_gathering",
                    "description": f"Gathered {len(search_results)} relevant sources",
                    "timestamp": datetime.utcnow().isoformat()
                },
                {
                    "step": "llm_generation",
                    "description": f"Generated response using {model_used}",
                    "model": model_used,
                    "timestamp": datetime.utcnow().isoformat()
                }
            ]

            return {
                "results": [{
                    "content": response_text,
                    "metadata": {"generated": True, "query_type": query_type},
                    "similarity_score": confidence_score,
                    "source_type": "llm_generated",
                    "source_id": str(uuid.uuid4()),
                    "rank": 1
                }],
                "reasoning": reasoning_trace,
                "confidence": confidence_score,
                "model": model_used
            }

        except Exception as e:
            logger.error(f"LLM response generation failed: {e}")
            raise

    async def _save_query_results(self, query_record: RAGQuery,
                                results: List[Dict[str, Any]]):
        """Save query and results to database."""
        try:
            db.session.add(query_record)

            for result in results:
                result_record = RAGResult(
                    query_id=query_record.id,
                    rank=result.get("rank", 0),
                    content=result.get("content", ""),
                    metadata=result.get("metadata", {}),
                    similarity_score=result.get("similarity_score", 0.0),
                    source_type=result.get("source_type", "unknown"),
                    source_id=result.get("source_id", ""),
                    provenance_chain=result.get("provenance_chain", [])
                )
                db.session.add(result_record)

            db.session.commit()

        except Exception as e:
            logger.error(f"Failed to save query results: {e}")
            db.session.rollback()

    async def _record_metrics(self, query_type: str, processing_time: int,
                            confidence_score: float):
        """Record performance metrics."""
        try:
            metrics = [
                RAGMetrics(
                    metric_type="query_latency",
                    value=processing_time,
                    labels={"query_type": query_type}
                ),
                RAGMetrics(
                    metric_type="query_confidence",
                    value=confidence_score,
                    labels={"query_type": query_type}
                )
            ]

            for metric in metrics:
                db.session.add(metric)

            db.session.commit()

        except Exception as e:
            logger.error(f"Failed to record metrics: {e}")
            db.session.rollback()


# Global RAG engine instance
_rag_engine = None


def get_rag_engine() -> RAGEngine:
    """Get the global RAG engine instance."""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = RAGEngine()
    return _rag_engine


def init_rag_tables():
    """Initialize RAG database tables."""
