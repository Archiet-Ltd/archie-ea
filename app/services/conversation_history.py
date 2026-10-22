"""
Conversation History Service with Vector Search
Persistent chat threads with semantic search capabilities
"""
import uuid
from dataclasses import dataclass  # dead-code-ok
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text

from app import db
from app.extensions.cache import cached, invalidate_cache
from app.middleware.tenant_context import current_org_id

# Vector search imports
try:
    import chromadb
    from chromadb.config import Settings
    from sentence_transformers import SentenceTransformer

    HAS_VECTOR_SEARCH = True
except ImportError:
    HAS_VECTOR_SEARCH = False


@dataclass
class ConversationThread:
    """A conversation thread with messages."""

    id: str
    user_id: int
    title: str
    model: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "model": self.model,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": self.message_count,
        }


@dataclass
class ConversationMessage:
    """Single message in a conversation."""

    id: str
    thread_id: str
    role: str  # 'system', 'user', 'assistant'
    content: str
    model: Optional[str]
    tokens: Optional[int]
    created_at: datetime

    def to_dict(self):
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "tokens": self.tokens,
            "created_at": self.created_at.isoformat(),
        }


class ConversationHistoryService:
    """
    Service for managing conversation threads with vector search.
    Stores conversations in database and indexes for semantic search.
    """

    def __init__(self):
        self.chroma_client = None
        self.collection = None
        self.embedder = None

        if HAS_VECTOR_SEARCH:
            try:
                # Initialize ChromaDB for vector storage
                self.chroma_client = chromadb.Client(
                    Settings(
                        chroma_db_impl="duckdb+parquet",
                        persist_directory="./data/chroma_conversations",
                    )
                )

                # Get or create collection for conversations
                self.collection = self.chroma_client.get_or_create_collection(
                    name="conversation_messages",
                    metadata={"description": "Conversation messages with embeddings"},
                )

                # Initialize sentence transformer for embeddings
                self.embedder = SentenceTransformer("all-MiniLM-L6-v2")

            except Exception as e:
                print(f"Warning: Vector search initialization failed: {e}")
                self.chroma_client = None

    def create_thread(
        self,
        user_id: int,
        title: str,
        model: str,
        initial_message: Optional[str] = None,
        organization_id: Optional[int] = None,
    ) -> ConversationThread:
        """
        Create a new conversation thread.

        Args:
            user_id: User ID owning the thread
            title: Thread title
            model: AI model used in thread
            initial_message: Optional first message
            organization_id: Organisation the thread is made in (defaults to the active one)

        Returns:
            ConversationThread object
        """
        if organization_id is None:
            organization_id = current_org_id()
        thread_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Insert into database
        db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
            text(
                """
                INSERT INTO conversation_threads
                (id, user_id, title, model, created_at, updated_at, message_count, organization_id)
                VALUES (:id, :user_id, :title, :model, :created_at, :updated_at, 0, :organization_id)
            """
            ),
            {
                "id": thread_id,
                "user_id": user_id,
                "title": title,
                "model": model,
                "created_at": now,
                "updated_at": now,
                "organization_id": organization_id,
            },
        )
        db.session.commit()

        thread = ConversationThread(
            id=thread_id,
            user_id=user_id,
            title=title,
            model=model,
            created_at=now,
            updated_at=now,
            message_count=0,
        )

        # Add initial message if provided
        if initial_message:
            self.add_message(thread_id, "user", initial_message, model=None)

        # Invalidate cache
        invalidate_cache(f"threads:user:{user_id}:*")

        return thread

    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        tokens: Optional[int] = None,
    ) -> ConversationMessage:
        """
        Add a message to a conversation thread.

        Args:
            thread_id: Thread ID
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            model: Model that generated the message (for assistant messages)
            tokens: Token count (optional)

        Returns:
            ConversationMessage object
        """
        message_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Insert message
        db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
            text(
                """
                INSERT INTO conversation_messages
                (id, thread_id, role, content, model, tokens, created_at)
                VALUES (:id, :thread_id, :role, :content, :model, :tokens, :created_at)
            """
            ),
            {
                "id": message_id,
                "thread_id": thread_id,
                "role": role,
                "content": content,
                "model": model,
                "tokens": tokens,
                "created_at": now,
            },
        )

        # Update thread
        db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
            text(
                """
                UPDATE conversation_threads
                SET updated_at = :updated_at,
                    message_count = message_count + 1
                WHERE id = :thread_id
            """
            ),
            {"updated_at": now, "thread_id": thread_id},
        )

        db.session.commit()

        # Index in vector store for semantic search
        if self.collection and role in ["user", "assistant"]:
            try:
                embedding = self.embedder.encode([content])[0].tolist()
                self.collection.add(
                    ids=[message_id],
                    embeddings=[embedding],
                    documents=[content],
                    metadatas=[
                        {
                            "thread_id": thread_id,
                            "role": role,
                            "model": model or "",
                            "created_at": now.isoformat(),
                        }
                    ],
                )
            except Exception as e:
                print(f"Warning: Vector indexing failed: {e}")

        # Invalidate cache
        invalidate_cache(f"thread:{thread_id}:messages")

        return ConversationMessage(
            id=message_id,
            thread_id=thread_id,
            role=role,
            content=content,
            model=model,
            tokens=tokens,
            created_at=now,
        )

    # tenant-scoping-ok: default cache key includes call args (user_id/thread_id), which already differentiate callers.
    @cached(ttl=300, key_prefix="threads:user")
    def get_user_threads(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        organization_id: Optional[int] = None,
    ) -> List[ConversationThread]:
        """
        Get conversation threads for a user.

        Args:
            user_id: User ID
            limit: Max threads to return
            offset: Pagination offset
            organization_id: Only threads made in this organisation (the cache key
                includes it); None lists every organisation, for system callers.

        Returns:
            List of ConversationThread objects
        """
        params = {"user_id": user_id, "limit": limit, "offset": offset}
        if organization_id is None:
            statement = text(
                """
                SELECT id, user_id, title, model, created_at, updated_at, message_count
                FROM conversation_threads
                WHERE user_id = :user_id
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
            """
            )
        else:
            params["organization_id"] = organization_id
            statement = text(
                """
                SELECT id, user_id, title, model, created_at, updated_at, message_count
                FROM conversation_threads
                WHERE user_id = :user_id AND organization_id = :organization_id
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
            """
            )
        result = db.session.execute(statement, params)  # tenant-filtered: user_id and organization_id

        threads = []
        for row in result:
            threads.append(
                ConversationThread(
                    id=row.id,
                    user_id=row.user_id,
                    title=row.title,
                    model=row.model,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    message_count=row.message_count,
                )
            )

        return threads

    # tenant-scoping-ok: default cache key includes call args (user_id/thread_id), which already differentiate callers.
    @cached(ttl=300, key_prefix="thread:messages")
    def get_thread_messages(
        self, thread_id: str, limit: Optional[int] = None
    ) -> List[ConversationMessage]:
        """
        Get messages for a conversation thread.

        Args:
            thread_id: Thread ID
            limit: Optional limit on message count

        Returns:
            List of ConversationMessage objects
        """
        query = """
            SELECT id, thread_id, role, content, model, tokens, created_at
            FROM conversation_messages
            WHERE thread_id = :thread_id
            ORDER BY created_at ASC
        """

        if limit:
            query += " LIMIT :limit"

        result = db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
            text(query),
            {"thread_id": thread_id, "limit": limit} if limit else {"thread_id": thread_id},
        )

        messages = []
        for row in result:
            messages.append(
                ConversationMessage(
                    id=row.id,
                    thread_id=row.thread_id,
                    role=row.role,
                    content=row.content,
                    model=row.model,
                    tokens=row.tokens,
                    created_at=row.created_at,
                )
            )

        return messages

    def search_conversations(self, user_id: int, query: str, limit: int = 10) -> List[Dict]:
        """
        Semantic search across user's conversation history.

        Args:
            user_id: User ID to search within
            query: Search query
            limit: Max results to return

        Returns:
            List of matching messages with metadata
        """
        if not self.collection:
            raise RuntimeError(
                "Vector search not available. Install chromadb and sentence-transformers."
            )

        # Get user's thread IDs
        thread_ids = [t.id for t in self.get_user_threads(user_id, limit=1000)]

        if not thread_ids:
            return []

        # Generate query embedding
        query_embedding = self.embedder.encode([query])[0].tolist()

        # Search in vector store
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit * 2,  # Get extra results for filtering
            where={"thread_id": {"$in": thread_ids}},
        )

        # Format results
        matches = []
        if results["ids"] and results["ids"][0]:
            for idx, msg_id in enumerate(results["ids"][0]):
                if idx >= limit:
                    break

                matches.append(
                    {
                        "message_id": msg_id,
                        "thread_id": results["metadatas"][0][idx]["thread_id"],
                        "content": results["documents"][0][idx],
                        "role": results["metadatas"][0][idx]["role"],
                        "model": results["metadatas"][0][idx]["model"],
                        "distance": results["distances"][0][idx]
                        if "distances" in results
                        else None,
                        "created_at": results["metadatas"][0][idx]["created_at"],
                    }
                )

        return matches

    def delete_thread(self, thread_id: str):
        """Delete a conversation thread and its messages."""
        # Delete from vector store
        if self.collection:
            try:
                # Get all message IDs for this thread
                messages = self.get_thread_messages(thread_id)
                message_ids = [m.id for m in messages]
                if message_ids:
                    self.collection.delete(ids=message_ids)
            except Exception as e:
                print(f"Warning: Vector deletion failed: {e}")

        # Delete from database
        db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
            text("DELETE FROM conversation_messages WHERE thread_id = :thread_id"),
            {"thread_id": thread_id},
        )
        db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
            text("DELETE FROM conversation_threads WHERE id = :thread_id"), {"thread_id": thread_id}
        )
        db.session.commit()

        # Invalidate cache
        invalidate_cache(f"thread:{thread_id}:*")
        invalidate_cache("threads:user:*")

    def update_thread_title(self, thread_id: str, title: str):
        """Update thread title."""
        db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
            text(
                """
                UPDATE conversation_threads
                SET title = :title, updated_at = :updated_at
                WHERE id = :thread_id
            """
            ),
            {"title": title, "updated_at": datetime.utcnow(), "thread_id": thread_id},
        )
        db.session.commit()

        invalidate_cache("threads:user:*")


