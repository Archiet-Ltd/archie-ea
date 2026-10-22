"""The chat-history tables must be declared by models, not only by a migration.

``conversation_threads`` and ``conversation_messages`` were created by exactly one
thing — ``migrations/versions/add_conversation_tables.py``. Deploys do not run
``flask db upgrade``, and ``create_all()`` cannot create a table no model declares,
so a fresh database had neither table and every ``/ai-chat/threads`` call raised
``UndefinedTable``. See
``docs/known-issues/conversation-tables-not-created-on-fresh-install.md``.

These tests pin the two properties that fix keeps alive:

1. Both tables are in ``db.metadata``, so ``flask init-db`` builds them.
2. Their shape still matches the migration and the live production tables —
   because production already holds data, a model that drifts from it would make
   ``reconcile-schema`` ALTER a populated table.

Database-free: everything asserted here is metadata.
"""

import pytest
from sqlalchemy import DateTime, Integer, String, Text


@pytest.fixture(scope="module")
def metadata(app):
    from app import db

    return db.metadata


# (column, python type, length or None, nullable) exactly as
# migrations/versions/add_conversation_tables.py declares them.
THREAD_COLUMNS = [
    ("id", String, 36, False),
    ("user_id", Integer, None, False),
    ("title", String, 255, False),
    ("model", String, 50, False),
    ("created_at", DateTime, None, False),
    ("updated_at", DateTime, None, False),
    ("message_count", Integer, None, True),
    # Added by 20261021_chat_records_org: the organisation active when the thread was made.
    ("organization_id", Integer, None, True),
]

MESSAGE_COLUMNS = [
    ("id", String, 36, False),
    ("thread_id", String, 36, False),
    ("role", String, 20, False),
    ("content", Text, None, False),
    ("model", String, 50, True),
    ("tokens", Integer, None, True),
    ("created_at", DateTime, None, False),
]


@pytest.mark.parametrize("table_name", ["conversation_threads", "conversation_messages"])
def test_table_is_declared_so_create_all_builds_it(metadata, table_name):
    assert table_name in metadata.tables, (
        f"{table_name} is not in db.metadata, so flask init-db will not create it "
        "and a fresh install has no chat history at all."
    )


@pytest.mark.parametrize(
    "table_name,expected",
    [
        ("conversation_threads", THREAD_COLUMNS),
        ("conversation_messages", MESSAGE_COLUMNS),
    ],
)
def test_columns_match_the_migration(metadata, table_name, expected):
    table = metadata.tables[table_name]
    assert [c.name for c in table.columns] == [name for name, _, _, _ in expected]

    for name, py_type, length, nullable in expected:
        col = table.columns[name]
        assert isinstance(col.type, py_type), f"{table_name}.{name} type"
        if length is not None:
            assert col.type.length == length, f"{table_name}.{name} length"
        assert col.nullable is nullable, f"{table_name}.{name} nullability"


def test_foreign_keys_match_the_migration(metadata):
    threads = metadata.tables["conversation_threads"]
    messages = metadata.tables["conversation_messages"]

    assert {fk.target_fullname for fk in threads.columns["user_id"].foreign_keys} == {
        "users.id"
    }
    assert {fk.target_fullname for fk in messages.columns["thread_id"].foreign_keys} == {
        "conversation_threads.id"
    }


def test_indexes_match_the_migration(metadata):
    threads = metadata.tables["conversation_threads"]
    messages = metadata.tables["conversation_messages"]

    by_name = {ix.name: [c.name for c in ix.columns] for ix in threads.indexes}
    assert by_name.get("idx_threads_user_updated") == ["user_id", "updated_at"]

    by_name = {ix.name: [c.name for c in ix.columns] for ix in messages.indexes}
    assert by_name.get("idx_messages_thread_created") == ["thread_id", "created_at"]


def test_threads_carry_their_organisation_without_the_mixin(metadata):
    """A thread records the organisation it was made in (nullable, indexed, FK).

    It still does not use ``TenantMixin``: the service scopes its raw SQL by
    ``user_id`` and ``organization_id`` itself, and a user with no home
    organisation can still chat, so the column stays nullable.
    """
    col = metadata.tables["conversation_threads"].columns["organization_id"]
    assert col.nullable is True
    assert {fk.target_fullname for fk in col.foreign_keys} == {"organizations.id"}


def test_messages_have_no_organization_column(metadata):
    """Messages are reached only through their thread and carry no column of their own."""
    assert "organization_id" not in metadata.tables["conversation_messages"].columns
