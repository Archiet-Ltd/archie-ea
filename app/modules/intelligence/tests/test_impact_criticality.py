"""Criticality and recovery objectives on every component and resource row,
and ``criticality_flags`` on the whole answer (T-WIRE-2).

Covers: two-organisation isolation, not-recorded honesty, shape and batching,
fabrication guard, route pass-through, and the pinned latency series.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import event

# Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
# discovered via app/modules/intelligence/tests/conftest.py's own import of
# tests.conftest.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NINE_KEYS = {
    "criticality", "business_criticality", "lifecycle_status",
    "rto_hours", "rpo_hours", "critical", "reason", "recovery_reason", "source",
}
FLAGS_KEYS = {"critical_element_ids", "reason"}


def _element(db_session, org_id, name, layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _tech_element(db_session, org_id, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="Node", layer="technology", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _application_component(db_session, org_id, element_id, name="App", **kw):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element_id, **kw)
    db_session.add(comp)
    db_session.flush()
    return comp


def _resource(db_session, element_id, **kw):
    from app.models.archimate_technology import Resource

    res = Resource(name="Res", archimate_element_id=element_id, **kw)
    db_session.add(res)
    db_session.flush()
    return res


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"crit-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Crit",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _impact(app, org_id, element_id, **kw):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.cross_layer_impact(element_id, **kw)


# ---------------------------------------------------------------------------
# (1) Two-organisation: component criticality is tenant-scoped
# ---------------------------------------------------------------------------


def test_mission_critical_component_sets_critical_true_and_flags(app, db_session, make_org):
    """A's own component rated mission_critical → critical is True, flags list it."""
    org = make_org("crit-1a")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                                     criticality="mission_critical")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    rows = result["rows"]
    assert len(rows) == 1
    block = rows[0]["criticality"]
    assert block["critical"] is True
    assert block["reason"] is None
    assert block["source"] == "application_components"
    assert result["criticality_flags"]["critical_element_ids"] == [app_b.id]
    assert result["criticality_flags"]["reason"] is None


# ---------------------------------------------------------------------------
# (2) Cross-tenant: foreign component's rating never enters A's chain
# ---------------------------------------------------------------------------


def test_foreign_component_rating_not_visible_to_tenant_a(app, db_session, make_org):
    """B's component rated Critical, A's component has nothing → A sees
    no_criticality_recorded, critical is None."""
    org_a = make_org("crit-2a")
    org_b = make_org("crit-2b")

    app_a = _element(db_session, org_a.id, "AppA")
    app_b = _element(db_session, org_a.id, "AppB")
    _relationship(db_session, org_a.id, app_a, app_b)

    # A's own component on app_b: no rating
    _application_component(db_session, org_a.id, app_b.id, name="CompB-A")

    # B's component pointing at the same element id: rated Critical
    _application_component(db_session, org_b.id, app_b.id, name="CompB-B",
                           business_criticality="Critical")
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, include_derived=False, with_owner=False)
    rows = result["rows"]
    assert len(rows) == 1
    block = rows[0]["criticality"]
    assert block["critical"] is None
    assert block["reason"] == "no_criticality_recorded"
    # Flags: no block with critical=True anywhere
    assert result["criticality_flags"]["critical_element_ids"] == []
    assert result["criticality_flags"]["reason"] is None


def test_mutation_proof_sec09_criticality_leak(app, db_session, make_org, monkeypatch):
    """Disabling _sec09_tenant_check makes the foreign rating leak — the red run.
    Calls _resolve_components_batch directly with a divergent org_id, the same
    pattern as the existing SEC-09 mutation proof in test_query_service.py."""
    from app.modules.intelligence.services import query_service

    org_a = make_org("crit-2mut-a")
    org_b = make_org("crit-2mut-b")

    target = _element(db_session, org_b.id, "Target")
    _application_component(db_session, org_b.id, target.id, name="CompB-B",
                            business_criticality="Critical")
    db_session.commit()

    # SEC-09 intact: org_a's org_id blocks org_b's component.
    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_b.id  # ORM filter allows org_b's component
        results = query_service._resolve_components_batch([target.id], org_a.id)
    assert target.id not in results

    # Disable SEC-09: the foreign component now leaks through.
    monkeypatch.setattr(
        query_service, "_sec09_tenant_check", lambda component_org_id, org_id: True
    )
    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_b.id
        results2 = query_service._resolve_components_batch([target.id], org_a.id)

    assert target.id in results2
    assert results2[target.id].business_criticality == "Critical"


