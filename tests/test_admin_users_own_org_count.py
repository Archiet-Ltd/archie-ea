"""DG-126: an organisation administrator sees only their own organisation's
users and counts. Organisation A has three users, organisation B has five;
A's administrator must never see 8 or a "platform-wide" total.
"""

import re
import uuid

import pytest


def _admin_role(db_session):
    from app.models.user import Permission, Role

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    elif role.permissions is None or (role.permissions & Permission.ADMINISTER) != Permission.ADMINISTER:
        role.permissions = Permission.ADMINISTER
    return role


def _user(db_session, org, label, *, admin=False, platform_admin=False, confirmed=True):
    from app.models.user import User

    u = User(
        email=f"dg126-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.title(),
        last_name="Dg",
        organization_id=org.id,
        confirmed=confirmed,
        is_org_admin=admin,
        is_platform_admin=platform_admin,
    )
    if admin or platform_admin:
        u.role = _admin_role(db_session)
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture
def two_orgs(db_session, make_org):
    """A: admin + two members = 3 users. B: five users."""
    org_a = make_org("dg126a")
    org_b = make_org("dg126b")
    # The Community plan admits three people; B holds five, so both sit on
    # the Startup plan (ten people).
    org_a.plan = "startup"
    org_b.plan = "startup"
    db_session.flush()
    admin_a = _user(db_session, org_a, "admina", admin=True)
    _user(db_session, org_a, "a1")
    _user(db_session, org_a, "a2", confirmed=False)
    for i in range(5):
        _user(db_session, org_b, f"b{i}")
    db_session.commit()
    return org_a, org_b, admin_a


def _get(app, client, login_as, user, url):
    with app.app_context():
        login_as(client, user)
        return client.get(url)


def test_users_page_counts_only_own_organisation(app, client, login_as, two_orgs):
    org_a, _org_b, admin_a = two_orgs
    resp = _get(app, client, login_as, admin_a, "/admin/users")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "platform-wide" not in html and "platform wide" not in html
    assert org_a.name in html
    m = re.search(r"\(\s*(\d+) users?\s*\)", html)
    assert m, "the users page must state the organisation's user count"
    assert m.group(1) == "3"
    assert not re.search(r"\b3 of \d+ users\b", html)


def test_admin_overview_user_total_is_own_organisation(app, client, login_as, two_orgs):
    _a, _b, admin_a = two_orgs
    for url in ("/admin/", "/admin/dashboard"):
        resp = _get(app, client, login_as, admin_a, url)
        assert resp.status_code == 200, url
        html = resp.get_data(as_text=True)
        m = re.search(r'text-sm font-bold text-foreground">\s*(\d+)', html)
        assert m, url
        assert m.group(1) == "3", f"{url} showed {m.group(1)}"


def test_platform_admin_still_sees_platform_totals(app, client, login_as, db_session, two_orgs):
    org_a, org_b, _admin = two_orgs
    pa = _user(db_session, org_a, "platform", admin=True, platform_admin=True)
    db_session.commit()
    resp = _get(app, client, login_as, pa, "/admin/organizations")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert org_a.name in html and org_b.name in html


def test_data_maturity_summary_counts_only_own_solutions(app, client, login_as, db_session, two_orgs, tenant_ctx):
    from app.models.solution_models import Solution

    org_a, org_b, admin_a = two_orgs
    with tenant_ctx(org_a.id):
        db_session.add(Solution(name="Dg126 Alpha Solution", organization_id=org_a.id))
        db_session.commit()
    with tenant_ctx(org_b.id):
        for i in range(4):
            db_session.add(Solution(name=f"Dg126 Beta Solution {i}", organization_id=org_b.id))
        db_session.commit()
    resp = _get(app, client, login_as, admin_a, "/admin/dashboard")
    html = resp.get_data(as_text=True)
    assert "Dg126 Beta Solution" not in html
