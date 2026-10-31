"""The Vendors page, its Mapping tab and the vendor page answer from one register.

Two real organisations throughout: neither may see, or change, the other's.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest

from app.models.application_portfolio import ApplicationComponent, VendorContract
from app.models.vendor.vendor_organization import VendorOrganization

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, role):
    from app.models.user import Role, User

    user = User(email=f"vendor-{uuid.uuid4().hex[:8]}@example.com", first_name="V", last_name="P",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    user.password = "Sup3rSecret!23"
    user.role = Role.query.filter_by(name="Architect").first()
    db_session.add(user)
    db_session.flush()
    return user


def _app(db_session, org, name, vendor=None, cost=None):
    application = ApplicationComponent(name=name, organization_id=org.id, vendor_name=vendor,
                                       total_cost_of_ownership=cost, lifecycle_status="operational")
    db_session.add(application)
    db_session.flush()
    return application


@pytest.fixture
def two_orgs(db_session, make_org):
    tag = uuid.uuid4().hex[:6]
    mine, other = make_org("mine"), make_org("other")
    sf1 = _app(db_session, mine, f"Sales Cloud {tag}", f"Salesforce {tag}", 12000)
    sf2 = _app(db_session, mine, f"Service Cloud {tag}", f"Salesforce {tag}", 3000)
    _app(db_session, mine, f"Identity {tag}", f"Okta {tag}")
    loose = _app(db_session, mine, f"Loose {tag}")
    datadog = VendorOrganization(name=f"Datadog {tag}", named_by_organization_id=mine.id)
    db_session.add(datadog)
    db_session.flush()
    db_session.add(VendorContract(
        contract_name="Monitoring", organization_id=mine.id, vendor_id=datadog.id,
        start_date=date.today() - timedelta(days=300), end_date=date.today() + timedelta(days=20),
        annual_cost=9000.0, status="active"))
    their = _app(db_session, other, f"Their CRM {tag}", f"Okta {tag}", 777)
    _app(db_session, other, f"Their Secret {tag}", f"OnlyTheirs {tag}")
    db_session.flush()
    return {"tag": tag, "mine": mine, "other": other, "sf": (sf1, sf2), "loose": loose,
            "their": their, "datadog": datadog}


def test_page_answer_table_and_vendor_page_agree(client, login_as, db_session, two_orgs):
    world, tag = two_orgs, two_orgs["tag"]
    user = _user(db_session, world["mine"], "procurement")
    login_as(client, user)
    # A decommissioned system is not one of "your systems" anywhere on the page: the
    # answer sentence and the data-quality line must count the same systems.
    db_session.add(ApplicationComponent(name=f"Retired {tag}", organization_id=world["mine"].id,
                                        lifecycle_status="5. Decommissioned"))
    db_session.flush()

    api = client.get("/api/vendors/register").get_json()
    names = [v["name"] for v in api["vendors"]]
    assert api["total"] == 3 and set(names) == {f"Salesforce {tag}", f"Okta {tag}", f"Datadog {tag}"}
    assert names[0] == f"Datadog {tag}", "the renewal due within 90 days comes first"
    sf = next(v for v in api["vendors"] if v["name"] == f"Salesforce {tag}")
    assert sf["annual_cost"] == 15000 and sf["system_count"] == 2
    assert f"OnlyTheirs {tag}" not in str(api)

    page = client.get("/applications/vendors").get_data(as_text=True)

    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))

    # The register is one table: three vendors, one row each, and no row for
    # the other organisation's vendor.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page, "html.parser")
    rows = {}
    for row in soup.select('[data-testid="vendor-row"]'):
        rows[row.select_one('[data-testid="vendor-link"]').get_text(strip=True)] = row
    assert len(soup.select('[data-testid="vendor-row"]')) == 3
    assert set(rows) == set(names)

    # three vendors supply three of this organisation's four systems: the
    # table says what is left over, and Salesforce's own row counts its two
    assert "1 of your 4 systems do not name their vendor yet" in text
    answer = re.search(r"supply (\d+) of your (\d+) systems", text)
    quality = re.search(r"(\d+) of your (\d+) systems do not name their vendor yet", text)
    assert answer and quality, text[:400]
    supplied, total = int(answer.group(1)), int(answer.group(2))
    unmapped, total_again = int(quality.group(1)), int(quality.group(2))
    assert total == total_again == 4, "the answer and the data-quality line count the same systems"
    assert supplied + unmapped == total
    systems_link = rows[f"Salesforce {tag}"].select_one('a[href$="#vendor-systems"]')
    assert systems_link is not None and systems_link.get_text(strip=True) == "2 systems"
    # exactly one renewal is due, and it is the monitoring contract's vendor
    assert "Due within 90 days" in rows[f"Datadog {tag}"].get_text(" ", strip=True)
    assert sum("Due within 90 days" in r.get_text(" ", strip=True) for r in rows.values()) == 1
    assert f"Their CRM {tag}" not in page and f"OnlyTheirs {tag}" not in page

    detail = client.get(f"/applications/vendors/{sf['id']}").get_data(as_text=True)
    detail_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", detail))
    assert "2 of your systems" in detail_text
    assert f"Sales Cloud {tag}" in detail and f"Service Cloud {tag}" in detail
    assert f"Their CRM {tag}" not in detail


def test_old_mapping_address_lands_on_the_tab(client, login_as, db_session, two_orgs):
    login_as(client, _user(db_session, two_orgs["mine"], "procurement"))
    response = client.get("/vendor-archimate-analysis")
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/applications/vendors?tab=mapping")


def test_setting_a_vendor_moves_the_system_off_the_unmapped_list(client, login_as, db_session, two_orgs):
    world, tag = two_orgs, two_orgs["tag"]
    login_as(client, _user(db_session, world["mine"], "procurement"))
    page = client.get("/applications/vendors?tab=mapping").get_data(as_text=True)
    assert f"Loose {tag}" in page
    response = client.post("/api/vendors/map-system",
                           json={"application_id": world["loose"].id, "vendor_name": f"Okta {tag}"})
    assert response.status_code == 200, response.get_data(as_text=True)
    login_as(client, _user(db_session, world["mine"], "procurement"))
    page = client.get("/applications/vendors?tab=mapping").get_data(as_text=True)
    assert 'data-testid="mapping-row"' not in page and f"Loose {tag}" not in page
    assert "Every one of your 4 systems has a vendor recorded." in re.sub(r"\s+", " ", page)
    okta = next(v for v in client.get("/api/vendors/register").get_json()["vendors"]
                if v["name"] == f"Okta {tag}")
    assert okta["system_count"] == 2
    # one record for "Okta" shared by both organisations, each counting only its own systems
    assert VendorOrganization.query.filter(VendorOrganization.name == f"Okta {tag}").count() == 1


def test_one_organisation_cannot_set_the_vendor_of_anothers_system(client, login_as, db_session, two_orgs):
    world, tag = two_orgs, two_orgs["tag"]
    login_as(client, _user(db_session, world["mine"], "procurement"))
    response = client.post("/api/vendors/map-system",
                           json={"application_id": world["their"].id, "vendor_name": f"Hijack {tag}"})
    assert response.status_code == 404
    assert VendorOrganization.query.filter_by(name=f"Hijack {tag}").count() == 0
    db_session.refresh(world["their"])
    assert world["their"].vendor_name == f"Okta {tag}"


def test_a_role_without_procurement_access_cannot_set_a_vendor(client, login_as, db_session, two_orgs):
    world, tag = two_orgs, two_orgs["tag"]
    user = _user(db_session, world["mine"], "security_architect")
    login_as(client, user)
    response = client.post("/api/vendors/map-system",
                           json={"application_id": world["loose"].id, "vendor_name": f"Nope {tag}"})
    assert response.status_code == 403
    assert VendorOrganization.query.filter_by(name=f"Nope {tag}").count() == 0


def test_a_role_that_does_not_see_costs_gets_no_cost_figure(client, login_as, db_session, two_orgs):
    login_as(client, _user(db_session, two_orgs["mine"], "security_architect"))
    rows = client.get("/api/vendors/register").get_json()["vendors"]
    assert rows and all(r["annual_cost"] is None and r["cost_visible"] is False for r in rows)


def test_an_organisation_with_no_systems_is_told_why(client, login_as, db_session, make_org):
    login_as(client, _user(db_session, make_org("empty"), "procurement"))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get("/applications/vendors").get_data(as_text=True)))
    assert "No vendors yet. Add a system or a contract that names its vendor and it is listed here." in text
    assert 'data-testid="vendors-true-empty-state"' in client.get("/applications/vendors").get_data(as_text=True)
