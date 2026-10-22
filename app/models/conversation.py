"""AI chat conversation history — thread and message tables.

These two tables existed only in ``migrations/versions/add_conversation_tables.py``.
Deploys do not run ``flask db upgrade`` (see CLAUDE.md, "Schema management"), and
``create_all()`` cannot create a table no model declares — so a fresh database had
neither table and every ``/ai-chat/threads`` call raised ``UndefinedTable``. See
``docs/known-issues/conversation-tables-not-created-on-fresh-install.md``.

Declaring the models fixes that: ``flask init-db`` creates the tables and
``flask reconcile-schema`` keeps their columns in line, exactly as for every other
table.

Two deliberate constraints on this module:

* **The shapes below mirror the migration's DDL and the live production tables.**
  Column names, types, lengths, nullability, foreign keys and indexes all match.
  Anything extra here would make ``reconcile-schema`` try to ALTER a table that
  already holds production data.

* **No ``TenantMixin``.** A thread carries ``organization_id`` (the organisation
  that was active when it was made; revision ``20261021_chat_records_org`` adds
  and backfills it) and ``app/services/conversation_history.py`` filters on it
  as well as on ``user_id``, because a person can belong to several
  organisations. The column is nullable (a user with no home organisation can
  still chat) and the service, which uses raw SQL, does the scoping, so the ORM
  auto-filter that ``TenantMixin`` brings is not wanted here. Messages are
  reached only through their thread and carry no column of their own.

``app/services/conversation_history.py`` keeps its raw SQL. These classes exist so
the tables get created, not to replace that service.
"""

from .. import db

__all__ = ["ConversationThreadRecord", "ConversationMessageRecord"]


class ConversationThreadRecord(db.Model):
    """One AI chat conversation, owned by a user.

    Named ``...Record`` to avoid colliding with the ``ConversationThread``
    dataclass that ``app/services/conversation_history.py`` returns to callers;
    the two describe the same row but this one is the mapped table.
    """

    __tablename__ = "conversation_threads"

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    model = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)
    # Nullable with a server-side 0, matching the live table. The counter is
    # maintained by the service's UPDATE, not by the ORM.
    message_count = db.Column(db.Integer, default=0, server_default=db.text("0"))
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        # Serves the history rail's only query:
        # WHERE user_id = :user_id ORDER BY updated_at DESC.
        db.Index("idx_threads_user_updated", "user_id", "updated_at"),
    )

    def __repr__(self):
        return f"<ConversationThreadRecord {self.id} user={self.user_id}>"


class ConversationMessageRecord(db.Model):
    """One message inside a conversation thread."""

    __tablename__ = "conversation_messages"

    id = db.Column(db.String(36), primary_key=True)
    thread_id = db.Column(
        db.String(36), db.ForeignKey("conversation_threads.id"), nullable=False
    )
    role = db.Column(db.String(20), nullable=False)  # 'system' | 'user' | 'assistant'
    content = db.Column(db.Text, nullable=False)
    model = db.Column(db.String(50), nullable=True)
    tokens = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        # Serves the transcript query: WHERE thread_id = :id ORDER BY created_at.
        db.Index("idx_messages_thread_created", "thread_id", "created_at"),
    )

    def __repr__(self):
        return f"<ConversationMessageRecord {self.id} thread={self.thread_id} {self.role}>"
