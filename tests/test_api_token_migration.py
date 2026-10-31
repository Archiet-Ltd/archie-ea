"""The scoped-token roll-out migration: idempotent, one head, no orphan tokens.

Covers: the migration runs twice without error, the chain has exactly one
head, every revision id fits the 32-character ``alembic_version`` column,
the backfill copies ``organization_id`` from the token's user, and any token
that still has no organisation afterwards is revoked.

The database-touching tests here need the ``oauth_tokens`` table, which only
exists in builds with the connector on (MCP_ENABLED); they skip in a plain
build, exactly like the connector test modules themselves.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20261012_api_token_scopes.py"


def _load_revision():
    spec = importlib.util.spec_from_file_location("migration_20261012_api_scopes", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(app, times=1):
    """Apply the revision's upgrade() against the app engine, committing."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    module = _load_revision()
    for _ in range(times):
        with app.app_context():
            with db.engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    module.upgrade()


def _run_downgrade(app):
    """Apply the revision's downgrade() against the app engine, committing."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    module = _load_revision()
    with app.app_context():
        with db.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.downgrade()


def _has_column(app, table, column):
    from sqlalchemy import inspect

    from app import db

    with app.app_context():
        try:
            return column in {c["name"] for c in inspect(db.engine).get_columns(table)}
        except Exception:
            return False


def _scalar(app, sql, **params):
    from app import db

    with app.app_context():
        with db.engine.connect() as connection:
            return connection.execute(text(sql), params).scalar()


def _insert(app, sql, **params):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            return connection.execute(text(sql), params).scalar()


def test_migration_chain_has_a_single_head_and_short_revision_ids():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    directory = REPO_ROOT / "migrations"
    config = Config(str(directory / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"expected one migration head, found {heads}"
    for revision in script.walk_revisions():
        assert len(revision.revision) <= 32, (
            f"revision id {revision.revision!r} exceeds the 32-character alembic_version column"
        )
    revision = _load_revision()
    assert revision.down_revision == "20261010_oauth_token_grants"


def test_migration_runs_twice_without_error(app):
    if "oauth_provider.token" not in app.view_functions:
        pytest.skip("OAuth routes not registered (MCP_ENABLED off)")
    _run_upgrade(app, times=2)  # idempotent: the second run changes nothing


def test_downgrade_removes_the_added_columns(app):
    if "oauth_provider.token" not in app.view_functions:
        pytest.skip("OAuth routes not registered (MCP_ENABLED off)")
    _run_upgrade(app)
    assert _has_column(app, "users", "is_service_user")
    assert _has_column(app, "oauth_clients", "service_user_id")
    assert _has_column(app, "oauth_clients", "scope")
    _run_downgrade(app)
    assert not _has_column(app, "users", "is_service_user")
    assert not _has_column(app, "oauth_clients", "service_user_id")
    assert not _has_column(app, "oauth_clients", "scope")


@pytest.fixture
def migration_rows(app):
    """A committed org, user and two org-less token rows for the backfill.

    Cleans up after itself, so the migration assertions run against real
    committed rows, not a rolled-back transaction.
    """
    if "oauth_provider.token" not in app.view_functions:
        pytest.skip("OAuth routes not registered (MCP_ENABLED off)")
    suffix = uuid.uuid4().hex[:8]
    org_id = _insert(
        app,
        "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id",
        n=f"Mig {suffix}", s=f"mig-{suffix}",
    )
    role_id = _scalar(app, "SELECT id FROM roles WHERE name = 'Viewer'")
    if role_id is None:
        role_id = _insert(
            app,
            "INSERT INTO roles (name, permissions, index, default) "
            "VALUES ('Viewer', 0, 'main', false) RETURNING id",
        )
    user_id = _insert(
        app,
        "INSERT INTO users (email, organization_id, confirmed, role_id, first_name, last_name) "
        "VALUES (:e, :o, true, :r, 'Mig', 'User') RETURNING id",
        e=f"mig-{suffix}@example.com", o=org_id, r=role_id,
    )
    # One org-less token whose user is real (backfillable) and one whose user
    # can never resolve (must be revoked, not left active). The second insert
    # disables FK triggers for its own transaction only — token/user FKs
    # forbid a dangling user_id, and the whole point of that row is to be
    # what the backfill cannot fix.
    token_a = f"at-mig-{suffix}-a"
    token_b = f"at-mig-{suffix}-b"
    _insert(
        app,
        "INSERT INTO oauth_tokens (client_id, user_id, access_token, scope, resource, "
        "grant_type, issued_at, expires_at, organization_id) "
        "VALUES ('svc_mig', :u, :t, 'mcp:read', 'https://x/mcp', 'authorization_code', "
        "now() - interval '1 hour', now() + interval '1 hour', NULL) RETURNING id",
        u=user_id, t=token_a,
    )
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("SET session_replication_role = replica"))  # session-replication-role-ok: constructs a dangling user_id on purpose, a shape the backfill cannot fix and no real caller can write (the token/user FK forbids it)
            try:
                connection.execute(
                    text(
                        "INSERT INTO oauth_tokens (client_id, user_id, access_token, scope, resource, "
                        "grant_type, issued_at, expires_at, organization_id) "
                        "VALUES ('svc_mig', 999999999, :t, 'mcp:read', 'https://x/mcp', "
                        "'authorization_code', now() - interval '1 hour', now() + interval '1 hour', NULL)"
                    ),
                    {"t": token_b},
                )
            finally:
                connection.execute(text("SET session_replication_role = origin"))  # session-replication-role-ok: restores the switch flipped above

    def cleanup():
        with app.app_context():
            with db.engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM oauth_tokens WHERE access_token = ANY(:t)"),
                    {"t": [token_a, token_b]},
                )
                connection.execute(text("DELETE FROM users WHERE id = :i"), {"i": user_id})
                connection.execute(
                    text("DELETE FROM organizations WHERE id = :i"), {"i": org_id}
                )

    try:
        yield {"org_id": org_id, "user_id": user_id, "token_a": token_a, "token_b": token_b}
    finally:
        cleanup()


def test_backfill_assigns_the_users_org_and_revokes_what_it_cannot_fix(app, migration_rows):
    rows = migration_rows
    user_org = _scalar(app, "SELECT organization_id FROM users WHERE id = :i", i=rows["user_id"])
    assert user_org is not None

    _run_upgrade(app)

    assert _scalar(
        app, "SELECT organization_id FROM oauth_tokens WHERE access_token = :t", t=rows["token_a"]
    ) == user_org, "the token's organisation was not backfilled from its user"
    assert _scalar(
        app, "SELECT organization_id FROM oauth_tokens WHERE access_token = :t", t=rows["token_b"]
    ) is None, "the unfixable token still carries no organisation"
    assert _scalar(
        app, "SELECT revoked_at FROM oauth_tokens WHERE access_token = :t", t=rows["token_b"]
    ) is not None, "an org-less token the backfill cannot fix must be revoked"
    assert _scalar(
        app,
        "SELECT count(*) FROM oauth_tokens WHERE organization_id IS NULL AND revoked_at IS NULL",
    ) == 0, "no active token may remain without an organisation"
    assert _scalar(
        app, "SELECT count(*) FROM oauth_tokens WHERE organization_id IS NULL"
    ) == 1, "exactly the unfixable token stays org-less, and it is revoked"