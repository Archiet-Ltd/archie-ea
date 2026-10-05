"""Oversight routes: pause/resume, refused-call log, catalogue export, classification.

Route-level tests for the agent oversight controls. Every route is
organisation-scoped: B's pause switch and refused-call log never touch A's
agents or calls.
"""

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_org_admin(db_session, org, label="admin"):
    """Create an org admin user."""
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name=label,
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Administrator").one()
    db_session.add(user)
    db_session.flush()
    return user


def _make_security_architect(db_session, org, label="secarch"):
    """Create a security architect user."""
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name=label,
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="security_architect",
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _make_architect(db_session, org, label="arch"):
    """Create a regular architect user (not org admin)."""
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name=label,
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


# ------------------------------------------------------------------ #
# Pause / Resume routes                                               #
# ------------------------------------------------------------------ #


def test_pause_route_requires_org_admin(app, db_session, make_org, client, login_as):
    """Only org admins can pause writes."""
    org = make_org("route-pause-auth")
    admin = _make_org_admin(db_session, org)
    architect = _make_architect(db_session, org)

    # Architect cannot pause
    login_as(client, architect)
    resp = client.post(
        "/ai-chat/oversight/pause",
        data=json.dumps({"reason": "Test"}),
        content_type="application/json",
    )
    assert resp.status_code == 403

    # Admin can pause
    login_as(client, admin)
    resp = client.post(
        "/ai-chat/oversight/pause",
        data=json.dumps({"reason": "Security incident"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["state"]["writes_paused"] is True
    assert data["state"]["reason"] == "Security incident"


def test_pause_route_requires_reason(app, db_session, make_org, client, login_as):
    """Pause without a reason returns 400."""
    org = make_org("route-pause-noreason")
    admin = _make_org_admin(db_session, org)

    login_as(client, admin)
    resp = client.post(
        "/ai-chat/oversight/pause",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_resume_route_requires_org_admin(app, db_session, make_org, client, login_as):
    """Only org admins can resume writes."""
    org = make_org("route-resume-auth")
    admin = _make_org_admin(db_session, org)
    architect = _make_architect(db_session, org)

    # First pause as admin
    login_as(client, admin)
    client.post(
        "/ai-chat/oversight/pause",
        data=json.dumps({"reason": "Test"}),
        content_type="application/json",
    )

    # Architect cannot resume
    login_as(client, architect)
    resp = client.post("/ai-chat/oversight/resume", content_type="application/json")
    assert resp.status_code == 403

    # Admin can resume
    login_as(client, admin)
    resp = client.post("/ai-chat/oversight/resume", content_type="application/json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["state"]["writes_paused"] is False


def test_get_state_route_requires_org_admin(app, db_session, make_org, client, login_as):
    """Only org admins can view oversight state."""
    org = make_org("route-state-auth")
    admin = _make_org_admin(db_session, org)
    architect = _make_architect(db_session, org)

    login_as(client, architect)
    resp = client.get("/ai-chat/oversight/state")
    assert resp.status_code == 403

    login_as(client, admin)
    resp = client.get("/ai-chat/oversight/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True


def test_service_fails_closed_without_tenant_context(app, db_session, make_org):
    """When g.current_org_id is None, every service method returns an error."""
    from app.modules.ai_chat.services.agent_oversight_service import AgentOversightService

    org = make_org("no-tenant-ctx")
    admin = _make_org_admin(db_session, org)

    service = AgentOversightService(admin.id)

    # Without a tenant context, _get_user_and_org returns (None, None)
    user, org_id = service._get_user_and_org()
    assert user is None
    assert org_id is None

    # Every public method should fail closed
    result = service.pause_all_writes("test")
    assert result["success"] is False
    assert "User not found" in result.get("error", "")

    result = service.resume_all_writes()
    assert result["success"] is False

    result = service.get_oversight_state()
    assert result["success"] is False

    result = service.get_refused_calls()
    assert result["success"] is False

    result = service.check_tool_classification("create_solution")
    assert result["success"] is False

    result = service.get_all_classification_status()
    assert result["success"] is False


# ------------------------------------------------------------------ #
# Two-organisation isolation for pause/resume                         #
# ------------------------------------------------------------------ #


def test_org_b_pause_does_not_affect_org_a_state(app, db_session, make_org, client, login_as):
    """Pausing org B does not change org A's oversight state."""
    org_a = make_org("route-iso-A")
    org_b = make_org("route-iso-B")
    admin_a = _make_org_admin(db_session, org_a, "adminA")
    admin_b = _make_org_admin(db_session, org_b, "adminB")

    # Pause org B
    login_as(client, admin_b)
    client.post(
        "/ai-chat/oversight/pause",
        data=json.dumps({"reason": "B-only incident"}),
        content_type="application/json",
    )

    # Org A's state should still show not paused
    login_as(client, admin_a)
    resp = client.get("/ai-chat/oversight/state")
    data = resp.get_json()
    assert data["state"]["writes_paused"] is False

    # Org B's state should show paused
    login_as(client, admin_b)
    resp = client.get("/ai-chat/oversight/state")
    data = resp.get_json()
    assert data["state"]["writes_paused"] is True
    assert data["state"]["reason"] == "B-only incident"


# ------------------------------------------------------------------ #
# Refused call log routes                                             #
# ------------------------------------------------------------------ #


def test_refused_calls_route_requires_org_admin(app, db_session, make_org, client, login_as):
    """Only org admins can view the refused call log."""
    org = make_org("route-refused-auth")
    admin = _make_org_admin(db_session, org)
    architect = _make_architect(db_session, org)

    login_as(client, architect)
    resp = client.get("/ai-chat/oversight/refused-calls")
    assert resp.status_code == 403

    login_as(client, admin)
    resp = client.get("/ai-chat/oversight/refused-calls")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "refused_calls" in data


def test_refused_calls_rejects_invalid_limit_and_offset(app, db_session, make_org, client, login_as):
    """Negative limit and offset values return 400, not 500."""
    org = make_org("route-refused-validate")
    admin = _make_org_admin(db_session, org)

    login_as(client, admin)

    # limit=-1 should return 400
    resp = client.get("/ai-chat/oversight/refused-calls?limit=-1")
    assert resp.status_code == 400, "limit=-1 should return 400, got %s" % resp.status_code

    # limit=0 should return 400
    resp = client.get("/ai-chat/oversight/refused-calls?limit=0")
    assert resp.status_code == 400, "limit=0 should return 400, got %s" % resp.status_code

    # offset=-1 should return 400
    resp = client.get("/ai-chat/oversight/refused-calls?offset=-1")
    assert resp.status_code == 400, "offset=-1 should return 400, got %s" % resp.status_code

    # Valid limit and offset should return 200
    resp = client.get("/ai-chat/oversight/refused-calls?limit=50&offset=0")
    assert resp.status_code == 200, "valid limit/offset should return 200, got %s" % resp.status_code


def test_refused_calls_shows_paused_refusals(app, db_session, make_org, client, login_as, tenant_ctx):
    """Refused calls from paused writes appear in the log."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("route-refused-paused")
    admin = _make_org_admin(db_session, org)
    architect = _make_architect(db_session, org)

    # Pause and trigger a refusal
    state = AgentOversightState.get_for_org(org.id)
    state.pause(admin.id, "Route test pause")
    db_session.flush()

    with tenant_ctx(org.id):
        ToolExecutor(architect.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "RouteProbe"},
        ))

    # Check the refused call log via the route
    login_as(client, admin)
    resp = client.get("/ai-chat/oversight/refused-calls")
    data = resp.get_json()
    assert data["success"] is True
    assert data["total"] >= 1
    # At least one entry should have the writes_paused rule
    paused_entries = [
        e for e in data["refused_calls"]
        if e.get("rule") == "writes_paused"
    ]
    assert len(paused_entries) >= 1
    assert paused_entries[0]["tool"] == "create_solution"


def test_refused_calls_org_isolation(app, db_session, make_org, client, login_as, tenant_ctx):
    """Org B's refused call log never shows org A's entries."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org_a = make_org("route-log-iso-A")
    org_b = make_org("route-log-iso-B")
    admin_a = _make_org_admin(db_session, org_a, "adminA")
    admin_b = _make_org_admin(db_session, org_b, "adminB")
    arch_a = _make_architect(db_session, org_a, "archA")

    # Pause org A and trigger a refusal
    state_a = AgentOversightState.get_for_org(org_a.id)
    state_a.pause(admin_a.id, "A incident")
    db_session.flush()

    with tenant_ctx(org_a.id):
        ToolExecutor(arch_a.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "A-Only"},
        ))

    # Org B's log should be empty (or not contain A's entries)
    login_as(client, admin_b)
    resp = client.get("/ai-chat/oversight/refused-calls")
    data = resp.get_json()
    # Org B should see no entries from org A
    for entry in data.get("refused_calls", []):
        assert entry.get("tool") != "create_solution" or "A-Only" not in str(entry)


# ------------------------------------------------------------------ #
# Catalogue export routes                                             #
# ------------------------------------------------------------------ #


def test_catalogue_export_requires_security_architect(app, db_session, make_org, client, login_as):
    """Only security architect or platform admin can export the catalogue."""
    org = make_org("route-cat-auth")
    sec_arch = _make_security_architect(db_session, org)
    architect = _make_architect(db_session, org)

    # Regular architect cannot export
    login_as(client, architect)
    resp = client.get("/ai-chat/oversight/catalogue/export")
    assert resp.status_code == 403

    # Security architect can export
    login_as(client, sec_arch)
    resp = client.get("/ai-chat/oversight/catalogue/export")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "catalogue" in data
    assert len(data["catalogue"]) > 0


def test_catalogue_export_filter_write_tools(app, db_session, make_org, client, login_as):
    """Filtering to write tools only excludes read tools."""
    org = make_org("route-cat-filter")
    sec_arch = _make_security_architect(db_session, org)

    login_as(client, sec_arch)
    resp = client.get("/ai-chat/oversight/catalogue/export?write_tools_only=true")
    assert resp.status_code == 200
    data = resp.get_json()
    for entry in data["catalogue"]:
        assert entry["mutates"] is True


def test_catalogue_export_includes_risk_class_and_record_types(app, db_session, make_org, client, login_as):
    """Catalogue export includes risk_class, record_types_written, and engine."""
    org = make_org("route-cat-fields")
    sec_arch = _make_security_architect(db_session, org)

    login_as(client, sec_arch)
    resp = client.get("/ai-chat/oversight/catalogue/export")
    data = resp.get_json()
    # Find a write tool in the catalogue
    write_tools = [e for e in data["catalogue"] if e["mutates"]]
    assert len(write_tools) > 0
    sample = write_tools[0]
    assert "risk_class" in sample
    assert "declared_record_types_written" in sample
    assert "surfaces" in sample
    assert "route" in sample


# ------------------------------------------------------------------ #
# Classification check routes                                         #
# ------------------------------------------------------------------ #


def test_classification_check_route_requires_org_admin(app, db_session, make_org, client, login_as):
    """Only org admins can check tool classification."""
    org = make_org("route-class-auth")
    admin = _make_org_admin(db_session, org)
    architect = _make_architect(db_session, org)

    login_as(client, architect)
    resp = client.get("/ai-chat/oversight/classification/check/create_solution")
    assert resp.status_code == 403

    login_as(client, admin)
    resp = client.get("/ai-chat/oversight/classification/check/create_solution")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "classification" in data


def test_classification_status_route(app, db_session, make_org, client, login_as):
    """Classification status returns all tools."""
    org = make_org("route-class-status")
    admin = _make_org_admin(db_session, org)

    login_as(client, admin)
    resp = client.get("/ai-chat/oversight/classification/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "classifications" in data
    assert "non_compliant_count" in data