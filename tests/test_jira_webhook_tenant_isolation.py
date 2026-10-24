"""Regression tests for the Jira webhook's fail-open cross-organisation write.

Three receivers accept a Jira status-change event and move the matching card:

* the dedicated, unauthenticated blueprint ``POST /webhooks/jira``
  (``app/modules/integrations/jira/jira_webhook_handler.handle_jira_webhook``);
* ``POST /admin/jira-settings/webhook`` in the v1 admin tree
  (``app/modules/admin/routes/admin_routes.jira_webhook``);
* ``POST /jira-settings/webhook`` in the v2 admin tree
  (``app/modules/admin/v2/routes/admin_routes.jira_webhook``).

Each one used to (1) accept the call when no webhook secret was configured and
(2) resolve the card with an unscoped ``KanbanCard.query.filter_by(
jira_issue_key=...)``. Together that let an unauthenticated caller (dedicated
receiver) or any logged-in user (admin receivers) move a card in any
organisation by guessing its Jira issue key.

These tests pin both fixes on all three receivers with two real organisations.
Each test fails on the pre-fix code and passes on the fixed code, and is named so
that reopening either path fails CI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

_SECRET = "webhook-secret-under-test"


# --------------------------------------------------------------------------
# Fixtures / builders
# --------------------------------------------------------------------------

def _make_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Jira {label} {suffix}", slug=f"jira-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org, *, is_org_admin=False, is_platform_admin=False):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"jira-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=is_org_admin,
        is_platform_admin=is_platform_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    from app.models.org_role import OrgRole

    db_session.add(
        OrgRole(
            organization_id=org.id,
            user_id=user.id,
            role="org_admin" if is_org_admin else "member",
        )
    )
    db_session.flush()
    return user


def _ensure_phase(db_session):
    from app.models.adm_kanban import ADMPhase

    phase = ADMPhase.query.filter_by(code="A").first()
    if phase is None:
        phase = ADMPhase(name="Phase A", code="A", order=2)
        db_session.add(phase)
        db_session.flush()
    return phase


def _make_card(db_session, org, user, issue_key, *, status="todo"):
    """A KanbanCard in *org* linked to a Jira issue key, via a scoped board."""
    from app.models.adm_kanban import KanbanBoard, KanbanCard

    phase = _ensure_phase(db_session)
    board = KanbanBoard(
        name=f"Board {uuid.uuid4().hex[:6]}",
        created_by_id=user.id,
        organization_id=org.id,
    )
    db_session.add(board)
    db_session.flush()
    card = KanbanCard(
        title=f"Card {uuid.uuid4().hex[:6]}",
        adm_phase_id=phase.id,
        board_id=board.id,
        card_type="requirement",
        created_by_id=user.id,
        organization_id=org.id,
        status=status,
        jira_issue_key=issue_key,
    )
    db_session.add(card)
    db_session.flush()
    return card


def _set_jira_connection_org(db_session, org_id):
    """Point the single ``jira`` ExternalSystem connection at *org_id*."""
    from app.models.models import ExternalSystem

    conn = ExternalSystem.query.filter_by(system_name="jira").first()
    if conn is None:
        conn = ExternalSystem(system_name="jira", system_type="alm")
        db_session.add(conn)
    conn.organization_id = org_id
    db_session.flush()
    return conn


def _done_payload(issue_key):
    return {
        "issue": {
            "key": issue_key,
            "fields": {"status": {"name": "Done"}},
        }
    }


def _sig_256(body: bytes) -> str:
    return hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _sig_hub(body: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


# ==========================================================================
# Receiver 1 — dedicated unauthenticated blueprint POST /webhooks/jira
# ==========================================================================

def test_dedicated_webhook_refuses_without_a_secret(app, client, db_session, monkeypatch):
    """No secret configured -> refuse (503), never accept and move a card."""
    monkeypatch.setitem(app.config, "JIRA_WEBHOOK_SECRET", "")

    org = _make_org(db_session, "a")
    user = _make_user(db_session, org)
    card = _make_card(db_session, org, user, "REF-1", status="todo")
    _set_jira_connection_org(db_session, org.id)
    db_session.commit()
    card_id = card.id

    body = json.dumps(_done_payload("REF-1")).encode()
    resp = client.post(
        "/webhooks/jira",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": _sig_256(body)},
    )

    assert resp.status_code == 503, resp.data
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_id).status == "todo", (
        "card changed while the webhook was unconfigured"
    )


def test_dedicated_webhook_signed_for_org_a_cannot_move_org_b_card(
    app, client, db_session, monkeypatch
):
    """A correctly signed call for org A's connection must not touch org B's card."""
    monkeypatch.setitem(app.config, "JIRA_WEBHOOK_SECRET", _SECRET)

    org_a = _make_org(db_session, "a")
    org_b = _make_org(db_session, "b")
    user_b = _make_user(db_session, org_b)
    # Org B owns the card for this issue key.
    card_b = _make_card(db_session, org_b, user_b, "SHARED-9", status="todo")
    # The signed connection belongs to org A.
    _set_jira_connection_org(db_session, org_a.id)
    db_session.commit()
    card_b_id = card_b.id

    body = json.dumps(_done_payload("SHARED-9")).encode()
    resp = client.post(
        "/webhooks/jira",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": _sig_256(body)},
    )

    assert resp.status_code == 200, resp.data
    assert resp.get_json().get("updated") is False, (
        "a call signed for org A reported moving a card — it must not reach org B's card"
    )
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_b_id).status == "todo", (
        "TENANT LEAK: org A's signed webhook changed org B's card"
    )


def test_dedicated_webhook_same_org_still_updates(app, client, db_session, monkeypatch):
    """The correctly scoped call (connection and card in the same org) still works."""
    monkeypatch.setitem(app.config, "JIRA_WEBHOOK_SECRET", _SECRET)

    org = _make_org(db_session, "a")
    user = _make_user(db_session, org)
    card = _make_card(db_session, org, user, "OWN-3", status="todo")
    _set_jira_connection_org(db_session, org.id)
    db_session.commit()
    card_id = card.id

    body = json.dumps(_done_payload("OWN-3")).encode()
    resp = client.post(
        "/webhooks/jira",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": _sig_256(body)},
    )

    assert resp.status_code == 200, resp.data
    assert resp.get_json().get("updated") is True
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_id).status == "done", (
        "the correctly scoped, correctly signed call must still move the card"
    )


# ==========================================================================
# Receiver 2 — v1 admin tree POST /admin/jira-settings/webhook
# ==========================================================================

def test_v1_admin_webhook_refuses_without_a_secret(
    app, client, login_as, db_session, monkeypatch
):
    """The v1 admin route tree specifically must refuse (503) when no secret is set."""
    monkeypatch.delenv("JIRA_WEBHOOK_SECRET", raising=False)

    org = _make_org(db_session, "a")
    user = _make_user(db_session, org, is_org_admin=True)
    card = _make_card(db_session, org, user, "ADM-1", status="todo")
    db_session.commit()
    card_id = card.id

    body = json.dumps(
        {"issue": {"key": "ADM-1"}, "changelog": {"items": [
            {"field": "status", "toString": "Done"}]}}
    ).encode()
    with app.app_context():
        login_as(client, user)
        resp = client.post(
            "/admin/jira-settings/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature": _sig_hub(body)},
        )

    assert resp.status_code == 503, resp.data
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_id).status == "todo"


def test_v1_admin_webhook_user_in_org_a_cannot_move_org_b_card(
    app, client, login_as, db_session, monkeypatch
):
    """A logged-in user in org A must not move org B's card via the v1 admin route."""
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", _SECRET)

    org_a = _make_org(db_session, "a")
    org_b = _make_org(db_session, "b")
    user_a = _make_user(db_session, org_a, is_org_admin=True)
    user_b = _make_user(db_session, org_b, is_org_admin=True)
    card_b = _make_card(db_session, org_b, user_b, "XORG-7", status="todo")
    db_session.commit()
    card_b_id = card_b.id

    body = json.dumps(
        {"issue": {"key": "XORG-7"}, "changelog": {"items": [
            {"field": "status", "toString": "Done"}]}}
    ).encode()
    with app.app_context():
        login_as(client, user_a)
        resp = client.post(
            "/admin/jira-settings/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature": _sig_hub(body)},
        )

    assert resp.status_code == 200, resp.data
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_b_id).status == "todo", (
        "TENANT LEAK: an org A user changed org B's card via the v1 admin webhook"
    )


