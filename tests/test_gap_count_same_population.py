"""One gap register: every total a screen or an API shows counts the
population it lists, so the register never disagrees with itself.

Regression tests for the independent review of the one-gap-register PR:

1. The gap analysis page's "Recorded Gaps" metric is the signed-in
   organisation's register count (count_gaps), not the length of the
   capped, list-backed metric (the page list is limited to 500 rows).
2. GET /capability-map/api/roadmap/gaps with a gap_type filter returns a
   total_gaps that equals the sum of the per-type counts and the length of
   the returned list.
3. GET /capability-map/api/roadmap/archimate-gaps returns a total_count
   that equals the length of the returned (filtered) list.

Each test seeds two real organisations and proves the other organisation's
rows are never returned or counted.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, label, role=None):
    from app.models.user import Role, User

    Role.insert_roles()
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    if role is None:
        role = Role.query.filter_by(name="Administrator").first()
    if role is not None:
        user.role = role
    db_session.commit()
    return user


def _make_business_capability(db_session, org, label):
    from app.models.business_capabilities import BusinessCapability

    suffix = uuid.uuid4().hex[:8]
    cap = BusinessCapability(
        name=f"{label} {suffix}",
        code=f"{label}-{suffix}",
        organization_id=org.id,
        level=1,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _make_application(db_session, org, label, **kwargs):
    from app.models.application_portfolio import ApplicationComponent

    suffix = uuid.uuid4().hex[:8]
    app = ApplicationComponent(
        name=f"{label} {suffix}",
        organization_id=org.id,
        **kwargs,
    )
    db_session.add(app)
    db_session.flush()
    return app


# ── 1. Gap analysis page metric is the register count ────────────────────────


def test_gap_analysis_recorded_gaps_metric_is_register_count(
    app, db_session, make_org, login_as, client, tenant_ctx
):
    """The Recorded Gaps metric equals the signed-in organisation's register
    count (count_gaps), not the length of the page's capped list."""
    from app.services.gap_register_service import count_gaps, create_gap

    org_a = make_org("rec-gaps-a")
    org_b = make_org("rec-gaps-b")

    # Org A owns more register gaps than the page list can show (its query
    # is limited to 500 rows), so the metric reading the list would stall
    # at 500 while the register count keeps growing.
    for i in range(501):
        create_gap(org_a.id, f"Rec Gap A {i:03d}", gap_type="coverage")
    # Org B owns gaps that must never inflate A's metric.
    for i in range(3):
        create_gap(org_b.id, f"Rec Gap B {i:03d}", gap_type="coverage")
    db_session.commit()

    with tenant_ctx(org_a.id):
        assert count_gaps(org_a.id) == 501

    user_a = _make_user(db_session, org_a, "rec-gaps-user")
    login_as(client, user_a)

    resp = client.get("/enterprise/implementation/gap-analysis")
    assert resp.status_code == 200, resp.data[:200]
    html = resp.data.decode("utf-8")

    match = re.search(
        r"data-testid=\"gap-stat-recorded-value\">\s*([0-9]+)\s*<",
        html,
    )
    assert match is not None, "Recorded gaps metric not found in rendered page"
    recorded = int(match.group(1))

    assert recorded == 501, (
        "Recorded Gaps must equal the signed-in organisation's register "
        f"count (501), got {recorded} — the metric reads the list, not count_gaps"
    )
    assert "Rec Gap B" not in html, (
        "The other organisation's gaps must never appear on this page"
    )


# ── 2. Roadmap gaps API total matches the filtered list ──────────────────────


def test_roadmap_gaps_api_total_matches_filtered_list(
    app, db_session, make_org, login_as, client
):
    """GET /capability-map/api/roadmap/gaps with a gap_type filter returns a
    total_gaps that equals the sum of the per-type counts and the length of
    the returned list — never a register-wide count."""
    from app.models.business_capabilities import ApplicationCapabilityCoverage

    org_a = make_org("rm-gaps-a")
    org_b = make_org("rm-gaps-b")

    # Org A: two capabilities with no applications -> coverage gaps; one
    # capability with a single tactical, non-retiring, low-risk application
    # -> a quality gap only.
    _make_business_capability(db_session, org_a, "RM A Coverage 1")
    _make_business_capability(db_session, org_a, "RM A Coverage 2")
    cap_aq = _make_business_capability(db_session, org_a, "RM A Quality 1")
    app_q = _make_application(
        db_session,
        org_a,
        "RM A Tactical App",
        strategic_importance="low",
        lifecycle_status="active",
        technical_risk="low",
        obsolescence_risk="low",
        technology_age_years=1,
    )
    db_session.add(
        ApplicationCapabilityCoverage(
            application_component_id=app_q.id,
            capability_id=cap_aq.id,
        )
    )

    # Org B: one uncovered capability that must not appear for A.
    cap_b1 = _make_business_capability(db_session, org_b, "RM B Coverage 1")
    db_session.commit()

    user_a = _make_user(db_session, org_a, "rm-gaps-user")
    login_as(client, user_a)

    resp = client.get("/capability-map/api/roadmap/gaps?gap_type=coverage")
    assert resp.status_code == 200, resp.data[:200]
    data = resp.get_json()
    assert data.get("success") is True

    gaps = data.get("gaps", [])
    stats = data.get("statistics", {})
    names = [g.get("name", "") for g in gaps]

    assert cap_b1.name not in names, (
        "The other organisation's capability must not appear in the list"
    )
    assert len(gaps) == 2, f"expected 2 coverage gaps, got {len(gaps)}: {names}"

    assert stats["total_gaps"] == 2, (
        "total_gaps must count the population listed (2 after the coverage "
        f"filter), got {stats.get('total_gaps')}"
    )
    part_sum = sum(
        stats.get(key, 0)
        for key in ("coverage_gaps", "quality_gaps", "retirement_gaps", "modernization_gaps")
    )
    assert stats["total_gaps"] == part_sum, (
        "total_gaps must equal the sum of the per-type counts "
        f"({part_sum})"
    )
    assert stats["total_gaps"] == len(gaps), (
        "total_gaps must equal the length of the returned list"
    )


