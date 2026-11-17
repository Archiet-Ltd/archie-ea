from __future__ import annotations

import inspect
import uuid

import pytest
from flask import g
from flask_login import login_user

from app.models.architecture_review_board import ARBReviewItem
from app.models.solution_architect_models import SolutionAnalysisSession
from app.models.solution_models import Solution
from app.models.user import User
from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel
from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
from app.modules.transformation_room.arb_submission_adapter import (
    LegacyARBSubmissionResult,
)


def _actor(session, org):
    actor = User(
        email=f"typed-ingress-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Ingress",
        last_name="Architect",
        organization_id=org.id,
        enterprise_role="platform_admin",
        is_org_admin=True,
        is_platform_admin=True,
        confirmed=True,
    )
    session.add(actor)
    session.flush()
    return actor


def _solution(session, org, actor):
    solution = Solution(
        name=f"Typed ingress {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        created_by_id=actor.id,
        governance_status="draft",
    )
    session.add(solution)
    session.flush()
    return solution


def _workspace(session, org, actor, solution):
    workspace = SolutionAnalysisSession(
        name=f"Typed workspace {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        created_by_id=actor.id,
        custom_metadata={"solution_id": solution.id, "workspace_type": "greenfield"},
    )
    session.add(workspace)
    session.flush()
    return workspace


def _success(*, idempotent=False):
    return LegacyARBSubmissionResult(
        True,
        review_item_id=71,
        review_number="REV-2026-INGRESS",
        snapshot_id=72,
        idempotent=idempotent,
        review_cycle_id=73,
        canonical_url="/solutions/19?tab=governance",
        http_status=200 if idempotent else 201,
        cycle_number=2,
        subject_type="solution",
        subject_id=19,
        status="submitted",
    )


def _json(response):
    if isinstance(response, tuple):
        response = response[0]
    return response.get_json()


@pytest.mark.parametrize(
    ("module_name", "function_name", "expected_status", "review_aliases", "wrapped"),
    [
        (
            "app.modules.solutions_strategic.v2.routes.solution_design_routes",
            "submit_solution_for_arb",
            200,
            ("review_item_id",),
            False,
        ),
        (
            "app.modules.solutions_strategic.v2.routes.governance_api_routes",
            "submit_for_arb",
            201,
            ("review_item_id",),
            False,
        ),
        (
            "app.modules.solutions_strategic.v2.routes.journey_v2_routes",
            "submit_arb",
            200,
            ("review_item_id",),
            True,
        ),
        (
            "app.modules.architecture.routes.arb_routes",
            "api_submit_solution_review",
            200,
            ("review_id", "review_item_id"),
            False,
        ),
    ],
)
def test_http_solution_ingresses_use_typed_adapter_and_preserve_success_contract(
    app,
    db_session,
    make_org,
    monkeypatch,
    module_name,
    function_name,
    expected_status,
    review_aliases,
    wrapped,
):
    org = make_org(f"typed-{function_name}")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return _success()

    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_from_request",
        submit,
    )
    module = __import__(module_name, fromlist=[function_name])
    payload = {
        "human_reviewed": True,
        "actor_id": actor.id + 100,
        "decided_by_id": actor.id + 100,
        "validation_result": {"passed": True},
        "readiness": True,
        "solution_id": solution.id + 100,
    }
    with app.test_request_context("/", method="POST", json=payload):
        g.current_org_id = org.id
        login_user(actor)
        response = inspect.unwrap(getattr(module, function_name))(solution.id)

    status = response[1] if isinstance(response, tuple) else response.status_code
    body = _json(response)
    data = body["data"] if wrapped else body
    assert calls == [{"solution_id": solution.id, "payload": payload}]
    assert status == expected_status
    assert body["success"] is True
    for alias in review_aliases:
        assert data[alias] == 71
    assert data["review_number"] == "REV-2026-INGRESS"
    assert data["snapshot_id"] == 72
    assert data["review_cycle_id"] == 73
    assert data["canonical_url"] == "/solutions/19?tab=governance"
    assert data["idempotent"] is False
    if function_name == "submit_for_arb":
        assert data["evidence_id"] == 72
        assert data["cycle_number"] == 2
        assert data["subject_type"] == "solution"
        assert data["subject_id"] == 19
        assert data["status"] == "submitted"


