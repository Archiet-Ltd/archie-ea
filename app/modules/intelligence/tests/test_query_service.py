"""Tests for ``IntelligenceQueryService.cross_layer_impact``: derived vs
explicit rows, owner attachment and tenant scoping, staleness, latency
recording, and mutation-proof coverage for the tenant check."""

from __future__ import annotations

import uuid

import pytest

# Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
# discovered via app/modules/conftest.py's import of
# tests.conftest -- pytest resolves fixtures by name without this module
# importing them itself (see test_derivation_runner.py for the same
# pattern). No import needed here.


def _element(db_session, org_id, name, layer="application", type_="ApplicationComponent"):
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


def _derived(db_session, org_id, source, target, *, rule_id="R1", depth=2, chain=None, stale=False):
    import datetime as _dt

    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    row = DerivedRelationship(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Serving",
        rule_id=rule_id,
        chain=chain if chain is not None else list(range(1, depth + 1)),
        chain_element_ids=[source.id, target.id],
        depth=depth,
        confidence=1.0,
        provenance="derivation",
        engine_version="v1",
        computed_at=_dt.datetime.utcnow(),
        stale=stale,
        stale_since=_dt.datetime.utcnow() if stale else None,
        stale_reason="element_deleted" if stale else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _application_component(db_session, org_id, element_id, name="App"):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element_id)
    db_session.add(comp)
    db_session.flush()
    return comp


