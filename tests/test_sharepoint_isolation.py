"""One organisation can never see, choose or save another organisation's SharePoint list.

Two real organisations, each with its own Microsoft 365 settings (tenants ``contoso`` and ``fabrikam`` of the
recorded fixture), its own secret in the vault and its own administrator. The stand-in records every request, so
"refused before any request" is read from what was actually sent.
"""

from __future__ import annotations

import pytest

from tests.smoke import graph_standin
from tests.test_connector_sync_route_honesty import _make_admin
from tests.test_sharepoint_graph_and_mapping import seed_m365

pytestmark = pytest.mark.usefixtures("db_session")

LIST_A = "list-apps-contoso"
LIST_B = "list-apps-fabrikam"


@pytest.fixture
def standin(app, monkeypatch):
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)
    return graph_standin.install(monkeypatch)


@pytest.fixture
def orgs(db_session, tenant_ctx, make_org):
    a, b = make_org("sp-iso-a"), make_org("sp-iso-b")
    seed_m365(db_session, tenant_ctx, a, tenant="contoso", site_name="Architecture")
    seed_m365(db_session, tenant_ctx, b, tenant="fabrikam", site_name="Platform")
    admin_a = _make_admin(db_session, a.id, "Ada")
    admin_b = _make_admin(db_session, b.id, "Bob")
    db_session.commit()
    return a, b, admin_a, admin_b


def _graph_paths(standin):
    return [r["path"] for r in standin.requests_seen if r["host"] == graph_standin.GRAPH_HOST]


def test_another_organisations_list_id_is_refused_before_any_request(db_session, tenant_ctx, standin, orgs):
    from app.connectors.sharepoint import SelectionRefused, SharePointConnector

    _a, b, _admin_a, _admin_b = orgs
    with tenant_ctx(b.id):
        connector = SharePointConnector.for_organisation(b.id, list_id=LIST_B, list_name="Application Inventory")
        before = len(standin.requests_seen)
        with pytest.raises(SelectionRefused):
            connector.columns(LIST_A)
    assert len(standin.requests_seen) == before, "a request was sent for another organisation's list"


def test_choosing_another_organisations_list_never_asks_for_its_columns(db_session, tenant_ctx, standin, orgs):
    from app.connectors.sharepoint import SelectionRefused, SharePointConnector

    _a, b, _admin_a, _admin_b = orgs
    with tenant_ctx(b.id):
        connector = SharePointConnector.for_organisation(b.id)
        with pytest.raises(SelectionRefused):
            connector.choose_list(LIST_A)
    assert not [p for p in _graph_paths(standin) if p.endswith("/columns")]
    assert all("contoso" not in p for p in _graph_paths(standin))


def test_each_organisation_is_offered_only_its_own_lists(db_session, tenant_ctx, standin, orgs):
    from app.connectors.sharepoint import SharePointConnector

    a, b, _admin_a, _admin_b = orgs
    with tenant_ctx(a.id):
        names_a = [i["id"] for i in SharePointConnector.for_organisation(a.id).available_lists()]
    with tenant_ctx(b.id):
        names_b = [i["id"] for i in SharePointConnector.for_organisation(b.id).available_lists()]
    assert names_a == [LIST_A, "list-notes-contoso"]
    assert names_b == [LIST_B]


def test_the_save_route_refuses_a_list_that_is_not_on_the_saved_site(app, db_session, standin, orgs, login_as):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    _a, b, _admin_a, admin_b = orgs
    client = app.test_client()
    login_as(client, admin_b)
    before = AIChatCRUDApproval.query.filter_by(organization_id=b.id).count()
    response = client.post(
        "/admin/connectors/sharepoint", data={"list_id": LIST_A, "name": "Title"}, follow_redirects=True
    )
    html = response.get_data(as_text=True)
    assert "That list is not on a site this organisation can read" in html
    assert AIChatCRUDApproval.query.filter_by(organization_id=b.id).count() == before
    assert not [p for p in _graph_paths(standin) if p.endswith("/columns")]


def test_the_page_of_one_organisation_names_none_of_the_others_lists(app, db_session, standin, orgs, login_as):
    _a, _b, admin_a, admin_b = orgs
    client = app.test_client()
    login_as(client, admin_b)
    html_b = client.get("/admin/connectors/sharepoint").get_data(as_text=True)
    assert "Application Inventory" in html_b
    assert "Meeting Notes" not in html_b and "Finance Apps" not in html_b
    login_as(client, admin_a)
    html_a = client.get("/admin/connectors/sharepoint").get_data(as_text=True)
    assert "Meeting Notes" in html_a and "Finance Apps" not in html_a


def test_the_status_of_another_organisations_save_answers_404(app, db_session, standin, orgs, login_as):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    a, _b, admin_a, admin_b = orgs
    client = app.test_client()
    login_as(client, admin_a)
    saved = client.post("/admin/connectors/sharepoint", data={"list_id": LIST_A, "name": "Title"})
    assert saved.status_code == 302
    approval = AIChatCRUDApproval.query.filter_by(organization_id=a.id).order_by(AIChatCRUDApproval.id.desc()).first()
    assert approval is not None
    own = client.get(f"/admin/connectors/sharepoint/status?approval={approval.id}")
    assert own.status_code == 200 and own.get_json()["state"] == "checking"
    login_as(client, admin_b)
    other = client.get(f"/admin/connectors/sharepoint/status?approval={approval.id}")
    assert other.status_code == 404
