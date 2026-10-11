"""
DEPRECATED: Import from app.modules.ai_chat.services instead.
-> app.modules.ai_chat.services.llm_service

Backward-compat re-export. Canonical: app/modules/ai_chat/services/llm_cache.py
"""

from app.modules.ai_chat.services.llm_cache import (  # noqa: F401
    LLMCache,
    get_cache,
)

get_llm_cache = get_cache