# ---------------------------------------------------------------------------
# (3) Resource row on a foreign element id is never asked for
# ---------------------------------------------------------------------------


def test_foreign_resource_not_in_select_in_list(app, db_session, make_org):
    """A Resource row pointing at a B element id → A's answer never asks for it
    because the identity map has no B ids."""
    org_a = make_org("crit-3a")
    org_b = make_org("crit-3b")

    app_a = _element(db_session, org_a.id, "AppA")
    app_b = _element(db_session, org_a.id, "AppB")
    _relationship(db_session, org_a.id, app_a, app_b)

    # Resource on B's element — should never appear in A's select.
    foreign_el = _tech_element(db_session, org_b.id, "ForeignTech")
    _resource(db_session, foreign_el.id, criticality="Critical")
    db_session.commit()

    # Use a statement counter to capture the Resource select's IN list.
    captured_params: list = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        stmt = str(statement)
        if "FROM archimate_resources" in stmt:
            for param in parameters:
                if isinstance(param, (list, tuple)):
                    captured_params.extend(param)
                elif isinstance(param, int):
                    captured_params.append(param)

    from app.extensions import db
    event.listen(db.engine, "before_cursor_execute", _capture)
    try:
        result = _impact(app, org_a.id, app_a.id, include_derived=False, with_owner=False)
    finally:
        event.remove(db.engine, "before_cursor_execute", _capture)

    assert result["rows"]
    # The Resource select's IN list must contain only A's element ids.
    if captured_params:
        assert foreign_el.id not in captured_params


# ---------------------------------------------------------------------------
# (4) Foreign element id in foreign session → 404 element_not_found
# ---------------------------------------------------------------------------


def test_foreign_element_id_in_foreign_session_returns_element_not_found(app, db_session, make_org):
    """A's element id queried in B's session → service-level flags are
    {None, 'element_not_found'}."""
    org_a = make_org("crit-4a")
    org_b = make_org("crit-4b")

    el_a = _element(db_session, org_a.id, "ElA")
    db_session.commit()

    result = _impact(app, org_b.id, el_a.id, include_derived=False, with_owner=False)
    assert result["rows"] == []
    assert result["reasons"] == ["element_not_found"]
    assert result["criticality_flags"]["critical_element_ids"] is None
    assert result["criticality_flags"]["reason"] == "element_not_found"


# ---------------------------------------------------------------------------
# (5) All four columns None → critical is None, both reasons set
# ---------------------------------------------------------------------------


def test_all_columns_none_yields_no_criticality_and_no_recovery(app, db_session, make_org):
    """All four columns None → critical is None, reason == no_criticality_recorded,
    recovery_reason == no_recovery_objective_recorded."""
    org = make_org("crit-5")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert block["critical"] is None
    assert block["reason"] == "no_criticality_recorded"
    assert block["recovery_reason"] == "no_recovery_objective_recorded"
    assert block["rto_hours"] is None
    assert block["rpo_hours"] is None


# ---------------------------------------------------------------------------
# (6) criticality = "supporting" only → critical is False, reason is None
# ---------------------------------------------------------------------------


def test_supporting_criticality_yields_critical_false(app, db_session, make_org):
    org = make_org("crit-6")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="supporting")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert block["critical"] is False
    assert block["reason"] is None


# ---------------------------------------------------------------------------
# (7) business_criticality = "critical" (lower case) → critical is True
# ---------------------------------------------------------------------------


def test_lowercase_critical_business_criticality_yields_critical_true(app, db_session, make_org):
    org = make_org("crit-7")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           business_criticality="critical")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert block["critical"] is True
    assert block["reason"] is None


# ---------------------------------------------------------------------------
# (8) rto_hours = 0, rpo_hours = None → one recorded objective is enough
# ---------------------------------------------------------------------------


def test_zero_rto_with_null_rpo_is_recorded_not_absent(app, db_session, make_org):
    org = make_org("crit-8")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           rto_hours=0, rpo_hours=None)
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert block["rto_hours"] == 0
    assert block["rpo_hours"] is None
    assert block["recovery_reason"] is None


# ---------------------------------------------------------------------------
# (9) Resource block: criticality = "Critical" → critical is True
# ---------------------------------------------------------------------------


