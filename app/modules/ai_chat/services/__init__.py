"""
AI Chat services — consolidated from 28 legacy files (~700KB) into 4 modules.

Modules:
- chat_service:         Chat approval, link, memory, multi-model, entity matching, multi-domain
- llm_service:          LLM routing, caching, cost tracking, health, unified LLM
- ai_analysis_service:  Gap detection, impact analysis, recommendations, semantic discovery
- ai_assistant_service: Unified assistant, suggestions, context helper, workspace AI

Usage:
    from app.modules.ai_chat.services.chat_service import MultiDomainChatService
    from app.modules.ai_chat.services.llm_service import LLMService, LLMModelRouter
    from app.modules.ai_chat.services.ai_assistant_service import UnifiedAIAssistant
"""
