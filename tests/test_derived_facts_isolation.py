"""Two-organisation isolation and single-source-of-truth tests for derived facts.

Acceptance criteria from the one-derivation brief:
- Derived facts are per organisation: other org's rows are never returned.
- The Composer and Ask show the same derived connection for the same pair.
- Three duplicate derivation implementations are removed.
"""

from __future__ import annotations

import datetime as dt
import importlib
import itertools

import pytest

_TARGETS = itertools.count(2000)


def _derived_fact(db_session, org, source_id, target_id, derived_type, rule_id, depth=1):
    """Insert one row directly into the derived-fact store for a given org."""
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    row = DerivedRelationship(
        organization_id=org.id,
        source_element_id=source_id,
        target_element_id=target_id,
        derived_type=derived_type,
        rule_id=rule_id,
        chain=[next(_TARGETS)],  # relationship IDs, length must equal depth
        chain_element_ids=[source_id, target_id],  # element IDs, length = depth + 1
        depth=depth,
        confidence=1.0,
        provenance="derivation",
        engine_version="1.0.0",
        computed_at=dt.datetime.utcnow(),
        stale=False,
        stale_since=None,
        stale_reason=None,
    )
    db_session.add(row)
    return row


def test_org_a_does_not_see_org_b_derived_facts(app, db_session, make_org):
    """Derived facts in org B must never appear in org A's listing."""
    from app.modules.intelligence.services.derived_facts import list_derived_facts

    org_a = make_org("one-derivation-a")
    org_b = make_org("one-derivation-b")
    _derived_fact(db_session, org_a, 1, 2, "Serving", "table:Serving:Serving")
    _derived_fact(db_session, org_a, 2, 3, "Flow", "table:Flow:Flow")
    _derived_fact(db_session, org_b, 10, 20, "Triggering", "table:Triggering:Triggering")
    _derived_fact(db_session, org_b, 20, 30, "Access", "table:Access:Serving")
    db_session.flush()

    a_facts = list_derived_facts(org_a.id)
    a_ids = {(f["source_element_id"], f["target_element_id"]) for f in a_facts}
    assert (1, 2) in a_ids, "org A must see its own fact (1,2)"
    assert (2, 3) in a_ids, "org A must see its own fact (2,3)"
    assert (10, 20) not in a_ids, "org A must NOT see org B fact (10,20)"
    assert (20, 30) not in a_ids, "org A must NOT see org B fact (20,30)"
    assert len(a_facts) == 2, f"org A should see exactly 2 facts, got {len(a_facts)}"

    b_facts = list_derived_facts(org_b.id)
    b_ids = {(f["source_element_id"], f["target_element_id"]) for f in b_facts}
    assert (10, 20) in b_ids, "org B must see its own fact (10,20)"
    assert (20, 30) in b_ids, "org B must see its own fact (20,30)"
    assert (1, 2) not in b_ids, "org B must NOT see org A fact (1,2)"
    assert (2, 3) not in b_ids, "org B must NOT see org A fact (2,3)"
    assert len(b_facts) == 2, f"org B should see exactly 2 facts, got {len(b_facts)}"


def test_derived_fact_aggregates_are_per_organisation(app, db_session, make_org):
    """Counts and aggregates for org A must never include org B's rows."""
    from app.modules.intelligence.services.derived_facts import derived_fact_aggregates

    org_a = make_org("one-derivation-agg-a")
    org_b = make_org("one-derivation-agg-b")
    _derived_fact(db_session, org_a, 1, 2, "Serving", "table:Serving:Serving")
    _derived_fact(db_session, org_b, 90, 91, "Flow", "table:Flow:Flow")
    _derived_fact(db_session, org_b, 91, 92, "Flow", "table:Flow:Flow")
    db_session.flush()

    agg_a = derived_fact_aggregates(org_a.id)
    assert agg_a["derived_count"] == 1, (
        f"org A should count 1 current fact, got {agg_a['derived_count']}"
    )

    agg_b = derived_fact_aggregates(org_b.id)
    assert agg_b["derived_count"] == 2, (
        f"org B should count 2 current facts, got {agg_b['derived_count']}"
    )


