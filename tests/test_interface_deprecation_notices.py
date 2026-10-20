"""Interface deprecation notices: name the consumers of an API operation and
notify their owners before the operation retires.

Everything here goes through the real routes:

- a bearer read on /api/v1 records one row per (organisation, consumer,
  operation) for both token families, and the row is committed
- a failing recorder never costs the caller a response
- the consumer list is for organisation administrators (their own clients) and
  counts only for a platform administrator, proven with two organisations
- marking an operation as retiring stores the retirement, adds the headers to
  that operation's real responses (success and error), and notifies each owner
  once
- the migration is idempotent

Needs the connector flag (MCP_ENABLED and PUBLIC_BASE_URL), as the other
bearer-token tests do.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import uuid
from pathlib import Path

import pytest

from app.modules.oauth_provider.tests.test_api_tokens import (
    _make_service_user,
    _mint_api_token,
    _register_service_client,
)

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("MCP_ENABLED", "").lower() in ("1", "true", "yes")
        and bool(os.environ.get("PUBLIC_BASE_URL"))
    ),
    reason="OAuth routes not registered (MCP_ENABLED off)",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20261020_api_op_calls.py"
ADMIN = "/admin/deprecation"


def _reset_identity():
    """db_session keeps one app context open, so g outlives each request."""
    from flask import g

    for cached in ("_login_user", "_current_user", "current_org_id", "current_org", "bearer_token", "auth_mode"):
        if hasattr(g, cached):
            delattr(g, cached)


def _bearer_get(app, path, token):
    _reset_identity()
    return app.test_client().get(path, headers={"Authorization": f"Bearer {token}"})


def _fresh_session():
    """A new session on the same database connection the test runs on."""
    from sqlalchemy.orm import Session

    from app import db

    return Session(bind=db.session.get_bind())


def _make_person(db_session, org, label, *, org_admin=False, platform_admin=False):
    from app.models.user import Role, User

    role_name = "Administrator" if (org_admin or platform_admin) else "Viewer"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="Person",
        organization_id=org.id,
        role=role,
        is_org_admin=org_admin,
        is_platform_admin=platform_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _admin_call(app, client, login_as, user, method, path, **kwargs):
    _reset_identity()
    login_as(client, user)
    return getattr(client, method)(f"{ADMIN}{path}", **kwargs)


def _consumers(db_session, make_org):
    """Two organisations; each has one registered client and one personal token."""
    org_a, org_b = make_org("depr-a"), make_org("depr-b")
    svc_a = _make_service_user(db_session, org_a, f"svc-a-{uuid.uuid4().hex[:6]}@example.com")
    svc_b = _make_service_user(db_session, org_b, f"svc-b-{uuid.uuid4().hex[:6]}@example.com")
    client_a = _register_service_client(db_session, svc_a)
    client_b = _register_service_client(db_session, svc_b)
    client_b.client_name = "Bravo Reader"
    client_a.client_name = "Alpha Reader"
    db_session.flush()
    tokens = {
        "client_a": _mint_api_token(svc_a, organization_id=org_a.id, client_id=client_a.client_id),
        "client_b": _mint_api_token(svc_b, organization_id=org_b.id, client_id=client_b.client_id),
        "personal_a": _mint_api_token(svc_a, organization_id=org_a.id),
    }
    return org_a, org_b, svc_a, svc_b, client_a, client_b, tokens


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _run_upgrade(app, times=1):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    spec = importlib.util.spec_from_file_location("migration_20261020_api_op_calls", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(times):
        with app.app_context():
            with db.engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()


def test_migration_runs_twice_and_both_tables_exist(app, db_session):
    from sqlalchemy import text

    from app import db

    _run_upgrade(app, times=2)
    with db.engine.connect() as connection:
        for table in ("api_operation_calls", "api_operation_retirements"):
            assert connection.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar() is not None
            assert connection.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:t)"), {"t": f"public.{table}"}
            ).scalar()
            policies = connection.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            ).scalar()
            assert policies == 4, (table, policies)
        foreign_keys = connection.execute(
            text(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid = to_regclass('public.api_operation_calls')"
            )
        ).scalar()
        assert foreign_keys == 2, "one named foreign key per column, no duplicates"


# ---------------------------------------------------------------------------
# Recording a call
# ---------------------------------------------------------------------------


def test_both_token_families_are_recorded_and_the_row_is_committed(app, db_session, make_org):
    from sqlalchemy import text

    org_a, _org_b, _svc_a, _svc_b, client_a, _client_b, tokens = _consumers(db_session, make_org)

    for _ in range(3):
        assert _bearer_get(app, "/api/v1/elements", tokens["client_a"]).status_code == 200
    assert _bearer_get(app, "/api/v1/elements", tokens["personal_a"]).status_code == 200
    assert _bearer_get(app, "/api/v1/elements/999999999", tokens["client_a"]).status_code == 404

    # A new session finds the rows: the record outlived the request that wrote it.
    db_session.expire_all()
    with _fresh_session() as other:
        rows = other.execute(
            text(
                "SELECT consumer_key, client_id, token_kind, operation_id, call_count "
                "FROM api_operation_calls WHERE organization_id = :o ORDER BY operation_id, consumer_key"
            ),
            {"o": org_a.id},
        ).all()
    by_key = {(r.consumer_key, r.operation_id): r for r in rows}
    registered = by_key[(client_a.client_id, "listElements")]
    assert registered.call_count == 3
    assert registered.token_kind == "oauth_client"
    assert registered.client_id == client_a.client_id
    personal_key = next(k for (k, op) in by_key if k.startswith("at_"))
    personal = by_key[(personal_key, "listElements")]
    assert personal.call_count == 1
    assert personal.token_kind == "api_token"
    assert personal.client_id is None
    assert (client_a.client_id, "getElement") in by_key, "an operation with a path parameter resolves too"


def test_a_failing_recorder_never_costs_the_caller_a_response(app, db_session, make_org, monkeypatch, caplog):
    from app import db
    from app.modules.oauth_provider import scopes

    _a, _b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    monkeypatch.setattr(scopes, "_UPSERT_CALL", db.text("INSERT INTO table_that_does_not_exist VALUES (1)"))
    with caplog.at_level(logging.WARNING):
        response = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert response.status_code == 200, response.get_data(as_text=True)
    assert any("API call record skipped" in r.getMessage() for r in caplog.records)
    # The request's own session is still usable afterwards.
    assert _bearer_get(app, "/api/v1/elements", tokens["client_b"]).status_code == 200


# ---------------------------------------------------------------------------
# The consumer list: two organisations, two roles
# ---------------------------------------------------------------------------


def test_org_admin_sees_own_clients_and_none_of_the_other_organisations(app, client, db_session, login_as, make_org):
    org_a, org_b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    _bearer_get(app, "/api/v1/elements", tokens["client_b"])
    admin_a = _make_person(db_session, org_a, "admin-a", org_admin=True)
    admin_b = _make_person(db_session, org_b, "admin-b", org_admin=True)

    body_a = _admin_call(app, client, login_as, admin_a, "get", "/api/clients")
    assert body_a.status_code == 200, body_a.get_data(as_text=True)
    text_a = body_a.get_data(as_text=True)
    names_a = {c["client_name"] for c in body_a.get_json()["clients"]}
    assert "Alpha Reader" in names_a
    assert "Bravo Reader" not in names_a and "Bravo Reader" not in text_a

    body_b = _admin_call(app, client, login_as, admin_b, "get", "/api/clients")
    names_b = {c["client_name"] for c in body_b.get_json()["clients"]}
    assert names_b == {"Bravo Reader"}


def test_platform_admin_sees_counts_per_organisation_and_no_names(app, client, db_session, login_as, make_org):
    org_a, org_b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    _bearer_get(app, "/api/v1/elements", tokens["client_b"])
    platform = _make_person(db_session, org_a, "platform", org_admin=True, platform_admin=True)

    response = _admin_call(app, client, login_as, platform, "get", "/api/clients")
    assert response.status_code == 200, response.get_data(as_text=True)
    raw = response.get_data(as_text=True)
    assert "Alpha Reader" not in raw and "Bravo Reader" not in raw
    counts = {o["organization_id"]: o for o in response.get_json()["organisations"]}
    assert counts[org_a.id]["total_calls"] == 2 and counts[org_a.id]["client_count"] == 1
    assert counts[org_b.id]["total_calls"] == 1 and counts[org_b.id]["client_count"] == 1
    assert "clients" not in response.get_json()


def test_a_plain_member_is_refused_both_routes(app, client, db_session, login_as, make_org):
    org_a = make_org("depr-member")
    member = _make_person(db_session, org_a, "member")
    assert _admin_call(app, client, login_as, member, "get", "/api/clients").status_code == 403
    response = _admin_call(
        app, client, login_as, member, "post", "/api/mark-retiring",
        json={"operation_id": "listElements", "retires_on": "2027-06-01"},
    )
    assert response.status_code == 403


def test_the_page_opens_for_an_org_admin_and_calls_live_endpoints(app, client, db_session, login_as, make_org):
    org_a = make_org("depr-page")
    admin = _make_person(db_session, org_a, "admin-page", org_admin=True)
    response = _admin_call(app, client, login_as, admin, "get", "/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    for path in ("/admin/deprecation/api/clients", "/admin/deprecation/api/mark-retiring"):
        assert path in page
        rule = next(r for r in app.url_map.iter_rules() if r.rule == path)
        assert rule.endpoint.startswith("deprecation."), "the page calls routes the running app registers"


# ---------------------------------------------------------------------------
# Marking an operation as retiring
# ---------------------------------------------------------------------------


def _mark(app, client, login_as, admin, **body):
    return _admin_call(app, client, login_as, admin, "post", "/api/mark-retiring", json=body)


def test_marking_validates_against_the_contract(app, client, db_session, login_as, make_org):
    org_a = make_org("depr-valid")
    admin = _make_person(db_session, org_a, "admin-valid", org_admin=True)
    assert _mark(app, client, login_as, admin, operation_id="noSuchOperation", retires_on="2027-06-01").status_code == 400
    assert _mark(app, client, login_as, admin, operation_id="listElements", retires_on="soon").status_code == 400
    assert _mark(
        app, client, login_as, admin, operation_id="listElements", retires_on="2027-06-01",
        replacement_operation_id="noSuchOperation",
    ).status_code == 400


def test_retiring_operation_answers_with_headers_on_success_and_error(app, client, db_session, login_as, make_org):
    org_a, org_b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    admin_a = _make_person(db_session, org_a, "admin-hdr", org_admin=True)

    before = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert before.status_code == 200 and "Deprecation" not in before.headers

    marked = _mark(
        app, client, login_as, admin_a,
        operation_id="listElements", retires_on="2027-06-01", replacement_operation_id="getElement",
    )
    assert marked.status_code == 200, marked.get_data(as_text=True)
    assert _mark(
        app, client, login_as, admin_a, operation_id="getElement", retires_on="2027-06-01"
    ).status_code == 200

    ok = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert ok.status_code == 200
    assert ok.headers["Deprecation"].startswith("@")
    assert ok.headers["Sunset"] == "Tue, 01 Jun 2027 00:00:00 GMT"
    assert "</api/v1/elements/{id}>" in ok.headers["Link"], "the link names the replacement's path, not its id"
    assert 'rel="deprecation"' in ok.headers["Link"]
    assert "getElement" not in ok.headers["Link"]

    missing = _bearer_get(app, "/api/v1/elements/999999999", tokens["client_a"])
    assert missing.status_code == 404
    assert missing.headers["Deprecation"].startswith("@"), "error responses carry the headers too"
    assert "Link" not in missing.headers, "no replacement was named for this operation"

    # Organisation B's administrator has marked nothing: its reads are unchanged.
    other = _bearer_get(app, "/api/v1/elements", tokens["client_b"])
    assert other.status_code == 200 and "Deprecation" not in other.headers

    # The retirement is a stored record, not process memory.
    with _fresh_session() as session:
        from sqlalchemy import text

        row = session.execute(
            text(
                "SELECT retires_on, replacement_operation_id FROM api_operation_retirements "
                "WHERE organization_id = :o AND operation_id = 'listElements'"
            ),
            {"o": org_a.id},
        ).one()
    assert str(row.retires_on) == "2027-06-01" and row.replacement_operation_id == "getElement"


def test_each_owner_is_notified_once_and_the_notice_is_committed(app, client, db_session, login_as, make_org):
    from sqlalchemy import text

    org_a, _org_b, svc_a, _svc_b, _ca, _cb, tokens = _consumers(db_session, make_org)
    admin = _make_person(db_session, org_a, "admin-notice", org_admin=True)
    _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    _bearer_get(app, "/api/v1/elements", tokens["personal_a"])

    first = _mark(app, client, login_as, admin, operation_id="listElements", retires_on="2027-06-01")
    assert first.status_code == 200 and first.get_json()["notices_created"] == 1, first.get_json()
    # Another date and another replacement is still the same retirement for the same owner.
    second = _mark(
        app, client, login_as, admin,
        operation_id="listElements", retires_on="2027-09-01", replacement_operation_id="getElement",
    )
    assert second.status_code == 200 and second.get_json()["notices_created"] == 0

    db_session.expire_all()
    with _fresh_session() as other:
        notices = other.execute(
            text(
                "SELECT user_id, message FROM notifications "
                "WHERE event_type = 'deprecation_notice' AND user_id = :u"
            ),
            {"u": svc_a.id},
        ).all()
    assert len(notices) == 1, notices
    assert "listElements" in notices[0].message
