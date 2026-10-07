"""The signed-webhooks revision only adds columns and can be applied twice (R1-B28)."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

REPO = pathlib.Path(__file__).resolve().parent.parent
REVISION_FILE = REPO / "migrations" / "versions" / "20261007_signed_webhooks.py"

SUBSCRIPTION_COLUMNS = {"secret_encrypted", "last_ordinal"}
DELIVERY_COLUMNS = {
    "log_event_id",
    "event_ordinal",
    "next_attempt_at",
    "first_attempt_at",
    "dead_lettered_at",
    "is_replay",
    "replay_of_id",
    "is_test",
    "request_body",
    "signature_timestamp",
    "signature",
}


def _load():
    spec = importlib.util.spec_from_file_location("signed_webhooks_revision", REVISION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection, table):
    return {column["name"] for column in inspect(connection).get_columns(table)}


def test_the_revision_is_chained_onto_an_existing_revision():
    from alembic.config import Config

    config = Config(str(REPO / "migrations" / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "migrations"))
    script = ScriptDirectory.from_config(config)
    module = _load()
    assert module.revision == "20261007_signed_webhooks"
    assert module.down_revision == "20261007_public_visitor_events"
    assert script.get_revision(module.down_revision) is not None
    assert len(script.get_heads()) == 1


def test_upgrade_twice_is_a_no_op_and_downgrade_then_upgrade_restores_the_columns(app, db_session):
    module = _load()
    connection = db_session.connection()
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        module.upgrade()
        module.upgrade()
        assert SUBSCRIPTION_COLUMNS <= _columns(connection, "webhook_subscriptions")
        assert DELIVERY_COLUMNS <= _columns(connection, "webhook_deliveries")

        module.downgrade()
        module.downgrade()
        assert not SUBSCRIPTION_COLUMNS & _columns(connection, "webhook_subscriptions")
        assert not DELIVERY_COLUMNS & _columns(connection, "webhook_deliveries")

        module.upgrade()
        assert SUBSCRIPTION_COLUMNS <= _columns(connection, "webhook_subscriptions")
        assert DELIVERY_COLUMNS <= _columns(connection, "webhook_deliveries")


def test_no_table_is_created():
    source = REVISION_FILE.read_text(encoding="utf-8")
    assert "create_table" not in source.lower()
    assert "CREATE TABLE" not in source.upper()


def test_every_statement_is_idempotent():
    source = REVISION_FILE.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in source
    assert "CREATE INDEX IF NOT EXISTS" in source
    assert "DROP COLUMN IF EXISTS" in source
    assert "DROP INDEX IF EXISTS" in source
