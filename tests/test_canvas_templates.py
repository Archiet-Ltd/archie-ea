"""Canvas templates as data — schema, seeding, and the two pages' structure.

Covers: (1) the static schema/placeholder test over CANVAS_TEMPLATES; (2)
that the Composer payload shape carries `zones`/`entries` for a canvas key
and that CANVAS_TEMPLATES' own zone order is a dense, ascending sequence the
page templates render one row per zone from; (3) seed_canvas_templates()
idempotence; (4) both detail pages render for an empty tenant with the
empty hint outside the input, "0 not yet classified", and no other literal
zero; (6) a foreign canvas id and a foreign business case id return the
page's existing not-found bytes.

This is data-and-render only: no projection yet, so every box's entries
list stays empty and every zone carries its empty_reason (a later change
fills the entries).
"""
from __future__ import annotations

import copy
import re
import uuid

import pytest
from bs4 import BeautifulSoup

from app.config.archimate_viewpoints import (
    CANVAS_PROFILE_OPTIONS_BY_TYPE,
    CANVAS_TEMPLATES,
    validate_canvas_templates,
)


def _without_per_request_nonce(html_text):
    """Strip the CSP nonce every response carries — a fresh random value on
    each request, present regardless of which id was asked for — so two
    separate requests' bodies can be compared for anything else."""
    return re.sub(r'nonce="[^"]*"', 'nonce="NONCE"', html_text)


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


def _content_root(soup, factory):
    """The Alpine component's own wrapper div — scopes a text search to the
    canvas page's own markup, not the surrounding admin shell/sidebar, which
    may carry its own digits. Strips <style>/<script> tags first: their
    source text (CSS pixel/rem values, JS numeric literals) is not content a
    reader sees, and .get_text() would otherwise count a plain "0" inside
    one as if it were a rendered digit."""
    root = soup.find(lambda tag: tag.name == "div" and tag.get("x-data", "").startswith(factory + "("))
    assert root is not None, f"no {factory}(...) x-data root found"
    for tag in root.find_all(["style", "script"]):
        tag.decompose()
    return root


# -- Schema and the static placeholder test ----------------------------------


class TestSchema:
    def test_validate_canvas_templates_passes_on_the_real_templates(self):
        validate_canvas_templates()

    def test_three_templates_with_the_srs_zone_counts(self):
        assert sorted(CANVAS_TEMPLATES) == ["business_case", "business_model_canvas", "lean_canvas"]
        assert [len(CANVAS_TEMPLATES[k]["zones"]) for k in sorted(CANVAS_TEMPLATES)] == [9, 9, 9]

    def test_bmc_box_keys_equal_the_record_columns(self):
        from app.models.business_model import CANVAS_BLOCKS

        keys = {z["box_key"] for z in CANVAS_TEMPLATES["business_model_canvas"]["zones"]}
        assert keys == set(CANVAS_BLOCKS)

    def test_case_zones_with_an_existing_column_use_the_column_name_as_box_key(self):
        from app.models.business_case import BusinessCase

        columns = set(BusinessCase.__table__.columns.keys())
        zones = {z["box_key"] for z in CANVAS_TEMPLATES["business_case"]["zones"]}
        for box_key in ("problem_statement", "options_considered", "expected_benefits", "key_risks"):
            assert box_key in columns
            assert box_key in zones

    def test_every_zone_element_type_has_a_profile_options_entry(self):
        all_types = {
            t for tpl in CANVAS_TEMPLATES.values() for z in tpl["zones"] for t in z["element_types"]
        }
        missing = all_types - set(CANVAS_PROFILE_OPTIONS_BY_TYPE)
        assert not missing, f"element types with no seeded profile options: {missing}"

    def test_no_literal_colour_anywhere_in_the_config_module(self):
        with open("app/config/archimate_viewpoints.py", encoding="utf-8") as fh:
            src = fh.read()
        assert not re.search(r"#[0-9a-fA-F]{6}", src)

    def test_validator_fails_on_placeholder_text_in_a_label(self):
        bad = copy.deepcopy(CANVAS_TEMPLATES)
        bad["lean_canvas"]["zones"][0]["label"] = "Lorem ipsum placeholder"
        with pytest.raises(AssertionError):
            validate_canvas_templates(bad)

    def test_validator_fails_on_a_literal_colour(self):
        bad = copy.deepcopy(CANVAS_TEMPLATES)
        bad["lean_canvas"]["zones"][0]["colour"] = "#112233"
        with pytest.raises(AssertionError):
            validate_canvas_templates(bad)

    def test_validator_fails_on_a_duplicate_box_key(self):
        bad = copy.deepcopy(CANVAS_TEMPLATES)
        bad["lean_canvas"]["zones"][1]["box_key"] = bad["lean_canvas"]["zones"][0]["box_key"]
        with pytest.raises(AssertionError):
            validate_canvas_templates(bad)

    def test_validator_fails_when_an_element_zone_carries_no_flags(self):
        bad = copy.deepcopy(CANVAS_TEMPLATES)
        bad["lean_canvas"]["zones"][0]["flags"] = []
        with pytest.raises(AssertionError):
            validate_canvas_templates(bad)

    def test_validator_fails_on_an_unknown_membership(self):
        bad = copy.deepcopy(CANVAS_TEMPLATES)
        bad["lean_canvas"]["zones"][0]["membership"] = "made_up"
        with pytest.raises(AssertionError):
            validate_canvas_templates(bad)