def _ownership(db_session, org_id, component_id, unit_id, ownership_type="Business Owner", end_date=None):
    from app.models.enterprise_intelligence import ApplicationOwnership

    row = ApplicationOwnership(
        organization_id=org_id,
        application_id=component_id,
        organization_unit_id=unit_id,
        ownership_type=ownership_type,
        end_date=end_date,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _org_unit(db_session, org_id, name="Finance"):
    from app.models.enterprise_intelligence import OrganizationUnit

    unit = OrganizationUnit(organization_id=org_id, name=f"{name} {uuid.uuid4().hex[:6]}")
    db_session.add(unit)
    db_session.flush()
    return unit


# --- Acceptance criteria 1-3: combined payload / explicit-only / provenance -


def test_include_derived_true_returns_explicit_and_derived_in_one_payload(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-combined")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _relationship(db_session, org.id, a, b)
    _derived(db_session, org.id, a, c, rule_id="RULE-1", depth=2, chain=[1, 2])
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, max_depth=3, with_owner=False
        )

    kinds = {r["relation"]["kind"] for r in result["rows"]}
    assert kinds == {"explicit", "derived"}


def test_include_derived_false_returns_only_explicit_rows(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-explicit-only")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _relationship(db_session, org.id, a, b)
    _derived(db_session, org.id, a, c, rule_id="RULE-1")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, max_depth=3, with_owner=False
        )

    assert result["rows"]
    assert all(r["relation"]["kind"] == "explicit" for r in result["rows"])


def test_derived_row_carries_chain_and_rule_id(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-provenance")
    a = _element(db_session, org.id, "A")
    c = _element(db_session, org.id, "C")
    _derived(db_session, org.id, a, c, rule_id="RULE-7", depth=2, chain=[11, 12])
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, max_depth=3, with_owner=False
        )

    derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
    assert derived
    for row in derived:
        assert row["relation"]["chain"]
        assert row["relation"]["rule_id"]


# --- Acceptance criteria 4-5: owner attach / owner honesty / no cross-tenant leak


def test_owner_attaches_when_chain_resolves_and_tenant_matches(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-owner-ok")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    comp = _application_component(db_session, org.id, b.id, name="Owned App")
    unit = _org_unit(db_session, org.id, "Ops")
    _ownership(db_session, org.id, comp.id, unit.id, ownership_type="Business Owner")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=False, with_owner=True)

    row = result["rows"][0]
    assert row["owner"] is not None
    assert row["owner"]["organization_unit_id"] == unit.id
    assert row["owner"]["ownership_type"] == "Business Owner"
    assert row["reason"] is None


def test_owner_absent_is_indistinguishable_and_cross_tenant_does_not_leak(app, db_session, make_org):
    """Acceptance item 5: a NULL link and a failed SEC-09 assertion produce the
    SAME ``{"owner": null, "reason": "no_ownership_recorded"}`` shape, and a
    component belonging to another tenant does not leak its unit name.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("qs-owner-leak-a")
    org_b = make_org("qs-owner-leak-b")

    a = _element(db_session, org_a.id, "A")
    b_no_link = _element(db_session, org_a.id, "B-no-link")
    _relationship(db_session, org_a.id, a, b_no_link)

    # A second element in org A whose chain points at a component that
    # actually belongs to org B (the leak scenario SEC-09 must catch).
    c_cross_tenant = _element(db_session, org_a.id, "C-cross-tenant")
    _relationship(db_session, org_a.id, a, c_cross_tenant)
    other_tenant_comp = _application_component(db_session, org_b.id, c_cross_tenant.id, name="OrgB App")
    other_unit = _org_unit(db_session, org_b.id, "OrgB-Secret-Unit")
    _ownership(db_session, org_b.id, other_tenant_comp.id, other_unit.id)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=False, with_owner=True)

    for row in result["rows"]:
        assert row["owner"] is None
        assert row["reason"] == "no_ownership_recorded"
        # The other tenant's unit name never appears anywhere in the payload.
        assert "OrgB-Secret-Unit" not in str(row)


def test_owner_absent_when_ownership_has_ended(app, db_session, make_org):
    """An ownership row whose end_date has already passed is not current
    ownership -- same absence shape as no link at all, not the ended row's
    unit."""
    import datetime as _dt

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-owner-ended")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    comp = _application_component(db_session, org.id, b.id, name="Formerly Owned App")
    unit = _org_unit(db_session, org.id, "Ended-Unit")
    _ownership(
        db_session, org.id, comp.id, unit.id,
        end_date=_dt.date.today() - _dt.timedelta(days=1),
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=False, with_owner=True)

    row = result["rows"][0]
    assert row["owner"] is None
    assert row["reason"] == "no_ownership_recorded"


# --- Acceptance criterion 7: derivation_state ---------------------------------


def test_derivation_state_not_computed_when_tenant_has_no_derived_rows(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-not-computed")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=True, with_owner=False)

    assert result["summary"]["derivation_state"] == "not_computed"


def test_derivation_state_current_when_fresh_derived_rows_exist(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-current")
    a = _element(db_session, org.id, "A")
    c = _element(db_session, org.id, "C")
    _derived(db_session, org.id, a, c, stale=False)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=True, with_owner=False)

    assert result["summary"]["derivation_state"] == "current"


# --- Acceptance criterion 8: stale honesty ------------------------------------


def test_stale_rows_only_appear_with_include_stale_true(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-stale")
    a = _element(db_session, org.id, "A")
    c = _element(db_session, org.id, "C")
    _derived(db_session, org.id, a, c, stale=True)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        default_result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, include_stale=False, with_owner=False
        )
        stale_result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, include_stale=True, with_owner=False
        )

    assert default_result["rows"] == []
    assert stale_result["rows"]
    assert stale_result["rows"][0]["relation"]["stale"] is True
    assert stale_result["rows"][0]["reason"] == "derivation_stale"


# --- Acceptance criterion 9: DA-4 tenancy -------------------------------------


def test_no_write_to_ownership_or_unit_tables(app, db_session, make_org):
    """Static check: the query service module issues no INSERT/UPDATE/DELETE
    against ``application_ownership`` or ``organization_units``.
    """
    import inspect

    from app.modules.intelligence.services import query_service

    source = inspect.getsource(query_service)
    for forbidden in ("ApplicationOwnership(", "OrganizationUnit(", ".add(ownership", ".add(unit"):
        assert forbidden not in source


def test_cross_tenant_component_pointer_does_not_leak_unit_name(app, db_session, make_org):
    """A dedicated leak test per DA-4: a component in another tenant, pointed
    at by a fenced element's chain, never surfaces its unit name.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("qs-da4-a")
    org_b = make_org("qs-da4-b")

    a = _element(db_session, org_a.id, "A")
    target = _element(db_session, org_a.id, "Target")
    _relationship(db_session, org_a.id, a, target)

    foreign_comp = _application_component(db_session, org_b.id, target.id, name="Foreign App")
    foreign_unit = _org_unit(db_session, org_b.id, "Foreign-Unit")
    _ownership(db_session, org_b.id, foreign_comp.id, foreign_unit.id)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=False, with_owner=True)

    payload_text = str(result)
    assert "Foreign-Unit" not in payload_text
    assert all(row["owner"] is None for row in result["rows"])


