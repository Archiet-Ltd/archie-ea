"""Plateau-to-gap links, "gaps not addressed", and one gap count.

Tests the service functions added to gap_register_service.py:
  - link_gap_to_plateau / unlink_gap_from_plateau / get_plateau_gaps
  - get_gaps_not_addressed
  - count_gaps

Against the shared fixtures in tests/conftest.py per root CLAUDE.md.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_plateau(db_session, org, name=None):
    from app.models.implementation_migration import Plateau

    suffix = uuid.uuid4().hex[:8]
    plateau = Plateau(
        name=name or f"Plateau-{suffix}",
        organization_id=org.id,
        sequence_order=1,
    )
    db_session.add(plateau)
    db_session.flush()
    return plateau


def _make_gap(db_session, org, name=None):
    from app.services.gap_register_service import create_gap

    suffix = uuid.uuid4().hex[:8]
    gap, _created = create_gap(org.id, name or f"Gap-{suffix}", gap_type="coverage")
    db_session.commit()
    return gap


def _make_work_package(db_session, org, name=None):
    from app.models.implementation_migration import WorkPackage

    suffix = uuid.uuid4().hex[:8]
    wp = WorkPackage(
        name=name or f"WP-{suffix}",
        organization_id=org.id,
    )
    db_session.add(wp)
    db_session.flush()
    return wp


# ── link_gap_to_plateau ──────────────────────────────────────────────────────


def test_link_gap_to_plateau(db_session, make_org):
    from app.services.gap_register_service import (
        link_gap_to_plateau,
        get_plateau_gaps,
    )

    org = make_org("link-test")
    gap = _make_gap(db_session, org)
    plateau = _make_plateau(db_session, org)

    result = link_gap_to_plateau(gap.id, plateau.id, org.id)
    db_session.commit()

    assert result.id == gap.id
    linked = get_plateau_gaps(plateau.id, org.id)
    assert len(linked) == 1
    assert linked[0].id == gap.id


def test_link_gap_to_plateau_cross_org_refused(db_session, make_org):
    from app.services.gap_register_service import link_gap_to_plateau

    org_a = make_org("link-a")
    org_b = make_org("link-b")
    gap = _make_gap(db_session, org_a)
    plateau = _make_plateau(db_session, org_b)

    # gap in org_a, plateau in org_b — linking from org_a's perspective
    # fails because the plateau is not in org_a
    with pytest.raises(ValueError, match="Plateau not found in this organisation"):
        link_gap_to_plateau(gap.id, plateau.id, org_a.id)

    # linking from org_b's perspective fails because the gap is not in org_b
    with pytest.raises(ValueError, match="Gap not found in this organisation"):
        link_gap_to_plateau(gap.id, plateau.id, org_b.id)


def test_link_gap_to_plateau_already_linked_raises(db_session, make_org):
    from app.services.gap_register_service import link_gap_to_plateau

    org = make_org("link-dupe")
    gap = _make_gap(db_session, org)
    plateau = _make_plateau(db_session, org)

    link_gap_to_plateau(gap.id, plateau.id, org.id)
    db_session.commit()

    with pytest.raises(ValueError, match="already linked"):
        link_gap_to_plateau(gap.id, plateau.id, org.id)


def test_link_gap_to_plateau_nonexistent_gap_raises(db_session, make_org):
    from app.services.gap_register_service import link_gap_to_plateau

    org = make_org("link-miss")
    plateau = _make_plateau(db_session, org)

    with pytest.raises(ValueError, match="Gap not found"):
        link_gap_to_plateau(999999, plateau.id, org.id)


def test_link_gap_to_plateau_nonexistent_plateau_raises(db_session, make_org):
    from app.services.gap_register_service import link_gap_to_plateau

    org = make_org("link-miss-p")
    gap = _make_gap(db_session, org)

    with pytest.raises(ValueError, match="Plateau not found"):
        link_gap_to_plateau(gap.id, 999999, org.id)


# ── unlink_gap_from_plateau ──────────────────────────────────────────────────


def test_unlink_gap_from_plateau(db_session, make_org):
    from app.services.gap_register_service import (
        link_gap_to_plateau,
        unlink_gap_from_plateau,
        get_plateau_gaps,
    )

    org = make_org("unlink-test")
    gap = _make_gap(db_session, org)
    plateau = _make_plateau(db_session, org)

    link_gap_to_plateau(gap.id, plateau.id, org.id)
    db_session.commit()

    result = unlink_gap_from_plateau(gap.id, plateau.id, org.id)
    db_session.commit()

    assert result.id == gap.id
    linked = get_plateau_gaps(plateau.id, org.id)
    assert len(linked) == 0


def test_unlink_gap_from_plateau_not_linked_raises(db_session, make_org):
    from app.services.gap_register_service import unlink_gap_from_plateau

    org = make_org("unlink-none")
    gap = _make_gap(db_session, org)
    plateau = _make_plateau(db_session, org)

    with pytest.raises(ValueError, match="not linked"):
        unlink_gap_from_plateau(gap.id, plateau.id, org.id)


def test_unlink_gap_from_plateau_cross_org_refused(db_session, make_org):
    from app.services.gap_register_service import (
        link_gap_to_plateau,
        unlink_gap_from_plateau,
    )

    org_a = make_org("unlink-a")
    org_b = make_org("unlink-b")
    gap = _make_gap(db_session, org_a)
    plateau = _make_plateau(db_session, org_a)

    link_gap_to_plateau(gap.id, plateau.id, org_a.id)
    db_session.commit()

    with pytest.raises(ValueError, match="Gap not found in this organisation"):
        unlink_gap_from_plateau(gap.id, plateau.id, org_b.id)


# ── get_plateau_gaps ─────────────────────────────────────────────────────────


def test_get_plateau_gaps_returns_empty_for_unlinked(db_session, make_org):
    from app.services.gap_register_service import get_plateau_gaps

    org = make_org("empty-plat")
    plateau = _make_plateau(db_session, org)

    linked = get_plateau_gaps(plateau.id, org.id)
    assert linked == []


def test_get_plateau_gaps_cross_org_refused(db_session, make_org):
    from app.services.gap_register_service import get_plateau_gaps

    org_a = make_org("plat-a")
    org_b = make_org("plat-b")
    plateau = _make_plateau(db_session, org_a)

    with pytest.raises(ValueError, match="Plateau not found"):
        get_plateau_gaps(plateau.id, org_b.id)


# ── get_gaps_not_addressed ───────────────────────────────────────────────────


def test_gaps_not_addressed_returns_unlinked_gaps(db_session, make_org):
    from app.services.gap_register_service import (
        create_gap,
        get_gaps_not_addressed,
    )

    org = make_org("not-addr")
    gap_a, _ = create_gap(org.id, "Unaddressed Gap A", gap_type="coverage")
    gap_b, _ = create_gap(org.id, "Unaddressed Gap B", gap_type="coverage")
    db_session.commit()

    unlinked = get_gaps_not_addressed(org.id)
    ids = {g.id for g in unlinked}
    assert gap_a.id in ids
    assert gap_b.id in ids


def test_gaps_not_addressed_excludes_gaps_with_work_package(db_session, make_org):
    from app.services.gap_register_service import (
        create_gap,
        get_gaps_not_addressed,
    )

    org = make_org("not-addr-wp")
    gap, _ = create_gap(org.id, "Addressed Gap", gap_type="coverage")
    db_session.commit()

    wp = _make_work_package(db_session, org)
    gap.work_packages.append(wp)
    db_session.commit()

    unlinked = get_gaps_not_addressed(org.id)
    ids = {g.id for g in unlinked}
    assert gap.id not in ids


def test_gaps_not_addressed_two_orgs_isolation(db_session, make_org):
    """B's gaps and work packages never appear for A."""
    from app.services.gap_register_service import (
        create_gap,
        get_gaps_not_addressed,
    )

    org_a = make_org("not-addr-a")
    org_b = make_org("not-addr-b")

    gap_a, _ = create_gap(org_a.id, "A's Gap", gap_type="coverage")
    gap_b, _ = create_gap(org_b.id, "B's Gap", gap_type="coverage")
    db_session.commit()

    # Link B's gap to a work package
    wp_b = _make_work_package(db_session, org_b)
    gap_b.work_packages.append(wp_b)
    db_session.commit()

    # A should see its gap as unaddressed (B's WP doesn't affect A)
    unlinked_a = get_gaps_not_addressed(org_a.id)
    ids_a = {g.id for g in unlinked_a}
    assert gap_a.id in ids_a
    assert gap_b.id not in ids_a

    # B should see no unaddressed gaps (its gap has a WP)
    unlinked_b = get_gaps_not_addressed(org_b.id)
    ids_b = {g.id for g in unlinked_b}
    assert gap_b.id not in ids_b


