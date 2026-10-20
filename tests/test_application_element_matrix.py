"""The Application element in the registry and the relationship matrix (ADR-0014).

`Application` specialises `ApplicationComponent`. Its matrix rows are generated
from the parent's finished rows with three narrowings, so everything valid in
Entelim is valid ArchiMate 3.2 once exported (the subset invariant).
"""

import pytest

from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    EXTENSION_ELEMENTS,
    RelationshipValidator,
    VALID_RELATIONSHIPS,
    get_element_aspect,
    get_element_layer,
    is_valid_relationship,
)
from app.models.archimate_element_types import (
    SPECIALIZATIONS,
    ArchiMateElementTypes,
    base_type,
    is_application_kind,
)

pytestmark = pytest.mark.journey

APP = "Application"
COMP = "ApplicationComponent"


class TestRegistry:
    def test_application_is_registered_as_a_specialisation(self):
        definition = ArchiMateElementTypes.get_element_type(APP)
        assert definition is not None
        assert definition.parent_type == COMP
        assert definition.layer == "application"
        assert definition.aspect == "active"

    def test_specialisation_helpers(self):
        assert SPECIALIZATIONS == {APP: COMP}
        assert base_type(APP) == COMP
        assert base_type(COMP) == COMP
        assert base_type("BusinessProcess") == "BusinessProcess"
        assert base_type(None) is None
        assert is_application_kind(APP)
        assert is_application_kind(COMP)
        assert not is_application_kind("Node")
        assert not is_application_kind(None)

    def test_standard_concepts_unchanged_and_extension_beside_them(self):
        assert len(ALL_ELEMENTS) == 61
        assert APP not in ALL_ELEMENTS
        assert EXTENSION_ELEMENTS == [APP]
        assert len(ALL_ELEMENTS) + len(EXTENSION_ELEMENTS) == 62

    def test_parent_plain_name_not_changed_in_this_task(self):
        from app.models.archimate_element_types import plain_name_for

        assert plain_name_for(COMP) == "Application"


class TestNarrowings:
    def test_application_composes_a_component(self):
        assert is_valid_relationship(APP, COMP, "composition")
        assert is_valid_relationship(APP, COMP, "aggregation")
        for rel in ("serving", "flow", "triggering", "association"):
            assert is_valid_relationship(APP, COMP, rel), rel
        assert not is_valid_relationship(APP, COMP, "specialization")

    def test_application_does_not_compose_an_application(self):
        assert not is_valid_relationship(APP, APP, "composition")
        for rel in ("aggregation", "serving", "flow", "triggering",
                    "specialization", "association"):
            assert is_valid_relationship(APP, APP, rel), rel

    def test_component_never_contains_an_application(self):
        for rel in ("composition", "aggregation", "specialization"):
            assert not is_valid_relationship(COMP, APP, rel), rel
        for rel in ("serving", "flow", "triggering", "association"):
            assert is_valid_relationship(COMP, APP, rel), rel

    def test_component_pairs_are_unchanged(self):
        assert is_valid_relationship(COMP, COMP, "composition")
        assert is_valid_relationship(COMP, COMP, "specialization")


class TestDerivedRows:
    def test_every_parent_row_is_present_for_application(self):
        narrowed = {(APP, COMP), (APP, APP), (COMP, APP)}
        for (src, tgt), rels in list(VALID_RELATIONSHIPS.items()):
            if APP in (src, tgt):
                continue
            if COMP not in (src, tgt):
                continue
            new_src = APP if src == COMP else src
            new_tgt = APP if tgt == COMP else tgt
            assert (new_src, new_tgt) in VALID_RELATIONSHIPS, (src, tgt)
            if (new_src, new_tgt) in narrowed:
                continue
            assert VALID_RELATIONSHIPS[(new_src, new_tgt)] == rels, (src, tgt)

    def test_subset_invariant_over_every_pair(self):
        checked = 0
        for (src, tgt), rels in VALID_RELATIONSHIPS.items():
            if APP not in (src, tgt):
                continue
            parent_src = COMP if src == APP else src
            parent_tgt = COMP if tgt == APP else tgt
            parent = VALID_RELATIONSHIPS.get((parent_src, parent_tgt), [])
            assert set(rels) <= set(parent), (src, tgt, rels, parent)
            checked += 1
        parent_pairs = sum(1 for k in VALID_RELATIONSHIPS if COMP in k and APP not in k)
        # (COMP, COMP) yields three Application pairs, every other parent pair yields one.
        assert checked == parent_pairs + 2

    def test_no_row_without_application_is_altered(self):
        # Pairs that do not involve Application are exactly the parent module's
        # own rules: nothing in them may mention the extension.
        for (src, tgt), rels in VALID_RELATIONSHIPS.items():
            if APP in (src, tgt):
                continue
            assert APP not in rels


class TestValidators:
    def test_matrix_validator_accepts_application(self):
        v = RelationshipValidator()
        assert v.validate(APP, COMP, "composition")
        assert not v.validate(APP, APP, "composition")
        assert not any("Unknown" in e for e in v.get_errors())

    def test_matrix_validator_still_rejects_unknown_types(self):
        v = RelationshipValidator()
        assert not v.validate("Applicaton", COMP, "composition")
        assert any("Unknown source" in e for e in v.get_errors())

    def test_architecture_validator_accepts_application(self):
        from app.modules.architecture.services.relationship_validator import (
            RelationshipValidator as ArchiMateRelationshipValidator,
        )

        v = ArchiMateRelationshipValidator()
        ok = v.validate_relationship(APP, COMP, "composition")
        assert ok.is_valid, ok.errors
        bad = v.validate_relationship(APP, APP, "composition")
        assert not bad.is_valid
        assert not any("Unknown" in e for e in bad.errors)


class TestLayerAndAspect:
    def test_layer_and_aspect_resolve_through_base_type(self):
        assert get_element_layer(APP) == get_element_layer(COMP) == "Application"
        assert get_element_aspect(APP) == get_element_aspect(COMP) == "Active Structure"