# --- Acceptance criterion 10: latency probe -----------------------------------


def test_latency_record_and_histogram_populated(app, db_session, make_org, caplog):
    import logging

    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION, generate_latest, REGISTRY

    org = make_org("qs-latency")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    before_sample_count = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="3", include_derived="false"
    )._sum.get()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        with caplog.at_level(logging.INFO, logger="archie.intelligence.oa2"):
            result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=False, with_owner=False)

    assert result["summary"]["latency_ms"] is not None
    assert result["summary"]["latency_ms"] >= 0

    after_sample_count = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="3", include_derived="false"
    )._sum.get()
    assert after_sample_count >= before_sample_count

    scraped = generate_latest(REGISTRY).decode("utf-8")
    assert "archie_intelligence_query_seconds" in scraped
    assert 'query="cross_layer_impact"' in scraped
    assert 'include_derived="false"' in scraped

    assert any("intelligence.query_latency" in message for message in caplog.messages)


def test_latency_histogram_labels_include_derived_true_separately(app, db_session, make_org):
    """M4 fix: NFR-5's measurement point (cross_layer_impact,
    include_derived=true, max_depth=4) must be queryable in isolation from
    the cheap explicit-only samples recorded under the same query/depth.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

    org = make_org("qs-latency-labels")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    before_true = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="4", include_derived="true"
    )._sum.get()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, max_depth=4, with_owner=False
        )

    after_true = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="4", include_derived="true"
    )._sum.get()
    assert after_true > before_true


# --- Acceptance criterion 12: mutation proof ----------------------------------


def test_mutation_proof_sec09_and_include_derived_filter(app, db_session, make_org, monkeypatch):
    """Disabling the include_derived=False filter makes criterion 2's
    assertion fail (re-enabled at the end; monkeypatch auto-reverts).

    B2/M7/NEW-3 correction: this test previously monkeypatched
    ``_find_component_for_element`` to force a same-tenant-looking
    pass-through -- a scenario the real code path never produces (a
    foreign-tenant component is already filtered out by
    ``ApplicationComponent``'s ORM tenant fencing before
    ``_sec09_tenant_check`` is ever reached). That function (and
    ``_resolve_owner``, its per-row caller) has since been DELETED as dead
    code -- round 2's own M7 fix had already made ``_resolve_owners_batch``
    the only production caller of owner resolution, leaving the old pair
    with zero real callers (NEW-3). The authoritative, REAL SEC-09
    traceability evidence is
    ``test_sec09_tenant_check_blocks_real_cross_tenant_resolution`` and
    ``test_mutation_proof_sec09_real_path`` below, which now call
    ``_resolve_owners_batch`` directly and reach the real tenant assertion
    through unmodified production control flow (a genuine g/org_id drift
    scenario, not a monkeypatched lookup). This test keeps only the
    include_derived-filter mutation proof, which is unaffected by either
    change.

    Test ids recorded in the build report:
      - test_include_derived_false_returns_only_explicit_rows (real proof)
      - test_sec09_tenant_check_blocks_real_cross_tenant_resolution /
        test_mutation_proof_sec09_real_path (real SEC-09 proof)
    """
    from app.modules.intelligence.services import query_service

    org_a = make_org("qs-mutation-a")
    a = _element(db_session, org_a.id, "A")
    target = _element(db_session, org_a.id, "Target")
    _relationship(db_session, org_a.id, a, target)
    db_session.commit()

    # Disable the include_derived=False filter: criterion 2's assertion
    # (explicit only) now fails once a derived row exists.
    c = _element(db_session, org_a.id, "C")
    _derived(db_session, org_a.id, a, c, rule_id="MUT-1")
    db_session.commit()
    monkeypatch.setattr(query_service, "_include_derived_gate", lambda include_derived: True)
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result2 = query_service.IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, with_owner=False
        )
    # The mutation (always returning a derived fact regardless of
    # include_derived) makes this assertion fail because a derived row is
    # now present in the "explicit only" result.
    with pytest.raises(AssertionError):
        assert all(r["relation"]["kind"] == "explicit" for r in result2["rows"])


# --- B2 fix: a REAL reachable SEC-09 proof, not a monkeypatched seam ----------


def test_sec09_tenant_check_blocks_real_cross_tenant_resolution(app, db_session, make_org):
    """B2/NEW-3: construct a scenario that reaches ``_sec09_tenant_check``
    through UNMODIFIED production control flow, via ``_resolve_owners_batch``
    -- the SAME function ``cross_layer_impact`` actually calls in production
    (there is no other owner-resolution path left after NEW-3's cleanup:
    the old per-row ``_resolve_owner``/``_find_component_for_element`` pair
    has been deleted as dead code).

    Under a single request, ``cross_layer_impact``'s ``org_id`` (from
    ``current_org_id()``) and the ORM tenant filter (``g.current_org_id``)
    read the SAME source, so a foreign-tenant component can never reach the
    SEC-09 check in the first place through a plain per-request call -- it
    is filtered out by ``ApplicationComponent``'s automatic tenant scoping
    first. That is exactly why a real divergent-org_id scenario has to be
    constructed explicitly here, calling the resolver directly with an
    ``org_id`` that differs from ``g.current_org_id``, rather than through
    ``cross_layer_impact`` itself.

    The real gap SEC-09 exists for is documented in this repo's own
    CLAUDE.md tenant-isolation section: "the exposure is anything that
    loops over tenants inside a single session" (CLI commands, the
    scheduler, importers, tests) where ``g.current_org_id`` can drift out
    of sync with the tenant a caller is actually resolving on behalf of.
    ``OrganizationUnit``/``ApplicationOwnership`` carry no
    ``organization_id`` column at all (00-verification-notes.md section 1),
    so once a caller reaches that join with a mismatched org_id, SEC-09 is
    the ONLY thing standing between it and another tenant's unit name.

    This test reproduces that drift directly: ``g.current_org_id`` is set
    to org B (so org B's real component and ownership chain are genuinely,
    ORM-returnably visible -- nothing here is mocked or monkeypatched), and
    ``_resolve_owners_batch`` -- the real, unmodified, production function --
    is called with an explicit ``org_id=org_a.id``, simulating a
    session-scoped caller resolving on behalf of org A while ``g`` has
    drifted to org B.

    Per the build report's honest B2 disclosure: this scenario is NOT
    reachable through either of this repo's two real HTTP callers today
    (both read ``org_id`` from the same ``g.current_org_id`` the ORM filter
    reads, so they can never diverge in a single request). It documents and
    protects the defense-in-depth guarantee for a caller shape this
    codebase's own tenant-isolation notes name as a real, if not currently
    wired, exposure class (a background job/CLI/importer looping tenants
    inside one session) -- not a guarantee currently exercised by a live
    request.
    """
    from app.modules.intelligence.services.query_service import _resolve_owners_batch

    org_a = make_org("qs-sec09-real-a")
    org_b = make_org("qs-sec09-real-b")

    target = _element(db_session, org_b.id, "Target")
    comp = _application_component(db_session, org_b.id, target.id, name="OrgB App")
    unit = _org_unit(db_session, org_b.id, "OrgB-Real-Unit")
    _ownership(db_session, org_b.id, comp.id, unit.id)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        # g (what the ORM tenant filter reads) is org B -- org B's own
        # component is legitimately, unfilteredly visible to this session.
        g.current_org_id = org_b.id
        results = _resolve_owners_batch([target.id], org_a.id)

    owner, reason = results[target.id]
    assert owner is None
    assert reason == "no_ownership_recorded"
    assert "OrgB-Real-Unit" not in str(owner)


def test_mutation_proof_sec09_real_path(app, db_session, make_org, monkeypatch):
    """Companion mutation proof for the test above: disabling the REAL,
    unmodified ``_sec09_tenant_check`` -- not an upstream seam -- makes the
    same real, reachable scenario leak org B's unit name, through
    ``_resolve_owners_batch``, the only owner-resolution implementation left
    in production after NEW-3's cleanup. This is the correct SEC-09
    traceability evidence (B2 fix).
    """
    from app.modules.intelligence.services import query_service

    org_a = make_org("qs-sec09-mut-a")
    org_b = make_org("qs-sec09-mut-b")

    target = _element(db_session, org_b.id, "Target")
    comp = _application_component(db_session, org_b.id, target.id, name="OrgB App")
    unit = _org_unit(db_session, org_b.id, "OrgB-Mut-Unit")
    _ownership(db_session, org_b.id, comp.id, unit.id)
    db_session.commit()

    # 1) SEC-09 intact: the guarded, real path returns no owner.
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_b.id
        results = query_service._resolve_owners_batch([target.id], org_a.id)
    owner, _reason = results[target.id]
    assert owner is None

    # 2) Disable ONLY _sec09_tenant_check (the real function, not a seam
    # upstream of it) -- the same real scenario now leaks org B's unit.
    monkeypatch.setattr(
        query_service, "_sec09_tenant_check", lambda component_org_id, org_id: True
    )
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_b.id
        results2 = query_service._resolve_owners_batch([target.id], org_a.id)

    owner2, reason2 = results2[target.id]
    assert owner2 is not None
    assert owner2["name"] == unit.name
    assert reason2 is None


# --- B3 fix: duplicate archimate_element_id must not 500 ----------------------


def test_duplicate_component_pointer_resolves_deterministically_not_500(app, db_session, make_org):
    """B3: two ``ApplicationComponent`` rows pointing at the same
    ``archimate_element_id`` (a real, expected state per the tech-lead's
    verification notes -- the before_insert listener "is not a guarantee")
    must not raise ``MultipleResultsFound`` out of
    ``_find_component_for_element``. The route call returns 200 with some
    deterministic owner resolution rather than 500ing.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("qs-duplicate-pointer")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)

    comp1 = ApplicationComponent(name="First", organization_id=org.id, archimate_element_id=b.id)
    comp2 = ApplicationComponent(name="Second", organization_id=org.id, archimate_element_id=b.id)
    db_session.add_all([comp1, comp2])
    db_session.flush()
    unit = _org_unit(db_session, org.id, "Dup-Unit")
    _ownership(db_session, org.id, comp1.id, unit.id, ownership_type="Business Owner")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        # Must not raise MultipleResultsFound.
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, with_owner=True
        )

    assert result["rows"]