# ── count_gaps ───────────────────────────────────────────────────────────────


def test_count_gaps_returns_zero_for_empty_org(db_session, make_org):
    from app.services.gap_register_service import count_gaps

    org = make_org("empty-count")
    assert count_gaps(org.id) == 0


def test_count_gaps_counts_only_own_org_gaps(db_session, make_org):
    from app.services.gap_register_service import count_gaps, create_gap

    org_a = make_org("count-a")
    org_b = make_org("count-b")

    create_gap(org_a.id, "A's Gap", gap_type="coverage")
    create_gap(org_b.id, "B's Gap", gap_type="coverage")
    create_gap(org_b.id, "B's Other Gap", gap_type="coverage")
    db_session.commit()

    assert count_gaps(org_a.id) == 1
    assert count_gaps(org_b.id) == 2


def test_count_gaps_excludes_plateau_transition_gaps(db_session, make_org, tenant_ctx):
    from app.services.gap_register_service import count_gaps, create_gap
    from app.models.implementation_migration import Gap, GAP_KIND_PLATEAU_TRANSITION

    org = make_org("count-pt")
    create_gap(org.id, "Regular Gap", gap_type="coverage")
    db_session.commit()

    with tenant_ctx(org.id):
        pt_gap = Gap(
            name="Plateau Transition Gap",
            gap_kind=GAP_KIND_PLATEAU_TRANSITION,
            organization_id=org.id,
            originating_plateau_id=None,
            target_plateau_id=None,
        )
        # Override the validator by setting both plateau IDs
        arch = _make_plateau(db_session, org)
        pt_gap.originating_plateau_id = arch.id
        pt_gap.target_plateau_id = arch.id
        db_session.add(pt_gap)
        db_session.commit()

    assert count_gaps(org.id) == 1  # only the regular gap


