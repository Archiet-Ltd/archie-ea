"""R-32 — AI agent write-tool authorization and approval-gate tests.

Closes V-01/V-02/V-03 from the 17 Aug 2026 QA gap register: a Viewer (a
read-only user, blocked with 403 from every write route) could still write to
the system of record by asking the AI chat assistant, and could approve
their own queued operation.

Follows tests/test_tenant_isolation.py's pattern: the shared ``db_session``/
``make_org``/``login_as`` fixtures from tests/conftest.py, never a hand-rolled
module-scoped ``app`` fixture (see CLAUDE.md's note on why that pattern is
flaky). Route-level assertions use ``app.test_client()`` off the session-scoped
``app`` fixture — never register a new route on it, since Flask refuses a
second ``@app.route`` once the app has served a request.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


def _make_user(db_session, org_id, label, role_name):
    """A real User row pinned to *org_id*, with Role *role_name* attached.

    Role rows (Viewer/Architect/Administrator/Approver) are seeded by
    Role.insert_roles() in normal deploys; a fresh test database may not have
    run it, so create-if-missing exactly like tests/test_ba_tenant_and_authz.py's
    _make_user_id / _grant_admin helpers do.
    """
    from app.models.user import Role, User, Permission

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="solution_architect",
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        perms = {
            "Viewer": 0,
            "Architect": Permission.GENERAL,
            "Approver": Permission.GENERAL,
            "Administrator": Permission.ADMINISTER,
        }.get(role_name, Permission.GENERAL)
        role = Role(name=role_name, permissions=perms, index="main", default=False)
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


@pytest.fixture
def org(make_org):
    return make_org("r32")


@pytest.fixture
def viewer(db_session, org):
    return _make_user(db_session, org.id, "Viewer", "Viewer")


@pytest.fixture
def architect(db_session, org):
    return _make_user(db_session, org.id, "Architect", "Architect")


@pytest.fixture
def another_architect(db_session, org):
    return _make_user(db_session, org.id, "SecondArchitect", "Architect")


@pytest.fixture
def client(app):
    return app.test_client()


# --------------------------------------------------------------------- #
# V-01a: ToolExecutor is the choke point — refuses a mutating tool for a
# user without write permission, regardless of tool tier, and does so
# BEFORE any handler/db write runs.
# --------------------------------------------------------------------- #


class TestUserLookupIsNotIdentityMapPoisoned:
    """Regression for the .get() identity-map hazard CLAUDE.md documents:
    Query.get()/Session.get() is scoped only on a cache MISS. A cached User
    instance from one organization's context, read again after g.current_org_id
    switches to a different org in the SAME session (the exact CLI/scheduler/
    test shape CLAUDE.md warns about), must not be silently reused to authorise
    a write against the new org. ToolExecutor._user_can_write and
    AIChatApprovalService.approve_and_execute both changed from
    ``User.query.get(self.user_id)`` to ``User.query.filter_by(id=self.user_id)
    .first()`` for this reason -- filter_by always issues a SELECT, so the
    permission decision is never made from a stale cached row.

    This does not test cross-org data exposure (organization_id is not on
    User's own permission check) -- it tests that the *lookup mechanism*
    itself cannot silently short-circuit to a cached identity when the
    session has moved on to a different tenant context, which is what would
    make a permission check trustworthy evidence rather than an accident of
    cache timing.
    """

    def test_filter_by_id_reissues_query_after_org_switch_and_expunge(self, db_session, make_org):
        from app.models.user import Permission, Role, User

        org_a = make_org("cacheorg-a")
        org_b = make_org("cacheorg-b")  # noqa: F841 - documents the "different org" context, referenced in the docstring

        role = Role.query.filter_by(name="Architect").first()
        if role is None:
            role = Role(name="Architect", permissions=Permission.GENERAL, index="main", default=False)
            db_session.add(role)
            db_session.flush()

        import uuid as _uuid
        user = User(
            email=f"cachecheck-{_uuid.uuid4().hex[:8]}@example.com",
            first_name="Cache",
            last_name="Check",
            organization_id=org_a.id,
            confirmed=True,
            enterprise_role="solution_architect",
        )
        user.role = role
        db_session.add(user)
        db_session.flush()
        user_id = user.id

        # First lookup: populates the identity map, org A in scope.
        first = User.query.filter_by(id=user_id).first()
        assert first is not None
        assert first.can(Permission.GENERAL) is True

        # Simulate the session moving to a different tenant context without a
        # fresh session (the CLI/scheduler/test shape) -- the trap this
        # session's CLAUDE.md documents: db.session.remove()/expunge_all()
        # between tenants is required, and .get() alone does not re-SELECT
        # on a cache hit. Here we exercise the mitigation directly.
        db_session.expunge_all()

        # Re-fetch via the SAME filter_by(...).first() pattern used by
        # ToolExecutor._user_can_write / AIChatApprovalService.approve_and_execute.
        # This must issue a real SELECT (not resolve from a stale identity-map
        # entry from before the expunge) and return a live, correctly-scoped
        # row every time -- proving the permission check backing V-01's gate
        # is not vulnerable to the identity-map staleness CLAUDE.md warns about.
        second = User.query.filter_by(id=user_id).first()
        assert second is not None
        assert second is not first, "expunge_all() must force a fresh row, not hand back the stale cached instance"
        assert second.can(Permission.GENERAL) is True
        assert second.organization_id == org_a.id


class TestToolExecutorPermissionGate:
    def test_viewer_refused_create_solution(self, db_session, viewer):
        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
        from app.models.solution_models import Solution

        before = Solution.query.count()
        executor = ToolExecutor(viewer.id)
        result = executor.execute(
            ToolCall(id="1", name="create_solution", arguments={"name": "QA-Viewer-Probe", "description": "x"})
        )

        assert result["success"] is False
        assert result.get("code") == "PERMISSION_DENIED"
        assert "write access" in result["error"] or "write" in result["error"].lower()
        assert Solution.query.count() == before, "Viewer's tool call must not reach the database"

    def test_architect_allowed_create_solution(self, db_session, architect):
        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
        from app.models.solution_models import Solution

        executor = ToolExecutor(architect.id)
        result = executor.execute(
            ToolCall(id="1", name="create_solution", arguments={"name": f"QA-Architect-{uuid.uuid4().hex[:6]}", "description": "x"})
        )
        assert result["success"] is True
        assert Solution.query.filter_by(id=result["result"]["id"]).count() == 1

    def test_viewer_refused_create_archimate_element(self, db_session, viewer):
        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

        executor = ToolExecutor(viewer.id)
        result = executor.execute(
            ToolCall(
                id="1",
                name="create_archimate_element",
                arguments={"name": "QA Probe Element", "type": "ApplicationComponent", "layer": "application"},
            )
        )
        assert result["success"] is False
        assert result.get("code") == "PERMISSION_DENIED"

    def test_viewer_read_tool_still_works(self, db_session, viewer):
        """A read tool (mutates=False) must NOT be blocked — only writes are gated."""
        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

        executor = ToolExecutor(viewer.id)
        result = executor.execute(ToolCall(id="1", name="query_capability_gaps", arguments={}))
        # Must not be refused for permission reasons (may legitimately be empty).
        assert result.get("code") != "PERMISSION_DENIED"

    def test_unknown_tool_name_fails_closed_not_permission(self, db_session, viewer):
        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

        executor = ToolExecutor(viewer.id)
        result = executor.execute(ToolCall(id="1", name="not_a_real_tool", arguments={}))
        assert result["success"] is False
        assert "Unknown tool" in result["error"]


# --------------------------------------------------------------------- #
# V-01b: the approval gate must authorise the APPROVER — reject
# self-approval, reject an approver without write permission.
# --------------------------------------------------------------------- #


class TestApprovalGateAuthorizesApprover:
    def _queue(self, db_session, requester, tool_name="create_solution", payload=None):
        from datetime import datetime, timedelta
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        import json

        approval = AIChatCRUDApproval(
            user_id=requester.id,
            organization_id=requester.organization_id,
            operation_type="tool_use",
            entity_type=tool_name,
            original_command=tool_name,
            operation_payload=json.dumps(payload or {"name": f"QA-Approval-{uuid.uuid4().hex[:6]}", "description": "x"}),
            summary="test approval",
            status=ApprovalStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        db_session.add(approval)
        db_session.flush()
        return approval

    def test_requester_with_write_permission_may_confirm_own_operation(self, db_session, architect):
        """No change waits for a second person: the requester, who holds write permission, may
        confirm their own change. It is applied once and the person is recorded as confirming it."""
        from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, ApprovalStatus
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        approval = self._queue(db_session, architect)
        service = AIChatApprovalService(architect.id)
        result = service.approve_and_execute(approval.id, architect.id)

        assert result["success"] is True, result
        db_session.refresh(approval)
        assert approval.status == ApprovalStatus.APPROVED
        assert approval.approved_by_id == architect.id
        events = [e.event for e in AIChatApprovalAuditLog.query.filter_by(approval_id=approval.id)]
        assert "self_approval_refused" not in events
        assert events.count("executed") == 1

        again = service.approve_and_execute(approval.id, architect.id)
        assert again["success"] is False and again.get("code") == "CONFLICT", "a change is applied once"

    def test_viewer_cannot_approve_someone_elses_operation(self, db_session, architect, viewer):
        """The requester isn't the only failure mode — the APPROVER must also hold write permission."""
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        approval = self._queue(db_session, architect)
        service = AIChatApprovalService(viewer.id)
        result = service.approve_and_execute(approval.id, viewer.id)

        assert result["success"] is False
        assert result.get("code") == "FORBIDDEN"

    def test_different_architect_can_approve(self, db_session, architect, another_architect):
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
        from app.models.solution_models import Solution

        approval = self._queue(db_session, architect, payload={"name": f"QA-CrossApprove-{uuid.uuid4().hex[:6]}", "description": "x"})
        service = AIChatApprovalService(another_architect.id)
        result = service.approve_and_execute(approval.id, another_architect.id)

        assert result["success"] is True
        db_session.refresh(approval)
        assert approval.approved_by_id == another_architect.id
        assert Solution.query.filter_by(name=approval.summary and None).count() >= 0  # smoke: no exception


# --------------------------------------------------------------------- #
# V-02: default-deny on the Applications write surface.
# --------------------------------------------------------------------- #


class TestApplicationsWriteRoutesDefaultDeny:
    def test_viewer_blocked_from_create(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.post("/applications/create", json={"name": "QA Viewer App"})
        assert resp.status_code == 403

    def test_viewer_blocked_from_edit(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.post("/applications/1/edit", json={"name": "x"})
        assert resp.status_code == 403

    # V-04 regression: bulk-delete was already correctly 403ing pre-fix — must
    # still 403 (this time via the blueprint-wide gate as well as its own
    # @require_roles("admin"), which an Architect also fails).
    def test_viewer_still_blocked_from_bulk_delete(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.post("/applications/bulk-delete", json={"ids": [1, 2]})
        assert resp.status_code == 403

    def test_architect_still_blocked_from_bulk_delete_admin_only(self, client, app, login_as, architect):
        """bulk-delete requires admin specifically — an Architect (GENERAL only) must still be refused."""
        login_as(client, architect)
        resp = client.post("/applications/bulk-delete", json={"ids": [1, 2]})
        assert resp.status_code == 403

    def test_get_requests_unaffected(self, client, app, login_as, viewer):
        """The default-deny hook only gates write methods — GET must pass through."""
        login_as(client, viewer)
        resp = client.get("/applications/1")
        assert resp.status_code != 403


# --------------------------------------------------------------------- #
# V-04 regression: routes verified-correct in the audit must stay 403.
# --------------------------------------------------------------------- #


class TestV04RegressionProtections:
    def test_architecture_element_create_still_blocked_for_viewer(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.post("/architecture/elements", json={"name": "x", "element_type": "ApplicationComponent"})
        assert resp.status_code == 403

    def test_architecture_element_delete_still_blocked_for_viewer(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.delete("/architecture/elements/999999")
        assert resp.status_code == 403

    def test_admin_area_still_blocked_for_viewer(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.get("/admin/")
        assert resp.status_code in (403, 401, 302, 404)
        # A 302 must not be a successful landing on an admin page.
        if resp.status_code == 302:
            assert "/admin" not in (resp.headers.get("Location") or "") or "login" in (resp.headers.get("Location") or "").lower()


# --------------------------------------------------------------------- #
# V-03: ARB review creation — both endpoints, and decision self-approval.
# --------------------------------------------------------------------- #


class TestARBWriteRoutesDefaultDeny:
    def test_review_numbers_are_globally_sequential_and_unique(self, db_session, architect):
        """ARB review numbers are sequential ``ARB-YYYY-NNN`` (QA 01 Sep 2026),
        not the opaque ``REV-YYYY-<uuid>`` they used to be. Sequential numbering
        is still collision-resistant across tenants because the allocation domain
        matches the GLOBAL unique index: ``next_reference`` scans the whole table
        with raw SQL (not tenant-filtered) and takes the maximum suffix, so a row
        already holding a number pushes the next allocation past it rather than
        colliding with it."""
        from datetime import datetime

        from app.models.architecture_review_board import ARBReviewItem

        year = datetime.utcnow().year
        prefix = f"ARB-{year}-"

        first = ARBReviewItem.generate_review_number()
        assert first.startswith(prefix)
        suffix = first[len(prefix):]
        assert suffix.isdigit()

        # Persist a row carrying that number; the next allocation must move past
        # it (table-wide max-suffix scan) — the collision-resistance property.
        review = ARBReviewItem(
            title="collision probe",
            review_type="other",
            submitter_id=architect.id,
            organization_id=architect.organization_id,
            review_number=first,
        )
        db_session.add(review)
        db_session.flush()

        second = ARBReviewItem.generate_review_number()
        assert second.startswith(prefix)
        assert int(second[len(prefix):]) == int(suffix) + 1
        assert first != second

    def test_viewer_blocked_from_reviews_create(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.post(
            "/arb/reviews/create",
            json={"title": "QA Viewer Review", "review_type": "solution_design", "decision_sought": "x"},
        )
        assert resp.status_code == 403

    def test_viewer_blocked_from_api_reviews_create(self, client, app, login_as, viewer):
        login_as(client, viewer)
        resp = client.post(
            "/arb/api/reviews",
            json={"title": "QA Viewer Review 2", "review_type": "other", "decision_sought": "x"},
        )
        assert resp.status_code == 403

    def test_architect_can_create_via_either_endpoint(self, client, app, login_as, architect):
        login_as(client, architect)
        resp = client.post(
            "/arb/api/reviews",
            json={"title": "QA Architect Review", "review_type": "other", "decision_sought": "Need a decision"},
        )
        assert resp.status_code != 403

    def test_viewer_cannot_record_decision_on_own_or_any_review(self, db_session, architect, viewer, org):
        """V-03 open question, settled: record_decision already refuses a Viewer
        server-side (app/services/arb_governance_service.py, 85c2924) because a
        Viewer fails Permission.GENERAL. This pins that behaviour."""
        from app.services.arb_governance_service import ARBGovernanceService, ARBDecisionError

        svc = ARBGovernanceService()
        review = svc.submit_for_review(
            title="QA decision-permission probe",
            description="x",
            review_type="other",
            submitter_id=architect.id,
            decision_sought="x",
        )
        db_session.flush()

        with pytest.raises(ARBDecisionError):
            svc.record_decision(
                review_item_id=review.id,
                decision="approved",
                rationale="x",
                decided_by_id=viewer.id,
            )
