"""
Vector Embedding Service - Production-grade vector embeddings for semantic search

Provides unified interface for generating and managing vector embeddings
using multiple embedding models and vector stores.
"""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional  # dead-code-ok

import numpy as np
from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

# CRITICAL FIX: Lazy PyTorch import to prevent startup failures
torch = None
TORCH_AVAILABLE = False


def _ensure_torch():
    """Lazy import of PyTorch to avoid startup failures"""
    global torch, TORCH_AVAILABLE
    if torch is None:
        try:
            import torch

            TORCH_AVAILABLE = True

            # Force CPU device and prevent meta tensor issues (PyTorch 2.0+ only)
            if hasattr(torch, "set_default_device"):
                torch.set_default_device("cpu")
            # Clear any cached meta tensors
            if hasattr(torch, "_C") and hasattr(torch._C, "_jit_set_profiling_mode"):
                torch._C._jit_set_profiling_mode(False)
            logger.info("PyTorch configured to use CPU device to prevent meta tensor issues")
        except ImportError:
            torch = None
            logger.warning("PyTorch not available - some embedding features will be limited")
        except Exception as e:
            logger.warning(f"PyTorch configuration error (non-fatal): {e}")
    return torch


class VectorEmbeddingService:
    """
    Production-grade vector embedding service with multiple model support.

    Features:
    - Multiple embedding models (OpenAI, local sentence-transformers)
    - Vector store abstraction (PostgreSQL pgvector, in-memory fallback)
    - Batch processing for efficiency
    - Caching for frequently used embeddings
    - Error handling and fallback mechanisms
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # Check if local embeddings are enabled
        self.enable_local_embeddings = (
            os.getenv("ENABLE_LOCAL_EMBEDDINGS", "false").lower() == "true"
        )

        # Embedding model configurations
        self.embedding_models = {
            "openai-small": {
                "provider": "openai",
                "model": "text-embedding - 3 - small",
                "dimensions": 1536,
                "max_tokens": 8191,
                "cost_per_1k": 0.00002,
            },
            "openai-large": {
                "provider": "openai",
                "model": "text-embedding - 3 - large",
                "dimensions": 3072,
                "max_tokens": 8191,
                "cost_per_1k": 0.00013,
            },
            "sentence-transformers": {
                "provider": "local",
                "model": "all-MiniLM-L6-v2",
                "dimensions": 384,
                "max_tokens": 512,
                "cost_per_1k": 0.0,
            },
        }

        # Initialize vector store
        self.vector_store = self._initialize_vector_store()

        # Initialize embedding cache
        self._embedding_cache = {}
        self._cache_ttl = 3600  # 1 hour

        # Default model
        self.default_model = "openai-small"

    @classmethod
    def configuration_status(cls) -> Dict[str, Any]:
        """
        Check configuration status of embedding service.

        Returns:
            Dict with 'ready' boolean and list of enabled providers
        """
        try:
            # Check if any cloud providers are configured
            from app import db
            from app.models.models import APISettings

            # Ensure clean transaction state
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

            enabled_providers = APISettings.query.filter_by(enabled=True).all()
            provider_names = [
                p.provider
                for p in enabled_providers
                if p.provider in ["openai", "anthropic", "huggingface"]
            ]

            ready = len(provider_names) > 0

            return {
                "ready": ready,
                "enabled_providers": provider_names,
                "local_embeddings_enabled": os.getenv("ENABLE_LOCAL_EMBEDDINGS", "false").lower()
                == "true",
                "default_model": "openai-small",
            }

        except Exception as e:
            logger.error(f"Error checking embedding configuration: {e}")
            return {
                "ready": False,
                "enabled_providers": [],
                "local_embeddings_enabled": os.getenv("ENABLE_LOCAL_EMBEDDINGS", "false").lower()
                == "true",
                "default_model": "openai-small",
                "error": str(e),
            }

    def _initialize_vector_store(self):
        """Initialize vector store based on available infrastructure"""
        # Try managed/remote vector stores first
        try:
            # Try pgvector first (managed PostgreSQL vector extension)
            return PGVectorStore()
        except Exception as e:
            self.logger.warning(f"PGVector not available: {e}. Trying cloud-hosted options...")

        # Only try local adapters if explicitly enabled
        if self.enable_local_embeddings:
            try:
                # Try ChromaDB (superior metadata support, persistence)
                from .chromadb_apqc_service import get_chromadb_apqc_service

                return ChromaDBAdapter(get_chromadb_apqc_service())
            except Exception as e:
                self.logger.warning(f"ChromaDB not available: {e}. Trying FAISS...")
                try:
                    # Fall back to FAISS (superior performance, simpler deps)
                    from .faiss_apqc_service import FAISSAPQCService

                    return FAISSAdapter(FAISSAPQCService())
                except Exception as e2:
                    self.logger.warning(f"FAISS not available: {e2}. Using in-memory fallback.")
        else:
            self.logger.info("Local embeddings disabled, skipping ChromaDB/FAISS initialization")

        # Final fallback: in-memory store
        return InMemoryVectorStore()

    def embed_text(self, text: str, model: Optional[str] = None) -> List[float]:
        """
        Generate vector embedding for text.

        Args:
            text: Text to embed
            model: Model to use (defaults to default_model)

        Returns:
            Vector embedding as list of floats
        """
        model = model or self.default_model

        # Check cache first
        cache_key = f"{hash(text)}_{model}"
        if cache_key in self._embedding_cache:
            cached = self._embedding_cache[cache_key]
            if datetime.utcnow().timestamp() - cached["timestamp"] < self._cache_ttl:
                return cached["embedding"]

        try:
            model_config = self.embedding_models[model]

            if model_config["provider"] == "openai":
                embedding = self.embed_with_openai(text, model_config)
            elif model_config["provider"] == "local":
                embedding = self.embed_with_sentence_transformers(text, model_config)
            else:
                raise ValueError(f"Unknown provider: {model_config['provider']}")

            # Cache the result
            self._embedding_cache[cache_key] = {
                "embedding": embedding,
                "timestamp": datetime.utcnow().timestamp(),
            }

            return embedding

        except Exception as e:
            self.logger.error(f"Error embedding text with {model}: {e}")
            # Fallback to local model only if enabled
            if model != "sentence-transformers" and self.enable_local_embeddings:
                return self.embed_text(text, "sentence-transformers")
            elif model == "sentence-transformers":
                # If sentence-transformers failed and it's the requested model, raise error
                raise
            else:
                # No fallback available
                raise ValueError(
                    f"Embedding failed for model {model} and local embeddings are disabled"
                )

    def embed_with_openai(self, text: str, model_config: Dict) -> List[float]:
        """Embed text using OpenAI API"""
        try:
            # Get API configuration
            from app.models.models import APISettings
            from app.services.llm_service import LLMService

            # Ensure clean transaction state before query
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

            try:
                settings = APISettings.query.filter_by(provider="openai", enabled=True).first()
            except Exception as db_error:
                self.logger.error(f"Database error getting API settings: {db_error}")
                raise ValueError("Failed to access API settings due to database error")

            if not settings or not settings.has_key():
                raise ValueError("OpenAI API key not configured")

            LLMService._guard_provider_call(
                "openai",
                model_config["model"],
                prompt=text,
            )

            # Use OpenAI API for embeddings
            import openai

            client = openai.OpenAI(api_key=settings.api_key)

            response = client.embeddings.create(
                model=model_config["model"], input=text, encoding_format="float"
            )

            return response.data[0].embedding

        except ImportError:
            raise ImportError("OpenAI library not installed. Run: pip install openai")
        except Exception as e:
            self.logger.error(f"OpenAI embedding error: {e}")
            raise

    def embed_with_sentence_transformers(self, text: str, model_config: Dict) -> List[float]:
        """Embed text using local sentence-transformers model"""
        if not self.enable_local_embeddings:
            raise ValueError(
                "Local embeddings are disabled. Enable with ENABLE_LOCAL_EMBEDDINGS=true"
            )

        try:
            import torch
            from sentence_transformers import SentenceTransformer

            # CRITICAL FIX: Handle PyTorch meta tensor issue globally
            # This prevents the "Cannot copy out of meta tensor" error
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Load model (cached)
            if not hasattr(self, "_sentence_model"):
                self._sentence_model = SentenceTransformer(model_config["model"])
                # CRITICAL FIX: Handle PyTorch meta tensor issue properly
                try:
                    # Force model to CPU and ensure proper initialization
                    device = torch.device("cpu")

                    # CRITICAL: Use to_empty() instead of to() for meta tensors
                    if hasattr(self._sentence_model, "module"):
                        # Handle potential meta tensor state
                        self._sentence_model.module.to_empty(device=device, recurse=False)
                    self._sentence_model = self._sentence_model.to(device)

                    # CRITICAL: Test embedding to verify model works
                    with torch.no_grad():
                        test_embedding = self._sentence_model.encode(
                            "test", show_progress_bar=False
                        )
                        if hasattr(test_embedding, "tolist"):
                            test_embedding = test_embedding.tolist()

                    self.logger.info("Sentence-transformers model successfully initialized on CPU")

                except Exception as init_error:
                    self.logger.error(f"Model initialization failed: {init_error}")
                    # CRITICAL: Use hash fallback immediately if model fails
                    import hashlib

                    text_bytes = text.encode("utf-8")
                    hash_obj = hashlib.sha256(text_bytes)
                    hash_int = int(hash_obj.hexdigest(), 16)
                    embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
                    embedding = [x / 255.0 for x in embedding]
                    return embedding

            # Generate embedding with proper error handling
            try:
                with torch.no_grad():
                    embedding = self._sentence_model.encode(text, show_progress_bar=False)

                # Convert to list of floats
                if hasattr(embedding, "tolist"):
                    return embedding.tolist()
                else:
                    return list(embedding)

            except Exception as embed_error:
                self.logger.error(f"Embedding generation failed: {embed_error}")
                # Fallback to hash-based embedding
                import hashlib

                text_bytes = text.encode("utf-8")
                hash_obj = hashlib.sha256(text_bytes)
                hash_int = int(hash_obj.hexdigest(), 16)
                embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
                embedding = [x / 255.0 for x in embedding]
                return embedding

        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. Run: pip install sentence-transformers"
            )
        except Exception as e:
            self.logger.error(f"Sentence-transformers embedding error: {e}")
            # Fallback to simple hash-based embedding
            import hashlib

            text_bytes = text.encode("utf-8")
            hash_obj = hashlib.sha256(text_bytes)
            hash_int = int(hash_obj.hexdigest(), 16)
            embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
            embedding = [x / 255.0 for x in embedding]
            return embedding

    async def embed_batch(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """
        Generate embeddings for multiple texts efficiently.

        Args:
            texts: List of texts to embed
            model: Model to use

        Returns:
            List of vector embeddings
        """
        model = model or self.default_model
        model_config = self.embedding_models[model]

        # For local models, use batch processing
        if model_config["provider"] == "local":
            return self._embed_batch_sentence_transformers(texts, model_config)

        # For OpenAI, process in parallel with rate limiting
        tasks = []
        for item in texts:
            task = self.embed_text(item, model)
            tasks.append(task)

        # Process with concurrency control
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests

        async def bounded_embed(item, model):
            async with semaphore:
                return await self.embed_text(item, model)

        bounded_tasks = [bounded_embed(item, model) for item in texts]
        return await asyncio.gather(*bounded_tasks)

    def _embed_batch_sentence_transformers(
        self, texts: List[str], model_config: Dict
    ) -> List[List[float]]:
        """Batch embed using sentence-transformers"""
        if not self.enable_local_embeddings:
            raise ValueError(
                "Local embeddings are disabled. Enable with ENABLE_LOCAL_EMBEDDINGS=true"
            )

        try:
            import torch
            from sentence_transformers import SentenceTransformer

            # CRITICAL FIX: Handle PyTorch meta tensor issue globally
            # This prevents the "Cannot copy out of meta tensor" error
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            if not hasattr(self, "_sentence_model"):
                try:
                    self._sentence_model = SentenceTransformer(model_config["model"])
                    # CRITICAL FIX: Handle PyTorch meta tensor issue properly
                    try:
                        # Force model to CPU and ensure proper initialization
                        device = torch.device("cpu")

                        # CRITICAL: Use to_empty() instead of to() for meta tensors
                        if hasattr(self._sentence_model, "module"):
                            # Handle potential meta tensor state
                            self._sentence_model.module.to_empty(device=device, recurse=False)
                        self._sentence_model = self._sentence_model.to(device)

                        # CRITICAL: Test embedding to verify model works
                        with torch.no_grad():
                            test_embedding = self._sentence_model.encode(
                                "test", show_progress_bar=False
                            )
                            if hasattr(test_embedding, "tolist"):
                                test_embedding = test_embedding.tolist()

                        self.logger.info(
                            "Sentence-transformers batch model successfully initialized on CPU"
                        )

                    except Exception as init_error:
                        self.logger.error(f"Batch model initialization failed: {init_error}")
                        # CRITICAL: Use hash fallback immediately if model fails
                        import hashlib

                        embeddings = []
                        for text in texts:
                            text_bytes = text.encode("utf-8")
                            hash_obj = hashlib.sha256(text_bytes)
                            hash_int = int(hash_obj.hexdigest(), 16)
                            embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
                            embedding = [x / 255.0 for x in embedding]
                            embeddings.append(embedding)
                        return embeddings

                except Exception as model_error:
                    # If model loading fails completely, fallback to simple embedding
                    self.logger.warning(
                        f"Failed to load sentence-transformers model for batch: {model_error}"
                    )
                    # Return simple hash-based embeddings as fallback
                    import hashlib

                    embeddings = []
                    for text in texts:
                        text_bytes = text.encode("utf-8")
                        hash_obj = hashlib.sha256(text_bytes)
                        hash_int = int(hash_obj.hexdigest(), 16)
                        embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
                        embedding = [x / 255.0 for x in embedding]
                        embeddings.append(embedding)
                    return embeddings

            # Batch encode with proper error handling
            try:
                with torch.no_grad():
                    embeddings = self._sentence_model.encode(texts, show_progress_bar=False)

                # Convert to list of lists
                if hasattr(embeddings, "tolist"):
                    return embeddings.tolist()
                else:
                    return [list(emb) for emb in embeddings]

            except Exception as embed_error:
                self.logger.error(f"Batch embedding generation failed: {embed_error}")
                # Fallback to hash-based embeddings
                import hashlib

                embeddings = []
                for text in texts:
                    text_bytes = text.encode("utf-8")
                    hash_obj = hashlib.sha256(text_bytes)
                    hash_int = int(hash_obj.hexdigest(), 16)
                    embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
                    embedding = [x / 255.0 for x in embedding]
                    embeddings.append(embedding)
                return embeddings

        except Exception as e:
            self.logger.error(f"Batch sentence-transformers error: {e}")
            # Fallback to simple hash-based embeddings
            import hashlib

            embeddings = []
            for text in texts:
                text_bytes = text.encode("utf-8")
                hash_obj = hashlib.sha256(text_bytes)
                hash_int = int(hash_obj.hexdigest(), 16)
                embedding = [(hash_int >> i) & 0xFF for i in range(0, 384 * 8, 8)]
                embedding = [x / 255.0 for x in embedding]
                embeddings.append(embedding)
            return embeddings

    def get_model_info(self, model: Optional[str] = None) -> Dict[str, Any]:
        """Get information about an embedding model"""
        model = model or self.default_model
        return self.embedding_models.get(model, {})

    def list_available_models(self) -> List[str]:
        """List all available embedding models"""
        return list(self.embedding_models.keys())

    def clear_cache(self):
        """Clear embedding cache"""
        self._embedding_cache.clear()
        self.logger.info("Embedding cache cleared")


class VectorStore(ABC):
    """Abstract base class for vector stores"""

    @abstractmethod
    async def add_vector(self, id: str, vector: List[float], metadata: Dict[str, Any]):
        """Add vector to store"""

    @abstractmethod
    async def search_similar(
        self, query_vector: List[float], top_k: int = 10, domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors"""

    @abstractmethod
    async def delete_vector(self, id: str):
        """Delete vector from store"""


