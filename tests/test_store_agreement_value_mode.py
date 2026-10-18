"""The store-agreement gate's second comparison mode: values, not just presence.

Count mode cannot see an application column and its cost fact that both exist
and disagree (each holds one row, so every count is 1). A concept opts into
value mode through VALUE_PAIRS; "applications with a recorded annual cost" is
the first. These tests reproduce the case the cost-fact review seeded -- two
organisations whose column and fact amounts differ -- and pin that concepts
which have not opted in are compared exactly as before.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.test_store_agreement_concepts import SCRIPT, _live_findings, _make_user, gate

CONCEPT = "applications with a recorded annual cost"


def _application(db_session, org, name, **costs):
    from app.models.application_portfolio import ApplicationComponent

    application = ApplicationComponent(name=name, organization_id=org.id, **costs)
    db_session.add(application)
    db_session.flush()
    return application


def _record_costs(db_session, org, application):
    """Put the application's cost in the cost store and the fact store."""
    from app.commands.backfill_cost_facts import backfill_cost_facts
    from app.models.enterprise_intelligence import ApplicationCost

    db_session.add(ApplicationCost(application_id=application.id, fiscal_year=2026,
                                   total_cost=1200))
    db_session.flush()
    backfill_cost_facts(organization_ids=[org.id])


def _set_fact_amount(tenant_ctx, db_session, org, application, category, amount):
    from app.models.cost_fact import CostFact

    with tenant_ctx(org.id):
        fact = db_session.query(CostFact).filter_by(
            element_type="application", element_id=application.id,
            category=category, source="application_cost_accessor").one()
        fact.amount = amount
        db_session.flush()


def _count_only(observations):
    """The same observations with the value readings stripped: count mode alone."""
    return gate.Observations({k: v for k, v in observations.items()})


def test_mismatched_column_and_fact_is_red_in_two_organisations(
        app, db_session, make_org, tenant_ctx):
    """The reviewer's case: counts agree everywhere, the amounts do not."""
    org_a, org_b = make_org("value-a"), make_org("value-b")
    user_a, user_b = _make_user(db_session, org_a), _make_user(db_session, org_b)
    app_a = _application(db_session, org_a, "A ledger", license_cost=1200)
    app_b = _application(db_session, org_b, "B ledger", maintenance_cost=500)
    _record_costs(db_session, org_a, app_a)
    _record_costs(db_session, org_b, app_b)

    # Consistent: every count agrees and every amount agrees.
    for org, user in ((org_a, user_a), (org_b, user_b)):
        findings, notes, _ = _live_findings(app, tenant_ctx, org.id, user, [CONCEPT], http=False)
        assert findings == [], (findings, notes)

    # The fact drifts from the column in each organisation, by different amounts.
    _set_fact_amount(tenant_ctx, db_session, org_a, app_a, "license_cost", 900)
    _set_fact_amount(tenant_ctx, db_session, org_b, app_b, "maintenance_cost", 250)

    findings, notes, observations = _live_findings(
        app, tenant_ctx, org_a.id, user_a, [CONCEPT], http=False)
    # Presence-only comparison still finds nothing: this is the gap.
    assert gate.compare(_count_only(observations))[0] == []
    assert len(findings) == 1, (findings, notes)
    assert "[value-disagreement]" in findings[0]
    assert "id %s" % app_a.id in findings[0]
    assert "orm:ApplicationComponent.license_cost=1200" in findings[0]
    assert "orm:CostFact(license_cost)=900" in findings[0]
    assert "id %s" % app_b.id not in findings[0]

    findings, notes, _ = _live_findings(app, tenant_ctx, org_b.id, user_b, [CONCEPT], http=False)
    assert len(findings) == 1, (findings, notes)
    assert "id %s" % app_b.id in findings[0]
    assert "orm:ApplicationComponent.maintenance_cost=500" in findings[0]
    assert "orm:CostFact(maintenance_cost)=250" in findings[0]
    assert "id %s" % app_a.id not in findings[0]

    # Repair the fact: green again.
    _set_fact_amount(tenant_ctx, db_session, org_a, app_a, "license_cost", 1200)
    findings, notes, _ = _live_findings(app, tenant_ctx, org_a.id, user_a, [CONCEPT], http=False)
    assert findings == [], (findings, notes)


def test_difference_inside_the_tolerance_is_not_reported():
    pair = gate.VALUE_PAIRS[CONCEPT][0]
    reading = gate.ValueReading(pair, {1: gate.Decimal("10.000")}, {1: gate.Decimal("10.004")})
    assert gate._compare_values({CONCEPT: [reading]}) == ([], [])
    reading = gate.ValueReading(pair, {1: gate.Decimal("10.00")}, {1: gate.Decimal("10.02")})
    assert len(gate._compare_values({CONCEPT: [reading]})[0]) == 1


def test_an_element_on_one_side_only_is_a_presence_question_not_a_value_one():
    pair = gate.VALUE_PAIRS[CONCEPT][0]
    reading = gate.ValueReading(pair, {1: gate.Decimal("5"), 2: gate.Decimal("7")},
                                {1: gate.Decimal("5")})
    assert gate._compare_values({CONCEPT: [reading]}) == ([], [])


def test_value_mode_is_opt_in_and_other_concepts_are_compared_as_before(
        app, db_session, make_org, tenant_ctx):
    assert set(gate.VALUE_PAIRS) == {CONCEPT}
    assert set(gate.VALUE_PAIRS) <= set(gate.CONCEPTS)

    # A plain dict of count observations (no value readings) compares as before.
    plain = {"risks": [("a", 3, "all"), ("b", 4, "all")]}
    findings, notes = gate.compare(plain)
    assert len(findings) == 1 and "[store-disagreement]" in findings[0]

    # A concept that has not opted in produces no value readings at all.
    org = make_org("value-optout")
    user = _make_user(db_session, org)
    _, _, observations = _live_findings(app, tenant_ctx, org.id, user, ["risks"], http=False)
    assert observations.value_readings == {}


def test_value_pairs_cover_exactly_the_columns_the_cost_accessor_syncs():
    from app.services import application_cost_accessor as accessor

    assert gate._APPLICATION_COST_COLUMNS == {
        column: category for column, (category, _period) in accessor.FACT_COLUMNS.items()}
    assert gate._APPLICATION_COST_FACT_SOURCE == accessor.FACT_SOURCE
    assert len(gate.VALUE_PAIRS[CONCEPT]) == len(accessor.FACT_COLUMNS)


@pytest.mark.parametrize("fact_amount, expected_findings", [("1200", "0"), ("900", "1")])
def test_probe_file_value_readings_drive_the_same_judgement(tmp_path, fact_amount, expected_findings):
    probe = {
        CONCEPT: [{"surface": "a", "count": 1}, {"surface": "b", "count": 1}],
        "__values__": {CONCEPT: [{
            "pair": "application.license_cost = cost fact license_cost",
            "left": "orm:ApplicationComponent.license_cost",
            "right": "orm:CostFact(license_cost)",
            "left_values": {"7": "1200"}, "right_values": {"7": fact_amount}}]},
    }
    (tmp_path / "store_agreement_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    proc = subprocess.run([sys.executable, SCRIPT, "--root", str(tmp_path)],
                          capture_output=True, text=True, check=True)
    assert proc.stdout.strip().splitlines()[-1] == expected_findings, proc.stdout
    if expected_findings == "1":
        assert "[value-disagreement]" in proc.stdout
