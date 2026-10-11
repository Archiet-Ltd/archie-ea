"""
-> app.modules.ai_chat.services.ai_analysis_service

AI-Powered Semantic Discovery Service - LLM-PRD - 01 Implementation

Advanced semantic search and vendor discovery using:
- Sentence-transformers for vector embeddings
- FAISS/ChromaDB for fast vector search
- LLM integration for intelligent recommendations
- Real-time semantic capability matching

Key Features:
- Vector embedding generation for vendor products and capabilities
- Sub-second semantic search performance
- LLM-powered "why recommended" rationales
- Implementation roadmap generation
- Alternative scenario analysis
"""

import logging
from dataclasses import dataclass
from datetime import datetime  # dead-code-ok
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.orm import joinedload  # dead-code-ok

# Core AI/ML deps: torch + sentence-transformers (faiss-cpu is the vector backend)
try:
    import torch
    from sentence_transformers import SentenceTransformer
    AI_ML_AVAILABLE = True
except Exception as e:
    logger = logging.getLogger(__name__)
    logger.warning(f"AI/ML dependencies not available: {e}")
    torch = None
    SentenceTransformer = None
    AI_ML_AVAILABLE = False

# ChromaDB is optional — faiss-cpu is the installed alternative
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except Exception:
    chromadb = None
    Settings = None
    CHROMADB_AVAILABLE = False

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.vendor.vendor_organization import (  # dead-code-ok
    VendorProduct,
    VendorProductCapability,
)

# LLM Integration (placeholder for actual API integration)
# import openai  # or anthropic for Claude


logger = logging.getLogger(__name__)


@dataclass
class SemanticSearchResult:
    """Result of semantic search with confidence scores."""

    vendor_product_id: int
    product_name: str
    vendor_name: str
    similarity_score: float
    capability_coverage: float
    relevance_factors: List[str]
    confidence_level: str  # "high", "medium", "low"


@dataclass
class LLMRecommendation:
    """LLM-generated vendor recommendation."""

    recommended_vendor_id: int
    confidence: float  # 0.0 - 1.0
    rationale: str  # 2 - 3 paragraphs explaining why
    implementation_roadmap: List[Dict[str, Any]]
    risk_mitigation_strategies: List[str]
    alternative_scenarios: List[Dict[str, Any]]
    estimated_tco: Optional[Decimal] = None
    implementation_timeline: int = 0  # months


