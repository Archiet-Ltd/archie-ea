"""
-> app.modules.ai_chat.services.llm_service

LLM Router Service

Multi-provider LLM router with intelligent model switching, caching,
throttling, metrics, and request-level provenance tracking.

Key Features:
- Dynamic provider switching based on availability and cost
- Request/response caching with TTL
- Rate limiting and throttling per provider
- Comprehensive metrics and monitoring
- Provenance tracking for all LLM interactions
- Fallback strategies and error handling

Provider Priority (configurable):
1. anthropic (Claude - highest quality)
2. openai (GPT - 4 - fast, reliable)
3. gemini (Google - cost-effective)
4. openrouter (free models)
5. nvidia (free models, fallback)
5a. azure (enterprise option)
6. huggingface (free, local models - last resort)

Caching Strategy:
- Exact match caching for identical prompts
- Semantic similarity caching for similar prompts
- TTL-based cache expiration
- Cache hit/miss metrics
"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship  # dead-code-ok

from app import db
from app.services.llm_cache import get_cache as get_llm_cache
from app.modules.ai_chat.services.llm_service_impl import LLMService as _LLMServiceImpl


def get_llm_service():
    """Factory function for LLMService."""
    return _LLMServiceImpl()


logger = logging.getLogger(__name__)

Base = declarative_base()


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    NVIDIA = "nvidia"
    AZURE = "azure"
    HUGGINGFACE = "huggingface"
    OPENROUTER = "openrouter"


class LLMRequest(Base):
    """LLM request audit log."""

    __tablename__ = "llm_requests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(String(36), unique=True, nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_hash = Column(String(64), nullable=False)  # SHA256 of prompt
    prompt_length = Column(Integer)
    max_tokens = Column(Integer)
    temperature = Column(Float)
    user_id = Column(String(36))
    session_id = Column(String(36))
    request_type = Column(String(50))  # generation, completion, embedding, etc.
    timestamp = Column(DateTime, default=datetime.utcnow)
    processing_time_ms = Column(Integer)
    tokens_used = Column(Integer)
    cost_estimate = Column(Float)
    cached = Column(Boolean, default=False)
    success = Column(Boolean, default=False)
    error_message = Column(Text)

    response = relationship("LLMResponse", back_populates="request", uselist=False)


class LLMResponse(Base):
    """LLM response with provenance."""

    __tablename__ = "llm_responses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(String(36), ForeignKey("llm_requests.id"))
    response_text = Column(Text)
    response_metadata = Column(JSON)  # Token counts, finish reason, etc.
    confidence_score = Column(Float)
    provenance_data = Column(JSON)  # Model version, provider metadata, etc.

    request = relationship("LLMRequest", back_populates="response")


class LLMMetrics(Base):
    """LLM performance metrics."""

    __tablename__ = "llm_metrics"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime, default=datetime.utcnow)
    provider = Column(String(50))
    metric_type = Column(String(50))  # latency, throughput, cost, etc.
    value = Column(Float)
    labels = Column(JSON)


@dataclass
class RouterConfig:
    """LLM router configuration."""

    provider_priority: List[LLMProvider] = None
    cache_ttl_seconds: int = 3600
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    rate_limit_per_minute: int = 60
    enable_metrics: bool = True
    enable_provenance: bool = True
    fallback_enabled: bool = True

    def __post_init__(self):
        if self.provider_priority is None:
            self.provider_priority = [
                LLMProvider.OPENAI,
                LLMProvider.ANTHROPIC,
                LLMProvider.GEMINI,
                LLMProvider.OPENROUTER,
                LLMProvider.NVIDIA,
                LLMProvider.AZURE,
                LLMProvider.HUGGINGFACE,
            ]


@dataclass
class LLMRequestContext:
    """Context for LLM request."""

    request_id: str
    prompt: str
    max_tokens: int = 1000
    temperature: float = 0.7
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_type: str = "generation"
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class LLMResponseContext:
    """Context for LLM response."""

    request_id: str
    provider: LLMProvider
    model: str
    response_text: str
    processing_time_ms: int
    tokens_used: int
    cost_estimate: float
    cached: bool = False
    metadata: Optional[Dict[str, Any]] = None


class RateLimiter:
    """Simple rate limiter for LLM providers."""

    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: List[float] = []

    def allow_request(self) -> bool:
        """Check if request is allowed under rate limit."""
        now = time.time()
        # Remove old requests
        self.requests = [t for t in self.requests if now - t < 60]

        if len(self.requests) < self.requests_per_minute:
            self.requests.append(now)
            return True
        return False

    def get_remaining_requests(self) -> int:
        """Get remaining requests in current window."""
        now = time.time()
        self.requests = [t for t in self.requests if now - t < 60]
        return max(0, self.requests_per_minute - len(self.requests))


class LLMRouter:
    """Intelligent LLM router with caching and provenance."""

    def __init__(self, config: Optional[RouterConfig] = None):
        self.config = config or RouterConfig()
        self.llm_service = None
        self.cache = None
        self.rate_limiters: Dict[LLMProvider, RateLimiter] = {}
        self._initialized = False

        # Initialize rate limiters
        for provider in LLMProvider:
            self.rate_limiters[provider] = RateLimiter(
                self.config.rate_limit_per_minute
            )

    async def initialize(self):
        """Initialize router components."""
        if self._initialized:
            return

        try:
            self.llm_service = get_llm_service()
            self.cache = get_llm_cache()
            self._initialized = True
            logger.info("LLM router initialized successfully")
        except Exception as e:
            logger.error(f"LLM router initialization failed: {e}")
            raise

    async def generate(self, context: LLMRequestContext) -> Dict[str, Any]:
        """Generate text using optimal LLM provider with caching."""
        start_time = datetime.utcnow()

        # Generate prompt hash for caching
        prompt_hash = hashlib.sha256(context.prompt.encode()).hexdigest()

        # Check cache first
        cache_key = f"{context.request_type}:{prompt_hash}:{context.max_tokens}:{context.temperature}"
        cached_response = await self._check_cache(cache_key)

        if cached_response:
            processing_time = int(
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )
            await self._record_request(
                context, None, None, processing_time, cached=True, success=True
            )
            return {
                **cached_response,
                "cached": True,
                "processing_time_ms": processing_time,
            }

        # Try providers in priority order
        last_error = None
        for provider in self.config.provider_priority:
            try:
                # Check rate limit
                if not self.rate_limiters[provider].allow_request():
                    logger.warning(f"Rate limit exceeded for {provider}")
                    continue

                # Make request
                provider_start = datetime.utcnow()
                response = await self._call_provider(provider, context)
                (int(
                    (datetime.utcnow() - provider_start).total_seconds() * 1000
                ))

                # Cache successful response
                await self._cache_response(
                    cache_key, response, self.config.cache_ttl_seconds
                )

                # Record successful request
                total_time = int(
                    (datetime.utcnow() - start_time).total_seconds() * 1000
                )
                await self._record_request(
                    context, provider, response, total_time, cached=False, success=True
                )

                return {
                    **response,
                    "provider": provider.value,
                    "cached": False,
                    "processing_time_ms": total_time,
                }

            except Exception as e:
                logger.warning(f"Provider {provider} failed: {e}")
                last_error = e
                continue

        # All providers failed
        total_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        await self._record_request(
            context,
            None,
            None,
            total_time,
            cached=False,
            success=False,
            error_message=str(last_error) if last_error else "All providers failed",
        )

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def _call_provider(
        self, provider: LLMProvider, context: LLMRequestContext
    ) -> Dict[str, Any]:
        """Call specific LLM provider."""
        if not self.llm_service:
            raise RuntimeError("LLM service not initialized")

        # Map provider to service method
        provider_config = {
            "provider": provider.value,
            "model": self._get_default_model(provider),
            "max_tokens": context.max_tokens,
            "temperature": context.temperature,
        }

        response = await self.llm_service.generate(
            prompt=context.prompt, **provider_config
        )

        # Normalize response
        if isinstance(response, dict):
            return response
        else:
            return {
                "text": str(response),
                "model": provider_config["model"],
                "tokens_used": getattr(response, "usage", {}).get("total_tokens", 0),
                "finish_reason": getattr(response, "finish_reason", "unknown"),
            }

    def _get_default_model(self, provider: LLMProvider) -> str:
        """Get default model for provider."""
        # DB is the source of truth — only fall back if the DB has nothing configured
        try:
            from app.models.models import APISettings
            prov_str = provider.value if hasattr(provider, "value") else str(provider)
            row = APISettings.query.filter_by(provider=prov_str).order_by(
                APISettings.enabled.desc()
            ).first()
            if row and row.default_model and row.default_model.strip():
                return row.default_model.strip()
        except Exception as exc:
            logger.debug("suppressed error in LLMRouter._get_default_model (app/modules/ai_chat/services/llm_router.py): %s", exc)
        # Last-resort fallbacks — update these in Admin > API Settings instead
        defaults = {
            LLMProvider.ANTHROPIC: "claude-haiku-4-5-20251001",
            LLMProvider.OPENAI: "gpt-4o-mini",
            LLMProvider.GEMINI: "gemini-2.0-flash",
            LLMProvider.NVIDIA: "nvidia/nemotron-3-ultra-550b-a55b",
            LLMProvider.OPENROUTER: "google/gemma-4-31b-it:free",
            LLMProvider.AZURE: "gpt-4o",
            LLMProvider.HUGGINGFACE: "meta-llama/Llama-3.1-8B-Instruct",
        }
        return defaults.get(provider, "default-model")

    async def _check_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Check cache for response."""
        if not self.cache:
            return None

        try:
            cached = await self.cache.get(cache_key)
            if cached:
                # Parse cached response
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Cache read failed: {e}")

        return None

    async def _cache_response(self, cache_key: str, response: Dict[str, Any], ttl: int):
        """Cache response."""
        if not self.cache:
            return

        try:
            await self.cache.set(cache_key, json.dumps(response), ttl)
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")

    async def _record_request(
        self,
        context: LLMRequestContext,
        provider: Optional[LLMProvider],
        response: Optional[Dict[str, Any]],
        processing_time: int,
        cached: bool = False,
        success: bool = True,
        error_message: Optional[str] = None,
    ):
        """Record LLM request for provenance."""
        if not self.config.enable_provenance:
            return

        try:
            request_record = LLMRequest(
                request_id=context.request_id,
                provider=provider.value if provider else None,
                model=response.get("model") if response else None,
                prompt_hash=hashlib.sha256(context.prompt.encode()).hexdigest(),
                prompt_length=len(context.prompt),
                max_tokens=context.max_tokens,
                temperature=context.temperature,
                user_id=context.user_id,
                session_id=context.session_id,
                request_type=context.request_type,
                processing_time_ms=processing_time,
                tokens_used=response.get("tokens_used") if response else 0,
                cost_estimate=self._estimate_cost(
                    provider, response.get("tokens_used", 0) if response else 0
                ),
                cached=cached,
                success=success,
                error_message=error_message,
            )

            db.session.add(request_record)

            if response and success:
                response_record = LLMResponse(
                    request_id=context.request_id,
                    response_text=response.get("text", ""),
                    response_metadata={
                        "finish_reason": response.get("finish_reason"),
                        "model": response.get("model"),
                    },
                    confidence_score=0.8,  # Could be calculated based on response
                    provenance_data={
                        "provider": provider.value if provider else None,
                        "model": response.get("model"),
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                )
                db.session.add(response_record)

            db.session.commit()

        except Exception as e:
            logger.error(f"Failed to record LLM request: {e}")
            db.session.rollback()

    def _estimate_cost(self, provider: Optional[LLMProvider], tokens: int) -> float:
        """Estimate cost of LLM request."""
        if not provider or tokens == 0:
            return 0.0

        # Simplified cost estimation (per 1K tokens)
        costs = {
            LLMProvider.ANTHROPIC: 0.015,  # $15 per 1M tokens
            LLMProvider.OPENAI: 0.03,  # $30 per 1M tokens
            LLMProvider.GEMINI: 0.001,  # $1 per 1M tokens
            LLMProvider.NVIDIA: 0.0,  # Free models
            LLMProvider.OPENROUTER: 0.0,  # Free models
            LLMProvider.AZURE: 0.03,  # Similar to OpenAI
            LLMProvider.HUGGINGFACE: 0.0,  # Free
        }

        cost_per_1k = costs.get(provider, 0.0)
        return (tokens / 1000) * cost_per_1k

    async def get_metrics(
        self, provider: Optional[LLMProvider] = None, hours: int = 24
    ) -> Dict[str, Any]:
        """Get LLM usage metrics."""
        try:
            # Query metrics from database
            since = datetime.utcnow() - timedelta(hours=hours)

            query = db.session.query(LLMMetrics).filter(LLMMetrics.timestamp >= since)

            if provider:
                query = query.filter(LLMMetrics.provider == provider.value)

            metrics = query.all()

            # Aggregate metrics
            aggregated = {}
            for metric in metrics:
                key = f"{metric.provider}_{metric.metric_type}"
                if key not in aggregated:
                    aggregated[key] = []
                aggregated[key].append(metric.value)

            # Calculate statistics
            stats = {}
            for key, values in aggregated.items():
                stats[key] = {
                    "count": len(values),
                    "avg": sum(values) / len(values) if values else 0,
                    "min": min(values) if values else 0,
                    "max": max(values) if values else 0,
                }

            return {
                "period_hours": hours,
                "total_requests": len(metrics),
                "statistics": stats,
            }

        except Exception as e:
            logger.error(f"Failed to get metrics: {e}")
            return {"error": str(e)}
