"""The chat workflow HTTP routes: write permission AND workspace ownership.

Reproduces the cross-user scenario with real distinct users on a real database:
a Viewer posting a step confirm with another user's workspace id, a same-organisation
non-owner, and an other-organisation architect. The workflow engines themselves are
stubbed (they call a model); the gate, the ownership lookup and the persisted
workspace metadata are real.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

CONFIRM = "/ai-chat/workflow/greenfield/step/BRIEF/confirm"
SKIP = "/ai-chat/workflow/greenfield/step/BRIEF/skip"
B_CONFIRM = "/ai-chat/workflow/brownfield/step/CONTEXT_LOAD/confirm"
B_SKIP = "/ai-chat/workflow/brownfield/step/CONTEXT_LOAD/skip"
G_START = "/ai-chat/workflow/greenfield/start"
B_START = "/ai-chat/workflow/brownfield/start"
STEP_ROUTES = [CONFIRM, SKIP, B_CONFIRM, B_SKIP]


def _user(db_session, org, label, role_name="Architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"cwf-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    return user


def _workspace(db_session, org, owner):
    from app.models.solution_architect_models import (
        SolutionAnalysisSession,
        SolutionSessionStatus,
    )

    ws = SolutionAnalysisSession(
        name="Workspace",
        status=SolutionSessionStatus.IN_PROGRESS,
        created_by_id=owner.id,
        organization_id=org.id,
        custom_metadata={"workflow_state": {"step": "BRIEF", "accumulated": {}}},
    )
    db_session.add(ws)
    db_session.flush()
    return ws


def _state(db_session, ws):
    """Read the persisted step with raw SQL so the reading user's organisation
    (set by the last request) cannot hide the row."""
    from sqlalchemy import text

    meta = db_session.execute(
        text("SELECT custom_metadata FROM solution_analysis_sessions WHERE id = :i"), {"i": ws.id}
    ).scalar_one()
    return (meta or {}).get("workflow_state", {}).get("step")


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("cwf-a"), make_org("cwf-b")
    owner = _user(db_session, a, "owner")
    return {
        "a": a, "b": b, "owner": owner,
        "viewer": _user(db_session, a, "viewer", role_name="Viewer"),
        "peer": _user(db_session, a, "peer"),
        "outsider": _user(db_session, b, "outsider"),
        "ws": _workspace(db_session, a, owner),
    }


@pytest.fixture(autouse=True)
def stub_engines(monkeypatch):
    from app.utils import csrf_helper

    # The gate is under test, not CSRF; the codegen routes use require_csrf.
    monkeypatch.setattr(csrf_helper, "validate_csrf", lambda *_a, **_k: None)
    from app.modules.ai_chat.services import workbench_kernel as wk

    def advance(self, action, state, requested_model=None):
        return {"success": True, "response": "advanced",
                "workflow_state": {**state, "step": "ADVANCED"}}

    def start(self, *_a, **_k):
        ws = self.kernel.create_workspace("Started", solution_id=None)
        return {"success": True, "workspace_id": ws["workspace_id"], "response": "ok",
                "workflow_state": {"step": "BRIEF", "accumulated": {}}}

    for cls in (wk.GreenfieldWorkflow, wk.BrownfieldWorkflow):
        monkeypatch.setattr(cls, "advance", advance)
        monkeypatch.setattr(cls, "start", start)


def _post(client, login_as, user, url, **body):
    login_as(client, user)
    return client.post(url, json=body, headers={"X-CSRFToken": "test"})


@pytest.mark.parametrize("url", STEP_ROUTES)
def test_viewer_cannot_advance_another_users_workspace(client, login_as, db_session, world, url):
    """The F1 scenario: a Viewer posts the owner's workspace id."""
    resp = _post(client, login_as, world["viewer"], url, workspace_id=world["ws"].id)
    assert resp.status_code == 403
    assert _state(db_session, world["ws"]) == "BRIEF"


@pytest.mark.parametrize("url", STEP_ROUTES)
def test_same_org_non_owner_is_refused_as_not_found(client, login_as, db_session, world, url):
    resp = _post(client, login_as, world["peer"], url, workspace_id=world["ws"].id)
    assert resp.status_code == 404
    assert _state(db_session, world["ws"]) == "BRIEF"


