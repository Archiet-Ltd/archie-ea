"""Evidence on an adopted control: one rule, a ranked gap list, control tests.

Two real organisations in every isolation test; the organisation a call acts for
is entered the way a request enters it. Every test here fails on the unchanged
branch (no validity period, no gap list, no control test, three disagreeing
"missing evidence" checks).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests import _control_evidence_world as w

pytestmark = pytest.mark.usefixtures("db_session")


def _gaps(app, tenant_ctx, org_id, **kwargs):
    from app.modules.compliance.services.evidence_gap_service import evidence_gaps

    with tenant_ctx(org_id):
        return evidence_gaps(org_id, **kwargs)


# --------------------------------------------------------------------- #
# The one rule                                                           #
# --------------------------------------------------------------------- #


def test_evidence_state_names_exactly_one_of_five_states(db_session, make_org):
    from app.modules.compliance.services.control_evidence import evidence_state

    org = make_org("rule")
    fw = w.framework(db_session)
    component, _el = w.application(db_session, org.id, "Ledger")
    now = datetime(2026, 10, 16, 12, 0, 0)

    def row(code, status="implemented", **fields):
        return w.adopted(db_session, org.id, w.control(db_session, fw, code), component, status, **fields)

    assert evidence_state(row("A", "not_applicable"), now) == "not_required"
    assert evidence_state(row("B", "waived", evidence_url="https://e.example/1"), now) == "not_required"
    assert evidence_state(row("C"), now) == "missing"
    assert evidence_state(row("D", evidence_url="   "), now) == "missing"
    assert evidence_state(
        row("E", evidence_url="https://e.example/1", evidence_valid_until=now - timedelta(days=1)), now
    ) == "expired"
    assert evidence_state(
        row("F", evidence_url="https://e.example/1", evidence_valid_until=now + timedelta(days=30)), now
    ) == "current"
    assert evidence_state(row("G", evidence_url="https://e.example/1"), now) == "undated"
    # A verification date alone counts as evidence recorded, as it did before.
    assert evidence_state(row("H", verified_date=now - timedelta(days=3)), now) == "undated"


def test_an_expired_and_a_missing_control_are_listed_and_a_current_one_is_not(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("gaps")
    fw = w.framework(db_session)
    component, _el = w.application(db_session, org.id, "Payments")
    missing = w.adopted(db_session, org.id, w.control(db_session, fw, "M-1"), component)
    expired = w.adopted(
        db_session, org.id, w.control(db_session, fw, "E-1"), component,
        evidence_url="https://e.example/old", evidence_valid_until=w.days(-1),
    )
    current = w.adopted(
        db_session, org.id, w.control(db_session, fw, "C-1"), component,
        evidence_url="https://e.example/new", evidence_valid_until=w.days(30),
    )
    db_session.commit()

    rows = _gaps(app, tenant_ctx, org.id)

    by_id = {r["trace"]["adopted_row_id"]: r for r in rows}
    assert set(by_id) == {missing.id, expired.id}
    assert by_id[missing.id]["state"] == "missing"
    assert by_id[expired.id]["state"] == "expired"
    assert current.id not in by_id
    for r in rows:
        assert r["application_name"] == "Payments"
        assert r["framework_code"] == fw.code
        assert r["evidence"] in ("https://e.example/old", "—")
        assert r["confidence"] == "recorded"
        assert set(r["trace"]) >= {
            "control_id", "framework_id", "adopted_row_id", "application_id", "element_id",
        }
        assert r["provenance"].keys() >= {"source", "who", "collected_at"}
    assert by_id[missing.id]["valid_until"] is None


def test_a_control_that_is_not_required_is_not_a_gap(app, db_session, make_org, tenant_ctx):
    org = make_org("notreq")
    fw = w.framework(db_session)
    component, _el = w.application(db_session, org.id, "Payments")
    w.adopted(db_session, org.id, w.control(db_session, fw, "N-1"), component, "not_applicable")
    w.adopted(db_session, org.id, w.control(db_session, fw, "N-2"), component, "waived")
    db_session.commit()

    assert _gaps(app, tenant_ctx, org.id) == []


def test_an_organisation_wide_control_reads_as_organisation_wide(app, db_session, make_org, tenant_ctx):
    org = make_org("orgwide")
    fw = w.framework(db_session)
    w.adopted(db_session, org.id, w.control(db_session, fw, "O-1"), None, "planned")
    db_session.commit()

    [row] = _gaps(app, tenant_ctx, org.id)

    assert row["application_name"] == "Organisation-wide"
    assert row["exposure"] is None and row["exposure_reason"]
    assert row["trace"]["application_id"] is None and row["trace"]["element_id"] is None


# --------------------------------------------------------------------- #
# One rule on three surfaces                                             #
# --------------------------------------------------------------------- #


def test_the_three_surfaces_report_the_same_state_for_the_same_row(app, db_session, make_org):
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.models.regulatory_framework import FrameworkAdoption
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.models.risk import Risk, RiskStatus

    org = make_org("surfaces")
    fw = w.framework(db_session)
    component, el = w.application(db_session, org.id, "Treasury")
    dependent = w.element(db_session, org.id, "Settlement", type_="BusinessService", layer="business")
    w.serves(db_session, org.id, el, dependent)
    db_session.add(Risk(
        organization_id=org.id, archimate_element_id=el.id, title="Outage",
        likelihood=3, impact=3, status=RiskStatus.OPEN,
    ))
    adoption = FrameworkAdoption(organization_id=org.id, framework_id=fw.id, status="active")
    db_session.add(adoption)
    db_session.flush()
    expired = w.adopted(
        db_session, org.id, w.control(db_session, fw, "X-1"), component,
        adoption_id=adoption.id, evidence_url="https://e.example/old", evidence_valid_until=w.days(-2),
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        lens = IntelligenceQueryService.compliance_for_element(el.id)
        risk = IntelligenceQueryService.risk_propagation(dependent.id)
        status = ApplicabilityService.evidence_status(org.id, adoption.id)

    [lens_row] = lens["controls"]
    [risk_row] = risk["rows"][0]["controls"]
    [status_row] = status
    assert lens_row["evidence_state"] == risk_row["evidence_state"] == status_row["evidence_state"] == "expired"
    assert status_row["id"] == expired.id
    assert lens_row["no_evidence"] is True
    assert "control_evidence_expired" in lens["reasons"]


def test_an_expired_control_no_longer_counts_as_present_in_the_lens(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("lens")
    fw = w.framework(db_session)
    component, el = w.application(db_session, org.id, "Billing")
    w.adopted(
        db_session, org.id, w.control(db_session, fw, "L-1"), component,
        evidence_url="https://e.example/old", evidence_valid_until=w.days(-1),
    )
    w.adopted(
        db_session, org.id, w.control(db_session, fw, "L-2"), component,
        evidence_url="https://e.example/new", evidence_valid_until=w.days(60),
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        lens = IntelligenceQueryService.compliance_for_element(el.id)

    by_code = {c["code"]: c for c in lens["controls"]}
    assert by_code["L-1"]["no_evidence"] is True and by_code["L-1"]["evidence_state"] == "expired"
    assert by_code["L-2"]["no_evidence"] is False and by_code["L-2"]["evidence_state"] == "current"
    assert "control_evidence_expired" in lens["reasons"]
    assert "no_control_evidence" not in lens["reasons"]


def test_the_harmonised_partner_fallback_survives_the_rule(db_session, make_org):
    from app.models.regulatory_framework import FrameworkAdoption
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    org = make_org("harmonised")
    fw_a, fw_b = w.framework(db_session), w.framework(db_session)
    component, _el = w.application(db_session, org.id, "Vault")
    own = w.control(db_session, fw_a, "H-1")
    partner = w.control(db_session, fw_b, "H-2")
    own.harmonized_control_id = partner.id
    own.harmonization_status = "confirmed"
    adoption = FrameworkAdoption(organization_id=org.id, framework_id=fw_a.id, status="active")
    db_session.add(adoption)
    db_session.flush()
    w.adopted(db_session, org.id, own, component, adoption_id=adoption.id)
    w.adopted(
        db_session, org.id, partner, component,
        evidence_url="https://e.example/partner", evidence_valid_until=w.days(10),
    )
    db_session.commit()

    [entry] = ApplicabilityService.evidence_status(org.id, adoption.id)

    assert entry["evidence_url"] == "https://e.example/partner"
    assert entry["evidence_source"]["control_code"] == "H-2"
    assert entry["evidence_state"] == "current"


# --------------------------------------------------------------------- #
# Two organisations                                                      #
# --------------------------------------------------------------------- #


def test_one_organisations_gap_list_never_shows_the_others_rows(app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("iso-a"), make_org("iso-b")
    fw = w.framework(db_session)
    comp_a, _ = w.application(db_session, org_a.id, "A Ledger")
    comp_b, _ = w.application(db_session, org_b.id, "B Ledger")
    row_a = w.adopted(db_session, org_a.id, w.control(db_session, fw, "A-1"), comp_a)
    row_b = w.adopted(db_session, org_b.id, w.control(db_session, fw, "B-1"), comp_b)
    db_session.commit()

    rows_a = _gaps(app, tenant_ctx, org_a.id)
    rows_b = _gaps(app, tenant_ctx, org_b.id)

    assert [r["trace"]["adopted_row_id"] for r in rows_a] == [row_a.id]
    assert [r["trace"]["adopted_row_id"] for r in rows_b] == [row_b.id]
    assert "B Ledger" not in repr(rows_a) and "B-1" not in repr(rows_a)


def test_with_no_active_organisation_the_gap_list_is_empty(app, db_session, make_org):
    from app.modules.compliance.services.evidence_gap_service import evidence_gaps

    org = make_org("unset")
    fw = w.framework(db_session)
    comp, _ = w.application(db_session, org.id, "Ledger")
    w.adopted(db_session, org.id, w.control(db_session, fw, "U-1"), comp)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        assert evidence_gaps(None) == []


# --------------------------------------------------------------------- #
# Control tests                                                          #
# --------------------------------------------------------------------- #


def _test(db_session, org_id, row, kind, result, when=None, notes=None):
    from app.models.control_test import ControlTest

    t = ControlTest(
        organization_id=org_id, application_control_id=row.id, test_kind=kind, result=result,
        tested_at=when or datetime.utcnow(), notes=notes,
    )
    db_session.add(t)
    db_session.flush()
    return t


def test_effectiveness_is_the_latest_test_of_each_kind(db_session, make_org):
    from app.modules.compliance.services.control_evidence import control_effectiveness

    org = make_org("effect")
    fw = w.framework(db_session)
    comp, _ = w.application(db_session, org.id, "Ledger")
    row = w.adopted(db_session, org.id, w.control(db_session, fw, "T-1"), comp)
    t0 = datetime(2026, 1, 1)

    assert control_effectiveness([]) == "not_tested"
    d = _test(db_session, org.id, row, "design", "effective", t0)
    assert control_effectiveness([d]) == "not_tested"
    o = _test(db_session, org.id, row, "operating", "effective", t0 + timedelta(days=1))
    assert control_effectiveness([d, o]) == "effective"
    o2 = _test(db_session, org.id, row, "operating", "ineffective", t0 + timedelta(days=2))
    assert control_effectiveness([d, o, o2]) == "ineffective"
    o3 = _test(db_session, org.id, row, "operating", "exception", t0 + timedelta(days=3))
    assert control_effectiveness([d, o, o2, o3]) == "exception"
    # An ineffective latest test wins even with the other kind untested.
    assert control_effectiveness([o2]) == "ineffective"


def test_an_ineffective_operating_test_puts_a_current_control_on_the_gap_list(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("ineffective")
    fw = w.framework(db_session)
    comp, _ = w.application(db_session, org.id, "Ledger")
    row = w.adopted(
        db_session, org.id, w.control(db_session, fw, "I-1"), comp,
        evidence_url="https://e.example/ok", evidence_valid_until=w.days(30),
    )
    t0 = datetime.utcnow() - timedelta(days=5)
    _test(db_session, org.id, row, "design", "effective", t0)
    _test(db_session, org.id, row, "operating", "effective", t0)
    db_session.commit()
    assert _gaps(app, tenant_ctx, org.id) == []

    _test(db_session, org.id, row, "operating", "ineffective", t0 + timedelta(days=2), notes="Sample failed")
    db_session.commit()

    [gap] = _gaps(app, tenant_ctx, org.id)
    assert gap["state"] == "current"
    assert gap["latest_test_result"] == "ineffective"
    assert gap["effectiveness"] == "ineffective"


def test_an_exception_is_listed_with_its_notes(app, db_session, make_org, tenant_ctx):
    org = make_org("exception")
    fw = w.framework(db_session)
    comp, _ = w.application(db_session, org.id, "Ledger")
    row = w.adopted(
        db_session, org.id, w.control(db_session, fw, "X-1"), comp,
        evidence_url="https://e.example/ok", evidence_valid_until=w.days(30),
    )
    now = datetime.utcnow()
    _test(db_session, org.id, row, "design", "effective", now)
    _test(db_session, org.id, row, "operating", "exception", now, notes="Two late approvals")
    db_session.commit()

    [gap] = _gaps(app, tenant_ctx, org.id)

    assert gap["effectiveness"] == "exception"
    assert gap["test_notes"] == "Two late approvals"


# --------------------------------------------------------------------- #
# Ranking and exposure                                                   #
# --------------------------------------------------------------------- #


def _app_with_downstream(db_session, org_id, name, downstream):
    component, el = w.application(db_session, org_id, name)
    for i in range(downstream):
        target = w.element(db_session, org_id, f"{name} target {i}", type_="BusinessService", layer="business")
        w.serves(db_session, org_id, el, target)
    return component, el


def test_priority_ranks_first_then_exposure_then_valid_until(app, db_session, make_org, tenant_ctx):
    org = make_org("rank")
    fw = w.framework(db_session)
    wide, _ = _app_with_downstream(db_session, org.id, "Wide", 3)
    narrow, _ = _app_with_downstream(db_session, org.id, "Narrow", 1)
    low_wide = w.adopted(db_session, org.id, w.control(db_session, fw, "R-low", priority="low"), wide)
    med_narrow = w.adopted(db_session, org.id, w.control(db_session, fw, "R-med1", priority="medium"), narrow)
    med_wide = w.adopted(db_session, org.id, w.control(db_session, fw, "R-med2", priority="medium"), wide)
    crit_narrow = w.adopted(db_session, org.id, w.control(db_session, fw, "R-crit", priority="critical"), narrow)
    db_session.commit()

    rows = _gaps(app, tenant_ctx, org.id)

    assert [r["trace"]["adopted_row_id"] for r in rows] == [
        crit_narrow.id, med_wide.id, med_narrow.id, low_wide.id,
    ]
    by_id = {r["trace"]["adopted_row_id"]: r for r in rows}
    assert by_id[med_wide.id]["exposure"] == 3
    assert by_id[med_narrow.id]["exposure"] == 1


def test_valid_until_ascending_breaks_a_tie(app, db_session, make_org, tenant_ctx):
    org = make_org("ranktie")
    fw = w.framework(db_session)
    comp, _ = _app_with_downstream(db_session, org.id, "Same", 1)
    later = w.adopted(
        db_session, org.id, w.control(db_session, fw, "T-b", priority="high"), comp,
        evidence_url="https://e.example/b", evidence_valid_until=w.days(-1),
    )
    earlier = w.adopted(
        db_session, org.id, w.control(db_session, fw, "T-a", priority="high"), comp,
        evidence_url="https://e.example/a", evidence_valid_until=w.days(-20),
    )
    db_session.commit()

    rows = _gaps(app, tenant_ctx, org.id)

    assert [r["trace"]["adopted_row_id"] for r in rows] == [earlier.id, later.id]


def test_thirty_applications_give_twenty_five_exposures_and_five_dashes(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("thirty")
    fw = w.framework(db_session)
    for i in range(30):
        comp, _ = _app_with_downstream(db_session, org.id, f"App {i:02d}", 1)
        w.adopted(db_session, org.id, w.control(db_session, fw, f"P-{i:02d}", priority="high"), comp)
    db_session.commit()

    rows = _gaps(app, tenant_ctx, org.id)

    assert len(rows) == 30
    computed = [r for r in rows if r["exposure"] is not None]
    skipped = [r for r in rows if r["exposure"] is None]
    assert len(computed) == 25 and len(skipped) == 5
    assert all(r["exposure_reason"] == "not computed for this many applications" for r in skipped)
    assert all(r["exposure"] == 1 for r in computed)


def test_the_list_makes_one_set_impact_call_and_a_constant_number_of_other_statements(
    app, db_session, make_org, tenant_ctx, monkeypatch
):
    from sqlalchemy import event

    from app import db
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    fw = w.framework(db_session)

    def build(label, count):
        org = make_org(label)
        for i in range(count):
            comp, _ = _app_with_downstream(db_session, org.id, f"{label}-{i}", 2)
            w.adopted(db_session, org.id, w.control(db_session, fw, f"{label}-C{i}"), comp)
        db_session.commit()
        return org

    small, large = build("qsmall", 2), build("qlarge", 7)
    state = {"calls": 0, "inside": False, "outside": 0}
    original = IntelligenceQueryService.set_impact

    def counting(*args, **kwargs):
        state["calls"] += 1
        state["inside"] = True
        try:
            return original(*args, **kwargs)
        finally:
            state["inside"] = False

    monkeypatch.setattr(IntelligenceQueryService, "set_impact", staticmethod(counting))

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        if not state["inside"]:
            state["outside"] += 1

    engine = db.session.get_bind()
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        measured = {}
        for label, org in (("small", small), ("large", large)):
            state.update(calls=0, outside=0)
            rows = _gaps(app, tenant_ctx, org.id)
            measured[label] = (state["calls"], state["outside"], len(rows))
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)

    assert measured["small"][0] == 1 and measured["large"][0] == 1
    assert measured["large"][2] == 7 and all(
        r["exposure"] == 2 for r in _gaps(app, tenant_ctx, large.id)
    )
    assert measured["small"][1] == measured["large"][1], measured


# --------------------------------------------------------------------- #
# Owners and names                                                       #
# --------------------------------------------------------------------- #


def test_owner_display_comes_from_owner_rows_and_a_nameless_user_is_a_dash(
    app, db_session, make_org, tenant_ctx
):
    from app.models.application_owner import ApplicationOwner

    org = make_org("owners")
    fw = w.framework(db_session)
    comp, _ = w.application(db_session, org.id, "Ledger")
    nameless = w.user(db_session, org.id, "nameless", first_name="", last_name="")
    nameless.first_name = None
    nameless.last_name = None
    named = w.user(db_session, org.id, "Ada", first_name="Ada", last_name="Lovelace")
    db_session.add(ApplicationOwner(
        application_id=comp.id, user_id=named.id, ownership_type="primary", organization_id=org.id,
    ))
    row = w.adopted(db_session, org.id, w.control(db_session, fw, "O-1"), comp, evidence_owner_id=nameless.id)
    db_session.commit()

    [gap] = _gaps(app, tenant_ctx, org.id)

    assert gap["trace"]["adopted_row_id"] == row.id
    assert "Ada Lovelace" in gap["owner"]
    assert gap["evidence_owner"] == "—"
    assert nameless.email not in repr(gap)
