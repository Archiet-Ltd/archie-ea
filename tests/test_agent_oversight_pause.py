"""Oversight pause switch: a paused organisation's mutating tool calls are refused.

The stop-all-writes switch takes effect on the next dispatch, not on a poll.
Pausing organisation B never touches organisation A's agents or calls.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role_name):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name=label,
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
    )
    user.role = Role.query.filter_by(name=role_name).one()
    db_session.add(user)
    db_session.flush()
    return user


# ------------------------------------------------------------------ #
# Executor-level: pause refuses mutating calls                        #
# ------------------------------------------------------------------ #


def test_paused_org_refuses_mutating_tool_call(db_session, make_org, tenant_ctx):
    """A paused organisation's mutating tool call is refused with WRITES_PAUSED."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("paused-refuse")
    architect = _user(db_session, org, "architect", "Architect")

    # Pause writes for this org
    state = AgentOversightState.get_for_org(org.id)
    state.pause(architect.id, "Security incident — all writes halted")
    db_session.flush()

    with tenant_ctx(org.id):
        result = ToolExecutor(architect.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "ShouldBeBlocked"},
        ))

    assert result["success"] is False
    assert result["code"] == "WRITES_PAUSED"
    assert result["writes_paused"] is True
    assert "Security incident" in result["error"]


def test_paused_org_still_allows_read_tools(db_session, make_org, tenant_ctx):
    """Read-only tools are not blocked when writes are paused."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("paused-read")
    architect = _user(db_session, org, "architect", "Architect")

    state = AgentOversightState.get_for_org(org.id)
    state.pause(architect.id, "Incident")
    db_session.flush()

    with tenant_ctx(org.id):
        result = ToolExecutor(architect.id).execute(ToolCall(
            id="1", name="query_capability_gaps", arguments={},
        ))

    # Read tools should still work
    assert result.get("success") is not False or result.get("code") != "WRITES_PAUSED"


def test_resumed_org_allows_mutating_calls_again(db_session, make_org, tenant_ctx):
    """After resume, mutating calls succeed again."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("resumed-ok")
    architect = _user(db_session, org, "architect", "Architect")

    # Pause then resume
    state = AgentOversightState.get_for_org(org.id)
    state.pause(architect.id, "Test pause")
    db_session.flush()
    state.resume()
    db_session.flush()

    with tenant_ctx(org.id):
        result = ToolExecutor(architect.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "ShouldSucceed"},
        ))

    assert result.get("code") != "WRITES_PAUSED"


def test_paused_call_is_recorded_in_audit_log(db_session, make_org, tenant_ctx):
    """A paused refusal is recorded in the audit log with the writes_paused rule."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.models.audit_log import AuditLog
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("paused-audit")
    architect = _user(db_session, org, "architect", "Architect")

    state = AgentOversightState.get_for_org(org.id)
    state.pause(architect.id, "Audit test pause")
    db_session.flush()

    with tenant_ctx(org.id):
        ToolExecutor(architect.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "AuditProbe"},
        ))

    refusals = AuditLog.query.filter_by(
        organization_id=org.id, action="tool_refused",
    ).all()
    paused_refusals = [r for r in refusals
                       if r.new_value and r.new_value.get("rule") == "writes_paused"]
    assert len(paused_refusals) >= 1
    entry = paused_refusals[0]
    assert entry.new_value["tool"] == "create_solution"
    assert entry.new_value["via"] == "oversight"
    assert "Audit test pause" in entry.new_value["rule_description"]


# ------------------------------------------------------------------ #
# Two-organisation isolation                                          #
# ------------------------------------------------------------------ #


def test_org_b_pause_does_not_affect_org_a(db_session, make_org, tenant_ctx):
    """Pausing organisation B never touches organisation A's agents or calls."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org_a = make_org("pause-iso-A")
    org_b = make_org("pause-iso-B")
    arch_a = _user(db_session, org_a, "archA", "Architect")
    arch_b = _user(db_session, org_b, "archB", "Architect")

    # Pause only org B
    state_b = AgentOversightState.get_for_org(org_b.id)
    state_b.pause(arch_b.id, "B-only incident")
    db_session.flush()

    # Org A's writes still work
    with tenant_ctx(org_a.id):
        result_a = ToolExecutor(arch_a.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "OrgAWorks"},
        ))
    assert result_a.get("code") != "WRITES_PAUSED"

    # Org B's writes are blocked
    with tenant_ctx(org_b.id):
        result_b = ToolExecutor(arch_b.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "OrgBBlocked"},
        ))
    assert result_b["code"] == "WRITES_PAUSED"