# ── Roadmap and gap analysis return the same count ───────────────────────────


def test_roadmap_and_gap_analysis_count_agree(db_session, make_org):
    """The count_gaps function is the single source of truth for both screens."""
    from app.services.gap_register_service import count_gaps, create_gap

    org = make_org("agree-test")
    create_gap(org.id, "Gap One", gap_type="coverage")
    create_gap(org.id, "Gap Two", gap_type="coverage")
    db_session.commit()

    # Both screens call count_gaps(org.id) — they must agree
    roadmap_count = count_gaps(org.id)
    gap_analysis_count = count_gaps(org.id)
    assert roadmap_count == gap_analysis_count
    assert roadmap_count == 2


# ── Two-organisation tests for save, link and "not addressed" ─────────────────


def test_two_org_save_link_and_not_addressed(db_session, make_org):
    """Full round-trip: create gaps in two orgs, link one to a plateau,
    link one to a work package, verify isolation."""
    from app.services.gap_register_service import (
        create_gap,
        link_gap_to_plateau,
        get_gaps_not_addressed,
        count_gaps,
        get_plateau_gaps,
    )

    org_a = make_org("full-a")
    org_b = make_org("full-b")

    # Create gaps
    gap_a1, _ = create_gap(org_a.id, "A Gap 1", gap_type="coverage")
    gap_a2, _ = create_gap(org_a.id, "A Gap 2", gap_type="coverage")
    gap_b1, _ = create_gap(org_b.id, "B Gap 1", gap_type="coverage")
    db_session.commit()

    # Create plateaus
    plat_a = _make_plateau(db_session, org_a)
    plat_b = _make_plateau(db_session, org_b)

    # Link A's gap to A's plateau
    link_gap_to_plateau(gap_a1.id, plat_a.id, org_a.id)
    db_session.commit()

    # Link B's gap to B's plateau
    link_gap_to_plateau(gap_b1.id, plat_b.id, org_b.id)
    db_session.commit()

    # Link A's gap to a work package
    wp_a = _make_work_package(db_session, org_a)
    gap_a2.work_packages.append(wp_a)
    db_session.commit()

    # Verify counts
    assert count_gaps(org_a.id) == 2
    assert count_gaps(org_b.id) == 1

    # Verify plateau gaps
    plat_a_gaps = get_plateau_gaps(plat_a.id, org_a.id)
    assert len(plat_a_gaps) == 1
    assert plat_a_gaps[0].id == gap_a1.id

    plat_b_gaps = get_plateau_gaps(plat_b.id, org_b.id)
    assert len(plat_b_gaps) == 1
    assert plat_b_gaps[0].id == gap_b1.id

    # Verify "not addressed" — A's gap_a1 has no WP, gap_a2 has one
    not_addr_a = get_gaps_not_addressed(org_a.id)
    not_addr_a_ids = {g.id for g in not_addr_a}
    assert gap_a1.id in not_addr_a_ids  # linked to plateau but no WP
    assert gap_a2.id not in not_addr_a_ids  # has a WP

    # B's gap has no WP
    not_addr_b = get_gaps_not_addressed(org_b.id)
    not_addr_b_ids = {g.id for g in not_addr_b}
    assert gap_b1.id in not_addr_b_ids

    # Cross-org: B's gap not in A's list
    assert gap_b1.id not in not_addr_a_ids
    # A's gaps not in B's list
    assert gap_a1.id not in not_addr_b_ids
    assert gap_a2.id not in not_addr_b_ids


