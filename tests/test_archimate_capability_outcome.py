"""Realization pairs ArchiMate 3.2 permits: Capability to Outcome, and the
implementation and migration elements to core elements."""
import pytest

from app.config.archimate_relationship_matrix import (
    VALID_RELATIONSHIPS,
    is_valid_relationship,
)


def test_capability_may_realize_outcome():
    assert "realization" in VALID_RELATIONSHIPS[("Capability", "Outcome")]
    assert is_valid_relationship("Capability", "Outcome", "realization")


def test_outcome_may_not_realize_capability():
    assert not is_valid_relationship("Outcome", "Capability", "realization")


@pytest.mark.parametrize("source", ["WorkPackage", "Deliverable"])
@pytest.mark.parametrize(
    "target",
    ["ApplicationComponent"],
)
def test_work_package_and_deliverable_may_realize_application_component(source, target):
    assert is_valid_relationship(source, target, "realization")


@pytest.mark.parametrize("source", ["WorkPackage", "Deliverable"])
@pytest.mark.parametrize("target", ["BusinessActor", "Node", "Device"])
def test_other_active_structure_stays_refused(source, target):
    assert not is_valid_relationship(source, target, "realization")


def test_artifact_may_realize_application_component():
    assert is_valid_relationship("Artifact", "ApplicationComponent", "realization")


@pytest.mark.parametrize(
    "source", ["Capability", "Requirement", "Constraint", "Equipment"]
)
def test_other_elements_still_may_not_realize_an_application_component(source):
    assert not is_valid_relationship(source, "ApplicationComponent", "realization")
