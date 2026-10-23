"""The element-backed strategy canvases place profiled elements in their
boxes, tenant by tenant.

Covers the projection (a Product/Business Service/Assessment/Goal carrying a
box profile lands in that box), the Blue Ocean value curve reading its
competing-factor levels, the unclassified count, the add-from-canvas writer,
and that a second organisation sees none of the first's elements.
"""
from __future__ import annotations

import pytest

from app.services import strategy_canvas_service as svc


def _element(db_session, org_id, name, element_type, layer, profile=None, **props):
    from app.models.archimate_core import ArchiMateElement

    acm = {}
    if profile is not None:
        acm["profile"] = profile
    acm.update(props)
    element = ArchiMateElement(
        organization_id=org_id,
        name=name,
        type=element_type,
        layer=layer,
        scope="enterprise",
        acm_properties=acm,
    )
    db_session.add(element)
    db_session.flush()
    return element


class TestProjection:
    def test_bcg_products_land_in_their_quadrant(self, app, db_session, make_org, tenant_ctx):
        org = make_org("bcg-place")
        _element(db_session, org.id, "Flagship", "Product", "Business", profile="star")
        _element(db_session, org.id, "Newbie", "Product", "Business", profile="question_mark")
        _element(db_session, org.id, "Steady", "Product", "Business", profile="cash_cow")

        with tenant_ctx(org.id):
            placed = svc.project("bcg_matrix")

        assert [e["name"] for e in placed["stars"]] == ["Flagship"]
        assert [e["name"] for e in placed["question_marks"]] == ["Newbie"]
        assert [e["name"] for e in placed["cash_cows"]] == ["Steady"]
        assert placed["dogs"] == []

    def test_ansoff_products_and_services_land_in_their_quadrant(
        self, app, db_session, make_org, tenant_ctx
    ):
        org = make_org("ansoff-place")
        _element(db_session, org.id, "Core upsell", "Product", "Business", profile="market_penetration")
        _element(db_session, org.id, "New region", "BusinessService", "Business", profile="market_development")

        with tenant_ctx(org.id):
            placed = svc.project("ansoff_matrix")

        assert [e["name"] for e in placed["market_penetration"]] == ["Core upsell"]
        assert [e["name"] for e in placed["market_development"]] == ["New region"]
        assert placed["product_development"] == []
        assert placed["diversification"] == []

    def test_blue_ocean_actions_and_value_curve(self, app, db_session, make_org, tenant_ctx):
        org = make_org("blue-ocean-place")
        _element(db_session, org.id, "Drop paperwork", "Goal", "Motivation", profile="eliminate")
        _element(db_session, org.id, "Lift support", "Goal", "Motivation", profile="raise")
        _element(
            db_session, org.id, "Price", "Assessment", "Motivation",
            profile="competing_factor", offering_level=3, player="Us",
        )
        _element(
            db_session, org.id, "Ease of use", "Assessment", "Motivation",
            profile="competing_factor", offering_level=8, player="Us",
        )

        with tenant_ctx(org.id):
            placed = svc.project("blue_ocean_strategy")

        assert [e["name"] for e in placed["eliminate"]] == ["Drop paperwork"]
        assert [e["name"] for e in placed["raise"]] == ["Lift support"]
        assert placed["reduce"] == []
        assert placed["create"] == []

        curve = {p["factor"]: p for p in placed["value_curve"]}
        assert curve["Price"]["offering_level"] == 3
        assert curve["Ease of use"]["offering_level"] == 8
        assert curve["Price"]["player"] == "Us"

    def test_value_curve_missing_level_stays_none(self, app, db_session, make_org, tenant_ctx):
        org = make_org("blue-ocean-gap")
        _element(
            db_session, org.id, "Brand", "Assessment", "Motivation",
            profile="competing_factor", player="Us",
        )
        with tenant_ctx(org.id):
            placed = svc.project("blue_ocean_strategy")
        assert placed["value_curve"][0]["offering_level"] is None

    def test_unclassified_counts_profileless_elements(self, app, db_session, make_org, tenant_ctx):
        org = make_org("bcg-unclassified")
        _element(db_session, org.id, "Classified", "Product", "Business", profile="star")
        _element(db_session, org.id, "Loose one", "Product", "Business")
        _element(db_session, org.id, "Loose two", "Product", "Business")

        with tenant_ctx(org.id):
            assert svc.unclassified_count("bcg_matrix") == 2


class TestAddFromCanvas:
    def test_add_element_files_it_in_the_box(self, app, db_session, make_org, tenant_ctx):
        org = make_org("bcg-add")
        with tenant_ctx(org.id):
            element = svc.add_element("bcg_matrix", "cash_cows", "Mature line")
            db_session.flush()
            placed = svc.project("bcg_matrix")

        assert element.type == "Product"
        assert svc._read_profile_value(element) == "cash_cow"
        assert [e["name"] for e in placed["cash_cows"]] == ["Mature line"]

    def test_add_competing_factor_with_level(self, app, db_session, make_org, tenant_ctx):
        org = make_org("blue-ocean-add")
        with tenant_ctx(org.id):
            svc.add_element(
                "blue_ocean_strategy", "competing_factors", "Speed",
                offering_level=7, player="Us",
            )
            db_session.flush()
            placed = svc.project("blue_ocean_strategy")

        point = placed["value_curve"][0]
        assert point["factor"] == "Speed"
        assert point["offering_level"] == 7

    def test_add_rejects_a_blank_name(self, app, db_session, make_org, tenant_ctx):
        org = make_org("bcg-add-blank")
        with tenant_ctx(org.id):
            with pytest.raises(ValueError):
                svc.add_element("bcg_matrix", "stars", "   ")

    def test_add_rejects_an_attribute_box(self, app, db_session, make_org, tenant_ctx):
        org = make_org("bcg-add-attr")
        with tenant_ctx(org.id):
            with pytest.raises(ValueError):
                svc.add_element("blue_ocean_strategy", "value_curve", "x")


class TestTwoOrganisationIsolation:
    def test_second_org_sees_none_of_the_first_orgs_elements(
        self, app, db_session, make_org, tenant_ctx
    ):
        org_a = make_org("strat-iso-a")
        org_b = make_org("strat-iso-b")
        _element(db_session, org_a.id, "A star", "Product", "Business", profile="star")
        _element(db_session, org_a.id, "A cow", "Product", "Business", profile="cash_cow")
        _element(db_session, org_b.id, "B star", "Product", "Business", profile="star")

        with tenant_ctx(org_a.id):
            placed_a = svc.project("bcg_matrix")
        with tenant_ctx(org_b.id):
            placed_b = svc.project("bcg_matrix")

        assert [e["name"] for e in placed_a["stars"]] == ["A star"]
        assert [e["name"] for e in placed_a["cash_cows"]] == ["A cow"]

        assert [e["name"] for e in placed_b["stars"]] == ["B star"]
        assert placed_b["cash_cows"] == []
        all_b_names = {e["name"] for box in placed_b.values() for e in box}
        assert "A star" not in all_b_names
        assert "A cow" not in all_b_names
