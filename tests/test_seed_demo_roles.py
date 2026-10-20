"""The demo organisation has one administrator and one architect (DG-070).

The seeded Lantern Quay accounts were all read-only viewers, so /admin/team
refused everyone and no demo account could link a gap to a work package.
"""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company

ADMIN_EMAIL = "ivo.reed@lantern-quay.example.com"
ARCHITECT_EMAIL = "sage.itops@lantern-quay.example.com"
VIEWER_EMAIL = "demo@lantern-quay.example.com"


@pytest.fixture
def seeded(app, db_session):
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    return db_session.query(Organization).filter_by(slug="lantern-quay").one()


def _user(org, email):
    from app.models.user import User

    return User.query.filter_by(email=email, organization_id=org.id).one()


def test_exactly_one_seeded_account_is_the_organisation_administrator(seeded):
    from app.models.user import User

    admins = [u.email for u in User.query.filter_by(organization_id=seeded.id).all() if u.is_admin()]
    assert admins == [ADMIN_EMAIL]


def test_the_administrator_opens_the_team_page_and_a_viewer_is_refused(seeded, client, login_as):
    login_as(client, _user(seeded, ADMIN_EMAIL))
    resp = client.get("/admin/team")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/admin/team/invite" in html and "/admin/team/role" in html

    login_as(client, _user(seeded, VIEWER_EMAIL))
    assert client.get("/admin/team").status_code == 403


def test_the_architect_account_is_not_refused_on_the_gap_link_route(seeded, client, login_as):
    from app.models.implementation_migration import Gap
    from app.models.org_role import OrgRole
    from app.models.unified_work_package import UnifiedWorkPackage

    architect = _user(seeded, ARCHITECT_EMAIL)
    assert OrgRole.get_role(seeded.id, architect.id) == "architect"
    assert architect.role.name == "Architect"
    assert not architect.is_admin()

    gap = Gap.query.filter_by(organization_id=seeded.id).first()
    wp = UnifiedWorkPackage.query.filter_by(organization_id=seeded.id).first()
    url = f"/capability-map/api/roadmap/gaps/{gap.id}/link-work-package/{wp.id}"

    login_as(client, _user(seeded, VIEWER_EMAIL))
    assert client.post(url).status_code == 403
    login_as(client, architect)
    assert client.post(url).status_code == 200


def test_the_demo_viewer_and_the_other_accounts_stay_viewers(seeded):
    from app.models.org_role import OrgRole
    from app.models.user import User

    assert _user(seeded, VIEWER_EMAIL).role.name == "Viewer"
    others = [
        u for u in User.query.filter_by(organization_id=seeded.id).all()
        if u.email not in (ADMIN_EMAIL, ARCHITECT_EMAIL)
    ]
    assert others and all(u.role.name == "Viewer" for u in others)
    assert all(OrgRole.get_role(seeded.id, u.id) in (None, "viewer") for u in others)


def test_seeding_twice_keeps_the_same_roles(seeded):
    # seed_demo_company() ends its tenant scope by removing the session, so
    # the organisation is looked up again afterwards.
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    org = Organization.query.filter_by(slug="lantern-quay").one()
    assert _user(org, ADMIN_EMAIL).is_admin()
    assert _user(org, ARCHITECT_EMAIL).role.name == "Architect"