# --- derived rows carry derived_id and engine_version ------------------------


def test_derived_row_carries_derived_id_and_engine_version(app, db_session, make_org):
    """``relation.derived_id`` / ``relation.engine_version`` come from the
    derived-fact store's own row, so a caller can address
    ``GET /api/v1/intelligence/derived/<derived_id>`` for the row it is
    looking at. (Fuller coverage is in ``test_query_service_elements_map.py``.)
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-derived-id")
    a = _element(db_session, org.id, "A")
    c = _element(db_session, org.id, "C")
    fact = _derived(db_session, org.id, a, c, rule_id="RULE-9", depth=2, chain=[21, 22])
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, max_depth=3, with_owner=False
        )

    derived = [r for r in result["rows"] if r["relation"]["kind"] == "derived"]
    assert derived
    for row in derived:
        assert row["relation"]["derived_id"] == fact.id
        assert row["relation"]["engine_version"] == "v1"


def test_explicit_row_carries_null_derived_id_and_engine_version(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-explicit-null-derived")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, with_owner=False
        )

    assert result["rows"]
    for row in result["rows"]:
        assert row["relation"]["derived_id"] is None
        assert row["relation"]["engine_version"] is None


# --- Four-layer proof: cross_layer_impact works beyond the application layer
# (both unfiltered and with layer= set) for Motivation, Strategy,
# Implementation & Migration and Physical elements, plus one chain crossing
# at least four layers end to end.


def test_cross_layer_impact_through_motivation_layer_element(app, db_session, make_org):
    """The layer= filter keeps a row when EITHER of its two endpoints is on
    the named layer (query_service.py:364), so the root itself -- always on
    the layer under test here -- makes every row one hop from it pass
    regardless of the far end. Proving the explicit-row filter genuinely
    excludes something needs an element the filter's own logic can actually
    drop: one two hops away, reached only through an intermediate that is
    ALSO off the filtered layer, so neither endpoint of that specific edge
    is a match.

    The derived-fact filter (derived_facts.py:143-152) needs the same proof
    separately: list_derived_facts always keys its query on the id of
    whichever element cross_layer_impact was called with, so a derived fact
    with both endpoints off the filtered layer can only be exercised by
    calling cross_layer_impact from an off-layer element -- here, `bridge`,
    already off-layer from the explicit-row proof above -- not from `a`.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-layer-motivation")
    a = _element(db_session, org.id, "Stakeholder-A", layer="motivation", type_="Stakeholder")
    b = _element(db_session, org.id, "Driver-B", layer="motivation", type_="Driver")
    c = _element(db_session, org.id, "Assessment-C", layer="motivation", type_="Assessment")
    bridge = _element(db_session, org.id, "Bridge-D", layer="application", type_="ApplicationComponent")
    excluded = _element(db_session, org.id, "Excluded-E", layer="application", type_="ApplicationComponent")
    derived_off_layer = _element(
        db_session, org.id, "Derived-Off-Layer-F", layer="application", type_="ApplicationComponent"
    )
    _relationship(db_session, org.id, a, b, type_="Influence")
    _relationship(db_session, org.id, a, bridge, type_="Association")
    _relationship(db_session, org.id, bridge, excluded, type_="Association")
    _derived(db_session, org.id, a, c, rule_id="MOTIVATION-1")
    _derived(db_session, org.id, bridge, derived_off_layer, rule_id="MOTIVATION-OFFLAYER-1")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        unfiltered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, with_owner=False
        )
        filtered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, layer="motivation", with_owner=False
        )
        unfiltered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, with_owner=False
        )
        filtered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, layer="motivation", with_owner=False
        )

    unfiltered_kinds = {r["relation"]["kind"] for r in unfiltered["rows"]}
    assert unfiltered_kinds == {"explicit", "derived"}
    unfiltered_ids = {r["element_id"] for r in unfiltered["rows"]}
    assert excluded.id in unfiltered_ids

    filtered_ids = {r["element_id"] for r in filtered["rows"]}
    assert b.id in filtered_ids
    assert c.id in filtered_ids
    assert excluded.id not in filtered_ids

    # The derived fact bridge -> derived_off_layer has neither endpoint on
    # "motivation": present with no filter, absent once filtered by it.
    unfiltered_from_bridge_ids = {r["element_id"] for r in unfiltered_from_bridge["rows"]}
    assert derived_off_layer.id in unfiltered_from_bridge_ids
    filtered_from_bridge_ids = {r["element_id"] for r in filtered_from_bridge["rows"]}
    assert derived_off_layer.id not in filtered_from_bridge_ids


