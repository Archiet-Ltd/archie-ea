"""T-001 acceptance criteria 11-12: the Derivation Runner (DE-1).

Uses the shared ``tests/conftest.py`` fixtures (``db_session``, ``make_org``),
re-exposed for this directory by the local ``conftest.py`` sitting beside this
file (pytest's conftest discovery does not cross from ``app/modules/`` into
``tests/``). ``db_session`` rolls the whole test back at teardown, so nothing
here leaves residue in the shared, persistent test database.

Setup data is committed (not merely flushed) before calling
``DerivationRunner.run()``: the runner enters ``tenant_scope()``, which calls
``db.session.remove()`` on entry and exit (see
``app/jobs/tenant_safe_job.py``). A rollback only discards work back to the
last savepoint, so uncommitted setup rows would vanish before the runner ever
reads them -- exactly the pattern ``tests/conftest.py`` documents for
``session.commit()`` under ``create_savepoint`` mode.

Mapping to the T-001 brief's numbered Acceptance Criteria:

    11 -> test_runner_loads_only_the_scoped_tenants_rows,
          test_runner_uses_tenant_scope_and_never_hand_writes_organization_id
    12 -> test_derivation_result_shape_and_ratio_is_none_when_no_explicit_relationships,
          test_derivation_result_shape_with_a_populated_model
"""

from __future__ import annotations

import pathlib

import pytest


def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer=layer, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_relationship(db_session, org_id, source, target, type_):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


# --- Acceptance criterion 11: cross-tenant isolation -------------------------


