"""The SWOT and PESTLE pages and their draft/accept routes.

Server-side coverage of what the page renders and what the two write routes
do, driven through the test client as a signed-in architect. The Playwright
journey (tests/smoke/test_strategy_draft_swot_journey.py) drives the rendered
page end to end; this file pins the route contract underneath it, including
two-organisation isolation.
"""
from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup

from app.services import strategy_draft_service as engine


def _architect(db_session, org_id, label="Arch"):
    import uuid

    from app.models.user import User

    user = User(
        email=f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, type_="ApplicationComponent"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _stub_llm(monkeypatch, responses):
    calls = {"i": 0}

    def fake(prompt, *args, **kwargs):
        i = calls["i"]
        calls["i"] = i + 1
        return responses[min(i, len(responses) - 1)]

    monkeypatch.setattr(
        "app.modules.ai_chat.services.llm_service_impl.LLMService.generate_from_prompt",
        staticmethod(fake),
    )
    return calls


class TestCanvasPage:
    def test_swot_renders_the_four_boxes_of_the_two_by_two(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("swot-page")
        user = _architect(db_session, org.id)
        login_as(client, user)

        resp = client.get("/strategy/swot")
        assert resp.status_code == 200
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")

        assert soup.find(attrs={"data-testid": "strategy-grid-swot"}) is not None
        for box in ("strengths", "weaknesses", "opportunities", "threats"):
            assert soup.find(attrs={"data-testid": f"strategy-box-{box}"}) is not None, box

    def test_pestle_renders_the_six_labelled_columns(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("pestle-page")
        user = _architect(db_session, org.id)
        login_as(client, user)

        resp = client.get("/strategy/pestle")
        assert resp.status_code == 200
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")

        assert soup.find(attrs={"data-testid": "strategy-grid-pestle"}) is not None
        for box in ("political", "economic", "social", "technological", "legal", "environmental"):
            assert soup.find(attrs={"data-testid": f"strategy-box-{box}"}) is not None, box

    def test_an_accepted_point_shows_in_its_box_with_a_working_citation_link(
        self, app, db_session, make_org, client, login_as, tenant_ctx
    ):
        org = make_org("swot-accepted")
        user = _architect(db_session, org.id)
        el = _element(db_session, org.id, "Payroll System")

        with tenant_ctx(org.id):
            engine.accept_point(
                "swot", "strengths", "Payroll is a core asset", [str(el.id)], org.id
            )
            db_session.flush()

        login_as(client, user)
        resp = client.get("/strategy/swot")
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")

        box = soup.find(attrs={"data-testid": "strategy-box-strengths"})
        assert "Payroll is a core asset" in box.get_text()
        # The citation renders as a link to a page with content (the element's
        # neighbourhood in the Composer), not a bare id.
        link = box.find(attrs={"data-testid": "strategy-citation-link"})
        assert link is not None
        assert link.get_text().strip() == "Payroll System"
        assert f"element={el.id}" in link["href"]

    def test_an_unknown_framework_is_not_found(self, app, db_session, make_org, client, login_as):
        org = make_org("swot-nf")
        user = _architect(db_session, org.id)
        login_as(client, user)
        assert client.get("/strategy/made-up").status_code == 404


class TestDraftRoute:
    def test_draft_returns_cited_proposals_per_box(
        self, app, db_session, make_org, client, login_as, monkeypatch
    ):
        org = make_org("swot-draft-route")
        user = _architect(db_session, org.id)
        el = _element(db_session, org.id, "Payroll System")

        response = {
            "strengths": [
                {"point": "Payroll is a core asset", "citations": [str(el.id)]},
                {"point": "Generic claim", "citations": []},
            ],
            "weaknesses": [], "opportunities": [], "threats": [],
        }
        _stub_llm(monkeypatch, [json.dumps(response), json.dumps(response)])

        login_as(client, user)
        resp = client.post("/strategy/swot/draft", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        body = data.get("data", data)
        strengths = body["boxes"]["strengths"]
        assert len(strengths) == 1
        assert strengths[0]["citations"][0]["label"] == "Payroll System"
        assert f"element={el.id}" in strengths[0]["citations"][0]["href"]


class TestAcceptRoute:
    def test_accept_persists_and_a_reload_shows_it(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("swot-accept-route")
        user = _architect(db_session, org.id)
        el = _element(db_session, org.id, "Payroll System")

        login_as(client, user)
        resp = client.post(
            "/strategy/swot/accept",
            json={"box_key": "strengths", "point": "Payroll is a core asset",
                  "citations": [str(el.id)]},
        )
        assert resp.status_code == 200

        # A fresh GET (the "reload") shows the saved point in its box.
        page = client.get("/strategy/swot")
        soup = BeautifulSoup(page.get_data(as_text=True), "html.parser")
        box = soup.find(attrs={"data-testid": "strategy-box-strengths"})
        assert "Payroll is a core asset" in box.get_text()

    def test_accept_refuses_an_uncited_point(self, app, db_session, make_org, client, login_as):
        org = make_org("swot-accept-uncited")
        user = _architect(db_session, org.id)
        login_as(client, user)
        resp = client.post(
            "/strategy/swot/accept",
            json={"box_key": "strengths", "point": "Floating claim", "citations": []},
        )
        assert resp.status_code == 400


class TestTwoOrganisationIsolation:
    def test_a_second_organisation_sees_none_of_the_firsts_points(
        self, app, db_session, make_org, client, login_as, tenant_ctx
    ):
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")
        el_a = _element(db_session, org_a.id, "Org A Payroll")
        with tenant_ctx(org_a.id):
            engine.accept_point(
                "swot", "strengths", "Org A strength", [str(el_a.id)], org_a.id
            )
            db_session.flush()

        user_b = _architect(db_session, org_b.id, "ArchB")
        login_as(client, user_b)
        resp = client.get("/strategy/swot")
        assert "Org A strength" not in resp.get_data(as_text=True)
