"""VRIO, McKinsey 7S and Porter's five forces strategy canvases.

Covers: the three new CANVAS_TEMPLATES entries and their schema; the
projection that places an organisation's own elements into a box by profile;
two-organisation isolation (one organisation never sees another's elements);
each page opening in its recognisable layout and showing a seeded element in
its box with an empty box explained; and that each prompt file names every box
of its template.
"""
from __future__ import annotations

import pathlib
import uuid

import pytest
from bs4 import BeautifulSoup

from app.config.archimate_viewpoints import CANVAS_TEMPLATES

STRATEGY_KEYS = ["mckinsey_7s", "porters_five_forces", "vrio"]


def _make_user(db_session, org_id, label):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seed_element(db_session, org_id, element_type, profile, name, layer="Motivation"):
    """Create a native ArchiMate element in an organisation carrying a box's
    profile — the same shape add-from-canvas writes."""
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        organization_id=org_id,
        name=name,
        type=element_type,
        layer=layer,
        scope="enterprise",
        acm_properties={"profile": profile},
    )
    db_session.add(el)
    db_session.flush()
    return el


# -- Schema -------------------------------------------------------------------


class TestSchema:
    def test_six_templates_now_exist(self):
        assert sorted(CANVAS_TEMPLATES) == [
            "business_case", "business_model_canvas", "lean_canvas",
            "mckinsey_7s", "porters_five_forces", "vrio",
        ]

    def test_strategy_canvases_have_the_recognisable_box_counts(self):
        assert len(CANVAS_TEMPLATES["vrio"]["zones"]) == 5
        assert len(CANVAS_TEMPLATES["mckinsey_7s"]["zones"]) == 7
        assert len(CANVAS_TEMPLATES["porters_five_forces"]["zones"]) == 5

    def test_strategy_canvas_boxes_are_type_profile_over_assessment_driver_goal(self):
        allowed = {"Assessment", "Driver", "Goal"}
        for key in STRATEGY_KEYS:
            for zone in CANVAS_TEMPLATES[key]["zones"]:
                assert zone["membership"] == "type_profile", (key, zone["box_key"])
                assert set(zone["element_types"]) <= allowed, (key, zone["box_key"])
                assert zone["profile"], (key, zone["box_key"])

    def test_vrio_has_the_four_questions_and_the_implication_column(self):
        box_keys = [z["box_key"] for z in CANVAS_TEMPLATES["vrio"]["zones"]]
        assert box_keys == [
            "valuable", "rare", "inimitable", "organised", "competitive_implication",
        ]

    def test_seven_s_has_shared_values_and_the_six_others(self):
        labels = {z["label"] for z in CANVAS_TEMPLATES["mckinsey_7s"]["zones"]}
        assert "Shared values" in labels
        assert {"Strategy", "Structure", "Systems", "Style", "Staff", "Skills"} <= labels

    def test_porter_has_rivalry_and_the_four_forces(self):
        box_keys = {z["box_key"] for z in CANVAS_TEMPLATES["porters_five_forces"]["zones"]}
        assert "competitive_rivalry" in box_keys
        assert {
            "supplier_power", "buyer_power",
            "threat_of_substitution", "threat_of_new_entrants",
        } <= box_keys

    @pytest.mark.parametrize("key", STRATEGY_KEYS)
    def test_prompt_file_exists_and_names_every_box(self, key):
        path = pathlib.Path("app/prompts/strategy") / f"{key}.txt"
        assert path.exists(), path
        text = path.read_text(encoding="utf-8").lower()
        for zone in CANVAS_TEMPLATES[key]["zones"]:
            assert zone["label"].lower() in text, (key, zone["label"])


# -- Projection and two-organisation isolation --------------------------------


