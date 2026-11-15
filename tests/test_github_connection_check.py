"""Testing a GitHub connection only reads, names what failed, and shows only reasons it can vouch for.

A person who clicks Test on a connector wants to know whether the token reaches GitHub. The check
must not change the stored settings, must say which account GitHub could not find, and must keep
an unexpected fault out of the words a person reads, reporting it through the route's generic,
logged answer instead. Two real organisations are used: the second one's connector is never touched.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from tests.test_github_connector import connect, github, org_a, org_b  # noqa: F401
from tests.test_connector_sync_route_honesty import _make_admin

pytestmark = pytest.mark.usefixtures("db_session")


def _build(cfg):
    from app.services.connector_framework import build_connector

    return build_connector(cfg, cfg.organization_id)


def test_testing_the_connection_does_not_change_the_stored_settings(
    db_session, tenant_ctx, github, org_a, org_b, connect
):
    github.overrides["orgs/acme"] = (200, {"type": "Organization", "login": "acme"}, {})
    mine = connect(org_a, {"organization": "acme"})
    theirs = connect(org_b, {"organization": "acme"})
    before = (dict(mine.config), mine.name, dict(theirs.config), theirs.name)

    with tenant_ctx(org_a.id):
        ok, message = _build(mine).check_connection()
    db_session.expire_all()

    assert ok and message == "Connected to GitHub as acme"
    assert (dict(mine.config), mine.name, dict(theirs.config), theirs.name) == before
    assert "account_type" not in mine.config


def test_an_account_github_cannot_find_is_named(db_session, tenant_ctx, github, org_a, connect):
    cfg = connect(org_a, {"organization": "ghost-team", "account_type": "User"})
    with tenant_ctx(org_a.id):
        ok, message = _build(cfg).check_connection()
    assert not ok
    assert "the account ghost-team" in message
    assert "HTTP 404" in message


def test_an_unexpected_fault_is_not_shown_as_a_reason(db_session, tenant_ctx, github, org_a, connect, monkeypatch):
    cfg = connect(org_a, {"organization": "acme", "account_type": "User"})
    connector = _build(cfg)

    def broken(*a, **k):
        raise RuntimeError("login")

    monkeypatch.setattr(type(connector), "_get", broken)
    with tenant_ctx(org_a.id), pytest.raises(RuntimeError):
        connector.check_connection()


@pytest.fixture
def admin(db_session, org_a):
    return _make_admin(db_session, org_a.id, "Tester")


def _post(app, login_as, admin, cfg):
    client = app.test_client()
    login_as(client, admin)
    return client.post(f"/integrations/api/connectors/{cfg.id}/test")


def test_route_says_a_connector_type_cannot_be_tested(app, db_session, org_a, admin, login_as, connect, monkeypatch):
    cfg = connect(org_a)
    db_session.commit()

    def unknown(*a, **k):
        raise LookupError("no such connector")

    monkeypatch.setattr("app.routes.connector_routes.build_connector", unknown)
    response = _post(app, login_as, admin, cfg)
    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is False
    assert body["message"] == "This connector type cannot be tested yet."


def test_route_answers_an_unexpected_fault_generically_and_logs_it(
    app, db_session, github, org_a, admin, login_as, connect, monkeypatch, caplog
):
    cfg = connect(org_a, {"organization": "acme", "account_type": "User"})
    db_session.commit()

    def broken(*a, **k):
        raise RuntimeError("login")

    from app.connectors.github import GitHubConnector

    monkeypatch.setattr(GitHubConnector, "_get", broken)
    with caplog.at_level(logging.ERROR):
        response = _post(app, login_as, admin, cfg)
    body = response.get_json()
    assert body["success"] is False
    assert "login" not in body["message"]
    assert body["message"].startswith("The test could not be completed.")
    assert any("Connector test failed" in r.getMessage() and r.exc_info for r in caplog.records)


def test_route_shows_the_reason_github_gave(app, db_session, github, org_a, admin, login_as, connect):
    cfg = connect(org_a, {"organization": "ghost-team", "account_type": "User"})
    db_session.commit()
    body = _post(app, login_as, admin, cfg).get_json()
    assert body["success"] is False
    assert "the account ghost-team" in body["message"]
