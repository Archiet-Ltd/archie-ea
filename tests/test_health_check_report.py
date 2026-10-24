"""The health-check report: one test per promised section, and the figures agree with the screens."""
import datetime as dt
import re

import pytest

from app import db
from app.jobs.tenant_safe_job import tenant_scope
from app.services import health_check_report_service as service
from tests.test_sample_company_working_data import _architect, _figure

TODAY = dt.date(2026, 10, 24)


def _contract(org_id, app_id, name, days, annual, auto=False, status="active"):
    from app.models.application_portfolio import VendorContract

    db.session.add(VendorContract(
        organization_id=org_id, application_id=app_id, contract_name=name, status=status,
        start_date=TODAY - dt.timedelta(days=365), renewal_date=TODAY + dt.timedelta(days=days),
        annual_cost=annual, auto_renewal=auto,
    ))


def _estate(client, login_as, make_org, label):
    """The sample company, plus what it does not carry: an unowned critical application in a
    business unit, a quarter's contracts behind one shared capability, and one contract outside the quarter."""
    from app.models.application_portfolio import ApplicationComponent as App

    org = make_org(label)
    user = _architect(org, "hc-%s@example.com" % label)
    login_as(client, user)
    assert client.post("/architecture/sample-company/load").status_code == 302
    org_id, user_id = org.id, user.id
    with tenant_scope(org_id):
        orders = App.query.filter_by(name="Order management system").one()
        notifications = App.query.filter_by(name="Notification service").one()
        inventory = App.query.filter_by(name="Inventory system").one()
        orders.business_domain = "Fulfilment"
        orphan = App(
            name="Legacy billing %s" % label, organization_id=org_id, business_criticality="critical",
            business_domain="Finance", lifecycle_status="operational", vendor_name="Acme",
        )
        db.session.add(orphan)
        _contract(org_id, orders.id, "Orders licence %s" % label, 30, 48000, auto=True)
        _contract(org_id, notifications.id, "Notices plan %s" % label, 60, 9000)
        _contract(org_id, inventory.id, "Stock licence %s" % label, 300, 30000)
        db.session.commit()
    # A tenant scope resets the session, which detaches the rows, so only their ids are returned.
    return org_id, user_id


@pytest.fixture
def estate(client, login_as, make_org):
    return _estate(client, login_as, make_org, "a")


def _report(org_id):
    return service.build_report(org_id, today=TODAY)


def test_inventory_counts_every_application_and_each_missing_field(estate):
    org_id, _ = estate
    section = _report(org_id)["inventory"]
    assert section["total"] == 11
    by_field = {c["field"]: c for c in section["completeness"]}
    # The sample records an owner for all ten of its applications; the added one has none.
    assert (by_field["Owner"]["filled"], by_field["Owner"]["missing"]) == (10, 1)
    assert by_field["Business unit"]["filled"] == 2
    assert by_field["Business unit"]["percent"] == 18
    legacy = next(r for r in section["rows"] if r["name"].startswith("Legacy billing"))
    assert "Owner" in legacy["missing"] and "Annual cost" in legacy["missing"]
    assert legacy["source"] == "Application record: %s" % legacy["name"]


def test_what_breaks_lists_what_depends_on_each_critical_system_with_distance_and_owner(estate):
    org_id, _ = estate
    section = _report(org_id)["impact"]
    payments = next(s for s in section["systems"] if s["name"] == "Payments service")
    assert payments["modelled"] and payments["affected_total"] >= 3
    nearest = payments["affected"][0]
    assert nearest["hops"] == 1 and nearest["layer"] and nearest["basis"] in (
        "Stated in your data", "Derived from the model")
    assert [a["hops"] for a in payments["affected"]] == sorted(a["hops"] for a in payments["affected"])
    # An application's owner is named; an element with no recorded owner comes back as None.
    owned = [a for a in payments["affected"] if a["owner"]]
    assert owned and all(isinstance(a["owner"], str) for a in owned)
    legacy = next(s for s in section["systems"] if s["name"].startswith("Legacy billing"))
    assert legacy["affected_total"] == 0 and legacy["owner"] is None


def test_overlapping_applications_show_the_contracts_behind_them(estate):
    org_id, _ = estate
    overlaps = {o["capability"]: o for o in _report(org_id)["duplicates"]["overlaps"]}
    orders = overlaps["Track customer orders"]
    assert set(orders["applications"]) >= {"Order management system", "Notification service"}
    assert {c["name"] for c in orders["contracts"]["items"]} == {"Orders licence a", "Notices plan a"}
    assert orders["contracts"]["combined_annual_cost"] == 57000
    assert orders["contracts"]["nearest_end"] == TODAY + dt.timedelta(days=30)
    # A shared capability whose applications have no contract says so with None, not zero.
    stock = overlaps["Manage stock"]
    assert stock["contracts"]["combined_annual_cost"] is None or stock["contracts"]["items"]
    assert orders["source"] == "Capability record: Track customer orders"


