"""What a technology costs and each application's share of it.

Two real organisations in every test; the shared transactional fixtures leave nothing behind.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services import technology_cost as tc

pytestmark = pytest.mark.usefixtures("db_session")


def _application(db_session, org, name):
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
    db_session.add(row)
    db_session.flush()
    return row


def _technology(db_session, org, name, serves=()):
    from app.models import ArchiMateElement, ArchiMateRelationship

    node = ArchiMateElement(name=name, type="Node", layer="technology", organization_id=org.id)
    db_session.add(node)
    db_session.flush()
    for application in serves:
        db_session.add(ArchiMateRelationship(
            source_id=node.id, target_id=application.archimate_element_id, type="realization",
            organization_id=org.id))
    db_session.flush()
    return node


@pytest.fixture
def as_org(tenant_ctx):
    import contextlib

    from flask import g

    @contextlib.contextmanager
    def _as(org_id):
        try:
            with tenant_ctx(org_id):
                yield
        finally:
            g.pop("current_org_id", None)

    return _as


def test_no_application_means_no_share(db_session, make_org, as_org):
    org = make_org("zero")
    node = _technology(db_session, org, "Orphan cluster")
    with as_org(org.id):
        tc.record_cost(org.id, node.id, "1000", "GBP", "hosting")
        result = tc.allocation(org.id, node.id, "GBP")
    assert result["total"] == Decimal("1000.00")
    assert result["applications"] == []


def test_one_application_carries_the_whole_cost(db_session, make_org, as_org):
    org = make_org("one")
    only = _application(db_session, org, "Ledger")
    node = _technology(db_session, org, "Cluster", [only])
    with as_org(org.id):
        tc.record_cost(org.id, node.id, "1234.56", "GBP", "licence")
        result = tc.allocation(org.id, node.id, "GBP")
        mine = tc.shares_for_application(org.id, only.id, "GBP")
    assert [a["share"] for a in result["applications"]] == [Decimal("1234.56")]
    assert mine[0]["share"] == Decimal("1234.56") and mine[0]["total"] == Decimal("1234.56")


def test_three_applications_split_to_the_penny(db_session, make_org, as_org):
    org = make_org("three")
    apps = [_application(db_session, org, n) for n in ("Alpha", "Beta", "Gamma")]
    node = _technology(db_session, org, "Cluster", apps)
    with as_org(org.id):
        tc.record_cost(org.id, node.id, "100.00", "GBP", "hosting")
        result = tc.allocation(org.id, node.id, "GBP")
    shares = [a["share"] for a in result["applications"]]
    assert shares == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(shares) == result["total"] == Decimal("100.00")


def test_two_currencies_with_a_rate_convert_and_without_one_say_so(db_session, make_org, as_org):
    from app.models.cost_fact import ExchangeRate

    org = make_org("fx")
    apps = [_application(db_session, org, n) for n in ("Alpha", "Beta")]
    node = _technology(db_session, org, "Cluster", apps)
    with as_org(org.id):
        tc.record_cost(org.id, node.id, "100", "GBP", "hosting")
        tc.record_cost(org.id, node.id, "50", "USD", "support")
        missing = tc.allocation(org.id, node.id, "GBP")
        assert missing["total"] is None and missing["missing"]
        assert [a["share"] for a in missing["applications"]] == [None, None]
        db_session.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.80"),
                                    effective_date=date(2020, 1, 1)))
        db_session.flush()
        converted = tc.allocation(org.id, node.id, "GBP")
    assert converted["total"] == Decimal("140.00")
    assert [a["share"] for a in converted["applications"]] == [Decimal("70.00"), Decimal("70.00")]


def test_another_organisation_sees_nothing(db_session, make_org, as_org):
    org_a, org_b = make_org("iso-a"), make_org("iso-b")
    shared = _application(db_session, org_a, "Ledger")
    node = _technology(db_session, org_a, "Cluster", [shared])
    with as_org(org_a.id):
        tc.record_cost(org_a.id, node.id, "500", "GBP", "hosting")
    with as_org(org_b.id):
        assert tc.allocation(org_b.id, node.id, "GBP")["facts"] == []
        assert tc.shares_for_application(org_b.id, shared.id, "GBP") == []
        assert tc.canvas_figures(org_b.id, "GBP")["shares"] == []
        with pytest.raises(tc.TechnologyCostError):
            tc.record_cost(org_b.id, node.id, "1", "GBP", "hosting")


@pytest.mark.parametrize("amount,currency,category", [
    ("0", "GBP", "hosting"), ("abc", "GBP", "hosting"), ("5", "POUNDS", "hosting"), ("5", "GBP", "nonsense")])
def test_a_bad_cost_is_refused(db_session, make_org, as_org, amount, currency, category):
    org = make_org("bad")
    node = _technology(db_session, org, "Cluster")
    with as_org(org.id):
        with pytest.raises(tc.TechnologyCostError):
            tc.record_cost(org.id, node.id, amount, currency, category)


def test_a_cost_no_application_carries_never_blocks_the_canvas_total(db_session, make_org, as_org):
    org = make_org("canvas")
    carried = _application(db_session, org, "Alpha")
    served = _technology(db_session, org, "Cluster", [carried])
    spare = _technology(db_session, org, "Spare node")
    with as_org(org.id):
        tc.record_cost(org.id, served.id, "200", "GBP", "hosting")
        tc.record_cost(org.id, spare.id, "75", "JPY", "licence")
        figures = tc.canvas_figures(org.id, "GBP")
    assert figures["total"] == Decimal("200.00")
    assert [(r["name"], r["share"]) for r in figures["shares"]] == [("Alpha", Decimal("200.00"))]
