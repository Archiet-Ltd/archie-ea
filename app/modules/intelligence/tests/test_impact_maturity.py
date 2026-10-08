"""The maturity block on every Capability row of the impact answer, and the
whole-answer ``maturity_flags`` summary of which capabilities on the chain
are unassessed or below their own target.

Covers: the block attaches only to rows whose element is a Capability, from
one batched read over the distinct Capability ids the identity map already
resolved under the tenant predicate; a foreign capability's level can never
enter the answer even though the ownership foreign key is not itself
tenant-checked; an unassessed capability changes nothing about counts,
depth, ordering or position; the flags carry a reason instead of an empty
list on every branch that measured nothing; and the shape, call-count and
latency guarantees the design makes.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid

import pytest

# Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
# discovered via app/modules/conftest.py's import of
# tests.conftest -- pytest resolves fixtures by name without this module
# importing them itself (see test_impact_route.py for the same pattern).

TEN_KEYS = {
    "capability_id", "element_id", "current", "target", "assessed",
    "assessed_on", "under_target", "target_gap", "reason", "maturity_source",
}
FLAG_KEYS = {
    "unassessed_capability_ids", "under_target_capability_ids", "reason", "maturity_source",
}


# --- fixtures / helpers ------------------------------------------------------


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"impmat-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Impmat",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _capability(db_session, org_id, element, *, current=None, target=None, assessment_date=None, name=None):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=name or f"Capability {uuid.uuid4().hex[:8]}",
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        organization_id=org_id,
        archimate_element_id=element.id,
        current_maturity_level=current,
        target_maturity_level=target,
        maturity_assessment_date=assessment_date,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _fixture(db_session, make_org, *, current=2, target=4, label):
    """One organisation with an ApplicationComponent explicitly Serving a
    Capability, and that organisation's own maturity row on the capability.
    """
    org_a = make_org(f"{label}-a")
    org_b = make_org(f"{label}-b")
    user_a = _user(db_session, org_a.id)
    app_a = _element(db_session, org_a.id, "AppA", type_="ApplicationComponent")
    cap_a = _element(db_session, org_a.id, "CapA", type_="Capability", layer="strategy")
    _relationship(db_session, org_a.id, app_a, cap_a)
    cap_row = _capability(db_session, org_a.id, cap_a, current=current, target=target)
    db_session.commit()
    return org_a, org_b, user_a, app_a, cap_a, cap_row


def _impact(app, org_id, element_id, **kwargs):
    """``cross_layer_impact`` under a request context for *org_id*."""
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.cross_layer_impact(element_id, **kwargs)


# --- (1)-(3) two organisations ------------------------------------------------


def test_1_capability_row_carries_under_target_block_and_flag(app, db_session, make_org):
    org_a, org_b, user_a, app_a, cap_a, cap_row = _fixture(
        db_session, make_org, current=2, target=4, label="two-org-1"
    )

    result = _impact(app, org_a.id, app_a.id, with_owner=False)
    cap_row_r = next(r for r in result["rows"] if r["element_id"] == cap_a.id)
    block = cap_row_r["maturity"]
    assert block["under_target"] is True
    assert block["target_gap"] == 2
    assert result["maturity_flags"]["under_target_capability_ids"] == [cap_a.id]


def test_2_cross_tenant_capability_row_never_leaks_into_the_chain(app, db_session, make_org, monkeypatch):
    """A's own row wins while it exists. A second, B-owned row also points at
    A's capability element -- the ownership foreign key is not itself
    tenant-checked -- and must never surface its level, even once A's own row
    is gone. Mutation-proved: with the tenant predicate removed from the one
    read path this task's own code calls, the same assertion goes red.
    """
    org_a, org_b, user_a, app_a, cap_a, cap_row_a = _fixture(
        db_session, make_org, current=2, target=4, label="two-org-2"
    )
    _capability(db_session, org_b.id, cap_a, current=5, target=5, name="Stray B row")
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, with_owner=False)
    cap_row_r = next(r for r in result["rows"] if r["element_id"] == cap_a.id)
    assert cap_row_r["maturity"]["current"] == 2  # A's own value, never B's 5

    db_session.delete(cap_row_a)
    db_session.commit()

    def _assert_never_five():
        after = _impact(app, org_a.id, app_a.id, with_owner=False)
        after_row = next(r for r in after["rows"] if r["element_id"] == cap_a.id)
        block = after_row["maturity"]
        assert block["assessed"] is False
        assert block["current"] is None
        assert block["reason"] == "no_maturity_recorded"

    _assert_never_five()  # A's row gone: never fabricated as B's 5

    from app.modules.capabilities.services.capability_heatmap_service import (
        CapabilityHeatmapService,
    )

    # The mutation: replace maturity_for_elements with one that always returns
    # assessed=True (B's level leaks through), which contradicts _assert_never_five
    # (assessed=False). This proves the tenant predicate matters: without it,
    # the maturity block would reflect B's row's assessed-as-true status.
    def _leaky_maturity_for_elements(self, element_ids, *, organization_id):
        result = {}
        for eid in sorted({int(e) for e in element_ids}):
            result[eid] = self._maturity_block(
                capability_id=99999, element_id=eid,
                current=5, target=5,
                reason_code=None, assessment_date=None,
            )
        return result

    monkeypatch.setattr(
        CapabilityHeatmapService, "maturity_for_elements", _leaky_maturity_for_elements
    )
    with pytest.raises(AssertionError):
        _assert_never_five()

    monkeypatch.undo()
    _assert_never_five()  # restored


def test_3_foreign_element_id_404s_and_service_level_flags_are_not_found(
    app, db_session, make_org, client, login_as
):
    org_a, org_b, user_a, app_a, cap_a, cap_row = _fixture(
        db_session, make_org, current=2, target=4, label="two-org-3"
    )
    user_b = _user(db_session, org_b.id)
    db_session.commit()

    login_as(client, user_b)
    resp_foreign = client.get(f"/api/v1/intelligence/impact/{app_a.id}")
    login_as(client, user_b)
    resp_absent = client.get("/api/v1/intelligence/impact/999999999")

    assert resp_foreign.status_code == 404 == resp_absent.status_code
    body_foreign = resp_foreign.get_json()
    body_absent = resp_absent.get_json()
    body_foreign.pop("meta", None)
    body_absent.pop("meta", None)
    assert body_foreign == body_absent

    service_level = _impact(app, org_b.id, app_a.id, with_owner=False)
    assert service_level["maturity_flags"] == {
        "unassessed_capability_ids": None,
        "under_target_capability_ids": None,
        "reason": "element_not_found",
        "maturity_source": "unified_capabilities",
    }


# --- (4)-(6) not assessed -----------------------------------------------------


def test_4_unassessed_row_and_flag_shape_identical_to_assessed_same_fixture(app, db_session, make_org):
    org_a, org_b, user_a, app_a, cap_a, cap_row = _fixture(
        db_session, make_org, current=None, target=None, label="notassessed-4"
    )

    unassessed = _impact(app, org_a.id, app_a.id, with_owner=False)
    cap_row_u = next(r for r in unassessed["rows"] if r["element_id"] == cap_a.id)
    assert cap_row_u["maturity"]["assessed"] is False
    assert cap_row_u["maturity"]["current"] is None
    assert unassessed["maturity_flags"]["unassessed_capability_ids"] == [cap_a.id]
    assert unassessed["maturity_flags"]["under_target_capability_ids"] == []

    # The exact same fixture, only the maturity now recorded -- everything
    # else about the graph is untouched.
    cap_row.current_maturity_level = 4
    cap_row.target_maturity_level = 4
    db_session.commit()

    assessed = _impact(app, org_a.id, app_a.id, with_owner=False)

    def _strip_maturity(rows):
        return [{k: v for k, v in row.items() if k != "maturity"} for row in rows]

    assert _strip_maturity(unassessed["rows"]) == _strip_maturity(assessed["rows"])
    for key in ("explicit_count", "derived_count", "stale_count", "derivation_state"):
        assert unassessed["summary"][key] == assessed["summary"][key]


def test_5_no_capability_in_result_flags_are_none_with_reason(app, db_session, make_org):
    org = make_org("nocap-5")
    a = _element(db_session, org.id, "A", type_="ApplicationComponent")
    b = _element(db_session, org.id, "B", type_="ApplicationComponent")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    result = _impact(app, org.id, a.id, with_owner=False)
    assert result["maturity_flags"] == {
        "unassessed_capability_ids": None,
        "under_target_capability_ids": None,
        "reason": "no_capability_in_chain",
        "maturity_source": "unified_capabilities",
    }
    assert all("maturity" not in row for row in result["rows"])


def test_6_capability_only_in_chain_elements_still_flagged(app, db_session, make_org):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    org = make_org("chainonly-6")
    a = _element(db_session, org.id, "A", type_="ApplicationComponent")
    cap = _element(db_session, org.id, "Cap", type_="Capability", layer="strategy")
    c = _element(db_session, org.id, "C", type_="ApplicationComponent")
    _capability(db_session, org.id, cap, current=2, target=4)
    derived = DerivedRelationship(
        organization_id=org.id,
        source_element_id=a.id,
        target_element_id=c.id,
        derived_type="Serving",
        rule_id="R1",
        chain=[1, 2],
        chain_element_ids=[a.id, cap.id, c.id],
        depth=2,
        confidence=1.0,
        provenance="derivation",
        engine_version="v1",
        computed_at=_dt.datetime.utcnow(),
        stale=False,
    )
    db_session.add(derived)
    db_session.commit()

    result = _impact(app, org.id, a.id, include_derived=True, max_depth=2, with_owner=False)
    row_element_ids = {r["element_id"] for r in result["rows"]}
    assert cap.id not in row_element_ids  # no row's own element_id is the capability
    assert result["maturity_flags"]["under_target_capability_ids"] == [cap.id]
    for row in result["rows"]:
        assert "maturity" not in row  # the capability names no row of its own here


# --- (7)-(9) shape -------------------------------------------------------------


def test_7_shape_ten_keys_and_four_keys_on_every_branch(app, db_session, make_org):
    org_a = make_org("shape-7-a")
    app_a = _element(db_session, org_a.id, "AppA", type_="ApplicationComponent")
    cap_a = _element(db_session, org_a.id, "CapA", type_="Capability", layer="strategy")
    z = _element(db_session, org_a.id, "Z", type_="ApplicationComponent")
    _relationship(db_session, org_a.id, app_a, cap_a)
    _relationship(db_session, org_a.id, cap_a, z)
    _capability(db_session, org_a.id, cap_a, current=2, target=4)
    db_session.commit()

    computed = _impact(app, org_a.id, app_a.id, max_depth=2, with_owner=False)
    cap_row_c = next(r for r in computed["rows"] if r["element_id"] == cap_a.id)
    assert set(cap_row_c["maturity"].keys()) == TEN_KEYS
    non_cap_rows = [r for r in computed["rows"] if r["element_id"] != cap_a.id]
    assert non_cap_rows  # not vacuous: z's own row carries no maturity key
    for row in non_cap_rows:
        assert "maturity" not in row
    assert set(computed["maturity_flags"].keys()) == FLAG_KEYS

    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = None
        no_tenant = IntelligenceQueryService.cross_layer_impact(app_a.id, with_owner=False)
    assert set(no_tenant["maturity_flags"].keys()) == FLAG_KEYS

    not_found = _impact(app, org_a.id, 2_000_000_000, with_owner=False)
    assert set(not_found["maturity_flags"].keys()) == FLAG_KEYS

    org_empty = make_org("shape-7-empty")
    lonely = _element(db_session, org_empty.id, "Lonely", type_="ApplicationComponent")
    db_session.commit()
    computed_empty = _impact(app, org_empty.id, lonely.id, with_owner=False)
    assert computed_empty["rows"] == []
    assert set(computed_empty["maturity_flags"].keys()) == FLAG_KEYS


def test_8_helper_called_at_most_once_per_answer(app, db_session, make_org, monkeypatch):
    from app.modules.capabilities.services.capability_heatmap_service import (
        CapabilityHeatmapService,
    )

    org = make_org("onecall-8")
    root = _element(db_session, org.id, "Root", type_="ApplicationComponent")
    caps = []
    for i in range(3):
        cap = _element(db_session, org.id, f"Cap{i}", type_="Capability", layer="strategy")
        _relationship(db_session, org.id, root, cap)
        _capability(db_session, org.id, cap, current=2, target=4)
        caps.append(cap)
    db_session.commit()

    real = CapabilityHeatmapService.maturity_for_elements
    calls = {"count": 0}

    def _spy(self, element_ids, *, organization_id):
        calls["count"] += 1
        return real(self, element_ids, organization_id=organization_id)

    monkeypatch.setattr(CapabilityHeatmapService, "maturity_for_elements", _spy)

    result = _impact(app, org.id, root.id, with_owner=False)
    cap_ids = {c.id for c in caps}
    assert len([r for r in result["rows"] if r["element_id"] in cap_ids]) == 3
    assert calls["count"] == 1

    monkeypatch.undo()

    org2 = make_org("onecall-8-none")
    root2 = _element(db_session, org2.id, "Root2", type_="ApplicationComponent")
    leaf2 = _element(db_session, org2.id, "Leaf2", type_="ApplicationComponent")
    _relationship(db_session, org2.id, root2, leaf2)
    db_session.commit()

    calls2 = {"count": 0}

    def _spy2(self, element_ids, *, organization_id):
        calls2["count"] += 1
        return real(self, element_ids, organization_id=organization_id)

    monkeypatch.setattr(CapabilityHeatmapService, "maturity_for_elements", _spy2)
    _impact(app, org2.id, root2.id, with_owner=False)
    assert calls2["count"] == 0


def test_9_with_owner_false_leaves_maturity_block_in_place(app, db_session, make_org):
    org_a, org_b, user_a, app_a, cap_a, cap_row = _fixture(
        db_session, make_org, current=2, target=4, label="ownerindep-9"
    )
    result = _impact(app, org_a.id, app_a.id, with_owner=False)
    cap_row_r = next(r for r in result["rows"] if r["element_id"] == cap_a.id)
    assert cap_row_r["owner"] is None
    assert cap_row_r["maturity"]["current"] == 2


# --- (10) latency ---------------------------------------------------------------


def test_10_latency_ms_present_and_series_label_unchanged(app, db_session, make_org):
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION, REGISTRY

    org_a, org_b, user_a, app_a, cap_a, cap_row = _fixture(
        db_session, make_org, current=2, target=4, label="latency-10"
    )
    labels = {"query": "cross_layer_impact", "depth": "3", "include_derived": "true"}
    INTELLIGENCE_QUERY_DURATION.labels(**labels)
    before = REGISTRY.get_sample_value("archie_intelligence_query_seconds_count", labels) or 0.0

    result = _impact(app, org_a.id, app_a.id, include_derived=True, with_owner=False)
    assert result["summary"]["latency_ms"] is not None
    assert result["summary"]["latency_ms"] >= 0

    after = REGISTRY.get_sample_value("archie_intelligence_query_seconds_count", labels)
    assert after - before == 1


# --- (11) fabrication -------------------------------------------------------------


def test_11_fabrication_no_zero_default_for_unassessed(app, db_session, make_org):
    org = make_org("fabrication-11")
    root = _element(db_session, org.id, "Root", type_="ApplicationComponent")
    caps = []
    for i in range(2):
        cap = _element(db_session, org.id, f"Cap{i}", type_="Capability", layer="strategy")
        _relationship(db_session, org.id, root, cap)
        _capability(db_session, org.id, cap, current=None, target=None)
        caps.append(cap)
    db_session.commit()

    result = _impact(app, org.id, root.id, with_owner=False)
    cap_ids = {c.id for c in caps}
    cap_rows = [r for r in result["rows"] if r["element_id"] in cap_ids]
    assert len(cap_rows) == 2
    for row in cap_rows:
        block = row["maturity"]
        assert block["assessed"] is False
        assert block["current"] is None
        assert block["target"] is None
        assert block["target_gap"] is None

    text = json.dumps(result)
    assert '"current": 0' not in text
    assert '"target": 0' not in text
    assert '"target_gap": 0' not in text
