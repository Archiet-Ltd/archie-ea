"""
Semantic APQC Service - Intelligent process classification using vector embeddings

.. deprecated:: 2.0.0
   This service is deprecated. Use :class:`app.services.unified_apqc_service.UnifiedAPQCService` instead.

   Migration:
       # Old way (deprecated):
       from app.services.semantic_apqc_service import SemanticAPQCService
       service = SemanticAPQCService()

       # New way:
       from app.services.unified_apqc_service import get_unified_apqc_service
       service = get_unified_apqc_service()

Replaces brittle keyword matching with semantic similarity for APQC PCF classification.
Uses VectorEmbeddingService infrastructure for high-quality semantic matching.

NOTE: This module is kept for backward compatibility but should not be used directly.
The UnifiedAPQCService automatically delegates to this service when appropriate.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db
from app.models.apqc_process import APQCProcess, sort_processes_naturally
from app.services.llm_service import LLMService
from app.services.vector_embedding_service import VectorEmbeddingService
from app.utils.deprecation import (
    APQC_UNIFIED_GETTER,
    APQC_UNIFIED_SERVICE,
    deprecated_function,
    deprecated_service,
)

logger = logging.getLogger(__name__)

# Module-level deprecation warning
import warnings

warnings.warn(
    "semantic_apqc_service is deprecated. Use unified_apqc_service instead.",
    DeprecationWarning,
    stacklevel=2,
)


@dataclass
class APQCMatch:
    """Represents a semantic match to an APQC process."""

    process_id: int
    process_code: str
    process_name: str
    level: int
    category_level_1: str
    category_level_2: Optional[str]
    similarity_score: float
    match_method: str  # 'semantic', 'keyword', 'llm_enhanced'
    confidence: str  # 'high', 'medium', 'low'


@dataclass
class APQCClassificationResult:
    """Result of classifying text to APQC processes."""

    input_text: str
    matches: List[APQCMatch]
    primary_category: Optional[str]
    processing_time_ms: int
    model_used: str
    total_candidates_evaluated: int


@deprecated_service(
    replacement=APQC_UNIFIED_SERVICE,
    version="2.0.0",
    reason="Consolidated into UnifiedAPQCService for maintainability",
    removal_version="3.0.0",
)
class SemanticAPQCService:
    """
    Semantic APQC process classification using vector embeddings.

    .. deprecated:: 2.0.0
       Use :class:`app.services.unified_apqc_service.UnifiedAPQCService` instead.

    Features:
    - Semantic similarity matching using vector embeddings
    - LLM-enhanced classification for complex cases
    - Hybrid approach combining keywords + semantics
    - Batch processing for efficiency
    """

    APQC_DOMAIN = "apqc_pcf"
    HIGH_CONFIDENCE_THRESHOLD = 0.85
    MEDIUM_CONFIDENCE_THRESHOLD = 0.70
    LOW_CONFIDENCE_THRESHOLD = 0.50

    def __init__(self):
        self.embedding_service = VectorEmbeddingService()
        # Override default model to use sentence-transformers (local, free)
        self.embedding_service.default_model = "sentence-transformers"
        self.llm_service = LLMService()
        self._process_cache = None
        self._cache_timestamp = None
        self._cache_ttl = 3600
        self._index_built = False

    def _get_apqc_processes(self) -> List[APQCProcess]:
        """Get all APQC processes with caching and proper session management."""
        now = datetime.utcnow().timestamp()
        if (
            self._process_cache is None
            or self._cache_timestamp is None
            or now - self._cache_timestamp > self._cache_ttl
        ):
            try:
                # Ensure clean session state
                db.session.rollback()
                processes = sort_processes_naturally(APQCProcess.query.order_by(APQCProcess.id).all())
                self._process_cache = processes
                self._cache_timestamp = now
            except Exception as e:
                logger.error(f"Error fetching APQC processes: {e}")
                # Fallback to empty cache
                self._process_cache = []
                self._cache_timestamp = now
        return self._process_cache

    def _build_process_text(self, process: APQCProcess) -> str:
        """Build comprehensive text representation of an APQC process."""
        parts = [
            process.process_name,
            process.description or "",
            process.category_level_1 or "",
            process.category_level_2 or "",
        ]
        return " ".join(filter(None, parts))

    async def build_apqc_embedding_index(self, force_rebuild: bool = False) -> Dict[str, Any]:
        """Build vector index of all APQC process descriptions."""
        if self._index_built and not force_rebuild:
            return {"status": "already_built"}

        processes = self._get_apqc_processes()
        stats = {"total": len(processes), "created": 0, "errors": 0}

        for process in processes:
            try:
                text = self._build_process_text(process)
                if not text.strip():
                    continue
                embedding = self.embedding_service.embed_text(text)
                metadata = {
                    "domain": self.APQC_DOMAIN,
                    "content_type": "apqc_process",
                    "process_id": process.id,
                    "process_code": process.process_code,
                    "process_name": process.process_name,
                    "level": process.level,
                    "category_level_1": process.category_level_1,
                    "category_level_2": process.category_level_2,
                }
                await self.embedding_service.vector_store.add_vector(
                    id=f"apqc_{process.id}", vector=embedding, metadata=metadata
                )
                stats["created"] += 1
            except Exception as e:
                logger.error(f"Error embedding APQC {process.process_code}: {e}")
                stats["errors"] += 1

        self._index_built = True
        return stats

    async def classify_to_apqc(
        self, text: str, threshold: float = None, max_results: int = 5
    ) -> APQCClassificationResult:
        """Classify text to APQC processes using semantic similarity."""
        start_time = datetime.utcnow()
        # Use a lower default threshold to capture more matches
        # FAISS L2 distance converted to similarity via 1/(1 + dist) produces lower scores
        threshold = threshold or 0.30  # Lower threshold for FAISS compatibility

        logger.info(f"SemanticAPQC: Starting classification for text (len={len(text)})")

        try:
            query_embedding = self.embedding_service.embed_text(text)
            model_used = self.embedding_service.default_model
            logger.info(
                f"SemanticAPQC: Generated embedding with {model_used}, "
                f"dim={len(query_embedding)}"
            )
        except Exception as e:
            logger.error(f"SemanticAPQC: Error embedding query: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return APQCClassificationResult(text, [], None, 0, "error", 0)

        try:
            # Don't apply domain filter initially to see all results
            results = await self.embedding_service.vector_store.search_similar(
                query_vector=query_embedding, top_k=max_results * 3, domain_filter=None
            )  # Remove domain filter to get all results
            logger.info(f"SemanticAPQC: Vector search returned {len(results)} raw results")

            # Log first few results for debugging
            if results:
                for i, r in enumerate(results[:3]):
                    logger.info(
                        f"SemanticAPQC: Result {i + 1}: "
                        f"code={r.get('metadata', {}).get('process_code', 'N/A')}, "
                        f"similarity={r.get('similarity', 0):.4f}, "
                        f"domain={r.get('domain', 'N/A')}"
                    )
            else:
                logger.warning("SemanticAPQC: No results returned from vector store")
                # Check if vector store is properly initialized
                store_type = type(self.embedding_service.vector_store).__name__
                logger.warning(f"SemanticAPQC: Vector store type: {store_type}")

        except Exception as e:
            logger.error(f"SemanticAPQC: Error searching vector store: {e}")
            import traceback

            logger.error(traceback.format_exc())
            results = []

        matches = []
        filtered_count = 0
        for result in results:
            metadata = result.get("metadata", {})
            similarity = result.get("similarity", 0)

            if similarity < threshold:
                filtered_count += 1
                continue

            confidence = (
                "high"
                if similarity >= self.HIGH_CONFIDENCE_THRESHOLD
                else "medium"
                if similarity >= self.MEDIUM_CONFIDENCE_THRESHOLD
                else "low"
            )

            matches.append(
                APQCMatch(
                    process_id=metadata.get("process_id", 0),
                    process_code=metadata.get("process_code", ""),
                    process_name=metadata.get("process_name", ""),
                    level=metadata.get("level", 0),
                    category_level_1=metadata.get("category_level_1", ""),
                    category_level_2=metadata.get("category_level_2"),
                    similarity_score=similarity,
                    match_method="semantic",
                    confidence=confidence,
                )
            )

        if filtered_count > 0:
            logger.info(
                f"SemanticAPQC: Filtered out {filtered_count} results below threshold {threshold}"
            )

        # Sort by similarity score
        matches.sort(key=lambda x: x.similarity_score, reverse=True)
        matches = matches[:max_results]

        end_time = datetime.utcnow()
        processing_time = int((end_time - start_time).total_seconds() * 1000)

        logger.info(f"SemanticAPQC: Returning {len(matches)} matches in {processing_time}ms")

        return APQCClassificationResult(
            input_text=text,
            matches=matches,
            primary_category=matches[0].category_level_1 if matches else None,
            processing_time_ms=processing_time,
            model_used=model_used,
            total_candidates_evaluated=len(results),
        )

    def classify_text(
        self, text: str, threshold: float = None, max_results: int = 5
    ) -> APQCClassificationResult:
        """
        Fully synchronous APQC classification using FAISS or ChromaDB directly.

        This method avoids async/sync mixing by using the underlying services'
        synchronous methods directly, which prevents hot-reload issues.

        Args:
            text: Text to classify
            threshold: Minimum similarity threshold (default 0.30)
            max_results: Maximum results to return

        Returns:
            APQCClassificationResult with matches
        """
        from datetime import datetime

        start_time = datetime.utcnow()
        threshold = threshold or 0.30
        model_used = "sentence-transformers"

        if not text or not text.strip():
            return APQCClassificationResult(text, [], None, 0, model_used, 0)

        try:
            # Try FAISS service directly (fully synchronous)
            try:
                from .faiss_apqc_service import get_faiss_apqc_service

                faiss_service = get_faiss_apqc_service()

                if faiss_service.index is not None:
                    logger.info("SemanticAPQC: Using FAISS service (sync) for classification")
                    results = faiss_service.classify_text(text, max_results=max_results * 2)

                    matches = []
                    for result in results:
                        score = result.get("score", 0)
                        if score < threshold:
                            continue

                        confidence = (
                            "high"
                            if score >= self.HIGH_CONFIDENCE_THRESHOLD
                            else "medium"
                            if score >= self.MEDIUM_CONFIDENCE_THRESHOLD
                            else "low"
                        )

                        matches.append(
                            APQCMatch(
                                process_id=result.get("existing_id", 0),
                                process_code=result.get("process_code", ""),
                                process_name=result.get("process_name", ""),
                                level=result.get("apqc_level", 0),
                                category_level_1=result.get("category_level_1", ""),
                                category_level_2=result.get("category_level_2"),
                                similarity_score=score,
                                match_method=result.get("source", "faiss"),
                                confidence=confidence,
                            )
                        )

                    matches.sort(key=lambda x: x.similarity_score, reverse=True)
                    matches = matches[:max_results]

                    end_time = datetime.utcnow()
                    processing_time = int((end_time - start_time).total_seconds() * 1000)

                    logger.info(
                        f"SemanticAPQC: FAISS returned {len(matches)} matches in {processing_time}ms"
                    )
                    return APQCClassificationResult(
                        input_text=text,
                        matches=matches,
                        primary_category=matches[0].category_level_1 if matches else None,
                        processing_time_ms=processing_time,
                        model_used=model_used,
                        total_candidates_evaluated=len(results),
                    )

            except Exception as faiss_error:
                logger.warning(f"FAISS service unavailable: {faiss_error}")

            # Try ChromaDB service directly (fully synchronous)
            try:
                from .chromadb_apqc_service import get_chromadb_apqc_service

                chromadb_service = get_chromadb_apqc_service()

                if chromadb_service.collection is not None:
                    logger.info("SemanticAPQC: Using ChromaDB service (sync) for classification")
                    results = chromadb_service.classify_text(text, max_results=max_results * 2)

                    matches = []
                    for result in results:
                        score = result.get("score", 0)
                        if score < threshold:
                            continue

                        confidence = (
                            "high"
                            if score >= self.HIGH_CONFIDENCE_THRESHOLD
                            else "medium"
                            if score >= self.MEDIUM_CONFIDENCE_THRESHOLD
                            else "low"
                        )

                        matches.append(
                            APQCMatch(
                                process_id=result.get("existing_id", 0),
                                process_code=result.get("process_code", ""),
                                process_name=result.get("process_name", ""),
                                level=result.get("level", 0),
                                category_level_1=result.get("category_level_1", ""),
                                category_level_2=result.get("category_level_2"),
                                similarity_score=score,
                                match_method=result.get("source", "chromadb"),
                                confidence=confidence,
                            )
                        )

                    matches.sort(key=lambda x: x.similarity_score, reverse=True)
                    matches = matches[:max_results]

                    end_time = datetime.utcnow()
                    processing_time = int((end_time - start_time).total_seconds() * 1000)

                    logger.info(
                        f"SemanticAPQC: ChromaDB returned {len(matches)} matches in {processing_time}ms"
                    )
                    return APQCClassificationResult(
                        input_text=text,
                        matches=matches,
                        primary_category=matches[0].category_level_1 if matches else None,
                        processing_time_ms=processing_time,
                        model_used=model_used,
                        total_candidates_evaluated=len(results),
                    )

            except Exception as chromadb_error:
                logger.warning(f"ChromaDB service unavailable: {chromadb_error}")

            # Fallback: return empty result if no vector services available
            logger.warning("SemanticAPQC: No vector services available, returning empty result")
            end_time = datetime.utcnow()
            processing_time = int((end_time - start_time).total_seconds() * 1000)
            return APQCClassificationResult(text, [], None, processing_time, "fallback", 0)

        except Exception as e:
            logger.error(f"Error in classify_text: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return APQCClassificationResult(text, [], None, 0, "error", 0)

    async def suggest_apqc_for_application(
        self, application_id: int, top_n: int = 5
    ) -> List[APQCMatch]:
        """Suggest APQC processes for an application using semantic matching."""
        from app.models.application_portfolio import ApplicationComponent

        app = db.session.get(ApplicationComponent, application_id)
        if not app:
            return []

        text_parts = [
            app.name,
            app.description or "",
            getattr(app, "imported_capabilities", "") or "",
            getattr(app, "business_purpose", "") or "",
        ]
        text = " ".join(filter(None, text_parts))
        if not text.strip():
            return []

        result = await self.classify_to_apqc(text, max_results=top_n)
        return result.matches

    def classify_text_sync(self, text: str, max_results: int = 5) -> List[APQCMatch]:
        """
        Synchronous APQC classification - returns just the matches list.

        Uses the fully synchronous classify_text method under the hood.

        Args:
            text: Text to classify
            max_results: Maximum number of results

        Returns:
            List of APQCMatch objects
        """
        result = self.classify_text(text, max_results=max_results)
        return result.matches


@deprecated_function(
    replacement=APQC_UNIFIED_GETTER,
    version="2.0.0",
    reason="Use get_unified_apqc_service() instead",
)
def get_semantic_apqc_service() -> SemanticAPQCService:
    """
    Factory function to get SemanticAPQCService instance.

    .. deprecated:: 2.0.0
       Use :func:`app.services.unified_apqc_service.get_unified_apqc_service` instead.
    """
    return SemanticAPQCService()
