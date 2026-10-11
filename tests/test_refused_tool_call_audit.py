"""A refused AI tool call is recorded where the administrator's audit screen reads it.

Both places a tool call is refused for lacking write access record the same
entry: the tool executor (the choke point for every agent write) and the
approval gate (someone without write access trying to run a queued tool call).
The entry carries the organisation, the person, the tool, the rule that
refused it and a secret-free summary of what was asked for; the audit screen
shows it only to that organisation's administrator.
"""

import json
import uuid
import warnings
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role_name):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]), first_name=label,
                last_name="Tester", organization_id=org.id, confirmed=True)
    user.role = Role.query.filter_by(name=role_name).one()
    db_session.add(user)
    db_session.flush()
    return user


def _refusals(org_id):
    from app.models.audit_log import AuditLog

    return AuditLog.query.filter_by(organization_id=org_id, action="tool_refused").all()


def test_the_executor_records_a_refused_write_with_its_rule(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("refused-exec")
    viewer = _user(db_session, org, "viewer", "Viewer")
    with tenant_ctx(org.id):
        result = ToolExecutor(viewer.id).execute(ToolCall(
            id="1", name="create_solution",
            arguments={"name": "Probe", "api_key": "sk-live-123", "_trusted_workspace_id": 9,
                       "details": {"owner": "Dana", "token": "abc"}},
        ))
    assert result["code"] == "PERMISSION_DENIED"

    [entry] = _refusals(org.id)
    assert entry.user_id == viewer.id and entry.table_name == "ai_tool_call"
    recorded = entry.new_value
    assert recorded["tool"] == "create_solution"
    assert recorded["rule"] == "write_permission"
    assert "Viewer role does not include write access" in recorded["rule_description"]
    assert recorded["via"] == "agent"
    assert recorded["arguments"] == {
        "api_key": "[withheld]", "details.owner": "Dana", "details.token": "[withheld]",
        "name": "Probe",
    }
    assert entry.status == "refused"
    assert "create_solution" in entry.description


def test_a_permitted_write_records_no_refusal(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org = make_org("refused-none")
    viewer = _user(db_session, org, "viewer", "Viewer")
    with tenant_ctx(org.id):
        ToolExecutor(viewer.id).execute(ToolCall(id="1", name="query_capability_gaps", arguments={}))
    assert _refusals(org.id) == []


def _queued_tool_call(db_session, requester, tool="apply_genome_patch"):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    approval = AIChatCRUDApproval(
        user_id=requester.id, organization_id=requester.organization_id,
        operation_type="tool_use", entity_type=tool, original_command=tool,
        operation_payload=json.dumps({"element": {"name": "Claims Desk"}}),
        summary="queued", status=ApprovalStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db_session.add(approval)
    db_session.flush()
    return approval


def test_running_a_queued_tool_call_without_write_access_is_recorded(
    db_session, make_org, tenant_ctx
):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("refused-approval")
    viewer = _user(db_session, org, "viewer", "Viewer")
    approval = _queued_tool_call(db_session, viewer)
    with tenant_ctx(org.id):
        result = AIChatApprovalService(viewer.id).approve_and_execute(approval.id, viewer.id)
    assert result["code"] == "FORBIDDEN"

    [entry] = _refusals(org.id)
    assert entry.new_value["tool"] == "apply_genome_patch"
    assert entry.new_value["via"] == "approval"
    assert entry.new_value["arguments"] == {"element.name": "Claims Desk"}


def test_another_organisations_approval_id_is_not_recorded(db_session, make_org, tenant_ctx):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    theirs = make_org("refused-theirs")
    mine = make_org("refused-mine")
    requester = _user(db_session, theirs, "architect", "Architect")
    viewer = _user(db_session, mine, "viewer", "Viewer")
    approval = _queued_tool_call(db_session, requester)
    with tenant_ctx(mine.id):
        result = AIChatApprovalService(viewer.id).approve_and_execute(approval.id, viewer.id)
    assert result["code"] == "FORBIDDEN"
    assert _refusals(mine.id) == [] and _refusals(theirs.id) == []


def test_the_audit_screen_shows_refusals_to_their_own_organisation_only(
    app, db_session, make_org, client, login_as
):
    from app.models.audit_log import AuditLog

    mine = make_org("refused-screen")
    other = make_org("refused-other")
    admin = _user(db_session, mine, "admin", "Administrator")
    other_admin = _user(db_session, other, "otheradmin", "Administrator")
    viewer = _user(db_session, mine, "viewer", "Viewer")
    db_session.add(AuditLog(
        organization_id=mine.id, user_id=viewer.id, action="tool_refused", table_name="ai_tool_call",
        new_value={"tool": "create_solution", "rule": "write_permission",
                   "rule_description": "The Viewer role does not include write access.",
                   "via": "agent", "arguments": {"name": "Probe"}},
    ))
    db_session.flush()

    login_as(client, admin)
    page = client.get("/admin/audit-log?action=tool_refused").get_data(as_text=True)
    assert "AI tool &#39;create_solution&#39; refused" in page or "AI tool 'create_solution' refused" in page
    assert "The Viewer role does not include write access." in page

    login_as(client, other_admin)
    page = client.get("/admin/audit-log?action=tool_refused").get_data(as_text=True)
    assert "create_solution" not in page
    assert ">tool_refused<" not in page, "another organisation's action names leaked into the filter"


def test_refusal_entry_resolves_user_email_without_legacy_query_get(db_session, make_org):
    from app.models.audit_log import AuditLog

    org = make_org("refused-email")
    viewer = _user(db_session, org, "viewer", "Viewer")
    entry = AuditLog(
        organization_id=org.id,
        user_id=viewer.id,
        action="tool_refused",
        table_name="ai_tool_call",
        new_value={"tool": "create_solution"},
    )
    db_session.add(entry)
    db_session.flush()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert entry.user_email == viewer.email

    assert not any("Query.get()" in str(w.message) for w in caught)
