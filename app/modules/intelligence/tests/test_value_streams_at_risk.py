"""T-S1: `IntelligenceQueryService.value_streams_at_risk` and its route.

Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
discovered via app/modules/conftest.py's import of
tests.conftest -- no import needed here, matching test_impact_route.py's
own pattern.

Test-id mapping to the acceptance criteria, current after a correction pass
that fixed a per-mapping-row counting defect, a stage/tenancy join defect,
an untested no-tenant-context branch, a missing explicit predicate on the
not-found resolver, untested value_stream_id narrowing, and an inexact
"byte-identical" claim:

  1  -> test_capability_below_threshold_is_at_risk
  2  -> test_null_maturity_is_neutral_and_counted
  3  -> test_shared_catalogue_capability_contributes_mapping_not_maturity
  4  -> test_tenant_with_no_value_streams_gets_reason_and_empty_rows
  5  -> test_value_stream_with_no_mapping_gets_no_capability_linked
  6  -> test_route_foreign_and_missing_value_stream_id_are_indistinguishable (renamed
        from "...are_byte_identical", which cannot be literally true)
  7  -> test_cross_tenant_value_stream_is_invisible,
        test_cross_tenant_mapping_row_invisible_and_mutation_proof,
        test_stage_belonging_to_another_tenants_value_stream_is_nulled_not_leaked (was
          test_cross_tenant_stage_makes_mapping_unreachable -- the old assertion was
          itself the defect the join shape below now corrects),
        test_cross_tenant_owned_capability_invisible,
        test_foreign_tenants_own_answer_unaffected_by_request
  8  -> test_route_threshold_and_value_stream_id_validation
  9  -> test_latency_series_is_own_and_leaves_nfr5_selector_untouched
  10 -> test_cross_tenant_mapping_row_invisible_and_mutation_proof
  11 -> test_mutation_proof_null_maturity_forced_to_false
  12 -> test_mutation_proof_shared_catalogue_permissive_predicate
  13 -> test_mutation_proof_foreign_vs_missing_message_diverges
  14 -> test_invented_reason_code_is_rejected_not_emitted,
        test_every_reason_string_in_a_realistic_payload_is_a_closed_vocabulary_member
  18 -> test_dependency_object_exact_key_set_and_no_forbidden_keys (now eight keys:
          stage_criticality, assessed_by and assessed_at joined the original five),
        test_dependency_object_reports_assessment_fields_honestly,
        test_route_ignores_graph_only_parameters
  19 -> test_four_batched_selects_regardless_of_row_count

Additional coverage from the correction pass, grouped by scenario:

  distinct-capability counting -> test_capability_on_two_stages_counted_once,
          test_capability_on_two_value_streams_counted_once_in_summary,
          test_demo_data_counts_three_below_threshold
  stage/tenancy join shape -> test_null_owner_stage_is_populated_at_method_level,
          test_stage_belonging_to_another_tenants_value_stream_is_nulled_not_leaked,
          test_stage_of_a_different_own_value_stream_is_nulled,
          test_route_null_owner_stage_is_listed_and_counted
  no-tenant-context branch -> test_route_no_tenant_context_returns_400,
          test_route_anonymous_is_redirected_to_login
  not-found resolver predicate -> test_resolver_carries_explicit_tenant_predicate
  value_stream_id narrowing -> test_value_stream_id_narrows_to_that_stream,
          test_route_value_stream_id_narrows
  dependency object fields -> test_dependency_object_exact_key_set_and_no_forbidden_keys,
          test_dependency_object_reports_assessment_fields_honestly
  statement count by context -> test_four_batched_selects_regardless_of_row_count
          (no request context), test_statement_count_constant_inside_tenant_context
          (inside one)
"""

from __future__ import annotations

import uuid

import pytest


def _org_suffix() -> str:
    return uuid.uuid4().hex[:8]


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"vsr-{uuid.uuid4().hex[:10]}@example.com",
        first_name="VSR",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _value_stream(db_session, org_id, name, code, archimate_element_id=None):
    from app.models.unified_capability import ValueStream

    vs = ValueStream(
        name=name,
        code=code,
        organization_id=org_id,
        archimate_element_id=archimate_element_id,
    )
    db_session.add(vs)
    db_session.flush()
    return vs


def _stage(db_session, org_id, value_stream_id, name, order=1):
    from app.models.unified_capability import ValueStreamStage

    stage = ValueStreamStage(
        name=name,
        value_stream_id=value_stream_id,
        stage_order=order,
        organization_id=org_id,
    )
    db_session.add(stage)
    db_session.flush()
    return stage


def _capability(db_session, org_id, name, code, current=None, target=None):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=name,
        code=code,
        organization_id=org_id,
        scope="tenant" if org_id is not None else "reference",
        level=1,
        current_maturity_level=current,
        target_maturity_level=target,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _mapping(
    db_session,
    org_id,
    capability_id,
    value_stream_id,
    value_stream_stage_id,
    support_type="primary",
    support_level=3,
    impact_level="medium",
):
    from app.models.unified_capability import CapabilityValueStreamMapping

    mapping = CapabilityValueStreamMapping(
        capability_id=capability_id,
        value_stream_id=value_stream_id,
        value_stream_stage_id=value_stream_stage_id,
        organization_id=org_id,
        support_type=support_type,
        support_level=support_level,
        impact_level=impact_level,
    )
    db_session.add(mapping)
    db_session.flush()
    return mapping


def _without_latency(payload):
    out = dict(payload)
    out["summary"] = {k: v for k, v in payload["summary"].items() if k != "latency_ms"}
    return out


def _relax_not_null(db_session, table: str, column: str) -> None:
    """Drop a ``NOT NULL`` constraint for the rest of THIS test's own
    transaction only -- rolled back at teardown along with everything else
    ``db_session`` touches, per the fixture's own rollback contract, since
    PostgreSQL DDL is transactional.

    A fresh ``db.create_all()`` schema always applies the model's current
    ``nullable=False`` (``TenantMixin.organization_id``, `User.organization_id`);
    the "legacy null-owner stage" and "user with no organisation" scenarios
    tested below are both about a REAL, already-deployed database that
    predates that constraint (or an un-hardened migration), which cannot
    otherwise be constructed against a schema built from the current models.
    This recreates exactly that shape for one test, not a schema change that
    survives it.

    Delegates to the production ``relax_not_null`` rather than duplicating
    the ``ALTER TABLE`` statement. ``relax_not_null`` needs an
    actual ``Connection`` (it reads ``.dialect`` to quote identifiers), not
    the ``scoped_session`` itself -- ``db_session.connection()`` returns the
    one bound to this test's own transaction.
    """
    from app.commands.schema_migrations import relax_not_null

    relax_not_null(db_session.connection(), table, column)


# --- Acceptance item 1 ----------------------------------------------------------


def test_capability_below_threshold_is_at_risk(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac1")
    vs = _value_stream(db_session, org.id, "VS AC1", f"VSR-AC1-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage AC1", 1)
    cap = _capability(db_session, org.id, "Cap AC1", f"VSR-AC1-CAP-{_org_suffix()}", current=2, target=4)
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)

    row = result["rows"][0]
    assert row["value_stream"]["id"] == vs.id
    assert row["at_risk_capability_count"] == 1
    cap_row = row["capabilities"][0]
    assert cap_row["at_risk"] is True
    assert cap_row["maturity_source"] == "unified_capabilities"
    assert cap_row["current_maturity"] == 2
    assert result["summary"]["value_streams_at_risk"] == 1
    assert result["summary"]["capabilities_below_threshold"] == 1


