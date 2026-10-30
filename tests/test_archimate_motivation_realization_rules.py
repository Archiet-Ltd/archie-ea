"""ArchiMate 3.2 motivation rules: who may realize a requirement or a goal.

Controls trace to the obligations they satisfy by realizing a requirement, and a
requirement realizes a goal. The matrix had both reversed or missing, which
rejected valid compliance models on import.
"""
import pytest

from app.config.archimate_relationship_matrix import (
    BUSINESS_ELEMENTS,
    APPLICATION_ELEMENTS,
    PHYSICAL_ELEMENTS,
    TECHNOLOGY_ELEMENTS,
    get_valid_relationships,
    is_valid_relationship,
)


def test_a_goal_does_not_realize_a_requirement():
    assert not is_valid_relationship("Goal", "Requirement", "realization")
    assert is_valid_relationship("Goal", "Requirement", "influence")
    assert is_valid_relationship("Goal", "Requirement", "association")


def test_a_requirement_realizes_a_goal():
    types = get_valid_relationships("Requirement", "Goal")
    assert "realization" in types and "influence" in types and "association" in types


@pytest.mark.parametrize(
    "source",
    ["BusinessProcess", "ApplicationService", "ApplicationComponent", "BusinessService", "Node", "TechnologyService", "DataObject", "Equipment"],
)
def test_core_elements_realize_a_requirement(source):
    assert is_valid_relationship(source, "Requirement", "realization")
    assert is_valid_relationship(source, "Requirement", "association")


def test_every_core_element_may_realize_a_requirement():
    missing = [
        e for e in BUSINESS_ELEMENTS + APPLICATION_ELEMENTS + TECHNOLOGY_ELEMENTS + PHYSICAL_ELEMENTS
        if not is_valid_relationship(e, "Requirement", "realization")
    ]
    assert missing == []


def test_requirement_still_cannot_realize_an_active_core_element():
    # the long-standing rule (realization points at the more abstract thing) is untouched
    assert not is_valid_relationship("Requirement", "ApplicationComponent", "realization")


# Serving relationships common to context views and cloud / SaaS pattern models.

def test_an_application_component_serves_a_business_actor():
    assert is_valid_relationship("ApplicationComponent", "BusinessActor", "serving")


def test_a_node_serves_an_application_component():
    assert is_valid_relationship("Node", "ApplicationComponent", "serving")


def test_a_technology_service_serves_a_business_actor():
    assert is_valid_relationship("TechnologyService", "BusinessActor", "serving")


@pytest.mark.parametrize("source", ["Node", "TechnologyService"])
def test_technology_elements_realize_a_requirement(source):
    # a recovery-time requirement realized by traffic routing, for example
    assert is_valid_relationship(source, "Requirement", "realization")


def test_earlier_rules_on_those_pairs_are_kept():
    assert is_valid_relationship("Node", "ApplicationComponent", "assignment")
    assert is_valid_relationship("ApplicationComponent", "BusinessActor", "flow")
    assert is_valid_relationship("ApplicationService", "BusinessActor", "serving")
