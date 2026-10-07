"""Regression test for the FE-QA route sweep's 22 authenticated 500s.

An authenticated sweep (admin user, empty-ish org, non-existent ids) found these
routes returning HTTP 500 instead of a clean 404 (entity-id routes) or 200 with an
empty result (list/aggregate routes). Root causes were all in the "broad except
Exception swallows the deliberate 404" family, one aggregate KeyError on empty
data, and two external-enrichment routes that should answer 503 rather than 500
when the upstream provider is unreachable. See the fixes referenced inline below.

Uses the shared fixtures from tests/conftest.py (db_session/make_org), following
tests/test_tenant_isolation.py's style.
"""
from __future__ import annotations

import uuid

import pytest

# Every fixture-using test below reaches db_session through its fixtures. The
# isolation-sweep tests at the end deliberately do not: each route they drive
# opens and discards a transaction of its own (tests/_isolation_sweep.py).


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def admin_client(app, db_session, make_org):
    from app.models.user import User

    org = make_org("route_sweep")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"route-sweep-admin-{suffix}@example.com",
        first_name="Sweep",
        last_name="Admin",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)
    return client


# (path, min_expected_status) - all must be < 500. Entity-id routes with a
# non-existent id (999999) expect 404; the two no-param aggregate routes and
# the coverage matrix expect 200 with an empty/zeroed result; the two external
# enrichment routes expect 503 because the upstream provider is unreachable in
# this environment (app/api/api_pipeline_routes.py::enrich_vendor/enrich_product).
ROUTES = [
    # app/api/application_routes.py - get_or_404() was inside a bare
    # `except Exception`, so the deliberate NotFound was caught and turned into
    # a 500. Fixed by re-raising HTTPException before the generic handler.
    "/api/applications/999999/process-links",
    "/api/applications/999999/solutions",
    "/api/applications/999999/work-packages",
    "/api/applications/999999/architecture/elements",
    "/api/applications/999999/architecture/export-csv",
    "/api/applications/999999/details",
    # no-param aggregate routes - already tolerant of empty data once the
    # get_or_404 issue elsewhere was ruled out; kept here as a regression guard.
    "/api/applications/duplicates",
    "/api/applications/table-data",
    # app/services/interactive_coverage_matrix.py::_calculate_matrix_statistics
    # - the empty-cells branch omitted "coverage_distribution", which the route
    # unconditionally indexes.
    "/api/coverage-matrix/matrix-data",
    # app/api/api_pipeline_routes.py - external enrichment providers
    # unreachable; now answers 503 instead of 500.
    "/api/pipeline/enrich/product/nonexistent-product",
    "/api/pipeline/enrich/vendor/nonexistent-vendor",
    # app/modules/applications/routes/vendor_api_routes.py::get_architectural_analysis -
    # the route captures id as a string and passed it straight into
    # ApplicationComponent.query.filter_by(id=id); under psycopg 3 that compares
    # an integer column to varchar and PostgreSQL refuses rather than coercing
    # it, so every call 500'd. The id is now parsed as an integer at the edge.
    "/applications/api/v1/applications/999999/architectural-analysis",
    "/applications/api/vendor-analysis/999999/export",
    "/applications/api/vendor-analysis/999999/results",
    # app/modules/governance/routes/capability_governance_routes.py -
    # service returned {"success": False, "error": "Capability not found"} but
    # the route always answered 500 for any success=False result.
    "/capability-governance/api/health-check/999999",
    # app/modules/governance/routes/capability_management_routes.py - same
    # get_or_404-inside-broad-except pattern as application_routes.py.
    "/capability-management/api/capability-details/999999",
    # app/modules/applications/routes/capability_tagging_routes.py - same
    # pattern.
    "/dashboard/api/applications/999999/tags",
    # app/application_mgmt/implementation_layer_routes.py - same pattern.
    "/dashboard/api/applications/999999/work-packages",
    # app/api/application_merging_routes.py - same pattern.
    "/dashboard/api/applications/merging/analyze/999999",
    # app/modules/solutions_strategic/v2/routes/solution_design_routes.py -
    # same pattern, plus it leaked str(e) into the response body.
    "/solutions/999999/traceability/chain",
    # app/modules/vendors/routes/vendor_analysis_routes.py -
    # OptionsAnalysisService.get_comparison_data() raises ValueError for a
    # missing analysis; the route caught it with a bare `except Exception`
    # and returned 500.
    "/vendor-analysis/999999/comparison",
    "/vendor-analysis/999999/results",
]


