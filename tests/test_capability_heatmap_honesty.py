"""``CapabilityHeatmapService.get_maturity_heatmap`` must never invent a
maturity level, must never silently drop a capability with no domain, and must
scope its population read explicitly to the caller's organisation.

Every test creates the rows it asserts on inside its own organisation and
asserts only about those rows -- the test database is shared and may already
carry rows from seeds or other sessions, so no test assumes a table is
globally empty or that a count equals a repository-wide total.
"""

from __future__ import annotations

import uuid

import pytest
from flask import g
from flask_login import login_user

from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.modules.capabilities.services.capability_heatmap_service import (
    CapabilityHeatmapService,
)

pytestmark = pytest.mark.usefixtures("db_session")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _domain(db_session, label):
    """A BusinessDomain with a random, collision-free code (max 10 chars)."""

    code = ("D" + uuid.uuid4().hex[:8]).upper()[:10]
    domain = BusinessDomain(code=code, name=f"Domain {label} {code}")
    db_session.add(domain)
    db_session.flush()
    return domain


def _capability(
    db_session,
    org,
    *,
    domain=None,
    current=None,
    target=None,
    scope="tenant",
    name=None,
):
    cap = UnifiedCapability(
        name=name or f"Capability {uuid.uuid4().hex[:8]}",
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        scope=scope,
        organization_id=org.id if org is not None else None,
        domain_id=domain.id if domain is not None else None,
        current_maturity_level=current,
        target_maturity_level=target,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _make_user(db_session, org, label):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"heat-{label}-{suffix}@example.com",
        first_name="Heat",
        last_name="Honesty",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user


def _domain_row(result, code):
    for row in result["domains"]:
        if row["code"] == code:
            return row
    raise AssertionError(
        f"No domain row with code {code!r}; got {[r['code'] for r in result['domains']]}"
    )


def _all_capability_names(result):
    names = set()
    for row in result["domains"]:
        for level_names in row["capability_names"].values():
            names.update(level_names)
        names.update(row["unassessed_capability_names"])
    return names


# ---------------------------------------------------------------------------
# An unrecorded current level is never Level 1
# ---------------------------------------------------------------------------


def test_missing_current_maturity_is_unassessed_not_level_one(db_session, make_org, tenant_ctx):
    org = make_org("unassessed-current")
    domain = _domain(db_session, "unassessed-current")
    cap = _capability(db_session, org, domain=domain, current=None, target=None, name="No current maturity")

    with tenant_ctx(org.id):
        result = CapabilityHeatmapService().get_maturity_heatmap()

    row = _domain_row(result, domain.code)
    assert row["counts"][1] == 0
    assert cap.name not in row["capability_names"][1]
    assert row["unassessed_count"] == 1
    assert cap.name in row["unassessed_capability_names"]
    assert row["avg_maturity"] is None
    assert row["avg_maturity_denominator"] == 0
    assert row["health_score"] is None
    assert row["reason_code"] == "no_maturity_recorded"
    # total_capabilities still counts recorded + unassessed -- same population
    # size as before this change, for the same population.
    assert row["total_capabilities"] == 1


# ---------------------------------------------------------------------------
# An unrecorded target contributes to no target figure
# ---------------------------------------------------------------------------


def test_missing_target_contributes_to_no_target_figure(db_session, make_org, tenant_ctx):
    org = make_org("unassessed-target")
    domain = _domain(db_session, "unassessed-target")
    cap = _capability(db_session, org, domain=domain, current=3, target=None, name="No target maturity")

    with tenant_ctx(org.id):
        result = CapabilityHeatmapService().get_maturity_heatmap()

    row = _domain_row(result, domain.code)
    assert row["counts"][3] == 1
    assert cap.name in row["capability_names"][3]
    assert row["avg_maturity"] == 3.0
    assert row["avg_maturity_denominator"] == 1
    assert row["avg_target"] is None
    assert row["avg_target_denominator"] == 0
    # health_score needs both averages present.
    assert row["health_score"] is None


# ---------------------------------------------------------------------------
# Recorded current levels 1-5 land in their own bucket, and nowhere else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_recorded_current_levels_land_in_their_own_bucket(db_session, make_org, tenant_ctx, level):
    org = make_org(f"level-{level}")
    domain = _domain(db_session, f"level-{level}")
    cap = _capability(
        db_session, org, domain=domain, current=level, target=level, name=f"Capability at level {level}"
    )

    with tenant_ctx(org.id):
        result = CapabilityHeatmapService().get_maturity_heatmap()

    row = _domain_row(result, domain.code)
    for other_level in range(1, 6):
        if other_level == level:
            assert row["counts"][other_level] == 1
            assert cap.name in row["capability_names"][other_level]
        else:
            assert row["counts"][other_level] == 0
            assert cap.name not in row["capability_names"][other_level]
    assert row["unassessed_count"] == 0
    assert row["avg_maturity"] == float(level)
    assert row["avg_target"] == float(level)
    assert row["avg_maturity_denominator"] == 1
    assert row["avg_target_denominator"] == 1
    assert row["health_score"] == 100.0
    assert row["reason_code"] is None


# ---------------------------------------------------------------------------
# A capability with no domain is kept, grouped, ordered last
# ---------------------------------------------------------------------------


def test_missing_domain_capability_grouped_separately_and_ordered_last(db_session, make_org, tenant_ctx):
    org = make_org("no-domain")
    domain = _domain(db_session, "real")
    real_cap = _capability(db_session, org, domain=domain, current=2, name="Has a domain")
    orphan_cap = _capability(db_session, org, domain=None, current=4, name="No domain at all")

    with tenant_ctx(org.id):
        result = CapabilityHeatmapService().get_maturity_heatmap()

    codes = [row["code"] for row in result["domains"]]
    # Sorting must not raise with both kinds of row present, and the
    # no-domain group (code None) sorts after every real domain.
    assert codes[-1] is None, f"no-domain group must sort last, got {codes}"
    assert codes.count(None) == 1
    assert codes[:-1] == sorted(codes[:-1])

    no_domain_row = result["domains"][-1]
    assert no_domain_row["has_domain"] is False
    assert no_domain_row["code"] is None
    assert orphan_cap.name in no_domain_row["capability_names"][4]
    assert real_cap.name not in no_domain_row["capability_names"][4]
    # Not a hardcoded count: a shared-reference row with no domain, from
    # seed data or another session, would legitimately add to this group
    # too, so assert the relationship and that our own row is in it rather
    # than assuming the database holds only what this test created.
    assert result["capabilities_without_domain"] == no_domain_row["total_capabilities"]
    assert no_domain_row["total_capabilities"] >= 1
    # total_domains excludes the no-domain group.
    assert result["total_domains"] == len(result["domains"]) - 1

    real_row = _domain_row(result, domain.code)
    assert real_row["has_domain"] is True
    assert real_cap.name in real_row["capability_names"][2]


# ---------------------------------------------------------------------------
# An empty tenant never sees another tenant's rows
# ---------------------------------------------------------------------------


def test_tenant_with_no_capabilities_sees_none_of_a_sibling_tenants_rows(db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    org_empty = make_org("no-caps")
    org_other = make_org("has-caps")
    domain = _domain(db_session, "empty-check")
    other_cap = _capability(
        db_session, org_other, domain=domain, current=3, target=4, name="Other tenant's capability"
    )
    # A BusinessCapability row too, owned by the other tenant. When the
    # empty tenant's UnifiedCapability population comes back with zero rows,
    # get_maturity_heatmap falls back to BusinessCapability.query.all() --
    # if that fallback's own tenant scoping were absent or mis-keyed, this
    # row would leak onto the empty tenant's grid and this test would still
    # pass if it only checked for the UnifiedCapability row by name.
    other_business_cap = BusinessCapability(
        name="Other tenant's business capability",
        code=f"BC-{uuid.uuid4().hex[:8]}",
        organization_id=org_other.id,
        business_domain=domain.name,
    )
    db_session.add(other_business_cap)
    db_session.flush()

    with tenant_ctx(org_empty.id):
        result = CapabilityHeatmapService().get_maturity_heatmap()

    assert result["domains"] == []
    assert result["total_capabilities"] == 0
    assert other_cap.name not in _all_capability_names(result)
    assert other_business_cap.name not in _all_capability_names(result)
    assert result["tenant_reason_code"] is None


# ---------------------------------------------------------------------------
# Reference rows are shared, unclassified null rows are not, and two
# tenants cannot see each other's rows
# ---------------------------------------------------------------------------


def test_reference_shared_unclassified_null_hidden_tenants_isolated(db_session, make_org, tenant_ctx):
    org_a = make_org("cross-a")
    org_b = make_org("cross-b")
    domain = _domain(db_session, "cross")

    cap_a = _capability(db_session, org_a, domain=domain, current=2, name="Tenant A capability")
    cap_b = _capability(db_session, org_b, domain=domain, current=4, name="Tenant B capability")
    reference_cap = _capability(
        db_session, None, domain=domain, current=5, scope="reference", name="Reference capability"
    )
    # An organisation-null row that is NOT an explicit reference row must not
    # be treated as shared. Relying on the ambient listener alone (the fourth
    # mutation below) would leak this into both tenants.
    unclassified_cap = UnifiedCapability(
        name="Unclassified null-org capability",
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        scope=None,
        organization_id=None,
        domain_id=domain.id,
        current_maturity_level=1,
    )
    db_session.add(unclassified_cap)
    db_session.flush()

    with tenant_ctx(org_a.id):
        result_a = CapabilityHeatmapService().get_maturity_heatmap()
    with tenant_ctx(org_b.id):
        result_b = CapabilityHeatmapService().get_maturity_heatmap()

    names_a = _all_capability_names(result_a)
    names_b = _all_capability_names(result_b)

    assert cap_a.name in names_a
    assert cap_b.name not in names_a
    assert reference_cap.name in names_a
    assert unclassified_cap.name not in names_a

    assert cap_b.name in names_b
    assert cap_a.name not in names_b
    assert reference_cap.name in names_b
    assert unclassified_cap.name not in names_b


def test_seeded_catalogue_row_is_hidden_from_every_tenant_until_classified(db_session, make_org, tenant_ctx):
    """The bulk capability seeder writes ``organization_id`` NULL with no
    ``scope`` set; a row in that exact shape only becomes a shared reference
    row once a later classification step sets ``scope='reference'`` on it.
    Until that has run, this row is real, deployed data that the strict
    predicate correctly hides from every tenant -- a real operational state,
    not a hypothetical one, and worth its own named regression separate from
    the general reference/unclassified-visibility case above."""
    org = make_org("pre-classification")
    domain = _domain(db_session, "pre-classification")
    seeded_row = UnifiedCapability(
        name="Seeded catalogue capability, not yet classified",
        code=f"SEED-{uuid.uuid4().hex[:8]}",
        level=1,
        scope=None,
        organization_id=None,
        domain_id=domain.id,
        current_maturity_level=3,
    )
    db_session.add(seeded_row)
    db_session.flush()

    with tenant_ctx(org.id):
        result = CapabilityHeatmapService().get_maturity_heatmap()

    assert seeded_row.name not in _all_capability_names(result)


# ---------------------------------------------------------------------------
# No resolvable organisation fails closed
# ---------------------------------------------------------------------------


def test_no_tenant_context_fails_closed(db_session, make_org):
    org = make_org("no-context")
    domain = _domain(db_session, "no-context")
    _capability(db_session, org, domain=domain, current=3, name="Should not leak with no tenant context")

    # Deliberately no tenant_ctx: g.current_org_id is never set.
    result = CapabilityHeatmapService().get_maturity_heatmap()

    assert result["domains"] == []
    assert result["total_capabilities"] == 0
    assert result["total_domains"] == 0
    assert result["capabilities_without_domain"] == 0
    assert result["tenant_reason_code"] == "no_tenant_context"


# ---------------------------------------------------------------------------
# get_domain_health / get_gap_alerts are untouched
# ---------------------------------------------------------------------------


def test_get_domain_health_still_carries_its_own_pre_existing_defect(db_session, make_org, tenant_ctx):
    """``get_domain_health`` still invents a maturity level for an unassessed
    capability -- unlike ``get_maturity_heatmap``, it was not changed here.

    Pinning the old, still-present behaviour here means a future change to
    ``get_domain_health`` is a deliberate decision, not an accidental side
    effect of this one.
    """
    org = make_org("domain-health-untouched")
    domain = _domain(db_session, "domain-health-untouched")
    _capability(db_session, org, domain=domain, current=None, target=None, name="Unassessed for domain health")

    with tenant_ctx(org.id):
        health = CapabilityHeatmapService().get_domain_health()

    row = next(r for r in health if r["domain_code"] == domain.code)
    # Still `current_maturity_level or 1` -- unchanged by this task.
    assert row["avg_maturity"] == 1.0


def test_get_gap_alerts_still_reachable_and_unchanged_in_shape(db_session, make_org, tenant_ctx):
    org = make_org("gap-alerts-untouched")
    domain = _domain(db_session, "gap-alerts-untouched")
    _capability(db_session, org, domain=domain, current=1, target=5, name="Big gap capability")

    with tenant_ctx(org.id):
        alerts = CapabilityHeatmapService().get_gap_alerts()

    assert set(alerts.keys()) == {"unmapped", "low_coverage", "maturity_gaps", "summary"}
    assert isinstance(alerts["summary"]["total_alerts"], int)


# ---------------------------------------------------------------------------
# The population read is one query, and the query count does not grow
# with the number of capabilities (measured further at 50/1,000 -- see the
# build report for the larger-scale run).
# ---------------------------------------------------------------------------


def test_population_query_count_does_not_grow_with_capability_count(db_session, make_org, tenant_ctx):
    """The population read is one query, regardless of population size.

    Every tenant-scoped query in this app is preceded by one transaction-local
    ``SELECT set_config('archie.organization_id', ...)`` from the pre-existing
    tenant-isolation middleware (``app/middleware/tenant_isolation.py``, not
    touched by this task) -- that companion statement is unrelated plumbing,
    fires once regardless of population size, and is excluded from the count
    below so the assertion is about the population read itself.
    """
    from app.modules.capabilities.services import capability_heatmap_service as heatmap_module

    def _population_query_count():
        return sum(
            1 for q in heatmap_module.query_counter.queries if "unified_capabilities" in q
        )

    org_small = make_org("scale-small")
    _capability(db_session, org_small, domain=None, current=1, name="scale-small-1")

    with tenant_ctx(org_small.id):
        CapabilityHeatmapService().get_maturity_heatmap()
    small_total = heatmap_module.query_counter.count
    small_population_queries = _population_query_count()

    org_big = make_org("scale-big")
    for i in range(50):
        _capability(db_session, org_big, domain=None, current=(i % 5) + 1, name=f"scale-big-{i}")

    with tenant_ctx(org_big.id):
        CapabilityHeatmapService().get_maturity_heatmap()
    big_total = heatmap_module.query_counter.count
    big_population_queries = _population_query_count()

    assert small_population_queries == 1, heatmap_module.query_counter.queries
    assert big_population_queries == 1, heatmap_module.query_counter.queries
    assert big_total == small_total, (
        f"total query count grew with population size: {small_total} at 1 row vs {big_total} at 50 rows"
    )


# ---------------------------------------------------------------------------
# All four direct callers answer with a valid body
# ---------------------------------------------------------------------------


def test_all_four_direct_callers_return_a_valid_body(app, db_session, make_org):
    import werkzeug.exceptions

    from app.api.dashboard_routes import api_capability_heatmap as legacy_api
    from app.main.capability_framework_routes import get_maturity_heatmap as orphaned_api
    from app.modules.dashboard.routes.dashboard_pages_routes import (
        api_capability_heatmap as v1_api,
    )
    from app.modules.dashboard.v2.routes.dashboard_pages_routes import (
        api_capability_heatmap as v2_api,
    )

    org = make_org("four-callers")
    actor = _make_user(db_session, org, "callers")
    domain = _domain(db_session, "four-callers")
    cap = _capability(db_session, org, domain=domain, current=2, target=4, name="Four-callers capability")

    # A platform administrator for the capability framework view.
    from app.models.user import Role, User

    platform_admin = User(
        email=f"pa-four-callers-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Platform",
        last_name="Admin",
        organization_id=org.id,
        enterprise_role="platform_admin",
        confirmed=True,
        is_platform_admin=True,
    )
    platform_admin.password = "Sup3rSecret!23"
    db_session.add(platform_admin)
    db_session.flush()
    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator").first()
    platform_admin.role = role
    db_session.flush()

    # Three dashboard heatmap views accept a signed-in organisation user.
    for view in (v1_api, v2_api, legacy_api):
        with app.test_request_context("/"):
            g.current_org_id = org.id
            login_user(actor)
            response = view()
        resp_obj, status = (
            response if isinstance(response, tuple) else (response, response.status_code)
        )
        assert status == 200, f"{view.__module__}.{view.__name__} returned {status}"
        payload = resp_obj.get_json()
        assert payload is not None, f"{view.__module__}.{view.__name__} returned no JSON body"

    # The capability framework view requires a platform administrator.
    with app.test_request_context("/"):
        g.current_org_id = org.id
        login_user(platform_admin)
        response = orphaned_api()
    resp_obj, status = (
        response if isinstance(response, tuple) else (response, response.status_code)
    )
    assert status == 200, f"orphaned_api returned {status}"
    payload = resp_obj.get_json()
    assert payload is not None, "orphaned_api returned no JSON body"

    # A plain organisation user must be refused.
    with pytest.raises(werkzeug.exceptions.Forbidden):
        with app.test_request_context("/"):
            g.current_org_id = org.id
            login_user(actor)
            orphaned_api()

    # Spot-check the v1 (live) shape carries the new fields and the recorded
    # capability, not a fabricated one.
    with app.test_request_context("/"):
        g.current_org_id = org.id
        login_user(actor)
        v1_response = v1_api()
    v1_payload = v1_response[0].get_json()
    v1_data = v1_payload["data"]
    assert "capabilities_without_domain" in v1_data
    assert "unassessed_legend" in v1_data
    assert "tenant_reason_code" in v1_data
    row = _domain_row(v1_data, domain.code)
    # JSON object keys are always strings -- "2", not the Python int 2, once
    # this has round-tripped through jsonify()/get_json().
    assert cap.name in row["capability_names"]["2"]


# ---------------------------------------------------------------------------
# The four compatibility re-export shims still import the same class
# ---------------------------------------------------------------------------


def test_reexport_shims_still_import_the_same_service_class():
    from app.modules.capabilities.services import CapabilityHeatmapService as PkgCapabilities
    from app.modules.capabilities.services.analysis_service import (
        CapabilityHeatmapService as AnalysisShim,
    )
    from app.modules.dashboard.v2.services import CapabilityHeatmapService as PkgDashboardV2
    from app.modules.dashboard.v2.services.capability_heatmap_service_v2 import (
        CapabilityHeatmapService as DashboardV2Shim,
    )
    from app.modules.governance.v2.services import CapabilityHeatmapService as PkgGovernanceV2
    from app.modules.governance.v2.services.capability_heatmap_service_v2 import (
        CapabilityHeatmapService as GovernanceV2Shim,
    )
    from app.services.capability_heatmap_service import (
        CapabilityHeatmapService as ServicesShim,
    )

    for shim in (
        PkgCapabilities,
        AnalysisShim,
        PkgDashboardV2,
        DashboardV2Shim,
        PkgGovernanceV2,
        GovernanceV2Shim,
        ServicesShim,
    ):
        assert shim is CapabilityHeatmapService


# ---------------------------------------------------------------------------
# The no-domain group carries no fabricated investment figure
# ---------------------------------------------------------------------------


def test_investment_guard_leaves_no_domain_group_unset_and_real_domain_unchanged(
    app, db_session, make_org
):
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_lifecycle_models import SolutionTCOItem
    from app.models.solution_models import Solution, SolutionCapabilityMapping
    from app.modules.dashboard.routes.dashboard_pages_routes import (
        api_capability_heatmap as v1_api,
    )
    from app.modules.dashboard.v2.routes.dashboard_pages_routes import (
        api_capability_heatmap as v2_api,
    )

    org = make_org("investment-guard")
    actor = _make_user(db_session, org, "investment")
    domain = _domain(db_session, "investment")
    _capability(db_session, org, domain=domain, current=3, target=4, name="Domain-linked capability")
    _capability(db_session, org, domain=None, current=2, name="No-domain capability")

    # Investment is looked up by matching BusinessCapability.code against the
    # heatmap domain row's code -- pre-existing, unchanged behaviour.
    business_cap = BusinessCapability(
        name="Investment business capability",
        code=domain.code,
        business_domain=domain.name,
        organization_id=org.id,
    )
    db_session.add(business_cap)
    db_session.flush()

    solution = Solution(
        name=f"Investment solution {uuid.uuid4().hex[:8]}",
        description="Investment guard test fixture",
        organization_id=org.id,
        created_by_id=actor.id,
        governance_status="draft",
    )
    db_session.add(solution)
    db_session.flush()

    db_session.add(SolutionCapabilityMapping(solution_id=solution.id, capability_id=business_cap.id))
    db_session.add(SolutionTCOItem(solution_id=solution.id, cost_category="build", amount=1000))
    db_session.flush()

    for view in (v1_api, v2_api):
        with app.test_request_context("/?group_by=domain"):
            g.current_org_id = org.id
            login_user(actor)
            response = view()
        resp_obj = response[0] if isinstance(response, tuple) else response
        payload = resp_obj.get_json()
        data = payload["data"]

        real_row = next(r for r in data["domains"] if r["code"] == domain.code)
        no_domain_row = next(r for r in data["domains"] if r["code"] is None)

        assert real_row["total_investment"] == 1000.0
        assert "total_investment" not in no_domain_row
        assert "solution_count" not in no_domain_row
        assert "cost_breakdown" not in no_domain_row


# ---------------------------------------------------------------------------
# The page renders the new column and legend entry server-side
# ---------------------------------------------------------------------------


def test_page_renders_not_assessed_column_and_legend(app, client, login_as, db_session, make_org):
    org = make_org("page-render")
    user = _make_user(db_session, org, "page-render")

    login_as(client, user)
    resp = client.get("/dashboard/capability-heatmap")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Not assessed" in html


# Every test above uses db_session (rolled back at teardown) and make_org
# (collision-free names); the module's own repeated green runs, logged in
# the build report, are the actual evidence that this leaves no row behind.