def test_cross_layer_impact_through_strategy_layer_element(app, db_session, make_org):
    """See test_cross_layer_impact_through_motivation_layer_element's
    docstring for why the excluded element sits two hops behind an
    off-layer bridge rather than one hop from the root, and why the
    derived-fact filter is proven from `bridge`, not from `a`.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-layer-strategy")
    a = _element(db_session, org.id, "Resource-A", layer="strategy", type_="Resource")
    b = _element(db_session, org.id, "Capability-B", layer="strategy", type_="Capability")
    c = _element(db_session, org.id, "CourseOfAction-C", layer="strategy", type_="CourseOfAction")
    bridge = _element(db_session, org.id, "Bridge-D", layer="application", type_="ApplicationComponent")
    excluded = _element(db_session, org.id, "Excluded-E", layer="application", type_="ApplicationComponent")
    derived_off_layer = _element(
        db_session, org.id, "Derived-Off-Layer-F", layer="application", type_="ApplicationComponent"
    )
    _relationship(db_session, org.id, a, b, type_="Serving")
    _relationship(db_session, org.id, a, bridge, type_="Association")
    _relationship(db_session, org.id, bridge, excluded, type_="Association")
    _derived(db_session, org.id, a, c, rule_id="STRATEGY-1")
    _derived(db_session, org.id, bridge, derived_off_layer, rule_id="STRATEGY-OFFLAYER-1")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        unfiltered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, with_owner=False
        )
        filtered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, layer="strategy", with_owner=False
        )
        unfiltered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, with_owner=False
        )
        filtered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, layer="strategy", with_owner=False
        )

    unfiltered_kinds = {r["relation"]["kind"] for r in unfiltered["rows"]}
    assert unfiltered_kinds == {"explicit", "derived"}
    unfiltered_ids = {r["element_id"] for r in unfiltered["rows"]}
    assert excluded.id in unfiltered_ids

    filtered_ids = {r["element_id"] for r in filtered["rows"]}
    assert b.id in filtered_ids
    assert c.id in filtered_ids
    assert excluded.id not in filtered_ids

    unfiltered_from_bridge_ids = {r["element_id"] for r in unfiltered_from_bridge["rows"]}
    assert derived_off_layer.id in unfiltered_from_bridge_ids
    filtered_from_bridge_ids = {r["element_id"] for r in filtered_from_bridge["rows"]}
    assert derived_off_layer.id not in filtered_from_bridge_ids


def test_cross_layer_impact_through_implementation_and_migration_layer_element(app, db_session, make_org):
    """See test_cross_layer_impact_through_motivation_layer_element's
    docstring for why the excluded element sits two hops behind an
    off-layer bridge rather than one hop from the root, and why the
    derived-fact filter is proven from `bridge`, not from `a`.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-layer-impl-migration")
    a = _element(db_session, org.id, "WorkPackage-A", layer="implementation_migration", type_="WorkPackage")
    b = _element(db_session, org.id, "Deliverable-B", layer="implementation_migration", type_="Deliverable")
    c = _element(db_session, org.id, "Gap-C", layer="implementation_migration", type_="Gap")
    bridge = _element(db_session, org.id, "Bridge-D", layer="application", type_="ApplicationComponent")
    excluded = _element(db_session, org.id, "Excluded-E", layer="application", type_="ApplicationComponent")
    derived_off_layer = _element(
        db_session, org.id, "Derived-Off-Layer-F", layer="application", type_="ApplicationComponent"
    )
    _relationship(db_session, org.id, a, b, type_="Serving")
    _relationship(db_session, org.id, a, bridge, type_="Association")
    _relationship(db_session, org.id, bridge, excluded, type_="Association")
    _derived(db_session, org.id, a, c, rule_id="IMPL-MIGRATION-1")
    _derived(db_session, org.id, bridge, derived_off_layer, rule_id="IMPL-MIGRATION-OFFLAYER-1")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        unfiltered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, with_owner=False
        )
        filtered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, layer="implementation_migration", with_owner=False
        )
        unfiltered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, with_owner=False
        )
        filtered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, layer="implementation_migration", with_owner=False
        )

    unfiltered_kinds = {r["relation"]["kind"] for r in unfiltered["rows"]}
    assert unfiltered_kinds == {"explicit", "derived"}
    unfiltered_ids = {r["element_id"] for r in unfiltered["rows"]}
    assert excluded.id in unfiltered_ids

    filtered_ids = {r["element_id"] for r in filtered["rows"]}
    assert b.id in filtered_ids
    assert c.id in filtered_ids
    assert excluded.id not in filtered_ids

    unfiltered_from_bridge_ids = {r["element_id"] for r in unfiltered_from_bridge["rows"]}
    assert derived_off_layer.id in unfiltered_from_bridge_ids
    filtered_from_bridge_ids = {r["element_id"] for r in filtered_from_bridge["rows"]}
    assert derived_off_layer.id not in filtered_from_bridge_ids


