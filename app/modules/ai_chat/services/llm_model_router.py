"""
-> app.modules.ai_chat.services.llm_service

LLM Model Router - Intelligent Model Selection

Routes LLM requests to optimal models based on task complexity, cost, and speed requirements.
Addresses Gap #6: No Multi-Model Support
"""

import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    """Task complexity levels for model selection."""

    SIMPLE = "simple"  # Simple text generation, classification
    MODERATE = "moderate"  # Standard architecture generation, analysis
    COMPLEX = "complex"  # Complex reasoning, multi-step tasks
    EXPERT = "expert"  # Requires highest capability model


class TaskPriority(Enum):
    """Task priority for speed vs quality trade-offs."""

    REALTIME = "realtime"  # <2s response time needed
    INTERACTIVE = "interactive"  # <5s response time
    BACKGROUND = "background"  # >10s acceptable
    BATCH = "batch"  # Minutes acceptable


class LLMModelRouter:
    """
    Intelligently routes LLM requests to optimal models.

    Features:
    - Cost optimization (use cheaper models for simple tasks)
    - Speed optimization (use faster models for real-time UI)
    - Quality optimization (use best models for critical tasks)
    - Multi-provider support (OpenAI, Anthropic, Azure)
    """

    # Model capabilities and characteristics
    MODELS = {
        "openai": {
            "gpt - 4 - turbo": {
                "capability": TaskComplexity.EXPERT,
                "speed": "medium",  # ~30 - 60 tokens/sec
                "cost": "high",
                "context_window": 128000,
                "strengths": ["reasoning", "architecture", "code"],
                "use_cases": ["complex_architecture", "strategic_analysis", "expert_reasoning"],
            },
            "gpt - 4": {
                "capability": TaskComplexity.EXPERT,
                "speed": "slow",  # ~15 - 30 tokens/sec
                "cost": "very_high",
                "context_window": 8192,
                "strengths": ["reasoning", "analysis"],
                "use_cases": ["critical_decisions", "deep_analysis"],
            },
            "gpt - 3.5 - turbo": {
                "capability": TaskComplexity.SIMPLE,
                "speed": "fast",  # ~60 - 100 tokens/sec
                "cost": "low",
                "context_window": 16385,
                "strengths": ["speed", "cost", "simple_tasks"],
                "use_cases": ["classification", "simple_generation", "ui_suggestions"],
            },
        },
        "anthropic": {
            "claude - 3 - opus - 20240229": {
                "capability": TaskComplexity.EXPERT,
                "speed": "slow",
                "cost": "very_high",
                "context_window": 200000,
                "strengths": ["reasoning", "long_context", "accuracy"],
                "use_cases": ["complex_architecture", "document_analysis", "strategic_planning"],
            },
            "claude - 3 - 5-sonnet - 20241022": {
                "capability": TaskComplexity.COMPLEX,
                "speed": "medium",
                "cost": "medium",
                "context_window": 200000,
                "strengths": ["balanced", "architecture", "code", "long_context"],
                "use_cases": ["standard_architecture", "gap_analysis", "requirement_generation"],
            },
            "claude - 3 - haiku - 20240307": {
                "capability": TaskComplexity.MODERATE,
                "speed": "fast",
                "cost": "low",
                "context_window": 200000,
                "strengths": ["speed", "cost", "simple_architecture"],
                "use_cases": ["quick_analysis", "simple_generation", "validation"],
            },
        },
        "gemini": {
            "gemini - 1.5 - pro-latest": {
                "capability": TaskComplexity.COMPLEX,
                "speed": "medium",
                "cost": "medium",
                "context_window": 1000000,
                "strengths": ["multi_modal", "long_context", "architecture_reasoning"],
                "use_cases": ["standard_architecture", "document_analysis", "uml_generation"],
            },
            "gemini - 1.5 - flash-latest": {
                "capability": TaskComplexity.MODERATE,
                "speed": "fast",
                "cost": "low",
                "context_window": 1000000,
                "strengths": ["speed", "multi_modal", "summarization"],
                "use_cases": ["quick_analysis", "classification", "validation"],
            },
        },
    }

    # Task type to recommended models mapping
    TASK_ROUTING = {
        "archimate_generation": {
            TaskComplexity.COMPLEX: [
                ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
                ("openai", "gpt - 4 - turbo"),
                ("gemini", "gemini - 1.5 - pro-latest"),
            ],
            TaskComplexity.SIMPLE: [
                ("anthropic", "claude - 3 - haiku - 20240307"),
                ("openai", "gpt - 3.5 - turbo"),
                ("gemini", "gemini - 1.5 - flash-latest"),
            ],
        },
        "capability_discovery": {
            TaskComplexity.COMPLEX: [
                ("anthropic", "claude - 3 - opus - 20240229"),
                ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
                ("gemini", "gemini - 1.5 - pro-latest"),
            ]
        },
        "gap_analysis": {
            TaskComplexity.MODERATE: [
                ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
                ("openai", "gpt - 4 - turbo"),
                ("gemini", "gemini - 1.5 - pro-latest"),
            ]
        },
        "requirement_generation": {
            TaskComplexity.MODERATE: [
                ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
                ("openai", "gpt - 3.5 - turbo"),
                ("gemini", "gemini - 1.5 - flash-latest"),
            ]
        },
        "code_generation": {
            TaskComplexity.COMPLEX: [
                ("openai", "gpt - 4 - turbo"),
                ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
                ("gemini", "gemini - 1.5 - pro-latest"),
            ]
        },
        "classification": {
            TaskComplexity.SIMPLE: [
                ("openai", "gpt - 3.5 - turbo"),
                ("anthropic", "claude - 3 - haiku - 20240307"),
                ("gemini", "gemini - 1.5 - flash-latest"),
            ]
        },
        "validation": {
            TaskComplexity.SIMPLE: [
                ("anthropic", "claude - 3 - haiku - 20240307"),
                ("openai", "gpt - 3.5 - turbo"),
                ("gemini", "gemini - 1.5 - flash-latest"),
            ]
        },
        "document_analysis": {
            TaskComplexity.COMPLEX: [
                ("anthropic", "claude - 3 - opus - 20240229"),
                ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
                ("gemini", "gemini - 1.5 - pro-latest"),
            ]
        },
    }

    def __init__(self):
        """Initialize the model router."""
        self.fallback_chain = [
            ("anthropic", "claude - 3 - 5-sonnet - 20241022"),
            ("openai", "gpt - 4 - turbo"),
            ("gemini", "gemini - 1.5 - pro-latest"),
            ("openai", "gpt - 3.5 - turbo"),
        ]

    def select_model(
        self,
        task_type: str,
        complexity: TaskComplexity = TaskComplexity.MODERATE,
        priority: TaskPriority = TaskPriority.BACKGROUND,
        context_size: int = 0,
        optimize_for: str = "balanced",
    ) -> Tuple[str, str]:
        """
        Select optimal model for a task.

        Args:
            task_type: Type of task (archimate_generation, gap_analysis, etc.)
            complexity: Task complexity level
            priority: Speed priority
            context_size: Approximate token count for context
            optimize_for: 'cost', 'speed', 'quality', or 'balanced'

        Returns:
            Tuple of (provider, model_name)
        """
        # Get candidate models for task type
        candidates = self._get_candidates(task_type, complexity)

        if not candidates:
            logger.warning(f"No specific routing for task {task_type}, using fallback")
            candidates = self.fallback_chain

        # Filter by context window size
        candidates = self._filter_by_context(candidates, context_size)

        # Filter by availability
        candidates = self._filter_by_availability(candidates)

        if not candidates:
            raise ValueError(
                "No available LLM models configured. "
                "Please configure at least one provider in Admin > AI settings"
            )

        # Select best candidate based on optimization criteria
        selected = self._select_optimal(candidates, priority, optimize_for)

        provider, model = selected
        logger.info(
            f"Selected {provider}/{model} for {task_type} "
            f"(complexity={complexity.value}, optimize_for={optimize_for})"
        )

        return provider, model

    def _get_candidates(self, task_type: str, complexity: TaskComplexity) -> List[Tuple[str, str]]:
        """Get candidate models for task type and complexity."""
        if task_type in self.TASK_ROUTING:
            task_config = self.TASK_ROUTING[task_type]

            # Try exact complexity match first
            if complexity in task_config:
                return task_config[complexity]

            # Fall back to any complexity for this task
            for candidates in task_config.values():
                return candidates

        return []

    def _filter_by_context(
        self, candidates: List[Tuple[str, str]], context_size: int
    ) -> List[Tuple[str, str]]:
        """Filter candidates by context window size."""
        if context_size == 0:
            return candidates

        filtered = []
        for provider, model in candidates:
            if provider in self.MODELS and model in self.MODELS[provider]:
                model_info = self.MODELS[provider][model]
                if model_info["context_window"] >= context_size:
                    filtered.append((provider, model))

        return filtered if filtered else candidates

    def _filter_by_availability(self, candidates: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Filter candidates by configured and enabled providers."""
        from app.models.models import APISettings

        available = []
        for provider, model in candidates:
            settings = APISettings.query.filter_by(provider=provider, enabled=True).first()

            if settings and settings.has_key():
                available.append((provider, model))

        return available

    def _select_optimal(
        self, candidates: List[Tuple[str, str]], priority: TaskPriority, optimize_for: str
    ) -> Tuple[str, str]:
        """Select best candidate based on optimization criteria."""
        if not candidates:
            raise ValueError("No available candidates")

        # Priority-based selection
        if priority == TaskPriority.REALTIME:
            # Need fastest model
            return self._select_by_speed(candidates, prefer="fast")

        if priority == TaskPriority.INTERACTIVE:
            # Balance speed and quality
            if optimize_for == "cost":
                return self._select_by_cost(candidates, prefer="low")
            return self._select_by_speed(candidates, prefer="medium")

        # Optimization-based selection
        if optimize_for == "cost":
            return self._select_by_cost(candidates, prefer="low")
        elif optimize_for == "speed":
            return self._select_by_speed(candidates, prefer="fast")
        elif optimize_for == "quality":
            return self._select_by_capability(candidates, prefer="expert")

        # Balanced: prefer medium cost, good quality
        return candidates[0]  # First candidate is typically well-balanced

    def _select_by_speed(
        self, candidates: List[Tuple[str, str]], prefer: str = "fast"
    ) -> Tuple[str, str]:
        """Select model optimized for speed."""
        speed_ranking = {"fast": 3, "medium": 2, "slow": 1}

        best = None
        best_score = 0

        for provider, model in candidates:
            if provider in self.MODELS and model in self.MODELS[provider]:
                speed = self.MODELS[provider][model]["speed"]
                score = speed_ranking.get(speed, 0)

                if score > best_score:
                    best_score = score
                    best = (provider, model)

        return best if best else candidates[0]

    def _select_by_cost(
        self, candidates: List[Tuple[str, str]], prefer: str = "low"
    ) -> Tuple[str, str]:
        """Select model optimized for cost."""
        cost_ranking = {"low": 3, "medium": 2, "high": 1, "very_high": 0}

        best = None
        best_score = 0

        for provider, model in candidates:
            if provider in self.MODELS and model in self.MODELS[provider]:
                cost = self.MODELS[provider][model]["cost"]
                score = cost_ranking.get(cost, 0)

                if score > best_score:
                    best_score = score
                    best = (provider, model)

        return best if best else candidates[0]

    def _select_by_capability(
        self, candidates: List[Tuple[str, str]], prefer: str = "expert"
    ) -> Tuple[str, str]:
        """Select model optimized for capability."""
        capability_ranking = {
            TaskComplexity.EXPERT: 4,
            TaskComplexity.COMPLEX: 3,
            TaskComplexity.MODERATE: 2,
            TaskComplexity.SIMPLE: 1,
        }

        best = None
        best_score = 0

        for provider, model in candidates:
            if provider in self.MODELS and model in self.MODELS[provider]:
                capability = self.MODELS[provider][model]["capability"]
                score = capability_ranking.get(capability, 0)

                if score > best_score:
                    best_score = score
                    best = (provider, model)

        return best if best else candidates[0]

    def get_model_info(self, provider: str, model: str) -> Optional[Dict]:
        """Get information about a specific model."""
        if provider in self.MODELS and model in self.MODELS[provider]:
            return self.MODELS[provider][model]
        return None

    def get_available_models(self) -> List[Dict]:
        """Get list of all configured and available models."""
        from app.models.models import APISettings

        available = []

        for provider, models in self.MODELS.items():
            settings = APISettings.query.filter_by(provider=provider, enabled=True).first()

            if settings and settings.has_key():
                for model_name, model_info in models.items():
                    available.append(
                        {
                            "provider": provider,
                            "model": model_name,
                            "capability": model_info["capability"].value,
                            "speed": model_info["speed"],
                            "cost": model_info["cost"],
                            "context_window": model_info["context_window"],
                            "strengths": model_info["strengths"],
                        }
                    )

        return available