# --- Counts are per distinct capability, not per mapping row -------------------


def test_capability_on_two_stages_counted_once(app, db_session, make_org):
    """One capability mapped to two stages of ONE value stream: the row's
    ``at_risk_capability_count`` is 1 (one distinct capability), not 2, even
    though ``capabilities[]`` correctly holds two entries -- one per mapping
    row, DA-S1 rule 5's own requirement that each stage's dependency
    statement travels with its own row."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-d1-twostages")
    vs = _value_stream(db_session, org.id, "VS", f"VSR-D1-TWOSTAGES-{_org_suffix()}")
    stage_a = _stage(db_session, org.id, vs.id, "Stage A", 1)
    stage_b = _stage(db_session, org.id, vs.id, "Stage B", 2)
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-D1-TWOSTAGES-CAP-{_org_suffix()}", current=2, target=4
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage_a.id, support_type="primary")
    _mapping(db_session, org.id, cap.id, vs.id, stage_b.id, support_type="secondary")
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)

    row = result["rows"][0]
    assert row["at_risk_capability_count"] == 1
    assert len(row["capabilities"]) == 2
    ids = {c["id"] for c in row["capabilities"]}
    assert ids == {cap.id}
    for c in row["capabilities"]:
        assert c["at_risk"] is True
        assert c["current_maturity"] == 2
    assert result["summary"]["capabilities_below_threshold"] == 1


def test_capability_on_two_value_streams_counted_once_in_summary(app, db_session, make_org):
    """One capability mapped on two DIFFERENT value streams: each row's own
    ``at_risk_capability_count`` is 1, and the summary's
    ``capabilities_below_threshold`` is also 1 -- the same capability at
    risk on two streams counts once in the whole-tenant total."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-d1-twostreams")
    vs1 = _value_stream(db_session, org.id, "VS1", f"VSR-D1-TWOSTREAMS-1-{_org_suffix()}")
    vs2 = _value_stream(db_session, org.id, "VS2", f"VSR-D1-TWOSTREAMS-2-{_org_suffix()}")
    stage1 = _stage(db_session, org.id, vs1.id, "Stage 1", 1)
    stage2 = _stage(db_session, org.id, vs2.id, "Stage 2", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-D1-TWOSTREAMS-CAP-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, cap.id, vs1.id, stage1.id)
    _mapping(db_session, org.id, cap.id, vs2.id, stage2.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)

    for row in result["rows"]:
        assert row["at_risk_capability_count"] == 1
    assert result["summary"]["capabilities_below_threshold"] == 1
    assert result["summary"]["capabilities_considered"] == 1


def test_demo_data_counts_three_below_threshold(app, db_session, make_org):
    """The headline scenario this counting fix targets: the shipped
    demonstration data has three capabilities below the default threshold of
    3 (Order Capture 2, Delivery Scheduling 1, Knowledge Base 2), not four --
    the old per-mapping-row count double-counted Knowledge Base, which is
    mapped on both stages of the Service stream.
    """
    from app.commands.seed_strategic_demo import seed_strategic_demo
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-d1-demodata")
    org_id = org.id
    db_session.commit()

    seed_strategic_demo(org_id)

    result = IntelligenceQueryService.value_streams_at_risk(org_id)

    assert result["summary"]["capabilities_below_threshold"] == 3
    assert result["summary"]["capabilities_considered"] == 6
    assert (
        result["summary"]["capabilities_below_threshold"]
        + result["summary"]["capabilities_with_no_maturity"]
        <= result["summary"]["capabilities_considered"]
    )

    service_row = next(
        r for r in result["rows"] if r["value_stream"]["code"] == "DEMO-VSR-SERVICE"
    )
    assert service_row["at_risk_capability_count"] == 1
    assert len(service_row["capabilities"]) == 4
    knowledge_base_entries = [
        c for c in service_row["capabilities"] if c["code"] == "DEMO-CAP-KNOWLEDGE-BASE"
    ]
    assert len(knowledge_base_entries) == 2
    assert knowledge_base_entries[0]["at_risk"] == knowledge_base_entries[1]["at_risk"] is True
    assert (
        knowledge_base_entries[0]["current_maturity"]
        == knowledge_base_entries[1]["current_maturity"]
        == 2
    )


# --- Acceptance item 2 ----------------------------------------------------------


