"""Four routes returned or changed data that belongs to someone else.

Found by triaging reads of models that carry no tenant column, each verified against the code:

* the LLM decision log returned every user's prompts and decisions to any signed-in user;
* the usage-analytics events (user, session, route, metadata) were readable by any signed-in user;
* an AI audit entry could be read, and approved, by id from any organisation;
* the platform-wide agent configuration could be rewritten by any signed-in user.
"""

from __future__ import annotations

import uuid

PASSWORD = "test-password-123"


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"RS {label} {suffix}", slug=f"rs-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, role_name="User", platform=False):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    # A platform admin needs both the flag and the administer permission (is_platform_admin()).
    role = Role.query.filter_by(name="Administrator" if platform else role_name).first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="RS", last_name=f"User-{suffix}", email=f"rs-{suffix}@example.test",
        password=PASSWORD, confirmed=True, organization_id=org.id, role=role,
        is_org_admin=False, is_platform_admin=platform,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer", granted_by_id=user.id)
    db_session.flush()
    return user


# -- LLM decision log -----------------------------------------------------------------


def _record_decision_log(monkeypatch):
    from app.services.llm_service import LLMService

    seen = []
    monkeypatch.setattr(
        LLMService, "get_decision_log",
        staticmethod(lambda **kw: seen.append(kw.get("user_id")) or []),
    )
    return seen


def test_the_decision_log_is_limited_to_the_callers_own_decisions(app, db_session, login_as, client, monkeypatch):
    org = _org(db_session, "log")
    user = _user(db_session, org)
    db_session.commit()
    seen = _record_decision_log(monkeypatch)

    login_as(client, user)
    resp = client.get("/api/agentic-gaps/decision-logs")

    assert resp.status_code == 200
    assert seen == [user.id]


def test_a_platform_admin_still_sees_every_users_decisions(app, db_session, login_as, client, monkeypatch):
    org = _org(db_session, "log-admin")
    admin = _user(db_session, org, platform=True)
    db_session.commit()
    seen = _record_decision_log(monkeypatch)

    login_as(client, admin)
    resp = client.get("/api/agentic-gaps/decision-logs")

    assert resp.status_code == 200
    assert seen == [None]


# -- usage analytics ------------------------------------------------------------------


def _event(db_session, user, path):
    from app.models.usage_analytics import UsageAnalytics

    row = UsageAnalytics(
        user_id=user.id, session_id=f"s-{uuid.uuid4().hex[:6]}", event_type="page_view",
        feature_name="x_feature", route_path=path,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_usage_events_show_only_the_callers_own_activity(app, db_session, login_as, client):
    org_a, org_b = _org(db_session, "use-a"), _org(db_session, "use-b")
    me, other = _user(db_session, org_a), _user(db_session, org_b)
    _event(db_session, me, "/mine")
    _event(db_session, other, "/theirs")
    db_session.commit()

    login_as(client, me)
    resp = client.get("/usage-analytics/api/events?limit=500")

    paths = {e["route_path"] for e in resp.get_json()}
    assert "/mine" in paths
    assert "/theirs" not in paths


def test_a_platform_admin_sees_every_users_usage_events(app, db_session, login_as, client):
    org_a, org_b = _org(db_session, "use-pa-a"), _org(db_session, "use-pa-b")
    admin, other = _user(db_session, org_a, platform=True), _user(db_session, org_b)
    _event(db_session, other, "/theirs-for-admin")
    db_session.commit()

    login_as(client, admin)
    resp = client.get("/usage-analytics/api/events?limit=500")

    assert "/theirs-for-admin" in {e["route_path"] for e in resp.get_json()}


# -- AI audit entries -----------------------------------------------------------------


def _audit_entry(db_session, *, solution=None, user=None):
    from app.models.ai_audit_log import AIAuditLog

    entry = AIAuditLog(
        action="generate", model_name="test-model", solution_id=solution.id if solution else None,
        user_id=user.id if user else None, prompt_summary="secret prompt summary",
        reasoning="secret reasoning",
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _solution(db_session, org):
    from app.models.solution_models import Solution

    solution = Solution(name=f"Sol {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def test_an_ai_audit_entry_of_another_organisations_solution_is_not_readable(app, db_session, login_as, client):
    org_a, org_b = _org(db_session, "aud-a"), _org(db_session, "aud-b")
    intruder = _user(db_session, org_a)
    entry = _audit_entry(db_session, solution=_solution(db_session, org_b))
    db_session.commit()

    login_as(client, intruder)
    resp = client.get(f"/api/architecture-assistant/ai-reasoning/entry/{entry.id}")

    assert resp.status_code == 404
    assert "secret reasoning" not in resp.get_data(as_text=True)


def test_an_ai_audit_entry_of_the_callers_own_solution_is_still_readable(app, db_session, login_as, client):
    org = _org(db_session, "aud-own")
    user = _user(db_session, org)
    entry = _audit_entry(db_session, solution=_solution(db_session, org))
    db_session.commit()

    login_as(client, user)

    assert client.get(f"/api/architecture-assistant/ai-reasoning/entry/{entry.id}").status_code == 200


def test_an_entry_with_no_solution_belongs_to_the_user_who_made_it(app, db_session, login_as, client):
    org = _org(db_session, "aud-solo")
    owner, other = _user(db_session, org), _user(db_session, org)
    entry = _audit_entry(db_session, user=owner)
    db_session.commit()

    login_as(client, other)
    assert client.get(f"/api/architecture-assistant/ai-reasoning/entry/{entry.id}").status_code == 404

    login_as(client, owner)
    assert client.get(f"/api/architecture-assistant/ai-reasoning/entry/{entry.id}").status_code == 200


def test_an_ai_audit_entry_of_another_organisation_cannot_be_approved(app, db_session, login_as, client):
    from sqlalchemy import text

    org_a, org_b = _org(db_session, "apr-a"), _org(db_session, "apr-b")
    intruder = _user(db_session, org_a, role_name="Administrator")
    entry = _audit_entry(db_session, solution=_solution(db_session, org_b))
    db_session.commit()
    entry_id = entry.id

    login_as(client, intruder)
    resp = client.post(
        f"/api/architecture-assistant/ai-reasoning/entry/{entry_id}/approve", json={"decision": "approved"}
    )

    status = db_session.execute(
        text("SELECT approval_status FROM ai_audit_logs WHERE id = :id"), {"id": entry_id}
    ).scalar()
    assert resp.status_code == 404
    assert status != "approved"


# -- platform-wide agent configuration ------------------------------------------------


def test_an_ordinary_user_cannot_rewrite_the_platform_agent_configuration(app, db_session, login_as, client):
    org = _org(db_session, "agent")
    user = _user(db_session, org)
    db_session.commit()
    name = f"agent-{uuid.uuid4().hex[:6]}"

    login_as(client, user)
    denied = client.post(f"/api/agentic-gaps/config/{name}", json={"require_review": False})
    readable = client.get(f"/api/agentic-gaps/config/{name}")

    assert denied.status_code == 403
    assert readable.status_code == 200


def test_a_platform_admin_can_rewrite_the_agent_configuration(app, db_session, login_as, client):
    org = _org(db_session, "agent-pa")
    admin = _user(db_session, org, platform=True)
    db_session.commit()
    name = f"agent-{uuid.uuid4().hex[:6]}"

    login_as(client, admin)
    resp = client.post(f"/api/agentic-gaps/config/{name}", json={"require_review": False})

    assert resp.status_code == 200