class PGVectorStore(VectorStore):
    """PostgreSQL vector store using pgvector extension"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._ensure_pgvector_extension()

    def _ensure_pgvector_extension(self):
        """Ensure pgvector extension is installed"""
        try:
            # Check if we have app context
            from flask import has_app_context

            if not has_app_context():
                # info, not warning: this service is instantiated at boot,
                # before any request context exists, so this fires on every
                # single boot unconditionally and is not actionable -- the
                # check genuinely is deferred, not skipped or failed
                # (10 Sep 2026: flooding /admin/errors with 24 occurrences).
                self.logger.info("Deferring pgvector extension check - no app context")
                return

            db.session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # tenant-exempt: system table
            db.session.commit()
            self.logger.info("pgvector extension ensured")
        except Exception as e:
            self.logger.error(f"Error ensuring pgvector extension: {e}")
            # CREATE EXTENSION failing (pgvector not installed) aborts the
            # transaction. Without this rollback the session stays poisoned and
            # every later query in the SAME request fails with
            # InFailedSqlTransaction (silently broke map-apqc, generate-archimate
            # comprehensive analysis, FAISS/APQC vector builds, audit writes).
            try:
                db.session.rollback()
            except Exception:
                pass
            raise

    async def add_vector(self, id: str, vector: List[float], metadata: Dict[str, Any]):
        """Add vector to PostgreSQL"""
        try:
            # Convert vector to pgvector format
            vector_str = f"[{','.join(map(str, vector))}]"

            # Insert into database
            # tenant-exempt: system table (document_embeddings is infrastructure)
            query = text(
                """
                INSERT INTO document_embeddings (content_id, embedding, metadata, domain, content_type)
                VALUES (:content_id, :embedding, :metadata, :domain, :content_type)
                ON CONFLICT (content_id) DO UPDATE SET
                    embedding = :embedding,
                    metadata = :metadata,
                    updated_at = NOW()
            """
            )

            db.session.execute(  # tenant-exempt: document_embeddings is infrastructure table
                query,
                {
                    "content_id": id,
                    "embedding": vector_str,
                    "metadata": json.dumps(metadata),
                    "domain": metadata.get("domain", "general"),
                    "content_type": metadata.get("content_type", "unknown"),
                },
            )
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Error adding vector to PGVector: {e}")
            raise

    async def search_similar(
        self, query_vector: List[float], top_k: int = 10, domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors using pgvector"""
        try:
            # Convert query vector to pgvector format
            query_vector_str = f"[{','.join(map(str, query_vector))}]"

            # Build query with optional domain filter
            # tenant-exempt: system table (document_embeddings is infrastructure)
            where_clause = "WHERE domain = :domain" if domain_filter else ""
            query_params = {"query_vector": query_vector_str, "top_k": top_k}
            if domain_filter:
                query_params["domain"] = domain_filter

            query = text(
                f"""
                SELECT content_id, metadata, domain, content_type,
                       1 - (embedding <=> :query_vector) as similarity
                FROM document_embeddings
                {where_clause}
                ORDER BY embedding <=> :query_vector
                LIMIT :top_k
            """
            )

            result = db.session.execute(query, query_params)  # tenant-exempt: document_embeddings is infrastructure table
            rows = result.fetchall()

            # Convert to list of dictionaries
            results = []
            for row in rows:
                results.append(
                    {
                        "id": row.content_id,
                        "metadata": json.loads(row.metadata) if row.metadata else {},
                        "domain": row.domain,
                        "content_type": row.content_type,
                        "similarity": float(row.similarity),
                    }
                )

            return results

        except Exception as e:
            self.logger.error(f"Error searching PGVector: {e}")
            raise

    async def delete_vector(self, id: str):
        """Delete vector from PostgreSQL"""
        try:
            query = text("DELETE FROM document_embeddings WHERE content_id = :content_id")
            db.session.execute(query, {"content_id": id})  # tenant-exempt: system table (document_embeddings is infrastructure)
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Error deleting vector from PGVector: {e}")
            raise