class TestProjection:
    def test_a_seeded_element_lands_in_its_box(self, app, db_session, make_org, tenant_ctx):
        from app.services.strategy_canvas_projection import project_canvas

        org = make_org("vrio-projection")
        zone = CANVAS_TEMPLATES["vrio"]["zones"][0]  # "valuable", Assessment
        _seed_element(db_session, org.id, "Assessment", zone["profile"], "Brand reputation")

        with tenant_ctx(org.id):
            projected = project_canvas("vrio")

        by_key = {z["box_key"]: z for z in projected}
        names = [e["name"] for e in by_key[zone["box_key"]]["entries"]]
        assert names == ["Brand reputation"]
        # Every other box is empty for this tenant.
        for other in projected:
            if other["box_key"] != zone["box_key"]:
                assert other["entries"] == [], other["box_key"]

    def test_a_second_organisation_sees_none_of_the_first_s_elements(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.strategy_canvas_projection import project_canvas

        org_a = make_org("iso-a")
        org_b = make_org("iso-b")
        zone = CANVAS_TEMPLATES["porters_five_forces"]["zones"][0]
        _seed_element(db_session, org_a.id, "Assessment", zone["profile"], "Org A rivalry note")

        with tenant_ctx(org_a.id):
            a_names = {
                e["name"]
                for z in project_canvas("porters_five_forces")
                for e in z["entries"]
            }
        with tenant_ctx(org_b.id):
            b_names = {
                e["name"]
                for z in project_canvas("porters_five_forces")
                for e in z["entries"]
            }

        assert "Org A rivalry note" in a_names
        assert b_names == set()

    def test_an_element_with_an_unnamed_profile_counts_as_unclassified(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.services.strategy_canvas_projection import project_canvas, unclassified_count

        org = make_org("vrio-unclassified")
        _seed_element(db_session, org.id, "Assessment", "not_a_vrio_box", "Stray assessment")

        with tenant_ctx(org.id):
            projected = project_canvas("vrio")
            count = unclassified_count("vrio")

        assert all(z["entries"] == [] for z in projected)
        assert count == 1


# -- Pages open in their recognisable layout ----------------------------------


class TestPagesOpen:
    @pytest.mark.parametrize("key", STRATEGY_KEYS)
    def test_each_canvas_opens_for_a_signed_in_architect(
        self, app, db_session, make_org, client, login_as, key
    ):
        org = make_org(f"open-{key}")
        user = _make_user(db_session, org.id, "Opener")
        login_as(client, user)
        resp = client.get(f"/strategy-canvas/{key}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert f'data-testid="strategy-canvas-{key}"' in html
        # Every box label appears on the page.
        for zone in CANVAS_TEMPLATES[key]["zones"]:
            assert zone["label"] in html, zone["label"]

    def test_a_seeded_element_shows_in_its_box_and_empty_boxes_explain(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("vrio-page")
        user = _make_user(db_session, org.id, "Viewer")
        valuable = CANVAS_TEMPLATES["vrio"]["zones"][0]
        _seed_element(db_session, org.id, "Assessment", valuable["profile"], "Patented process")

        login_as(client, user)
        resp = client.get("/strategy-canvas/vrio")
        assert resp.status_code == 200
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")

        # The seeded element shows in the Valuable box as a link.
        entries = soup.find(attrs={"data-testid": f"canvas-entries-{valuable['box_key']}"})
        assert entries is not None
        assert "Patented process" in entries.get_text()

        # Every other box has no entries and shows its explained reason.
        for zone in CANVAS_TEMPLATES["vrio"]["zones"][1:]:
            reason = soup.find(attrs={"data-testid": f"canvas-box-reason-{zone['box_key']}"})
            assert reason is not None, zone["box_key"]
            assert "Nothing here yet" in reason.get_text()

    def test_a_foreign_organisation_does_not_see_the_element_on_the_page(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("page-iso-a")
        org_b = make_org("page-iso-b")
        user_b = _make_user(db_session, org_b.id, "ForeignViewer")
        valuable = CANVAS_TEMPLATES["vrio"]["zones"][0]
        _seed_element(db_session, org_a.id, "Assessment", valuable["profile"], "Org A secret sauce")

        login_as(client, user_b)
        resp = client.get("/strategy-canvas/vrio")
        assert resp.status_code == 200
        assert "Org A secret sauce" not in resp.get_data(as_text=True)

    def test_an_unknown_canvas_is_not_found(self, app, db_session, make_org, client, login_as):
        org = make_org("nf")
        user = _make_user(db_session, org.id, "NfViewer")
        login_as(client, user)
        resp = client.get("/strategy-canvas/not_a_real_canvas")
        assert resp.status_code == 404


# -- Add from the canvas lands in the box -------------------------------------


class TestAddFromCanvas:
    def test_adding_an_element_lands_in_that_box_after_reload(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("add-box")
        user = _make_user(db_session, org.id, "Adder")
        rivalry = CANVAS_TEMPLATES["porters_five_forces"]["zones"][0]

        login_as(client, user)
        post = client.post(
            "/strategy-canvas/porters_five_forces/add",
            data={"box_key": rivalry["box_key"], "name": "Three direct competitors"},
        )
        assert post.status_code in (302, 303)

        resp = client.get("/strategy-canvas/porters_five_forces")
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
        entries = soup.find(attrs={"data-testid": f"canvas-entries-{rivalry['box_key']}"})
        assert entries is not None
        assert "Three direct competitors" in entries.get_text()
