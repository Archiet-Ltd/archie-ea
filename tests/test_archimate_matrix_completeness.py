"""The conformance matrix must know every ArchiMate 3.2 concept it validates.

The matrix held 58 of the 61 concepts. Grouping, Location and Junction were
absent, and because validation is an exact-pair lookup, an absent concept does
not fail loudly - `get_valid_relationships` returns an empty list, which reads
as "the specification permits nothing here" rather than "this concept is
unknown to me".

That distinction matters because the two are indistinguishable to every caller.
A Grouping aggregating its members is the most ordinary construct in ArchiMate
and the matrix reported it as a conformance error, which is how the Lucid
importer came to drop nested structure entirely.

These tests pin the concepts and a sample of their rules. They deliberately do
not restate the whole specification - that is what the matrix is - but they do
guard the shape of it, and they fail if a concept silently disappears again.
"""

import pytest

from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    CONNECTOR_ELEMENTS,
    EXTENSION_ELEMENTS,
    LAYERED_ELEMENTS,
    OTHER_ELEMENTS,
    get_valid_relationships,
    is_valid_relationship,
)

pytestmark = pytest.mark.journey


def test_every_archimate_32_concept_is_known():
    """61 concepts: 58 layered, Grouping and Location (§4.5), Junction (§5.4)."""
    missing = [c for c in ("Grouping", "Location", "Junction") if c not in ALL_ELEMENTS]
    assert not missing, (
        "%s absent from the matrix. Validation is an exact-pair lookup, so an "
        "unknown concept silently validates as 'nothing is permitted' rather "
        "than raising - every relationship touching it is reported as a "
        "conformance error." % missing)
    assert len(ALL_ELEMENTS) == 61, (
        "expected 61 ArchiMate 3.2 concepts, found %d: %s"
        % (len(ALL_ELEMENTS), sorted(set(ALL_ELEMENTS))))
    assert len(set(ALL_ELEMENTS)) == len(ALL_ELEMENTS), (
        "duplicate entries in ALL_ELEMENTS: %s"
        % sorted({e for e in ALL_ELEMENTS if ALL_ELEMENTS.count(e) > 1}))


def test_standard_concepts_plus_one_extension():
    """61 standard concepts plus the one Entelim extension (ADR-0014)."""
    assert len(ALL_ELEMENTS) == 61
    assert EXTENSION_ELEMENTS == ["Application"]
    assert "Application" not in ALL_ELEMENTS
    assert len(ALL_ELEMENTS) + len(EXTENSION_ELEMENTS) == 62


class TestGrouping:
    """§4.5.1 - aggregates or composes concepts that belong together."""

    @pytest.mark.parametrize("target", [
        "ApplicationComponent", "BusinessProcess", "Capability", "Node",
        "Goal", "WorkPackage", "Requirement",
    ])
    def test_a_grouping_can_aggregate_anything(self, target):
        assert is_valid_relationship("Grouping", target, "aggregation"), (
            "Grouping cannot aggregate %s, which is the element's entire "
            "purpose" % target)

    def test_a_grouping_covers_every_layered_element(self):
        """No layer may be left out, or nesting breaks only in that layer."""
        uncovered = [e for e in LAYERED_ELEMENTS
                     if not get_valid_relationships("Grouping", e)]
        assert not uncovered, (
            "%d element type(s) have no rule from Grouping: %s"
            % (len(uncovered), uncovered[:10]))

    def test_grouping_is_encoded_permissively_on_purpose(self):
        """Tighter than the spec would trade one false positive class for another.

        Restricting Grouping to composition/aggregation/association - what it is
        FOR - would reject models that were previously tolerated, which is the
        trade this codebase already refused when it made an absent matrix entry
        warn rather than reject. See test_types_absent_from_the_matrix... in
        tests/test_archimate_conformance_and_oef.py.
        """
        for rel in ("triggering", "flow", "serving", "realization"):
            assert is_valid_relationship("Grouping", "BusinessProcess", rel), (
                "Grouping --%s--> BusinessProcess is now a hard error where it "
                "used to be a warning; that is a new false positive" % rel)

    def test_specialization_across_types_is_still_refused(self):
        """"This grouping is a kind of business process" is not a real statement."""
        assert not is_valid_relationship("Grouping", "BusinessProcess", "specialization")
        assert not is_valid_relationship("Location", "BusinessProcess", "specialization")

    def test_groupings_nest_and_specialize_within_themselves(self):
        for rel in ("composition", "aggregation", "specialization", "association"):
            assert is_valid_relationship("Grouping", "Grouping", rel), rel


class TestLocation:
    """§4.5.2 - a place where structure and behaviour elements sit."""

    @pytest.mark.parametrize("target", ["BusinessActor", "ApplicationComponent", "Node"])
    def test_elements_are_assigned_to_a_location(self, target):
        assert is_valid_relationship("Location", target, "assignment"), (
            "a Location cannot be assigned %s, so physical placement cannot be "
            "modelled" % target)

    def test_locations_nest(self):
        assert is_valid_relationship("Location", "Location", "composition")
        assert is_valid_relationship("Location", "Location", "aggregation")


class TestJunction:
    """§5.4 - a connector joining relationships of the same type."""

    def test_a_junction_carries_dynamic_relationships(self):
        for rel in ("triggering", "flow"):
            assert is_valid_relationship("Junction", "BusinessProcess", rel), rel
            assert is_valid_relationship("BusinessProcess", "Junction", rel), rel

    def test_a_junction_cannot_express_specialization(self):
        """A junction joins relationships; it cannot state that one thing is another."""
        assert not is_valid_relationship("Junction", "BusinessProcess", "specialization")
        assert not is_valid_relationship("BusinessProcess", "Junction", "specialization")

    def test_junction_is_declared_a_connector_not_an_element(self):
        """It is in ALL_ELEMENTS only so models containing it can be validated."""
        assert CONNECTOR_ELEMENTS == ["Junction"]
        assert "Junction" not in LAYERED_ELEMENTS
        assert "Junction" not in OTHER_ELEMENTS


def test_the_additions_did_not_overwrite_existing_rules():
    """Generated rules use setdefault; an explicit rule must always win.

    If generation clobbered the hand-written matrix, the damage would be broad
    and quiet - rules would still exist, they would just be wrong.
    """
    # A sample of long-standing hand-written rules that the generated block
    # must not have touched.
    assert get_valid_relationships("Resource", "Capability") == ["assignment", "association"]
    assert "realization" in get_valid_relationships("ApplicationComponent", "ApplicationService")
    assert is_valid_relationship("BusinessProcess", "BusinessProcess", "triggering")
