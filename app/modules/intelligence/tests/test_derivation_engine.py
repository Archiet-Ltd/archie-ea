"""T-001 acceptance criteria 1-10: the derivation engine's ADR-002-v2 extension.

Pure-function tests against ``ArchiMateDerivationService.compute_derived`` —
no Flask app, no database. Every test builds a tiny element/relationship
graph and asserts on the returned rows.

Mapping to the T-001 brief's numbered Acceptance Criteria (build report
carries the same mapping against actual test ids):

    1  -> test_serving_plus_serving_derives_serving .. test_influence_plus_influence_derives_influence
    2  -> test_composition_transparent_in_source_position .. test_assignment_transparent_in_target_position
    3  -> test_association_fallback_for_uncombined_types
    4  -> test_cycle_terminates_without_revisiting_a_node
    5  -> test_max_depth_boundary_five_yes_six_no
    6  -> test_explicit_pair_suppression_ignores_relationship_type
    7  -> test_shortest_chain_wins_over_longer_chain
    8  -> verified by coverage measurement (pytest --cov), not a test id here
    9  -> test_signature_pin_matches_recorded_expectation
    10 -> test_rule_id_is_deterministic_and_distinguishes_rules_for_same_pair
"""

from __future__ import annotations

import pytest

from app.services.archimate_derivation_service import (
    ArchiMateDerivationService,
    _DERIVATION_TABLE,
    _TRANSPARENT,
)


def _el(id_: int) -> dict:
    return {"id": id_, "name": f"E{id_}", "type": "ApplicationComponent", "layer": "application"}


def _rel(id_: int, source_id: int, target_id: int, type_: str) -> dict:
    return {"id": id_, "source_id": source_id, "target_id": target_id, "type": type_}


def _only_row(derived: list, source_id: int, target_id: int) -> dict:
    matches = [d for d in derived if d["source_id"] == source_id and d["target_id"] == target_id]
    assert len(matches) == 1, (
        f"expected exactly one derived row for ({source_id}, {target_id}), got {len(matches)}: {matches}"
    )
    return matches[0]


def _two_hop_row(type_a: str, type_b: str) -> dict:
    """A -[type_a]-> B -[type_b]-> C, elements 1,2,3, relationships 10,20."""
    elements = [_el(1), _el(2), _el(3)]
    relationships = [_rel(10, 1, 2, type_a), _rel(20, 2, 3, type_b)]
    derived = ArchiMateDerivationService().compute_derived(elements, relationships)
    return _only_row(derived, 1, 3)


# --- Acceptance criterion 1: six tests, one per _DERIVATION_TABLE row -------


def test_serving_plus_serving_derives_serving():
    assert ("Serving", "Serving") in _DERIVATION_TABLE  # row still present, unaltered
    row = _two_hop_row("Serving", "Serving")
    assert row["type"] == "Serving"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_serving_plus_access_derives_access():
    assert ("Serving", "Access") in _DERIVATION_TABLE
    row = _two_hop_row("Serving", "Access")
    assert row["type"] == "Access"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_access_plus_serving_derives_access():
    assert ("Access", "Serving") in _DERIVATION_TABLE
    row = _two_hop_row("Access", "Serving")
    assert row["type"] == "Access"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_flow_plus_flow_derives_flow():
    assert ("Flow", "Flow") in _DERIVATION_TABLE
    row = _two_hop_row("Flow", "Flow")
    assert row["type"] == "Flow"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_triggering_plus_triggering_derives_triggering():
    assert ("Triggering", "Triggering") in _DERIVATION_TABLE
    row = _two_hop_row("Triggering", "Triggering")
    assert row["type"] == "Triggering"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_influence_plus_influence_derives_influence():
    assert ("Influence", "Influence") in _DERIVATION_TABLE
    row = _two_hop_row("Influence", "Influence")
    assert row["type"] == "Influence"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_derivation_table_has_exactly_six_rows_all_exercised_above():
    """Guards the coverage claim in acceptance criterion 8: if a row is ever
    added to _DERIVATION_TABLE without a matching test above, this fails
    loudly instead of the coverage gap going unnoticed."""
    exercised = {
        ("Serving", "Serving"),
        ("Serving", "Access"),
        ("Access", "Serving"),
        ("Flow", "Flow"),
        ("Triggering", "Triggering"),
        ("Influence", "Influence"),
    }
    assert set(_DERIVATION_TABLE.keys()) == exercised
    assert len(_DERIVATION_TABLE) == 6


# --- Acceptance criterion 2: eight tests, one per _TRANSPARENT member, ------
# --- source position and target position ------------------------------------


@pytest.mark.parametrize("transparent_type", sorted(_TRANSPARENT))
def test_transparent_member_in_source_position_propagates_other_type(transparent_type):
    # hop1 is transparent, hop2 ("Serving") is not -> derived type is "Serving".
    row = _two_hop_row(transparent_type, "Serving")
    assert row["type"] == "Serving"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


