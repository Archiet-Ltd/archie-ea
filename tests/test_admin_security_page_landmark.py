"""/admin/security must render inside the same landmark structure and admin
chrome as every other admin page.

app/templates/admin/security.html extended layouts/base.html (a legacy
layout kept only for standalone pages without the admin sidebar) instead of
layouts/admin_base.html (what every other authenticated admin page uses).
Two consequences: the page's content sat in a plain <div id="main-content">
with no <main> landmark for assistive technology, and the legacy layout's
nav macro links to an endpoint ("main.about") that is not registered
anywhere in the application, which app's global url_for override degrades
to an inert href="#" rather than raising — so the page always carried one
dead, unlabelled link.
"""

import uuid


def _make_platform_admin(db_session, org):
    from app.models.user import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    user = User(
        email=f"secpage-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Sec",
        last_name="Admin",
        organization_id=org.id,
        role=role,
        is_platform_admin=True,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex  # generated, not a real credential
    db_session.add(user)
    db_session.flush()
    return user


def test_admin_security_page_has_a_main_landmark(app, db_session, make_org, login_as):
    org = make_org("secpage")
    admin = _make_platform_admin(db_session, org)
    db_session.commit()
    client = app.test_client()
    login_as(client, admin)

    resp = client.get("/admin/security")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<main" in body, "/admin/security has no <main> landmark"


def test_admin_security_page_has_no_dead_about_link(app, db_session, make_org, login_as):
    org = make_org("secpage")
    admin = _make_platform_admin(db_session, org)
    db_session.commit()
    client = app.test_client()
    login_as(client, admin)

    resp = client.get("/admin/security")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="#">' not in body, (
        "/admin/security still carries a dead href=\"#\" link"
    )
