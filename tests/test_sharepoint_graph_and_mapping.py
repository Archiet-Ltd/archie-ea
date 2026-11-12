"""The SharePoint list connector: the Graph client, the token, the lists on a site and the column mapping.

Microsoft is never called: ``tests/smoke/graph_standin.py`` answers the sign-in and Graph hosts from a recorded
fixture (``tests/fixtures/sharepoint_contoso.json``). Each organisation here is a real row with its own
Microsoft 365 settings and its own secret in the vault.
"""

from __future__ import annotations

import json

import pytest
import requests

from tests.smoke import graph_standin

pytestmark = pytest.mark.usefixtures("db_session")


def seed_m365(db_session, tenant_ctx, org, tenant="contoso", site_name="Architecture", secret=None):
    """A Microsoft 365 row for ``org`` pointing at a tenant and site of the fixture, with its secret in the vault."""
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    world = graph_standin.load_world()["tenants"][tenant]
    site = next(s for s in world["sites"] if s["name"] == site_name)
    cfg = ConnectorConfig(
        organization_id=org.id,
        connector_type="m365",
        name="Microsoft 365",
        config={
            "tenant_id": tenant,
            "client_id": world["client_id"],
            "site_id": site["id"],
            "enabled": True,
        },
        status="active",
    )
    db_session.add(cfg)
    db_session.flush()
    with tenant_ctx(org.id):
        OrgCredentialVault().store(
            org.id, "m365", "client_secret", secret if secret is not None else world["client_secret"]
        )
    return cfg


@pytest.fixture
def standin(app, monkeypatch):
    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)
    return graph_standin.install(monkeypatch)


@pytest.fixture
def org(make_org):
    return make_org("sp-map")


def _connector(db_session, tenant_ctx, org, **kwargs):
    from app.connectors.sharepoint import SharePointConnector

    seed_m365(db_session, tenant_ctx, org)
    with tenant_ctx(org.id):
        return SharePointConnector.for_organisation(org.id, **kwargs)


def test_the_connector_type_is_registered_and_named(app):
    from app.connectors.sharepoint import SharePointConnector
    from app.services.connector_framework import CONNECTOR_TYPE_LABELS, _CONNECTOR_CLASSES, _register_concrete_connectors

    _register_concrete_connectors()
    assert _CONNECTOR_CLASSES["sharepoint_read"] is SharePointConnector
    assert CONNECTOR_TYPE_LABELS["sharepoint_read"] == "SharePoint (read)"


def test_graph_token_is_the_organisations_own_token(db_session, tenant_ctx, standin, org):
    from app.services.m365_service import M365Service

    cfg = seed_m365(db_session, tenant_ctx, org)
    with tenant_ctx(org.id):
        token = M365Service().graph_token(cfg)
    assert token == "graph-token-contoso-5521"
    assert [r["path"] for r in standin.requests_seen] == ["/contoso/oauth2/v2.0/token"]


def test_graph_token_is_none_when_the_secret_is_wrong(db_session, tenant_ctx, standin, org):
    from app.services.m365_service import M365Service

    cfg = seed_m365(db_session, tenant_ctx, org, secret="not-the-secret")
    with tenant_ctx(org.id):
        assert M365Service().graph_token(cfg) is None


def test_the_bearer_header_is_sent_on_every_graph_request(db_session, tenant_ctx, standin, org):
    connector = _connector(db_session, tenant_ctx, org)
    with tenant_ctx(org.id):
        connector.available_lists()
    graph_calls = [r for r in standin.requests_seen if r["host"] == graph_standin.GRAPH_HOST]
    assert graph_calls and all(r["bearer"] == "graph-token-contoso-5521" for r in graph_calls)


def test_a_redirect_is_refused_not_followed():
    from app.services.api_clients.base_client import APIClientError
    from app.services.api_clients.graph_client import GraphClient

    client = GraphClient("tok-redirect-1")
    reply = requests.Response()
    reply.status_code = 302
    reply.headers["Location"] = "https://elsewhere.example/steal"
    with pytest.raises(APIClientError):
        for hook in client.session.hooks["response"]:
            hook(reply)


def test_the_token_is_scrubbed_from_an_error_message(app, monkeypatch):
    from app.services.api_clients.graph_client import GraphClient, GraphError

    monkeypatch.setitem(app.config, "ALLOW_EXTERNAL_API_CALLS", True)
    secret = "tok-must-not-leak-9917"

    class Reply:
        status_code = 500
        reason = "Server Error"
        headers = {}
        text = f"internal error for Bearer {secret}"

        def json(self):
            return {}

    monkeypatch.setattr(requests.Session, "request", lambda self, **kw: Reply())
    client = GraphClient(secret)
    with pytest.raises(GraphError) as caught:
        client.lists_at("sites/s1/lists")
    assert secret not in str(caught.value)
    assert "***" in str(caught.value)


def test_system_lists_are_not_offered(db_session, tenant_ctx, standin, org):
    connector = _connector(db_session, tenant_ctx, org)
    with tenant_ctx(org.id):
        names = [item["display_name"] for item in connector.available_lists()]
    assert names == ["Application Inventory", "Meeting Notes"]


