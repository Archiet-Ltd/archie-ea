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
        # The link lands on the cited element's own detail page (its id and
        # layer in the path), not a bare id or a composer query string.
        assert f"/{el.id}" in link["href"]
        assert "/application/" in link["href"]

    def test_an_unknown_framework_is_not_found(self, app, db_session, make_org, client, login_as):
        org = make_org("swot-nf")
        user = _architect(db_session, org.id)
        login_as(client, user)
        assert client.get("/strategy/made-up").status_code == 404


class TestElementBackedFrameworks:
    def test_a_product_sits_in_a_bcg_quadrant_and_an_ansoff_quadrant_at_once(
        self, app, db_session, make_org, client, login_as, tenant_ctx
    ):
        # The exclusivity bug: one shared profile key let a product sit in only
        # one of BCG and Ansoff. Each framework now reads its own membership
        # key, so a classified product appears in both, and each "not yet
        # classified" count is per framework and honest.
        org = make_org("bcg-ansoff")
        user = _architect(db_session, org.id)

        with tenant_ctx(org.id):
            engine.add_element("bcg_matrix", "stars", "Flagship product", org.id,
                               element_type="Product")
            db_session.flush()
            engine.add_element("ansoff_matrix", "market_penetration", "Flagship product 2", org.id,
                               element_type="Product")
            db_session.flush()
            # Classify the SAME product in both frameworks.
            from app.models.archimate_core import ArchiMateElement
            from app.services.composer_export_formats import canvas_profile_key
            prod = ArchiMateElement.query.filter_by(
                organization_id=org.id, name="Flagship product"
            ).first()
            props = dict(prod.acm_properties or {})
            props[canvas_profile_key("ansoff_matrix")] = "diversification"
            prod.acm_properties = props
            db_session.flush()

            bcg = engine.project("bcg_matrix", org.id)
            ansoff = engine.project("ansoff_matrix", org.id)

        # The one product is a Star in BCG and in Diversification in Ansoff.
        assert any(e["name"] == "Flagship product" for e in bcg["stars"])
        assert any(e["name"] == "Flagship product" for e in ansoff["diversification"])

    def test_unclassified_count_is_per_framework(
        self, app, db_session, make_org, tenant_ctx
    ):
        org = make_org("unclassified-count")
        with tenant_ctx(org.id):
            # One product classified in BCG only.
            engine.add_element("bcg_matrix", "stars", "Classified in BCG", org.id,
                               element_type="Product")
            db_session.flush()
            # BCG: the one product is classified, so 0 unclassified.
            assert engine.unclassified_count("bcg_matrix", org.id) == 0
            # Ansoff: the same product carries no Ansoff value, so it is
            # unclassified in Ansoff — counted here, never in BCG.
            assert engine.unclassified_count("ansoff_matrix", org.id) == 1

    def test_add_surfaces_a_failure_rather_than_swallowing_it(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("add-fail")
        user = _architect(db_session, org.id)
        login_as(client, user)
        # A blank name is rejected; the failure is carried back as a message.
        resp = client.post(
            "/strategy/bcg_matrix/add",
            data={"box_key": "stars", "name": "", "element_type": "Product"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303)
        assert "add_error" in resp.headers.get("Location", "")


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
        assert f"/{el.id}" in strengths[0]["citations"][0]["href"]
        assert "/application/" in strengths[0]["citations"][0]["href"]


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

    def test_a_session_naming_a_foreign_organisation_is_refused_it(
        self, app, db_session, make_org, client, login_as, tenant_ctx
    ):
        # The switched-organisation case: a user native to org A whose session
        # names org B (which they do not belong to) as the active organisation
        # is refused B and kept in their own A. They never see B's points and a
        # write never lands in B.
        from app.middleware.tenant_context import ACTIVE_ORG_SESSION_KEY

        org_a = make_org("switch-home")
        org_b = make_org("switch-foreign")
        el_a = _element(db_session, org_a.id, "Org A record")
        el_b = _element(db_session, org_b.id, "Org B record")
        with tenant_ctx(org_a.id):
            engine.accept_point("swot", "strengths", "Org A only point", [str(el_a.id)], org_a.id)
            db_session.flush()
        with tenant_ctx(org_b.id):
            engine.accept_point("swot", "strengths", "Org B only point", [str(el_b.id)], org_b.id)
            db_session.flush()

        user = _architect(db_session, org_a.id, "HomeA")
        login_as(client, user)
        # Point the session's active organisation at the foreign org B.
        with client.session_transaction() as sess:
            sess[ACTIVE_ORG_SESSION_KEY] = org_b.id

        page = client.get("/strategy/swot").get_data(as_text=True)
        # Refused B, kept in A: A's point shows, B's never does.
        assert "Org A only point" in page
        assert "Org B only point" not in page

        # A write while the session names B lands in the user's real org A,
        # never in the foreign org B.
        resp = client.post(
            "/strategy/swot/accept",
            json={"box_key": "weaknesses", "point": "Written after a foreign switch",
                  "citations": [str(el_a.id)]},
        )
        assert resp.status_code == 200
        from app.models.archimate_core import ArchiMateElement
        assert ArchiMateElement.query.filter_by(
            organization_id=org_a.id, name="Written after a foreign switch"
        ).count() == 1
        assert ArchiMateElement.query.filter_by(
            organization_id=org_b.id, name="Written after a foreign switch"
        ).count() == 0


class TestWriteAuthorisation:
    def test_a_viewer_cannot_add_or_accept(self, app, db_session, make_org, client, login_as):
        # A read-only user (no architect role) is refused the write routes, and
        # nothing is created.
        import uuid

        from app.models.user import User

        from app.models.user import Role

        org = make_org("viewer")
        el = _element(db_session, org.id, "A record")
        Role.insert_roles()
        viewer_role = Role.query.filter_by(name="Viewer").one()
        viewer = User(
            email=f"viewer-{uuid.uuid4().hex[:8]}@example.com",
            organization_id=org.id,
            enterprise_role="viewer",
            confirmed=True,
        )
        viewer.role = viewer_role
        db_session.add(viewer)
        db_session.flush()
        login_as(client, viewer)

        accept = client.post(
            "/strategy/swot/accept",
            json={"box_key": "strengths", "point": "Viewer tried", "citations": [str(el.id)]},
        )
        assert accept.status_code in (401, 403)

        add = client.post(
            "/strategy/bcg_matrix/add",
            data={"box_key": "stars", "name": "Viewer product", "element_type": "Product"},
        )
        assert add.status_code in (401, 403)

        from app.models.archimate_core import ArchiMateElement
        assert ArchiMateElement.query.filter_by(
            organization_id=org.id, name="Viewer tried"
        ).count() == 0
        assert ArchiMateElement.query.filter_by(
            organization_id=org.id, name="Viewer product"
        ).count() == 0
