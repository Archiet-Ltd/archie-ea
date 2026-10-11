"""
-> app.modules.ai_chat.services

AI Chat Memory Service with pgvector

Provides semantic memory and context retrieval for multi-domain chat.
Uses pgvector to store and retrieve relevant chat history based on semantic similarity.

Features:
- Semantic chat history search
- Context window management
- Session-based conversation tracking
- Automatic context injection for LLM prompts
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from app import db
from app.models.vector_embeddings import ChatMessageEmbedding
from app.services.pgvector_embedding_service import (
    get_pgvector_service,
    scoped_chat_message_query,
)

logger = logging.getLogger(__name__)


class AIChatMemoryService:
    """
    Service for managing AI chat conversation memory using pgvector.
    Enables semantic search over chat history for context-aware responses.
    """

    def __init__(self, user_id: Optional[int] = None, session_id: Optional[str] = None):
        """
        Initialize chat memory service.

        Args:
            user_id: ID of the user (optional)
            session_id: Unique session identifier (optional)
        """
        self.user_id = user_id
        self.session_id = session_id or f"session_{datetime.utcnow().timestamp()}"
        self.pgvector_service = get_pgvector_service()
        self.max_context_tokens = 3000  # Approximate token limit for context
        self.context_lookback_messages = 10  # Recent messages to consider

    def add_message(
        self,
        message_text: str,
        role: str = "user",
        domain: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ChatMessageEmbedding]:
        """
        Add a message to chat history with semantic embedding.

        Args:
            message_text: The message text
            role: 'user' or 'assistant'
            domain: Chat domain (vendor, capability, architecture, etc)
            metadata: Optional metadata (persona, intent, etc)

        Returns:
            ChatMessageEmbedding object or None if failed
        """
        try:
            embedding = self.pgvector_service.create_chat_message_embedding(
                chat_session_id=self.session_id,
                message_text=message_text,
                user_id=self.user_id,
                role=role,
                domain=domain,
                metadata=metadata or {},
            )
            logger.debug(f"Added chat message to session {self.session_id}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to add chat message: {e}")
            return None

    def get_relevant_context(
        self, query_text: str, limit: int = 5, threshold: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve semantically relevant messages from chat history.
        Used to inject context into LLM prompts.

        Args:
            query_text: Current user query or topic
            limit: Maximum messages to retrieve
            threshold: Minimum similarity score

        Returns:
            List of relevant message contexts
        """
        try:
            relevant_messages = self.pgvector_service.search_chat_history(
                query_text=query_text,
                chat_session_id=self.session_id,
                limit=limit,
                threshold=threshold,
            )
            return relevant_messages
        except Exception as e:
            logger.error(f"Failed to retrieve context: {e}")
            return []

    def get_recent_messages(self, limit: int = 10) -> List[ChatMessageEmbedding]:
        """
        Get recent messages from chat session (temporal ordering).

        Args:
            limit: Maximum messages to retrieve

        Returns:
            List of recent ChatMessageEmbedding objects
        """
        try:
            messages = (
                scoped_chat_message_query(self.session_id)
                .order_by(ChatMessageEmbedding.created_at.desc())
                .limit(limit)
                .all()
            )
            return list(reversed(messages))  # Return in chronological order
        except Exception as e:
            logger.error(f"Failed to retrieve recent messages: {e}")
            return []

    def get_session_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics for current chat session.

        Returns:
            Dictionary with session metadata
        """
        try:
            base_query = scoped_chat_message_query(self.session_id)

            total_messages = base_query.count()

            user_messages = base_query.filter(
                ChatMessageEmbedding.message_role == "user"
            ).count()

            assistant_messages = base_query.filter(
                ChatMessageEmbedding.message_role == "assistant"
            ).count()

            domains = (
                base_query.with_entities(ChatMessageEmbedding.domain, func.count())
                .group_by(ChatMessageEmbedding.domain)
                .all()
            )

            return {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "total_messages": total_messages,
                "user_messages": user_messages,
                "assistant_messages": assistant_messages,
                "domains": dict(domains) if domains else {},
                "created_at": None,  # Could track if needed
            }
        except Exception as e:
            logger.error(f"Failed to get session summary: {e}")
            return {"error": str(e)}

    def build_context_prompt(self, query_text: str, include_recent: bool = True) -> str:
        """
        Build a context-enhanced prompt for the LLM.
        Combines relevant semantic context with recent messages.

        Args:
            query_text: The current user query
            include_recent: Whether to include recent messages

        Returns:
            Formatted context string for injection into LLM prompt
        """
        try:
            context_parts = []

            # Add semantically relevant messages
            relevant = self.get_relevant_context(query_text, limit=3, threshold=0.3)
            if relevant:
                context_parts.append("## Relevant Previous Context:")
                for msg in relevant:
                    role_label = "User" if msg.get("role") == "user" else "Assistant"
                    context_parts.append(f"{role_label}: {msg.get('message', '')[:200]}...")

            # Add recent messages for immediate context
            if include_recent:
                recent = self.get_recent_messages(limit=3)
                if recent:
                    context_parts.append("\n## Recent Messages:")
                    for msg in recent:
                        role_label = "User" if msg.message_role == "user" else "Assistant"
                        context_parts.append(f"{role_label}: {msg.message_text[:200]}")

            return "\n".join(context_parts) if context_parts else ""
        except Exception as e:
            logger.error(f"Failed to build context prompt: {e}")
            return ""

    def clear_session(self) -> bool:
        """
        Clear all messages for this session.

        Returns:
            True if successful, False otherwise
        """
        try:
            scoped_chat_message_query(self.session_id).delete()
            db.session.commit()
            logger.info(f"Cleared session {self.session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear session: {e}")
            db.session.rollback()
            return False

    def export_session(self) -> Dict[str, Any]:
        """
        Export complete session as JSON.

        Returns:
            Dictionary with session data
        """
        try:
            messages = self.get_recent_messages(limit=1000)
            return {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "exported_at": datetime.utcnow().isoformat(),
                "message_count": len(messages),
                "messages": [
                    {
                        "role": m.message_role,
                        "text": m.message_text,
                        "domain": m.domain,
                        "timestamp": m.created_at.isoformat(),
                        "metadata": m.metadata_json,
                    }
                    for m in messages
                ],
            }
        except Exception as e:
            logger.error(f"Failed to export session: {e}")
            return {"error": str(e)}

    # ENT-050: Cross-session pgvector semantic search ─────────────────────────
    def search_similar_messages(
        self, query_text: str, limit: int = 5, threshold: float = 0.25
    ) -> List[Dict[str, Any]]:
        """Return semantically similar messages for this user across ALL sessions.

        Uses pgvector cosine distance on ChatMessageEmbedding.embedding.
        Degrades gracefully to an empty list when pgvector is unavailable.

        Args:
            query_text: Natural-language query to compare against stored messages.
            limit: Maximum number of results to return.
            threshold: Minimum similarity score (0–1) to include a result.

        Returns:
            List of dicts with keys: session_id, content, role, similarity, timestamp.
        """
        if not self.user_id:
            return []
        try:
            # pgvector_service.search_chat_history supports cross-session when no
            # session filter is applied; we scope to user via a post-filter on the
            # ChatMessageEmbedding rows that share this user_id.
            all_results: List[Dict[str, Any]] = self.pgvector_service.search_chat_history(
                query_text=query_text,
                chat_session_id=None,  # search all sessions
                limit=limit * 4,  # over-fetch so user filter still hits `limit`
                threshold=threshold,
            )
            user_results = [
                r for r in all_results if r.get("user_id") == self.user_id
            ][:limit]
            return user_results
        except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.warning("pgvector search unavailable, returning empty: %s", e)
            return []


# Singleton instance per user/session
_chat_memory_instances: Dict[str, AIChatMemoryService] = {}


def get_chat_memory_service(
    user_id: Optional[int] = None, session_id: Optional[str] = None
) -> AIChatMemoryService:
    """
    Get or create chat memory service instance.

    Args:
        user_id: User ID
        session_id: Session ID

    Returns:
        AIChatMemoryService instance
    """
    key = f"{user_id}:{session_id}"
    if key not in _chat_memory_instances:
        _chat_memory_instances[key] = AIChatMemoryService(user_id, session_id)
    return _chat_memory_instances[key]