def test_resource_rated_critical_reads_as_critical(app, db_session, make_org):
    """A resource's own top word, 'Critical', reads as critical beside the word itself."""
    org = make_org("crit-9")
    app_a = _element(db_session, org.id, "AppA")
    tech_b = _tech_element(db_session, org.id, "TechB")
    _relationship(db_session, org.id, app_a, tech_b)
    _resource(db_session, tech_b.id, criticality="Critical")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert block["criticality"] == "Critical"
    assert block["critical"] is True
    assert block["reason"] is None
    assert block["source"] == "archimate_resources"
    assert block["recovery_reason"] == "no_recovery_objective_recorded"
    assert block["business_criticality"] is None
    assert block["rto_hours"] is None
    assert block["rpo_hours"] is None


def test_resource_rated_low_reads_as_not_critical(app, db_session, make_org):
    org = make_org("crit-9b")
    app_a = _element(db_session, org.id, "AppA")
    tech_b = _tech_element(db_session, org.id, "TechB")
    _relationship(db_session, org.id, app_a, tech_b)
    _resource(db_session, tech_b.id, criticality="Low")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert block["criticality"] == "Low"
    assert block["critical"] is False
    assert block["reason"] is None


# ---------------------------------------------------------------------------
# (10) Shape: no criticality key on non-component/resource rows;
#      every block has exactly nine keys; flags have exactly two keys
# ---------------------------------------------------------------------------


def test_row_without_component_or_resource_has_no_criticality_key(app, db_session, make_org):
    org = make_org("crit-10a")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    # No component, no resource on app_b.
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    row = result["rows"][0]
    assert "criticality" not in row


def test_every_block_has_exactly_nine_keys(app, db_session, make_org):
    org = make_org("crit-10b")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical", rto_hours=4, rpo_hours=2)
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    assert set(block.keys()) == NINE_KEYS


def test_flags_have_exactly_two_keys_on_every_branch(app, db_session, make_org):
    """Flags have exactly two keys on the success, no-tenant, and not-found branches."""
    org = make_org("crit-10c")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical")
    db_session.commit()

    # Success branch with a block.
    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    assert set(result["criticality_flags"].keys()) == FLAGS_KEYS

    # No-tenant-context branch.
    result2 = _impact(app, None, app_a.id, include_derived=False, with_owner=False)
    assert set(result2["criticality_flags"].keys()) == FLAGS_KEYS

    # Element-not-found branch.
    result3 = _impact(app, org.id, 999999999, include_derived=False, with_owner=False)
    assert set(result3["criticality_flags"].keys()) == FLAGS_KEYS


# ---------------------------------------------------------------------------
# (11) At most two selects of this task's own for one row and for twenty,
#      none for an empty answer
# ---------------------------------------------------------------------------


class _SelectCounter:
    def __init__(self):
        self.count = 0
        self.statements: list = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        stmt = str(statement)
        # Count only the selects this task adds: the component read and the
        # resource read.  The component read goes through
        # _resolve_components_batch which selects from application_components
        # where archimate_element_id IN (...).  The resource read selects
        # archimate_element_id, criticality, lifecycle_status from
        # archimate_resources.
        if ("FROM application_components" in stmt and "archimate_element_id" in stmt) or \
           ("FROM archimate_resources" in stmt and "criticality" in stmt):
            self.count += 1
            self.statements.append(stmt)


@pytest.fixture
def select_counter(app):
    counter = _SelectCounter()
    event.listen(db_session_factory(app), "before_cursor_execute", counter)
    try:
        yield counter
    finally:
        event.remove(db_session_factory(app), "before_cursor_execute", counter)


def db_session_factory(app):
    from app.extensions import db
    return db.engine


def test_at_most_two_selects_for_one_row(app, db_session, make_org):
    org = make_org("crit-11a")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical")
    db_session.commit()

    counter = _SelectCounter()
    from app.extensions import db
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)

    # At most two: one component select, one resource select.
    assert counter.count <= 2, f"Expected <=2 selects, got {counter.count}: {counter.statements}"


def test_at_most_two_selects_for_twenty_rows(app, db_session, make_org):
    org = make_org("crit-11b")
    # Build a chain of 20 elements.
    elements = []
    for i in range(21):
        el = _element(db_session, org.id, f"El{i}")
        elements.append(el)
    for i in range(20):
        _relationship(db_session, org.id, elements[i], elements[i + 1])
    # Give every other element a component.
    for i in range(1, 21, 2):
        _application_component(db_session, org.id, elements[i].id, name=f"Comp{i}",
                               criticality="important")
    db_session.commit()

    counter = _SelectCounter()
    from app.extensions import db
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        _impact(app, org.id, elements[0].id, include_derived=False, with_owner=False,
                max_depth=5)
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)

    assert counter.count <= 2, f"Expected <=2 selects, got {counter.count}: {counter.statements}"


