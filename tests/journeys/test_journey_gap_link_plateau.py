"""Journey: enterprise architect links gaps to plateaus and work packages.

The enterprise architect saves two gaps, links one to a plateau and one to a
work package, reloads the gap analysis page, and sees only the unlinked gap
under "Gaps Not Addressed by Any Work Package".
"""

import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def _make_plateau(db_session, org_id, name=None):
    from app.models.implementation_migration import Plateau

    suffix = uuid.uuid4().hex[:8]
    plateau = Plateau(
        name=name or f"Journey-Plateau-{suffix}",
        organization_id=org_id,
        sequence_order=1,
    )
    db_session.add(plateau)
    db_session.flush()
    return plateau


def _make_gap(db_session, org_id, name=None):
    from app.services.gap_register_service import create_gap

    suffix = uuid.uuid4().hex[:8]
    gap, _created = create_gap(org_id, name or f"Journey-Gap-{suffix}", gap_type="coverage")
    db_session.commit()
    return gap


def _make_work_package(db_session, org_id, name=None):
    from app.models.implementation_migration import WorkPackage

    suffix = uuid.uuid4().hex[:8]
    wp = WorkPackage(
        name=name or f"Journey-WP-{suffix}",
        organization_id=org_id,
    )
    db_session.add(wp)
    db_session.flush()
    return wp


def test_enterprise_architect_links_gaps_and_sees_not_addressed(app, client):
    """Save two gaps, link one to a plateau and one to a work package,
    reload, see only the unlinked one under 'not addressed'."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "JourneyLink")
        architect_id = make_user(
            db, org_id, "jlink", enterprise_role="enterprise_architect",
            role_name="Administrator",
        )

        # Create two gaps
        gap_a = _make_gap(db, org_id, "Journey Gap A - Plateau Linked")
        gap_b = _make_gap(db, org_id, "Journey Gap B - WP Linked")

        # Create a plateau
        plateau = _make_plateau(db, org_id, "Journey Plateau Alpha")

        # Create a work package
        wp = _make_work_package(db, org_id, "Journey WP Alpha")

    login(client, architect_id)

    # Link gap_a to the plateau via the API
    link_resp = client.post(
        "/capability-map/api/roadmap/gaps/%d/link-plateau/%d" % (gap_a.id, plateau.id),
    )
    assert link_resp.status_code == 200, (
        "Linking gap to plateau failed: %s" % link_resp.data[:200]
    )
    link_data = link_resp.get_json()
    assert link_data.get("success") is True

    # Link gap_b to the work package via the existing API
    wp_link_resp = client.post(
        "/capability-map/api/roadmap/gaps/%d/work-packages" % gap_b.id,
        json={"name": "Journey WP for Gap B"},
    )
    assert wp_link_resp.status_code == 201, (
        "Linking gap to work package failed: %s" % wp_link_resp.data[:200]
    )

    # Reload the gap analysis page
    gap_analysis_resp = client.get("/implementation/gap-analysis")
    assert gap_analysis_resp.status_code == 200
    html = gap_analysis_resp.data.decode("utf-8")

    # The gap linked to a plateau (gap_a) should appear under "not addressed"
    # because it has no work package
    assert "Journey Gap A - Plateau Linked" in html, (
        "Gap linked to plateau should appear on the gap analysis page"
    )

    # The gap linked to a work package (gap_b) should NOT appear under
    # "not addressed" because it has a work package
    # It may still appear in the main gaps table, but the "not addressed"
    # section should not include it
    assert "Journey Gap B - WP Linked" in html, (
        "Gap linked to work package should still appear in the main gaps table"
    )

    # Verify the not-addressed API returns only gap_a
    not_addr_resp = client.get("/capability-map/api/roadmap/gaps/not-addressed")
    assert not_addr_resp.status_code == 200
    not_addr_data = not_addr_resp.get_json()
    assert not_addr_data.get("success") is True
    not_addr_names = [g["name"] for g in not_addr_data.get("gaps", [])]

    assert "Journey Gap A - Plateau Linked" in not_addr_names, (
        "Gap linked only to a plateau should be in the not-addressed list"
    )
    assert "Journey Gap B - WP Linked" not in not_addr_names, (
        "Gap linked to a work package should NOT be in the not-addressed list"
    )
    assert len(not_addr_names) == 1, (
        "Only one gap should be not addressed, got %d: %s"
        % (len(not_addr_names), not_addr_names)
    )