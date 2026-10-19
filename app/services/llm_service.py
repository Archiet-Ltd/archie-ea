"""
DEPRECATED: Import from app.modules.ai_chat.services instead.
-> app.modules.ai_chat.services.llm_service

Backward-compat re-export. Canonical: app/modules/ai_chat/services/llm_service_impl.py
"""

from app.modules.ai_chat.services.llm_service_impl import (  # noqa: F401
    LLMService,
)
from app.modules.ai_chat.services.llm_router import get_llm_service  # noqa: F401

try:
    from app.modules.ai_chat.services.llm_service_impl import test_api_key  # noqa: F401
except ImportError:
    pass
