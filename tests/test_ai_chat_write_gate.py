"""AI chat write paths honour the user's write permission.

A user whose role carries no write access (Viewer) must be refused on every
chat path that writes: the /link-capability and /generate-from-capabilities
slash commands, and the natural-language branches of the chat service reached
from /ai-chat/entity-matching. Each refusal leaves no row behind and leaves a
``tool_refused`` audit row. A permitted user is unaffected, read-only commands
are unaffected, and a Viewer in one organisation cannot write into another.
"""

import uuid

import pytest

from tests.test_ai_write_approval import _login


def _user(db_session, org, role_name, tag):
    from app.models.user import Role, User

    user = User(
        email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=tag,
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="architect",
    )
    role = Role.query.filter_by(name=role_name).first()
    assert role is not None, role_name
    user.role = role
    user.role_id = role.id
    db_session.add(user)
    db_session.flush()
    return user


def _capability_and_solution(db_session, org):
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import Solution

    tag = uuid.uuid4().hex[:6]
    cap = BusinessCapability(name=f"ZZCap{tag}", organization_id=org.id)
    sol = Solution(name=f"ZZSol{tag}", organization_id=org.id)
    db_session.add_all([cap, sol])
    db_session.flush()
    return cap, sol


def _mapping_count(solution_id, capability_id=None):
    from app import db

    # Raw count: the ORM count is tenant-filtered to whoever is signed in, and these tests ask what
    # another organisation's attempt left behind.
    sql = "SELECT count(*) FROM solution_capability_mappings WHERE solution_id = :s"
    params = {"s": solution_id}
    if capability_id is not None:
        sql += " AND capability_id = :c"
        params["c"] = capability_id
    return db.session.execute(db.text(sql), params).scalar()


def _element_count():
    # Raw count: the ORM count is tenant-filtered to whoever is signed in, which
    # would hide a write into another organisation.
    from app import db

    return db.session.execute(db.text("SELECT count(*) FROM archimate_elements")).scalar()


def _refusals_since(first_audit_id, user):
    from app.models.audit_log import AuditLog

    return AuditLog.query.filter(
        AuditLog.id > first_audit_id,
        AuditLog.action == "tool_refused",
        AuditLog.user_id == user.id,
    ).all()


def _last_audit_id():
    from app.models.audit_log import AuditLog

    latest = AuditLog.query.order_by(AuditLog.id.desc()).first()
    return latest.id if latest else 0


@pytest.fixture
def chat(app, client, db_session, make_org):
    app.config["AI_CHAT_ENABLED"] = True

    def post(user, message):
        _login(client, user.id)
        return client.post(
            "/ai-chat/message", json={"message": message, "domain": "general"}
        )

    return post


def test_viewer_has_no_write_permission(db_session, make_org):
    from app.models.user import Permission

    viewer = _user(db_session, make_org("gate"), "Viewer", "viewer")
    assert viewer.can(Permission.GENERAL) is False