# -- Seed idempotence ---------------------------------------------------------


class TestSeedIdempotence:
    def test_seed_canvas_templates_twice_changes_nothing_the_second_time(
        self, app, db_session, make_org
    ):
        from app.commands.seed_viewpoints import seed_canvas_templates
        from app.models.acm_property_template import AcmPropertyTemplate
        from app.models.archimate_viewpoint import ArchiMateViewpoint

        # Named explicitly, the same way the CLI command takes --org-id: the
        # seed does not have to guess a tenant among however many other
        # organizations already exist in the database.
        org = make_org("canvas-seed-idempotence")

        profile_type_count = len(CANVAS_PROFILE_OPTIONS_BY_TYPE)

        first = seed_canvas_templates(org_id=org.id)
        assert first == (3, 0, profile_type_count, 0)

        second = seed_canvas_templates(org_id=org.id)
        assert second == (0, 3, 0, profile_type_count)

        canvas_rows = ArchiMateViewpoint.query.filter_by(viewpoint_type="canvas").all()
        assert len(canvas_rows) == 3
        assert {row.is_standard for row in canvas_rows} == {False}

        profile_rows = AcmPropertyTemplate.query.filter_by(property_key="profile").all()
        assert len(profile_rows) == profile_type_count
        by_type = {row.archimate_type: row.enum_options for row in profile_rows}
        for archimate_type, options in CANVAS_PROFILE_OPTIONS_BY_TYPE.items():
            assert by_type[archimate_type] == options

    def test_seed_viewpoints_calls_seed_canvas_templates(self, app, db_session, make_org):
        from app.commands.seed_viewpoints import seed_viewpoints
        from app.models.archimate_viewpoint import ArchiMateViewpoint

        # Same explicit tenant as above: seed_viewpoints() also inserts the
        # standard viewpoints, which carry the same tenant column.
        org = make_org("canvas-seed-viewpoints")

        seed_viewpoints(org_id=org.id)
        assert ArchiMateViewpoint.query.filter_by(viewpoint_type="canvas").count() == 3


# -- Composer render shape ----------------------------------------------------


