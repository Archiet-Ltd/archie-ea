"""The workbench kernel's lookup of a workspace by id does not cross organisations.

``WorkbenchKernel`` looks a workspace up with ``SolutionAnalysisSession.query.get(workspace_id)`` on an id
the browser supplies. In a request the session is fresh, so the lookup runs a SELECT and the tenant filter
in ``do_orm_execute`` applies: another organisation's workspace answers None. This pins that behaviour for
the status read, the metadata write and the metadata write, so it cannot regress unseen.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"WK {label} {suffix}", slug=f"wk-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _workspace(db_session, org):
    from app.models.solution_architect_models import SolutionAnalysisSession
    from app.models.user import User

    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="WK", last_name=f"User-{suffix}", email=f"wk-{suffix}@example.test",
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    ws = SolutionAnalysisSession(
        name="Org A workspace", created_by_id=user.id, organization_id=org.id,
        custom_metadata={"workspace_type": "greenfield"},
    )
    db_session.add(ws)
    db_session.flush()
    return ws


def test_another_organisations_workspace_is_not_loaded_or_changed(app, db_session, tenant_ctx):
    from app.modules.ai_chat.services.workbench_kernel import WorkbenchKernel

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    ws = _workspace(db_session, org_a)
    ws_id = ws.id
    db_session.expunge_all()  # a request starts with an empty identity map

    kernel = WorkbenchKernel()
    with tenant_ctx(org_b.id):
        assert kernel.load_workspace(ws_id) is None
        assert kernel.update_workspace_metadata(ws_id, {"workflow_state": {"step": "X"}}) is False
    db_session.expunge_all()

    with tenant_ctx(org_a.id):
        loaded = kernel.load_workspace(ws_id)
        assert loaded is not None and loaded["workspace_id"] == ws_id
        from app.models.solution_architect_models import SolutionAnalysisSession

        assert "workflow_state" not in (db_session.get(SolutionAnalysisSession, ws_id).custom_metadata or {})
