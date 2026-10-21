"""Maintenance cost entered through the application forms is stored on the real
column (`maintenance_cost`) and read back; it is never attached to a Python
attribute that is not a column, and one organisation's value is not visible on
another organisation's application."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _user(db_session, org, label):
    from app.models.user import User

    user = User(email=f"{label}-{org.id}@example.com", organization_id=org.id,
                enterprise_role="enterprise_architect", confirmed=True)
    db_session.add(user)
    db_session.commit()
    return user


def test_form_field_is_the_real_column_name():
    from app.application_mgmt.forms import ApplicationComponentForm
    from app.models.application_portfolio import ApplicationComponent

    assert hasattr(ApplicationComponent, "maintenance_cost")
    assert not hasattr(ApplicationComponent, "maintenance_cost_annual")
    assert hasattr(ApplicationComponentForm, "maintenance_cost")
    assert not hasattr(ApplicationComponentForm, "maintenance_cost_annual")


@pytest.mark.parametrize("path", [
    "app/application_mgmt/crud_routes.py",
    "app/application_mgmt/forms.py",
    "app/modules/applications/routes/crud_routes.py",
])
def test_form_code_no_longer_names_the_missing_attribute(path):
    assert not re.search(r"maintenance_cost_annual", (ROOT / path).read_text(encoding="utf-8"))


def test_create_and_edit_persist_maintenance_cost_and_isolate_organisations(
        app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from tests.test_ba_tenant_and_authz import _login

    org_a, org_b = make_org("maint-a"), make_org("maint-b")
    with tenant_ctx(org_a.id):
        user_a = _user(db_session, org_a, "maint-a")
    with tenant_ctx(org_b.id):
        _user(db_session, org_b, "maint-b")
        app_b = ApplicationComponent(name="Org B Ledger", organization_id=org_b.id)
        db_session.add(app_b)
        db_session.commit()
        app_b_id = app_b.id

    with tenant_ctx(org_a.id):
        client = app.test_client()
        with app.app_context():
            _login(client, user_a.id)
            # Create through the form-encoded path, with a maintenance cost.
            resp = client.post("/applications/create", data={
                "name": "Org A Billing", "maintenance_cost": "12345.50"})
            assert resp.status_code in (200, 201, 302), resp.get_data(as_text=True)
            created = db_session.query(ApplicationComponent).filter_by(
                name="Org A Billing", organization_id=org_a.id).one()
            assert created.maintenance_cost == pytest.approx(12345.50)
            app_a_id = created.id

            # Edit changes it; a fresh read returns the new value.
            resp = client.post(f"/applications/{app_a_id}/edit", data={
                "name": "Org A Billing", "maintenance_cost": "999"})
            assert resp.status_code == 302, resp.get_data(as_text=True)
            db_session.expire_all()
            assert db_session.get(ApplicationComponent, app_a_id).maintenance_cost == pytest.approx(999.0)

            # Org A cannot write to org B's application, and org B's stays empty.
            resp = client.post(f"/applications/{app_b_id}/edit", data={
                "name": "Org B Ledger", "maintenance_cost": "777"})
            assert resp.status_code in (302, 403, 404)
            db_session.expire_all()
            # Org A's context cannot even load org B's application.
            assert db_session.get(ApplicationComponent, app_b_id) is None

    # Org B's own application never carries org A's figure.
    with tenant_ctx(org_b.id):
        db_session.expire_all()
        values = {a.name: a.maintenance_cost for a in db_session.query(ApplicationComponent).all()}
        assert values == {"Org B Ledger": None}