def test_v2_governance_replay_keeps_200_status(app, db_session, make_org, monkeypatch):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    org = make_org("typed-v2-replay")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_from_request",
        lambda **_kwargs: _success(idempotent=True),
    )
    with app.test_request_context("/", method="POST", json={}):
        g.current_org_id = org.id
        login_user(actor)
        response = inspect.unwrap(governance_api_routes.submit_for_arb)(solution.id)

    assert response[1] == 200
    assert response[0].get_json()["idempotent"] is True


def test_http_solution_ingress_uses_adapter_error_status_and_safe_envelope(
    app, db_session, make_org, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import solution_design_routes

    org = make_org("typed-http-blocked")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_from_request",
        lambda **_kwargs: LegacyARBSubmissionResult(
            False,
            ["arb_readiness_stale"],
            [{"code": "evidence_changed"}],
            http_status=409,
        ),
    )
    with app.test_request_context("/", method="POST", json={"ready": True}):
        g.current_org_id = org.id
        login_user(actor)
        response = inspect.unwrap(solution_design_routes.submit_solution_for_arb)(
            solution.id
        )

    assert response[1] == 409
    assert response[0].get_json() == {
        "success": False,
        "reason_codes": ["arb_readiness_stale"],
        "missing_evidence": [{"code": "evidence_changed"}],
    }


def test_chat_command_preserves_link_aliases_and_ignores_forged_assertions(
    app, db_session, make_org, monkeypatch
):
    org = make_org("typed-chat-command")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    workspace = _workspace(db_session, org, actor, solution)
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return _success(idempotent=True)

    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_for_actor",
        submit,
    )
    service = object.__new__(MultiDomainChatService)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        login_user(actor)
        result = service._handle_arb_submission(
            f"/submit-arb {solution.id}",
            {
                "_trusted_workspace_id": workspace.id,
                "arb_assertions": {
                    "human_reviewed": True,
                    "readiness": True,
                    "decided_by_id": actor.id + 1,
                },
            },
        )

    assert calls == [
        {
            "actor_id": actor.id,
            "solution_id": solution.id,
            "trusted_workspace_id": workspace.id,
            "trusted_human_reviewed": False,
        }
    ]
    assert result["arb_id"] == 71
    assert result["snapshot_id"] == 72
    assert result["review_cycle_id"] == 73
    assert result["canonical_url"] == "/solutions/19?tab=governance"
    assert result["review_url"] == "/arb/reviews/71"
    assert result["already_submitted"] is True
    assert result["idempotent"] is True


def test_approved_tool_uses_tool_call_as_server_command_key_and_returns_typed_ids(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org = make_org("typed-approved-tool")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    workspace = _workspace(db_session, org, actor, solution)
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return _success()

    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_for_actor",
        submit,
    )
    with tenant_ctx(org.id):
        executor = ToolExecutor(actor.id)
        monkeypatch.setattr(executor, "_user_can_write", lambda _tool_name=None: True)
        executor._resolver.resolve_solution = lambda _name: {
            "resolved": True,
            "id": solution.id,
            "name": solution.name,
        }
        result = executor.execute(
            ToolCall(
                "approved-tool-call-19",
                "submit_for_arb_review",
                {"solution_name": solution.name, "workspace_id": workspace.id},
            )
        )

    assert calls == [
        {
            "actor_id": actor.id,
            "solution_id": solution.id,
            "trusted_workspace_id": workspace.id,
            "trusted_human_reviewed": True,
            "command_key": (
                "ai-tool-1a2c4ec067035c0532ad72f150600b91b95be29785b00147db61ad26b991080a"
            ),
        }
    ]
    assert result["result"]["review_item_id"] == 71
    assert result["result"]["review_cycle_id"] == 73
    assert result["result"]["canonical_url"] == "/solutions/19?tab=governance"