def test_runner_loads_only_the_scoped_tenants_rows(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org_a = make_org("derivation-a")
    org_b = make_org("derivation-b")
    org_a_id, org_b_id = org_a.id, org_b.id

    a1 = _make_element(db_session, org_a_id, "a1")
    a2 = _make_element(db_session, org_a_id, "a2")
    a3 = _make_element(db_session, org_a_id, "a3")
    _make_relationship(db_session, org_a_id, a1, a2, "Serving")
    _make_relationship(db_session, org_a_id, a2, a3, "Serving")

    b1 = _make_element(db_session, org_b_id, "b1")
    b2 = _make_element(db_session, org_b_id, "b2")
    _make_relationship(db_session, org_b_id, b1, b2, "Serving")

    # Capture ids as plain ints before commit/runner touch session state.
    a1_id, a3_id = a1.id, a3.id
    b1_id, b2_id = b1.id, b2.id

    db_session.commit()  # survive tenant_scope()'s internal session reset

    result = DerivationRunner().run(org_a_id)

    # Only org A's two explicit relationships were loaded.
    assert result.explicit_count == 2
    # Only org A's one derivable pair (a1 -> a3, Serving) was produced.
    assert result.derived_count == 1
    row = result.derived[0]
    assert row["source_id"] == a1_id
    assert row["target_id"] == a3_id

    org_b_element_ids = {b1_id, b2_id}
    for derived_row in result.derived:
        assert derived_row["source_id"] not in org_b_element_ids
        assert derived_row["target_id"] not in org_b_element_ids
        for node_id in derived_row["chain"]:
            assert node_id not in org_b_element_ids, "no chain node may belong to another tenant"


def test_runner_uses_tenant_scope_and_never_hand_writes_organization_id():
    """Static check: the runner obtains scope through tenant_scope(), and the
    ORM READS it makes carry no hand-written organization_id predicate (a
    filter keyword argument or a WHERE-clause comparison) -- only the
    tenant_scope()/do_orm_execute listener may filter a READ by tenant.

    A bare substring check for "organization_id" would false-fail: the
    parameter name in ``run(self, organization_id: int)`` and the docstring's
    prose both legitimately contain the string. What must never appear on a
    READ path is a predicate shaped like ``organization_id=...`` (as a filter
    kwarg) or ``organization_id ==`` (as a WHERE comparison).

    T-005's ``_record_run`` WRITES one new ``DerivationRun`` row per
    completed run, and constructing that row legitimately passes
    ``organization_id=organization_id`` as a plain model-constructor keyword
    argument (not a filter) -- exactly the same shape ``TenantMixin``'s own
    ``before_flush`` listener would stamp on the row anyway, made explicit
    here for clarity. It is excluded from this scan by name.
    """
    import app.modules.intelligence.services.derivation_runner as runner_module

    source = pathlib.Path(runner_module.__file__).read_text(encoding="utf-8")
    assert "from app.jobs.tenant_safe_job import tenant_scope" in source
    assert "with tenant_scope(" in source, "the runner must obtain scope through tenant_scope()"

    # Scan line-by-line so the one legitimate write-side constructor call
    # (DerivationRun(organization_id=organization_id, ...)) can be excluded
    # by name without weakening the check for every other line.
    forbidden_predicates = ["organization_id=", "organization_id =="]
    allowed_line_substring = "organization_id=organization_id,"
    for lineno, line in enumerate(source.splitlines(), start=1):
        if line.strip() == allowed_line_substring:
            continue
        for pattern in forbidden_predicates:
            assert pattern not in line, (
                f"found a hand-written organization_id predicate ({pattern!r}) on "
                f"line {lineno} of the runner -- do_orm_execute already filters "
                "READS inside tenant_scope(), and a hand-written predicate on a "
                "TenantMixin model READ would double-filter"
            )


# --- Acceptance criterion 12: DerivationResult shape -------------------------


def test_derivation_result_shape_and_ratio_is_none_when_no_explicit_relationships(
    app, db_session, make_org
):
    from app.modules.intelligence.services.derivation_runner import (
        ENGINE_VERSION,
        DerivationRunner,
    )

    org = make_org("empty-model")
    org_id = org.id
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 0
    assert result.derived_count == 0
    # Never 0: a measured zero and "not computed" must stay distinguishable.
    assert result.ratio is None
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0
    assert result.engine_version == ENGINE_VERSION == "1.0.0"
    assert result.derived == []


def test_derivation_result_shape_with_a_populated_model(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("populated-model")
    org_id = org.id
    e1 = _make_element(db_session, org_id, "e1")
    e2 = _make_element(db_session, org_id, "e2")
    e3 = _make_element(db_session, org_id, "e3")
    _make_relationship(db_session, org_id, e1, e2, "Serving")
    _make_relationship(db_session, org_id, e2, e3, "Serving")
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 2
    assert result.derived_count == 1
    assert result.ratio == pytest.approx(0.5)
    assert result.engine_version == "1.0.0"
    assert len(result.derived) == 1
    assert result.derived[0]["type"] == "Serving"
    assert result.derived[0]["depth"] == 2
    assert len(result.derived[0]["relationship_chain"]) == 2


# --- Layer invariance, not four independent proofs: the runner loads every
# element/relationship for the tenant with no layer filter at all
# (derivation_runner.py has none), so the five tests below establish ONE
# fact -- that a chain derives identically regardless of which ArchiMate
# layer its elements are on -- shown on four single-layer chains and one
# chain crossing four layers. Each uses "Serving" or "Influence", the
# capitalised relationship-type spelling app/services/archimate_derivation_service.py's
# tables are written in; the product's create route stores the lower-case
# form, which the same engine derives differently (build-report-v2.md).


def test_derivation_through_a_motivation_layer_element(app, db_session, make_org):
    """One instance of the layer-invariance fact above, on Motivation
    elements. Uses "Influence", the capitalised relationship-type spelling.
    """
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("layer-motivation")
    org_id = org.id
    a = _make_element(db_session, org_id, "stakeholder", type_="Stakeholder", layer="motivation")
    b = _make_element(db_session, org_id, "driver", type_="Driver", layer="motivation")
    c = _make_element(db_session, org_id, "assessment", type_="Assessment", layer="motivation")
    _make_relationship(db_session, org_id, a, b, "Influence")
    _make_relationship(db_session, org_id, b, c, "Influence")
    # Capture ids as plain ints before commit/runner touch session state
    # (tenant_scope() removes the session; see the runner's own docstring).
    a_id, c_id = a.id, c.id
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 2
    assert result.derived_count == 1
    assert result.derived[0]["source_id"] == a_id
    assert result.derived[0]["target_id"] == c_id
    assert result.derived[0]["type"] == "Influence"


def test_derivation_through_a_strategy_layer_element(app, db_session, make_org):
    """One instance of the layer-invariance fact above, on Strategy
    elements. Uses "Serving", the capitalised relationship-type spelling.
    """
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("layer-strategy")
    org_id = org.id
    a = _make_element(db_session, org_id, "resource", type_="Resource", layer="strategy")
    b = _make_element(db_session, org_id, "capability", type_="Capability", layer="strategy")
    c = _make_element(db_session, org_id, "courseofaction", type_="CourseOfAction", layer="strategy")
    _make_relationship(db_session, org_id, a, b, "Serving")
    _make_relationship(db_session, org_id, b, c, "Serving")
    a_id, c_id = a.id, c.id
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 2
    assert result.derived_count == 1
    assert result.derived[0]["source_id"] == a_id
    assert result.derived[0]["target_id"] == c_id
    assert result.derived[0]["type"] == "Serving"


def test_derivation_through_an_implementation_and_migration_layer_element(app, db_session, make_org):
    """One instance of the layer-invariance fact above, on Implementation &
    Migration elements. Uses "Serving", the capitalised relationship-type
    spelling.
    """
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("layer-impl-migration")
    org_id = org.id
    a = _make_element(db_session, org_id, "workpackage", type_="WorkPackage", layer="implementation_migration")
    b = _make_element(db_session, org_id, "deliverable", type_="Deliverable", layer="implementation_migration")
    c = _make_element(db_session, org_id, "gap", type_="Gap", layer="implementation_migration")
    _make_relationship(db_session, org_id, a, b, "Serving")
    _make_relationship(db_session, org_id, b, c, "Serving")
    a_id, c_id = a.id, c.id
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 2
    assert result.derived_count == 1
    assert result.derived[0]["source_id"] == a_id
    assert result.derived[0]["target_id"] == c_id
    assert result.derived[0]["type"] == "Serving"


def test_derivation_through_a_physical_layer_element(app, db_session, make_org):
    """One instance of the layer-invariance fact above, on Physical
    elements. Uses "Serving", the capitalised relationship-type spelling.
    """
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("layer-physical")
    org_id = org.id
    a = _make_element(db_session, org_id, "equipment", type_="Equipment", layer="physical")
    b = _make_element(db_session, org_id, "facility", type_="Facility", layer="physical")
    c = _make_element(db_session, org_id, "material", type_="Material", layer="physical")
    _make_relationship(db_session, org_id, a, b, "Serving")
    _make_relationship(db_session, org_id, b, c, "Serving")
    a_id, c_id = a.id, c.id
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 2
    assert result.derived_count == 1
    assert result.derived[0]["source_id"] == a_id
    assert result.derived[0]["target_id"] == c_id
    assert result.derived[0]["type"] == "Serving"


def test_derivation_chain_crosses_at_least_four_layers_end_to_end(app, db_session, make_org):
    """The layer-invariance fact above, extended to a single chain of four
    elements each on a different ArchiMate layer (Motivation -> Strategy ->
    Implementation & Migration -> Physical): derivation must produce every
    shortcut across the whole chain, exactly as it does for a same-layer
    chain, because the engine carries no layer filter at all. Uses
    "Serving", the capitalised relationship-type spelling.
    """
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("layer-four-chain")
    org_id = org.id
    a = _make_element(db_session, org_id, "stakeholder", type_="Stakeholder", layer="motivation")
    b = _make_element(db_session, org_id, "courseofaction", type_="CourseOfAction", layer="strategy")
    c = _make_element(
        db_session, org_id, "workpackage", type_="WorkPackage", layer="implementation_migration"
    )
    d = _make_element(db_session, org_id, "equipment", type_="Equipment", layer="physical")
    _make_relationship(db_session, org_id, a, b, "Serving")
    _make_relationship(db_session, org_id, b, c, "Serving")
    _make_relationship(db_session, org_id, c, d, "Serving")
    a_id, b_id, c_id, d_id = a.id, b.id, c.id, d.id
    db_session.commit()

    result = DerivationRunner().run(org_id)

    assert result.explicit_count == 3
    derived_pairs = {(row["source_id"], row["target_id"]): row for row in result.derived}
    # motivation -> implementation_migration (2 hops) and
    # motivation -> physical (3 hops, crossing all four layers) must both
    # be derived, alongside strategy -> physical (2 hops).
    assert (a_id, c_id) in derived_pairs
    assert (a_id, d_id) in derived_pairs
    assert (b_id, d_id) in derived_pairs

    end_to_end = derived_pairs[(a_id, d_id)]
    assert end_to_end["type"] == "Serving"
    assert end_to_end["depth"] == 3
    assert end_to_end["chain"] == [a_id, b_id, c_id, d_id]
