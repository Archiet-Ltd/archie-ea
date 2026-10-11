"""``CapabilityHeatmapService.maturity_for_elements`` and
``.maturity_for_capability_ids`` -- the one batched, tri-state maturity read
every engine makes (ADR-MAT-1), and ``get_gap_alerts``' ``maturity_gaps``
section repointed onto it (ADR-MAT-2).

Every test creates the rows it asserts on inside its own organisation and
asserts only about those rows -- the test database is shared and may already
carry rows from seeds or other sessions, so no test assumes a table is
globally empty or that a count equals a repository-wide total.
"""

from __future__ import annotations

import datetime
import json
import uuid

import pytest

from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.modules.capabilities.services.capability_heatmap_service import (
    CapabilityHeatmapService,
    query_counter,
)

pytestmark = pytest.mark.usefixtures("db_session")

TEN_KEYS = {
    "capability_id",
    "element_id",
    "current",
    "target",
    "assessed",
    "assessed_on",
    "under_target",
    "target_gap",
    "reason",
    "maturity_source",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _domain(db_session, label):
    """A BusinessDomain with a random, collision-free code (max 10 chars)."""

    code = ("D" + uuid.uuid4().hex[:8]).upper()[:10]
    domain = BusinessDomain(code=code, name=f"Domain {label} {code}")
    db_session.add(domain)
    db_session.flush()
    return domain


def _element(db_session, org_id, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(
        name=name, type="Capability", layer="strategy", organization_id=org_id
    )
    db_session.add(el)
    db_session.flush()
    return el


def _capability(
    db_session,
    org,
    *,
    domain=None,
    element=None,
    current=None,
    target=None,
    scope="tenant",
    name=None,
    assessment_date=None,
):
    cap = UnifiedCapability(
        name=name or f"Capability {uuid.uuid4().hex[:8]}",
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        scope=scope,
        organization_id=org.id if org is not None else None,
        domain_id=domain.id if domain is not None else None,
        archimate_element_id=element.id if element is not None else None,
        current_maturity_level=current,
        target_maturity_level=target,
        maturity_assessment_date=assessment_date,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _org_capability_with_element(db_session, org, label, **kwargs):
    """A capability of *org*, its own domain and its own linked element --
    the fixture shape this task's brief specifies: each organisation gets a
    ``BusinessDomain``, an ``ArchiMateElement`` of type Capability and a
    ``UnifiedCapability`` row pointing at it through ``archimate_element_id``.
    """

    domain = _domain(db_session, label)
    org_id = org.id if org is not None else None
    element = _element(db_session, org_id, f"Element {label}")
    cap = _capability(
        db_session, org, domain=domain, element=element, name=f"Capability {label}", **kwargs
    )
    return cap, element, domain


# ---------------------------------------------------------------------------
# Two-organisation (1)-(4)
# ---------------------------------------------------------------------------


def test_1_capability_id_owned_by_a_asked_for_by_b_is_not_assessed(db_session, make_org):
    org_a = make_org("mat1-two-org-a1")
    org_b = make_org("mat1-two-org-b1")
    cap_a, _element_a, _domain_a = _org_capability_with_element(
        db_session, org_a, "two-org-1", current=3, target=4
    )

    result = CapabilityHeatmapService().maturity_for_capability_ids(
        [cap_a.id], organization_id=org_b.id
    )

    block = result[cap_a.id]
    assert block["reason"] == "no_maturity_recorded"
    assert block["current"] is None
    assert block["target"] is None
    assert block["under_target"] is None
    assert block["capability_id"] == cap_a.id


def test_2_element_id_owned_by_a_asked_for_by_b_is_not_assessed(db_session, make_org):
    org_a = make_org("mat1-two-org-a2")
    org_b = make_org("mat1-two-org-b2")
    cap_a, element_a, _domain_a = _org_capability_with_element(
        db_session, org_a, "two-org-2", current=3, target=4
    )
    assert cap_a.id  # the row exists; B must still not see its level

    result = CapabilityHeatmapService().maturity_for_elements(
        [element_a.id], organization_id=org_b.id
    )

    block = result[element_a.id]
    assert block["reason"] == "no_maturity_recorded"
    assert block["current"] is None
    assert block["target"] is None
    assert block["under_target"] is None
    assert block["element_id"] == element_a.id
    assert block["capability_id"] is None


def test_3_shared_catalogue_row_never_carries_its_maturity_mutation_proof(
    db_session, make_org, monkeypatch
):
    """Identity comes from elsewhere, maturity never: a shared/reference
    catalogue row is visible for other purposes, but its maturity is never
    read as this tenant's own assessment. Mutation-proved: the same
    assertion raises ``AssertionError`` once the accessor's strict predicate
    is relaxed to the permissive ``or_(== org, IS NULL)`` shape the two
    source-provenance members use.
    """

    org_a = make_org("mat1-shared-3")
    domain = _domain(db_session, "shared-3")
    shared_cap = _capability(
        db_session, None, domain=domain, current=3, target=4, scope="reference", name="Shared cap 3"
    )

    def _read():
        result = CapabilityHeatmapService().maturity_for_capability_ids(
            [shared_cap.id], organization_id=org_a.id
        )
        return result[shared_cap.id]

    control = _read()
    assert control["assessed"] is False

    def _permissive_maturity_for_capability_ids(capability_ids, *, organization_id):
        from sqlalchemy import or_

        from app.modules.intelligence.services.reason_codes import validate_reason_code

        wanted = sorted(set(capability_ids))
        result = {
            cid: {
                "current_maturity_level": None,
                "target_maturity_level": None,
                "reason_code": validate_reason_code("no_maturity_recorded"),
            }
            for cid in wanted
        }
        if not wanted:
            return result
        query = UnifiedCapability.query.filter(
            UnifiedCapability.id.in_(wanted),
            or_(
                UnifiedCapability.organization_id == organization_id,
                UnifiedCapability.organization_id.is_(None),
            ),
        )
        for row in query.all():
            if row.current_maturity_level is None:
                continue
            result[row.id] = {
                "current_maturity_level": row.current_maturity_level,
                "target_maturity_level": row.target_maturity_level,
                "reason_code": None,
            }
        return result

    monkeypatch.setattr(
        UnifiedCapability, "maturity_for_capability_ids", _permissive_maturity_for_capability_ids
    )
    mutated = _read()
    with pytest.raises(AssertionError):
        assert mutated["assessed"] is False

    monkeypatch.undo()
    restored = _read()
    assert restored["assessed"] is False


def test_4_b_owned_row_pointed_at_a_element_fk_is_not_tenant_checked(db_session, make_org):
    org_a = make_org("mat1-fk-a4")
    org_b = make_org("mat1-fk-b4")
    domain_a = _domain(db_session, "fk-4")
    element_a = _element(db_session, org_a.id, "Element FK 4")
    # A B-owned row whose archimate_element_id is A's element -- the FK
    # itself is not tenant-checked.
    _capability(
        db_session,
        org_b,
        domain=domain_a,
        element=element_a,
        current=5,
        target=5,
        name="B row on A's element",
    )

    result = CapabilityHeatmapService().maturity_for_elements(
        [element_a.id], organization_id=org_a.id
    )
    block = result[element_a.id]
    assert block["reason"] == "no_maturity_recorded"
    assert block["current"] is None
    assert block["assessed"] is False
    assert block["capability_id"] is None


# ---------------------------------------------------------------------------
# Not assessed (5)-(8)
# ---------------------------------------------------------------------------


def test_5_current_null_target_recorded_is_not_assessed(db_session, make_org):
    org = make_org("mat1-notassessed-5")
    cap, _element, _domain = _org_capability_with_element(
        db_session, org, "notassessed-5", current=None, target=4
    )

    result = CapabilityHeatmapService().maturity_for_capability_ids(
        [cap.id], organization_id=org.id
    )
    block = result[cap.id]
    assert block["assessed"] is False
    assert block["under_target"] is None
    assert block["target_gap"] is None
    assert block["reason"] == "no_maturity_recorded"


def test_6_current_recorded_target_null_has_its_own_reason(db_session, make_org):
    org = make_org("mat1-notarget-6")
    cap, _element, _domain = _org_capability_with_element(
        db_session, org, "notarget-6", current=3, target=None
    )

    result = CapabilityHeatmapService().maturity_for_capability_ids(
        [cap.id], organization_id=org.id
    )
    block = result[cap.id]
    assert block["assessed"] is True
    assert block["under_target"] is None
    assert block["target_gap"] is None
    assert block["reason"] == "no_maturity_target_recorded"


@pytest.mark.parametrize(
    "current, target, expected_under_target, expected_gap",
    [
        (2, 4, True, 2),
        (4, 4, False, 0),
        (5, 3, False, -2),
    ],
)
def test_7_under_target_and_gap_are_a_real_comparison_and_subtraction(
    db_session, make_org, current, target, expected_under_target, expected_gap
):
    org = make_org(f"mat1-gap7-{current}-{target}-{uuid.uuid4().hex[:6]}")
    cap, _element, _domain = _org_capability_with_element(
        db_session, org, f"gap-7-{current}-{target}", current=current, target=target
    )

    result = CapabilityHeatmapService().maturity_for_capability_ids(
        [cap.id], organization_id=org.id
    )
    block = result[cap.id]
    assert block["under_target"] is expected_under_target
    assert block["target_gap"] == expected_gap
    assert block["reason"] is None


def test_8_assessed_on_is_independent_of_assessed(db_session, make_org):
    org = make_org("mat1-assessedon-8")
    cap_no_date, _e1, _d1 = _org_capability_with_element(
        db_session, org, "assessedon-8-no-date", current=2, target=4
    )
    assessment_date = datetime.datetime(2026, 3, 1)
    cap_with_date, _e2, _d2 = _org_capability_with_element(
        db_session,
        org,
        "assessedon-8-with-date",
        current=2,
        target=4,
        assessment_date=assessment_date,
    )

    result = CapabilityHeatmapService().maturity_for_capability_ids(
        [cap_no_date.id, cap_with_date.id], organization_id=org.id
    )
    assert result[cap_no_date.id]["assessed"] is True
    assert result[cap_no_date.id]["assessed_on"] is None
    assert result[cap_with_date.id]["assessed"] is True
    assert result[cap_with_date.id]["assessed_on"] == assessment_date.isoformat()


# ---------------------------------------------------------------------------
# Shape and batching (9)-(11)
# ---------------------------------------------------------------------------


def test_9_every_id_asked_for_is_present_including_ids_with_no_row(db_session, make_org):
    org = make_org("mat1-shape-9")
    cap, element, _domain = _org_capability_with_element(
        db_session, org, "shape-9", current=2, target=4
    )
    missing_cap_id = -1
    missing_element_id = -2

    cap_result = CapabilityHeatmapService().maturity_for_capability_ids(
        [cap.id, missing_cap_id], organization_id=org.id
    )
    assert set(cap_result.keys()) == {cap.id, missing_cap_id}
    assert cap_result[missing_cap_id]["capability_id"] == missing_cap_id

    element_result = CapabilityHeatmapService().maturity_for_elements(
        [element.id, missing_element_id], organization_id=org.id
    )
    assert set(element_result.keys()) == {element.id, missing_element_id}
    assert element_result[missing_element_id]["capability_id"] is None
    assert element_result[missing_element_id]["element_id"] == missing_element_id


def test_10_empty_input_returns_empty_dict_with_no_query(db_session, make_org):
    org = make_org("mat1-empty-10")
    service = CapabilityHeatmapService()

    query_counter.start()
    try:
        assert service.maturity_for_capability_ids([], organization_id=org.id) == {}
        assert query_counter.count == 0
        assert service.maturity_for_elements([], organization_id=org.id) == {}
        assert query_counter.count == 0
    finally:
        query_counter.stop()


def test_11_statement_count_is_two_regardless_of_id_count(db_session, make_org):
    org = make_org("mat1-count-11")
    caps = []
    elements = []
    for i in range(20):
        cap, element, _domain = _org_capability_with_element(
            db_session, org, f"count-11-{i}", current=(i % 5) + 1, target=5
        )
        caps.append(cap)
        elements.append(element)
    service = CapabilityHeatmapService()

    query_counter.start()
    try:
        service.maturity_for_capability_ids([caps[0].id], organization_id=org.id)
        assert query_counter.count == 2, query_counter.queries
    finally:
        query_counter.stop()

    query_counter.start()
    try:
        service.maturity_for_capability_ids([c.id for c in caps], organization_id=org.id)
        assert query_counter.count == 2, query_counter.queries
    finally:
        query_counter.stop()

    query_counter.start()
    try:
        service.maturity_for_elements([elements[0].id], organization_id=org.id)
        assert query_counter.count == 2, query_counter.queries
    finally:
        query_counter.stop()

    query_counter.start()
    try:
        service.maturity_for_elements([e.id for e in elements], organization_id=org.id)
        assert query_counter.count == 2, query_counter.queries
    finally:
        query_counter.stop()


# ---------------------------------------------------------------------------
# Fabrication (12)-(13)
# ---------------------------------------------------------------------------


def test_12_every_block_carries_exactly_the_ten_keys(db_session, make_org):
    org = make_org("mat1-keys-12")
    assessed_cap, assessed_element, _d1 = _org_capability_with_element(
        db_session, org, "keys-12-assessed", current=2, target=4
    )
    no_target_cap, no_target_element, _d2 = _org_capability_with_element(
        db_session, org, "keys-12-notarget", current=2, target=None
    )
    not_assessed_cap, not_assessed_element, _d3 = _org_capability_with_element(
        db_session, org, "keys-12-notassessed", current=None, target=None
    )
    missing_id = -3

    cap_result = CapabilityHeatmapService().maturity_for_capability_ids(
        [assessed_cap.id, no_target_cap.id, not_assessed_cap.id, missing_id],
        organization_id=org.id,
    )
    assert cap_result  # non-empty: the key-set check below is meaningful
    for block in cap_result.values():
        assert set(block.keys()) == TEN_KEYS

    element_result = CapabilityHeatmapService().maturity_for_elements(
        [assessed_element.id, no_target_element.id, not_assessed_element.id, -4],
        organization_id=org.id,
    )
    assert element_result
    for block in element_result.values():
        assert set(block.keys()) == TEN_KEYS


def test_13_no_absence_is_ever_rendered_as_a_fabricated_zero(db_session, make_org, tenant_ctx):
    org = make_org("mat1-fabrication-13")
    not_assessed_cap, _e, domain = _org_capability_with_element(
        db_session, org, "fabrication-13-notassessed", current=None, target=4
    )
    missing_id = -5

    result = CapabilityHeatmapService().maturity_for_capability_ids(
        [not_assessed_cap.id, missing_id], organization_id=org.id
    )
    checked_any = False
    for block in result.values():
        if block["assessed"] is False:
            checked_any = True
            assert block["current"] is None
            assert block["target"] is None
            assert block["target_gap"] is None
            serialized = json.dumps(block)
            assert '"current": 0' not in serialized
            assert '"target": 0' not in serialized
            assert '"target_gap": 0' not in serialized
    assert checked_any, "expected at least one not-assessed block in this fixture"

    # The same check over every block get_gap_alerts()["maturity_gaps"]
    # returns -- a regression guard, since a row only ever appears there once
    # it is assessed and under target, so none of its numeric fields should
    # ever legitimately be a fabricated 0 hiding an absence.
    with tenant_ctx(org.id):
        alerts = CapabilityHeatmapService().get_gap_alerts()
    for row in alerts["maturity_gaps"]:
        serialized = json.dumps(row)
        assert '"current": 0' not in serialized
        assert '"target": 0' not in serialized
        assert '"gap": 0' not in serialized


# ---------------------------------------------------------------------------
# get_gap_alerts (14)-(16)
# ---------------------------------------------------------------------------


def test_14_get_gap_alerts_lists_only_the_real_two_or_more_gap(db_session, make_org, tenant_ctx):
    org_a = make_org("mat1-gapalerts-a14")
    domain = _domain(db_session, "gapalerts-14")
    cap_c = _capability(db_session, org_a, domain=domain, current=2, target=4, name="Cap C 14")
    _cap_d = _capability(db_session, org_a, domain=domain, current=3, target=4, name="Cap D 14")
    _cap_u = _capability(db_session, org_a, domain=domain, current=None, target=4, name="Cap U 14")

    with tenant_ctx(org_a.id):
        alerts = CapabilityHeatmapService().get_gap_alerts()

    names = [row["name"] for row in alerts["maturity_gaps"]]
    assert names == [cap_c.name], names
    assert _cap_d.name not in names
    assert _cap_u.name not in names

    row = alerts["maturity_gaps"][0]
    assert row == {
        "id": cap_c.id,
        "name": cap_c.name,
        "domain": domain.name,
        "current": 2,
        "target": 4,
        "gap": 2,
    }
    assert alerts["summary"]["maturity_gap_count"] == 1
    assert alerts["summary"]["maturity_gap_reason"] is None


def test_15_get_gap_alerts_inside_b_excludes_a(db_session, make_org, tenant_ctx):
    org_a = make_org("mat1-gapalerts-a15")
    org_b = make_org("mat1-gapalerts-b15")
    domain = _domain(db_session, "gapalerts-15")
    cap_a = _capability(db_session, org_a, domain=domain, current=1, target=5, name="Cap A 15")

    with tenant_ctx(org_b.id):
        alerts = CapabilityHeatmapService().get_gap_alerts()

    names = [row["name"] for row in alerts["maturity_gaps"]]
    assert cap_a.name not in names


def test_16_get_gap_alerts_with_no_tenant_context_fails_closed(db_session, make_org):
    org = make_org("mat1-gapalerts-notenant-16")
    domain = _domain(db_session, "gapalerts-notenant-16")
    _capability(db_session, org, domain=domain, current=1, target=5, name="Should not leak 16")

    # Deliberately no tenant_ctx: g.current_org_id is never set.
    alerts = CapabilityHeatmapService().get_gap_alerts()

    assert alerts["maturity_gaps"] == []
    assert alerts["summary"]["maturity_gap_reason"] == "no_tenant_context"
