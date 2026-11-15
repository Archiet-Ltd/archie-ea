"""The overview's figures carry their label and the months and names they stand on.

The year card shows one figure alone when nothing was added last year, so the figure says what it counts; the
monthly run cost draws the months the contracts' dates hold; the card that names an application with no owner
names one a person recorded before one a connector derived from a repository.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.models.application_portfolio import ApplicationComponent, VendorContract
from app.models.vendor.vendor_organization import VendorOrganization
from app.models.archimate_core import ArchiMateElement
from app.modules.dashboard.services import overview_figures as figures


def _vendor_contract(db_session, org, started, annual):
    vendor = VendorOrganization(name="Vendor %s %s" % (org.id, annual))
    db_session.add(vendor)
    db_session.flush()
    db_session.add(VendorContract(
        vendor_id=vendor.id, organization_id=org.id, contract_name="Agreement %s" % annual,
        contract_number="N-%s-%s" % (org.id, annual), status="active", annual_cost=annual, start_date=started,
    ))
    db_session.flush()


def test_run_cost_draws_each_month_a_contract_was_running(db_session, make_org):
    mine, theirs = make_org("cost"), make_org("costrival")
    now = datetime.utcnow()
    _vendor_contract(db_session, mine, date.today() - timedelta(days=75), 12000)
    _vendor_contract(db_session, theirs, date.today() - timedelta(days=200), 99000)

    series = figures._monthly_run_cost(mine.id, now, 1000)

    assert 3 <= len(series) <= 4
    assert series[-1] == {"month": now.strftime("%b"), "value": 1000}
    assert all(point["value"] == 1000 for point in series)
    assert 99000 / 12 not in {point["value"] for point in series}


def test_run_cost_with_no_contract_before_this_month_is_the_one_month(db_session, make_org):
    org = make_org("cost")
    now = datetime.utcnow()
    _vendor_contract(db_session, org, date.today(), 6000)

    assert figures._monthly_run_cost(org.id, now, 500) == [{"month": now.strftime("%b"), "value": 500}]


def _insert(when):
    return SimpleNamespace(action="insert", valid_from=when, snapshot={}, table_name="archimate_elements",
                           record_id=1, changed_by_id=None, previous_snapshot=None,
                           label="Billing", kind="Element", changed_by_name=None, source=None)


def test_year_card_figure_is_labelled_when_last_year_is_empty(db_session, make_org):
    org = make_org("year")
    now = datetime(2026, 11, 7, 12, 0)
    this_only = figures._activity([_insert(datetime(2026, 5, 1)) for _ in range(3)], now, org.id)
    assert this_only["this_year"] == "3 added this year"
    assert this_only["year_rows"][1]["this"] == 3

    both = figures._activity([_insert(datetime(2026, 5, 1)), _insert(datetime(2025, 5, 1))], now, org.id)
    assert both["this_year"] == 1 and both["last_year"] == 1

    none = figures._activity([], now, org.id)
    assert none["this_year"] == 0


def _application(db_session, org, name, source_kind):
    element = ArchiMateElement(name=name, type="Application", layer="application", organization_id=org.id,
                               source_kind=source_kind)
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id,
                               component_type="application")
    db_session.add(row)
    db_session.flush()
    return row


def test_the_unowned_card_names_a_recorded_application_before_a_derived_one(db_session, make_org, tenant_ctx):
    mine, theirs = make_org("owners"), make_org("ownersrival")
    _application(db_session, mine, "Archie Ea", "connector")
    _application(db_session, mine, "Billing", None)
    _application(db_session, theirs, "Aardvark", None)

    with tenant_ctx(mine.id):
        result = figures._applications(mine.id, datetime.utcnow())

    assert [name for _, name in result["unowned"]] == ["Billing", "Archie Ea"]