def test_a_site_id_that_is_not_a_plain_id_never_reaches_a_path():
    from app.connectors.sharepoint import Selection, selection_path
    from app.services.api_clients.graph_client import GraphError

    with pytest.raises(GraphError):
        selection_path(Selection("contoso", "a/../../me", None), "lists")
    with pytest.raises(GraphError):
        selection_path(Selection("contoso", "site-1", "l1?$expand=x"), "columns")


def test_selection_path_is_built_only_from_the_selection():
    from app.connectors.sharepoint import Selection, SelectionRefused, selection_path

    selection = Selection("contoso", "contoso.sharepoint.com,s1,w1", "list-1")
    assert selection_path(selection, "lists") == "sites/contoso.sharepoint.com,s1,w1/lists"
    assert selection_path(selection, "columns") == "sites/contoso.sharepoint.com,s1,w1/lists/list-1/columns"
    with pytest.raises(SelectionRefused):
        selection_path(Selection("contoso", "s1", None), "columns")
    with pytest.raises(SelectionRefused):
        selection_path(selection, "items?x=1")


def test_the_mapping_is_proposed_by_column_title_ignoring_case_and_spacing():
    from app.connectors.sharepoint import propose_mapping

    columns = [
        {"name": "Title", "display_name": "TITLE", "hidden": False, "read_only": False},
        {"name": "Desc", "display_name": "description", "hidden": False, "read_only": False},
        {"name": "Biz", "display_name": "Business_Owner", "hidden": False, "read_only": False},
        {"name": "Cost1", "display_name": "annual  cost", "hidden": False, "read_only": False},
        {"name": "Stage", "display_name": "Status", "hidden": False, "read_only": False},
    ]
    assert propose_mapping(columns) == {
        "name": "Title",
        "description": "Desc",
        "owner": "Biz",
        "annual_cost": "Cost1",
        "lifecycle": "Stage",
    }


def test_hidden_and_read_only_columns_are_never_proposed():
    from app.connectors.sharepoint import propose_mapping

    columns = [
        {"name": "Title", "display_name": "Title", "hidden": False, "read_only": False},
        {"name": "Owner", "display_name": "Owner", "hidden": True, "read_only": False},
        {"name": "Lifecycle", "display_name": "Lifecycle", "hidden": False, "read_only": True},
    ]
    proposal = propose_mapping(columns)
    assert proposal["name"] == "Title"
    assert proposal["owner"] is None
    assert proposal["lifecycle"] is None


def test_a_cost_column_with_another_name_is_left_as_no_column_chosen(db_session, tenant_ctx, standin, org):
    connector = _connector(db_session, tenant_ctx, org)
    with tenant_ctx(org.id):
        chosen = connector.choose_list("list-apps-contoso")
        columns = chosen.columns()
    from app.connectors.sharepoint import propose_mapping

    proposal = propose_mapping(columns)
    assert proposal["name"] == "Title"
    assert proposal["owner"] == "Owner"
    assert proposal["annual_cost"] is None  # the fixture's cost column is named Budget
    assert "Budget" in [c["name"] for c in columns]


def test_a_posted_mapping_keeps_only_offered_columns():
    from app.connectors.sharepoint import clean_mapping

    columns = [
        {"name": "Title", "display_name": "Title", "hidden": False, "read_only": False},
        {"name": "Budget", "display_name": "Budget", "hidden": False, "read_only": False},
        {"name": "ContentType", "display_name": "Content Type", "hidden": True, "read_only": False},
    ]
    cleaned = clean_mapping(
        {"name": "Title", "annual_cost": "Budget", "owner": "ContentType", "description": "Made-up"}, columns
    )
    assert cleaned["name"] == "Title"
    assert cleaned["annual_cost"] == "Budget"
    assert cleaned["owner"] is None
    assert cleaned["description"] is None


def test_validate_config_asks_for_a_name_column(db_session, tenant_ctx, standin, org):
    connector = _connector(db_session, tenant_ctx, org, list_id="list-apps-contoso", mapping={})
    assert any("application name" in e for e in connector.validate_config())
    connector = _connector(
        db_session, tenant_ctx, org, list_id="list-apps-contoso", mapping={"name": "Title"}
    )
    assert connector.validate_config() == []


def test_the_first_read_says_nothing_has_been_read(db_session, tenant_ctx, standin, org):
    import asyncio

    connector = _connector(db_session, tenant_ctx, org, list_id="list-apps-contoso", mapping={"name": "Title"})
    result = asyncio.run(connector.batch_sync())
    assert result["records_processed"] == 0
    assert "not been read" in result["message"]
    assert not [r for r in standin.requests_seen if r["host"] == graph_standin.GRAPH_HOST]


def test_the_page_says_what_is_missing_when_the_microsoft_365_page_is_empty(db_session, tenant_ctx, standin, make_org):
    from app.connectors.sharepoint import SharePointConnector, SharePointError

    empty = make_org("sp-empty")
    with tenant_ctx(empty.id), pytest.raises(SharePointError) as caught:
        SharePointConnector.for_organisation(empty.id)
    assert "Microsoft 365 page" in str(caught.value)
    assert json.dumps(standin.requests_seen) == "[]"
