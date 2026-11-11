"""Removing a member from an organisation removes only that organisation's membership.

The Administrator role belongs to the account and applies in the person's home organisation. Someone
invited into another organisation who is removed from it keeps the role where they belong; someone whose
home organisation it is loses it, as before. Two real organisations, both callers (an administrator based
in the organisation and an administrator invited into it).
"""

import uuid

import pytest


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="Removal %s %s" % (label, suffix), slug="removal-%s-%s" % (label, suffix))
    db_session.add(org)
    db_session.flush()
    return org


def _person(db_session, org, label, administrator):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    if Role.query.filter_by(name="Administrator").first() is None:
        Role.insert_roles()
    role = Role.query.filter_by(name="Administrator" if administrator else "Architect").one()
    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]), first_name=label, last_name="Person",
        organization_id=org.id, role=role, is_org_admin=administrator, confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    if administrator:
        OrgRole.set_role(org.id, user.id, "org_admin")
    return user


@pytest.fixture
def estate(db_session):
    """Alpha with a home administrator and an invited administrator; Bravo whose only administrator is invited into Alpha."""
    from app.models.org_role import OrgRole

    alpha = _org(db_session, "alpha")
    bravo = _org(db_session, "bravo")
    charlie = _org(db_session, "charlie")
    home_admin = _person(db_session, alpha, "homeadmin", True)
    invited_admin = _person(db_session, charlie, "invitedadmin", True)
    target = _person(db_session, bravo, "target", True)
    OrgRole.set_role(alpha.id, invited_admin.id, "org_admin", granted_by_id=home_admin.id)
    OrgRole.set_role(alpha.id, target.id, "viewer", granted_by_id=home_admin.id)
    db_session.commit()
    return {"alpha": alpha, "bravo": bravo, "home_admin": home_admin, "invited_admin": invited_admin, "target": target}


def _remove(app, client, login_as, caller, alpha, target):
    with app.app_context():
        login_as(client, caller)
        if caller.organization_id != alpha.id:
            switched = client.post("/account/switch-organization", data={"organization_id": str(alpha.id)}, follow_redirects=False)
            assert switched.status_code == 302
        return client.delete("/admin/team/member/%d" % target.id)


@pytest.mark.parametrize("caller_key", ["home_admin", "invited_admin"])
def test_removing_an_invited_person_leaves_their_home_administrator_role(app, db_session, client, login_as, estate, caller_key):
    from app.models.org_role import OrgRole
    from app.models.user import User

    target_id, bravo_id, alpha_id = estate["target"].id, estate["bravo"].id, estate["alpha"].id
    assert estate["target"].is_admin()
    response = _remove(app, client, login_as, estate[caller_key], estate["alpha"], estate["target"])
    assert response.status_code == 200, response.get_data(as_text=True)

    db_session.expire_all()
    assert OrgRole.query.filter_by(organization_id=alpha_id, user_id=target_id).first() is None
    target = db_session.get(User, target_id)
    assert target.is_admin(), "the account lost Administrator in its home organisation"
    from app.services.rbac_service import rbac_service

    assert rbac_service.is_org_admin(target, bravo_id)


def test_removing_a_person_from_their_home_organisation_still_revokes_administrator(app, db_session, client, login_as, estate):
    from app.models.org_role import OrgRole
    from app.models.user import User

    alpha = _org(db_session, "own")
    caller = _person(db_session, alpha, "ownadmin", True)
    other = _person(db_session, alpha, "otherhome", True)
    db_session.commit()
    other_id, alpha_id = other.id, alpha.id
    response = _remove(app, client, login_as, caller, alpha, other)
    assert response.status_code == 200, response.get_data(as_text=True)

    db_session.expire_all()
    assert OrgRole.query.filter_by(organization_id=alpha_id, user_id=other_id).first() is None
    assert not db_session.get(User, other_id).is_admin()
