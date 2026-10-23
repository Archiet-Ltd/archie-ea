"""Pins that app/models/archimate_element_types.py (the element-type registry)
and app/models/archimate_core.py (the relationship-validity classifier) agree
on one canonical layer string per element type, that app.models.constants's
ArchiMateLayer.canonical resolves the one legacy spelling of the seventh
layer, and that scripts/check_ai_layer_coverage.py still parses all 58
element types.

No database access is needed: everything under test here is pure Python over
module-level data or a script parsed from its own source file.
"""

from __future__ import annotations

import collections
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_element_type_layer_is_recognised_by_the_relationship_validator():
    """Every one of the 58 ArchiMate element types must resolve to a layer
    _ELEMENT_TYPE_LAYER also knows about, and both authorities must spell
    that layer the same way.
    """
    from app.modules.ai_chat.services.workbench_kernel import _pascal_to_snake

    from app.models.archimate_core import _ELEMENT_TYPE_LAYER
    from app.models.archimate_element_types import ArchiMateElementTypes

    all_elements = ArchiMateElementTypes.get_all_elements()
    assert all_elements, "the element type registry must not be empty"

    missing = []
    mismatched = []
    for name, definition in all_elements.items():
        key = _pascal_to_snake(name)
        if key not in _ELEMENT_TYPE_LAYER:
            missing.append((name, key))
            continue
        if _ELEMENT_TYPE_LAYER[key] != definition.layer:
            mismatched.append((name, _ELEMENT_TYPE_LAYER[key], definition.layer))

    assert not missing, f"element types missing from _ELEMENT_TYPE_LAYER: {missing}"
    assert not mismatched, (
        "archimate_core._ELEMENT_TYPE_LAYER disagrees with "
        f"ArchiMateElementTypes on the canonical layer string: {mismatched}"
    )


def test_workpackage_resolves_to_one_canonical_implementation_migration_layer():
    """Direct regression pin for the reported defect: the seventh layer must
    read 'implementation_migration' from both authorities, not
    'implementation_migration' from one and 'implementation' from the other.
    """
    from app.models.archimate_core import _ELEMENT_TYPE_LAYER
    from app.models.archimate_element_types import ArchiMateElementTypes

    assert (
        ArchiMateElementTypes.get_element_type("WorkPackage").layer
        == "implementation_migration"
    )
    assert _ELEMENT_TYPE_LAYER["work_package"] == "implementation_migration"


def test_element_type_layer_value_set_is_exactly_archimate_layer_all():
    """After the rename, _ELEMENT_TYPE_LAYER's values are exactly the seven
    names ArchiMateLayer.ALL declares -- no more, no fewer, no stray spelling.
    """
    from app.models.archimate_core import _ELEMENT_TYPE_LAYER
    from app.models.constants import ArchiMateLayer

    assert set(_ELEMENT_TYPE_LAYER.values()) == set(ArchiMateLayer.ALL)


def test_every_valid_relationships_layer_is_an_archimate_layer():
    """Every layer named in a VALID_RELATIONSHIPS key is a member of
    ArchiMateLayer.ALL -- the matrix and the classifier share one vocabulary.
    """
    from app.models.archimate_core import VALID_RELATIONSHIPS
    from app.models.constants import ArchiMateLayer

    named_layers = set()
    for _rel_type, source_layer, target_layer in VALID_RELATIONSHIPS:
        named_layers.add(source_layer)
        named_layers.add(target_layer)

    assert named_layers <= set(ArchiMateLayer.ALL), (
        f"VALID_RELATIONSHIPS names a layer outside ArchiMateLayer.ALL: "
        f"{named_layers - set(ArchiMateLayer.ALL)}"
    )


def test_archimate_layer_canonical():
    """ArchiMateLayer.canonical folds the one known legacy spelling of the
    seventh layer onto the current one, passes every other member of
    ArchiMateLayer.ALL through unchanged, strips and lower-cases an unknown
    string, and passes a non-string through untouched.
    """
    from app.models.constants import ArchiMateLayer

    for legacy in (
        "implementation",
        "Implementation",
        " IMPLEMENTATION ",
        "implementation_migration",
    ):
        assert ArchiMateLayer.canonical(legacy) == "implementation_migration"

    for layer in ArchiMateLayer.ALL:
        assert ArchiMateLayer.canonical(layer) == layer

    assert ArchiMateLayer.canonical(" Some Unknown Layer ") == "some unknown layer"
    assert ArchiMateLayer.canonical(None) is None


def test_structural_element_relationship_returns_defined_true_via_registry_skip():
    """A relationship touching a Grouping, Junction or Location companion
    element (app/models/structural_elements.py) is not one of the 58 types
    _ELEMENT_TYPE_LAYER classifies, so validate_relationship resolves it
    through the same "not in registry" path any other unrecognised type
    takes: a defined True, not a rejection. Element-type-level validity for
    these three -- which relationship types they may actually take, and to
    what -- lives in the element-type-keyed matrix in
    app/config/archimate_relationship_matrix.py, not here.
    """
    from app.models.archimate_core import validate_relationship

    for source_type, target_type in (
        ("Grouping", "business_process"),
        ("application_component", "Junction"),
        ("node", "Location"),
    ):
        is_valid, message = validate_relationship("association", source_type, target_type)
        assert is_valid is True
        assert message == "Element type not in registry; validation skipped"


def test_genuinely_unrecognised_type_still_skips_like_before():
    """A made-up type still gets the honest "not in the registry" skip."""
    from app.models.archimate_core import validate_relationship

    is_valid, message = validate_relationship(
        "association", "totally_made_up_type", "business_process"
    )
    assert is_valid is True
    assert "not in registry" in message.lower()


def test_check_ai_layer_coverage_parses_all_58_element_types():
    """scripts/check_ai_layer_coverage.py's _element_types must keep parsing
    all 58 element types across the seven ArchiMateLayer.ALL names, with
    their per-layer counts, once it anchors on the real
    "_ELEMENT_TYPE_LAYER = {" dict rather than the comment now sitting above
    it.

    Imported by file path, the way this script is designed to run: no
    database, no app context.
    """
    from app.models.constants import ArchiMateLayer

    checker_path = os.path.join(REPO, "scripts", "check_ai_layer_coverage.py")
    spec = importlib.util.spec_from_file_location("_ai_layer_coverage", checker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    types = module._element_types(REPO)
    assert len(types) == 58, f"expected 58 element types, found {len(types)}: {sorted(types)}"

    layers = {layer for layer, _line_no in types.values()}
    assert layers == set(ArchiMateLayer.ALL)

    counts = collections.Counter(layer for layer, _line_no in types.values())
    assert dict(counts) == {
        "business": 13,
        "technology": 13,
        "motivation": 10,
        "application": 9,
        "implementation_migration": 5,
        "strategy": 4,
        "physical": 4,
    }
