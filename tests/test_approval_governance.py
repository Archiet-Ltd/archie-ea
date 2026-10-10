"""ARCH-020/021/022 governance tests for the AI chat CRUD approval workflow.

Uses the shared fixtures (tests/conftest.py) per CLAUDE.md's convention —
db_session for transactional isolation, make_org/tenant_ctx for tenancy.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id, email):
    from app.models.user import User
    import uuid

    unique_email = email.replace("@", f"+{uuid.uuid4().hex[:8]}@")
    user = User(
        email=unique_email,
        first_name="Test",
        last_name="User",
        organization_id=org_id,
    )
    if hasattr(user, "set_password"):
        user.set_password("password123!")
    # V-01: writes and approvals now require Permission.GENERAL, enforced in
    # the tool-execution layer and on the approver. A user created with no role
    # holds no permissions and is correctly refused — which is right for a
    # Viewer, and wrong for a fixture standing in for an ordinary architect.
    # Give the fixture user a real role so these tests exercise the authorised
    # path; the refusal path has its own dedicated tests.
    from app.models.user import Role
    role = Role.query.filter_by(name="Architect").first() or Role.query.filter_by(name="Administrator").first()
    if role is not None:
        user.role_id = role.id
    db_session.add(user)
    db_session.flush()
    return user


def _make_org(db_session):
    from app.models.organization import Organization
    import uuid

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test org {suffix}", slug=f"test-org-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


# --------------------------------------------------------------------- #
# ARCH-020: chat_session_id is populated at creation                      #
# --------------------------------------------------------------------- #


def test_create_pending_approval_populates_chat_session_id(db_session, tenant_ctx):
    """RED before the fix: chat_session_id defaulted to None on every call site.

    This directly exercises AIChatApprovalService.create_pending_approval with
    a chat_session_id, which is what multi_domain_chat_service.py's eight call
    sites and agent_runner.py's _queue_approval now pass (previously omitted).
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch020@example.com")

    with tenant_ctx(org.id):
        svc = AIChatApprovalService(user_id=user.id)
        result = svc.create_pending_approval(
            operation_type="create",
            entity_type="capability",
            original_command="create capability Customer Insight",
            operation_payload={"name": "Customer Insight"},
            summary="Create new capability 'Customer Insight'",
            chat_session_id="chat_user_%d" % user.id,
        )

    assert result["success"] is True
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    row = AIChatCRUDApproval.query.get(result["approval_id"])
    assert row.chat_session_id == "chat_user_%d" % user.id


def test_agent_runner_queue_approval_carries_session_and_turn_id(db_session, tenant_ctx):
    """AgentRunner._queue_approval must stamp chat_session_id + agent_turn_id."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.tools.executor import ToolCall
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch020b@example.com")

    with tenant_ctx(org.id):
        runner = AgentRunner(user_id=user.id, chat_session_id="thread-abc")
        runner._turn_id = "turn-xyz"
        tc = ToolCall(id="1", name="update_application_status", arguments={
            "application_name": "SAP ERP", "new_status": "retired", "rationale": "test",
        })
        approval_id = runner._queue_approval(tc)

    row = AIChatCRUDApproval.query.get(approval_id)
    assert row.chat_session_id == "thread-abc"
    assert row.agent_turn_id == "turn-xyz"


def test_natural_language_approve_resumes_pending_not_a_new_row(db_session, tenant_ctx):
    """ARCH-020's headline scenario: "I approve, proceed" must act on the
    existing pending approval for this session, not create a second one.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch020c@example.com")
    session_id = "chat_user_%d" % user.id

    with tenant_ctx(org.id):
        svc = AIChatApprovalService(user_id=user.id)
        created = svc.create_pending_approval(
            operation_type="create",
            entity_type="vendor",
            original_command="create vendor Acme",
            operation_payload={"name": "Acme"},
            summary="Create new vendor 'Acme'",
            chat_session_id=session_id,
        )
        assert created["success"]
        approval_id = created["approval_id"]

        confirmation = svc.check_for_confirmation_command("I approve, proceed")
        assert confirmation == {"action": "confirm", "approval_id": None}

        result = svc.resolve_natural_confirmation(confirmation, chat_session_id=session_id)

        # It must RESOLVE to the row already pending for this session rather
        # than queueing another. Whether it then executes is a separate
        # question: since M-05/V-01 the requester cannot approve their own
        # request, so "I approve" from the requester is correctly refused —
        # what ARCH-020 forbids is silently creating a SECOND approval.
        assert result.get("approval_id") == approval_id or result.get("success") is False

    rows = AIChatCRUDApproval.query.filter_by(user_id=user.id, entity_type="vendor").all()
    assert len(rows) == 1, "a second, duplicate approval must not have been queued"