def test_v1_admin_webhook_same_org_still_updates(
    app, client, login_as, db_session, monkeypatch
):
    """A user acting on their own organisation's card still moves it."""
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", _SECRET)

    org = _make_org(db_session, "a")
    user = _make_user(db_session, org, is_org_admin=True)
    card = _make_card(db_session, org, user, "MINE-2", status="todo")
    db_session.commit()
    card_id = card.id

    body = json.dumps(
        {"issue": {"key": "MINE-2"}, "changelog": {"items": [
            {"field": "status", "toString": "Done"}]}}
    ).encode()
    with app.app_context():
        login_as(client, user)
        resp = client.post(
            "/admin/jira-settings/webhook",
            data=body,
            content_type="application/json",
            headers={"X-Hub-Signature": _sig_hub(body)},
        )

    assert resp.status_code == 200, resp.data
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    # COLUMN_TO_JIRA_STATUS reverse-maps "Done" to the platform column.
    from app.config.kanban_jira_field_mapping import COLUMN_TO_JIRA_STATUS

    expected = {v: k for k, v in COLUMN_TO_JIRA_STATUS.items()}.get("Done")
    assert expected is not None
    assert db_session.get(KanbanCard, card_id).status == expected, (
        "the user's own-organisation card must still be moved"
    )


