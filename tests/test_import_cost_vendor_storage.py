"""An imported cost and vendor are stored where every screen reads them, and the edit form can change them.

Driven through the real import endpoints and the edit page as a signed-in user.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest

from tests.test_import_update_never_blanks import _user

pytestmark = pytest.mark.usefixtures("db_session")


def _apps(org_id):
    from app.models.application_portfolio import ApplicationComponent

    return {
        a.name: a
        for a in ApplicationComponent.query.filter(ApplicationComponent.organization_id == org_id).all()
    }


def _facts(org_id, app_id):
    from app.models.cost_fact import CostFact

    return CostFact.query.filter_by(organization_id=org_id, element_id=app_id).all()


def _mapped_upload(client, csv_text, mappings, login=None):
    if login:
        login()
    return client.post(
        "/applications/upload-excel",
        data={
            "file": (io.BytesIO(csv_text.encode()), f"apps-{uuid.uuid4().hex[:6]}.csv"),
            "duplicate_mode": "merge",
            "field_mappings": json.dumps(mappings),
        },
        content_type="multipart/form-data",
    )


def test_mapped_import_stores_vendor_and_cost_where_screens_read_them(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    from app.services.application_cost_accessor import get_annual_cost_with_source

    org = make_org("cost")
    user = _user(db_session, org.id, "cost")
    login = lambda: login_as(client, user)  # noqa: E731
    with tenant_ctx(org.id):
        resp = _mapped_upload(
            client,
            "Name,Vendor Name,Software / License Cost,Annual Cost\nAlpha,Acme,\"1,200\",5000\nBeta,Globex,3400,\n",
            {
                "Name": "name",
                "Vendor Name": "vendor_name",
                "Software / License Cost": "license_cost",
                "Annual Cost": "total_cost_of_ownership",
            },
            login,
        )
        assert resp.status_code == 200, resp.get_json()
        db_session.expire_all()
        apps = _apps(org.id)
        alpha, beta = apps["Alpha"], apps["Beta"]
        assert alpha.vendor_name == "Acme" and beta.vendor_name == "Globex"
        assert get_annual_cost_with_source(alpha)[0] == 5000.0
        assert get_annual_cost_with_source(beta)[0] == 3400.0
        # The cost fact store agrees with the columns.
        assert {f.category: float(f.amount) for f in _facts(org.id, alpha.id)} == {
            "total": 5000.0,
            "license_cost": 1200.0,
        }
        assert {f.category: float(f.amount) for f in _facts(org.id, beta.id)} == {"license_cost": 3400.0}


def test_mapped_import_update_records_a_changed_cost_fact(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    org = make_org("cost-upd")
    user = _user(db_session, org.id, "cost-upd")
    login = lambda: login_as(client, user)  # noqa: E731
    mappings = {"Name": "name", "Annual Cost": "total_cost_of_ownership"}
    with tenant_ctx(org.id):
        assert _mapped_upload(client, "Name,Annual Cost\nGamma,100\n", mappings, login).status_code == 200
        assert _mapped_upload(client, "Name,Annual Cost\nGamma,250\n", mappings, login).status_code == 200
        db_session.expire_all()
        gamma = _apps(org.id)["Gamma"]
        assert gamma.total_cost_of_ownership == 250.0
        assert [float(f.amount) for f in _facts(org.id, gamma.id)] == [250.0]


def test_template_import_stores_cost_and_a_blank_cost_never_clears_it(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    from app.services.application_cost_accessor import get_annual_cost

    org = make_org("cost-tpl")
    user = _user(db_session, org.id, "cost-tpl")
    login = lambda: login_as(client, user)  # noqa: E731

    def post(csv_text, mode):
        login()
        return client.post(
            "/applications/import",
            data={"file": (io.BytesIO(csv_text.encode()), "apps.csv"), "import_mode": mode,
                  "quick_mode": "true", "enable_ai": "false"},
            content_type="multipart/form-data",
        )

    with tenant_ctx(org.id):
        assert post("Name,Vendor,Annual Cost\nDelta,Initech,\"7,500\"\n", "skip").status_code in (200, 302)
        db_session.expire_all()
        delta = _apps(org.id)["Delta"]
        assert delta.vendor_name == "Initech"
        assert get_annual_cost(delta) == 7500
        assert [float(f.amount) for f in _facts(org.id, delta.id)] == [7500.0]

        assert post("Name,Description,Annual Cost\nDelta,Refreshed,\n", "update").status_code in (200, 302)
        db_session.expire_all()
        delta = _apps(org.id)["Delta"]
        assert get_annual_cost(delta) == 7500 and delta.vendor_name == "Initech"


def test_edit_form_has_cost_and_vendor_fields_that_persist(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import get_annual_cost

    org = make_org("cost-edit")
    user = _user(db_session, org.id, "cost-edit")
    login = lambda: login_as(client, user)  # noqa: E731
    with tenant_ctx(org.id):
        comp = ApplicationComponent(name=f"Edit {uuid.uuid4().hex[:6]}", organization_id=org.id,
                                    deployment_status="production")
        db_session.add(comp)
        db_session.flush()
        login()
        page = client.get(f"/applications/{comp.id}/edit")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'name="total_cost_of_ownership"' in html and 'name="vendor_name"' in html

        login()
        resp = client.post(
            f"/applications/{comp.id}/edit",
            data={"name": comp.name, "total_cost_of_ownership": "12345.50", "vendor_name": "Hooli"},
        )
        assert resp.status_code in (200, 302), resp.status_code
        db_session.expire_all()
        comp = db_session.get(ApplicationComponent, comp.id)
        assert comp.vendor_name == "Hooli"
        assert float(get_annual_cost(comp)) == 12345.50
        login()
        assert 'value="12345.5"' in client.get(f"/applications/{comp.id}/edit").get_data(as_text=True)


def test_imported_cost_in_one_organisation_is_invisible_to_another(
    app, db_session, make_org, client, login_as, tenant_ctx
):
    org_a, org_b = make_org("cost-a"), make_org("cost-b")
    user_a = _user(db_session, org_a.id, "cost-a")
    login = lambda: login_as(client, user_a)  # noqa: E731
    with tenant_ctx(org_a.id):
        resp = _mapped_upload(
            client, "Name,Annual Cost\nIsolated,999\n",
            {"Name": "name", "Annual Cost": "total_cost_of_ownership"}, login,
        )
        assert resp.status_code == 200
    with tenant_ctx(org_b.id):
        assert _apps(org_b.id) == {}
        from app.models.cost_fact import CostFact

        assert CostFact.query.filter_by(organization_id=org_b.id).count() == 0
