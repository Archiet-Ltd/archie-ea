"""What an application and a technology cost: one set of lines, one total, the same on every screen.

Every test uses two real organisations where scope matters, against the shared transactional ``db_session``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _application(db_session, org, name, *, technologies=(), shares=None, **costs):
    """An application with its model element, running on ``technologies`` (model elements), with cost columns."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id, **costs)
    db_session.add(row)
    db_session.flush()
    for tech in technologies:
        pct = (shares or {}).get(tech.name)
        db_session.add(ArchiMateRelationship(
            type="Serving", source_id=tech.id, target_id=element.id, organization_id=org.id,
            custom_properties={"cost_share_percent": pct} if pct is not None else None))
    db_session.flush()
    return row


def _technology(db_session, org, name, kind="Node"):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type=kind, layer="technology", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    return element


def _gbp(org):
    org.reporting_currency = "GBP"


# ---------------------------------------------------------------- the split rule


def test_an_even_split_adds_up_to_the_penny():
    from app.services.cost_view import split

    shares = split(Decimal("100.00"), [(1, None), (2, None), (3, None)])
    assert [s for _, s, _ in shares] == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(s for _, s, _ in shares) == Decimal("100.00")


def test_recorded_percentages_are_used_and_the_rest_is_split_evenly():
    from app.services.cost_view import split

    shares = dict((t, s) for t, s, _ in split(Decimal("1000"), [(1, Decimal(50)), (2, None), (3, None)]))
    assert shares == {1: Decimal("500.00"), 2: Decimal("250.00"), 3: Decimal("250.00")}


def test_recorded_percentages_over_a_hundred_are_scaled_down():
    from app.services.cost_view import split

    shares = dict((t, s) for t, s, _ in split(Decimal("300"), [(1, Decimal(150)), (2, Decimal(150))]))
    assert shares == {1: Decimal("150.00"), 2: Decimal("150.00")}


def test_no_technology_means_no_share():
    from app.services.cost_view import split

    assert split(Decimal("100"), []) == []


# ---------------------------------------------------------------- an application's lines


def test_the_recorded_total_is_never_counted_twice(db_session, make_org):
    """The demo shape: a total of ownership equal to licences plus twelve months of infrastructure."""
    from app.services.application_cost_accessor import FACT_COLUMNS, sync_cost_fact
    from app.services.cost_view import application_cost

    org = make_org("a")
    _gbp(org)
    app_row = _application(db_session, org, "Ledger", total_cost_of_ownership=Decimal("72000"),
                           license_cost_annual=Decimal("60000"), infrastructure_cost_monthly=Decimal("1000"))
    for column in ("total_cost_of_ownership", "license_cost_annual", "infrastructure_cost_monthly"):
        assert column in FACT_COLUMNS
        sync_cost_fact(app_row, column, "GBP")
    db_session.flush()

    view = application_cost(org.id, app_row.id)
    assert view.total == Decimal("72000.00")
    assert sorted((ln.label, ln.amount) for ln in view.counted_lines) == [
        ("Infrastructure", Decimal("12000")), ("Licences", Decimal("60000"))]
    assert all(ln.source == "Recorded on the application's cost fields" and ln.as_of for ln in view.lines)


def test_a_total_larger_than_its_breakdown_shows_the_rest(db_session, make_org):
    from app.services.application_cost_accessor import sync_cost_fact
    from app.services.cost_view import application_cost

    org = make_org("a")
    _gbp(org)
    app_row = _application(db_session, org, "Ledger", total_cost_of_ownership=Decimal("100000"),
                           license_cost_annual=Decimal("60000"))
    sync_cost_fact(app_row, "total_cost_of_ownership", "GBP")
    sync_cost_fact(app_row, "license_cost_annual", "GBP")
    db_session.flush()

    view = application_cost(org.id, app_row.id)
    assert view.total == Decimal("100000.00")
    assert ("Not broken down", Decimal("40000")) in [(ln.label, ln.amount) for ln in view.lines]


