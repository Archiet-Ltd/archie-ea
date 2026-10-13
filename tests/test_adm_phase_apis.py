"""Regression tests for the six ADM phase APIs that 500ed on a shared ORM bug.

Design-review P0 wave, Task 2. Two root causes, both pinned here:

1. ``ArchiMateElement.plateau`` is NOT the plateau column in normal runtime —
   ``Plateau.archimate_element``'s ``backref="plateau"`` shadows it, so the
   column's Python attribute is ``togaf_plateau``. Querying the backref with
   ``==`` raised ``InvalidRequestError: Can't compare a collection to an
   object or collection`` inside
   ``WorkflowArchiMateContextService.get_phase_elements``, which every phase
   viewpoint endpoint calls.

2. ``ArchitectureComplianceMatrixService`` filtered ``ARBReviewItem`` and
   ``ComplianceViolation`` by an ``application_id`` column neither table has.
   Reviews reach an application via solution_id -> solution_applications;
   violations record their target only as the free-text ``affected_system``.

Uses the shared fixtures in tests/conftest.py (db_session rolls everything
back), per CLAUDE.md.
"""

from __future__ import annotations

import uuid

import pytest

VIEWPOINT_ENDPOINTS = [
    "/api/ea-workflows/ba/viewpoint",
    "/api/ea/phase-a/viewpoint",
    "/api/ea/phase-d/viewpoint",
    "/api/ea/phase-f/viewpoint",
    "/api/ea/phase-g/viewpoint",
    "/api/ea/phase-g/compliance-matrix",
]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_user_id(db_session, make_org, client):
    """A confirmed user in a fresh org, logged into the test client."""
    from app.models.user import User

    org = make_org("adm-phase")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"adm-phase-{suffix}@example.com",
        first_name="ADM",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()

    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    # The db_session fixture holds an app context open for the whole test, and
    # test-client requests REUSE it (Flask only pushes a fresh app context when
    # none is active for this app). Anything that touched current_user during
    # setup — the tenant flush listener does — cached an anonymous user in the
    # shared `g`, and flask-login then never consults the session cookie. Same
    # trap as tests/test_ba_tenant_and_authz.py::_login; clear the caches.
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    return user.id


@pytest.mark.parametrize("endpoint", VIEWPOINT_ENDPOINTS)
def test_adm_phase_api_no_server_error(client, logged_in_user_id, endpoint):
    """Authenticated GET must not 500 (empty data is a legitimate answer)."""
    response = client.get(endpoint)
    assert response.status_code != 401, f"{endpoint}: login did not take"
    assert response.status_code < 500, (
        f"{endpoint} returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:500]}"
    )


def test_get_phase_elements_matches_plateau_column(db_session, tenant_ctx, make_org):
    """The plateau fallback in get_phase_elements queries the real column.

    Before the fix this raised InvalidRequestError because it compared the
    Plateau backref (a collection) to a string.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.workflow_archimate_context_service import (
        WorkflowArchiMateContextService,
    )

    org = make_org("plateau")
    element = ArchiMateElement(
        name=f"Phase A driver {uuid.uuid4().hex[:6]}",
        type="Driver",
        layer="motivation",
        organization_id=org.id,
    )
    element.togaf_plateau = "ADM_PHASE_A_VISION"
    db_session.add(element)
    db_session.flush()

    with tenant_ctx(org.id):
        elements = WorkflowArchiMateContextService().get_phase_elements(
            "ADM_PHASE_A_VISION"
        )

    assert element.id in [e["id"] for e in elements]
    matched = next(e for e in elements if e["id"] == element.id)
    assert matched["plateau"] == "ADM_PHASE_A_VISION"


def test_compliance_matrix_uses_real_linkage(db_session, tenant_ctx, make_org):
    """ARB status reaches an application via its solutions (the
    solution_applications junction); the violation count is None because
    compliance_violations has no application linkage — affected_system is
    free text, and a fabricated 0 would read as "no violations". Neither
    query may reference the nonexistent application_id column."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.compliance_models import CompliancePolicy, ComplianceViolation
    from app.models.solution_models import Solution, solution_applications
    from app.models.user import User
    from app.services.architecture_compliance_matrix_service import (
        ArchitectureComplianceMatrixService,
    )
    from app import db

    org = make_org("compliance")
    suffix = uuid.uuid4().hex[:8]

    submitter = User(
        email=f"arb-{suffix}@example.com",
        first_name="ARB",
        last_name="Submitter",
        organization_id=org.id,
        confirmed=True,
    )
    application = ApplicationComponent(
        name=f"Matrix App {suffix}", organization_id=org.id
    )
    solution = Solution(
        name=f"Matrix Solution {suffix}", organization_id=org.id
    )
    db_session.add_all([submitter, application, solution])
    db_session.flush()

    db_session.execute(
        solution_applications.insert().values(
            solution_id=solution.id,
            application_component_id=application.id,
        )
    )
    review = ARBReviewItem(
        review_number=f"REV-{suffix}",
        title="Matrix review",
        review_type="solution_architecture",
        status="approved",
        solution_id=solution.id,
        submitter_id=submitter.id,
        # ARBReviewItem is TenantMixin as of the wave6 tenancy fixes; outside
        # a request context the org is not auto-assigned, and a NULL org row
        # is invisible to the tenant-filtered query under test.
        organization_id=org.id,
    )
    # CompliancePolicy/ComplianceViolation are TenantMixin as of the compliance
    # tenancy fix; outside a request context the org is not auto-assigned, same
    # as ARBReviewItem above.
    policy = CompliancePolicy(
        name=f"Matrix Policy {suffix}",
        policy_type="security",
        description="test policy",
        organization_id=org.id,
    )
    db_session.add_all([review, policy])
    db_session.flush()
    violation = ComplianceViolation(
        policy_id=policy.id,
        description="test violation",
        affected_system=application.name,
        organization_id=org.id,
    )
    db_session.add(violation)
    db_session.flush()

    with tenant_ctx(org.id):
        matrix = ArchitectureComplianceMatrixService().compute_compliance_matrix()

    rows = [r for r in matrix if r["app_id"] == application.id]
    assert rows, "seeded application missing from compliance matrix"
    assert rows[0]["arb_review_status"] == "approved"
    assert rows[0]["compliance_score"] == 100
    # Even with a violation row whose affected_system matches the app's name,
    # the count is None: free text is not a foreign key, and the null-vs-zero
    # rule forbids publishing an unattributable count as a number.
    assert rows[0]["violation_count"] is None
