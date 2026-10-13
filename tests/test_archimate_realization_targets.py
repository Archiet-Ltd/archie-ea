"""Realization may only target a more abstract entity (ArchiMate 3.2 §5.1.3).

Regression cover for QA finding C-01: the composer's relationship picker offered
Realization for ``ApplicationComponent -> BusinessActor`` while correctly refusing it
for ``BusinessActor -> ApplicationComponent``. The cross-layer rule granted realization
on layer RANK alone and never looked at the target's aspect, so the same element pair
validated differently depending on which way it was drawn — and an invalid relationship
reached the database.

These assertions are deliberately direction-explicit: a pair the specification forbids
must be forbidden BOTH ways round, so a future per-direction edit cannot re-open the gap
on one side only.
"""
import pytest

from app.config.archimate_relationship_matrix import (
    LAYERED_ELEMENTS,
    VALID_RELATIONSHIPS,
    is_valid_relationship,
)
from app.services.archimate_validity_service import ArchimateValidityService

# Every active structure element in the four core layers. None may be the TARGET of a
# realization — they are concrete performers, never "more abstract" entities.
CORE_ACTIVE_STRUCTURE = [
    "BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface",
    "ApplicationComponent", "ApplicationCollaboration", "ApplicationInterface",
    "Node", "Device", "SystemSoftware", "TechnologyCollaboration",
    "TechnologyInterface", "Path", "CommunicationNetwork",
    "Equipment", "Facility", "DistributionNetwork",
]


@pytest.fixture(scope="module")
def svc():
    return ArchimateValidityService()


def _offered(svc, source, target):
    """Relationship type names the picker would offer for this ordered pair."""
    return {r["type"] for r in svc.get_valid_relationships(source, target)}


# -- The reported defect, pinned in both directions --------------------------


def test_application_component_does_not_realize_business_actor(svc):
    """The exact pair from the QA report, and the direction that was wrong."""
    assert "realization" not in _offered(svc, "ApplicationComponent", "BusinessActor")
    assert not svc.is_valid("ApplicationComponent", "BusinessActor", "realization")


def test_business_actor_does_not_realize_application_component(svc):
    """The direction that was already correct — it must stay correct."""
    assert "realization" not in _offered(svc, "BusinessActor", "ApplicationComponent")
    assert not svc.is_valid("BusinessActor", "ApplicationComponent", "realization")


def test_the_reported_pair_is_still_usable(svc):
    """Realization is refused, not every relationship — the pair remains
    drawable, so a validator that left users with nothing would just be
    swapped for a different complaint. Association is always available
    (ArchiMate 3.2 §5.2.4); triggering and flow are also offered between
    these two active-structure elements (§5.3)."""
    offered = _offered(svc, "ApplicationComponent", "BusinessActor")
    assert "association" in offered
    assert "realization" not in offered


# -- The whole class of error, not just the reported instance ----------------


@pytest.mark.parametrize("target", CORE_ACTIVE_STRUCTURE)
def test_no_source_realizes_a_core_active_structure_element(svc, target):
    """Sweep every layered element type as a source: none may realize active
    structure. Grouping, Location and Junction belong to no layer and are
    exempt by design (see test_static_matrix_agrees_with_the_service)."""
    offenders = [
        source for source in sorted(LAYERED_ELEMENTS)
        if "realization" in _offered(svc, source, target)
    ]
    assert offenders == [], (
        f"realization into active structure {target} offered from: {offenders}"
    )


@pytest.mark.parametrize("target", CORE_ACTIVE_STRUCTURE)
def test_static_matrix_agrees_with_the_service(target):
    """``archimate_relationship_matrix`` is the second consumer of the same rule
    (conformance checking on import). It carried 12 hand-authored pairs realizing into
    active structure, every one the reverse of a legitimate rule."""
    offenders = sorted(
        source for (source, tgt), types in VALID_RELATIONSHIPS.items()
        if tgt == target and "realization" in types
        # Grouping/Location/Junction belong to no layer and are permissive by design.
        and source not in ("Grouping", "Location", "Junction")
    )
    assert offenders == [], (
        f"matrix allows realization into active structure {target} from: {offenders}"
    )


# -- Realization's legitimate targets are untouched ---------------------------


@pytest.mark.parametrize("source,target", [
    ("ApplicationComponent", "ApplicationService"),
    ("Capability", "BusinessProcess"),
    ("ApplicationProcess", "ApplicationService"),
    ("Capability", "ApplicationService"),
    ("Node", "TechnologyService"),
    ("WorkPackage", "Capability"),
    ("WorkPackage", "Deliverable"),
])
def test_valid_realizations_still_offered(svc, source, target):
    """Guards against over-correction: realization into services, motivation
    and implementation elements is unaffected."""
    assert "realization" in _offered(svc, source, target)


def test_specialization_and_association_unaffected(svc):
    """Only realization was narrowed."""
    assert svc.is_valid("BusinessActor", "BusinessActor", "specialization")
    assert svc.is_valid("ApplicationComponent", "BusinessActor", "association")
    assert is_valid_relationship("ApplicationComponent", "BusinessProcess", "serving")