@pytest.mark.parametrize("url", STEP_ROUTES)
def test_other_org_user_is_refused_as_not_found(client, login_as, db_session, world, url):
    resp = _post(client, login_as, world["outsider"], url, workspace_id=world["ws"].id)
    assert resp.status_code == 404, resp.get_data(as_text=True)[:300]
    assert _state(db_session, world["ws"]) == "BRIEF"


@pytest.mark.parametrize("url", STEP_ROUTES)
def test_owner_advances_their_own_workspace(client, login_as, db_session, world, url):
    resp = _post(client, login_as, world["owner"], url, workspace_id=world["ws"].id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["step"] == "ADVANCED"
    assert _state(db_session, world["ws"]) == "ADVANCED"


@pytest.mark.parametrize("url,field", [(G_START, "brief"), (B_START, "target_domain")])
def test_viewer_cannot_start_a_workflow(client, login_as, db_session, world, url, field):
    from app.models.solution_architect_models import SolutionAnalysisSession

    before = db_session.query(
        SolutionAnalysisSession).filter(
        SolutionAnalysisSession.organization_id == world["viewer"].organization_id,
        SolutionAnalysisSession.created_by_id == world["viewer"].id).count()
    resp = _post(client, login_as, world["viewer"], url, **{field: "anything"})
    assert resp.status_code == 403
    assert db_session.query(
        SolutionAnalysisSession).filter(
        SolutionAnalysisSession.organization_id == world["viewer"].organization_id,
        SolutionAnalysisSession.created_by_id == world["viewer"].id).count() == before


@pytest.mark.parametrize("url,field", [(G_START, "brief"), (B_START, "target_domain")])
def test_start_creates_a_workspace_owned_by_the_caller(client, login_as, db_session, world, url, field):
    from app.models.solution_architect_models import SolutionAnalysisSession

    resp = _post(client, login_as, world["owner"], url, **{field: "anything"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    created = db_session.get(SolutionAnalysisSession, resp.get_json()["workspace_id"])
    assert created.created_by_id == world["owner"].id
    # and the creator can immediately continue it
    follow = _post(client, login_as, world["owner"], CONFIRM, workspace_id=created.id)
    assert follow.status_code == 200


@pytest.mark.parametrize("url,field", [(G_START, "brief"), (B_START, "target_domain")])
def test_start_refuses_a_solution_from_another_organisation(
        client, login_as, db_session, world, url, field):
    from app.models.solution_models import Solution

    foreign = Solution(name="Foreign", organization_id=world["b"].id)
    db_session.add(foreign)
    db_session.flush()
    resp = _post(client, login_as, world["owner"], url, solution_id=foreign.id, **{field: "x"})
    assert resp.status_code == 404


# -- codegen debug routes (same shape: access check without a write check) ----

DEBUG_ROUTES = ["debug-session", "debug-analyze", "debug-apply", "debug-continue"]


@pytest.mark.parametrize("route", DEBUG_ROUTES)
def test_codegen_debug_routes_refuse_a_viewer_with_access(client, login_as, db_session, world, route):
    """A Viewer who passes the access check (here: creator of the solution) is still
    refused, because every debug route changes generated code."""
    from app.models.solution_models import Solution

    solution = Solution(name="Viewer-owned", organization_id=world["a"].id,
                        created_by_id=world["viewer"].id)
    db_session.add(solution)
    db_session.flush()
    resp = _post(client, login_as, world["viewer"],
                 f"/solutions/{solution.id}/codegen/{route}", error_log="boom", workspace_id=1)
    assert resp.status_code == 403, resp.get_data(as_text=True)[:300]


@pytest.mark.parametrize("route", DEBUG_ROUTES)
def test_codegen_debug_routes_admit_a_writer(client, login_as, db_session, world, route):
    from app.models.solution_models import Solution

    solution = Solution(name="Owner-owned", organization_id=world["a"].id,
                        created_by_id=world["owner"].id)
    db_session.add(solution)
    db_session.flush()
    resp = _post(client, login_as, world["owner"], f"/solutions/{solution.id}/codegen/{route}")
    assert resp.status_code not in (403, 404)
