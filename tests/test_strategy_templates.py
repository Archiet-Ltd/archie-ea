"""SWOT and PESTLE as canvas templates — their shape and seeding.

Static-and-data only: proves the two framework templates follow the canvas
contract every framework brief builds to — one CANVAS_TEMPLATES entry per
framework, zones whose membership is type_profile over Assessment elements
carrying a profile value naming the box, and the seeded profile vocabulary
that lets those values be written. No model call and no projection here; the
engine, the pages and the journeys are covered by their own tests.
"""
from __future__ import annotations

from app.config.archimate_viewpoints import (
    CANVAS_PROFILE_OPTIONS_BY_TYPE,
    CANVAS_TEMPLATES,
    validate_canvas_templates,
)


class TestStrategyTemplateShape:
    def test_swot_and_pestle_are_canvas_templates(self):
        assert "swot" in CANVAS_TEMPLATES
        assert "pestle" in CANVAS_TEMPLATES

    def test_validate_passes_with_the_two_framework_templates(self):
        # The real templates, SWOT and PESTLE included, satisfy the canvas
        # schema the validator enforces.
        validate_canvas_templates()

    def test_swot_is_the_recognisable_two_by_two(self):
        zones = CANVAS_TEMPLATES["swot"]["zones"]
        assert [z["box_key"] for z in zones] == [
            "strengths", "weaknesses", "opportunities", "threats"
        ]
        assert [z["label"] for z in zones] == [
            "Strengths", "Weaknesses", "Opportunities", "Threats"
        ]
        # Strengths/weaknesses on the top row, opportunities/threats below:
        # the page lays the 2x2 out from this order.
        assert [z["order"] for z in zones] == [1, 2, 3, 4]
        assert [z["phone_order"] for z in zones] == [1, 2, 3, 4]

    def test_pestle_is_the_six_labelled_columns_in_order(self):
        zones = CANVAS_TEMPLATES["pestle"]["zones"]
        assert [z["box_key"] for z in zones] == [
            "political", "economic", "social", "technological", "legal", "environmental"
        ]
        assert [z["order"] for z in zones] == [1, 2, 3, 4, 5, 6]

    def test_every_box_reads_assessment_elements_by_a_profile_naming_it(self):
        # The contract: membership is type_profile over Assessment elements
        # carrying a profile value naming the box.
        for key in ("swot", "pestle"):
            for zone in CANVAS_TEMPLATES[key]["zones"]:
                assert zone["element_types"] == ["Assessment"], (key, zone["box_key"])
                assert zone["membership"] == "type_profile", (key, zone["box_key"])
                assert zone["profile"], (key, zone["box_key"])

    def test_every_box_profile_is_in_the_seeded_assessment_vocabulary(self):
        allowed = set(CANVAS_PROFILE_OPTIONS_BY_TYPE["Assessment"])
        for key in ("swot", "pestle"):
            for zone in CANVAS_TEMPLATES[key]["zones"]:
                assert zone["profile"] in allowed, (key, zone["box_key"], zone["profile"])

    def test_box_profiles_are_distinct_across_the_two_frameworks(self):
        profiles = [
            z["profile"]
            for key in ("swot", "pestle")
            for z in CANVAS_TEMPLATES[key]["zones"]
        ]
        assert len(profiles) == len(set(profiles)), profiles

    def test_neither_framework_backs_a_record(self):
        # A SWOT or PESTLE is read straight from the organisation's own
        # Assessment elements, not from a per-record table.
        assert CANVAS_TEMPLATES["swot"]["record"] is None
        assert CANVAS_TEMPLATES["pestle"]["record"] is None