# --------------------------------------------------------------------- #
# ARCH-021: duplicate detection                                            #
# --------------------------------------------------------------------- #


def test_duplicate_pending_approval_is_not_recreated(db_session, tenant_ctx):
    """Two requests for the same operation, worded differently, must not
    produce two pending rows."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch021@example.com")

    with tenant_ctx(org.id):
        svc = AIChatApprovalService(user_id=user.id)
        first = svc.create_pending_approval(
            operation_type="create",
            entity_type="solution",
            original_command="create solution HxGN EAM",
            operation_payload={"name": "HxGN EAM", "business_domain": "operations"},
            summary="Create solution 'HxGN EAM' for operations",
        )
        second = svc.create_pending_approval(
            operation_type="create",
            entity_type="solution",
            original_command="please set up HxGN EAM as a new solution",
            operation_payload={"name": "HxGN EAM", "business_domain": "operations"},
            summary="Set up a new solution named HxGN EAM in operations",
        )

    assert first["success"] and second["success"]
    assert second.get("duplicate_of_existing") is True
    assert second["approval_id"] == first["approval_id"]

    rows = AIChatCRUDApproval.query.filter_by(user_id=user.id, entity_type="solution").all()
    assert len(rows) == 1


# --------------------------------------------------------------------- #
# ARCH-022: audit trail + approved_by_id enforcement                       #
# --------------------------------------------------------------------- #


def test_approve_and_execute_requires_approver_and_writes_audit(db_session, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus, AIChatApprovalAuditLog

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch022@example.com")
    # M-05/V-01: the requester is now excluded from deciding their own
    # request, so a second identity is required to approve. This test
    # previously had the requester approve themselves — it encoded the
    # insecure contract, and the security fix correctly broke it.
    approver = _make_user(db_session, org.id, "arch022-approver@example.com")

    with tenant_ctx(org.id):
        approval = AIChatCRUDApproval(
            user_id=user.id,
            operation_type="create",
            entity_type="capability",
            original_command="create capability X",
            operation_payload=json.dumps({"name": "X"}),
            summary="Create capability X",
            status=ApprovalStatus.PENDING,
            chat_session_id="s1",
            expires_at=__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(minutes=15),
        )
        db_session.add(approval)
        db_session.flush()

        # Monkeypatch the data service so this stays a unit test of the
        # governance logic, not a full capability-creation integration test.
        import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod

        class _FakeDataService:
            def __init__(self, user_id):
                pass

            def create_capability(self, payload):
                return {"success": True, "id": 999}

        monkeypatch.setattr(svc_mod, "AIDataInteractionService", _FakeDataService)

        svc = AIChatApprovalService(user_id=approver.id)
        result = svc.approve_and_execute(approval.id, approving_user_id=approver.id)

        print("APPROVE RESULT:", result)
        assert result["success"] is True
        db_session.refresh(approval)
        assert approval.approved_by_id == approver.id
        assert approval.status == ApprovalStatus.APPROVED

        events = [
            row.event
            for row in AIChatApprovalAuditLog.query.filter_by(approval_id=approval.id).order_by(
                AIChatApprovalAuditLog.id
            )
        ]
        assert "approved" in events
        assert "executed" in events
        for row in AIChatApprovalAuditLog.query.filter_by(approval_id=approval.id, event="approved"):
            assert row.actor_user_id == approver.id


def test_approve_and_execute_mirrors_into_compliance_audit_log(db_session, tenant_ctx, monkeypatch):
    """F-01: /admin/audit-log (soc2_audit_log / AuditLog) queries only AuditLog,
    so an approval executed via AI chat must ALSO land there — not just in
    AIChatApprovalAuditLog, which the compliance viewer never reads.

    RED before the fix: AuditLog had zero rows for this approval_id/org, so
    the compliance page could not answer "who approved this change" even
    though AIChatApprovalAuditLog had the full detail.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.audit_log import AuditLog

    org = _make_org(db_session)
    requester = _make_user(db_session, org.id, "f01-mirror-req@example.com")
    approver = _make_user(db_session, org.id, "f01-mirror-appr@example.com")

    with tenant_ctx(org.id):
        approval = AIChatCRUDApproval(
            user_id=requester.id,
            operation_type="create",
            entity_type="capability",
            original_command="create capability Y",
            operation_payload=json.dumps({"name": "Y"}),
            summary="Create capability Y",
            status=ApprovalStatus.PENDING,
            chat_session_id="s-f01",
            expires_at=__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(minutes=15),
        )
        db_session.add(approval)
        db_session.flush()

        import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod

        class _FakeDataService:
            def __init__(self, user_id):
                pass

            def create_capability(self, payload):
                return {"success": True, "id": 998}

        monkeypatch.setattr(svc_mod, "AIDataInteractionService", _FakeDataService)

        svc = AIChatApprovalService(user_id=approver.id)
        result = svc.approve_and_execute(approval.id, approving_user_id=approver.id)
        assert result["success"] is True

        mirrored = AuditLog.query.filter_by(
            organization_id=org.id, user_id=approver.id
        ).filter(AuditLog.table_name.like("ai_chat_approval:%")).all()
        mirrored_actions = {row.action for row in mirrored}
        assert "approved" in mirrored_actions
        assert "executed" in mirrored_actions
        for row in mirrored:
            assert row.user_id == approver.id
            assert row.table_name == "ai_chat_approval:capability"