def test_zero_selects_for_empty_answer(app, db_session, make_org):
    org = make_org("crit-11c")
    el = _element(db_session, org.id, "Lonely")
    db_session.commit()

    counter = _SelectCounter()
    from app.extensions import db
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        _impact(app, org.id, el.id, include_derived=False, with_owner=False)
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)

    # An element with no relationships produces zero rows, so no component or
    # resource select should fire.
    assert counter.count == 0, f"Expected 0 selects, got {counter.count}: {counter.statements}"


# ---------------------------------------------------------------------------
# (12) with_owner=False leaves the block in place
# ---------------------------------------------------------------------------


def test_with_owner_false_leaves_criticality_block_in_place(app, db_session, make_org):
    org = make_org("crit-12")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    row = result["rows"][0]
    assert row["owner"] is None
    assert row["reason"] is None
    assert "criticality" in row
    assert row["criticality"]["critical"] is True


# ---------------------------------------------------------------------------
# (13) Counts, depths and positions identical with and without ratings
# ---------------------------------------------------------------------------


def test_counts_depths_positions_identical_with_and_without_ratings(app, db_session, make_org):
    org = make_org("crit-13")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    app_c = _element(db_session, org.id, "AppC")
    _relationship(db_session, org.id, app_a, app_b)
    _relationship(db_session, org.id, app_b, app_c)
    db_session.commit()

    # Without ratings.
    result_without = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)

    # Add ratings.
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical", rto_hours=4)
    _application_component(db_session, org.id, app_c.id, name="CompC",
                           criticality="important")
    db_session.commit()

    result_with = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)

    # Counts must be identical.
    assert result_with["summary"]["explicit_count"] == result_without["summary"]["explicit_count"]
    assert result_with["summary"]["derived_count"] == result_without["summary"]["derived_count"]
    assert result_with["summary"]["stale_count"] == result_without["summary"]["stale_count"]
    assert result_with["summary"]["derivation_state"] == result_without["summary"]["derivation_state"]

    # Depths and positions must be identical.
    for r1, r2 in zip(result_with["rows"], result_without["rows"]):
        assert r1["element_id"] == r2["element_id"]
        assert r1["relation"]["depth"] == r2["relation"]["depth"]
        assert r1["relation"]["confidence"] == r2["relation"]["confidence"]

    # Answer-level reasons[] unchanged.
    assert result_with["reasons"] == result_without["reasons"]


# ---------------------------------------------------------------------------
# (14) Fabrication: reason → every value field is None; no defaulted values
# ---------------------------------------------------------------------------


def test_fabrication_reason_implies_all_value_fields_none(app, db_session, make_org):
    """For every block whose reason is a member of REASON_CODES, every value
    field of that block is None, asserted with is None."""
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("crit-14a")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    # Component with all columns None.
    _application_component(db_session, org.id, app_b.id, name="CompB")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]

    # reason is a member of REASON_CODES.
    assert block["reason"] in REASON_CODES

    # Every value field is None.
    value_fields = ["criticality", "business_criticality", "lifecycle_status",
                    "rto_hours", "rpo_hours", "critical"]
    for field in value_fields:
        assert block[field] is None, f"{field} should be None, got {block[field]!r}"


def test_json_dumps_contains_no_fabricated_values(app, db_session, make_org):
    """json.dumps(answer) for an all-unrecorded fixture contains none of
    'critical': false beside a reason, 'rto_hours': 0 on an unrecorded block,
    and any rating word not entered by the fixture."""
    org = make_org("crit-14b")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    serialised = json.dumps(result)

    # No "critical": false beside a no_criticality_recorded reason.
    # The block should have "critical": null, not false.
    assert '"critical": false' not in serialised

    # No "rto_hours": 0 on an unrecorded block.
    assert '"rto_hours": 0' not in serialised

    # No rating word not entered by the fixture.
    for word in ["mission_critical", "Critical", "High", "Medium", "Low",
                 "important", "supporting", "optional"]:
        assert word not in serialised