class TestComposerRenderShape:
    @pytest.mark.parametrize("key", sorted(CANVAS_TEMPLATES))
    def test_get_viewpoint_data_carries_zones_and_entries_present_and_empty(self, key):
        from app.services.archimate_viewpoint_service import get_viewpoint_data

        data = get_viewpoint_data(key)
        assert data["scope_required"] is False
        assert "zones" in data and data["zones"] == []
        assert "entries" in data and data["entries"] == []
        assert data["elements"] == []
        assert data["relationships"] == []

    @pytest.mark.parametrize("key", sorted(CANVAS_TEMPLATES))
    def test_template_zone_order_is_dense_and_ascending(self, key):
        zones = CANVAS_TEMPLATES[key]["zones"]
        orders = [z["order"] for z in zones]
        assert orders == sorted(orders)
        assert orders == list(range(1, len(zones) + 1))
        assert [z["phone_order"] for z in zones] == orders

    def test_get_available_viewpoints_lists_the_three_templates_as_canvas_category(self):
        from app.services.archimate_viewpoint_service import get_available_viewpoints

        entries = {v["id"]: v for v in get_available_viewpoints() if v["category"] == "canvas"}
        assert set(entries) == set(CANVAS_TEMPLATES)
        for key, tpl in CANVAS_TEMPLATES.items():
            assert entries[key]["name"] == tpl["name"]

    def test_a_non_canvas_key_is_unaffected(self):
        from app.services.archimate_viewpoint_service import STANDARD_VIEWPOINTS, get_viewpoint_data

        assert "zones" not in STANDARD_VIEWPOINTS["motivation"]
        # 'motivation' has no solution_id and is not enterprise_scope, so it
        # still asks for scope — proving the canvas short-circuit added above
        # it did not change this existing invariant.
        data = get_viewpoint_data("motivation")
        assert data["scope_required"] is True
        assert "zones" not in data


# -- Both pages render for an empty tenant -----------------------------------