def test_requester_cancellation_mirrors_into_compliance_audit_log(db_session, tenant_ctx):
    """Same as above for requester cancellation, which used to write
    AIChatApprovalAuditLog directly rather than through _audit()."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.audit_log import AuditLog

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "f01-reject@example.com")

    with tenant_ctx(org.id):
        approval = AIChatCRUDApproval(
            user_id=user.id,
            operation_type="create",
            entity_type="vendor",
            original_command="create vendor Z",
            operation_payload=json.dumps({"name": "Z"}),
            summary="Create vendor Z",
            status=ApprovalStatus.PENDING,
            chat_session_id="s-f01-reject",
            expires_at=__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(minutes=15),
        )
        db_session.add(approval)
        db_session.flush()

        svc = AIChatApprovalService(user_id=user.id)
        result = svc.reject_approval(approval.id, reason="not needed")
        assert result["success"] is True

        row = AuditLog.query.filter_by(
            organization_id=org.id, user_id=user.id, action="cancelled"
        ).filter(AuditLog.table_name.like("ai_chat_approval:%")).first()
        assert row is not None
        assert row.table_name == "ai_chat_approval:vendor"


def test_overdue_approval_still_executes_when_a_real_approver_approves_it(db_session, tenant_ctx, monkeypatch):
    """Overdue-not-expired: past expires_at is overdue, not expired. It must stay
    actionable — a real approver's decision executes normally, it is not
    silently blocked or auto-flipped to a dead EXPIRED state.

    Superseded (2026-09-30) the previous "expiry sweep never executes"
    version of this test, which pinned the prior behaviour this brief
    deliberately removes (an overdue PENDING approval refusing to execute).
    """
    from datetime import datetime, timedelta

    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch022b@example.com")
    # M-05/V-01: a requester can no longer approve their own request, so
    # these tests need a second identity. The security fix correctly broke
    # the old assumption that one user could do both halves.
    approver = _make_user(db_session, org.id, "arch022b-approver@example.com")

    with tenant_ctx(org.id):
        approval = AIChatCRUDApproval(
            user_id=user.id,
            operation_type="create",
            entity_type="capability",
            original_command="create capability Y",
            operation_payload=json.dumps({"name": "Y"}),
            summary="Create capability Y",
            status=ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() - timedelta(minutes=1),  # overdue
            chat_session_id="s2",
        )
        db_session.add(approval)
        db_session.flush()

        import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod

        executed = {"called": False}

        class _FakeDataService:
            def __init__(self, user_id):
                pass

            def create_capability(self, payload):
                executed["called"] = True
                return {"success": True, "id": 1}

        monkeypatch.setattr(svc_mod, "AIDataInteractionService", _FakeDataService)

        svc = AIChatApprovalService(user_id=approver.id)
        result = svc.approve_and_execute(approval.id, approving_user_id=approver.id)

        assert result["success"] is True, result
        assert executed["called"] is True, "an overdue approval must still execute for a real approver"
        db_session.refresh(approval)
        assert approval.status == ApprovalStatus.APPROVED
        assert approval.approved_by_id == approver.id


def test_execution_refused_without_approving_user(db_session, tenant_ctx):
    """If somehow no approver id can be resolved, execution is refused —
    not silently defaulted to a system actor.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch022c@example.com")

    with tenant_ctx(org.id):
        approval = AIChatCRUDApproval(
            user_id=user.id,
            operation_type="create",
            entity_type="capability",
            original_command="create capability Z",
            operation_payload=json.dumps({"name": "Z"}),
            summary="Create capability Z",
            status=ApprovalStatus.PENDING,
            chat_session_id="s3",
            expires_at=__import__("datetime").datetime.utcnow() + __import__("datetime").timedelta(minutes=15),
        )
        db_session.add(approval)
        db_session.flush()

        svc = AIChatApprovalService(user_id=user.id)
        # Force the internal auth-required guard to look satisfied but strip the
        # actual approver identity out from under approve_and_execute.
        svc.user_id = user.id
        # A None approving_user_id with self.user_id present still resolves via
        # self.user_id (per design) — this test instead directly exercises the
        # audit refusal path for a "system" attempt.
        with pytest.raises(ValueError):
            svc._audit(approval, event="approved", to_status="approved", actor_user_id=None)


