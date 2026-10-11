"""
-> app.modules.ai_chat.services

Multi-Model AI Chat Service
Supports GPT - 4, Claude - 3, and Llama - 3 with unified interface
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Literal, Optional

from app.modules.ai_chat.services.llm_service_impl import LLMService

# Model provider clients
try:
    import openai

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    HAS_LLAMA = True
except ImportError:
    HAS_LLAMA = False

logger = logging.getLogger(__name__)

ModelType = Literal[
    "gpt - 4",
    "gpt - 4 - turbo",
    "claude - 3 - opus",
    "claude - 3 - sonnet",
    "llama - 3 - 70b",
    "llama - 3 - 8b",
]


@dataclass
class ChatMessage:
    """Single chat message with role and content."""

    role: str  # 'system', 'user', 'assistant'
    content: str
    timestamp: Optional[datetime] = None
    model: Optional[str] = None

    def to_dict(self):
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "model": self.model,
        }


@dataclass
class ModelConfig:
    """Configuration for a specific model."""

    name: str
    provider: str  # 'openai', 'anthropic', 'local'
    max_tokens: int
    temperature: float
    top_p: float
    available: bool
    cost_per_1k_tokens: float


class MultiModelChatService:
    """
    Unified interface for multiple AI models.
    Handles GPT - 4, Claude - 3, and Llama - 3 with consistent API.
    """

    def __init__(self, default_model: ModelType = "gpt - 4 - turbo"):
        self.default_model = default_model
        self.openai_client = None
        self.anthropic_client = None
        self.llama_model = None
        self.llama_tokenizer = None

        # Initialize available clients
        self._init_clients()

        # Model configurations
        self.models = self._get_model_configs()

    def _init_clients(self):
        """Initialize API clients based on available keys."""
        # OpenAI
        if HAS_OPENAI and os.getenv("OPENAI_API_KEY"):
            try:
                self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                logger.info("✅ OpenAI client initialized (GPT - 4 available)")
            except Exception as e:
                logger.warning(f"OpenAI initialization failed: {e}")

        # Anthropic (Claude)
        if HAS_ANTHROPIC and os.getenv("ANTHROPIC_API_KEY"):
            try:
                self.anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
                logger.info("✅ Anthropic client initialized (Claude - 3 available)")
            except Exception as e:
                logger.warning(f"Anthropic initialization failed: {e}")

        # Llama - 3 (local or API)
        if HAS_LLAMA:
            try:
                # Check if model is locally available
                # This would download the model on first run
                # In production, pre-download models to persistent storage
                logger.info("Llama - 3 tokenizer/model available (but not loaded to save memory)")
            except Exception as e:
                logger.warning(f"Llama - 3 initialization failed: {e}")

    def _get_model_configs(self) -> Dict[str, ModelConfig]:
        """Get configuration for all supported models."""
        return {
            "gpt - 4": ModelConfig(
                name="GPT - 4",
                provider="openai",
                max_tokens=8192,
                temperature=0.7,
                top_p=0.9,
                available=self.openai_client is not None,
                cost_per_1k_tokens=0.03,
            ),
            "gpt - 4 - turbo": ModelConfig(
                name="GPT - 4 Turbo",
                provider="openai",
                max_tokens=128000,
                temperature=0.7,
                top_p=0.9,
                available=self.openai_client is not None,
                cost_per_1k_tokens=0.01,
            ),
            "claude - 3 - opus": ModelConfig(
                name="Claude 3 Opus",
                provider="anthropic",
                max_tokens=4096,
                temperature=0.7,
                top_p=0.9,
                available=self.anthropic_client is not None,
                cost_per_1k_tokens=0.015,
            ),
            "claude - 3 - sonnet": ModelConfig(
                name="Claude 3 Sonnet",
                provider="anthropic",
                max_tokens=4096,
                temperature=0.7,
                top_p=0.9,
                available=self.anthropic_client is not None,
                cost_per_1k_tokens=0.003,
            ),
            "llama - 3 - 70b": ModelConfig(
                name="Llama 3 70B",
                provider="local",
                max_tokens=8192,
                temperature=0.7,
                top_p=0.9,
                available=HAS_LLAMA,
                cost_per_1k_tokens=0.0,  # Free if self-hosted
            ),
            "llama - 3 - 8b": ModelConfig(
                name="Llama 3 8B",
                provider="local",
                max_tokens=8192,
                temperature=0.7,
                top_p=0.9,
                available=HAS_LLAMA,
                cost_per_1k_tokens=0.0,  # Free if self-hosted
            ),
        }

    def get_available_models(self) -> List[Dict[str, any]]:
        """Get list of available models with their configurations."""
        return [
            {
                "id": model_id,
                "name": config.name,
                "provider": config.provider,
                "available": config.available,
                "max_tokens": config.max_tokens,
                "cost_per_1k_tokens": config.cost_per_1k_tokens,
            }
            for model_id, config in self.models.items()
        ]

    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[ModelType] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> ChatMessage:
        """
        Send chat messages and get response from specified model.

        Args:
            messages: List of chat messages (conversation history)
            model: Model to use (defaults to self.default_model)
            temperature: Sampling temperature (overrides model default)
            max_tokens: Max response tokens (overrides model default)
            stream: Whether to stream response (not implemented yet)

        Returns:
            ChatMessage with assistant response
        """
        model = model or self.default_model
        config = self.models.get(model)

        if not config:
            raise ValueError(f"Unknown model: {model}")

        if not config.available:
            raise RuntimeError(f"Model {model} is not available. Check API keys/dependencies.")

        # Use model-specific implementation
        if config.provider == "openai":
            return self._chat_openai(messages, model, temperature, max_tokens)
        elif config.provider == "anthropic":
            return self._chat_anthropic(messages, model, temperature, max_tokens)
        elif config.provider == "local":
            return self._chat_llama(messages, model, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {config.provider}")

    def _chat_openai(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> ChatMessage:
        """Chat using OpenAI API (GPT - 4)."""
        config = self.models[model]
        LLMService._guard_provider_call("openai", model, prompt="multi-model chat")

        # Convert ChatMessage objects to OpenAI format
        openai_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        response = self.openai_client.chat.completions.create(
            model=model,
            messages=openai_messages,
            temperature=temperature or config.temperature,
            max_tokens=max_tokens or config.max_tokens,
            top_p=config.top_p,
        )

        return ChatMessage(
            role="assistant",
            content=response.choices[0].message.content,
            timestamp=datetime.utcnow(),
            model=model,
        )

    def _chat_anthropic(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> ChatMessage:
        """Chat using Anthropic API (Claude - 3)."""
        config = self.models[model]
        LLMService._guard_provider_call("anthropic", model, prompt="multi-model chat")

        # Extract system message if present
        system_msg = None
        user_messages = []

        for msg in messages:
            if msg.role == "system":
                system_msg = msg.content
            else:
                user_messages.append({"role": msg.role, "content": msg.content})

        # Claude API uses different format
        kwargs = {
            "model": model,
            "messages": user_messages,
            "temperature": temperature or config.temperature,
            "max_tokens": max_tokens or config.max_tokens,
            "top_p": config.top_p,
        }

        if system_msg:
            kwargs["system"] = system_msg

        response = self.anthropic_client.messages.create(**kwargs)

        return ChatMessage(
            role="assistant",
            content=response.content[0].text,
            timestamp=datetime.utcnow(),
            model=model,
        )

    def _chat_llama(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> ChatMessage:
        """
        Chat using local Llama - 3 model.
        Note: This requires significant GPU memory (8B model ~16GB, 70B model ~140GB).
        """
        config = self.models[model]

        # Lazy load model (only when first used)
        if self.llama_model is None:
            model_name = (
                "meta-llama/Meta-Llama - 3 - 8B-Instruct"
                if "8b" in model
                else "meta-llama/Meta-Llama - 3 - 70B-Instruct"
            )
            logger.info(f"Loading {model_name}...")

            self.llama_tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.llama_model = AutoModelForCausalLM.from_pretrained(
                model_name,
                torch_dtype=torch.float16,
                device_map="auto",  # Automatically use GPU if available
            )

        # Format conversation for Llama
        conversation = []
        for msg in messages:
            conversation.append({"role": msg.role, "content": msg.content})

        # Tokenize and generate
        inputs = self.llama_tokenizer.apply_chat_template(conversation, return_tensors="pt").to(
            self.llama_model.device
        )

        outputs = self.llama_model.generate(
            inputs,
            max_new_tokens=max_tokens or config.max_tokens,
            temperature=temperature or config.temperature,
            top_p=config.top_p,
            do_sample=True,
        )

        response_text = self.llama_tokenizer.decode(
            outputs[0][inputs.shape[1] :], skip_special_tokens=True
        )

        return ChatMessage(
            role="assistant", content=response_text, timestamp=datetime.utcnow(), model=model
        )

    def estimate_cost(self, messages: List[ChatMessage], model: ModelType) -> float:
        """
        Estimate cost for a chat request in USD.

        Returns:
            Estimated cost in dollars
        """
        config = self.models.get(model)
        if not config:
            return 0.0

        # Rough token estimation (1 token ≈ 4 characters)
        total_chars = sum(len(msg.content) for msg in messages)
        estimated_tokens = total_chars / 4

        # Add estimated response tokens (assume 500 tokens average)
        estimated_tokens += 500

        cost = (estimated_tokens / 1000) * config.cost_per_1k_tokens
        return round(cost, 4)
