"""The model registry under /auto-dashboard is platform tooling, not a customer feature.

Its pages render placeholders; the customer-facing job (browse every element type with real
counts) is the Element Catalog. No customer persona may reach the registry from a menu, the
module directory or an AI chat suggestion, and the routes themselves answer 403 to anyone who
is not a platform admin.
"""

from __future__ import annotations

import uuid

import pytest

REGISTRY_PATHS = [
    "/auto-dashboard/",
    "/auto-dashboard/registry",
    "/auto-dashboard/business-capability",
    "/auto-dashboard/business-capability/1",
    "/auto-dashboard/workflow-pipeline",
]


def _user(db_session, org, *, platform):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"autodash-{uuid.uuid4().hex[:6]}@example.test", first_name="Auto", last_name="Dash",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.parametrize("path", REGISTRY_PATHS)
def test_anonymous_is_not_served(app, db_session, path):
    resp = app.test_client().get(path)
    assert resp.status_code in (301, 302, 401), path


@pytest.mark.parametrize("path", REGISTRY_PATHS)
def test_tenant_admin_is_refused(app, db_session, make_org, login_as, path):
    tenant_admin = _user(db_session, make_org("autodash"), platform=False)
    db_session.commit()
    client = app.test_client()
    login_as(client, tenant_admin)
    assert client.get(path).status_code == 403, path


def test_platform_admin_still_reaches_the_registry(app, db_session, make_org, login_as):
    platform_admin = _user(db_session, make_org("autodash"), platform=True)
    db_session.commit()
    client = app.test_client()
    login_as(client, platform_admin)
    assert client.get("/auto-dashboard/registry").status_code == 200


def test_no_customer_navigation_points_at_the_registry():
    """Every navigation source: sidebar zones, nav configs, module directory."""
    from app.config import navigation_registry, navigation_sections_v2, navigation_sections_v3_hardened
    from app.modules.modules_directory import routes as modules_directory
    from app.utils import role_access

    for module in (navigation_registry, navigation_sections_v2, navigation_sections_v3_hardened):
        text = repr(vars(module))
        assert "dynamic_dashboards" not in text, module.__name__
        assert "/auto-dashboard" not in text, module.__name__

    for source in (modules_directory, role_access):
        text = repr({k: v for k, v in vars(source).items() if not callable(v)})
        assert "dynamic_dashboards" not in text, source.__name__
        assert "/auto-dashboard" not in text, source.__name__


def test_ai_chat_suggestions_do_not_link_to_the_registry():
    import inspect

    from app.modules.ai_chat.services import ai_chat_link_service

    assert "/auto-dashboard" not in inspect.getsource(ai_chat_link_service)


def test_element_catalog_is_unaffected(app, db_session, make_org, login_as):
    user = _user(db_session, make_org("autodash"), platform=False)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)
    assert client.get("/architecture/elements").status_code == 200