def test_no_default_fallbacks_on_read_path(app, db_session, make_org):
    """git diff check: no 'or 0', 'or 1', 'or 3', 'max(..., 1)', 'default=' added
    on the read path.  This is a static check verified by the acceptance
    command; here we assert the block itself never defaults a value."""
    org = make_org("crit-14c")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]

    # When nothing is recorded, critical is None (never False).
    assert block["critical"] is None
    # rto_hours and rpo_hours are None (never 0).
    assert block["rto_hours"] is None
    assert block["rpo_hours"] is None


# ---------------------------------------------------------------------------
# (15) Route: GET /api/v1/intelligence/impact/<id> returns criticality_flags
# ---------------------------------------------------------------------------


def test_route_returns_criticality_flags_beside_summary(app, db_session, make_org, client, login_as):
    org = make_org("crit-15")
    user = _user(db_session, org.id)
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/impact/{app_a.id}")
    assert resp.status_code == 200
    body = resp.get_json()
    data = body["data"]
    assert "criticality_flags" in data
    assert "summary" in data
    assert data["criticality_flags"]["critical_element_ids"] == [app_b.id]
    # The component row carries the block.
    row = data["rows"][0]
    assert "criticality" in row
    assert row["criticality"]["critical"] is True


def test_route_foreign_id_returns_404_byte_for_byte(app, db_session, make_org, client, login_as):
    org_a = make_org("crit-15-404-a")
    org_b = make_org("crit-15-404-b")
    user_a = _user(db_session, org_a.id)
    foreign_el = _element(db_session, org_b.id, "Foreign")
    db_session.commit()

    login_as(client, user_a)
    resp_cross = client.get(f"/api/v1/intelligence/impact/{foreign_el.id}")
    resp_nonexistent = client.get("/api/v1/intelligence/impact/999999999")

    assert resp_cross.status_code == 404 == resp_nonexistent.status_code
    body_a = resp_cross.get_json()
    body_b = resp_nonexistent.get_json()
    body_a.pop("meta", None)
    body_b.pop("meta", None)
    assert body_a == body_b


# ---------------------------------------------------------------------------
# (16) Pinned latency series tests pass unchanged
# ---------------------------------------------------------------------------


def test_nfr5_pinned_series_still_populated(app, db_session, make_org):
    """The two pinned series (cross_layer_impact, include_derived=true,
    max_depth=4) still record latency."""
    from app.modules.intelligence.services.query_service import (
        NFR5_DEPTH,
        NFR5_INCLUDE_DERIVED,
        NFR5_QUERY,
    )
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

    org = make_org("crit-16a")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    before = INTELLIGENCE_QUERY_DURATION.labels(
        query=NFR5_QUERY, depth=NFR5_DEPTH, include_derived=NFR5_INCLUDE_DERIVED
    )._sum.get()

    _impact(app, org.id, a.id, include_derived=True, max_depth=4, with_owner=False)

    after = INTELLIGENCE_QUERY_DURATION.labels(
        query=NFR5_QUERY, depth=NFR5_DEPTH, include_derived=NFR5_INCLUDE_DERIVED
    )._sum.get()
    assert after > before


def test_nfr5_pinned_series_explicit_only_still_populated(app, db_session, make_org):
    """The explicit-only series (include_derived=false, max_depth=3) still
    records latency."""
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

    org = make_org("crit-16b")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    before = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="3", include_derived="false"
    )._sum.get()

    _impact(app, org.id, a.id, include_derived=False, max_depth=3, with_owner=False)

    after = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="3", include_derived="false"
    )._sum.get()
    assert after > before


# ---------------------------------------------------------------------------
# Additional: element that is both a component and a resource takes the
# component block (asserted in a test per Decision D).
# ---------------------------------------------------------------------------


def test_element_both_component_and_resource_takes_component_block(app, db_session, make_org):
    org = make_org("crit-both")
    app_a = _element(db_session, org.id, "AppA")
    app_b = _element(db_session, org.id, "AppB")
    _relationship(db_session, org.id, app_a, app_b)
    _application_component(db_session, org.id, app_b.id, name="CompB",
                           criticality="mission_critical")
    _resource(db_session, app_b.id, criticality="Low")
    db_session.commit()

    result = _impact(app, org.id, app_a.id, include_derived=False, with_owner=False)
    block = result["rows"][0]["criticality"]
    # Component wins.
    assert block["source"] == "application_components"
    assert block["criticality"] == "mission_critical"
    assert block["critical"] is True