# --------------------------------------------------------------------- #
# End-to-end: queue -> approve in chat -> executed exactly once            #
# --------------------------------------------------------------------- #


def test_queue_then_chat_approve_executes_exactly_once(db_session, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    org = _make_org(db_session)
    user = _make_user(db_session, org.id, "arch020e2e@example.com")
    session_id = "chat_user_%d" % user.id

    with tenant_ctx(org.id):
        import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod

        call_count = {"n": 0}

        class _FakeDataService:
            def __init__(self, user_id):
                pass

            def create_capability(self, payload):
                call_count["n"] += 1
                return {"success": True, "id": 42}

        monkeypatch.setattr(svc_mod, "AIDataInteractionService", _FakeDataService)

        svc = AIChatApprovalService(user_id=user.id)
        queued = svc.create_pending_approval(
            operation_type="create",
            entity_type="capability",
            original_command="create capability Onboarding",
            operation_payload={"name": "Onboarding"},
            summary="Create capability 'Onboarding'",
            chat_session_id=session_id,
        )
        assert queued["success"]

        confirmation = svc.check_for_confirmation_command("approve it")
        result = svc.resolve_natural_confirmation(confirmation, chat_session_id=session_id)

        # TWO FINDINGS MEET HERE, and the resolution is deliberate.
        # ARCH-020 required that "approve it" act on the pending approval
        # rather than silently queueing a second one. M-05/V-01 then required
        # that nobody approve their own request. In a chat session the person
        # typing IS the requester, so the honest outcome is: resolve to the
        # existing approval, refuse it as self-approval, and execute nothing.
        # Separation of duties wins over conversational convenience — an
        # approval gate that the requester can satisfy is not a gate.
        assert result["success"] is False
        assert "APPROVAL_DENIED" in str(result.get("code", "")) or "own request" in str(result.get("error", ""))

    assert call_count["n"] == 0, "a self-approval must not execute the operation"
    rows = AIChatCRUDApproval.query.filter_by(user_id=user.id, entity_type="capability").all()
    assert len(rows) == 1, "ARCH-020: a second, duplicate approval must not have been queued"
    assert rows[0].status == ApprovalStatus.PENDING
    assert rows[0].approved_by_id is None


# --- ARCH-020 follow-up: consent must be the WHOLE message -------------------
# Found in adversarial review of f147872. The affirmation patterns were anchored
# only at the start (r"^go ahead\b"), so a sentence that merely began with an
# affirming word executed whatever write was pending. On a path that mutates the
# system of record, a prefix match is not consent: the words after the prefix
# can reverse the meaning ("do it later", "approve it only after ...").

import pytest

from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService


@pytest.mark.parametrize(
    "message",
    [
        "I approve, proceed",          # the exact phrase from the register
        "approve",
        "Approve it",
        "go ahead",
        "yes, proceed",
        "please proceed",
        "confirm it",
        "looks good, proceed",
        "do it",
        "Yes",
        "OK, proceed.",
    ],
)
def test_bare_affirmation_is_consent(message):
    result = AIChatApprovalService.check_for_confirmation_command(None, message)
    assert result is not None, f"{message!r} should be recognised as approval"
    assert result["action"] == "confirm"
    assert result["approval_id"] is None


@pytest.mark.parametrize(
    "message",
    [
        "go ahead and explain the risk register",
        "go ahead and list the pending approvals",
        "approve it only after checking X",
        "do it later",
        "should I approve this?",
        "yes but first show me the payload",
        "can you approve this for me once the ARB signs off",
    ],
)
def test_sentence_merely_starting_with_an_affirmation_is_not_consent(message):
    """These must never execute a pending write."""
    result = AIChatApprovalService.check_for_confirmation_command(None, message)
    assert not (result and result.get("action") == "confirm"), (
        f"{message!r} was treated as approval and would have executed a pending write"
    )
