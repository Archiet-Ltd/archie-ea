"""Endpoints opened directly answer with their content or a plain status.

Five JSON addresses answered 500 when opened by an architect:

  * an application's architecture elements and its CSV export compared an
    integer column with the address text, so they failed for every application;
  * an ArchiMate model asked a model row for a method it does not have, so it
    failed for every model;
  * an application's capabilities did the same comparison, and also never
    checked the application belonged to the caller's organisation;
  * a market-analysis category with no data answered 500 although nothing had
    gone wrong.

Each case uses two organisations: the architect of one organisation must get
content for its own rows and a plain 404 for the other organisation's row, for
a row that does not exist, and for an address that is not a number.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def two_orgs(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchitectureModel
    from app.models.user import User

    org_a, org_b = make_org("direct-a"), make_org("direct-b")
    architect = User(
        email=f"direct-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    app_a = ApplicationComponent(name="Billing (A)", organization_id=org_a.id)
    app_b = ApplicationComponent(name="Billing (B)", organization_id=org_b.id)
    model_a = ArchitectureModel(name="Target state (A)", version="1", organization_id=org_a.id)
    model_b = ArchitectureModel(name="Target state (B)", version="1", organization_id=org_b.id)
    db_session.add_all((architect, app_a, app_b, model_a, model_b))
    db_session.flush()
    assert architect.enterprise_role == "enterprise_architect"
    return {"user": architect, "app_a": app_a.id, "app_b": app_b.id, "model_a": model_a.id, "model_b": model_b.id}


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def test_architecture_elements_answer_for_own_application(client, login_as, two_orgs):
    resp = _get(client, login_as, two_orgs["user"], f"/api/applications/{two_orgs['app_a']}/architecture/elements")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["application_id"] == two_orgs["app_a"]
    assert body["elements"] == [] and body["count"] == 0


def test_architecture_export_answers_for_own_application(client, login_as, two_orgs):
    resp = _get(client, login_as, two_orgs["user"], f"/api/applications/{two_orgs['app_a']}/architecture/export-csv")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.mimetype == "text/csv"
    assert resp.get_data(as_text=True).splitlines()[0] == "ID,Type,Name,Description,Created At"


@pytest.mark.parametrize("tail", ["architecture/elements", "architecture/export-csv"])
def test_architecture_endpoints_refuse_others_missing_and_malformed(client, login_as, two_orgs, tail):
    for ident in (two_orgs["app_b"], 987654321, "not-a-number"):
        resp = _get(client, login_as, two_orgs["user"], f"/api/applications/{ident}/{tail}")
        assert resp.status_code == 404, (ident, resp.status_code)


def test_archimate_model_answers_with_its_details(client, login_as, two_orgs):
    resp = _get(client, login_as, two_orgs["user"], f"/api/v2/enterprise/archimate/models/{two_orgs['model_a']}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert data["id"] == two_orgs["model_a"] and data["name"] == "Target state (A)"


def test_archimate_model_refuses_others_missing_and_malformed(client, login_as, two_orgs):
    for ident in (two_orgs["model_b"], 987654321, "not-a-number"):
        resp = _get(client, login_as, two_orgs["user"], f"/api/v2/enterprise/archimate/models/{ident}")
        assert resp.status_code == 404, (ident, resp.status_code)


def test_application_capabilities_answer_for_own_application_only(client, login_as, two_orgs):
    resp = _get(client, login_as, two_orgs["user"], f"/api/v2/enterprise/applications/capabilities/{two_orgs['app_a']}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["count"] == 0
    for ident in (two_orgs["app_b"], 987654321, "not-a-number"):
        resp = _get(client, login_as, two_orgs["user"], f"/api/v2/enterprise/applications/capabilities/{ident}")
        assert resp.status_code == 404, (ident, resp.status_code)


def test_market_analysis_without_data_is_not_found(client, login_as, two_orgs, monkeypatch):
    from app.api import api_pipeline_routes

    monkeypatch.setattr(api_pipeline_routes.pipeline_orchestrator, "get_market_analysis", lambda category: {"success": False})
    resp = _get(client, login_as, two_orgs["user"], "/api/pipeline/market-analysis/no-such-category")
    assert resp.status_code == 404
    assert resp.get_json()["category"] == "no-such-category"
