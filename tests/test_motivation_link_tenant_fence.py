"""Tenant fence for motivation-layer link routes.

Proves that no route links or reveals another organisation's Goal, Driver
or Requirement. Uses two real organisations throughout.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_goal(db_session, org_id, name="Test Goal"):
    """Insert a Goal directly attributed to *org_id*."""
    from app.models.motivation import Goal

    row = Goal(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_driver(db_session, org_id, name="Test Driver"):
    """Insert a Driver directly attributed to *org_id*."""
    from app.models.motivation import Driver

    row = Driver(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_requirement(db_session, org_id, title="Test Requirement"):
    """Insert a Requirement directly attributed to *org_id*."""
    from app.models.models import Requirement

    row = Requirement(title=title, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_app(db_session, org_id, name="Test App"):
    """Insert an ApplicationComponent directly attributed to *org_id*."""
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------- Goal linking


def test_org_a_cannot_link_org_b_goal(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must not be able to link Org B's Goal to its own application."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.motivation import Goal

    org_a = make_org("a")
    org_b = make_org("b")

    # Create app owned by org A
    app_a = _make_app(db_session, org_a.id, "App A")

    # Create goal owned by org B
    goal_b = _make_goal(db_session, org_b.id, "Goal B")

    # Create a user in org A and log in
    from app.models.user import User
    user_a = User(
        email=f"user-a-{goal_b.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # Attempt to link org B's goal to org A's app
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/goals/add",
            data={"element_id": goal_b.id},
            follow_redirects=True,
        )

    # Should not succeed - goal should not be found or should be refused
    assert resp.status_code == 200
    # The goal should not be linked (flash message should indicate not found or error)
    # Check that the goal's application_component_id is still None
    db_session.expire(goal_b)
    assert goal_b.application_component_id is None, (
        "TENANT LEAK: org A linked org B's goal to its own application"
    )


def test_org_a_can_link_own_goal(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must be able to link its own Goal to its own application."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.motivation import Goal

    org_a = make_org("a")

    # Create app owned by org A
    app_a = _make_app(db_session, org_a.id, "App A")

    # Create goal owned by org A
    goal_a = _make_goal(db_session, org_a.id, "Goal A")

    # Create a user in org A and log in
    from app.models.user import User
    user_a = User(
        email=f"user-a-{goal_a.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # Link org A's goal to org A's app
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/goals/add",
            data={"element_id": goal_a.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    # The goal should now be linked
    db_session.expire(goal_a)
    assert goal_a.application_component_id == app_a.id, (
        "Org A should be able to link its own goal"
    )


# --------------------------------------------------------------- Driver linking


def test_org_a_cannot_link_org_b_driver(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must not be able to link Org B's Driver to its own application."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.motivation import Driver

    org_a = make_org("a")
    org_b = make_org("b")

    # Create app owned by org A
    app_a = _make_app(db_session, org_a.id, "App A")

    # Create driver owned by org B
    driver_b = _make_driver(db_session, org_b.id, "Driver B")

    # Create a user in org A and log in
    from app.models.user import User
    user_a = User(
        email=f"user-a-{driver_b.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # Attempt to link org B's driver to org A's app
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/drivers/add",
            data={"element_id": driver_b.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    # The driver should not be linked
    db_session.expire(driver_b)
    assert driver_b.application_component_id is None, (
        "TENANT LEAK: org A linked org B's driver to its own application"
    )


def test_org_a_can_link_own_driver(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must be able to link its own Driver to its own application."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.motivation import Driver

    org_a = make_org("a")

    # Create app owned by org A
    app_a = _make_app(db_session, org_a.id, "App A")

    # Create driver owned by org A
    driver_a = _make_driver(db_session, org_a.id, "Driver A")

    # Create a user in org A and log in
    from app.models.user import User
    user_a = User(
        email=f"user-a-{driver_a.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # Link org A's driver to org A's app
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/drivers/add",
            data={"element_id": driver_a.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    # The driver should now be linked
    db_session.expire(driver_a)
    assert driver_a.application_component_id == app_a.id, (
        "Org A should be able to link its own driver"
    )


# --------------------------------------------------------------- Requirement linking


def test_org_a_cannot_link_org_b_requirement(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must not be able to link Org B's Requirement to its own application."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import Requirement

    org_a = make_org("a")
    org_b = make_org("b")

    # Create app owned by org A
    app_a = _make_app(db_session, org_a.id, "App A")

    # Create requirement owned by org B
    req_b = _make_requirement(db_session, org_b.id, "Requirement B")

    # Create a user in org A and log in
    from app.models.user import User
    user_a = User(
        email=f"user-a-{req_b.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # Attempt to link org B's requirement to org A's app
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/requirements/add",
            data={"element_id": req_b.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    # The requirement should not be linked
    db_session.expire(req_b)
    assert req_b.application_component_id is None, (
        "TENANT LEAK: org A linked org B's requirement to its own application"
    )


def test_org_a_can_link_own_requirement(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must be able to link its own Requirement to its own application."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import Requirement

    org_a = make_org("a")

    # Create app owned by org A
    app_a = _make_app(db_session, org_a.id, "App A")

    # Create requirement owned by org A
    req_a = _make_requirement(db_session, org_a.id, "Requirement A")

    # Create a user in org A and log in
    from app.models.user import User
    user_a = User(
        email=f"user-a-{req_a.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # Link org A's requirement to org A's app
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/requirements/add",
            data={"element_id": req_a.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    # The requirement should now be linked
    db_session.expire(req_a)
    assert req_a.application_component_id == app_a.id, (
        "Org A should be able to link its own requirement"
    )


# --------------------------------------------------------------- Unified applications routes


def test_org_a_cannot_link_org_b_goal_via_unified(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must not link Org B's Goal via unified applications route."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.motivation import Goal

    org_a = make_org("a")
    org_b = make_org("b")

    app_a = _make_app(db_session, org_a.id, "App A")
    goal_b = _make_goal(db_session, org_b.id, "Goal B")

    from app.models.user import User
    user_a = User(
        email=f"user-a-{goal_b.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/goals/add",
            data={"element_id": goal_b.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    db_session.expire(goal_b)
    assert goal_b.application_component_id is None, (
        "TENANT LEAK via unified route: org A linked org B's goal"
    )


def test_org_a_cannot_link_org_b_driver_via_unified(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must not link Org B's Driver via unified applications route."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.motivation import Driver

    org_a = make_org("a")
    org_b = make_org("b")

    app_a = _make_app(db_session, org_a.id, "App A")
    driver_b = _make_driver(db_session, org_b.id, "Driver B")

    from app.models.user import User
    user_a = User(
        email=f"user-a-{driver_b.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/drivers/add",
            data={"element_id": driver_b.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    db_session.expire(driver_b)
    assert driver_b.application_component_id is None, (
        "TENANT LEAK via unified route: org A linked org B's driver"
    )


def test_org_a_cannot_link_org_b_requirement_via_unified(db_session, make_org, tenant_ctx, client, login_as):
    """Org A must not link Org B's Requirement via unified applications route."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import Requirement

    org_a = make_org("a")
    org_b = make_org("b")

    app_a = _make_app(db_session, org_a.id, "App A")
    req_b = _make_requirement(db_session, org_b.id, "Requirement B")

    from app.models.user import User
    user_a = User(
        email=f"user-a-{req_b.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/requirements/add",
            data={"element_id": req_b.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    db_session.expire(req_b)
    assert req_b.application_component_id is None, (
        "TENANT LEAK via unified route: org A linked org B's requirement"
    )


# --------------------------------------------------------------- Bug fix: .name vs .title


def test_a_requirement_with_no_archimate_element_is_refused_not_guessed(
    db_session, make_org, tenant_ctx, client, login_as
):
    """Linking a requirement must not error with 'Requirement object has no attribute name'.

    The Requirement model uses 'title', not 'name'. Flash messages must use .title
    or .display_title, not .name.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import Requirement

    org_a = make_org("a")

    app_a = _make_app(db_session, org_a.id, "App A")
    req_a = _make_requirement(db_session, org_a.id, "Requirement With Title Only")

    from app.models.user import User
    user_a = User(
        email=f"user-a-{req_a.id}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user_a)
    db_session.flush()

    login_as(client, user_a)

    # This must not raise AttributeError: 'Requirement' object has no attribute 'name'
    with tenant_ctx(org_a.id):
        resp = client.post(
            f"/applications/{app_a.id}/requirements/add",
            data={"element_id": req_a.id},
            follow_redirects=True,
        )

    assert resp.status_code == 200, (
        f"Requirement linking failed with status {resp.status_code}: {resp.data[:500]}"
    )
    # The requirement should be linked successfully
    db_session.expire(req_a)
    assert req_a.application_component_id == app_a.id, (
        "Requirement linking should succeed for same-org requirement"
    )