def test_a_person_adds_edits_and_deletes_a_line_and_the_total_follows(db_session, make_org):
    from app.models.cost_fact import ELEMENT_APPLICATION
    from app.services.application_cost_accessor import sync_cost_fact
    from app.models.user import User
    from app.services.cost_view import add_line, application_cost, delete_line, update_line

    org = make_org("a")
    _gbp(org)
    person = User(email="cost-editor@example.com", first_name="Bola", last_name="Adeyemi", organization_id=org.id)
    db_session.add(person)
    db_session.flush()
    app_row = _application(db_session, org, "Ledger", total_cost_of_ownership=Decimal("1000"))
    sync_cost_fact(app_row, "total_cost_of_ownership", "GBP")
    fact = add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=person.id, category="support",
                    amount="250.50", currency="gbp")
    db_session.flush()
    view = application_cost(org.id, app_row.id)
    assert view.total == Decimal("1250.50")
    person = [ln for ln in view.lines if ln.fact_id == fact.id]
    assert person and person[0].label == "Support" and person[0].source == "Added by Bola Adeyemi"

    update_line(org.id, ELEMENT_APPLICATION, app_row.id, fact.id, category="support", amount="100",
                currency="GBP", period="monthly")
    db_session.flush()
    assert application_cost(org.id, app_row.id).total == Decimal("2200.00")

    delete_line(org.id, ELEMENT_APPLICATION, app_row.id, fact.id)
    db_session.flush()
    assert application_cost(org.id, app_row.id).total == Decimal("1000.00")


def test_a_line_names_the_person_who_added_it(db_session, make_org):
    from app.models.cost_fact import ELEMENT_APPLICATION
    from app.models.user import User
    from app.services.cost_view import add_line, application_cost

    org = make_org("a")
    person = User(email="cost-person@example.com", first_name="Ada", last_name="Okafor", organization_id=org.id)
    db_session.add(person)
    db_session.flush()
    app_row = _application(db_session, org, "Ledger")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=person.id, category="support", amount="5",
             currency="GBP")
    db_session.flush()
    assert application_cost(org.id, app_row.id).lines[0].source == "Added by Ada Okafor"


def test_a_budget_is_shown_but_not_counted(db_session, make_org):
    from app.models.cost_fact import ELEMENT_APPLICATION
    from app.services.cost_view import add_line, application_cost

    org = make_org("a")
    _gbp(org)
    app_row = _application(db_session, org, "Ledger")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, category="licences", amount="500",
             currency="GBP")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, category="licences", amount="900",
             currency="GBP", kind="budget")
    db_session.flush()
    view = application_cost(org.id, app_row.id)
    assert view.total == Decimal("500.00")
    assert len(view.lines) == 2 and "not part of the actual total" in [ln for ln in view.lines if not ln.counted][0].note


@pytest.mark.parametrize("fields, words", [
    ({"category": "rent"}, "what the cost is for"),
    ({"amount": "lots"}, "as a number"),
    ({"amount": "-5"}, "above zero"),
    ({"currency": "pounds"}, "three-letter code"),
    ({"period": "weekly"}, "a year or a month"),
])
def test_a_line_that_cannot_be_recorded_says_why(db_session, make_org, fields, words):
    from app.models.cost_fact import ELEMENT_APPLICATION
    from app.services.cost_view import CostLineError, add_line

    org = make_org("a")
    app_row = _application(db_session, org, "Ledger")
    line = {"category": "support", "amount": "10", "currency": "GBP", "period": "annual", **fields}
    with pytest.raises(CostLineError, match=words):
        add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, **line)


def test_a_missing_rate_leaves_the_total_missing_and_names_the_rate(db_session, make_org):
    from app.models.cost_fact import ELEMENT_APPLICATION
    from app.services.cost_view import add_line, application_cost

    org = make_org("a")
    _gbp(org)
    app_row = _application(db_session, org, "Ledger")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, category="licences", amount="500", currency="GBP")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, category="hosting", amount="100", currency="JPY")
    db_session.flush()
    view = application_cost(org.id, app_row.id)
    assert view.total is None
    assert [c for c, _ in view.missing] == ["JPY"]


def test_a_rate_on_file_converts_the_other_currency(db_session, make_org):
    from app.models.cost_fact import ELEMENT_APPLICATION, ExchangeRate
    from app.services.cost_view import add_line, application_cost

    org = make_org("a")
    _gbp(org)
    db_session.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.8"),
                                effective_date=date(2000, 1, 1), source="test"))
    app_row = _application(db_session, org, "Ledger")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, category="licences", amount="500", currency="GBP")
    add_line(org.id, ELEMENT_APPLICATION, app_row.id, user_id=1, category="hosting", amount="100", currency="USD")
    db_session.flush()
    assert application_cost(org.id, app_row.id).total == Decimal("580.00")


# ---------------------------------------------------------------- a technology's lines


