"""An organisation administrator who never picked a persona keeps full sight.

A fresh sign-up creates an organisation and makes the person its administrator;
sign-up writes no persona. The administrator must still see budget figures on
the Strategy answer and the full administrator sidebar, while an invited Viewer
with no persona gets neither. Release-rule lines 1 and 2.
"""
import uuid

from tests.test_account_mail_flows import _anonymous, mail_off  # noqa: F401
from tests.test_team_invite_acceptance import PASSWORD


def _zone_keys(user):
    from app.utils.role_access import get_sidebar_zones

    return [z["zone"] for z in get_sidebar_zones(user)]


def _strategy_row(client, element_id):
    resp = client.get(f"/api/v1/intelligence/strategy/{element_id}?include_derived=true")
    assert resp.status_code == 200
    return resp.get_json()["data"]["initiatives"][0]


def test_signed_up_administrator_without_persona_sees_budget_and_admin_sidebar_but_invited_viewer_does_not(
    app, db_session, client, login_as, mail_off  # noqa: F811
):
    from app.models import ArchiMateElement
    from app.models.enterprise_intelligence import PortfolioInitiative
    from app.models.user import Role, User
    from app.utils.role_access import can_access_section

    email = "founder-{}@example.com".format(uuid.uuid4().hex[:8])
    resp = _anonymous(app).post("/account/register", data={
        "first_name": "Trial", "last_name": "Founder", "email": email,
        "password": PASSWORD, "password2": PASSWORD,
    })
    assert resp.status_code == 302
    admin = User.find_by_email(email)
    assert admin.organization_id is not None
    assert admin.enterprise_role is None, "sign-up must not write a persona"
    assert admin.is_org_admin

    Role.insert_roles()
    viewer = User(email=f"viewer-{uuid.uuid4().hex[:8]}@example.com", organization_id=admin.organization_id,
                  confirmed=True, enterprise_role=None, is_platform_admin=False,
                  role=Role.query.filter_by(name="Viewer").first())
    db_session.add(viewer)
    element = ArchiMateElement(name="E-A", type="ApplicationComponent", layer="application",
                               organization_id=admin.organization_id)
    db_session.add(element)
    db_session.flush()
    db_session.add(PortfolioInitiative(
        organization_id=admin.organization_id, name="Transform A", archimate_element_id=element.id,
        status="Active", total_budget=50000.0, spent_to_date=45000.0,
    ))
    db_session.commit()

    login_as(client, admin)
    row = _strategy_row(client, element.id)
    assert row["budget_reason"] is None
    assert round(row["budget_variance_pct"], 2) == -10.0
    admin_zones = _zone_keys(admin)
    assert "admin" in admin_zones
    assert can_access_section(admin, "administration")
    assert can_access_section(admin, "procurement")
    page = client.get("/dashboard/overview")
    assert page.status_code == 200

    login_as(client, viewer)
    row = _strategy_row(client, element.id)
    assert row["budget_variance_pct"] is None
    assert row["budget_reason"] == "financial_data_restricted"
    assert "admin" not in _zone_keys(viewer)
    assert _zone_keys(viewer) != admin_zones
    assert not can_access_section(viewer, "administration")
    assert not can_access_section(viewer, "procurement")