def test_cross_layer_impact_through_physical_layer_element(app, db_session, make_org):
    """See test_cross_layer_impact_through_motivation_layer_element's
    docstring for why the excluded element sits two hops behind an
    off-layer bridge rather than one hop from the root, and why the
    derived-fact filter is proven from `bridge`, not from `a`.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-layer-physical")
    a = _element(db_session, org.id, "Equipment-A", layer="physical", type_="Equipment")
    b = _element(db_session, org.id, "Facility-B", layer="physical", type_="Facility")
    c = _element(db_session, org.id, "Material-C", layer="physical", type_="Material")
    bridge = _element(db_session, org.id, "Bridge-D", layer="application", type_="ApplicationComponent")
    excluded = _element(db_session, org.id, "Excluded-E", layer="application", type_="ApplicationComponent")
    derived_off_layer = _element(
        db_session, org.id, "Derived-Off-Layer-F", layer="application", type_="ApplicationComponent"
    )
    _relationship(db_session, org.id, a, b, type_="Serving")
    _relationship(db_session, org.id, a, bridge, type_="Association")
    _relationship(db_session, org.id, bridge, excluded, type_="Association")
    _derived(db_session, org.id, a, c, rule_id="PHYSICAL-1")
    _derived(db_session, org.id, bridge, derived_off_layer, rule_id="PHYSICAL-OFFLAYER-1")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        unfiltered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, with_owner=False
        )
        filtered = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=True, layer="physical", with_owner=False
        )
        unfiltered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, with_owner=False
        )
        filtered_from_bridge = IntelligenceQueryService.cross_layer_impact(
            bridge.id, include_derived=True, layer="physical", with_owner=False
        )

    unfiltered_kinds = {r["relation"]["kind"] for r in unfiltered["rows"]}
    assert unfiltered_kinds == {"explicit", "derived"}
    unfiltered_ids = {r["element_id"] for r in unfiltered["rows"]}
    assert excluded.id in unfiltered_ids

    filtered_ids = {r["element_id"] for r in filtered["rows"]}
    assert b.id in filtered_ids
    assert c.id in filtered_ids
    assert excluded.id not in filtered_ids

    unfiltered_from_bridge_ids = {r["element_id"] for r in unfiltered_from_bridge["rows"]}
    assert derived_off_layer.id in unfiltered_from_bridge_ids
    filtered_from_bridge_ids = {r["element_id"] for r in filtered_from_bridge["rows"]}
    assert derived_off_layer.id not in filtered_from_bridge_ids


def test_cross_layer_impact_chain_crosses_at_least_four_layers_end_to_end(app, db_session, make_org):
    """One chain, four elements, each on a different ArchiMate layer
    (Motivation -> Strategy -> Implementation & Migration -> Physical):
    cross_layer_impact's explicit walk must reach every element regardless
    of which layer it is on.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("qs-layer-four-chain")
    a = _element(db_session, org.id, "Stakeholder-A", layer="motivation", type_="Stakeholder")
    b = _element(db_session, org.id, "CourseOfAction-B", layer="strategy", type_="CourseOfAction")
    c = _element(
        db_session, org.id, "WorkPackage-C", layer="implementation_migration", type_="WorkPackage"
    )
    d = _element(db_session, org.id, "Equipment-D", layer="physical", type_="Equipment")
    _relationship(db_session, org.id, a, b, type_="Serving")
    _relationship(db_session, org.id, b, c, type_="Serving")
    _relationship(db_session, org.id, c, d, type_="Serving")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, max_depth=3, with_owner=False
        )

    element_ids = {row["element_id"] for row in result["rows"]}
    assert {b.id, c.id, d.id} <= element_ids