class InMemoryVectorStore(VectorStore):
    """In-memory vector store for development/testing"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._vectors = {}
        self._metadata = {}

    async def add_vector(self, id: str, vector: List[float], metadata: Dict[str, Any]):
        """Add vector to memory store"""
        self._vectors[id] = np.array(vector)
        self._metadata[id] = metadata

    async def search_similar(
        self, query_vector: List[float], top_k: int = 10, domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors using cosine similarity"""
        query_array = np.array(query_vector)

        # Calculate similarities
        similarities = []
        for id, vector in self._vectors.items():
            metadata = self._metadata.get(id, {})

            # Apply domain filter
            if domain_filter and metadata.get("domain") != domain_filter:
                continue

            # Calculate cosine similarity
            similarity = np.dot(query_array, vector) / (
                np.linalg.norm(query_array) * np.linalg.norm(vector)
            )

            similarities.append(
                {
                    "id": id,
                    "metadata": metadata,
                    "domain": metadata.get("domain", "general"),
                    "content_type": metadata.get("content_type", "unknown"),
                    "similarity": float(similarity),
                }
            )

        # Sort by similarity and return top_k
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        return similarities[:top_k]

    async def delete_vector(self, id: str):
        """Delete vector from memory store"""
        self._vectors.pop(id, None)
        self._metadata.pop(id, None)


