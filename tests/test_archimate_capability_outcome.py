"""Capability realizes Outcome is a valid relationship (ArchiMate 3.2 section 7)."""
from app.config.archimate_relationship_matrix import (
    VALID_RELATIONSHIPS,
    is_valid_relationship,
)


def test_capability_may_realize_outcome():
    assert "realization" in VALID_RELATIONSHIPS[("Capability", "Outcome")]
    assert is_valid_relationship("Capability", "Outcome", "realization")


def test_outcome_may_not_realize_capability():
    assert not is_valid_relationship("Outcome", "Capability", "realization")


def test_work_package_still_cannot_realize_an_application_component_directly():
    # The specification reaches a component only through a deliverable.
    assert not is_valid_relationship("WorkPackage", "ApplicationComponent", "realization")
