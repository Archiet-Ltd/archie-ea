"""Browser journey test: gap register enforces two-organisation isolation.

This test simulates two organisations (Org A and Org B) each creating gaps,
then verifies that each organisation only sees its own gaps in the gap register.
"""

import pytest

from tests.test_ba_tenant_and_authz import (
    _cleanup_ids,
    _make_org_id,
    _make_user_id,
)


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


def _make_gap(app, org_id, name, gap_type="technology", severity="high"):
    """Create a gap in the gap register for the given organisation."""
    from app import db
    from app.services.gap_register_service import create_gap

    with app.app_context():
        gap, _ = create_gap(
            organization_id=org_id,
            name=name,
            gap_type=gap_type,
            severity=severity,
            description=f"Test gap: {name}",
        )
        db.session.commit()
        return gap.id


def _gap_count_for_org(app, org_id):
    """Count gaps visible to an organisation via the gap register query."""
    from app import db
    from app.services.gap_register_service import gap_register_query

    with app.app_context():
        return gap_register_query(org_id).count()


def test_gap_register_query_isolation_direct(app):
    """
    Direct test of gap_register_query isolation without browser.

    Verifies the gap_register_service.query correctly filters by organisation.
    """
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.gap_register_service import gap_register_query

    with app.app_context():
        org_a = _make_org_id(db, "OrgA_Direct")
        org_b = _make_org_id(db, "OrgB_Direct")

    try:
        # Create gaps for each org
        _make_gap(app, org_a, "Direct OrgA Gap 1", "technology", "high")
        _make_gap(app, org_a, "Direct OrgA Gap 2", "skill", "medium")
        _make_gap(app, org_b, "Direct OrgB Gap 1", "compliance", "critical")

        # Verify isolation at query level
        with app.app_context():
            org_a_gaps = gap_register_query(org_a).all()
            org_b_gaps = gap_register_query(org_b).all()

            assert len(org_a_gaps) == 2, f"Org A query should return 2 gaps, got {len(org_a_gaps)}"
            assert len(org_b_gaps) == 1, f"Org B query should return 1 gap, got {len(org_b_gaps)}"

            # Verify gap names match expected org
            org_a_names = {g.name for g in org_a_gaps}
            org_b_names = {g.name for g in org_b_gaps}

            assert "Direct OrgA Gap 1" in org_a_names
            assert "Direct OrgA Gap 2" in org_a_names
            assert "Direct OrgB Gap 1" in org_b_names

            # Cross-check: Org A should not see Org B's gaps
            assert "Direct OrgB Gap 1" not in org_a_names
            # Cross-check: Org B should not see Org A's gaps
            assert "Direct OrgA Gap 1" not in org_b_names
            assert "Direct OrgA Gap 2" not in org_b_names

    finally:
        with app.app_context():
            _cleanup_ids(db, Organization, [org_a, org_b])