def test_applications_with_no_owner_are_listed_by_criticality_and_unit(estate):
    org_id, _ = estate
    section = _report(org_id)["unowned"]
    assert [r["name"] for r in section["rows"]] == ["Legacy billing a"]
    assert section["rows"][0]["criticality"] == "critical"
    assert section["rows"][0]["business_unit"] == "Finance"
    coverage = {c["business_unit"]: c for c in section["coverage"]}
    assert (coverage["Finance"]["owned"], coverage["Finance"]["total"], coverage["Finance"]["percent"]) == (0, 1, 0)
    assert coverage["Fulfilment"]["percent"] == 100


def test_each_application_is_scored_or_listed_with_the_facts_it_lacks(estate):
    org_id, _ = estate
    section = _report(org_id)["scores"]
    assert len(section["scored"]) + len(section["unscored"]) == 11
    for row in section["scored"]:
        assert row["time"] in ("TOLERATE", "INVEST", "MIGRATE", "ELIMINATE")
        assert 0 <= row["score"] <= 100
    legacy = next(r for r in section["unscored"] if r["name"].startswith("Legacy billing"))
    assert "Application Owner" in legacy["missing"]
    assert all(r["missing"] for r in section["unscored"] if r["time"])


def test_renewals_list_the_next_quarter_with_cost_and_auto_renewal(estate):
    org_id, _ = estate
    section = _report(org_id)["renewals"]
    assert [r["name"] for r in section["rows"]] == ["Orders licence a", "Notices plan a"]
    assert section["rows"][0]["auto_renewal"] is True and section["rows"][0]["annual_cost"] == 48000
    assert section["annual_cost"] == 57000
    assert section["contracts_total"] == 3  # the contract renewing in 300 days is counted, not listed


def test_a_missing_value_is_reported_as_missing_never_filled(client, login_as, make_org):
    from app.models.application_portfolio import ApplicationComponent as App

    org = make_org("bare")
    login_as(client, _architect(org, "hc-bare@example.com"))
    org_id = org.id
    with tenant_scope(org_id):
        db.session.add(App(name="Bare app", organization_id=org_id))
        db.session.commit()
    report = service.build_report(org_id, today=TODAY)
    row = report["inventory"]["rows"][0]
    assert row["annual_cost"] is None and row["owner"] is None and row["lifecycle"] is None
    assert report["renewals"]["rows"] == [] and report["renewals"]["contracts_total"] == 0
    html = service.render_html(report)
    assert "No contracts are recorded" in html and "Not recorded" in html


def test_the_report_holds_only_the_requested_organisations_records(client, login_as, make_org):
    first, _ = _estate(client, login_as, make_org, "iso1")
    second, _ = _estate(client, login_as, make_org, "iso2")
    text = service.render_html(_report(first))
    assert "Legacy billing iso1" in text and "Orders licence iso1" in text
    assert "iso2" not in text
    other = service.render_html(_report(second))
    assert "Legacy billing iso2" in other and "iso1" not in other


def test_figures_agree_with_the_product_screens(client, login_as, estate):
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.procurement.services import get_expiring_contracts
    from app.services.element_ownership_service import application_is_owned_expression

    org_id, user_id = estate
    report = _report(org_id)
    from app.models.user import User

    login_as(client, db.session.get(User, user_id))
    page = client.get("/dashboard/overview").get_data(as_text=True)
    assert report["inventory"]["total"] == int(_figure(page, "health-kpi-applications"))
    with tenant_scope(org_id):
        unowned = ApplicationComponent.query.filter(~application_is_owned_expression()).count()
        expiring = len(get_expiring_contracts(service.RENEWAL_WINDOW_DAYS))
    assert len(report["unowned"]["rows"]) == unowned
    assert len(report["renewals"]["rows"]) == expiring
    assert len(report["scores"]["scored"]) + len(report["scores"]["unscored"]) == report["inventory"]["total"]
    summary = " ".join(report["summary"])
    assert "%d applications are recorded" % report["inventory"]["total"] in summary
    assert "%d %s no accountable owner" % (unowned, "has" if unowned == 1 else "have") in summary


def test_the_written_report_names_no_internal_label_id_or_path(estate, tmp_path):
    org_id, _ = estate
    written = service.write_report(org_id, tmp_path)
    html = written["html"].read_text(encoding="utf-8")
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    assert "/*" not in html
    for banned in ("None", "null", "undefined", "unified_", "app/", ".py", "{{", "{%", "R1-B", "TODO"):
        assert banned not in text, banned
    assert written["pdf"].read_bytes().startswith(b"%PDF")