class AISemanticDiscoveryService:
    """
    AI-powered semantic discovery service with vector embeddings
    and intelligent vendor recommendations.
    """

    def __init__(self):
        """Initialize the semantic discovery service."""
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info(f"Initializing AI Semantic Discovery on device: {self.device}")

        # Initialize sentence transformer model
        try:
            self.embedding_model = SentenceTransformer("all-MiniLM-L6-v2", device=self.device)
            self.embedding_dimension = 384  # Dimension for all-MiniLM-L6-v2
            logger.info("Sentence transformer model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            self.embedding_model = None
            self.embedding_dimension = 384

        # Initialize ChromaDB for vector storage
        try:
            self.chroma_client = chromadb.PersistentClient(path="instance/vector_db")
            self._init_vector_collections()
            logger.info("ChromaDB vector database initialized")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            self.chroma_client = None

        # LLM integration placeholders
        self.llm_api_available = self._check_llm_availability()

        # Semantic search cache
        self._search_cache = {}
        self._cache_ttl = 300  # 5 minutes

    def _init_vector_collections(self):
        """Initialize ChromaDB collections for different entity types."""
        if not self.chroma_client:
            return

        # Collection for vendor products
        try:
            self.product_collection = self.chroma_client.get_or_create_collection(
                name="vendor_products",
                metadata={"description": "Vendor product embeddings for semantic search"},
            )
        except Exception as e:
            logger.error(f"Failed to create product collection: {e}")
            self.product_collection = None

        # Collection for business capabilities
        try:
            self.capability_collection = self.chroma_client.get_or_create_collection(
                name="business_capabilities",
                metadata={"description": "Business capability embeddings"},
            )
        except Exception as e:
            logger.error(f"Failed to create capability collection: {e}")
            self.capability_collection = None

    def _check_llm_availability(self) -> bool:
        """Check if LLM API is available for recommendations."""
        try:
            # Check if OpenAI API key is configured
            import os
            api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('LLM_API_KEY')
            if not api_key:
                return False
            
            # Try a simple API call to verify connectivity
            import openai
            from app.modules.ai_chat.services.llm_service_impl import LLMService

            LLMService._guard_provider_call("openai", None, prompt="semantic discovery availability check")
            openai.api_key = api_key
            # Make a minimal request to check availability
            openai.Model.list()
            return True
        except Exception as e:
            logger.debug(f"LLM API not available: {e}")
            return False

    def generate_embeddings(self, texts: List[str]) -> Optional[np.ndarray]:
        """
        Generate vector embeddings for a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            Numpy array of embeddings or None if failed
        """
        if not self.embedding_model:
            logger.error("Embedding model not available")
            return None

        try:
            embeddings = self.embedding_model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                batch_size=32,
                show_progress_bar=False,
            )
            return embeddings
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            return None

    def index_vendor_products(self, limit: int = 1000) -> Dict[str, Any]:
        """
        Index vendor products for semantic search.

        Args:
            limit: Maximum number of products to index

        Returns:
            Indexing results with counts and status
        """
        if not self.product_collection or not self.embedding_model:
            return {"error": "Vector database or embedding model not available"}

        logger.info(f"Starting vendor product indexing (limit: {limit})")

        # Get vendor products with their descriptions
        products = (
            db.session.query(VendorProduct)
            .options(joinedload(VendorProduct.vendor_organization))
            .limit(limit)
            .all()
        )

        if not products:
            return {"error": "No vendor products found"}

        # Prepare texts for embedding
        product_texts = []
        product_metadata = []
        product_ids = []

        for product in products:
            # Combine product name, description, and key features
            text_parts = [
                product.name or "",
                product.description or "",
                " ".join(product.get_key_features())
                if hasattr(product, "get_key_features")
                else "",
                product.functional_scope or "",
            ]

            combined_text = " ".join(filter(None, text_parts))
            if combined_text.strip():
                product_texts.append(combined_text)
                product_metadata.append(
                    {
                        "product_id": product.id,
                        "product_name": product.name,
                        "vendor_name": product.vendor_organization.name
                        if product.vendor_organization
                        else "Unknown",
                        "product_family": product.product_family.family_name
                        if product.product_family
                        else "Unknown",
                        "category": product.product_type or "Unknown",
                    }
                )
                product_ids.append(str(product.id))

        if not product_texts:
            return {"error": "No valid product texts found"}

        # Generate embeddings
        embeddings = self.generate_embeddings(product_texts)
        if embeddings is None:
            return {"error": "Failed to generate embeddings"}

        # Clear existing collection
        try:
            self.product_collection.delete()
        except Exception as e:
            logger.warning(f"Failed to clear collection: {e}")

        # Add to ChromaDB
        try:
            self.product_collection.add(
                embeddings=embeddings.tolist(),
                documents=product_texts,
                metadatas=product_metadata,
                ids=product_ids,
            )

            logger.info(f"Successfully indexed {len(product_ids)} vendor products")
            return {
                "success": True,
                "indexed_count": len(product_ids),
                "embedding_dimension": self.embedding_dimension,
                "collection_size": self.product_collection.count(),
            }

        except Exception as e:
            logger.error(f"Failed to add embeddings to collection: {e}")
            return {"error": f"Failed to index products: {str(e)}"}

    def index_business_capabilities(self, limit: int = 500) -> Dict[str, Any]:
        """
        Index business capabilities for semantic search.

        Args:
            limit: Maximum number of capabilities to index

        Returns:
            Indexing results with counts and status
        """
        if not self.capability_collection or not self.embedding_model:
            return {"error": "Vector database or embedding model not available"}

        logger.info(f"Starting business capability indexing (limit: {limit})")

        # Get business capabilities
        capabilities = db.session.query(BusinessCapability).limit(limit).all()

        if not capabilities:
            return {"error": "No business capabilities found"}

        # Prepare texts for embedding
        capability_texts = []
        capability_metadata = []
        capability_ids = []

        for capability in capabilities:
            # Combine capability name and description
            text_parts = [capability.name or "", capability.description or ""]

            combined_text = " ".join(filter(None, text_parts))
            if combined_text.strip():
                capability_texts.append(combined_text)
                capability_metadata.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "category": capability.category or "Unknown",
                    }
                )
                capability_ids.append(str(capability.id))

        if not capability_texts:
            return {"error": "No valid capability texts found"}

        # Generate embeddings
        embeddings = self.generate_embeddings(capability_texts)
        if embeddings is None:
            return {"error": "Failed to generate embeddings"}

        # Clear existing collection
        try:
            self.capability_collection.delete()
        except Exception as e:
            logger.warning(f"Failed to clear collection: {e}")

        # Add to ChromaDB
        try:
            self.capability_collection.add(
                embeddings=embeddings.tolist(),
                documents=capability_texts,
                metadatas=capability_metadata,
                ids=capability_ids,
            )

            logger.info(f"Successfully indexed {len(capability_ids)} business capabilities")
            return {
                "success": True,
                "indexed_count": len(capability_ids),
                "embedding_dimension": self.embedding_dimension,
                "collection_size": self.capability_collection.count(),
            }

        except Exception as e:
            logger.error(f"Failed to add embeddings to collection: {e}")
            return {"error": f"Failed to index capabilities: {str(e)}"}

    def semantic_search_vendors(
        self,
        query: str,
        capability_requirements: Optional[List[str]] = None,
        n_results: int = 10,
        similarity_threshold: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Perform semantic search for vendors based on natural language query.

        Args:
            query: Natural language search query
            capability_requirements: List of specific capability requirements
            n_results: Number of results to return
            similarity_threshold: Minimum similarity score threshold

        Returns:
            Semantic search results with vendor recommendations
        """
        if not self.product_collection or not self.embedding_model:
            return {"error": "Vector database or embedding model not available"}

        # Check cache first
        cache_key = f"{hash(query)}_{n_results}_{similarity_threshold}"
        if cache_key in self._search_cache:
            cached_result = self._search_cache[cache_key]
            if datetime.now().timestamp() - cached_result["timestamp"] < self._cache_ttl:
                return cached_result["result"]

        logger.info(f"Performing semantic search for query: '{query}'")

        # Enhance query with capability requirements
        enhanced_query = query
        if capability_requirements:
            enhanced_query += " " + " ".join(capability_requirements)

        # Generate query embedding
        query_embedding = self.generate_embeddings([enhanced_query])
        if query_embedding is None:
            return {"error": "Failed to generate query embedding"}

        try:
            # Perform semantic search
            results = self.product_collection.query(
                query_embeddings=query_embedding.tolist(), n_results=n_results
            )

            # Process results
            semantic_results = []
            for i, product_id in enumerate(results["ids"][0]):
                similarity = results["distances"][0][i]

                # Filter by similarity threshold
                if similarity < similarity_threshold:
                    continue

                metadata = results["metadatas"][0][i]
                document = results["documents"][0][i]

                # Calculate capability coverage if requirements specified
                capability_coverage = 0.0
                if capability_requirements:
                    capability_coverage = self._calculate_capability_coverage(
                        int(product_id), capability_requirements
                    )

                # Determine confidence level
                confidence_level = self._determine_confidence_level(similarity, capability_coverage)

                semantic_results.append(
                    SemanticSearchResult(
                        vendor_product_id=int(product_id),
                        product_name=metadata.get("product_name", "Unknown"),
                        vendor_name=metadata.get("vendor_name", "Unknown"),
                        similarity_score=similarity,
                        capability_coverage=capability_coverage,
                        relevance_factors=self._extract_relevance_factors(document, query),
                        confidence_level=confidence_level,
                    )
                )

            # Sort by combined score (similarity + coverage)
            semantic_results.sort(
                key=lambda x: (x.similarity_score * 0.7 + x.capability_coverage * 0.3), reverse=True
            )

            result = {
                "query": query,
                "results": [sr.__dict__ for sr in semantic_results],
                "total_found": len(semantic_results),
                "search_metadata": {
                    "similarity_threshold": similarity_threshold,
                    "capability_requirements": capability_requirements,
                    "embedding_dimension": self.embedding_dimension,
                },
            }

            # Cache result
            self._search_cache[cache_key] = {
                "result": result,
                "timestamp": datetime.now().timestamp(),
            }

            return result

        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return {"error": f"Semantic search failed: {str(e)}"}

    def _calculate_capability_coverage(
        self, vendor_product_id: int, capability_requirements: List[str]
    ) -> float:
        """Calculate how well a vendor product covers required capabilities."""
        try:
            # Get capability mappings for the product
            mappings = (
                db.session.query(VendorProductCapability)
                .filter(VendorProductCapability.vendor_product_id == vendor_product_id)
                .all()
            )

            if not mappings:
                return 0.0

            total_coverage = 0.0
            for mapping in mappings:
                if mapping.business_capability and mapping.business_capability.name:
                    # Check if this capability matches any requirement
                    for req in capability_requirements:
                        if req.lower() in mapping.business_capability.name.lower():
                            total_coverage += mapping.coverage_percentage or 0.0
                            break

            # Average coverage
            return (
                min(total_coverage / len(capability_requirements), 100.0)
                if capability_requirements
                else 0.0
            )

        except Exception as e:
            logger.error(f"Failed to calculate capability coverage: {e}")
            return 0.0

    def _determine_confidence_level(self, similarity: float, coverage: float) -> str:
        """Determine confidence level based on similarity and coverage."""
        combined_score = similarity * 0.7 + (coverage / 100) * 0.3

        if combined_score >= 0.8:
            return "high"
        elif combined_score >= 0.6:
            return "medium"
        else:
            return "low"

    def _extract_relevance_factors(self, document: str, query: str) -> List[str]:
        """Extract factors that make this result relevant to the query."""
        factors = []

        # Simple keyword matching for now
        query_words = query.lower().split()
        document_words = document.lower().split()

        matched_words = set(query_words) & set(document_words)
        if matched_words:
            factors.append(f"Matches keywords: {', '.join(matched_words)}")

        # Check for capability mentions
        if "capability" in document.lower():
            factors.append("Contains capability information")

        if "feature" in document.lower():
            factors.append("Contains feature details")

        if "integration" in document.lower():
            factors.append("Mentions integration capabilities")

        return factors[:3]  # Limit to top 3 factors

    def generate_llm_recommendations(
        self,
        capability_requirements: List[str],
        organization_context: Dict[str, Any],
        top_vendors: List[SemanticSearchResult],
        budget_range: Optional[Tuple[Decimal, Decimal]] = None,
    ) -> Dict[str, Any]:
        """
        Generate LLM-powered vendor recommendations with rationales and roadmaps.

        Args:
            capability_requirements: List of required capabilities
            organization_context: Organization size, industry, etc.
            top_vendors: Top semantic search results
            budget_range: Optional budget constraints

        Returns:
            LLM-generated recommendations with detailed analysis
        """
        if not self.llm_api_available:
            logger.warning("LLM API not available — cannot generate recommendations")
            return self._empty_recommendations(
                capability_requirements, organization_context, budget_range,
                error="Recommendation service unavailable — LLM API not configured",
            )

        # Try LLM API call for enhanced recommendations
        try:
            import os
            import openai

            api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('LLM_API_KEY')
            if not api_key:
                logger.warning("No LLM API key configured — cannot generate recommendations")
                return self._empty_recommendations(
                    capability_requirements, organization_context, budget_range,
                    error="No LLM API key configured",
                )

            openai.api_key = api_key

            # Prepare prompt for vendor recommendation
            prompt = f"""Recommend the best vendor solution based on:
Requirements: {', '.join(capability_requirements)}
Organization: {organization_context}
Top Candidates: {[f"{v.vendor_name} - {v.product_name}" for v in top_vendors[:3]]}

Provide detailed analysis including:
1. Best vendor recommendation with rationale
2. Implementation roadmap (phases and duration)
3. Risk mitigation strategies
4. Alternative scenarios

Format as JSON."""

            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert enterprise architecture consultant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )

            # Parse LLM response
            llm_content = response.choices[0].message.content
            import json
            try:
                llm_result = json.loads(llm_content)
                return {
                    "recommendations": llm_result.get("recommendations", []),
                    "source": "llm",
                    "llm_api_available": True,
                }
            except json.JSONDecodeError:
                logger.warning("LLM returned non-JSON response")
                return self._empty_recommendations(
                    capability_requirements, organization_context, budget_range,
                    error="LLM returned unparseable response",
                )
        except Exception as e:
            logger.error(f"LLM recommendation call failed: {e}")
            return self._empty_recommendations(
                capability_requirements, organization_context, budget_range,
                error=f"Recommendation service error: {type(e).__name__}",
            )

    def _empty_recommendations(
        self,
        capability_requirements: List[str],
        organization_context: Dict[str, Any],
        budget_range: Optional[Tuple[Decimal, Decimal]] = None,
        error: str = "Recommendation service unavailable",
    ) -> Dict[str, Any]:
        """Return empty recommendation structure with error context."""
        return {
            "error": error,
            "recommendations": [],
            "analysis_metadata": {
                "capability_requirements": capability_requirements,
                "organization_context": organization_context,
                "budget_range": [float(budget_range[0]), float(budget_range[1])]
                if budget_range
                else None,
                "llm_api_available": False,
                "recommendation_count": 0,
            },
        }

    def get_discovery_statistics(self) -> Dict[str, Any]:
        """Get statistics about the semantic discovery system."""
        stats = {
            "embedding_model_loaded": self.embedding_model is not None,
            "vector_db_available": self.chroma_client is not None,
            "llm_api_available": self.llm_api_available,
            "device": self.device,
            "embedding_dimension": self.embedding_dimension,
        }

        if self.product_collection:
            stats["indexed_products"] = self.product_collection.count()

        if self.capability_collection:
            stats["indexed_capabilities"] = self.capability_collection.count()

        stats["search_cache_size"] = len(self._search_cache)

        return stats


# Global service instance
_semantic_discovery_service = None


def get_semantic_discovery_service() -> AISemanticDiscoveryService:
    """Get the global semantic discovery service instance."""
    global _semantic_discovery_service
    if _semantic_discovery_service is None:
        _semantic_discovery_service = AISemanticDiscoveryService()
    return _semantic_discovery_service