def test_org_b_refused_call_log_never_shows_org_a_entries(db_session, make_org, tenant_ctx):
    """B's refused-call log never touches A's calls."""
    from app.models.agent_oversight_state import AgentOversightState
    from app.models.audit_log import AuditLog
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org_a = make_org("log-iso-A")
    org_b = make_org("log-iso-B")
    arch_a = _user(db_session, org_a, "archA", "Architect")
    arch_b = _user(db_session, org_b, "archB", "Architect")

    # Pause both orgs
    for org, arch in [(org_a, arch_a), (org_b, arch_b)]:
        state = AgentOversightState.get_for_org(org.id)
        state.pause(arch.id, "Incident in %s" % org.name)
        db_session.flush()

    # Trigger a refusal in each org
    with tenant_ctx(org_a.id):
        ToolExecutor(arch_a.id).execute(ToolCall(
            id="1", name="create_solution", arguments={"name": "A-Probe"},
        ))
    with tenant_ctx(org_b.id):
        ToolExecutor(arch_b.id).execute(ToolCall(
            id="1", name="create_solution", arguments={"name": "B-Probe"},
        ))

    # Org A's log should only contain A's refusals
    a_refusals = AuditLog.query.filter_by(
        organization_id=org_a.id, action="tool_refused",
    ).all()
    a_tool_names = {r.new_value.get("tool") for r in a_refusals if r.new_value}
    assert "create_solution" in a_tool_names
    # Org A's log should not contain B's probe name in arguments
    for r in a_refusals:
        if r.new_value and r.new_value.get("arguments"):
            assert r.new_value["arguments"].get("name") != "B-Probe"

    # Org B's log should only contain B's refusals
    b_refusals = AuditLog.query.filter_by(
        organization_id=org_b.id, action="tool_refused",
    ).all()
    for r in b_refusals:
        if r.new_value and r.new_value.get("arguments"):
            assert r.new_value["arguments"].get("name") != "A-Probe"


# ------------------------------------------------------------------ #
# Oversight state model                                               #
# ------------------------------------------------------------------ #


def test_oversight_state_get_for_org_creates_if_missing(db_session, make_org):
    """get_for_org creates a row when none exists."""
    from app.models.agent_oversight_state import AgentOversightState

    org = make_org("state-new")
    state = AgentOversightState.get_for_org(org.id)
    assert state is not None
    assert state.organization_id == org.id
    assert state.writes_paused is False


def test_oversight_state_pause_and_resume_cycle(db_session, make_org):
    """Pause sets all fields; resume clears them."""
    from app.models.agent_oversight_state import AgentOversightState

    org = make_org("state-cycle")
    admin = _user(db_session, org, "admin", "Administrator")

    state = AgentOversightState.get_for_org(org.id)
    state.pause(admin.id, "Emergency maintenance")
    db_session.flush()

    assert state.writes_paused is True
    assert state.paused_by_id == admin.id
    assert state.paused_at is not None
    assert state.reason == "Emergency maintenance"

    state.resume()
    db_session.flush()

    assert state.writes_paused is False
    assert state.paused_by_id is None
    assert state.paused_at is None
    assert state.reason is None


def test_oversight_state_is_paused_returns_correctly(db_session, make_org):
    """is_paused() reflects the current state."""
    from app.models.agent_oversight_state import AgentOversightState

    org = make_org("state-ispaused")
    admin = _user(db_session, org, "admin", "Administrator")

    state = AgentOversightState.get_for_org(org.id)
    assert state.is_paused() is False

    state.pause(admin.id, "Test")
    db_session.flush()
    assert state.is_paused() is True

    state.resume()
    db_session.flush()
    assert state.is_paused() is False