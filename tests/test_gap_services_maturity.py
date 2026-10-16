"""The one capability-maturity gap row, read the same way by both gap
services from the batched maturity helper (ADR-MAT-1, ADR-MAT-4).

Every current/target/target_gap value in a row here is copied from
``CapabilityHeatmapService``'s batched per-capability maturity read; neither
``AIGapDetectionService.find_maturity_gaps`` nor
``GapDiscoveryService.maturity_gap_rows`` computes a level or a comparison of
its own. An unassessed capability is never a gap and never counted as
covered; nothing unrecorded is ever rendered as a fabricated ``0``.

Every test creates the rows it asserts on inside its own organisation and
asserts only about those rows -- the test database is shared and persistent,
so no test assumes a table is globally empty.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.modules.ai_chat.services.ai_gap_detection_service import AIGapDetectionService
from app.services.gap_discovery_service import GapDiscoveryService

pytestmark = pytest.mark.usefixtures("db_session")

GAP_ROW_KEYS = {
    "gap_type",
    "capability_id",
    "element_id",
    "current",
    "target",
    "target_gap",
    "maturity_source",
}
UNASSESSED_ROW_KEYS = {"capability_id", "element_id", "reason"}

TWO_SERVICE_FILES = [
    "app/modules/ai_chat/services/ai_gap_detection_service.py",
    "app/services/gap_discovery_service.py",
]
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _domain(db_session, label):
    code = ("D" + uuid.uuid4().hex[:8]).upper()[:10]
    domain = BusinessDomain(code=code, name=f"Domain {label} {code}")
    db_session.add(domain)
    db_session.flush()
    return domain


def _element(db_session, org_id, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="Capability", layer="strategy", organization_id=org_id)
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
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _org_capability_with_element(db_session, org, label, **kwargs):
    domain = _domain(db_session, label)
    org_id = org.id if org is not None else None
    element = _element(db_session, org_id, f"Element {label}")
    cap = _capability(
        db_session, org, domain=domain, element=element, name=f"Capability {label}", **kwargs
    )
    return cap, element, domain


@pytest.fixture
def two_orgs(db_session, make_org):
    """Org A: c1 2->4 (a gap), c2 4->4 (at target), u1 unassessed, u2 3->null
    (assessed, no target). Org B: b1 1->5 (a gap). Every capability carries
    its own ``archimate_element_id``.
    """
    label = uuid.uuid4().hex[:6]
    org_a = make_org(f"gapmat-a-{label}")
    org_b = make_org(f"gapmat-b-{label}")

    c1, c1_el, _ = _org_capability_with_element(db_session, org_a, f"c1-{label}", current=2, target=4)
    c2, c2_el, _ = _org_capability_with_element(db_session, org_a, f"c2-{label}", current=4, target=4)
    u1, u1_el, _ = _org_capability_with_element(
        db_session, org_a, f"u1-{label}", current=None, target=None
    )
    u2, u2_el, _ = _org_capability_with_element(
        db_session, org_a, f"u2-{label}", current=3, target=None
    )
    b1, b1_el, _ = _org_capability_with_element(db_session, org_b, f"b1-{label}", current=1, target=5)

    return {
        "org_a": org_a,
        "org_b": org_b,
        "c1": c1,
        "c1_el": c1_el,
        "c2": c2,
        "u1": u1,
        "u1_el": u1_el,
        "u2": u2,
        "u2_el": u2_el,
        "b1": b1,
        "b1_el": b1_el,
    }


def _expected_c1_row(fixture):
    c1 = fixture["c1"]
    return {
        "gap_type": "capability_maturity",
        "capability_id": c1.id,
        "element_id": fixture["c1_el"].id,
        "current": 2,
        "target": 4,
        "target_gap": 2,
        "maturity_source": "unified_capabilities",
    }


def _expected_b1_row(fixture):
    b1 = fixture["b1"]
    return {
        "gap_type": "capability_maturity",
        "capability_id": b1.id,
        "element_id": fixture["b1_el"].id,
        "current": 1,
        "target": 5,
        "target_gap": 4,
        "maturity_source": "unified_capabilities",
    }


# ---------------------------------------------------------------------------
# Both services, two-organisation (1)-(4)
# ---------------------------------------------------------------------------


def test_1_find_maturity_gaps_lists_exactly_c1_and_the_right_unassessed(db_session, two_orgs):
    org_a = two_orgs["org_a"]

    result = AIGapDetectionService().find_maturity_gaps(org_a.id)

    assert result["gaps"] == [_expected_c1_row(two_orgs)]
    for row in result["gaps"]:
        assert set(row.keys()) == GAP_ROW_KEYS

    unassessed_ids = {row["capability_id"]: row["reason"] for row in result["unassessed"]}
    assert unassessed_ids == {
        two_orgs["u1"].id: "no_maturity_recorded",
        two_orgs["u2"].id: "no_maturity_target_recorded",
    }
    for row in result["unassessed"]:
        assert set(row.keys()) == UNASSESSED_ROW_KEYS

    gap_ids = {row["capability_id"] for row in result["gaps"]}
    assert two_orgs["c2"].id not in gap_ids
    assert two_orgs["c2"].id not in unassessed_ids
    assert two_orgs["b1"].id not in gap_ids
    assert two_orgs["b1"].id not in unassessed_ids
    assert result["maturity_source"] == "unified_capabilities"
    assert result["reason"] is None


def test_2_maturity_gap_rows_returns_the_identical_dict(db_session, two_orgs):
    org_a = two_orgs["org_a"]

    from_ai_service = AIGapDetectionService().find_maturity_gaps(org_a.id)
    from_discovery_service = GapDiscoveryService().maturity_gap_rows(org_a.id)

    assert from_discovery_service == from_ai_service


def test_3_shared_catalogue_row_appears_in_neither_list_mutation_proof(
    db_session, two_orgs, monkeypatch
):
    """A shared catalogue row (``organization_id IS NULL``) is outside org A's
    population select, so it is structurally absent from both new methods'
    output. Mutation proof, same shape as the T-MAT-1 helper's own guard: the
    deeper reason it can never leak -- the accessor's own strict predicate --
    is still real, not merely an artifact of the population select. Relaxing
    it to the permissive ``or_(== org, IS NULL)`` shape the two
    source-provenance accessors use turns the named assertion red.
    """
    org_a = two_orgs["org_a"]
    domain = _domain(db_session, "shared-3")
    shared_cap = _capability(
        db_session, None, domain=domain, current=1, target=5, scope="reference", name="Shared cap 3"
    )

    ai_result = AIGapDetectionService().find_maturity_gaps(org_a.id)
    discovery_result = GapDiscoveryService().maturity_gap_rows(org_a.id)
    for result in (ai_result, discovery_result):
        ids_in_gaps = {row["capability_id"] for row in result["gaps"]}
        ids_in_unassessed = {row["capability_id"] for row in result["unassessed"]}
        assert shared_cap.id not in ids_in_gaps
        assert shared_cap.id not in ids_in_unassessed

    # Mutation proof on the accessor itself (the load-bearing named
    # assertion): with the strict predicate intact, the shared row is not
    # assessed for org A.
    from app.modules.capabilities.services.capability_heatmap_service import (
        CapabilityHeatmapService,
    )

    def _read_shared_block():
        blocks = CapabilityHeatmapService().maturity_for_capability_ids(
            [shared_cap.id], organization_id=org_a.id
        )
        return blocks[shared_cap.id]

    control = _read_shared_block()
    assert control["assessed"] is False  # the named assertion

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
    mutated = _read_shared_block()
    with pytest.raises(AssertionError):
        assert mutated["assessed"] is False  # goes red under the permissive predicate

    monkeypatch.undo()
    restored = _read_shared_block()
    assert restored["assessed"] is False


def test_4_called_for_b_each_service_lists_only_b1(db_session, two_orgs):
    org_b = two_orgs["org_b"]

    ai_result = AIGapDetectionService().find_maturity_gaps(org_b.id)
    discovery_result = GapDiscoveryService().maturity_gap_rows(org_b.id)

    for result in (ai_result, discovery_result):
        assert result["gaps"] == [_expected_b1_row(two_orgs)]
        assert result["unassessed"] == []


# ---------------------------------------------------------------------------
# Not assessed (5)
# ---------------------------------------------------------------------------


def test_5_unassessed_capabilities_never_gaps_never_covered(db_session, two_orgs, tenant_ctx):
    org_a = two_orgs["org_a"]
    u1_id = two_orgs["u1"].id
    u2_id = two_orgs["u2"].id

    ai_result = AIGapDetectionService().find_maturity_gaps(org_a.id)
    discovery_result = GapDiscoveryService().maturity_gap_rows(org_a.id)
    for result in (ai_result, discovery_result):
        gap_ids = {row["capability_id"] for row in result["gaps"]}
        assert u1_id not in gap_ids
        assert u2_id not in gap_ids

    # find_uncovered_capabilities is an application-mapping fact, untouched
    # and orthogonal to maturity: its row shape has no current/target/gap at
    # all, only a measured "coverage_percentage": 0 for capabilities with no
    # application mapping -- structurally distinct from a maturity gap.
    uncovered = AIGapDetectionService().find_uncovered_capabilities()
    uncovered_ids = {row["capability_id"] for row in uncovered}
    assert u1_id in uncovered_ids
    assert u2_id in uncovered_ids
    for row in uncovered:
        if row["capability_id"] in (u1_id, u2_id):
            assert set(row.keys()) & {"current", "target", "target_gap"} == set()

    # The coverage rows discover_capability_gaps computes are byte-identical
    # whether or not the maturity rows are appended: the maturity read is
    # purely additive, never interleaved with or mutating the coverage loop.
    with tenant_ctx(org_a.id):
        service = GapDiscoveryService()
        with_maturity = service.discover_capability_gaps(organization_id=org_a.id)
        without_maturity = service.discover_capability_gaps(
            organization_id=org_a.id,
            maturity_rows={
                "gaps": None,
                "unassessed": None,
                "maturity_source": "unified_capabilities",
                "reason": "no_tenant_context",
            },
        )
    coverage_only = [row for row in with_maturity if row.get("gap_type") != "capability_maturity"]
    assert coverage_only == without_maturity
    assert any(row.get("gap_type") == "capability_maturity" for row in with_maturity)


# ---------------------------------------------------------------------------
# No computed level (6)
# ---------------------------------------------------------------------------


def test_6_reader_gate_passes_for_both_files():
    from app.modules.intelligence.tests.test_maturity_authority_readers import (
        TestEngineMaturityReadsGoThroughTheHelper,
    )

    gate = TestEngineMaturityReadsGoThroughTheHelper()
    gate.test_no_engine_reads_the_authority_columns_or_the_source_accessors_directly()
    gate.test_accessor_is_called_only_from_its_allowed_callers()


def test_6_forbidden_patterns_absent_from_both_files():
    pattern = re.compile(
        r"current_maturity_level|target_maturity_level|maturity_for_source"
        r"|UnifiedCapability\.maturity_for_capability_ids"
    )
    hits = []
    for rel in TWO_SERVICE_FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == []


def test_6_row_values_equal_a_direct_helper_call(db_session, two_orgs):
    from app.modules.capabilities.services.capability_heatmap_service import (
        CapabilityHeatmapService,
    )

    org_a = two_orgs["org_a"]
    c1 = two_orgs["c1"]

    result = AIGapDetectionService().find_maturity_gaps(org_a.id)
    row = next(r for r in result["gaps"] if r["capability_id"] == c1.id)

    direct_block = CapabilityHeatmapService().maturity_for_capability_ids(
        [c1.id], organization_id=org_a.id
    )[c1.id]

    assert row["current"] == direct_block["current"]
    assert row["target"] == direct_block["target"]
    assert row["target_gap"] == direct_block["target_gap"]


# ---------------------------------------------------------------------------
# Discovery (7)-(11)
# ---------------------------------------------------------------------------


def _assert_discovery_holds(service, org_a, two_orgs):
    coverage_and_maturity = service.discover_capability_gaps(organization_id=org_a.id)
    maturity_rows_in_result = [
        row for row in coverage_and_maturity if row.get("gap_type") == "capability_maturity"
    ]
    assert maturity_rows_in_result == [_expected_c1_row(two_orgs)]

    all_gaps = service.discover_all_gaps(organization_id=org_a.id)
    assert all_gaps["summary"]["by_type"]["capability_maturity"] == 1
    unassessed_ids = {row["capability_id"]: row["reason"] for row in all_gaps["unassessed_capabilities"]}
    assert unassessed_ids == {
        two_orgs["u1"].id: "no_maturity_recorded",
        two_orgs["u2"].id: "no_maturity_target_recorded",
    }
    assert all_gaps["maturity_reason"] is None


def test_7_discover_capability_gaps_appends_only_c1(db_session, two_orgs):
    org_a = two_orgs["org_a"]
    service = GapDiscoveryService()

    result = service.discover_capability_gaps(organization_id=org_a.id)
    maturity_rows_in_result = [row for row in result if row.get("gap_type") == "capability_maturity"]
    assert maturity_rows_in_result == [_expected_c1_row(two_orgs)]


def test_8_discover_all_gaps_summary_and_single_accessor_call(db_session, two_orgs, monkeypatch):
    org_a = two_orgs["org_a"]
    service = GapDiscoveryService()

    from app.modules.capabilities.services.capability_heatmap_service import (
        CapabilityHeatmapService,
    )

    calls = []
    original = CapabilityHeatmapService.maturity_for_capability_ids

    def _spy(self, capability_ids, *, organization_id):
        calls.append(organization_id)
        return original(self, capability_ids, organization_id=organization_id)

    monkeypatch.setattr(CapabilityHeatmapService, "maturity_for_capability_ids", _spy)

    all_gaps = service.discover_all_gaps(organization_id=org_a.id)

    assert all_gaps["summary"]["by_type"]["capability_maturity"] == 1
    unassessed_ids = {
        row["capability_id"]: row["reason"] for row in all_gaps["unassessed_capabilities"]
    }
    assert unassessed_ids == {
        two_orgs["u1"].id: "no_maturity_recorded",
        two_orgs["u2"].id: "no_maturity_target_recorded",
    }
    assert all_gaps["maturity_reason"] is None
    assert calls == [org_a.id]  # the helper's accessor called once for the whole run


def test_9_no_tenant_context_and_organization_id_none(db_session, two_orgs):
    org_a = two_orgs["org_a"]
    service = GapDiscoveryService()

    # Deliberately no tenant_ctx: no g.current_org_id anywhere below.
    rows = service.maturity_gap_rows(None)
    assert rows["gaps"] is None
    assert rows["reason"] == "no_tenant_context"

    coverage_only = service.discover_capability_gaps()
    assert all(row.get("gap_type") != "capability_maturity" for row in coverage_only)

    all_gaps = service.discover_all_gaps()
    assert all_gaps["unassessed_capabilities"] is None
    assert all_gaps["maturity_reason"] == "no_tenant_context"
    assert "capability_maturity" not in all_gaps["summary"]["by_type"]

    # org_a exists only to confirm this test's fixture ran; the assertions
    # above are about the no-tenant path, not org_a's own data.
    assert org_a.id is not None


def test_10_tenant_ctx_resolves_and_7_and_8_hold(db_session, two_orgs, tenant_ctx):
    org_a = two_orgs["org_a"]
    service = GapDiscoveryService()

    with tenant_ctx(org_a.id):
        result = service.discover_capability_gaps(organization_id=None)
        maturity_rows_in_result = [
            row for row in result if row.get("gap_type") == "capability_maturity"
        ]
        assert maturity_rows_in_result == [_expected_c1_row(two_orgs)]

        all_gaps = service.discover_all_gaps(organization_id=None)
        assert all_gaps["summary"]["by_type"]["capability_maturity"] == 1
        unassessed_ids = {
            row["capability_id"]: row["reason"] for row in all_gaps["unassessed_capabilities"]
        }
        assert unassessed_ids == {
            two_orgs["u1"].id: "no_maturity_recorded",
            two_orgs["u2"].id: "no_maturity_target_recorded",
        }
        assert all_gaps["maturity_reason"] is None


def test_11_prioritize_gaps_keeps_the_row_and_save_discovered_gaps_treats_it_like_any_other_type(
    db_session, two_orgs, tenant_ctx
):
    from app.models.implementation_migration import Gap as ImplementationGap

    org_a = two_orgs["org_a"]
    maturity_row = _expected_c1_row(two_orgs)

    service = GapDiscoveryService()
    prioritized = service.prioritize_gaps([dict(maturity_row)])
    assert len(prioritized) == 1
    kept = prioritized[0]
    for key, value in maturity_row.items():
        assert kept[key] == value
    assert "priority" in kept
    assert "priority_score" in kept

    # save_discovered_gaps is untouched by this task, but main has since fixed
    # the ImplementationGap/Gap column-name bug that method's own docstring
    # describes, and tightened its signature to (gaps_data, architecture_id,
    # organization_id). What is in scope here is unchanged: proving the new
    # gap_type string is not special-cased. A maturity row and an
    # already-existing gap type, given the identical shape, produce the
    # identical, now-successful outcome.
    persistable_maturity_row = dict(kept)
    persistable_maturity_row["name"] = f"Capability maturity gap {maturity_row['capability_id']}"
    persistable_other_row = dict(persistable_maturity_row)
    persistable_other_row["gap_type"] = "capability"
    persistable_other_row["name"] = f"Coverage gap {maturity_row['capability_id']}"
    both_names = [persistable_maturity_row["name"], persistable_other_row["name"]]

    with tenant_ctx(org_a.id):
        before = ImplementationGap.query.filter(ImplementationGap.name.in_(both_names)).count()
        maturity_saved = service.save_discovered_gaps(
            {"gaps": [persistable_maturity_row]}, None, org_a.id)
        other_saved = service.save_discovered_gaps(
            {"gaps": [persistable_other_row]}, None, org_a.id)
        after = ImplementationGap.query.filter(ImplementationGap.name.in_(both_names)).count()

        assert maturity_saved == other_saved == {"saved": 1, "duplicates": 0, "failed": 0}
        assert after - before == 2


# ---------------------------------------------------------------------------
# Fabrication (12)
# ---------------------------------------------------------------------------


def test_12_all_unassessed_tenant_never_fabricates_a_zero(db_session, make_org):
    org_c = make_org(f"gapmat-fab-{uuid.uuid4().hex[:6]}")
    cap1, _e1, _d1 = _org_capability_with_element(
        db_session, org_c, "fab-1", current=None, target=None
    )
    cap2, _e2, _d2 = _org_capability_with_element(
        db_session, org_c, "fab-2", current=None, target=3
    )

    ai_result = AIGapDetectionService().find_maturity_gaps(org_c.id)
    discovery_result = GapDiscoveryService().maturity_gap_rows(org_c.id)

    for result in (ai_result, discovery_result):
        serialized = json.dumps(result)
        assert '"current": 0' not in serialized
        assert '"target": 0' not in serialized
        assert '"target_gap": 0' not in serialized

        assert result["gaps"] == []  # measured empty: the ids select ran
        unassessed_ids = {row["capability_id"] for row in result["unassessed"]}
        assert unassessed_ids == {cap1.id, cap2.id}
        for row in result["unassessed"]:
            assert row["reason"] is not None

    # The tenant-less path is a distinct None, never an empty list.
    no_tenant = AIGapDetectionService().find_maturity_gaps(None)
    assert no_tenant["gaps"] is None
    assert no_tenant["unassessed"] is None
    assert no_tenant["reason"] == "no_tenant_context"


# ---------------------------------------------------------------------------
# Callers (13)
# ---------------------------------------------------------------------------


def test_13_both_services_still_construct_and_expose_gap_types():
    """Full caller-compatibility verification runs as separate commands:
    ``python -m pytest tests/test_ai_wiring_ui.py -q`` and every test module
    importing ``GapDiscoveryService``. This is a light in-process smoke
    check that neither service's constructor or public surface broke.
    """
    ai_service = AIGapDetectionService()
    assert callable(getattr(ai_service, "find_maturity_gaps", None))

    discovery_service = GapDiscoveryService()
    assert "capability_maturity" in discovery_service.gap_types
    assert callable(getattr(discovery_service, "maturity_gap_rows", None))
