"""Plateau-to-gap links, "gaps not addressed", and one gap count.

Tests the service functions added to gap_register_service.py:
  - link_gap_to_plateau / unlink_gap_from_plateau / get_plateau_gaps
  - get_gaps_not_addressed
  - count_gaps

Against the shared fixtures in tests/conftest.py per root CLAUDE.md.
"""

from __future__ import annotations

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
    """The roadmap API and the gap analysis page return the same gap count
    for the same organisation, because both call count_gaps()."""
    from app.models.user import Role, User
    from app.services.gap_register_service import create_gap

    Role.insert_roles()
    org = make_org("route-count-compare")
    create_gap(org.id, "Route Gap A", gap_type="coverage")
    create_gap(org.id, "Route Gap B", gap_type="coverage")
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

    # Both screens use count_gaps() — they must agree
    assert roadmap_total == 2, (
        "Roadmap API total_gaps should be 2, got %d" % roadmap_total
    )
    # The gap analysis page renders the count somewhere in the HTML
    assert "Recorded Gaps" in html
    assert str(roadmap_total) in html, (
        "Gap analysis page should contain the count %d in its rendered HTML" % roadmap_total
    )