def test_null_maturity_is_neutral_and_counted(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac2")
    vs = _value_stream(db_session, org.id, "VS AC2", f"VSR-AC2-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage AC2", 1)
    cap = _capability(
        db_session, org.id, "Cap AC2", f"VSR-AC2-CAP-{_org_suffix()}", current=None, target=None
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)

    cap_row = result["rows"][0]["capabilities"][0]
    assert cap_row["current_maturity"] is None
    assert cap_row["at_risk"] is None
    assert cap_row["reason"] == "no_maturity_recorded"
    assert result["summary"]["capabilities_with_no_maturity"] == 1
    assert result["summary"]["capabilities_below_threshold"] == 0
    assert result["rows"][0]["at_risk_capability_count"] == 0


# --- Acceptance item 3 -----------------------------------------------------------


def test_shared_catalogue_capability_contributes_mapping_not_maturity(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac3")
    vs = _value_stream(db_session, org.id, "VS AC3", f"VSR-AC3-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage AC3", 1)
    # A shared catalogue row -- owned by no organisation -- that DOES carry a
    # maturity value (a pre-cutover legacy shape, § 3.2). This tenant's own
    # mapping names it.
    shared_cap = _capability(
        db_session, None, "Shared Cap AC3", f"VSR-AC3-SHARED-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, shared_cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)

    cap_row = result["rows"][0]["capabilities"][0]
    assert cap_row["id"] == shared_cap.id
    assert cap_row["name"] == "Shared Cap AC3"
    assert cap_row["current_maturity"] is None
    assert cap_row["at_risk"] is None
    assert cap_row["reason"] == "no_maturity_recorded"


# --- Acceptance item 4 ------------------------------------------------------------


def test_tenant_with_no_value_streams_gets_reason_and_empty_rows(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac4")
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    assert result["rows"] == []
    assert result["reasons"] == ["no_value_stream_recorded"]


# --- Acceptance item 5 -----------------------------------------------------------


def test_value_stream_with_no_mapping_gets_no_capability_linked(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac5")
    _value_stream(db_session, org.id, "VS AC5", f"VSR-AC5-{_org_suffix()}")
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    row = result["rows"][0]
    assert row["capabilities"] == []
    assert row["reason"] == "no_capability_linked"


# --- Acceptance item 6 ------------------------------------------------------------


def test_route_foreign_and_missing_value_stream_id_are_indistinguishable(
    app, db_session, make_org, client, login_as
):
    """"Byte-identical" cannot be literally true -- the envelope's
    ``meta.request_id`` and ``meta.timestamp`` differ on every request by
    construction. What this compares, and what "indistinguishable" actually
    requires: the status code and the ``error`` object (``code``,
    ``message``, ``details``) are identical between a foreign id and a
    never-existed id; request id and timestamp are excluded because they
    are expected to differ.
    """
    org_a = make_org("vsr-ac6-a")
    org_b = make_org("vsr-ac6-b")
    user = _user(db_session, org_a.id)
    foreign_vs = _value_stream(db_session, org_b.id, "Foreign VS", f"VSR-AC6-{_org_suffix()}")
    db_session.commit()
    never_existed_id = foreign_vs.id + 5_000_000

    login_as(client, user)
    resp_foreign = client.get(
        f"/api/v1/intelligence/value-streams-at-risk?value_stream_id={foreign_vs.id}"
    )
    login_as(client, user)
    resp_missing = client.get(
        f"/api/v1/intelligence/value-streams-at-risk?value_stream_id={never_existed_id}"
    )

    assert resp_foreign.status_code == 404
    assert resp_missing.status_code == 404
    assert resp_foreign.get_json()["error"] == resp_missing.get_json()["error"]
    assert resp_foreign.get_json()["error"]["code"] == "VALUE_STREAM_NOT_FOUND"


# --- The not-found resolver carries the explicit predicate too -----------------


def test_resolver_carries_explicit_tenant_predicate(app, db_session, make_org, tenant_ctx, monkeypatch):
    """`_value_stream_or_404_response` takes
    `organization_id` and applies `_value_stream_tenant_predicate` on top of
    whatever the ambient tenant-isolation listener already does, so a
    foreign value stream cannot resolve even if the listener's ambient
    organisation (`g.current_org_id`, e.g. set inside `tenant_ctx`) and the
    caller-resolved `organization_id` argument were ever to diverge.

    Reuses the SAME seam as the item-10 proof
    (`IntelligenceQueryService._value_stream_tenant_predicate`), so
    neutering it here demonstrates this resolver carries that predicate
    too, not only the method.
    """
    from app.modules.intelligence.routes.api import _value_stream_or_404_response
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("vsr-d7-a")
    org_b = make_org("vsr-d7-b")
    vs_b = _value_stream(db_session, org_b.id, "VS B", f"VSR-D7-{_org_suffix()}")
    db_session.commit()

    with tenant_ctx(org_b.id):
        # Control: the listener's ambient context is org_b (it alone WOULD
        # find vs_b), but the caller-resolved organisation is org_a -- the
        # explicit predicate must still say not-found.
        value_stream, err = _value_stream_or_404_response(vs_b.id, org_a.id)
        assert value_stream is None
        assert err is not None

        # Mutation: neuter the shared predicate seam -- the select now
        # relies solely on the ambient listener, which matches org_b, so
        # the foreign value stream resolves.
        from sqlalchemy import true as sa_true

        monkeypatch.setattr(
            IntelligenceQueryService,
            "_value_stream_tenant_predicate",
            staticmethod(lambda model, organization_id: sa_true()),
        )
        with pytest.raises(AssertionError):
            mutated_vs, _mutated_err = _value_stream_or_404_response(vs_b.id, org_a.id)
            assert mutated_vs is None

        monkeypatch.undo()
        restored_vs, _restored_err = _value_stream_or_404_response(vs_b.id, org_a.id)
        assert restored_vs is None


# --- Narrowing by value_stream_id ------------------------------------------------


def test_value_stream_id_narrows_to_that_stream(app, db_session, make_org):
    """This is the test that would fail if the ``value_stream_id`` ``where``
    were dropped -- mutation-proved once (comment out the ``where``, confirm
    this goes red, restore, confirm green)."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-d10")
    vs1 = _value_stream(db_session, org.id, "VS1", f"VSR-D10-1-{_org_suffix()}")
    vs2 = _value_stream(db_session, org.id, "VS2", f"VSR-D10-2-{_org_suffix()}")
    stage1 = _stage(db_session, org.id, vs1.id, "Stage 1", 1)
    stage2 = _stage(db_session, org.id, vs2.id, "Stage 2", 1)
    cap1 = _capability(db_session, org.id, "Cap1", f"VSR-D10-CAP1-{_org_suffix()}", current=1, target=3)
    cap2 = _capability(db_session, org.id, "Cap2", f"VSR-D10-CAP2-{_org_suffix()}", current=1, target=3)
    _mapping(db_session, org.id, cap1.id, vs1.id, stage1.id)
    _mapping(db_session, org.id, cap2.id, vs2.id, stage2.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, value_stream_id=vs1.id)

    assert len(result["rows"]) == 1
    assert result["rows"][0]["value_stream"]["id"] == vs1.id
    assert result["summary"]["value_streams_considered"] == 1
    assert result["summary"]["capabilities_considered"] == 1
    ids_in_answer = {c["id"] for row in result["rows"] for c in row["capabilities"]}
    assert cap2.id not in ids_in_answer


def test_route_value_stream_id_narrows(app, db_session, make_org, client, login_as):
    """Route-level variant of the above, through the real client."""
    org = make_org("vsr-d10-route")
    user = _user(db_session, org.id)
    vs1 = _value_stream(db_session, org.id, "VS1", f"VSR-D10ROUTE-1-{_org_suffix()}")
    vs2 = _value_stream(db_session, org.id, "VS2", f"VSR-D10ROUTE-2-{_org_suffix()}")
    stage1 = _stage(db_session, org.id, vs1.id, "Stage 1", 1)
    stage2 = _stage(db_session, org.id, vs2.id, "Stage 2", 1)
    cap1 = _capability(
        db_session, org.id, "Cap1", f"VSR-D10ROUTE-CAP1-{_org_suffix()}", current=1, target=3
    )
    cap2 = _capability(
        db_session, org.id, "Cap2", f"VSR-D10ROUTE-CAP2-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, cap1.id, vs1.id, stage1.id)
    _mapping(db_session, org.id, cap2.id, vs2.id, stage2.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/value-streams-at-risk?value_stream_id={vs1.id}")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert len(data["rows"]) == 1
    assert data["rows"][0]["value_stream"]["id"] == vs1.id
    assert data["summary"]["value_streams_considered"] == 1


# --- Acceptance item 7, table 1: foreign value_streams row ----------------------


def test_cross_tenant_value_stream_is_invisible(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("vsr-ac7-vs-a")
    org_b = make_org("vsr-ac7-vs-b")
    _value_stream(db_session, org_b.id, "VS B", f"VSR-AC7VS-{_org_suffix()}")
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org_a.id)

    assert result["rows"] == []
    assert result["reasons"] == ["no_value_stream_recorded"]


# --- Acceptance items 7 (table 2) and 10: mutation proof -------------------------


def test_cross_tenant_mapping_row_invisible_and_mutation_proof(app, db_session, make_org, monkeypatch):
    from sqlalchemy import true as sa_true

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("vsr-ac7map-a")
    org_b = make_org("vsr-ac7map-b")
    vs_a = _value_stream(db_session, org_a.id, "VS A", f"VSR-AC7MAP-{_org_suffix()}")
    stage_a = _stage(db_session, org_a.id, vs_a.id, "Stage A", 1)
    shared_cap = _capability(
        db_session, None, "Shared Cap", f"VSR-AC7MAP-SHARED-{_org_suffix()}", current=None, target=None
    )
    # A data anomaly: another tenant's OWN mapping row, pointing at org_a's
    # value stream and stage. Must never surface in org_a's answer.
    _mapping(db_session, org_b.id, shared_cap.id, vs_a.id, stage_a.id)
    db_session.commit()

    def _capability_ids(payload, vs_id):
        row = next(r for r in payload["rows"] if r["value_stream"]["id"] == vs_id)
        return {c["id"] for c in row["capabilities"]}

    # Control: the foreign mapping row is invisible.
    control = IntelligenceQueryService.value_streams_at_risk(org_a.id)
    assert shared_cap.id not in _capability_ids(control, vs_a.id)

    # Mutation: neuter the explicit tenant predicate -- the foreign mapping
    # row now passes the .where() clause that used to exclude it.
    monkeypatch.setattr(
        IntelligenceQueryService,
        "_value_stream_tenant_predicate",
        staticmethod(lambda model, organization_id: sa_true()),
    )
    mutated = IntelligenceQueryService.value_streams_at_risk(org_a.id)
    with pytest.raises(AssertionError):
        assert shared_cap.id not in _capability_ids(mutated, vs_a.id)

    # Restore: the leak closes again.
    monkeypatch.undo()
    restored = IntelligenceQueryService.value_streams_at_risk(org_a.id)
    assert shared_cap.id not in _capability_ids(restored, vs_a.id)


# --- Acceptance item 7, table 3: foreign unified_value_stream_stages row --------


def test_stage_belonging_to_another_tenants_value_stream_is_nulled_not_leaked(
    app, db_session, make_org
):
    """A mapping row whose stage does not survive the join is listed and
    counted with ``stage: null`` -- never dropped, and never leaking the
    unrelated stage's own name. This satisfies acceptance item 7's "foreign
    unified_value_stream_stages row invisible" case.

    Superseded by this rewrite: the old version of this test
    (`test_cross_tenant_stage_makes_mapping_unreachable`) asserted the
    mapping vanished from the answer entirely under a strict, inner-joined
    stage predicate -- that behaviour was itself the defect (a recorded
    dependency reported as an absence), so the join shape was corrected;
    this is the corrected scenario and the corrected assertion.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("vsr-ac7stage-a")
    org_b = make_org("vsr-ac7stage-b")
    vs_a = _value_stream(db_session, org_a.id, "VS A", f"VSR-AC7STAGE-{_org_suffix()}")
    # A different tenant's OWN value stream and OWN stage -- not vs_a's.
    vs_b = _value_stream(db_session, org_b.id, "VS B", f"VSR-AC7STAGE-VSB-{_org_suffix()}")
    foreign_stage = _stage(db_session, org_b.id, vs_b.id, "Foreign Stage", 1)
    cap = _capability(
        db_session, org_a.id, "Cap", f"VSR-AC7STAGE-CAP-{_org_suffix()}", current=1, target=3
    )
    # A data anomaly: org_a's own mapping row, on its own value stream,
    # whose value_stream_stage_id names a stage that belongs to org_b's
    # value stream instead of its own.
    _mapping(db_session, org_a.id, cap.id, vs_a.id, foreign_stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org_a.id)

    row = next(r for r in result["rows"] if r["value_stream"]["id"] == vs_a.id)
    assert row["reason"] is None, "a recorded dependency must never read as an absence"
    cap_row = row["capabilities"][0]
    assert cap_row["id"] == cap.id
    assert cap_row["dependency"]["stage"] is None
    assert row["at_risk_capability_count"] == 1

    import json

    serialised = json.dumps(result)
    assert "Foreign Stage" not in serialised


def test_stage_of_a_different_own_value_stream_is_nulled(app, db_session, make_org):
    """A mapping pointing at a stage of a *different own* value stream --
    same tenant, wrong stream -- also fails the join's ``value_stream_id``
    equality and is nulled, exactly like the cross-tenant case. The join
    checks stream membership, not ownership.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac4c")
    vs_a = _value_stream(db_session, org.id, "VS A", f"VSR-AC4C-A-{_org_suffix()}")
    vs_b = _value_stream(db_session, org.id, "VS B", f"VSR-AC4C-B-{_org_suffix()}")
    other_stream_stage = _stage(db_session, org.id, vs_b.id, "Stage of B", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-AC4C-CAP-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, cap.id, vs_a.id, other_stream_stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    row = next(r for r in result["rows"] if r["value_stream"]["id"] == vs_a.id)
    assert row["reason"] is None
    cap_row = row["capabilities"][0]
    assert cap_row["dependency"]["stage"] is None
    assert row["at_risk_capability_count"] == 1


def test_null_owner_stage_is_populated_at_method_level(app, db_session, make_org):
    """A tenant's own mapping on a stage whose
    ``organization_id`` is null (the legacy shape the tenancy backfill
    exists to repair) is listed, counted, and -- called with no ambient
    request context, so no listener adds an organisation criterion to the
    outer join's ``ON`` clause -- its stage is populated, because the join's
    only real fence, the stage's own ``value_stream_id``, is satisfied.
    """
    from app.models.unified_capability import ValueStreamStage
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac4a")
    vs = _value_stream(db_session, org.id, "VS", f"VSR-AC4A-{_org_suffix()}")
    _relax_not_null(db_session, "unified_value_stream_stages", "organization_id")
    null_owner_stage = ValueStreamStage(
        name="Null Owner Stage", value_stream_id=vs.id, stage_order=1, organization_id=None,
    )
    db_session.add(null_owner_stage)
    db_session.flush()
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-AC4A-CAP-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, cap.id, vs.id, null_owner_stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    row = next(r for r in result["rows"] if r["value_stream"]["id"] == vs.id)
    assert row["reason"] is None
    cap_row = row["capabilities"][0]
    assert row["at_risk_capability_count"] == 1
    assert cap_row["dependency"]["stage"] == {
        "id": null_owner_stage.id, "name": "Null Owner Stage",
    }


def test_route_null_owner_stage_is_listed_and_counted(app, db_session, make_org, client, login_as):
    """The route-level variant of the null-owner-stage case above. Inside a
    real request the tenant-isolation listener adds its own organisation
    criterion to the outer join's ``ON`` clause for ``ValueStreamStage`` (a
    ``TenantMixin`` model), so whether the null-owner stage's name survives
    depends on the listener, not on this method -- the capability is listed
    and counted either way, and ``stage`` is a dict or ``None``.
    """
    from app.models.unified_capability import ValueStreamStage

    org = make_org("vsr-ac4d")
    user = _user(db_session, org.id)
    vs = _value_stream(db_session, org.id, "VS", f"VSR-AC4D-{_org_suffix()}")
    _relax_not_null(db_session, "unified_value_stream_stages", "organization_id")
    null_owner_stage = ValueStreamStage(
        name="Route Null Owner Stage", value_stream_id=vs.id, stage_order=1, organization_id=None,
    )
    db_session.add(null_owner_stage)
    db_session.flush()
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-AC4D-CAP-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, cap.id, vs.id, null_owner_stage.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/value-streams-at-risk")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    row = next(r for r in data["rows"] if r["value_stream"]["id"] == vs.id)
    assert row["reason"] is None
    cap_row = row["capabilities"][0]
    assert row["at_risk_capability_count"] == 1
    assert cap_row["dependency"]["stage"] is None or isinstance(cap_row["dependency"]["stage"], dict)


# --- Acceptance item 7, table 4: foreign tenant-owned unified_capabilities ------


def test_cross_tenant_owned_capability_invisible(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("vsr-ac7cap-a")
    org_b = make_org("vsr-ac7cap-b")
    vs_a = _value_stream(db_session, org_a.id, "VS A", f"VSR-AC7CAP-{_org_suffix()}")
    stage_a = _stage(db_session, org_a.id, vs_a.id, "Stage A", 1)
    # A data anomaly: org_a's own mapping row names a capability owned by a
    # DIFFERENT tenant (not shared -- org_b's own).
    foreign_cap = _capability(
        db_session, org_b.id, "Foreign Cap", f"VSR-AC7CAP-FOREIGN-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org_a.id, foreign_cap.id, vs_a.id, stage_a.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org_a.id)

    row = next(r for r in result["rows"] if r["value_stream"]["id"] == vs_a.id)
    assert row["capabilities"] == []
    assert row["reason"] == "no_capability_linked"


# --- Acceptance item 7, last clause: the foreign tenant's own answer -----------


def test_foreign_tenants_own_answer_unaffected_by_request(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("vsr-ac7unaff-a")
    org_b = make_org("vsr-ac7unaff-b")
    vs_b = _value_stream(db_session, org_b.id, "VS B", f"VSR-AC7UNAFF-{_org_suffix()}")
    stage_b = _stage(db_session, org_b.id, vs_b.id, "Stage B", 1)
    cap_b = _capability(
        db_session, org_b.id, "Cap B", f"VSR-AC7UNAFF-CAP-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org_b.id, cap_b.id, vs_b.id, stage_b.id)
    db_session.commit()

    before = IntelligenceQueryService.value_streams_at_risk(org_b.id)

    # org_a's own (empty) request must not perturb org_b's answer.
    IntelligenceQueryService.value_streams_at_risk(org_a.id)

    after = IntelligenceQueryService.value_streams_at_risk(org_b.id)
    assert _without_latency(after) == _without_latency(before)


# --- Acceptance item 8 -----------------------------------------------------------


def test_route_threshold_and_value_stream_id_validation(app, db_session, make_org, client, login_as):
    org = make_org("vsr-ac8")
    user = _user(db_session, org.id)
    db_session.commit()

    for bad_threshold in ("0", "6", "abc"):
        login_as(client, user)
        resp = client.get(f"/api/v1/intelligence/value-streams-at-risk?threshold={bad_threshold}")
        assert resp.status_code == 400, bad_threshold
        assert resp.get_json()["error"]["code"] == "INVALID_PARAMETER"

    for bad_vs_id in ("0", "-1", "abc"):
        login_as(client, user)
        resp = client.get(f"/api/v1/intelligence/value-streams-at-risk?value_stream_id={bad_vs_id}")
        assert resp.status_code == 400, bad_vs_id
        assert resp.get_json()["error"]["code"] == "INVALID_PARAMETER"


def test_route_no_tenant_context_returns_400(app, db_session, make_org, client, login_as):
    """A LOGGED-IN user whose organisation is ``None`` gets
    the real 400 ``NO_TENANT_CONTEXT`` branch, distinct from the anonymous
    ``@login_required`` redirect this test used to conflate with it (see
    ``test_route_anonymous_is_redirected_to_login`` for that case).

    ``User.organization_id`` is ``NOT NULL`` at the schema level and
    ``_assign_default_organization``'s ``before_insert`` listener fills in a
    fallback whenever it is left unset, so a persisted user can never
    genuinely hold a null organisation through the ORM as-is. ``_relax_not_null``
    reproduces the real, already-deployed shape this scenario needs (see its
    own docstring) for the rest of this test's transaction only, so the
    organisation-less user is genuinely persisted -- flask-login's loader then
    reads it back exactly like a real request would, no identity-map trick or
    in-memory-only mutation required.
    """
    from app.modules.intelligence.routes.api import _NO_TENANT_CONTEXT_REASON

    org = make_org("vsr-ac6-notenant")
    user = _user(db_session, org.id)
    db_session.flush()

    login_as(client, user)

    _relax_not_null(db_session, "users", "organization_id")
    user.organization_id = None
    db_session.flush()

    resp = client.get("/api/v1/intelligence/value-streams-at-risk")

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"] == "NO_TENANT_CONTEXT"
    assert body["error"]["details"]["reason"] == _NO_TENANT_CONTEXT_REASON


def test_route_anonymous_is_redirected_to_login(app):
    """The anonymous case, kept as its own test asserting only what it
    tests: ``@login_required`` stops the request before it ever reaches the
    tenant-context check, so this is a login redirect (or a 401), never the
    400 ``NO_TENANT_CONTEXT`` body -- see
    ``test_route_no_tenant_context_returns_400`` for that branch."""
    client = app.test_client()
    resp = client.get("/api/v1/intelligence/value-streams-at-risk")
    assert resp.status_code in (302, 401)


# --- Acceptance item 9 ------------------------------------------------------------


def _sample_count(histogram, **labels):
    """Read a histogram series' total sample count via the public
    ``collect()`` API, the same approach ``latency_probe.read_p95_bucket_edge``
    uses -- not a private attribute, which does not exist on this
    ``prometheus_client`` version's ``Histogram`` child object."""
    family = histogram.collect()[0]
    for sample in family.samples:
        if sample.name.endswith("_count") and all(
            sample.labels.get(k) == v for k, v in labels.items()
        ):
            return sample.value
    return 0.0


def test_latency_series_is_own_and_leaves_nfr5_selector_untouched(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

    org = make_org("vsr-ac9")
    db_session.commit()

    nfr5_before = _sample_count(
        INTELLIGENCE_QUERY_DURATION, query="cross_layer_impact", depth="4", include_derived="true"
    )
    own_before = _sample_count(
        INTELLIGENCE_QUERY_DURATION,
        query="value_streams_at_risk", depth="unknown", include_derived="false",
    )

    result = IntelligenceQueryService.value_streams_at_risk(org.id)
    assert result["summary"]["latency_ms"] is not None
    assert result["summary"]["latency_ms"] >= 0

    nfr5_after = _sample_count(
        INTELLIGENCE_QUERY_DURATION, query="cross_layer_impact", depth="4", include_derived="true"
    )
    own_after = _sample_count(
        INTELLIGENCE_QUERY_DURATION,
        query="value_streams_at_risk", depth="unknown", include_derived="false",
    )

    assert nfr5_after == nfr5_before
    assert own_after == own_before + 1


# --- Acceptance item 11: mutation proof -------------------------------------------


def test_mutation_proof_null_maturity_forced_to_false(app, db_session, make_org, monkeypatch):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac11")
    vs = _value_stream(db_session, org.id, "VS", f"VSR-AC11-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-AC11-CAP-{_org_suffix()}", current=None, target=None
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    # Control.
    control = IntelligenceQueryService.value_streams_at_risk(org.id)
    assert control["rows"][0]["capabilities"][0]["at_risk"] is None

    # Mutation: the null-maturity branch now claims a scored "false" instead
    # of the neutral null.
    monkeypatch.setattr(
        IntelligenceQueryService,
        "_at_risk_for_maturity",
        staticmethod(
            lambda current_maturity, threshold: (
                False if current_maturity is None else current_maturity < threshold
            )
        ),
    )
    mutated = IntelligenceQueryService.value_streams_at_risk(org.id)
    with pytest.raises(AssertionError):
        assert mutated["rows"][0]["capabilities"][0]["at_risk"] is None

    # Restore.
    monkeypatch.undo()
    restored = IntelligenceQueryService.value_streams_at_risk(org.id)
    assert restored["rows"][0]["capabilities"][0]["at_risk"] is None


# --- Acceptance item 12: mutation proof -------------------------------------------


def test_mutation_proof_shared_catalogue_permissive_predicate(app, db_session, make_org, monkeypatch):
    from app.models.unified_capability import UnifiedCapability
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac12")
    vs = _value_stream(db_session, org.id, "VS", f"VSR-AC12-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    # A shared/reference catalogue row carrying a legacy maturity value.
    shared_cap = _capability(
        db_session, None, "Shared Cap", f"VSR-AC12-SHARED-{_org_suffix()}", current=1, target=3
    )
    _mapping(db_session, org.id, shared_cap.id, vs.id, stage.id)
    db_session.commit()

    # Control.
    control = IntelligenceQueryService.value_streams_at_risk(org.id)
    assert control["rows"][0]["capabilities"][0]["current_maturity"] is None

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

    # Mutation: relax the accessor to the permissive predicate the two
    # source-provenance members use.
    monkeypatch.setattr(
        UnifiedCapability, "maturity_for_capability_ids", _permissive_maturity_for_capability_ids
    )
    mutated = IntelligenceQueryService.value_streams_at_risk(org.id)
    with pytest.raises(AssertionError):
        assert mutated["rows"][0]["capabilities"][0]["current_maturity"] is None

    # Restore.
    monkeypatch.undo()
    restored = IntelligenceQueryService.value_streams_at_risk(org.id)
    assert restored["rows"][0]["capabilities"][0]["current_maturity"] is None


# --- Acceptance item 13: mutation proof -------------------------------------------


def test_mutation_proof_foreign_vs_missing_message_diverges(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app.modules.intelligence.routes import api as api_module

    org_a = make_org("vsr-ac13-a")
    org_b = make_org("vsr-ac13-b")
    user = _user(db_session, org_a.id)
    foreign_vs = _value_stream(db_session, org_b.id, "Foreign VS", f"VSR-AC13-{_org_suffix()}")
    db_session.commit()
    never_existed_id = foreign_vs.id + 5_000_000

    def _get_error(value_stream_id):
        login_as(client, user)
        resp = client.get(
            f"/api/v1/intelligence/value-streams-at-risk?value_stream_id={value_stream_id}"
        )
        return resp.get_json()["error"]

    # Control: identical.
    assert _get_error(foreign_vs.id) == _get_error(never_existed_id)

    def _diverging(value_stream_id, organization_id):
        from app.extensions import db
        from app.models.unified_capability import ValueStream
        from app.utils.api_response import error_response

        # Raw SQL, deliberately: an ORM `db.select(ValueStream...)` still
        # goes through the same tenant `do_orm_execute` with_loader_criteria
        # as the scoped query below, so it could never actually diverge --
        # only a non-ORM statement can see "exists for another tenant".
        # tenancy-ok: deliberately unscoped -- this is the "ignore tenant"
        # half of a mutation-proof test helper, and must NOT filter by
        # organization_id, or it could never diverge from the real, scoped
        # resolver it exists to test the divergence against. Read-only,
        # test-only, never a production code path.
        raw_exists = db.session.execute(
            db.text("SELECT 1 FROM value_streams WHERE id = :id"),
            {"id": value_stream_id},
        ).first()
        scoped = ValueStream.query.filter_by(id=value_stream_id).first()
        if scoped is not None:
            return scoped, None
        if raw_exists is not None:
            return None, error_response(
                "Value stream belongs to another organisation",
                code="VALUE_STREAM_NOT_FOUND",
                status_code=404,
            )
        return None, error_response(
            "Value stream not found", code="VALUE_STREAM_NOT_FOUND", status_code=404
        )

    monkeypatch.setattr(api_module, "_value_stream_or_404_response", _diverging)
    with pytest.raises(AssertionError):
        assert _get_error(foreign_vs.id) == _get_error(never_existed_id)

    monkeypatch.undo()
    assert _get_error(foreign_vs.id) == _get_error(never_existed_id)


# --- Acceptance item 14 -----------------------------------------------------------


def test_invented_reason_code_is_rejected_not_emitted():
    from app.modules.intelligence.services.reason_codes import (
        UnknownReasonCodeError,
        validate_reason_code,
    )

    with pytest.raises(UnknownReasonCodeError):
        validate_reason_code("value_streams_at_risk_made_up_reason")


def test_every_reason_string_in_a_realistic_payload_is_a_closed_vocabulary_member(
    app, db_session, make_org
):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("vsr-ac14")
    vs_with_gap = _value_stream(db_session, org.id, "VS Gap", f"VSR-AC14-GAP-{_org_suffix()}")
    _value_stream(db_session, org.id, "VS Empty", f"VSR-AC14-EMPTY-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs_with_gap.id, "Stage", 1)
    cap_null = _capability(
        db_session, org.id, "Null Cap", f"VSR-AC14-CAP-{_org_suffix()}", current=None, target=None
    )
    _mapping(db_session, org.id, cap_null.id, vs_with_gap.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    for reason in result["reasons"]:
        assert reason in REASON_CODES
    for row in result["rows"]:
        if row["reason"] is not None:
            assert row["reason"] in REASON_CODES
        for cap in row["capabilities"]:
            if cap["reason"] is not None:
                assert cap["reason"] in REASON_CODES


# --- Acceptance item 18 -----------------------------------------------------------


def test_dependency_object_exact_key_set_and_no_forbidden_keys(app, db_session, make_org):
    """The dependency object carries eight keys, not five --
    ``stage_criticality`` (previously dropped without a recorded reason) plus
    ``assessed_by`` / ``assessed_at`` (so a later reader can tell a column
    default from a real assessment without re-deriving it). Still none of
    the graph-path keys.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac18")
    vs = _value_stream(db_session, org.id, "VS", f"VSR-AC18-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(db_session, org.id, "Cap", f"VSR-AC18-CAP-{_org_suffix()}", current=2, target=4)
    _mapping(
        db_session, org.id, cap.id, vs.id, stage.id,
        support_type="primary", support_level=4, impact_level="high",
    )
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    row = result["rows"][0]
    assert "initiatives" not in row
    assert "value_stream_initiatives" not in row
    assert "initiatives" not in result

    dependency = row["capabilities"][0]["dependency"]
    assert set(dependency.keys()) == {
        "link_kind", "support_type", "support_level", "impact_level",
        "stage_criticality", "assessed_by", "assessed_at", "stage",
    }
    forbidden = {
        "relation_type", "depth", "chain", "chain_elements",
        "rule_id", "derived_id", "computed_at", "stale",
    }
    assert forbidden.isdisjoint(dependency.keys())
    assert dependency["link_kind"] == "curated"
    assert dependency["stage"] == {"id": stage.id, "name": stage.name}


def test_dependency_object_reports_assessment_fields_honestly(app, db_session, make_org):
    """`assessed_by` / `assessed_at` are whatever the
    mapping row actually carries -- `mapping.assessor` and
    `mapping.last_assessed` serialised as ISO-8601 -- never fabricated.

    `assessor` carries no column default, so a mapping row nobody assessed
    reports `assessed_by: null` -- genuinely absent, not a placeholder.
    `last_assessed` DOES carry a column default (`default=datetime.utcnow`),
    so it is never null on an inserted row regardless of assessment intent;
    this test proves the serialised value is the row's REAL stored value
    (an explicit later assessment date), not a re-derived or rounded one.
    """
    from datetime import datetime

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac14-assessed")
    vs = _value_stream(db_session, org.id, "VS", f"VSR-AC14ASSESSED-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"VSR-AC14ASSESSED-CAP-{_org_suffix()}", current=2, target=4
    )
    mapping = _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    assessed_at = datetime(2026, 1, 15, 12, 30, 0)
    mapping.assessor = "Jane Assessor"
    mapping.last_assessed = assessed_at
    db_session.flush()
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)
    dependency = result["rows"][0]["capabilities"][0]["dependency"]
    assert dependency["assessed_by"] == "Jane Assessor"
    assert dependency["assessed_at"] == assessed_at.isoformat()

    # A second mapping row whose assessor was never set reports assessed_by
    # null -- assessed_at still carries the column's own default timestamp,
    # since that field (unlike assessor) is never genuinely unset.
    stage2 = _stage(db_session, org.id, vs.id, "Stage 2", 2)
    cap2 = _capability(
        db_session, org.id, "Cap 2", f"VSR-AC14ASSESSED-CAP2-{_org_suffix()}", current=2, target=4
    )
    mapping2 = _mapping(db_session, org.id, cap2.id, vs.id, stage2.id)
    db_session.commit()

    result2 = IntelligenceQueryService.value_streams_at_risk(org.id)
    dep2 = next(
        c["dependency"]
        for c in result2["rows"][0]["capabilities"]
        if c["id"] == cap2.id
    )
    assert dep2["assessed_by"] is None
    assert dep2["assessed_at"] == mapping2.last_assessed.isoformat()


def test_route_ignores_graph_only_parameters(app, db_session, make_org, client, login_as):
    org = make_org("vsr-ac18-route")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    resp = client.get(
        "/api/v1/intelligence/value-streams-at-risk"
        "?include_derived=true&include_stale=true&max_depth=3"
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "initiatives" not in data
    for row in data["rows"]:
        assert "initiatives" not in row


# --- Acceptance item 19 ------------------------------------------------------------


class _SelectStatementCounter:
    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            self.statements.append(statement)


@pytest.fixture
def select_counter(app):
    from sqlalchemy import event

    from app.extensions import db

    counter = _SelectStatementCounter()
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        yield counter
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)


def test_four_batched_selects_regardless_of_row_count(app, db_session, make_org, select_counter):
    """This measures the method called with NO request
    context (no ``g.current_org_id``, no ``tenant_ctx``) -- the shape a CLI
    command or a job sees, where the tenant-isolation listener's own
    ``set_config`` statement never fires. See
    ``test_statement_count_constant_inside_tenant_context`` for the same
    growth assertion measured INSIDE a request context, where that extra
    statement per execute is present and the count is correspondingly
    higher but still constant under row growth.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac19")
    org_id = org.id  # captured before any commit -- see note below
    vs1 = _value_stream(db_session, org.id, "VS Small", f"VSR-AC19-SMALL-{_org_suffix()}")
    stage1 = _stage(db_session, org.id, vs1.id, "Stage 1", 1)
    small_caps = [
        _capability(db_session, org.id, f"Cap {i}", f"VSR-AC19-CAP-{i}-{_org_suffix()}", current=2, target=4)
        for i in range(6)
    ]
    for cap in small_caps:
        _mapping(db_session, org.id, cap.id, vs1.id, stage1.id)
    db_session.commit()

    # `commit()` expires every ORM attribute by default (including `org.id`),
    # so touching `org.id` again here would itself lazy-reload the whole row
    # and add a spurious 5th SELECT that has nothing to do with the method
    # under test -- `org_id`, a plain int captured above, avoids that.
    select_counter.statements.clear()
    IntelligenceQueryService.value_streams_at_risk(org_id)
    small_count = len(select_counter.statements)

    # Grow: a third value stream and four more mappings.
    vs2 = _value_stream(db_session, org_id, "VS Big", f"VSR-AC19-BIG-{_org_suffix()}")
    stage2 = _stage(db_session, org_id, vs2.id, "Stage 2", 1)
    more_caps = [
        _capability(
            db_session, org_id, f"Cap Extra {i}", f"VSR-AC19-EXTRA-{i}-{_org_suffix()}",
            current=1, target=5,
        )
        for i in range(4)
    ]
    for cap in more_caps:
        _mapping(db_session, org_id, cap.id, vs2.id, stage2.id)
    db_session.commit()

    select_counter.statements.clear()
    IntelligenceQueryService.value_streams_at_risk(org_id)
    big_count = len(select_counter.statements)

    assert small_count == 5, select_counter.statements
    assert big_count == small_count, select_counter.statements


def test_statement_count_constant_inside_tenant_context(
    app, db_session, make_org, select_counter, tenant_ctx
):
    """The same growth assertion, measured INSIDE a real
    request-like tenant context. The tenant-isolation listener issues its
    own ``SELECT set_config(...)`` once per ORM execute while
    ``g.current_org_id`` is set, so the count observed here is higher than
    the no-context measurement above -- but it is still constant under row
    growth, which is the property this test actually proves.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("vsr-ac19-ctx")
    org_id = org.id
    vs1 = _value_stream(db_session, org_id, "VS Small", f"VSR-AC19CTX-SMALL-{_org_suffix()}")
    stage1 = _stage(db_session, org_id, vs1.id, "Stage 1", 1)
    small_caps = [
        _capability(
            db_session, org_id, f"Cap {i}", f"VSR-AC19CTX-CAP-{i}-{_org_suffix()}",
            current=2, target=4,
        )
        for i in range(6)
    ]
    for cap in small_caps:
        _mapping(db_session, org_id, cap.id, vs1.id, stage1.id)
    db_session.commit()

    with tenant_ctx(org_id):
        select_counter.statements.clear()
        IntelligenceQueryService.value_streams_at_risk(org_id)
        small_count_in_context = len(select_counter.statements)

    vs2 = _value_stream(db_session, org_id, "VS Big", f"VSR-AC19CTX-BIG-{_org_suffix()}")
    stage2 = _stage(db_session, org_id, vs2.id, "Stage 2", 1)
    more_caps = [
        _capability(
            db_session, org_id, f"Cap Extra {i}", f"VSR-AC19CTX-EXTRA-{i}-{_org_suffix()}",
            current=1, target=5,
        )
        for i in range(4)
    ]
    for cap in more_caps:
        _mapping(db_session, org_id, cap.id, vs2.id, stage2.id)
    db_session.commit()

    with tenant_ctx(org_id):
        select_counter.statements.clear()
        IntelligenceQueryService.value_streams_at_risk(org_id)
        big_count_in_context = len(select_counter.statements)

    assert big_count_in_context == small_count_in_context, select_counter.statements


# --- T-MAT-3: under_target, target_gap and risk_reasons ---------------------------------------


def test_capability_under_target_is_named_not_scored(app, db_session, make_org):
    """Test (4): threshold 3, capability at 3 → 5:
    at_risk is False, under_target is True, target_gap == 2,
    stream's risk_reasons == ["capability_under_target"],
    at_risk_capability_count == 0, summary.value_streams_at_risk == 0.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac4")
    vs = _value_stream(db_session, org.id, "VS", f"MAT3-AC4-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC4-CAP-{_org_suffix()}", current=3, target=5
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)
    row = result["rows"][0]
    cap_row = row["capabilities"][0]

    assert cap_row["at_risk"] is False
    assert cap_row["under_target"] is True
    assert cap_row["target_gap"] == 2
    assert row["risk_reasons"] == ["capability_under_target"]
    assert row["at_risk_capability_count"] == 0
    assert result["summary"]["value_streams_at_risk"] == 0


def test_capability_below_threshold_carries_both_reasons_when_also_under_target(
    app, db_session, make_org
):
    """Test (5): capability 2 → 4, threshold 3 →
    risk_reasons == ["capability_below_threshold", "capability_under_target"],
    counts as T-S1 (1 at risk).
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac5")
    vs = _value_stream(db_session, org.id, "VS", f"MAT3-AC5-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC5-CAP-{_org_suffix()}", current=2, target=4
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id, threshold=3)
    row = result["rows"][0]

    assert row["risk_reasons"] == ["capability_below_threshold", "capability_under_target"]
    assert row["at_risk_capability_count"] == 1
    assert result["summary"]["value_streams_at_risk"] == 1


def test_unassessed_capability_is_named_and_not_counted(app, db_session, make_org):
    """Test (6): current null →
    risk_reasons == ["capability_unassessed"],
    under_target is None, target_gap is None,
    capabilities_with_no_maturity == 1, at_risk_capability_count == 0.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac6")
    vs = _value_stream(db_session, org.id, "VS", f"MAT3-AC6-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC6-CAP-{_org_suffix()}", current=None, target=None
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)
    row = result["rows"][0]
    cap_row = row["capabilities"][0]

    assert row["risk_reasons"] == ["capability_unassessed"]
    assert cap_row["under_target"] is None
    assert cap_row["target_gap"] is None
    assert result["summary"]["capabilities_with_no_maturity"] == 1
    assert row["at_risk_capability_count"] == 0


def test_assessed_without_target_carries_no_target_reason(app, db_session, make_org):
    """Test (7): current 3, target null →
    the row's under_target is None, target_gap is None, reason is None at row level
    (the row is assessed; the block's no_maturity_target_recorded is not a row reason) —
    assert risk_reasons does not name it and no count moves.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac7")
    vs = _value_stream(db_session, org.id, "VS", f"MAT3-AC7-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC7-CAP-{_org_suffix()}", current=3, target=None
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)
    row = result["rows"][0]
    cap_row = row["capabilities"][0]

    assert cap_row["under_target"] is None
    assert cap_row["target_gap"] is None
    assert cap_row["reason"] is None
    assert "capability_under_target" not in row["risk_reasons"]
    assert row["at_risk_capability_count"] == 0
    assert result["summary"]["capabilities_below_threshold"] == 0
    assert result["summary"]["capabilities_with_no_maturity"] == 0


def test_every_risk_reason_string_is_an_enum_member(app, db_session, make_org):
    """Test (8): walks a realistic payload against VALUE_STREAM_RISK_REASONS.
    An invented string is proven outside the frozen set by construction.
    """
    from app.modules.intelligence.services.query_service import (
        VALUE_STREAM_RISK_REASONS,
        IntelligenceQueryService,
    )

    org = make_org("mat3-ac8")
    vs = _value_stream(db_session, org.id, "VS", f"MAT3-AC8-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC8-CAP-{_org_suffix()}", current=2, target=4
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)

    for row in result["rows"]:
        for reason in row["risk_reasons"]:
            assert reason in VALUE_STREAM_RISK_REASONS, (
                f"risk_reason {reason!r} not in {VALUE_STREAM_RISK_REASONS}"
            )

    # An invented string is not a member by construction.
    assert "invented_reason" not in VALUE_STREAM_RISK_REASONS


def test_fabrication_none_not_zero(app, db_session, make_org):
    """Test (10): for every capability row with current_maturity is None,
    target_maturity is None and target_gap is None (asserted is None),
    and the JSON of an all-unassessed payload contains none of
    "current_maturity": 0, "target_maturity": 0, "target_gap": 0.
    """
    import json

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac10")
    vs = _value_stream(db_session, org.id, "VS", f"MAT3-AC10-{_org_suffix()}")
    stage = _stage(db_session, org.id, vs.id, "Stage", 1)
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC10-CAP-{_org_suffix()}", current=None, target=None
    )
    _mapping(db_session, org.id, cap.id, vs.id, stage.id)
    db_session.commit()

    result = IntelligenceQueryService.value_streams_at_risk(org.id)
    row = result["rows"][0]

    for cap_row in row["capabilities"]:
        if cap_row["current_maturity"] is None:
            assert cap_row["target_maturity"] is None
            assert cap_row["target_gap"] is None

    json_str = json.dumps(result)
    assert '"current_maturity": 0' not in json_str
    assert '"target_maturity": 0' not in json_str
    assert '"target_gap": 0' not in json_str


def test_five_batched_selects_regardless_of_row_count(app, db_session, make_org, select_counter):
    """T-MAT-3: the helper adds a fifth select (its own element map)."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac19")
    org_id = org.id
    vs1 = _value_stream(db_session, org.id, "VS Small", f"MAT3-AC19-SMALL-{_org_suffix()}")
    stage1 = _stage(db_session, org.id, vs1.id, "Stage 1", 1)
    small_caps = [
        _capability(
            db_session, org.id, f"Cap {i}", f"MAT3-AC19-CAP-{i}-{_org_suffix()}",
            current=2, target=4,
        )
        for i in range(6)
    ]
    for cap in small_caps:
        _mapping(db_session, org.id, cap.id, vs1.id, stage1.id)
    db_session.commit()

    select_counter.statements.clear()
    IntelligenceQueryService.value_streams_at_risk(org_id)
    small_count = len(select_counter.statements)

    vs2 = _value_stream(db_session, org_id, "VS Big", f"MAT3-AC19-BIG-{_org_suffix()}")
    stage2 = _stage(db_session, org_id, vs2.id, "Stage 2", 1)
    more_caps = [
        _capability(
            db_session, org_id, f"Cap Extra {i}", f"MAT3-AC19-EXTRA-{i}-{_org_suffix()}",
            current=1, target=5,
        )
        for i in range(4)
    ]
    for cap in more_caps:
        _mapping(db_session, org_id, cap.id, vs2.id, stage2.id)
    db_session.commit()

    select_counter.statements.clear()
    IntelligenceQueryService.value_streams_at_risk(org_id)
    big_count = len(select_counter.statements)

    assert small_count == 5, select_counter.statements
    assert big_count == small_count, select_counter.statements


def test_statement_count_constant_five_inside_tenant_context(
    app, db_session, make_org, select_counter, tenant_ctx
):
    """T-MAT-3: same constant-five assertion inside a request-like context."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac19-ctx")
    org_id = org.id
    vs1 = _value_stream(db_session, org_id, "VS Small", f"MAT3-AC19CTX-SMALL-{_org_suffix()}")
    stage1 = _stage(db_session, org_id, vs1.id, "Stage 1", 1)
    small_caps = [
        _capability(
            db_session, org_id, f"Cap {i}", f"MAT3-AC19CTX-CAP-{i}-{_org_suffix()}",
            current=2, target=4,
        )
        for i in range(6)
    ]
    for cap in small_caps:
        _mapping(db_session, org_id, cap.id, vs1.id, stage1.id)
    db_session.commit()

    with tenant_ctx(org_id):
        select_counter.statements.clear()
        IntelligenceQueryService.value_streams_at_risk(org_id)
        small_count_in_context = len(select_counter.statements)

    vs2 = _value_stream(db_session, org_id, "VS Big", f"MAT3-AC19CTX-BIG-{_org_suffix()}")
    stage2 = _stage(db_session, org_id, vs2.id, "Stage 2", 1)
    more_caps = [
        _capability(
            db_session, org_id, f"Cap Extra {i}", f"MAT3-AC19CTX-EXTRA-{i}-{_org_suffix()}",
            current=1, target=5,
        )
        for i in range(4)
    ]
    for cap in more_caps:
        _mapping(db_session, org_id, cap.id, vs2.id, stage2.id)
    db_session.commit()

    with tenant_ctx(org_id):
        select_counter.statements.clear()
        IntelligenceQueryService.value_streams_at_risk(org_id)
        big_count_in_context = len(select_counter.statements)

    assert big_count_in_context == small_count_in_context, select_counter.statements
