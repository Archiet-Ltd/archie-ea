"""One relationship matrix: the architecture services module answers from the config matrix."""

import pytest

from app.config import archimate_relationship_matrix as cfg
from app.modules.architecture.services import archimate_relationship_matrix as svc

ALL_TYPES = list(cfg.ALL_ELEMENTS)
REL_TYPES = [r for r in cfg.RELATIONSHIP_TYPES]

ADDED_PAIRS = [
    ("ApplicationComponent", "Capability", "Realization"),
    ("ApplicationCollaboration", "Capability", "Realization"),
    ("ApplicationFunction", "Capability", "Realization"),
    ("ApplicationProcess", "Capability", "Realization"),
    ("ApplicationService", "Capability", "Realization"),
    ("CourseOfAction", "Outcome", "Realization"),
    ("Resource", "BusinessFunction", "Assignment"),
]


def test_services_module_agrees_with_config_matrix_for_every_pair_and_type():
    assert len(ALL_TYPES) == 61
    disagreements = []
    for src in ALL_TYPES:
        for tgt in ALL_TYPES:
            for rel in REL_TYPES:
                want = cfg.is_valid_relationship(src, tgt, rel)
                got = svc.is_valid_relationship(src, tgt, rel.capitalize())
                got_validate = svc.validate_relationship(src, tgt, rel.capitalize())["valid"]
                if want != got or want != got_validate:
                    disagreements.append((src, tgt, rel, want, got, got_validate))
    assert disagreements == [], disagreements[:20]


def test_get_valid_relationships_matches_config():
    for src in ALL_TYPES:
        for tgt in ALL_TYPES:
            assert {r.lower() for r in svc.get_valid_relationships(src, tgt)} == {
                r.lower() for r in cfg.get_valid_relationships(src, tgt)
            }


@pytest.mark.parametrize("src,tgt,rel", ADDED_PAIRS)
def test_added_pairs_accepted_through_services_module(src, tgt, rel):
    assert svc.is_valid_relationship(src, tgt, rel)
    assert svc.validate_relationship(src, tgt, rel)["valid"] is True


def test_services_module_holds_no_validity_table_of_its_own():
    for name in ("EXPLICITLY_INVALID", "_ALL_RULES", "CROSS_LAYER_SERVING", "CROSS_LAYER_REALIZATION"):
        assert not hasattr(svc, name), name