# ── 3. ArchiMate gaps API total_count matches the filtered list ──────────────


def test_archimate_gaps_api_total_count_matches_list(
    app, db_session, make_org, login_as, client
):
    """GET /capability-map/api/roadmap/archimate-gaps returns a total_count
    that equals the length of the returned (filtered) list — not a count of
    every register row regardless of the active filter."""
    from app.services.gap_register_service import create_gap

    org_a = make_org("arch-gaps-a")
    org_b = make_org("arch-gaps-b")

    create_gap(org_a.id, "Arch Gap Cov 1", gap_type="coverage")
    create_gap(org_a.id, "Arch Gap Cov 2", gap_type="coverage")
    create_gap(org_a.id, "Arch Gap Qual 1", gap_type="quality")
    create_gap(org_b.id, "Arch Gap B 1", gap_type="coverage")
    db_session.commit()

    user_a = _make_user(db_session, org_a, "arch-gaps-user")
    login_as(client, user_a)

    resp = client.get("/capability-map/api/roadmap/archimate-gaps?gap_type=coverage")
    assert resp.status_code == 200, resp.data[:200]
    data = resp.get_json()
    assert data.get("success") is True

    gaps = data.get("gaps", [])
    names = [g.get("name", "") for g in gaps]

    assert "Arch Gap Qual 1" not in names, "quality gap must be filtered out"
    assert "Arch Gap B 1" not in names, (
        "the other organisation's gap must not appear in the list"
    )
    assert len(gaps) == 2, f"expected 2 coverage gaps, got {len(gaps)}: {names}"

    assert data["total_count"] == len(gaps), (
        "total_count must equal the length of the returned list "
        f"({len(gaps)}), got {data.get('total_count')}"
    )


# ── count_gaps optional filters (gap type, work-package link) ────────────────


def test_count_gaps_gap_type_filter_counts_only_that_population(
    db_session, make_org, tenant_ctx
):
    """count_gaps(org, gap_type=...) counts the same population the typed
    register list would show, in this organisation only."""
    from app.services.gap_register_service import count_gaps, create_gap

    org_a = make_org("count-type-a")
    org_b = make_org("count-type-b")

    create_gap(org_a.id, "A Cov 1", gap_type="coverage")
    create_gap(org_a.id, "A Cov 2", gap_type="coverage")
    create_gap(org_a.id, "A Qual 1", gap_type="quality")
    create_gap(org_b.id, "B Cov 1", gap_type="coverage")
    db_session.commit()

    with tenant_ctx(org_a.id):
        assert count_gaps(org_a.id, gap_type="coverage") == 2
        assert count_gaps(org_a.id, gap_type="quality") == 1
        assert count_gaps(org_a.id, gap_type="modernization") == 0
    with tenant_ctx(org_b.id):
        assert count_gaps(org_b.id, gap_type="coverage") == 1


def test_count_gaps_work_package_link_filter_matches_not_addressed_list(
    db_session, make_org, tenant_ctx
):
    """count_gaps(org, has_work_package=False) equals the not-addressed list
    length, and has_work_package=True equals the complement, per org."""
    from app.models.implementation_migration import WorkPackage
    from app.services.gap_register_service import (
        count_gaps,
        create_gap,
        get_gaps_not_addressed,
    )

    org_a = make_org("count-wp-a")
    org_b = make_org("count-wp-b")

    gap_a1, _ = create_gap(org_a.id, "A No WP 1", gap_type="coverage")
    gap_a2, _ = create_gap(org_a.id, "A Has WP 1", gap_type="coverage")
    gap_b1, _ = create_gap(org_b.id, "B No WP 1", gap_type="coverage")
    db_session.commit()

    wp_a = WorkPackage(name="WP A", organization_id=org_a.id)
    db_session.add(wp_a)
    db_session.flush()
    gap_a2.work_packages.append(wp_a)
    db_session.commit()

    with tenant_ctx(org_a.id):
        not_addr = get_gaps_not_addressed(org_a.id)
        assert count_gaps(org_a.id, has_work_package=False) == len(not_addr)
        assert count_gaps(org_a.id, has_work_package=False) == 1
        assert count_gaps(org_a.id, has_work_package=True) == 1
        assert gap_a1.id in {g.id for g in not_addr}
    with tenant_ctx(org_b.id):
        assert count_gaps(org_b.id, has_work_package=False) == 1
        assert count_gaps(org_b.id, has_work_package=True) == 0