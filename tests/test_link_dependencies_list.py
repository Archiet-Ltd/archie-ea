"""
Link Dependencies dialog, step 2: the application list.

The dialog (opened from an application's detail page via
``openUnifiedMappingModalDiscovery({context: 'archimate'})``) loads the
organisation's applications in its second step. It calls the endpoint the
dialog's JavaScript points at and expects a ``{"applications": [...]}``
payload. The list must contain only the active organisation's applications.
"""
import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, email_root):
    from app.models.user import User

    user = User(
        email=f"{email_root}-{__import__('uuid').uuid4().hex[:8]}@example.test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_link_dependencies_step2_loads_only_own_org_applications(
    db_session, make_org, client, login_as
):
    """Signed-in user of org A sees only org A's applications in step 2."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import ArchiMateElement

    org_a, org_b = make_org("a"), make_org("b")
    a_app = ApplicationComponent(name="App owned by A", organization_id=org_a.id)
    b_app = ApplicationComponent(name="App owned by B", organization_id=org_b.id)
    db_session.add_all([a_app, b_app])
    db_session.flush()

    # The dialog passes the selected archimate element id as the step-2 id.
    element = ArchiMateElement(
        name="Selected element", type="ApplicationComponent", layer="application",
        organization_id=org_a.id,
    )
    db_session.add(element)
    db_session.flush()
    element_id = element.id

    user = _make_user(db_session, org_a, "link-deps-user")
    login_as(client, user)

    # Endpoint the dialog's JavaScript calls in step 2 (archimate context).
    resp = client.get(f"/capability-map/api/capabilities/{element_id}/archimate-mappings")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert "applications" in payload, payload
    names = {app["name"] for app in payload["applications"]}
    assert "App owned by A" in names
    assert "App owned by B" not in names, (
        "TENANT LEAK: step 2 returned another organisation's application."
    )