def test_viewer_link_capability_is_refused(chat, db_session, make_org):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    cap, sol = _capability_and_solution(db_session, org)
    seen = _last_audit_id()

    resp = chat(viewer, f"/link-capability {cap.name} to {sol.name}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "write access" in body["response"]
    assert _mapping_count(sol.id, cap.id) == 0
    refusals = _refusals_since(seen, viewer)
    assert len(refusals) == 1
    assert refusals[0].new_value["via"] == "slash_command"
    assert refusals[0].new_value["tool"] == "link-capability"


def test_architect_link_capability_succeeds(chat, db_session, make_org):
    org = make_org("gate")
    architect = _user(db_session, org, "Architect", "architect")
    cap, sol = _capability_and_solution(db_session, org)
    seen = _last_audit_id()

    resp = chat(architect, f"/link-capability {cap.name} to {sol.name}")

    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert _mapping_count(sol.id, cap.id) == 1
    assert _refusals_since(seen, architect) == []


def _link(db_session, sol, cap):
    from app.models.solution_models import SolutionCapabilityMapping

    db_session.add(
        SolutionCapabilityMapping(
            solution_id=sol.id, capability_id=cap.id, support_level="required"
        )
    )
    db_session.flush()


def test_viewer_generate_from_capabilities_is_refused(chat, db_session, make_org):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    cap, sol = _capability_and_solution(db_session, org)
    _link(db_session, sol, cap)
    before = _element_count()
    seen = _last_audit_id()

    resp = chat(viewer, f"/generate-from-capabilities {sol.name}")

    assert resp.status_code == 200
    assert "write access" in resp.get_json()["response"]
    assert _element_count() == before
    refusals = _refusals_since(seen, viewer)
    assert len(refusals) == 1
    assert refusals[0].new_value["via"] == "slash_command"


def test_architect_generate_from_capabilities_succeeds(chat, db_session, make_org):
    org = make_org("gate")
    architect = _user(db_session, org, "Architect", "architect")
    cap, sol = _capability_and_solution(db_session, org)
    _link(db_session, sol, cap)
    before = _element_count()
    seen = _last_audit_id()

    resp = chat(architect, f"/generate-from-capabilities {sol.name}")

    assert resp.status_code == 200
    assert _element_count() > before
    assert _refusals_since(seen, architect) == []


def _stub_model_link(monkeypatch, sol):
    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    # What a model returns for a document that says "attach X to solution Y".
    monkeypatch.setattr(
        MultiDomainChatService,
        "_extract_capability_action_via_llm",
        lambda self, message: {
            "action": "link",
            "confidence": 0.9,
            "solution_name": sol.name,
        },
    )


def _entity_matching(client, user, cap, sol):
    _login(client, user.id)
    return client.post(
        "/ai-chat/entity-matching",
        json={"document_text": f"Please attach {cap.name} capability to {sol.name}."},
    )


def test_viewer_cap013_via_entity_matching_is_refused(
    app, client, db_session, make_org, monkeypatch
):
    app.config["AI_CHAT_ENABLED"] = True
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    cap, sol = _capability_and_solution(db_session, org)
    _stub_model_link(monkeypatch, sol)
    seen = _last_audit_id()

    resp = _entity_matching(client, viewer, cap, sol)

    assert resp.status_code == 403
    assert resp.get_json()["code"] == "PERMISSION_DENIED"
    assert _mapping_count(sol.id, cap.id) == 0
    refusals = _refusals_since(seen, viewer)
    assert len(refusals) == 1
    assert refusals[0].new_value["via"] == "chat_nl"


def test_architect_cap013_via_entity_matching_still_links(
    app, client, db_session, make_org, monkeypatch
):
    app.config["AI_CHAT_ENABLED"] = True
    org = make_org("gate")
    architect = _user(db_session, org, "Architect", "architect")
    cap, sol = _capability_and_solution(db_session, org)
    _stub_model_link(monkeypatch, sol)
    seen = _last_audit_id()

    resp = _entity_matching(client, architect, cap, sol)

    assert resp.status_code != 403
    assert _mapping_count(sol.id, cap.id) == 1
    assert _refusals_since(seen, architect) == []


def test_entity_matching_rejects_anonymous(client):
    resp = client.post("/ai-chat/entity-matching", json={"document_text": "x"})
    assert resp.status_code in (401, 302)


def test_viewer_keyword_link_in_chat_service_is_refused(app, db_session, make_org):
    from flask import g

    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    cap, sol = _capability_and_solution(db_session, org)
    g.current_org_id = org.id
    seen = _last_audit_id()

    service = MultiDomainChatService(user_id=viewer.id)
    result = service.detect_and_handle_crud_operation(
        f"link capability {cap.name} to {sol.name}", "general", {}, viewer.id
    )

    assert result["success"] is False
    assert result["permission_denied"] is True
    assert _mapping_count(sol.id, cap.id) == 0
    assert len(_refusals_since(seen, viewer)) == 1


def test_viewer_cannot_advance_capability_design_workflow(app, db_session, make_org):
    from flask import g, session

    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    cap, sol = _capability_and_solution(db_session, org)
    g.current_org_id = org.id
    seen = _last_audit_id()
    workflow = {
        "step": "SUGGEST",
        "solution_id": sol.id,
        "solution_name": sol.name,
        "suggested_capabilities": [{"id": cap.id, "name": cap.name}],
    }

    with app.test_request_context("/"):
        result = MultiDomainChatService(
            user_id=viewer.id
        )._advance_capability_design_workflow("accept all", workflow)
        assert "_cap_design_workflow_state" not in session

    assert result["success"] is False
    assert result["permission_denied"] is True
    assert _mapping_count(sol.id, cap.id) == 0
    assert len(_refusals_since(seen, viewer)) == 1


def test_read_only_commands_still_work_for_viewer(chat, db_session, make_org):
    viewer = _user(db_session, make_org("gate"), "Viewer", "viewer")
    seen = _last_audit_id()

    resp = chat(viewer, "/help")

    assert resp.status_code == 200
    assert "Available Slash Commands" in resp.get_json()["response"]
    assert _refusals_since(seen, viewer) == []


def test_every_command_states_whether_it_writes():
    from app.modules.ai_chat.services.command_parser_service import CommandParserService

    undeclared = [
        cmd for cmd, info in CommandParserService.COMMANDS.items() if "mutates" not in info
    ]
    assert undeclared == []
    writers = {c for c, i in CommandParserService.COMMANDS.items() if i["mutates"]}
    assert writers == {"/link-capability", "/generate-from-capabilities"}


def test_unclassified_command_is_gated_by_default(monkeypatch):
    from app.modules.ai_chat.services.command_parser_service import CommandParserService

    monkeypatch.setitem(
        CommandParserService.COMMANDS, "/future-write", {"description": "x", "args": None}
    )
    service = CommandParserService()

    assert service.is_mutating("/future-write") is True
    assert service.is_mutating("/help") is False


# ---- two organisations: permission and tenant isolation together ----------


def test_viewer_in_org_a_cannot_write_into_org_b(chat, db_session, make_org):
    from app.models.audit_log import AuditLog

    org_a = make_org("gate-a")
    org_b = make_org("gate-b")
    viewer_a = _user(db_session, org_a, "Viewer", "viewer-a")
    cap_b, sol_b = _capability_and_solution(db_session, org_b)
    _link(db_session, sol_b, cap_b)
    b_mappings = _mapping_count(sol_b.id)
    b_elements = _element_count()
    seen = _last_audit_id()

    link = chat(viewer_a, f"/link-capability {cap_b.name} to {sol_b.name}")
    generate = chat(viewer_a, f"/generate-from-capabilities {sol_b.name}")

    for resp in (link, generate):
        assert resp.status_code == 200
        assert "write access" in resp.get_json()["response"]
    assert _mapping_count(sol_b.id) == b_mappings
    assert _element_count() == b_elements
    refusals = _refusals_since(seen, viewer_a)
    assert len(refusals) == 2
    assert {r.organization_id for r in refusals} == {org_a.id}
    assert (
        AuditLog.query.filter(
            AuditLog.id > seen, AuditLog.organization_id == org_b.id
        ).count()
        == 0
    )


def test_architect_in_org_a_cannot_reach_org_b_records(chat, db_session, make_org):
    org_a = make_org("gate-a")
    org_b = make_org("gate-b")
    architect_a = _user(db_session, org_a, "Architect", "architect-a")
    cap_b, sol_b = _capability_and_solution(db_session, org_b)
    _link(db_session, sol_b, cap_b)
    b_mappings = _mapping_count(sol_b.id)
    b_elements = _element_count()

    link = chat(architect_a, f"/link-capability {cap_b.name} to {sol_b.name}")
    generate = chat(architect_a, f"/generate-from-capabilities {sol_b.name}")

    assert "No capability found" in link.get_json()["response"]
    assert "not found" in generate.get_json()["response"]
    assert _mapping_count(sol_b.id) == b_mappings
    assert _element_count() == b_elements


# --- Workbench commands and the automatic decision record --------------------

WORKBENCH_WRITES = [
    ('/create-element ApplicationComponent "ZZ Viewer Probe" Application', "create-element"),
    ("/generate-viewpoint", "generate-viewpoint"),
    ("/generate-sad", "generate-sad"),
    ("generate governance pack", "generate-governance-pack"),
    ("/record-decision Adopt event streaming", "record-decision"),
    ("generate work packages", "generate-work-packages"),
    ("generate roadmap", "generate-roadmap"),
    ("confirm scope", "confirm-artifact"),
]


def _raw_count(table):
    from app import db

    return db.session.execute(db.text(f"SELECT count(*) FROM {table}")).scalar()


def _workspace(user):
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    result = WorkbenchKernel(user_id=user.id).create_workspace("ZZ workspace")
    assert result["success"], result
    return result["workspace_id"]


def _workspace_metadata(workspace_id):
    import copy

    from app import db
    from app.models.solution_architect_models import SolutionAnalysisSession

    db.session.expire_all()
    return copy.deepcopy(
        db.session.get(SolutionAnalysisSession, workspace_id).custom_metadata
    )


def _service(user, org):
    from flask import g

    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    g.current_org_id = org.id
    return MultiDomainChatService(user_id=user.id)


@pytest.mark.parametrize("message,tool", WORKBENCH_WRITES)
def test_viewer_workbench_write_is_refused(app, db_session, make_org, message, tool):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    architect = _user(db_session, org, "Architect", "architect")
    _service(architect, org)
    workspace_id = _workspace(architect)
    before_meta = _workspace_metadata(workspace_id)
    before = (
        _raw_count("archimate_elements"),
        _raw_count("architecture_decisions"),
    )
    seen = _last_audit_id()

    service = _service(viewer, org)
    with app.test_request_context("/"):
        result = service._handle_workbench_command(
            message, {"workspace_id": workspace_id}
        )

    assert result["success"] is False
    assert result["permission_denied"] is True
    assert "write access" in result["response"]
    assert (
        _raw_count("archimate_elements"),
        _raw_count("architecture_decisions"),
    ) == before
    assert _workspace_metadata(workspace_id) == before_meta
    refusals = _refusals_since(seen, viewer)
    assert len(refusals) == 1
    assert refusals[0].new_value["via"] == "workbench_command"
    assert refusals[0].new_value["tool"] == tool


def test_viewer_workbench_readiness_check_is_not_refused(app, db_session, make_org):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    architect = _user(db_session, org, "Architect", "architect")
    _service(architect, org)
    workspace_id = _workspace(architect)
    seen = _last_audit_id()

    service = _service(viewer, org)
    with app.test_request_context("/"):
        result = service._handle_workbench_command(
            "check readiness", {"workspace_id": workspace_id}
        )

    assert result is not None and result.get("permission_denied") is not True
    assert _refusals_since(seen, viewer) == []


def test_architect_create_element_and_record_decision_still_work(
    app, db_session, make_org
):
    org = make_org("gate")
    architect = _user(db_session, org, "Architect", "architect")
    service = _service(architect, org)
    workspace_id = _workspace(architect)
    elements = _raw_count("archimate_elements")
    decisions = _raw_count("architecture_decisions")
    seen = _last_audit_id()

    with app.test_request_context("/"):
        created = service._handle_workbench_command(
            '/create-element ApplicationComponent "ZZ Architect Probe" Application',
            {"workspace_id": workspace_id},
        )
        recorded = service._handle_workbench_command(
            "/record-decision Adopt event streaming", {"workspace_id": workspace_id}
        )

    assert created["success"] is True
    assert recorded["success"] is True
    assert _raw_count("archimate_elements") == elements + 1
    assert _raw_count("architecture_decisions") == decisions + 1
    assert _refusals_since(seen, architect) == []


def test_viewer_cannot_start_a_workbench_workflow(app, db_session, make_org):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    service = _service(viewer, org)
    before = _raw_count("solution_analysis_sessions")
    seen = _last_audit_id()

    with app.test_request_context("/"):
        greenfield = service._start_greenfield_workflow("design a payments platform", {})
        brownfield = service._start_brownfield_workflow("modernise billing", {})

    assert greenfield["permission_denied"] is True
    assert brownfield["permission_denied"] is True
    assert _raw_count("solution_analysis_sessions") == before
    assert len(_refusals_since(seen, viewer)) == 2


def test_viewer_cannot_create_an_automated_action(app, db_session, make_org):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    service = _service(viewer, org)
    seen = _last_audit_id()

    result = service.create_automated_action(
        "bulk_update", {"target_type": "applications"}, viewer.id
    )

    assert result["success"] is False
    assert result["permission_denied"] is True
    refusals = _refusals_since(seen, viewer)
    assert len(refusals) == 1
    assert refusals[0].new_value["via"] == "automated_action"


def test_viewer_question_does_not_create_a_decision(app, db_session, make_org):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    service = _service(viewer, org)
    before = _raw_count("architecture_decisions")
    seen = _last_audit_id()

    with app.test_request_context("/"):
        service._detect_and_handle_decision(
            "How should we host the billing service?",
            "I recommend the managed container platform because it should cut cost.",
            {},
        )

    assert _raw_count("architecture_decisions") == before
    assert _refusals_since(seen, viewer) == []


def test_viewer_explicit_record_decision_in_chat_creates_nothing(
    app, db_session, make_org
):
    org = make_org("gate")
    viewer = _user(db_session, org, "Viewer", "viewer")
    service = _service(viewer, org)
    before = _raw_count("architecture_decisions")

    with app.test_request_context("/"):
        service._detect_and_handle_decision(
            "record this decision: use event streaming", "Noted.", {}
        )

    assert _raw_count("architecture_decisions") == before


def test_architect_question_still_records_a_proposed_decision(
    app, db_session, make_org
):
    org = make_org("gate")
    architect = _user(db_session, org, "Architect", "architect")
    service = _service(architect, org)
    before = _raw_count("architecture_decisions")

    with app.test_request_context("/"):
        service._detect_and_handle_decision(
            "How should we host the billing service?",
            "I recommend the managed container platform.",
            {},
        )

    assert _raw_count("architecture_decisions") == before + 1
