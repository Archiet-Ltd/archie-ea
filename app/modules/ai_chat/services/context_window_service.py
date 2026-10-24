"""
Context window management for AI Chat.

Provides token counting (char-based estimation) and history trimming
to keep LLM requests within each provider's context window limit.
No external tokenizer dependency -- uses len(text) // 4 approximation.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ContextWindowService:
    """Manages token budgets and trims chat history to fit provider context windows."""

    # Conservative token limits per provider, leaving headroom for the response.
    PROVIDER_LIMITS: Dict[str, int] = {
        "openai": 120000,       # GPT-4o: 128k context
        "anthropic": 180000,    # Claude: 200k context
        "huggingface": 4000,    # Flan-T5: small context
        "gemini": 900000,       # Gemini: 1M context
        "nvidia": 200000,       # NVIDIA Nemotron: 256k context
    }

    DEFAULT_LIMIT: int = 8000
    WARNING_THRESHOLD: float = 0.85  # warn when usage exceeds 85%

    # ------------------------------------------------------------------ #
    # Token counting
    # ------------------------------------------------------------------ #

    @staticmethod
    def count_tokens(text: str) -> int:
        """Estimate token count using the 4-chars-per-token heuristic.

        This avoids adding tiktoken to requirements.txt while giving a
        reasonable approximation for budget enforcement.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    def count_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count total estimated tokens across a list of chat messages.

        Each message is expected to have at least a ``content`` key (str)
        and a ``role`` key.  Per-message overhead (role tag, separators)
        is estimated at 4 tokens.
        """
        total = 0
        for msg in messages:
            content = msg.get("content") or msg.get("message_text") or ""
            role = msg.get("role") or msg.get("message_role") or ""
            total += self.count_tokens(content) + self.count_tokens(role) + 4
        return total

    # ------------------------------------------------------------------ #
    # Provider limits
    # ------------------------------------------------------------------ #

    def get_limit_for_provider(self, provider: str) -> int:
        """Return the context-window token budget for *provider*."""
        return self.PROVIDER_LIMITS.get(
            (provider or "").lower().strip(),
            self.DEFAULT_LIMIT,
        )

    # ------------------------------------------------------------------ #
    # History trimming
    # ------------------------------------------------------------------ #

    def trim_history(
        self,
        messages: List[Dict[str, Any]],
        provider: str,
        reserved_for_response: int = 4000,
    ) -> List[Dict[str, Any]]:
        """Trim *messages* so the total token count fits within the provider limit.

        Strategy:
        1. Always keep the first message if it has role ``system``.
        2. Always keep the most recent 2 messages (the latest user turn + any
           assistant reply).
        3. Remove the oldest non-system messages until the budget is met.
        4. If any messages were removed, insert a brief summary note so the
           LLM knows context was truncated.

        Returns a new list -- the original is not mutated.
        """
        if not messages:
            return []

        limit = self.get_limit_for_provider(provider) - reserved_for_response
        if limit <= 0:
            limit = self.get_limit_for_provider(provider)

        current_tokens = self.count_message_tokens(messages)
        if current_tokens <= limit:
            return list(messages)

        # Separate system prompt (first message) and tail (last 2).
        has_system = (
            len(messages) > 0
            and (messages[0].get("role") or messages[0].get("message_role") or "") == "system"
        )
        system_msgs = [messages[0]] if has_system else []
        body_start = 1 if has_system else 0

        # Keep at least the last 2 messages (user question + possible assistant answer).
        keep_tail = min(2, len(messages) - body_start)
        tail_msgs = messages[-keep_tail:] if keep_tail > 0 else []
        middle_msgs = list(messages[body_start: len(messages) - keep_tail]) if keep_tail > 0 else list(messages[body_start:])

        # Budget after system + tail
        fixed_tokens = self.count_message_tokens(system_msgs) + self.count_message_tokens(tail_msgs)
        remaining_budget = limit - fixed_tokens

        # Keep as many middle messages as possible, starting from the most recent.
        kept_middle: List[Dict[str, Any]] = []
        middle_tokens = 0
        for msg in reversed(middle_msgs):
            msg_tokens = self.count_message_tokens([msg])
            if middle_tokens + msg_tokens <= remaining_budget:
                kept_middle.insert(0, msg)
                middle_tokens += msg_tokens
            else:
                break

        removed_count = len(middle_msgs) - len(kept_middle)

        result = list(system_msgs)

        if removed_count > 0:
            summary_note = {
                "role": "system",
                "content": (
                    f"[Context trimmed: {removed_count} earlier message(s) removed "
                    f"to fit within the {provider} context window.]"
                ),
            }
            result.append(summary_note)
            logger.info(
                "Trimmed %d messages for provider %s (was %d tokens, limit %d)",
                removed_count,
                provider,
                current_tokens,
                limit,
            )

        result.extend(kept_middle)
        result.extend(tail_msgs)
        return result

    # ------------------------------------------------------------------ #
    # Usage info (for API / UI display)
    # ------------------------------------------------------------------ #

    def get_usage_info(self, messages: List[Dict[str, Any]], provider: str) -> Dict[str, Any]:
        """Return token-usage metadata suitable for returning to the UI.

        Keys:
        - ``total_tokens``: estimated token count of *messages*.
        - ``limit``: provider context-window budget.
        - ``percentage``: usage as a float 0-100.
        - ``warning``: human-readable warning if usage is high, else ``None``.
        """
        total = self.count_message_tokens(messages)
        limit = self.get_limit_for_provider(provider)
        percentage = round((total / limit) * 100, 1) if limit > 0 else 0.0

        warning = None
        if percentage >= 95:
            warning = (
                f"Context window nearly full ({percentage}% used). "
                "Older messages will be trimmed automatically."
            )
        elif percentage >= self.WARNING_THRESHOLD * 100:
            warning = (
                f"Context window usage is high ({percentage}% of {limit:,} tokens). "
                "Consider clearing history to free space."
            )

        return {
            "total_tokens": total,
            "limit": limit,
            "percentage": percentage,
            "warning": warning,
        }