# The two enrichment routes legitimately answer 503 (upstream provider
# unreachable) rather than 2xx/404; every other route must be < 500.
ALLOWED_5XX = {
    "/api/pipeline/enrich/product/nonexistent-product": 503,
    "/api/pipeline/enrich/vendor/nonexistent-vendor": 503,
}


@pytest.mark.parametrize("path", ROUTES)
def test_route_does_not_500(admin_client, path):
    resp = admin_client.get(path)
    allowed = ALLOWED_5XX.get(path)
    ok = resp.status_code < 500 or resp.status_code == allowed
    assert ok, (
        f"{path} returned {resp.status_code}: {resp.get_data(as_text=True)[:500]}"
    )


def test_architectural_analysis_missing_id_returns_404(admin_client):
    """A well-formed but non-existent application id 404s cleanly.

    Guards the specific status code -- test_route_does_not_500 above only
    checks < 500 -- for the id-type regression: filter_by(id=id) comparing a
    path-captured string against the integer id column used to 500 under
    psycopg 3 for this exact id.
    """
    resp = admin_client.get(
        "/applications/api/v1/applications/999999/architectural-analysis"
    )
    assert resp.status_code == 404, (
        f"Expected 404 for a non-existent application id; got {resp.status_code}: "
        f"{resp.get_data(as_text=True)[:500]}"
    )