@pytest.mark.parametrize("transparent_type", sorted(_TRANSPARENT))
def test_transparent_member_in_target_position_propagates_other_type(transparent_type):
    # hop1 ("Serving") is not transparent, hop2 is -> derived type is "Serving".
    row = _two_hop_row("Serving", transparent_type)
    assert row["type"] == "Serving"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2
    assert row["relationship_chain"] == [10, 20]


def test_transparent_set_is_exactly_the_four_structural_types():
    """Guards acceptance criterion 2's "eight tests" count against a silent
    addition/removal of a _TRANSPARENT member."""
    assert _TRANSPARENT == frozenset({"Composition", "Aggregation", "Realization", "Assignment"})


# --- Acceptance criterion 3: the Association fallback -----------------------


def test_association_fallback_for_uncombined_types():
    # Neither type is transparent, and (Access, Access) has no _DERIVATION_TABLE
    # entry (only (Serving, Access) and (Access, Serving) do) -> Association.
    assert "Access" not in _TRANSPARENT
    assert ("Access", "Access") not in _DERIVATION_TABLE
    row = _two_hop_row("Access", "Access")
    assert row["type"] == "Association"
    assert row["rule_id"].startswith("fallback:")


# --- Acceptance criterion 4: a cycle terminates, no chain revisits a node, --
# --- no depth exceeds 5 ------------------------------------------------------


def test_cycle_terminates_without_revisiting_a_node():
    # A 3-node cycle: 1 -> 2 -> 3 -> 1, all "Serving".
    elements = [_el(1), _el(2), _el(3)]
    relationships = [
        _rel(10, 1, 2, "Serving"),
        _rel(20, 2, 3, "Serving"),
        _rel(30, 3, 1, "Serving"),
    ]
    # Termination: this call returning at all (rather than hanging) is the
    # proof; pytest's own default timeout-free run still bounds it in
    # practice because MAX_DEPTH stops any BFS branch at depth 5.
    derived = ArchiMateDerivationService().compute_derived(elements, relationships)
    assert derived, "expected at least one derived relationship from the cycle"
    for row in derived:
        assert len(set(row["chain"])) == len(row["chain"]), f"chain revisits a node: {row['chain']}"
        assert row["depth"] <= 5
        assert len(row["relationship_chain"]) == row["depth"]


# --- Acceptance criterion 5: the MAX_DEPTH boundary --------------------------


def test_max_depth_boundary_five_yes_six_no():
    # A straight chain of 7 elements / 6 relationships, all "Serving", so the
    # accumulated type never leaves the table and every hop stays uniform:
    # 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7.
    elements = [_el(i) for i in range(1, 8)]
    relationships = [_rel(i * 10, i, i + 1, "Serving") for i in range(1, 7)]
    derived = ArchiMateDerivationService().compute_derived(elements, relationships)

    depth_five = [d for d in derived if d["source_id"] == 1 and d["target_id"] == 6]
    assert len(depth_five) == 1, "expected the depth-5 chain (1 -> 6) to be emitted"
    assert depth_five[0]["depth"] == 5
    assert len(depth_five[0]["relationship_chain"]) == 5

    depth_six = [d for d in derived if d["source_id"] == 1 and d["target_id"] == 7]
    assert depth_six == [], "a depth-6 chain (1 -> 7) must never be emitted"

    assert all(d["depth"] <= 5 for d in derived)


# --- Acceptance criterion 6: explicit-pair suppression ignores type ---------


def test_explicit_pair_suppression_ignores_relationship_type():
    # Explicit (1, 2) of type "Association" — a type nothing here would ever
    # derive for this pair. A derivable 2-hop path (1 -> 3 -> 2, both
    # "Serving") would normally produce a derived ("Serving") row for (1, 2)
    # were the pair not already explicit. explicit_pairs is keyed ignoring
    # type, so it must suppress it anyway.
    elements = [_el(1), _el(2), _el(3)]
    relationships = [
        _rel(10, 1, 2, "Association"),
        _rel(20, 1, 3, "Serving"),
        _rel(30, 3, 2, "Serving"),
    ]
    derived = ArchiMateDerivationService().compute_derived(elements, relationships)
    suppressed = [d for d in derived if d["source_id"] == 1 and d["target_id"] == 2]
    assert suppressed == [], "an explicit relationship of any type must suppress every derived one for that pair"


# --- Acceptance criterion 7: shortest-chain-wins -----------------------------


def test_shortest_chain_wins_over_longer_chain():
    # Two-hop path 1 -> 2 -> 4 ("Serving" + "Serving" -> "Serving", depth 2)
    # and a three-hop path 1 -> 5 -> 6 -> 4 ("Flow" + "Flow" + "Flow" ->
    # "Flow", depth 3) both reach the pair (1, 4). seen_pairs is shared
    # across BFS start nodes and the queue is level-order, so the shorter
    # chain is discovered first and wins; the longer one must never appear.
    elements = [_el(1), _el(2), _el(4), _el(5), _el(6)]
    relationships = [
        _rel(10, 1, 2, "Serving"),
        _rel(20, 2, 4, "Serving"),
        _rel(30, 1, 5, "Flow"),
        _rel(40, 5, 6, "Flow"),
        _rel(50, 6, 4, "Flow"),
    ]
    derived = ArchiMateDerivationService().compute_derived(elements, relationships)
    row = _only_row(derived, 1, 4)
    assert row["depth"] == 2
    assert row["type"] == "Serving"
    assert row["chain"] == [1, 2, 4]
    assert row["relationship_chain"] == [10, 20]