def test_a_technology_carries_its_share_of_each_application_it_runs(db_session, make_org):
    from app.services.application_cost_accessor import sync_cost_fact
    from app.services.cost_view import technology_cost

    org = make_org("a")
    _gbp(org)
    cluster = _technology(db_session, org, "Cluster")
    database = _technology(db_session, org, "Database server")
    ledger = _application(db_session, org, "Ledger", technologies=(cluster, database),
                          license_cost_annual=Decimal("5000"), infrastructure_cost_monthly=Decimal("100"))
    payments = _application(db_session, org, "Payments", technologies=(cluster,), shares={"Cluster": 40},
                            infrastructure_cost_monthly=Decimal("50"))
    for row in (ledger, payments):
        sync_cost_fact(row, "license_cost_annual", "GBP")
        sync_cost_fact(row, "infrastructure_cost_monthly", "GBP")
    db_session.flush()

    view = technology_cost(org.id, cluster.id)
    shares = {ln.source_name: ln.amount for ln in view.lines}
    assert shares == {"Ledger": Decimal("600.00"), "Payments": Decimal("240.00")}
    assert view.total == Decimal("840.00")
    assert {ln.application_id for ln in view.lines} == {ledger.id, payments.id}
    assert "split evenly across the 2 technologies" in [ln for ln in view.lines if ln.source_name == "Ledger"][0].source
    assert "40% of Payments" in [ln for ln in view.lines if ln.source_name == "Payments"][0].source
    # Licences stay with the application.
    assert technology_cost(org.id, database.id).total == Decimal("600.00")


def test_a_technology_with_nothing_recorded_or_shared_has_no_lines(db_session, make_org):
    from app.services.cost_view import technology_cost

    org = make_org("a")
    idle = _technology(db_session, org, "Idle server")
    view = technology_cost(org.id, idle.id)
    assert not view.has_lines and view.total is None


def test_a_person_records_a_cost_on_a_technology(db_session, make_org):
    from app.models.cost_fact import ELEMENT_TECHNOLOGY
    from app.services.cost_view import add_line, technology_cost

    org = make_org("a")
    _gbp(org)
    cluster = _technology(db_session, org, "Cluster")
    add_line(org.id, ELEMENT_TECHNOLOGY, cluster.id, user_id=1, category="support", amount="300", currency="GBP")
    db_session.flush()
    view = technology_cost(org.id, cluster.id)
    assert view.total == Decimal("300.00") and view.lines[0].fact_id


# ---------------------------------------------------------------- the organisation, and its boundary


def test_the_organisation_total_counts_each_application_once(db_session, make_org):
    from app.services.application_cost_accessor import sync_cost_fact
    from app.services.cost_view import application_cost, organisation_cost

    org = make_org("a")
    _gbp(org)
    cluster = _technology(db_session, org, "Cluster")
    rows = [
        _application(db_session, org, "Ledger", technologies=(cluster,), total_cost_of_ownership=Decimal("72000"),
                     license_cost_annual=Decimal("60000"), infrastructure_cost_monthly=Decimal("1000")),
        _application(db_session, org, "Payments", total_cost_of_ownership=Decimal("9000")),
    ]
    for row in rows:
        for column in ("total_cost_of_ownership", "license_cost_annual", "infrastructure_cost_monthly"):
            sync_cost_fact(row, column, "GBP")
    db_session.flush()

    whole = organisation_cost(org.id)
    assert whole.total == Decimal("81000.00")
    assert [(a["name"], a["total"]) for a in whole.applications] == [
        ("Ledger", application_cost(org.id, rows[0].id).total), ("Payments", Decimal("9000.00"))]
    assert dict(whole.categories)["Licences"] == Decimal("60000.00")


def test_another_organisations_element_cannot_be_costed_or_read(db_session, make_org):
    from app.models.cost_fact import ELEMENT_APPLICATION, ELEMENT_TECHNOLOGY
    from app.services.cost_view import add_line, delete_line, organisation_cost, technology_cost

    org_a, org_b = make_org("a"), make_org("b")
    _gbp(org_a)
    _gbp(org_b)
    ledger = _application(db_session, org_a, "Ledger")
    cluster = _technology(db_session, org_a, "Cluster")
    fact = add_line(org_a.id, ELEMENT_APPLICATION, ledger.id, user_id=1, category="support", amount="10",
                    currency="GBP")
    db_session.flush()

    with pytest.raises(LookupError):
        add_line(org_b.id, ELEMENT_APPLICATION, ledger.id, user_id=1, category="support", amount="10",
                 currency="GBP")
    with pytest.raises(LookupError):
        add_line(org_b.id, ELEMENT_TECHNOLOGY, cluster.id, user_id=1, category="support", amount="10",
                 currency="GBP")
    with pytest.raises(LookupError):
        delete_line(org_b.id, ELEMENT_APPLICATION, ledger.id, fact.id)
    assert organisation_cost(org_b.id).total is None and not organisation_cost(org_b.id).applications
    assert not technology_cost(org_b.id, cluster.id).has_lines