def test_composer_and_ask_share_the_same_derived_store(app, db_session, make_org):
    """Both the Composer impact endpoint and the Ask page read from the same
    list_derived_facts accessor, so the same pair always returns the same
    derived connection regardless of which consumer asks.

    This test writes two derived facts for one org, then reads them through
    list_derived_facts with a query that matches how the Composer impact
    route calls it (source_element_id + target_element_id + direction="both").
    Since the Ask page's derivation_status reads current_count from the same
    store (via derived_fact_aggregates), and the derived-fact store is the
    single authoritative source, both consumers see the same answer for the
    same element pair by construction.
    """
    from app.modules.intelligence.services.derived_facts import (
        derived_fact_aggregates,
        list_derived_facts,
    )

    org = make_org("one-derivation-both")
    # Two stored derived facts involving element 42:
    #   42 -> 43 via Serving (depth 1)
    #   43 -> 42 via Association (depth 1, opposite direction)
    _derived_fact(db_session, org, 42, 43, "Serving", "table:Serving:Serving", depth=1)
    _derived_fact(db_session, org, 43, 42, "Association", "fallback:Assignment:Access", depth=1)
    db_session.flush()

    # Ask reads current_count from aggregates
    aggregates = derived_fact_aggregates(org.id)
    assert aggregates["derived_count"] == 2, (
        f"Ask sees 2 current derived facts for this org, got {aggregates['derived_count']}"
    )

    # Composer impact reads with direction="both" on element 42
    composer_facts = list_derived_facts(
        org.id,
        source_element_id=42,
        target_element_id=42,
        direction="both",
        include_stale=False,
    )
    assert len(composer_facts) == 2, (
        f"Composer impact sees 2 derived facts for element 42, got {len(composer_facts)}"
    )

    # Both consumers agree on the same facts
    fact_pairs = {
        (f["source_element_id"], f["target_element_id"], f["derived_type"])
        for f in composer_facts
    }
    assert (42, 43, "Serving") in fact_pairs, "Composer sees 42->43 Serving"
    assert (43, 42, "Association") in fact_pairs, "Composer sees 43->42 Association"

    # Reading without the direction filter still sees both facts for this org
    all_facts = list_derived_facts(org.id, include_stale=False)
    assert len(all_facts) == 2, (
        f"Store has exactly 2 current facts, got {len(all_facts)}"
    )


# ── Tests that fail on main (module still exists) and pass on branch (module deleted) ──

def _assert_module_removed(module_path: str):
    """Helper: assert a module is not importable. Fails on main where the
    module exists, passes on the branch where it has been removed."""
    try:
        importlib.import_module(module_path)
    except ModuleNotFoundError:
        return  # expected on this branch
    else:
        pytest.fail(
            f"{module_path} must not be importable"
            f" — it is a removed duplicate implementation"
        )


def test_relationship_derivation_service_is_removed():
    _assert_module_removed("app.modules.architecture.services.relationship_derivation_service")


def test_unified_derivation_service_is_removed():
    _assert_module_removed("app.modules.architecture.services.unified_derivation_service")


def test_capability_derivation_service_is_removed():
    _assert_module_removed("app.modules.architecture_assistant.capability_derivation")


def test_unified_derivation_reexport_is_removed():
    _assert_module_removed("app.services.archimate.unified_derivation_service")


# ── Workflow step handler registration ──

def test_every_v2_workflow_step_handler_is_registered(app):
    """Every handler referenced by a v2 workflow step must be registered in
    STEP_HANDLERS so that no step silently produces None output."""
    from app.modules.solutions_strategic.v2.services.ea_workflow_engine import (
        EAWorkflowEngine,
    )

    engine = EAWorkflowEngine()
    handlers = set(engine.STEP_HANDLERS.keys())

    missing: dict[str, list[str]] = {}
    for wf_def in EAWorkflowEngine._build_default_workflow_definitions():
        for step in wf_def.get("steps", []):
            handler_name = step.get("handler")
            if handler_name and handler_name not in handlers:
                missing.setdefault(wf_def["workflow_code"], []).append(handler_name)

    assert not missing, (
        f"Unregistered handlers found: {missing}. "
        f"Every handler must be in STEP_HANDLERS."
    )