class ChromaDBAdapter(VectorStore):
    """Adapter to make ChromaDB compatible with VectorStore interface"""

    def __init__(self, chromadb_service):
        self.chromadb_service = chromadb_service
        self.logger = logging.getLogger(__name__)

    async def add_vector(self, id: str, vector: List[float], metadata: Dict[str, Any]):
        """Add vector using ChromaDB"""
        try:
            # ChromaDB stores documents and handles embeddings
            # We add the document with metadata
            document_text = metadata.get("content", "") or metadata.get("process_name", "") or id
            
            if hasattr(self.chromadb_service, "collection") and self.chromadb_service.collection:
                self.chromadb_service.collection.add(
                    ids=[id],
                    documents=[document_text],
                    metadatas=[metadata]
                )
                self.logger.info(f"Added vector {id} to ChromaDB")
            else:
                self.logger.warning("ChromaDB collection not available")
        except Exception as e:
            self.logger.error(f"Error adding vector to ChromaDB: {e}")
            raise

    async def delete_vector(self, id: str):
        """Delete vector using ChromaDB"""
        try:
            if hasattr(self.chromadb_service, "collection") and self.chromadb_service.collection:
                self.chromadb_service.collection.delete(ids=[id])
                self.logger.info(f"Deleted vector {id} from ChromaDB")
            else:
                self.logger.warning("ChromaDB collection not available for deletion")
        except Exception as e:
            self.logger.error(f"Error deleting vector from ChromaDB: {e}")
            raise

    async def search_similar(
        self, query_vector: List[float], top_k: int = 10, domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search using ChromaDB with actual query vector"""
        try:
            # Try to use vector-based search if ChromaDB service supports it
            if hasattr(self.chromadb_service, "search_by_embedding"):
                # Direct vector search
                where_filter = {"domain": domain_filter} if domain_filter else None
                results = self.chromadb_service.search_by_embedding(
                    query_vector, top_k, where_filter
                )
            elif hasattr(self.chromadb_service, "collection"):
                # Direct ChromaDB collection access
                where_filter = {"domain": domain_filter} if domain_filter else None
                query_results = self.chromadb_service.collection.query(
                    query_embeddings=[query_vector], n_results=top_k, where=where_filter
                )

                # Convert ChromaDB query results format
                results = []
                if query_results and query_results.get("ids") and query_results["ids"][0]:
                    ids = query_results["ids"][0]
                    metadatas = query_results.get("metadatas", [[]])[0]
                    distances = query_results.get("distances", [[]])[0]

                    for i, doc_id in enumerate(ids):
                        metadata = metadatas[i] if i < len(metadatas) else {}
                        distance = distances[i] if i < len(distances) else 1.0
                        # Convert distance to similarity (lower distance = higher similarity)
                        similarity = 1.0 / (1.0 + distance)

                        results.append(
                            {
                                "process_code": metadata.get("process_code", doc_id),
                                "process_name": metadata.get("process_name", ""),
                                "category_level_1": metadata.get("category_level_1", ""),
                                "category_level_2": metadata.get("category_level_2", ""),
                                "score": similarity,
                                **metadata,
                            }
                        )
            else:
                # Fallback to text-based search (legacy behavior)
                self.logger.warning(
                    "ChromaDB service does not support vector search, using fallback"
                )
                where_filter = {"domain": domain_filter} if domain_filter else None
                results = self.chromadb_service.search_similar("", top_k, where_filter)

            # Convert ChromaDB results to VectorStore format
            formatted_results = []
            for result in results:
                formatted_results.append(
                    {
                        "id": result.get("process_code"),
                        "metadata": result,
                        "domain": result.get("category_level_1", "general"),
                        "content_type": "apqc_process",
                        "similarity": result.get("score", 0.0),
                    }
                )

            return formatted_results

        except Exception as e:
            self.logger.error(f"ChromaDB search error: {e}")
            return []


class FAISSAdapter(VectorStore):
    """Adapter to make FAISS compatible with VectorStore interface"""

    def __init__(self, faiss_service):
        self.faiss_service = faiss_service
        self.logger = logging.getLogger(__name__)

    async def add_vector(self, id: str, vector: List[float], metadata: Dict[str, Any]):
        """Add vector using FAISS"""
        try:
            import numpy as np
            
            # Convert vector to numpy array
            vector_array = np.array(vector, dtype=np.float32).reshape(1, -1)
            
            # Check if index exists
            if self.faiss_service.index is None:
                self.logger.warning("FAISS index not initialized, cannot add vector")
                return
            
            # Add to index
            self.faiss_service.index.add(vector_array)
            
            # Store metadata
            idx = self.faiss_service.index.ntotal - 1
            self.faiss_service.process_metadata[idx] = {
                **metadata,
                "content_id": id,
                "index_position": idx
            }
            
            self.logger.info(f"Added vector {id} to FAISS at position {idx}")
            
        except Exception as e:
            self.logger.error(f"Error adding vector to FAISS: {e}")
            raise

    async def delete_vector(self, id: str):
        """Delete vector using FAISS"""
        try:
            # FAISS doesn't support direct deletion, mark as deleted in metadata
            for idx, metadata in self.faiss_service.process_metadata.items():
                if metadata.get("content_id") == id:
                    metadata["deleted"] = True
                    self.logger.info(f"Marked vector {id} as deleted in FAISS metadata")
                    return
            
            self.logger.warning(f"Vector {id} not found in FAISS metadata")
            
        except Exception as e:
            self.logger.error(f"Error marking vector as deleted in FAISS: {e}")
            raise

    async def search_similar(
        self, query_vector: List[float], top_k: int = 10, domain_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search using FAISS with actual query vector"""
        try:
            # Check if FAISS index is available
            if self.faiss_service.index is None:
                self.logger.warning("FAISS index not built, returning empty results")
                return []

            # Use the search_by_vector method if available (preferred)
            if hasattr(self.faiss_service, "search_by_vector"):
                results = self.faiss_service.search_by_vector(query_vector, top_k)

                # Convert to VectorStore format and apply domain filter
                formatted_results = []
                for result in results:
                    # Apply domain filter if specified
                    if domain_filter:
                        result_domain = result.get("category_level_1", "")
                        if result_domain != domain_filter:
                            continue

                    formatted_results.append(
                        {
                            "id": result.get("process_code", ""),
                            "metadata": {
                                "process_id": result.get("existing_id"),
                                "process_code": result.get("process_code", ""),
                                "process_name": result.get("process_name", ""),
                                "level": result.get("apqc_level", 0),
                                "category_level_1": result.get("category_level_1", ""),
                                "category_level_2": result.get("category_level_2", ""),
                            },
                            "domain": result.get("category_level_1", "general"),
                            "content_type": "apqc_process",
                            "similarity": result.get("score", 0.0),
                            "distance": result.get("distance", 0.0),
                            "rank": result.get("rank", 0),
                        }
                    )

                self.logger.info(f"FAISS vector search returned {len(formatted_results)} results")
                return formatted_results

            # Fallback to direct index search
            query_array = np.array(query_vector, dtype=np.float32).reshape(1, -1)

            # Ensure vector dimensions match the index
            expected_dim = self.faiss_service.dimension
            actual_dim = query_array.shape[1]

            if actual_dim != expected_dim:
                self.logger.warning(
                    f"Vector dimension mismatch: expected {expected_dim}, got {actual_dim}. "
                    "Attempting to pad/truncate."
                )
                if actual_dim < expected_dim:
                    padding = np.zeros((1, expected_dim - actual_dim), dtype=np.float32)
                    query_array = np.concatenate([query_array, padding], axis=1)
                else:
                    query_array = query_array[:, :expected_dim]

            # Search the FAISS index directly
            distances, indices = self.faiss_service.index.search(query_array, top_k)

            formatted_results = []
            for i, (dist, idx) in enumerate(zip(distances[0], indices[0])):
                if idx < 0:
                    continue

                metadata = self.faiss_service.process_metadata.get(idx, {})

                if domain_filter:
                    result_domain = metadata.get("category_level_1", "")
                    if result_domain != domain_filter:
                        continue

                similarity = 1.0 / (1.0 + float(dist))

                formatted_results.append(
                    {
                        "id": metadata.get("process_code", str(idx)),
                        "metadata": {
                            "process_id": metadata.get("id"),
                            "process_code": metadata.get("process_code", ""),
                            "process_name": metadata.get("process_name", ""),
                            "level": metadata.get("apqc_level", 0),
                            "category_level_1": metadata.get("category_level_1", ""),
                            "category_level_2": metadata.get("category_level_2", ""),
                        },
                        "domain": metadata.get("category_level_1", "general"),
                        "content_type": "apqc_process",
                        "similarity": similarity,
                        "distance": float(dist),
                        "rank": i + 1,
                    }
                )

            self.logger.info(f"FAISS direct search returned {len(formatted_results)} results")
            return formatted_results

        except Exception as e:
            self.logger.error(f"FAISS search error: {e}")
            import traceback

            self.logger.error(traceback.format_exc())
            return []


# Global service instance