def test_workbench_records_typed_submission_ids_from_committed_result(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org = make_org("typed-workbench")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    workspace = _workspace(db_session, org, actor, solution)
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return _success()

    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_for_actor",
        submit,
    )
    kernel = WorkbenchKernel(user_id=actor.id)
    recorded = []
    monkeypatch.setattr(
        kernel,
        "set_artifact_state",
        lambda workspace_id, name, state, data=None: recorded.append(
            (workspace_id, name, state, data)
        )
        or True,
    )
    with tenant_ctx(org.id):
        result = kernel.submit_to_arb(workspace.id)

    assert calls == [
        {
            "actor_id": actor.id,
            "solution_id": solution.id,
            "trusted_workspace_id": workspace.id,
            "trusted_human_reviewed": False,
        }
    ]
    assert result["review_cycle_id"] == 73
    assert result["canonical_url"] == "/solutions/19?tab=governance"
    assert all(item[3]["review_cycle_id"] == 73 for item in recorded)


def test_solution_linked_escalation_uses_typed_submission_and_preserves_id_alias(
    app, db_session, make_org, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import programme_routes

    org = make_org("typed-escalation")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return _success()

    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_from_request",
        submit,
    )
    payload = {
        "solution_id": solution.id,
        "title": "Forged decision title",
        "severity": "critical",
        "category": "governance",
        "human_reviewed": True,
        "decided_by_id": actor.id + 1,
    }
    with app.test_request_context("/", method="POST", json=payload):
        g.current_org_id = org.id
        login_user(actor)
        response = inspect.unwrap(programme_routes.arb_escalate_finding)()

    assert calls == [{"solution_id": solution.id, "payload": payload}]
    assert response[1] == 201
    assert response[0].get_json() == {
        "success": True,
        "review_number": "REV-2026-INGRESS",
        "id": 71,
        "review_cycle_id": 73,
        "snapshot_id": 72,
        "canonical_url": "/solutions/19?tab=governance",
        "idempotent": False,
    }


def test_legacy_solution_facade_delegates_without_direct_review_write(
    app, db_session, make_org, monkeypatch
):
    from app.modules.solutions_strategic.v2.services.arb_submission_service import (
        ARBSubmissionService,
    )

    org = make_org("typed-legacy-facade")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    calls = []

    def submit(**kwargs):
        calls.append(kwargs)
        return _success()

    monkeypatch.setattr(
        "app.modules.transformation_room.arb_submission_adapter."
        "TypedARBSubmissionAdapter.submit_solution_for_actor",
        submit,
    )
    with app.test_request_context("/"):
        g.current_org_id = org.id
        login_user(actor)
        result = ARBSubmissionService.submit(
            solution.id,
            actor.id,
            workspace_id=None,
            assertions={
                "human_reviewed": True,
                "readiness": True,
                "cost_source": "manual_override",
            },
        )

    assert calls == [
        {
            "actor_id": actor.id,
            "solution_id": solution.id,
            "trusted_workspace_id": None,
            "trusted_human_reviewed": True,
        }
    ]
    assert result.review_cycle_id == 73
    assert db_session.query(ARBReviewItem).filter_by(solution_id=solution.id).count() == 0


def test_escalation_service_cannot_direct_write_a_solution_review(
    db_session, make_org, tenant_ctx
):
    from app.modules.solutions_strategic.v2.services.arb_escalation_service import (
        ARBEscalationService,
    )

    org = make_org("typed-escalation-guard")
    actor = _actor(db_session, org)
    solution = _solution(db_session, org, actor)
    with tenant_ctx(org.id):
        result = ARBEscalationService.escalate(
            title="Finding",
            detail="Must not bypass typed evidence",
            category="governance",
            severity="high",
            user_id=actor.id,
            solution_id=solution.id,
        )

    assert result == {
        "success": False,
        "error": "Solution findings require the canonical evidence-gated submission endpoint.",
    }
    assert db_session.query(ARBReviewItem).filter_by(solution_id=solution.id).count() == 0