# ── Route-level count comparison ─────────────────────────────────────────────


def test_roadmap_api_and_gap_analysis_page_counts_agree(
    db_session, app, client, login_as, make_org,
):
    """The roadmap API and the gap analysis page report the same gap count
    for the same organisation: the roadmap API counts the derived analysis
    list it returns, and the page metric is the one register count."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import Role, User
    from app.services.gap_register_service import create_gap

    Role.insert_roles()
    org = make_org("route-count-compare")
    create_gap(org.id, "Route Gap A", gap_type="coverage")
    create_gap(org.id, "Route Gap B", gap_type="coverage")
    # Two uncovered business capabilities so the roadmap gap analysis
    # derives exactly two gaps for this organisation.
    db_session.add(BusinessCapability(
        name="Route Compare Capability 1", code="ROUTE-COMPARE-1",
        organization_id=org.id, level=1,
    ))
    db_session.add(BusinessCapability(
        name="Route Compare Capability 2", code="ROUTE-COMPARE-2",
        organization_id=org.id, level=1,
    ))
    db_session.commit()

    user = User(
        email="route-count@example.com",
        first_name="RouteCount",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role:
        user.role = role
    db_session.commit()

    login_as(client, user)

    # Roadmap API
    roadmap_resp = client.get("/capability-map/api/roadmap/gaps")
    assert roadmap_resp.status_code == 200
    roadmap_data = roadmap_resp.get_json()
    assert roadmap_data.get("success") is True
    roadmap_total = roadmap_data.get("statistics", {}).get("total_gaps", -1)

    # Gap analysis page — check the rendered HTML for the count
    gap_analysis_resp = client.get("/enterprise/implementation/gap-analysis")
    assert gap_analysis_resp.status_code == 200
    html = gap_analysis_resp.data.decode("utf-8")

    # Each screen counts the population it lists; for this organisation
    # both populations have exactly two gaps, so they agree.
    assert roadmap_total == 2, (
        "Roadmap API total_gaps should be 2, got %d" % roadmap_total
    )
    assert len(roadmap_data.get("gaps", [])) == roadmap_total
    # Parse the Recorded Gaps card value from the rendered HTML
    match = re.search(
        r"data-testid=\"gap-stat-recorded-value\">\s*([0-9]+)\s*<",
        html,
    )
    assert match is not None, "Recorded gaps metric not found in rendered page"
    recorded = int(match.group(1))
    assert recorded == roadmap_total, (
        "Recorded Gaps metric (%d) must equal the roadmap API total (%d)" % (recorded, roadmap_total)
    )


# ── Switched-session tests: scope by the active organisation ──────────────────


def test_switched_session_gap_analysis_page_shows_active_org(
    app, db_session, make_org, login_as, client,
):
    """When the session is switched to organisation B, the gap analysis page
    shows B's Recorded Gaps metric and not-addressed list, never A's."""
    from app.models.org_role import OrgRole
    from app.models.user import Role, User
    from app.services.gap_register_service import create_gap

    Role.insert_roles()

    org_a = make_org("switch-a")
    org_b = make_org("switch-b")

    # User whose home organisation is A
    user = User(
        email="switch-user@example.com",
        first_name="SwitchUser",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role:
        user.role = role
    db_session.flush()

    # Grant access to B via OrgRole
    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)
    db_session.commit()

    # Create gaps: 2 in A, 3 in B
    create_gap(org_a.id, "A Gap 1", gap_type="coverage")
    create_gap(org_a.id, "A Gap 2", gap_type="coverage")
    create_gap(org_b.id, "B Gap 1", gap_type="coverage")
    create_gap(org_b.id, "B Gap 2", gap_type="coverage")
    create_gap(org_b.id, "B Gap 3", gap_type="coverage")
    db_session.commit()

    # Login as user (home org A) then switch session to B
    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = org_b.id

    # GET gap analysis page
    resp = client.get("/enterprise/implementation/gap-analysis")
    assert resp.status_code == 200, resp.data[:200]
    html = resp.data.decode("utf-8")

    # Parse the Recorded Gaps card
    match = re.search(
        r"data-testid=\"gap-stat-recorded-value\">\s*([0-9]+)\s*<",
        html,
    )
    assert match is not None, "Recorded gaps metric not found"
    recorded = int(match.group(1))
    assert recorded == 3, (
        "Switched to B: Recorded Gaps should be 3 (B's gaps), got %d" % recorded
    )

    # Not-addressed list should contain B's gaps, not A's
    assert "B Gap 1" in html
    assert "B Gap 2" in html
    assert "B Gap 3" in html
    assert "A Gap 1" not in html, "A's gaps must not appear when switched to B"
    assert "A Gap 2" not in html, "A's gaps must not appear when switched to B"


def test_switched_session_gap_link_and_unlink_use_active_org(
    app, db_session, make_org, login_as, client,
):
    """When the session is switched to organisation B, POST link and unlink
    act on B's rows and never touch A's."""
    from app.models.org_role import OrgRole
    from app.models.user import Role, User
    from app.services.gap_register_service import create_gap
    from flask import g

    Role.insert_roles()

    org_a = make_org("switch-link-a")
    org_b = make_org("switch-link-b")

    user = User(
        email="switch-link-user@example.com",
        first_name="SwitchLinkUser",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role:
        user.role = role
    db_session.flush()

    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)

    # Create gaps and plateaus in both orgs
    create_gap(org_a.id, "A Link Gap", gap_type="coverage")
    gap_b = create_gap(org_b.id, "B Link Gap", gap_type="coverage")[0]
    plat_a = _make_plateau(db_session, org_a, "A Plateau")
    plat_b = _make_plateau(db_session, org_b, "B Plateau")
    db_session.commit()

    # Login as user (home org A) then switch session to B
    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = org_b.id

    # Link B's gap to B's plateau
    link_resp = client.post(
        f"/capability-map/api/roadmap/gaps/{gap_b.id}/link-plateau/{plat_b.id}"
    )
    assert link_resp.status_code == 200, link_resp.data[:200]
    link_data = link_resp.get_json()
    assert link_data.get("success") is True

    # Clear g.current_org_id so direct queries are not tenant-scoped
    for cached in ("current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)

    # Verify the link exists for B
    from app.services.gap_register_service import get_plateau_gaps

    b_linked = get_plateau_gaps(plat_b.id, org_b.id)
    assert len(b_linked) == 1
    assert b_linked[0].id == gap_b.id

    # Verify A's gap is NOT linked to B's plateau
    a_linked = get_plateau_gaps(plat_a.id, org_a.id)
    assert len(a_linked) == 0

    # Unlink B's gap from B's plateau
    unlink_resp = client.post(
        f"/capability-map/api/roadmap/gaps/{gap_b.id}/unlink-plateau/{plat_b.id}"
    )
    assert unlink_resp.status_code == 200, unlink_resp.data[:200]
    unlink_data = unlink_resp.get_json()
    assert unlink_data.get("success") is True

    # Verify the link is removed for B
    b_linked_after = get_plateau_gaps(plat_b.id, org_b.id)
    assert len(b_linked_after) == 0


def test_switched_session_roadmap_gaps_api_shows_active_org(
    app, db_session, make_org, login_as, client,
):
    """When the session is switched to organisation B, the roadmap gaps API
    returns B's totals, never A's."""
    from app.models.business_capabilities import BusinessCapability, ApplicationCapabilityCoverage
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()

    org_a = make_org("switch-api-a")
    org_b = make_org("switch-api-b")

    user = User(
        email="switch-api-user@example.com",
        first_name="SwitchApiUser",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role:
        user.role = role
    db_session.flush()

    OrgRole.set_role(org_b.id, user.id, "architect", granted_by_id=user.id)

    # Create business capabilities: 2 uncovered in A, 3 uncovered in B
    # The roadmap API derives coverage gaps from capabilities with no apps
    for i in range(2):
        cap = BusinessCapability(
            name=f"API A Cap {i}", code=f"API-A-CAP-{i}",
            organization_id=org_a.id, level=1,
        )
        db_session.add(cap)
    for i in range(3):
        cap = BusinessCapability(
            name=f"API B Cap {i}", code=f"API-B-CAP-{i}",
            organization_id=org_b.id, level=1,
        )
        db_session.add(cap)
    db_session.commit()

    # Login as user (home org A) then switch session to B
    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = org_b.id

    # GET roadmap gaps API
    resp = client.get("/capability-map/api/roadmap/gaps")
    assert resp.status_code == 200, resp.data[:200]
    data = resp.get_json()
    assert data.get("success") is True

    stats = data.get("statistics", {})
    total = stats.get("total_gaps", -1)
    assert total == 3, (
        "Switched to B: roadmap API total_gaps should be 3 (B's uncovered "
        f"capabilities), got {total}"
    )

    # B's gaps appear in the list, A's do not
    gap_names = [g.get("name", "") for g in data.get("gaps", [])]
    assert "API B Cap 0" in gap_names
    assert "API B Cap 1" in gap_names
    assert "API B Cap 2" in gap_names
    assert "API A Cap 0" not in gap_names, "A's gaps must not appear when switched to B"
    assert "API A Cap 1" not in gap_names, "A's gaps must not appear when switched to B"