class TestBothPagesRenderEmpty:
    def test_bmc_page_shows_the_empty_hint_and_zero_unclassified(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.business_model import BusinessModelCanvas

        org = make_org("canvas-bmc-empty")
        user = _make_user(db_session, org.id, "BmcEmptyOwner")
        canvas = BusinessModelCanvas(name="Empty Canvas", organization_id=org.id)
        db_session.add(canvas)
        db_session.flush()
        canvas_id = canvas.id

        login_as(client, user)
        resp = client.get(f"/business-model/{canvas_id}")
        assert resp.status_code == 200
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")

        zones = CANVAS_TEMPLATES["business_model_canvas"]["zones"]
        headers = soup.find_all(attrs={"data-testid": re.compile(r"^canvas-box-reason-")})
        assert len(headers) == len(zones)

        for zone in zones:
            reason = soup.find(attrs={"data-testid": f"canvas-box-reason-{zone['box_key']}"})
            assert reason is not None, zone["box_key"]
            assert "Nothing here yet" in reason.get_text()
            # The hint sits outside the input: never rendered inside the
            # existing textarea's own value/placeholder as example content.
            textarea = soup.find(attrs={"data-testid": f"bmc-textarea-{zone['box_key']}"})
            assert textarea is not None
            assert (textarea.string or "") == ""

        unclassified = soup.find(attrs={"data-testid": "canvas-unclassified"})
        assert unclassified is not None
        assert " ".join(unclassified.get_text().split()) == "0 not yet classified"

        content = _content_root(soup, "businessModelCanvas")
        zero_tokens = re.findall(r"(?<!\d)0(?!\d)", content.get_text())
        assert len(zero_tokens) == 1, "a literal 0 outside the unclassified count: %r" % content.get_text()

    def test_business_case_page_shows_the_empty_hint_and_zero_unclassified(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.business_case import BusinessCase

        org = make_org("canvas-case-empty")
        user = _make_user(db_session, org.id, "CaseEmptyOwner")
        case = BusinessCase(title="Empty Case", organization_id=org.id)
        db_session.add(case)
        db_session.flush()
        case_id = case.id

        login_as(client, user)
        resp = client.get(f"/business-case/{case_id}")
        assert resp.status_code == 200
        soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")

        zones = CANVAS_TEMPLATES["business_case"]["zones"]
        headers = soup.find_all(attrs={"data-testid": re.compile(r"^canvas-box-reason-")})
        assert len(headers) == len(zones)

        for zone in zones:
            reason = soup.find(attrs={"data-testid": f"canvas-box-reason-{zone['box_key']}"})
            assert reason is not None, zone["box_key"]
            if zone["empty_reason"] == "canvas_box_not_derived":
                assert "Composed from the other sections" in reason.get_text()
            else:
                assert "Nothing here yet" in reason.get_text()

        unclassified = soup.find(attrs={"data-testid": "canvas-unclassified"})
        assert unclassified is not None
        assert " ".join(unclassified.get_text().split()) == "0 not yet classified"

        content = _content_root(soup, "businessCaseDetail")
        zero_tokens = re.findall(r"(?<!\d)0(?!\d)", content.get_text())
        assert len(zero_tokens) == 1, "a literal 0 outside the unclassified count: %r" % content.get_text()

    def test_case_box_order_matches_the_record_order(self, app, db_session, make_org, client, login_as):
        """The nine box headers appear in the page source in exactly the
        order CANVAS_TEMPLATES declares — the same order at 360px, since
        this section is a single column at every width."""
        from app.models.business_case import BusinessCase

        org = make_org("canvas-case-order")
        user = _make_user(db_session, org.id, "CaseOrderOwner")
        case = BusinessCase(title="Order Case", organization_id=org.id)
        db_session.add(case)
        db_session.flush()
        case_id = case.id

        login_as(client, user)
        resp = client.get(f"/business-case/{case_id}")
        html = resp.get_data(as_text=True)

        zones = sorted(CANVAS_TEMPLATES["business_case"]["zones"], key=lambda z: z["order"])
        positions = [html.index(f'canvas-box-reason-{z["box_key"]}"') for z in zones]
        assert positions == sorted(positions), [z["box_key"] for z in zones]


# -- Foreign ids return the page's existing not-found bytes ------------------


class TestForeignIdReturnsNotFoundBytes:
    def test_foreign_canvas_id_returns_the_pages_not_found_bytes(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.business_model import BusinessModelCanvas

        org_a = make_org("canvas-nf-bmc-a")
        org_b = make_org("canvas-nf-bmc-b")
        user_b = _make_user(db_session, org_b.id, "ForeignCanvasViewer")
        canvas = BusinessModelCanvas(name="Org A Canvas", organization_id=org_a.id)
        db_session.add(canvas)
        db_session.flush()
        canvas_id = canvas.id

        login_as(client, user_b)
        own_missing = client.get("/business-model/999999999")
        foreign = client.get(f"/business-model/{canvas_id}")

        assert own_missing.status_code == 404
        assert foreign.status_code == 404
        # The not-found page echoes back the id the caller typed in the URL,
        # which differs between the two requests by design (999999999 vs the
        # real, foreign id) and carries no information the caller didn't
        # already have. Compare the page with that one distinguishing number
        # normalised out, so the assertion is about the page shown -- one
        # not-found template, never the record's own data -- not about the
        # two numbers happening to match.
        own_text = _without_per_request_nonce(
            re.sub(r"(?<!&)#999999999(?![\da-fA-F])", "#ID", own_missing.get_data(as_text=True))
        )
        foreign_text = _without_per_request_nonce(
            re.sub(rf"(?<!&)#{canvas_id}(?![\da-fA-F])", "#ID", foreign.get_data(as_text=True))
        )
        assert foreign_text == own_text
        assert "Org A Canvas" not in foreign_text

    def test_foreign_business_case_id_returns_the_pages_not_found_bytes(
        self, app, db_session, make_org, client, login_as
    ):
        from app.models.business_case import BusinessCase

        org_a = make_org("canvas-nf-case-a")
        org_b = make_org("canvas-nf-case-b")
        user_b = _make_user(db_session, org_b.id, "ForeignCaseViewer")
        case = BusinessCase(title="Org A Case", organization_id=org_a.id)
        db_session.add(case)
        db_session.flush()
        case_id = case.id

        login_as(client, user_b)
        own_missing = client.get("/business-case/999999999")
        foreign = client.get(f"/business-case/{case_id}")

        assert own_missing.status_code == 404
        assert foreign.status_code == 404
        # Same normalisation as the canvas case above: the id the caller
        # typed is echoed back and differs by design; the page itself must
        # not otherwise differ.
        own_text = _without_per_request_nonce(
            re.sub(r"(?<!&)#999999999(?![\da-fA-F])", "#ID", own_missing.get_data(as_text=True))
        )
        foreign_text = _without_per_request_nonce(
            re.sub(rf"(?<!&)#{case_id}(?![\da-fA-F])", "#ID", foreign.get_data(as_text=True))
        )
        assert foreign_text == own_text
        assert "Org A Case" not in foreign_text