@pytest.fixture
def other_org_application(app, db_session, make_org):
    """A user in one organisation, and an application that belongs to another.

    Mirrors admin_client above (db_session/make_org, flush not commit) rather
    than the explicit-commit two-organisation fixtures elsewhere in the test
    suite -- this module's own admin_client already demonstrates that shape is
    visible to requests made through the test client here.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    org_a = make_org("archan_a")
    org_b = make_org("archan_b")

    suffix = uuid.uuid4().hex[:8]
    user_a = User(
        email=f"archan-a-{suffix}@example.com",
        first_name="Archan",
        last_name="A",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user_a)

    app_b = ApplicationComponent(
        name=f"App-B-{suffix}",
        organization_id=org_b.id,
    )
    db_session.add(app_b)
    db_session.flush()

    client = app.test_client()
    _login(client, user_a.id)
    return {"client": client, "app_b_id": app_b.id}


def test_architectural_analysis_other_org_id_returns_404(other_org_application):
    """A well-formed id for another organisation's application 404s, not 500
    and not the application's data.

    The tenant-isolation SQLAlchemy listener filters ApplicationComponent
    reads by g.current_org_id automatically (see
    app/middleware/tenant_isolation.py), so this exercises that the id-type
    fix did not accidentally bypass it -- a platform_admin caller here still
    gets 404 for another organisation's id, the same as any other role.
    """
    client = other_org_application["client"]
    app_b_id = other_org_application["app_b_id"]

    resp = client.get(
        f"/applications/api/v1/applications/{app_b_id}/architectural-analysis"
    )
    assert resp.status_code == 404, (
        f"Expected 404 for another organisation's application id; got "
        f"{resp.status_code}: {resp.get_data(as_text=True)[:500]}"
    )


# =============================================================================
# Leaks found by the isolation sweep (tests/test_tenant_isolation_matrix.py)
# and fixed here. Each route acted on a child row -- a solution's TCO line, an
# application's process link, a board's sprint -- found by its own id and the
# parent id from the URL, without ever loading the parent through the tenant
# filter. So another organisation's pair of ids reached another organisation's
# row. Each is driven through the same engine as the sweep, and each of these
# answered "leak" before its fix.
# =============================================================================

from tests import _isolation_sweep as sweep  # noqa: E402
from tests.test_tenant_isolation_matrix import POLICY  # noqa: E402

FIXED_LEAKS = {
    # app/modules/solutions_strategic/v2/routes/solution_phase_routes.py
    "PUT /solutions/<int:solution_id>/tco/<int:tco_id>": "solution TCO line",
    "DELETE /solutions/<int:solution_id>/tco/<int:tco_id>": "solution TCO line",
    "PUT /solutions/<int:solution_id>/plateaus/<int:plateau_id>": "solution plateau",
    "DELETE /solutions/<int:solution_id>/plateaus/<int:plateau_id>": "solution plateau",
    "DELETE /solutions/<int:solution_id>/business-elements/<int:row_id>": "solution business element",
    "DELETE /solutions/<int:solution_id>/app-elements/<int:row_id>": "solution application element",
    "DELETE /solutions/<int:solution_id>/tech-elements/<int:row_id>": "solution technology element",
    "DELETE /solutions/<int:solution_id>/quality-attributes/<int:row_id>": "solution quality attribute",
    "DELETE /solutions/<int:solution_id>/slas/<int:row_id>": "solution SLA",
    "PUT /solutions/<int:solution_id>/capabilities/<int:mapping_id>": "solution capability mapping",
    # app/api/application_routes.py and app/application_mgmt/business_layer_routes.py
    "DELETE /api/applications/<int:app_id>/process-links/<int:link_id>": "application process link",
    "DELETE /dashboard/api/applications/<int:app_id>/process-links/<int:link_id>": "application process link",
    # app/modules/architecture/routes/sprint_routes.py, app/services/sprint_service.py
    "GET /api/sprints/<int:sprint_id>/burndown": "sprint",
    "GET /api/sprints/<int:sprint_id>/analytics": "sprint",
    "GET /sprints/<int:sprint_id>/analytics": "sprint",
    # app/modules/codegen/routes/rules_routes.py
    "DELETE /solutions/<int:solution_id>/codegen/rules/<int:rule_id>": "solution business rule",
    "POST /solutions/<int:solution_id>/codegen/rules/compile": "solution business rules",
    # app/modules/codegen/routes/workflow_routes.py
    "GET /api/codegen/workflow-designs/<int:design_id>": "workflow design",
    "PUT /api/codegen/workflow-designs/<int:design_id>": "workflow design",
    # app/modules/solutions_product/routes/product_routes.py
    "PUT /api/solutions/<int:solution_id>/webhooks/<int:webhook_id>": "solution webhook",
    "DELETE /api/solutions/<int:solution_id>/webhooks/<int:webhook_id>": "solution webhook",
    "POST /api/solutions/<int:solution_id>/webhooks/<int:webhook_id>/test": (
        "solution webhook: firing it sent a request to another organisation's endpoint"),
    # app/modules/solutions_strategic/v2/routes/programme_routes.py
    "DELETE /solutions/programmes/<int:initiative_id>/api/snapshots/<int:snapshot_id>": "programme snapshot",
    # app/modules/capabilities/routes/enterprise_api_routes.py
    "DELETE /api/enterprise/requirements/<int:req_id>/dependencies/<int:dep_id>": "requirement dependency",
    # app/modules/solutions_strategic/v2/routes/strategic_routes.py
    "GET /strategic/api/capability-health/overrides/<int:override_id>": "capability health override",
    "PUT /strategic/api/capability-health/overrides/<int:override_id>": "capability health override",
    "DELETE /strategic/api/capability-health/overrides/<int:override_id>": "capability health override",
}


@pytest.fixture
def sweep_app(app, _schema):
    previous = app.config.get("WTF_CSRF_ENABLED")
    app.config["WTF_CSRF_ENABLED"] = False
    yield app
    app.config["WTF_CSRF_ENABLED"] = previous


@pytest.mark.parametrize("route", sorted(FIXED_LEAKS))
def test_fixed_leak_refuses_another_organisation(sweep_app, login_as, route):
    """Another organisation's ids are refused, and the owner is still served."""
    cases, _excluded = sweep.run_sweep(sweep_app, login_as, POLICY, only=lambda c: c.key == route)
    assert cases, "%s is no longer an identifier-bearing route" % route
    for case in cases:
        assert case.status == sweep.PROVEN, (
            "%s (%s): %s -- B answered %s, owner answered %s. %s"
            % (case.key, FIXED_LEAKS[route], case.status, case.b_status, case.a_status, case.detail))