# =====================================================================


# =====================================================================
# Module-level API used by the chat routes.
#
# app/modules/ai_chat/routes/chat_core.py imported persist_turn (:479, :760),
# get_history_service and thread_owned_by (:1219, :1232, :1250) - none of which
# existed anywhere in the repository. The two persist_turn call sites are wrapped
# in `except Exception`, so the ImportError was swallowed and NO CONVERSATION WAS
# EVER SAVED; the thread-detail and delete routes import at function scope with
# no guard, so both returned 500. The result was a ChatGPT-style history rail
# that is permanently empty with two of its three controls broken.
# =====================================================================

_history_service = None


def get_history_service() -> ConversationHistoryService:
    """Process-wide singleton.

    Constructing the service initialises ChromaDB and a SentenceTransformer
    (see __init__), so building one per request would be very expensive - and
    every caller wants the same instance.
    """
    global _history_service
    if _history_service is None:
        _history_service = ConversationHistoryService()
    return _history_service


def thread_owned_by(thread_id: str, user_id: int, organization_id: Optional[int] = None) -> bool:
    """True when *thread_id* exists, belongs to *user_id* and was made in the
    caller's active organisation (or *organization_id* when given).

    Ownership is the authorisation boundary for the thread routes: conversation
    threads are keyed by user, so without this check any authenticated user
    could read or delete another user's chat history by guessing an id.
    """
    if not thread_id or not user_id:
        return False
    if organization_id is None:
        organization_id = current_org_id()
    params = {"thread_id": str(thread_id), "user_id": user_id}
    if organization_id is None:
        statement = text(
            "SELECT 1 FROM conversation_threads "
            "WHERE id = :thread_id AND user_id = :user_id LIMIT 1"
        )
    else:
        params["organization_id"] = organization_id
        statement = text(
            "SELECT 1 FROM conversation_threads "
            "WHERE id = :thread_id AND user_id = :user_id "
            "AND organization_id = :organization_id LIMIT 1"
        )
    row = db.session.execute(statement, params).first()  # tenant-filtered: user_id and organization_id
    return row is not None


