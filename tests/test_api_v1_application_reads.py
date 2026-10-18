"""Application reads over the public interface (application components).

Two GET routes under /api/v1/application-components accept a signed-in
session or a bearer token carrying api:read. The organisation always comes
from the token: A's token lists only A's applications, B's id fetched with
A's token answers not found, and a token without an organisation, an
api:write-only token, an mcp:* token, or no token at all is refused.

Needs MCP_ENABLED and PUBLIC_BASE_URL, like the strategy reads; CI runs this
file in the step that switches the connector on.
"""

from __future__ import annotations

import os

import pytest

from test_api_v1_strategy_reads import (
    MCP_SCOPE,
    WRITE_SCOPE,
    _get,
    _make_service_user,
    _mint_token,
)

_OAUTH_ACTIVE = (
    os.environ.get("MCP_ENABLED", "").lower() in ("1", "true", "yes")
    and bool(os.environ.get("PUBLIC_BASE_URL"))
)
pytestmark = pytest.mark.skipif(
    not _OAUTH_ACTIVE, reason="OAuth routes not registered (MCP_ENABLED off)"
)

LIST = "/api/v1/application-components"


def _seed(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org_a = make_org("apps-alpha")
    org_b = make_org("apps-beta")
    zeta = ApplicationComponent(name="zeta ledger", organization_id=org_a.id)
    alpha = ApplicationComponent(
        name="alpha crm",
        organization_id=org_a.id,
        application_code="CRM-01",
        lifecycle_status="operational",
        criticality="important",
    )
    secret = ApplicationComponent(name="beta secret", organization_id=org_b.id)
    db_session.add_all([zeta, alpha, secret])
    db_session.flush()
    svc_a = _make_service_user(db_session, org_a, "apps-svc-a@example.com")
    token = _mint_token(svc_a, organization_id=org_a.id)
    return org_a, org_b, alpha, zeta, secret, token


class TestRefusals:
    def test_anonymous_is_refused(self, app, db_session, make_org):
        _seed(db_session, make_org)
        for path in (LIST, f"{LIST}/1"):
            assert _get(app, path).status_code == 401, path

    def test_api_write_token_is_refused(self, app, db_session, make_org):
        org_a, *_ = _seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "apps-svc-w@example.com")
        write_token = _mint_token(svc, organization_id=org_a.id, scope=WRITE_SCOPE)
        for path in (LIST, f"{LIST}/1"):
            assert _get(app, path, write_token).status_code == 403, path

    def test_mcp_token_is_refused(self, app, db_session, make_org):
        org_a, *_ = _seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "apps-svc-m@example.com")
        mcp_token = _mint_token(svc, organization_id=org_a.id, scope=MCP_SCOPE)
        for path in (LIST, f"{LIST}/1"):
            assert _get(app, path, mcp_token).status_code == 401, path

    def test_token_without_organisation_is_refused(self, app, db_session, make_org):
        org_a, *_ = _seed(db_session, make_org)
        svc = _make_service_user(db_session, org_a, "apps-svc-n@example.com")
        orgless = _mint_token(svc, organization_id=None)
        for path in (LIST, f"{LIST}/1"):
            assert _get(app, path, orgless).status_code == 401, path


class TestTwoOrganisations:
    def test_token_lists_only_its_own_organisation(self, app, db_session, make_org):
        *_, alpha, zeta, secret, token = _seed(db_session, make_org)
        response = _get(app, LIST, token)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["application_components"]]
        assert names == [alpha.name, zeta.name]
        assert secret.name not in names

    def test_another_organisations_application_is_not_found(
        self, app, db_session, make_org
    ):
        *_, secret, token = _seed(db_session, make_org)
        response = _get(app, f"{LIST}/{secret.id}", token)
        assert response.status_code == 404, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "NOT_FOUND"

    def test_detail_returns_recorded_fields_and_empty_text_for_unrecorded(
        self, app, db_session, make_org
    ):
        _a, _b, alpha, zeta, _secret, token = _seed(db_session, make_org)
        body = _get(app, f"{LIST}/{alpha.id}", token).get_json()["data"]
        assert body["code"] == "CRM-01"
        assert body["lifecycle_status"] == "operational"
        assert body["criticality"] == "important"
        bare = _get(app, f"{LIST}/{zeta.id}", token).get_json()["data"]
        assert bare["lifecycle_status"] == ""


class TestPaginationAndEmpty:
    def test_pages_in_name_order(self, app, db_session, make_org):
        token = _seed(db_session, make_org)[-1]
        body = _get(app, f"{LIST}?page=2&per_page=1", token).get_json()["data"]
        assert [i["name"] for i in body["application_components"]] == ["zeta ledger"]
        assert body["pagination"] == {"page": 2, "per_page": 1, "total": 2, "pages": 2}

    def test_organisation_with_nothing_returns_an_empty_list(
        self, app, db_session, make_org
    ):
        _seed(db_session, make_org)
        org_c = make_org("apps-gamma")
        svc = _make_service_user(db_session, org_c, "apps-svc-c@example.com")
        token = _mint_token(svc, organization_id=org_c.id)
        body = _get(app, LIST, token).get_json()["data"]
        assert body["application_components"] == []
        assert body["pagination"]["total"] == 0


class TestSessionStillWorks:
    def test_signed_in_session_reads_own_organisation(
        self, app, client, db_session, login_as, make_org
    ):
        org_a, _b, alpha, *_ = _seed(db_session, make_org)
        person = _make_service_user(db_session, org_a, "apps-person@example.com")
        login_as(client, person)
        response = client.get(LIST)
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [i["name"] for i in response.get_json()["data"]["application_components"]]
        assert alpha.name in names