def _two_orgs(db_session, make_org):
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(permissions=Permission.ADMINISTER).first()
    orgs, users = [], []
    for tag in ("a", "b"):
        org = make_org("sweep_list_" + tag)
        user = User(
            email="sweep-list-%s-%s@example.test" % (tag, uuid.uuid4().hex[:8]),
            first_name="Sweep", last_name=tag.upper(), organization_id=org.id,
            confirmed=True, enterprise_role="platform_admin", role=role,
        )
        db_session.add(user)
        orgs.append(org)
        users.append(user)
    db_session.flush()
    return orgs, users


def _seed_in(db_session, model, org, user):
    """One row of ``model`` in ``org``, built the way the sweep builds it."""
    token = "zq" + uuid.uuid4().hex[:6]
    row = sweep.Seeder(db_session, org.id, user.id, token, POLICY.shared_models).seed(model, {})
    db_session.commit()
    return row, token


def test_workflow_design_list_holds_only_the_callers_organisation(
    app, db_session, make_org, client, login_as
):
    """The list beside the fixed detail route read every organisation's designs."""
    from app.modules.codegen.models import WorkflowDesign

    (org_a, _org_b), (user_a, user_b) = _two_orgs(db_session, make_org)
    _design, token = _seed_in(db_session, WorkflowDesign, org_a, user_a)

    a_id, b_id = user_a.id, user_b.id
    db_session.expunge_all()
    login_as(client, b_id)
    resp = client.get("/api/codegen/workflow-designs")
    assert resp.status_code == 200
    assert token not in resp.get_data(as_text=True)

    login_as(client, a_id)
    resp = client.get("/api/codegen/workflow-designs")
    assert token in resp.get_data(as_text=True)


def test_capability_health_override_list_holds_only_the_callers_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.strategic import CapabilityHealthOverride

    (org_a, _org_b), (user_a, user_b) = _two_orgs(db_session, make_org)
    _override, token = _seed_in(db_session, CapabilityHealthOverride, org_a, user_a)

    a_id, b_id = user_a.id, user_b.id
    db_session.expunge_all()
    login_as(client, b_id)
    resp = client.get("/strategic/api/capability-health/overrides")
    assert resp.status_code == 200
    assert token not in resp.get_data(as_text=True)

    login_as(client, a_id)
    resp = client.get("/strategic/api/capability-health/overrides")
    assert token in resp.get_data(as_text=True)


def test_sprints_cannot_be_listed_or_created_under_another_organisations_board(
    app, db_session, make_org, client, login_as
):
    from app.models.adm_kanban import KanbanBoard
    from app.models.sprint import Sprint

    (org_a, _org_b), (user_a, user_b) = _two_orgs(db_session, make_org)
    sprint, token = _seed_in(db_session, Sprint, org_a, user_a)
    board_id = sprint.board_id
    assert db_session.get(KanbanBoard, board_id).organization_id == org_a.id

    a_id, b_id = user_a.id, user_b.id
    db_session.expunge_all()
    login_as(client, b_id)
    listed = client.get("/api/sprints?board_id=%d" % board_id)
    created = client.post("/api/sprints", json={"board_id": board_id, "name": "intruder sprint"})
    assert listed.status_code == 404
    assert created.status_code == 404
    assert token not in listed.get_data(as_text=True)

    login_as(client, a_id)
    listed = client.get("/api/sprints?board_id=%d" % board_id)
    assert listed.status_code == 200
    assert token in listed.get_data(as_text=True)
