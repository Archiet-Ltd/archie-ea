"""The relationship matrix draws motivation relationships in the direction ArchiMate 3.2 does."""

import itertools

import pytest

from app.config import archimate_relationship_matrix as cfg

MOTIVATION_TYPES = [
    "Stakeholder",
    "Driver",
    "Assessment",
    "Goal",
    "Outcome",
    "Principle",
    "Requirement",
    "Constraint",
]

# The element that makes the others real is the source.
REALIZATION_ALLOWED = [
    ("Outcome", "Goal"),
    ("Principle", "Goal"),
    ("Principle", "Outcome"),
    ("Requirement", "Goal"),
    ("Requirement", "Outcome"),
    ("Requirement", "Principle"),
    ("Constraint", "Goal"),
    ("Constraint", "Outcome"),
    ("Constraint", "Principle"),
]

REALIZATION_REFUSED = [
    ("Goal", "Outcome"),
    ("Goal", "Principle"),
    ("Goal", "Requirement"),
    ("Goal", "Constraint"),
    ("Principle", "Requirement"),
    ("Principle", "Constraint"),
]


@pytest.mark.parametrize("source,target", REALIZATION_ALLOWED)
def test_realization_allowed_towards_the_thing_realized(source, target):
    assert cfg.is_valid_relationship(source, target, "realization")


@pytest.mark.parametrize("source,target", REALIZATION_REFUSED)
def test_realization_refused_in_the_backwards_direction(source, target):
    assert not cfg.is_valid_relationship(source, target, "realization")


@pytest.mark.parametrize(
    "source,target", list(itertools.product(MOTIVATION_TYPES, MOTIVATION_TYPES))
)
def test_influence_allowed_between_every_pair_of_motivation_types(source, target):
    assert cfg.is_valid_relationship(source, target, "influence")


def test_pairs_outside_the_motivation_layer_keep_their_realization():
    assert cfg.is_valid_relationship("CourseOfAction", "Goal", "realization")
    assert cfg.is_valid_relationship("Capability", "Outcome", "realization")
