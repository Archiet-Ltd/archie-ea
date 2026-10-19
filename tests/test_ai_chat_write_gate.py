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
    from app.models.solution_models import SolutionCapabilityMapping

    query = SolutionCapabilityMapping.query.filter_by(solution_id=solution_id)
    if capability_id is not None:
        query = query.filter_by(capability_id=capability_id)
    return query.count()


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