# --- Acceptance criterion 9: ADR-002-v2 signature pin ------------------------


def test_signature_pin_matches_recorded_expectation():
    """Fixed input, recorded expected output — the node path, source/target,
    type and depth are unchanged from the pre-ADR-002-v2 engine; the two new
    fields are additive."""
    elements = [_el(1), _el(2), _el(3)]
    relationships = [_rel(100, 1, 2, "Serving"), _rel(200, 2, 3, "Serving")]
    derived = ArchiMateDerivationService().compute_derived(elements, relationships)
    row = _only_row(derived, 1, 3)

    # Recorded expectation of the PRE-change output shape (source_id,
    # target_id, type, chain, depth) — unchanged by this task.
    assert row["source_id"] == 1
    assert row["target_id"] == 3
    assert row["type"] == "Serving"
    assert row["chain"] == [1, 2, 3]
    assert row["depth"] == 2

    # ADR-002-v2 additions.
    assert row["relationship_chain"] == [100, 200]
    assert all(isinstance(x, int) for x in row["relationship_chain"]), "must be a flat list of integers"
    assert len(row["relationship_chain"]) == row["depth"]
    assert row["rule_id"] is not None
    assert isinstance(row["rule_id"], str) and row["rule_id"] != ""


# --- Acceptance criterion 10: rule_id stability ------------------------------


def test_rule_id_is_deterministic_and_distinguishes_rules_for_same_pair():
    elements = [_el(1), _el(2), _el(3)]

    # Scenario A: table rule (Serving + Serving -> Serving).
    relationships_a = [_rel(10, 1, 2, "Serving"), _rel(20, 2, 3, "Serving")]
    service = ArchiMateDerivationService()
    row_a1 = _only_row(service.compute_derived(elements, relationships_a), 1, 3)
    row_a2 = _only_row(service.compute_derived(elements, relationships_a), 1, 3)
    assert row_a1["rule_id"] == row_a2["rule_id"], "same input must yield the same rule_id every run"
    assert row_a1["rule_id"].startswith("table:")

    # Scenario B: transparent rule (Composition propagates Serving), same
    # resulting (source, target, type) as scenario A but a different rule.
    relationships_b = [_rel(10, 1, 2, "Composition"), _rel(20, 2, 3, "Serving")]
    row_b = _only_row(service.compute_derived(elements, relationships_b), 1, 3)
    assert row_b["type"] == row_a1["type"] == "Serving"
    assert row_b["rule_id"].startswith("transparent:")
    assert row_b["rule_id"] != row_a1["rule_id"], (
        "two different rules producing the same (source, target) pair must carry different rule_id values"
    )


def test_lowercase_and_legacy_relationship_spellings_match_canonical_derivation_rules():
    elements = [_el(1), _el(2), _el(3)]
    relationships = [
        _rel(10, 1, 2, "servingrelationship"),
        _rel(20, 2, 3, "accessrelationship"),
    ]

    derived = ArchiMateDerivationService().compute_derived(elements, relationships)
    row = _only_row(derived, 1, 3)

    assert row["type"] == "Access"
    assert row["relationship_chain"] == [10, 20]
    assert row["rule_id"] == "table:Serving:Access"


# --- Layer invariance: compute_derived never reads an element's "layer" -----


def test_compute_derived_output_is_invariant_under_element_layer():
    """compute_derived reads only element ids and relationship
    source/target/type/id -- never an element's "layer" -- so the same
    three-element "Serving" chain must produce byte-identical derived rows
    regardless of what layer string each element carries: each of the seven
    ArchiMateLayer.ALL values applied uniformly, a mixed-layer chain (one
    element per layer), and a value that is not a layer at all.
    """
    from app.models.constants import ArchiMateLayer

    def _chain(layers):
        elements = [
            {"id": 1, "name": "E1", "type": "ApplicationComponent", "layer": layers[0]},
            {"id": 2, "name": "E2", "type": "ApplicationComponent", "layer": layers[1]},
            {"id": 3, "name": "E3", "type": "ApplicationComponent", "layer": layers[2]},
        ]
        relationships = [_rel(10, 1, 2, "Serving"), _rel(20, 2, 3, "Serving")]
        return ArchiMateDerivationService().compute_derived(elements, relationships)

    baseline = _chain(["application", "application", "application"])
    assert len(baseline) == 1, (
        "the two-hop Serving+Serving chain must derive exactly one row "
        f"(1 -> 3); a vacuous baseline would make every comparison below "
        f"pass trivially: {baseline}"
    )

    for layer in ArchiMateLayer.ALL:
        assert _chain([layer, layer, layer]) == baseline

    mixed_layer_chain = ["motivation", "strategy", "physical"]
    assert _chain(mixed_layer_chain) == baseline

    not_a_layer_at_all = ["banana", "banana", "banana"]
    assert _chain(not_a_layer_at_all) == baseline