def _derive_title(user_message: str) -> str:
    """First line of the opening message, trimmed - what the history rail shows."""
    text_ = (user_message or "").strip().splitlines()[0] if (user_message or "").strip() else ""
    if not text_:
        return "New conversation"
    return text_[:77] + "..." if len(text_) > 80 else text_


def persist_turn(
    user_id: int,
    thread_id: Optional[str],
    user_message: str,
    assistant_response: str,
    model: Optional[str] = None,
) -> Optional[str]:
    """Save one exchange and return the thread id to send back to the client.

    Creates the thread on the first turn. create_thread() already stores the
    opening user message via its initial_message argument, so passing it there
    and calling add_message() again would duplicate it - hence the branch.

    Returns None when there is no user to attribute the turn to; callers treat a
    falsy thread_id as "not persisted" rather than failing the request.
    """
    if not user_id:
        return None

    service = get_history_service()

    if thread_id and thread_owned_by(thread_id, user_id):
        service.add_message(thread_id, "user", user_message)
    else:
        thread = service.create_thread(
            user_id=user_id,
            title=_derive_title(user_message),
            model=model or "unknown",
            initial_message=user_message,
        )
        thread_id = thread.id

    if assistant_response:
        service.add_message(thread_id, "assistant", assistant_response, model=model)

    return thread_id
