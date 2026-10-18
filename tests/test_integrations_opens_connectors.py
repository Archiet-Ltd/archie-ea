"""/integrations opens the connectors page where a connector can be added, and is offered only to administrators."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, role_name, enterprise_role="enterprise_architect", admin=False):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"integ-{role_name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Integ",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        role=Role.query.filter_by(name=role_name).first(),
    )
    if admin:
        user.is_org_admin = True
    db_session.add(user)
    db_session.flush()
    return user


def test_integrations_lands_on_the_connectors_page_with_the_add_form(db_session, make_org, client, login_as):
    admin = _user(db_session, make_org("integ"), "Administrator", admin=True)
    login_as(client, admin)
    first = client.get("/integrations", follow_redirects=False)
    assert first.status_code == 302 and first.headers["Location"].endswith("/admin/connectors")
    login_as(client, admin)
    page = client.get("/integrations", follow_redirects=True)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Add connector" in html or "Add a connector" in html or 'name="connector_type"' in html


def test_the_modules_directory_offers_integrations_only_to_administrators(app, db_session, make_org):
    from flask_login import login_user

    from app.modules.modules_directory.routes import visible_module_links

    org = make_org("integ-dir")
    admin = _user(db_session, org, "Administrator", admin=True)
    viewer = _user(db_session, org, "Viewer")

    def offered(user):
        with app.test_request_context("/"):
            login_user(user)
            return "main.integrations" in {link["endpoint"] for link in visible_module_links()}

    assert offered(admin) is True
    assert offered(viewer) is False
