"""Interface deprecation notices: name the consumers of an API operation and
notify their owners before the operation retires.

Everything here goes through the real routes:

- a bearer read on /api/v1 records one row per (organisation, consumer,
  operation) for both token families, and the row is committed
- a failing recorder never costs the caller a response
- the consumer list is for organisation administrators (their own clients) and
  counts only for a platform administrator, proven with two organisations
- a platform administrator marks an operation as retiring for every caller; the
  retirement is stored, adds the headers to that operation's real responses
  (success and error), notifies each owner once, and can be undone
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
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20261020_api_op_usage.py"
ADMIN = "/admin/deprecation"


def _reset_identity():
    """db_session keeps one app context open, so g outlives each request."""
    from flask import g

    for cached in ("_login_user", "_current_user", "current_org_id", "current_org", "bearer_token", "auth_mode"):
        if hasattr(g, cached):
            delattr(g, cached)


def _bearer_get(app, path, token):
    """GET with a bearer token and end the request: the call is recorded once the
    response has been sent, which a test client only does when told to close."""
    _reset_identity()
    response = app.test_client().get(path, headers={"Authorization": f"Bearer {token}"})
    response.get_data()
    response.close()
    return response


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

    spec = importlib.util.spec_from_file_location("migration_20261020_api_op_usage", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(times):
        with app.app_context():
            with db.engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()


def test_migration_runs_twice_and_the_tables_have_the_right_fence(app, db_session):
    from sqlalchemy import text

    from app import db

    _run_upgrade(app, times=2)
    with db.engine.connect() as connection:

        def scalar(sql, **params):
            return connection.execute(text(sql), params).scalar()

        rls_on = "SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:t)"
        policies = "SELECT count(*) FROM pg_policies WHERE tablename = :t"
        for table in ("api_operation_calls", "api_operation_retirements", "api_retirement_notices"):
            assert scalar("SELECT to_regclass(:t)", t=f"public.{table}") is not None
        # The call table is tenant data: row security on, four policies.
        assert scalar(rls_on, t="public.api_operation_calls")
        assert scalar(policies, t="api_operation_calls") == 4
        # Retirements and notices are platform data: no organisation column, no fence.
        for table in ("api_operation_retirements", "api_retirement_notices"):
            assert not scalar(rls_on, t=f"public.{table}")
            assert scalar(policies, t=table) == 0
            assert not scalar(
                "SELECT count(*) FROM information_schema.columns WHERE table_name = :t "
                "AND column_name = 'organization_id'", t=table,
            )
        foreign_keys = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE contype = 'f' "
                    "AND conrelid = to_regclass('public.api_operation_calls')"
                )
            )
        }
        assert foreign_keys == {
            "fk_api_operation_calls_organization_id",
            "fk_api_operation_calls_client_id",
        }, "one named foreign key per column, no duplicates"
        assert scalar(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'uq_api_retirement_notices_retirement_user'"
        ) == 1


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


def test_the_record_is_written_after_the_response_not_during_it(app, db_session, make_org):
    from sqlalchemy import text

    org_a, _b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    _reset_identity()
    response = app.test_client().get("/api/v1/elements", headers={"Authorization": f"Bearer {tokens['client_a']}"})
    response.get_data()
    count = "SELECT count(*) FROM api_operation_calls WHERE organization_id = :o"
    with _fresh_session() as session:
        assert session.execute(text(count), {"o": org_a.id}).scalar() == 0, "written before the response was sent"
    response.close()
    with _fresh_session() as session:
        assert session.execute(text(count), {"o": org_a.id}).scalar() == 1


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


def test_a_failing_retirement_lookup_leaves_the_request_transaction_alone(
    app, db_session, make_org, monkeypatch, caplog
):
    from sqlalchemy.orm import Session

    from app import db
    from app.modules.oauth_provider import scopes

    _a, _b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    rollbacks = []
    original = Session.rollback
    monkeypatch.setattr(Session, "rollback", lambda self, *a, **k: (rollbacks.append(1), original(self, *a, **k))[1])
    monkeypatch.setattr(scopes, "_SELECT_RETIREMENT", db.text("SELECT * FROM table_that_does_not_exist"))
    with caplog.at_level(logging.WARNING):
        response = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert response.status_code == 200
    assert any("Retirement lookup skipped" in r.getMessage() for r in caplog.records)
    assert rollbacks == [], "the session was rolled back, which would discard what the request already holds"


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


def test_only_a_platform_admin_may_mark_or_undo_a_retirement(app, client, db_session, login_as, make_org):
    org_a = make_org("depr-member")
    member = _make_person(db_session, org_a, "member")
    org_admin = _make_person(db_session, org_a, "orgadmin", org_admin=True)
    body = {"operation_id": "listElements", "retires_on": "2027-06-01"}
    for who in (member, org_admin):
        assert _admin_call(app, client, login_as, who, "get", "/api/clients").status_code in (200, 403)
        for path in ("/api/mark-retiring", "/api/clear-retiring"):
            assert _admin_call(app, client, login_as, who, "post", path, json=body).status_code == 403
    assert _admin_call(app, client, login_as, member, "get", "/api/clients").status_code == 403


def test_the_page_opens_for_an_org_admin_and_calls_live_endpoints(app, client, db_session, login_as, make_org):
    org_a = make_org("depr-page")
    admin = _make_person(db_session, org_a, "admin-page", org_admin=True)
    response = _admin_call(app, client, login_as, admin, "get", "/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "/admin/deprecation/api/clients" in page
    assert "id=\"mark-retiring-form\"" not in page, "the marking form is for platform administrators"
    platform = _make_person(db_session, org_a, "platform-page", org_admin=True, platform_admin=True)
    page = _admin_call(app, client, login_as, platform, "get", "/").get_data(as_text=True)
    for path in (
        "/admin/deprecation/api/clients",
        "/admin/deprecation/api/mark-retiring",
        "/admin/deprecation/api/clear-retiring",
    ):
        assert path in page
        rule = next(r for r in app.url_map.iter_rules() if r.rule == path)
        assert rule.endpoint.startswith("deprecation."), "the page calls routes the running app registers"


# ---------------------------------------------------------------------------
# Marking an operation as retiring (platform-wide)
# ---------------------------------------------------------------------------


def _mark(app, client, login_as, admin, **body):
    return _admin_call(app, client, login_as, admin, "post", "/api/mark-retiring", json=body)


def _clear(app, client, login_as, admin, **body):
    return _admin_call(app, client, login_as, admin, "post", "/api/clear-retiring", json=body)


def test_marking_validates_against_the_contract(app, client, db_session, login_as, make_org):
    org_a = make_org("depr-valid")
    admin = _make_person(db_session, org_a, "admin-valid", org_admin=True, platform_admin=True)
    assert _mark(app, client, login_as, admin, operation_id="noSuchOperation", retires_on="2027-06-01").status_code == 400
    assert _mark(app, client, login_as, admin, operation_id="listElements", retires_on="soon").status_code == 400
    assert _mark(
        app, client, login_as, admin, operation_id="listElements", retires_on="2027-06-01",
        replacement_operation_id="noSuchOperation",
    ).status_code == 400


def test_a_platform_admin_with_no_organisation_can_mark_and_undo(app, client, db_session, login_as):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"no-org-{uuid.uuid4().hex[:8]}@example.com", first_name="No", last_name="Org",
        organization_id=None, role=role, is_platform_admin=True, confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    marked = _mark(app, client, login_as, user, operation_id="listElements", retires_on="2027-06-01")
    assert marked.status_code == 200, marked.get_data(as_text=True)
    assert _clear(app, client, login_as, user, operation_id="listElements").status_code == 200
    assert _clear(app, client, login_as, user, operation_id="listElements").status_code == 404


def test_retiring_operation_answers_every_caller_with_headers_on_success_and_error(
    app, client, db_session, login_as, make_org
):
    org_a, org_b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)
    platform = _make_person(db_session, org_a, "platform-hdr", org_admin=True, platform_admin=True)

    before = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert before.status_code == 200 and "Deprecation" not in before.headers

    marked = _mark(
        app, client, login_as, platform,
        operation_id="listElements", retires_on="2027-06-01", replacement_operation_id="getElement",
    )
    assert marked.status_code == 200, marked.get_data(as_text=True)
    assert _mark(
        app, client, login_as, platform, operation_id="getElement", retires_on="2027-06-01"
    ).status_code == 200

    ok = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert ok.status_code == 200
    assert ok.headers["Deprecation"].startswith("@")
    assert ok.headers["Sunset"] == "Tue, 01 Jun 2027 00:00:00 GMT"
    # getElement has a path parameter, so the link names the collection path, a valid URI reference.
    assert ok.headers["Link"] == '</api/v1/elements>; rel="successor-version"'

    missing = _bearer_get(app, "/api/v1/elements/999999999", tokens["client_a"])
    assert missing.status_code == 404
    assert missing.headers["Deprecation"].startswith("@"), "error responses carry the headers too"
    assert "Link" not in missing.headers, "no replacement was named for this operation"

    # One contract, one retirement: another organisation's callers see it as well.
    other = _bearer_get(app, "/api/v1/elements", tokens["client_b"])
    assert other.status_code == 200 and other.headers["Deprecation"].startswith("@")

    # The retirement is a stored record, not process memory.
    with _fresh_session() as session:
        from sqlalchemy import text

        row = session.execute(
            text("SELECT retires_on, replacement_operation_id FROM api_operation_retirements WHERE operation_id = 'listElements'")
        ).one()
    assert str(row.retires_on) == "2027-06-01" and row.replacement_operation_id == "getElement"

    # Undo clears the headers.
    assert _clear(app, client, login_as, platform, operation_id="listElements").status_code == 200
    after = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert after.status_code == 200 and "Deprecation" not in after.headers


def test_a_link_to_an_operation_without_parameters_names_its_own_path(app):
    import datetime
    from types import SimpleNamespace

    from app.modules.oauth_provider.scopes import retirement_headers

    row = SimpleNamespace(
        marked_at=datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc),
        retires_on=datetime.date(2027, 6, 1),
        replacement_operation_id="listElements",
    )
    assert retirement_headers(row)["Link"] == '</api/v1/elements>; rel="successor-version"'
    row.replacement_operation_id = None
    assert "Link" not in retirement_headers(row)


def test_every_owner_in_every_organisation_is_notified_once_and_an_earlier_date_notifies_again(
    app, client, db_session, login_as, make_org
):
    from sqlalchemy import text

    org_a, org_b, svc_a, svc_b, _ca, _cb, tokens = _consumers(db_session, make_org)
    platform = _make_person(db_session, org_a, "platform-notice", org_admin=True, platform_admin=True)
    _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    _bearer_get(app, "/api/v1/elements", tokens["personal_a"])
    _bearer_get(app, "/api/v1/elements", tokens["client_b"])

    def notices(user):
        db_session.expire_all()
        with _fresh_session() as other:
            return other.execute(
                text("SELECT message, organization_id FROM notifications WHERE event_type = 'deprecation_notice' AND user_id = :u"),
                {"u": user.id},
            ).all()

    first = _mark(app, client, login_as, platform, operation_id="listElements", retires_on="2027-06-01")
    assert first.status_code == 200 and first.get_json()["notices_created"] == 2, first.get_json()
    assert len(notices(svc_a)) == 1 and notices(svc_a)[0].organization_id == org_a.id
    assert len(notices(svc_b)) == 1 and notices(svc_b)[0].organization_id == org_b.id
    assert "listElements" in notices(svc_a)[0].message

    # The same date, or a later one, tells nobody twice.
    for date in ("2027-06-01", "2027-09-01"):
        again = _mark(app, client, login_as, platform, operation_id="listElements", retires_on=date)
        assert again.get_json()["notices_created"] == 0, date
    assert len(notices(svc_a)) == 1

    # An earlier date is news.
    earlier = _mark(app, client, login_as, platform, operation_id="listElements", retires_on="2027-03-01")
    assert earlier.get_json()["notices_created"] == 2
    assert len(notices(svc_a)) == 2 and len(notices(svc_b)) == 2

    # Two markings cannot both claim the same owner: the claim is one atomic upsert.
    with _fresh_session() as other:
        duplicates = other.execute(
            text("SELECT count(*) FROM (SELECT retirement_id, user_id FROM api_retirement_notices GROUP BY 1, 2 HAVING count(*) > 1) d")
        ).scalar()
    assert duplicates == 0


def test_marking_the_same_operation_twice_in_a_row_is_an_upsert(app, db_session):
    from sqlalchemy import text

    from app.utils.deprecation import mark_operation_retiring
    import datetime

    first = mark_operation_retiring("listElements", datetime.date(2027, 6, 1), None)
    second = mark_operation_retiring("listElements", datetime.date(2027, 3, 1), "getElement")
    assert first.id == second.id
    assert second.retires_on == datetime.date(2027, 3, 1) and second.replacement_operation_id == "getElement"
    count = db_session.execute(
        text("SELECT count(*) FROM api_operation_retirements WHERE operation_id = 'listElements'")
    ).scalar()
    assert count == 1


def test_an_unreadable_contract_never_fails_a_read(app, db_session, make_org, monkeypatch, caplog):
    from sqlalchemy import text

    from app.modules.oauth_provider import scopes

    org_a, _b, _sa, _sb, _ca, _cb, tokens = _consumers(db_session, make_org)

    def missing(*args, **kwargs):
        raise FileNotFoundError("api_contract_subset.yaml")

    monkeypatch.setattr(scopes, "load_subset", missing)
    monkeypatch.setattr(scopes, "_operation_index", None)
    monkeypatch.setattr(scopes, "_operation_paths", None)
    with caplog.at_level(logging.WARNING):
        first = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
        second = _bearer_get(app, "/api/v1/elements", tokens["client_a"])
    assert first.status_code == 200 and second.status_code == 200
    assert "Deprecation" not in first.headers
    assert sum("API contract could not be read" in r.getMessage() for r in caplog.records) == 1, "logged once"
    count = db_session.execute(
        text("SELECT count(*) FROM api_operation_calls WHERE organization_id = :o"), {"o": org_a.id}
    ).scalar()
    assert count == 0, "nothing is recorded when every operation is unknown"