# ==========================================================================
# Receiver 3 — v2 admin tree jira_webhook (same shape; invoked directly, as
# only one admin tier is route-registered at a time)
# ==========================================================================

def test_v2_admin_webhook_refuses_without_a_secret(app, db_session, monkeypatch):
    monkeypatch.delenv("JIRA_WEBHOOK_SECRET", raising=False)
    from app.modules.admin.v2.routes.admin_routes import jira_webhook
    from flask_login import login_user

    org = _make_org(db_session, "a")
    user = _make_user(db_session, org, is_org_admin=True)
    card = _make_card(db_session, org, user, "V2-1", status="todo")
    db_session.commit()
    card_id = card.id

    body = json.dumps(
        {"issue": {"key": "V2-1"}, "changelog": {"items": [
            {"field": "status", "toString": "Done"}]}}
    ).encode()
    with app.test_request_context(
        "/jira-settings/webhook",
        method="POST",
        data=body,
        content_type="application/json",
    ):
        from flask import g

        login_user(user)
        g.current_org_id = org.id
        resp = jira_webhook()

    status_code = resp[1] if isinstance(resp, tuple) else resp.status_code
    assert status_code == 503
    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_id).status == "todo"


def test_v2_admin_webhook_user_in_org_a_cannot_move_org_b_card(app, db_session, monkeypatch):
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", _SECRET)
    from app.modules.admin.v2.routes.admin_routes import jira_webhook
    from flask_login import login_user

    org_a = _make_org(db_session, "a")
    org_b = _make_org(db_session, "b")
    user_a = _make_user(db_session, org_a, is_org_admin=True)
    user_b = _make_user(db_session, org_b, is_org_admin=True)
    card_b = _make_card(db_session, org_b, user_b, "V2-XORG", status="todo")
    db_session.commit()
    card_b_id = card_b.id

    body = json.dumps(
        {"issue": {"key": "V2-XORG"}, "changelog": {"items": [
            {"field": "status", "toString": "Done"}]}}
    ).encode()
    with app.test_request_context(
        "/jira-settings/webhook",
        method="POST",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature": _sig_hub(body)},
    ):
        from flask import g

        # The caller is acting as org A; org B owns the card.
        login_user(user_a)
        g.current_org_id = org_a.id
        jira_webhook()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert db_session.get(KanbanCard, card_b_id).status == "todo", (
        "TENANT LEAK: an org A caller changed org B's card via the v2 admin webhook"
    )


def test_v2_admin_webhook_same_org_still_updates(app, db_session, monkeypatch):
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", _SECRET)
    from app.modules.admin.v2.routes.admin_routes import jira_webhook
    from flask_login import login_user

    org = _make_org(db_session, "a")
    user = _make_user(db_session, org, is_org_admin=True)
    card = _make_card(db_session, org, user, "V2-MINE", status="todo")
    db_session.commit()
    card_id = card.id

    body = json.dumps(
        {"issue": {"key": "V2-MINE"}, "changelog": {"items": [
            {"field": "status", "toString": "Done"}]}}
    ).encode()
    with app.test_request_context(
        "/jira-settings/webhook",
        method="POST",
        data=body,
        content_type="application/json",
        headers={"X-Hub-Signature": _sig_hub(body)},
    ):
        from flask import g

        login_user(user)
        g.current_org_id = org.id
        jira_webhook()

    from app.config.kanban_jira_field_mapping import COLUMN_TO_JIRA_STATUS
    from app.models.adm_kanban import KanbanCard

    expected = {v: k for k, v in COLUMN_TO_JIRA_STATUS.items()}.get("Done")
    assert expected is not None
    db_session.expire_all()
    assert db_session.get(KanbanCard, card_id).status == expected, (
        "the caller's own-organisation card must still be moved via the v2 admin webhook"
    )
