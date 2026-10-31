"""Journeys: every company workspace has an Administrator, and the top bar never
promises administration the account does not hold.

Two people, two goals:

* a person registers a new company and must reach its settings and members pages
  as the Administrator, whatever persona they pick on the way in, and must not be
  shown a platform label or be let into an operator page;
* the Administrator of a two-person company must not be able to lose the role
  (by demotion, by removal, by a platform operator's toggle) while nobody else
  holds it, and the page says why in plain words.
"""

import re
import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

PLATFORM_LABEL = re.compile(r"platform[ _-]?admin", re.I)
REFUSAL = "Every organisation needs at least one Administrator"


def _sign_up(client, label):
    email = "%s-%s@example.com" % (label, uuid.uuid4().hex[:8])
    resp = client.post(
        "/account/register",
        data={
            "first_name": "Reg",
            "last_name": label.title(),
            "email": email,
            "password": "Correct-Horse-9",
            "password2": "Correct-Horse-9",
            # What the old sign-up persona picker used to send.
            "enterprise_role": "platform_admin",
            "persona_lens": "platform_admin",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:400]
    return email


def _page_chrome(html):
    """What the signed-in shell tells the person about administration."""
    return re.findall(r'data-testid="user-menu-[^"]*"', html)


def test_a_new_registrant_administers_their_company_and_sees_no_platform_label(app, client):
    from app import db
    from app.models.user import User

    email = _sign_up(client, "founder")

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        assert user is not None
        assert user.is_admin(), "the person who registers a company is its Administrator"
        assert user.is_org_admin
        assert not user.is_platform_admin, "registration never grants platform administration"

    # They choose the "Platform Admin" persona on the way in.
    chosen = client.post(
        "/dashboard/api/onboarding-complete",
        json={"enterprise_role": "platform_admin", "persona_lens": "platform_admin"},
    )
    assert chosen.status_code == 200

    # Their company's settings and members pages open, with their own company in them.
    settings = client.get("/admin/")
    assert settings.status_code == 200, "an Administrator reaches the company settings"
    team = client.get("/admin/team")
    assert team.status_code == 200, "an Administrator reaches the members page"
    team_html = team.get_data(as_text=True)
    assert email in team_html, "the members page lists the person who registered"

    # No page shows a platform label from the persona choice, and the menu has no operator entry.
    for path in ("/dashboard/", "/admin/", "/admin/team"):
        html = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert 'data-testid="user-menu-platform_admin"' not in html, path
        assert not PLATFORM_LABEL.search(
            re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
        ), "%s shows a platform label to an account without the permission" % path

    # No operator page opens for them.
    for path in ("/admin/organizations", "/platform/"):
        assert client.get(path).status_code in (403, 404), path

    with app.app_context():
        assert db.session.get(User, user.id).enterprise_role != "platform_admin"
        assert not db.session.get(User, user.id).is_platform_admin


def test_a_stored_platform_persona_does_not_make_a_platform_label(app, client):
    """Accounts created before the picker was removed carry the persona in
    ``enterprise_role``. It is a persona, not a permission, so no label follows."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "LegacyPersona")
        user_id = make_user(db, org_id, "legacy", enterprise_role="platform_admin", role_name="Administrator")

    login(client, user_id)
    for path in ("/dashboard/", "/admin/", "/admin/team"):
        html = client.get(path, follow_redirects=True).get_data(as_text=True)
        assert 'data-testid="user-menu-platform_admin"' not in html, path
        assert not PLATFORM_LABEL.search(
            re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
        ), "%s labels a persona as platform administration" % path
    assert client.get("/admin/organizations").status_code in (403, 404)


def _two_person_company(db, label):
    """An organisation with an Administrator and an Architect, both signed up in it."""
    from app.models.org_role import OrgRole

    org_id = make_org(db, label)
    admin_id = make_user(db, org_id, label.lower() + "admin", enterprise_role="enterprise_architect", role_name="Administrator")
    member_id = make_user(db, org_id, label.lower() + "member", enterprise_role="solution_architect", role_name="Architect")
    OrgRole.set_role(org_id, admin_id, "org_admin")
    OrgRole.set_role(org_id, member_id, "architect")
    db.session.commit()
    return org_id, admin_id, member_id


def test_the_only_administrator_cannot_demote_themselves(app, client):
    from app import db
    from app.models.org_role import OrgRole
    from app.models.user import User

    with app.app_context():
        org_id, admin_id, member_id = _two_person_company(db, "SoleDemote")

    login(client, admin_id)
    resp = client.post("/admin/team/role", data={"user_id": admin_id, "role": "architect"})
    assert resp.status_code == 409
    html = resp.get_data(as_text=True)
    assert html.count(REFUSAL) == 1, "the page says why the change was refused, once"
    assert "data-team-member-error" in html
    assert "data-testid=\"team-members\"" in html and "<table" in html, "the members list is still shown"

    with app.app_context():
        assert OrgRole.get_role(org_id, admin_id) == "org_admin"
        assert db.session.get(User, admin_id).is_admin()
    # The page still lists both people.
    assert client.get("/admin/team").status_code == 200


def test_a_company_with_two_administrators_can_demote_one(app, client):
    from app import db
    from app.models.org_role import OrgRole
    from app.models.user import User

    with app.app_context():
        org_id, admin_id, member_id = _two_person_company(db, "TwoAdmins")
        member = db.session.get(User, member_id)
        member.grant_org_admin()
        OrgRole.set_role(org_id, member_id, "org_admin")
        db.session.commit()

    login(client, admin_id)
    resp = client.post("/admin/team/role", data={"user_id": member_id, "role": "viewer"}, follow_redirects=True)
    assert resp.status_code == 200
    assert REFUSAL not in resp.get_data(as_text=True)
    with app.app_context():
        assert OrgRole.get_role(org_id, member_id) == "viewer"
        assert not db.session.get(User, member_id).is_admin()
        assert OrgRole.get_role(org_id, admin_id) == "org_admin"


def test_a_platform_operator_cannot_strip_a_companys_last_administrator(app, client):
    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User

    with app.app_context():
        operator_org = make_org(db, "OperatorHome")
        operator_id = make_user(db, operator_org, "operator", enterprise_role="enterprise_architect", role_name="Administrator")
        db.session.get(User, operator_id).is_platform_admin = True
        if Organization.query.filter_by(slug="default").first() is None:
            db.session.add(Organization(name="Default", slug="default"))
        org_id, admin_id, member_id = _two_person_company(db, "OperatorTarget")
        db.session.commit()

    login(client, operator_id)
    toggled = client.post("/admin/organizations/%d/users/%d/toggle-admin" % (org_id, admin_id), follow_redirects=True)
    assert toggled.status_code == 200
    assert REFUSAL in toggled.get_data(as_text=True)
    moved = client.post("/admin/organizations/%d/users/%d/remove" % (org_id, admin_id), follow_redirects=True)
    assert moved.status_code == 200
    assert REFUSAL in moved.get_data(as_text=True)

    with app.app_context():
        user = db.session.get(User, admin_id)
        assert user.organization_id == org_id
        assert user.is_admin()
        assert OrgRole.get_role(org_id, admin_id) == "org_admin"
        assert Role.query.filter_by(name="Administrator").first() is not None


def test_changing_the_role_of_the_only_administrator_is_refused_by_the_user_service(app):
    """The admin pages' role change and account delete share one rule."""
    from app import db
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService as Service
    from app.models.user import Role, User
    from app.services.administrator_guard import LastAdministratorError

    with app.app_context():
        org_id, admin_id, member_id = _two_person_company(db, "ServiceRule")
        admin = db.session.get(User, admin_id)
        with pytest.raises(LastAdministratorError):
            Service.change_user_role(admin, Role.query.filter_by(name="Architect").first())
        with pytest.raises(LastAdministratorError):
            Service.delete_user(admin)
        db.session.rollback()
        assert db.session.get(User, admin_id).is_admin()
