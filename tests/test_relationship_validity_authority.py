"""The ArchiMate 3.2 relationship matrix is the one relationship validity
authority in this product. Every other place that used to answer "may a
relationship of type T run from element type S to element type U?" -- the
create route, the picker, ``ArchimateValidityService``, the layer-keyed
projection in ``app.models.archimate_core``, and the AI create tool -- is
now an adapter over ``app.config.archimate_relationship_matrix``.

This module pins that: nothing the matrix already permitted stops being
permitted (zero True->False drift against a snapshot taken before this
change); the new landings this change adds are exactly the ones intended,
nothing else; and every one of those adapters agrees with the matrix on a
shared sample.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    RelationshipValidator,
    VALID_RELATIONSHIPS as MATRIX_VALID_RELATIONSHIPS,
    get_valid_relationships,
    is_valid_relationship,
    normalize_element_type,
)
from app.models.archimate_core import (
    VALID_RELATIONSHIPS as PROJECTED_LAYER_RELATIONSHIPS,
    validate_relationship,
)
from app.services.archimate_validity_service import ArchimateValidityService

_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "relationship_matrix_main_snapshot.json"
)


def _matrix_true_triples():
    """Every (relationship type, source type, target type) the matrix, as it
    stands right now, answers True for."""
    triples = set()
    for (source_type, target_type), rel_types in MATRIX_VALID_RELATIONSHIPS.items():
        for rel_type in rel_types:
            triples.add((rel_type, source_type, target_type))
    return triples


@pytest.fixture(scope="module")
def main_snapshot():
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return {tuple(row) for row in json.load(f)}


@pytest.fixture(scope="module")
def head_triples():
    return _matrix_true_triples()


# -- (a) Zero True -> False drift against the pre-change snapshot ------------


def test_no_snapshot_triple_regresses(main_snapshot, head_triples):
    """Every (type, source, target) the matrix permitted before this change
    still validates True. A relationship-key removal or a narrower rule
    shows up here as a listed regression, not a bare count."""
    regressed = sorted(main_snapshot - head_triples)
    assert regressed == [], f"{len(regressed)} triple(s) flipped True->False: {regressed[:20]}"


# -- (b) The ten canvas landings ----------------------------------------------


def test_association_is_permitted_between_every_ordered_pair_of_elements():
    """ArchiMate 3.2 §5.2.4: association may connect any two concepts. Swept
    over every ordered pair of ALL_ELEMENTS, not sampled, because this is
    exactly the derived pass the matrix now runs at import time."""
    missing = [
        (s, u) for s in ALL_ELEMENTS for u in ALL_ELEMENTS
        if not is_valid_relationship(s, u, "association")
    ]
    assert missing == [], f"{len(missing)} pair(s) without association: {missing[:20]}"


@pytest.mark.parametrize("source,target,rel", [
    ("Capability", "Capability", "aggregation"),
    ("Resource", "Resource", "aggregation"),
    ("Resource", "Capability", "assignment"),
    ("Outcome", "Goal", "realization"),
    ("WorkPackage", "Outcome", "realization"),
    ("WorkPackage", "Outcome", "association"),
    # Two realization rows the rule-based service used to grant and the
    # config matrix regressed when it became the one authority, landed on
    # their own §7.5 citation (strategy elements realize motivation
    # elements): a capability realizes a requirement, a course of action
    # realizes an outcome.
    ("Capability", "Requirement", "realization"),
    ("CourseOfAction", "Outcome", "realization"),
])
def test_the_explicit_landings(source, target, rel):
    assert is_valid_relationship(source, target, rel), f"{source} -{rel}-> {target}"


# -- (c) The new True set is exactly the association pass, the two explicit -
# -- realization rows already pinned above, and every regressed row the ----
# -- rule-based service used to grant that this change lands back, nothing -
# -- else ------------------------------------------------------------------
#
# These counts are computed directly against the matrix, type by type, not
# copied from a prose estimate: the "every core element ->
# Goal/Outcome/Principle/Requirement/Constraint" pass is 39 elements x 5
# targets = 195 new rows, and the realization total below (348) and access
# total (21) are this computation's own numbers, not a hand count.
_EXPECTED_NEW_COUNTS_BY_TYPE = {
    "association": 2837,
    "influence": 1016,
    "serving": 613,
    "realization": 348,
    "triggering": 115,
    "flow": 157,
    "composition": 6,
    "aggregation": 6,
    "assignment": 2,
    "access": 21,
}


def test_new_true_triples_match_the_regressed_landings_exactly(main_snapshot, head_triples):
    from collections import Counter

    new_triples = head_triples - main_snapshot
    by_type = Counter(t[0] for t in new_triples)
    assert dict(by_type) == _EXPECTED_NEW_COUNTS_BY_TYPE
    assert "specialization" not in by_type


def test_association_new_triples_are_exactly_the_derived_pass(main_snapshot, head_triples):
    new_triples = head_triples - main_snapshot
    association_new = {t for t in new_triples if t[0] == "association"}
    expected_association_new = {
        ("association", s, u)
        for s in ALL_ELEMENTS for u in ALL_ELEMENTS
        if ("association", s, u) not in main_snapshot
    }
    assert association_new == expected_association_new


# -- The three legacy-only layer keys are narrowed away, not carried forward -


def test_the_three_narrowed_legacy_keys_are_absent_from_the_projection():
    """(realization, motivation, strategy), (serving, strategy, motivation)
    and (access, application, business) used to be kept by hand in
    ``app.models.archimate_core`` because no matrix row produced them.
    Settled on the standard instead: gone, not replaced."""
    narrowed = {
        ("realization", "motivation", "strategy"),
        ("serving", "strategy", "motivation"),
        ("access", "application", "business"),
    }
    assert narrowed.isdisjoint(PROJECTED_LAYER_RELATIONSHIPS.keys())


# -- (e) The derived layer projection is a superset of the literal it -------
# -- replaced ------------------------------------------------------------------

# The exact 72 keys app/models/archimate_core.py's hand-authored
# VALID_RELATIONSHIPS held before this change, captured from main. Not a
# live import (that table no longer exists) -- the point of this test is
# that the derived projection still answers True for every one of them.
_DELETED_LAYER_LITERAL = {
    ("composition", "business", "business"), ("composition", "application", "application"),
    ("composition", "technology", "technology"), ("composition", "motivation", "motivation"),
    ("composition", "strategy", "strategy"), ("composition", "physical", "physical"),
    ("composition", "implementation", "implementation"),
    ("aggregation", "business", "business"), ("aggregation", "application", "application"),
    ("aggregation", "technology", "technology"), ("aggregation", "motivation", "motivation"),
    ("aggregation", "strategy", "strategy"),
    ("assignment", "business", "business"), ("assignment", "application", "application"),
    ("assignment", "technology", "technology"), ("assignment", "technology", "application"),
    ("assignment", "strategy", "strategy"),
    ("realization", "business", "business"), ("realization", "application", "business"),
    ("realization", "technology", "application"), ("realization", "technology", "technology"),
    ("realization", "application", "application"), ("realization", "implementation", "motivation"),
    ("realization", "motivation", "motivation"), ("realization", "motivation", "strategy"),
    ("realization", "strategy", "motivation"), ("realization", "strategy", "business"),
    ("realization", "implementation", "implementation"),
    ("serving", "application", "business"), ("serving", "application", "application"),
    ("serving", "technology", "application"), ("serving", "technology", "technology"),
    ("serving", "business", "business"), ("serving", "strategy", "strategy"),
    ("serving", "strategy", "motivation"),
    ("access", "business", "business"), ("access", "application", "application"),
    ("access", "application", "business"), ("access", "technology", "technology"),
    ("influence", "motivation", "motivation"),
    ("triggering", "business", "business"), ("triggering", "application", "application"),
    ("triggering", "technology", "technology"), ("triggering", "strategy", "strategy"),
    ("flow", "business", "business"), ("flow", "application", "application"),
    ("flow", "technology", "technology"),
    ("association", "business", "business"), ("association", "application", "application"),
    ("association", "technology", "technology"), ("association", "motivation", "motivation"),
    ("association", "motivation", "strategy"), ("association", "strategy", "strategy"),
    ("association", "strategy", "motivation"), ("association", "implementation", "implementation"),
    ("association", "business", "application"), ("association", "application", "business"),
    ("association", "application", "technology"), ("association", "technology", "application"),
    ("association", "business", "motivation"), ("association", "motivation", "business"),
    ("association", "strategy", "implementation"), ("association", "implementation", "strategy"),
    ("association", "motivation", "implementation"), ("association", "implementation", "motivation"),
    ("association", "business", "strategy"), ("association", "strategy", "business"),
    ("specialization", "business", "business"), ("specialization", "application", "application"),
    ("specialization", "technology", "technology"), ("specialization", "motivation", "motivation"),
    ("specialization", "strategy", "strategy"),
}


def test_deleted_layer_literal_size_is_72():
    """Guards the fixture above itself: if this stops being 72, the literal
    was copied wrong."""
    assert len(_DELETED_LAYER_LITERAL) == 72


# These three of the 72 were never produced by a matrix row -- they were kept
# by hand in the now-deleted ``_LEGACY_LAYER_ONLY_KEYS`` -- and this change
# narrows them away rather than carrying them forward (see the narrowed-keys
# test above). The projection is a superset of the deleted literal except
# for these three.
_NARROWED_AWAY = {
    ("realization", "motivation", "strategy"),
    ("serving", "strategy", "motivation"),
    ("access", "application", "business"),
}


def test_derived_projection_is_a_superset_of_the_deleted_literal_except_the_narrowed_keys():
    expected = _DELETED_LAYER_LITERAL - _NARROWED_AWAY
    missing = sorted(expected - set(PROJECTED_LAYER_RELATIONSHIPS.keys()))
    assert missing == [], (
        f"{len(missing)} key(s) of the old table are no longer produced by the "
        f"derived projection: {missing}"
    )


# -- validate_relationship fails closed for Grouping, Location and Junction -
# -- not just for a genuinely unknown type -----------------------------------


def test_validate_relationship_gives_the_matrix_real_verdict_for_other_elements():
    """Before this change, ``get_element_layer`` returning ``None`` for
    Grouping/Location/Junction (they belong to no layer) took the same skip
    path as a genuinely unrecognised type, so every relationship touching
    one of them validated True regardless of what the matrix says.
    Specialization between a Grouping and a layered element is the one
    relationship the matrix's own generated pass excludes -- it must now
    refuse, not skip-and-allow."""
    is_valid, message = validate_relationship("specialization", "grouping", "business_process")
    assert is_valid is False, message
    assert "not in registry" not in message


def test_validate_relationship_still_skips_a_genuinely_unknown_type():
    is_valid, message = validate_relationship("association", "not_a_real_type", "business_process")
    assert is_valid is True
    assert "not in registry" in message


def test_validate_relationship_grants_a_real_other_element_verdict():
    """Grouping aggregating a business process is real per the matrix
    (ArchiMate 3.2 §4.5.1) -- the fail-closed fix must not turn this into a
    refusal too."""
    is_valid, message = validate_relationship("aggregation", "grouping", "business_process")
    assert is_valid is True, message


# -- (d) Every adapter agrees with the matrix on one shared sample -----------
#
# The eleven canvas pairs, the two motivation realizations the picker used to
# drop, and Grouping/Location/Junction/ApplicationCollaboration -- the
# element kinds the rule-based service could not see at all.

SAMPLE = [
    ("option -> plan item", "association", "CourseOfAction", "WorkPackage"),
    ("plan item -> option", "association", "WorkPackage", "CourseOfAction"),
    ("capability hierarchy", "aggregation", "Capability", "Capability"),
    ("resource hierarchy", "aggregation", "Resource", "Resource"),
    ("outcome -> work package", "association", "Outcome", "WorkPackage"),
    ("work package -> outcome", "association", "WorkPackage", "Outcome"),
    ("resource assigned to capability", "assignment", "Resource", "Capability"),
    ("key partner -> resource", "association", "BusinessActor", "Resource"),
    ("key partner -> capability", "association", "BusinessActor", "Capability"),
    ("resource -> key partner", "association", "Resource", "BusinessActor"),
    ("capability -> key partner", "association", "Capability", "BusinessActor"),
    ("outcome realises goal", "realization", "Outcome", "Goal"),
    ("work package realises outcome", "realization", "WorkPackage", "Outcome"),
    ("grouping aggregates business process", "aggregation", "Grouping", "BusinessProcess"),
    ("location aggregates node", "aggregation", "Location", "Node"),
    ("junction joins business process triggering", "triggering", "Junction", "BusinessProcess"),
    ("application collaboration flow", "flow", "ApplicationCollaboration", "ApplicationCollaboration"),
]


def _to_snake(pascal_name):
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", pascal_name).lower()


@pytest.mark.parametrize("label,rel,source,target", SAMPLE, ids=[r[0] for r in SAMPLE])
def test_matrix_ground_truth(label, rel, source, target):
    assert is_valid_relationship(source, target, rel), label


@pytest.mark.parametrize("label,rel,source,target", SAMPLE, ids=[r[0] for r in SAMPLE])
def test_service_adapter_agrees(label, rel, source, target):
    assert ArchimateValidityService().is_valid(source, target, rel), label


@pytest.mark.parametrize("label,rel,source,target", SAMPLE, ids=[r[0] for r in SAMPLE])
def test_relationship_validator_agrees(label, rel, source, target):
    assert RelationshipValidator().validate(source, target, rel), label


@pytest.mark.parametrize("label,rel,source,target", SAMPLE, ids=[r[0] for r in SAMPLE])
def test_validate_relationship_adapter_agrees(label, rel, source, target):
    is_valid, message = validate_relationship(rel, _to_snake(source), _to_snake(target))
    assert is_valid, f"{label}: {message}"


# -- The same sample, through the actual route, picker and AI tool -----------
# -- a representative subset spanning every kind above, exercised end to end.

_DB_BACKED_SAMPLE = [
    ("option -> plan item", "association", "CourseOfAction", "WorkPackage"),
    ("outcome realises goal", "realization", "Outcome", "Goal"),
    ("work package realises outcome", "realization", "WorkPackage", "Outcome"),
    ("grouping aggregates business process", "aggregation", "Grouping", "BusinessProcess"),
    ("location aggregates node", "aggregation", "Location", "Node"),
    ("junction joins business process triggering", "triggering", "Junction", "BusinessProcess"),
    ("application collaboration flow", "flow", "ApplicationCollaboration", "ApplicationCollaboration"),
]


def _make_user(db_session, org_id, label):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


# The DB "layer" column value the product uses for the element types the
# DB-backed sample creates. Grouping/Location/Junction belong to no
# ArchiMate layer (app/models/structural_elements.py uses "Reference" for
# them); everything else uses its real layer, lowercased.
_ELEMENT_LAYER_FOR_TEST = {
    "CourseOfAction": "strategy", "WorkPackage": "implementation",
    "Capability": "strategy", "Resource": "strategy",
    "Outcome": "motivation", "BusinessActor": "business", "Goal": "motivation",
    "Grouping": "Reference", "BusinessProcess": "business",
    "Location": "Reference", "Node": "technology", "Junction": "Reference",
    "ApplicationCollaboration": "application",
}


def _make_element(db_session, org_id, element_type, suffix):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=f"{element_type} {suffix}", type=element_type,
        layer=_ELEMENT_LAYER_FOR_TEST[element_type], organization_id=org_id,
    )
    db_session.add(el)
    db_session.flush()
    return el


@pytest.mark.parametrize(
    "label,rel,source,target", _DB_BACKED_SAMPLE, ids=[r[0] for r in _DB_BACKED_SAMPLE]
)
def test_route_and_picker_agree_with_the_matrix(
    label, rel, source, target, app, db_session, make_org, client, login_as
):
    org = make_org("authority-sample")
    user = _make_user(db_session, org.id, "AuthoritySample")
    suffix = uuid.uuid4().hex[:6]
    src_el = _make_element(db_session, org.id, source, suffix)
    tgt_el = _make_element(db_session, org.id, target, suffix)
    src_id, tgt_id, user_id = src_el.id, tgt_el.id, user.id

    login_as(client, user_id)

    picker_resp = client.get(
        f"/archimate/api/valid-relationship-types?source_id={src_id}&target_id={tgt_id}"
    )
    assert picker_resp.status_code == 200, picker_resp.get_data(as_text=True)
    assert rel in picker_resp.get_json()["valid_types"], (
        f"{label}: picker did not offer {rel} for {source} -> {target}"
    )

    create_resp = client.post("/archimate/api/relationships", json={
        "source_element_id": src_id,
        "target_element_id": tgt_id,
        "relationship_type": rel,
    })
    assert create_resp.status_code == 201, (
        f"{label}: {create_resp.status_code} {create_resp.get_data(as_text=True)}"
    )


@pytest.mark.parametrize(
    "label,rel,source,target", _DB_BACKED_SAMPLE, ids=[r[0] for r in _DB_BACKED_SAMPLE]
)
def test_ai_tool_agrees_with_the_matrix(
    label, rel, source, target, app, db_session, make_org, tenant_ctx
):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    org = make_org("authority-ai-sample")
    user = _make_user(db_session, org.id, "AuthorityAISample")
    suffix = uuid.uuid4().hex[:6]
    src_el = _make_element(db_session, org.id, source, suffix)
    tgt_el = _make_element(db_session, org.id, target, suffix)
    src_name, tgt_name, user_id = src_el.name, tgt_el.name, user.id

    with tenant_ctx(org.id):
        result = ToolExecutor(user_id)._tool_create_archimate_relationship({
            "source_element_name": src_name,
            "target_element_name": tgt_name,
            "relationship_type": rel,
        })
    assert result["success"] is True, f"{label}: {result}"
