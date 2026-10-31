"""Journey: a CTO asks where customer data flows and who is accountable at each
step. On the data architecture page they find every entity with the
applications that use it, open Customer's flow and read each hop's owner and
source, see which applications handle personal data, then ask the same thing
from their AI chat through the connector and get the same entities and hops."""

from __future__ import annotations

import json
import urllib.parse
import uuid

import pytest
import requests

from .conftest import PAGE_TIMEOUT
from .test_mcp_consent import (
    _CallbackCapture,  # noqa: F401  (imported for the fixture's module)
    _authorize_url,
    _login,
    _pkce_pair,
    _register_test_client,
    local_callback_server,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(seeded):
    """The CTO's organisation: Customer written by the CRM and read by Billing,
    copied in a batch to a profile the warehouse writes and reporting reads."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from flask import g

        from app.models.all_missing_models import DataLineage
        from app.models.application_portfolio import ApplicationComponent
        from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit
        from app.models.process_data import DataDomain, DataEntity
        from app.models.user import User
        from app.modules.architecture.services.data_architecture_service import record_entity_access

        cto = User.query.filter_by(email=seeded["emails"]["cto"]).one()
        org_id = cto.organization_id
        tag = uuid.uuid4().hex[:5]
        with app.test_request_context("/"):
            g.current_org_id = org_id
            domain = DataDomain(name=f"Customer data {tag}", organization_id=org_id,
                                data_steward="Customer Operations lead")
            db.session.add(domain)
            db.session.flush()
            customer = DataEntity(name=f"Customer {tag}", domain_id=domain.id, organization_id=org_id,
                                  contains_pii=True)
            profile = DataEntity(name=f"Customer profile {tag}", domain_id=domain.id,
                                 organization_id=org_id, contains_pii=True)
            apps = {n: ApplicationComponent(name=f"{n} {tag}", organization_id=org_id)
                    for n in ("CRM", "Billing", "Warehouse", "Reporting")}
            db.session.add_all([customer, profile, *apps.values()])
            db.session.flush()
            unit = OrganizationUnit(name=f"Sales Operations {tag}", organization_id=org_id)
            db.session.add(unit)
            db.session.flush()
            db.session.add(ApplicationOwnership(application_id=apps["CRM"].id, organization_unit_id=unit.id,
                                                ownership_type="business_owner", organization_id=org_id))
            record_entity_access(customer, apps["CRM"], ["C", "R", "U"])
            record_entity_access(customer, apps["Billing"], ["R"])
            record_entity_access(profile, apps["Warehouse"], ["C", "U"])
            record_entity_access(profile, apps["Reporting"], ["R"])
            db.session.add(DataLineage(name="nightly copy", organization_id=org_id, lineage_type="Batch",
                                       archimate_element_id=customer.archimate_element_id,
                                       target_archimate_element_id=profile.archimate_element_id))
            db.session.commit()
            return {"tag": tag, "customer_id": customer.id, "customer_element": customer.archimate_element_id,
                    "unit": unit.name}


def _connect_assistant(page, live_server, callback):
    client_id = _register_test_client(callback, live_server)
    verifier, challenge = _pkce_pair()
    page.goto(_authorize_url(live_server, client_id, callback, challenge, "mcp:read"),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.click('button[name="decision"][value="allow"]')
    page.wait_for_url(lambda url: "code=" in url, timeout=PAGE_TIMEOUT)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)["code"][0]
    return requests.post(live_server + "/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": callback,
        "client_id": client_id, "code_verifier": verifier, "resource": live_server + "/mcp",
    }, timeout=10).json()["access_token"]


def _ask(live_server, token, arguments):
    resp = requests.post(live_server + "/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ask_data_flow", "arguments": arguments},
    }, headers={"Authorization": "Bearer " + token}, timeout=60)
    assert resp.status_code == 200, resp.text[:300]
    return json.loads(resp.json()["result"]["content"][0]["text"])


def test_cto_finds_where_customer_data_flows_and_who_is_accountable(
    page, live_server, seeded, local_callback_server  # noqa: F811
):
    seed = _seed(seeded)
    tag = seed["tag"]
    _login(page, live_server, seeded["emails"]["cto"])

    page.goto(live_server + "/architecture/data-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    count_text = page.get_by_test_id("data-entity-count").inner_text()
    page.get_by_test_id("data-entities-link").click()
    page.wait_for_url(lambda url: "/architecture/data-entities" in url, timeout=PAGE_TIMEOUT)
    rows = page.locator("tr[data-testid^=data-entity-row-]")
    rows.first.wait_for(timeout=PAGE_TIMEOUT)
    entity_rows = rows.count()
    # The dashboard's count is the number of entities the list shows.
    assert count_text.startswith(f"{entity_rows} ")
    customer_row = page.get_by_test_id(f"data-entity-row-{seed['customer_id']}")
    assert "PII" in customer_row.inner_text()
    assert f"CRM {tag} creates, reads, updates" in customer_row.inner_text()
    assert "Customer Operations lead" in customer_row.inner_text()

    customer_row.get_by_test_id("data-entity-flow-link").click()
    flow = page.get_by_test_id("data-flow")
    flow.get_by_role("heading", name=f"Where Customer {tag} flows").wait_for(timeout=PAGE_TIMEOUT)
    hops = flow.locator("li[data-flow-hop]")
    hop_text = [hops.nth(i).inner_text() for i in range(hops.count())]
    assert [hops.nth(i).locator("span.font-semibold").first.inner_text().strip() for i in range(hops.count())] == [
        f"CRM {tag} creates, reads and updates it",
        f"Billing {tag} reads it",
        f"Copied to Customer profile {tag}",
        f"Warehouse {tag} creates and updates it",
        f"Reporting {tag} reads it",
    ]
    # Accountable at each step: the CRM's owning unit, the copy's steward.
    assert seed["unit"] in hop_text[0]
    assert "Steward: Customer Operations lead" in hop_text[2]
    assert "worked out" in page.get_by_test_id("data-flow-summary").inner_text()
    # Each hop's source opens beside it, from the keyboard as from the mouse.
    hops.nth(1).get_by_role("button", name="Source of this connection").focus()
    tip = hops.nth(1).get_by_role("tooltip")
    assert f"Worked out: CRM {tag} writes Customer {tag}, and Billing {tag} reads it" in tip.inner_text()
    hops.nth(2).get_by_role("button", name="Source of this connection").focus()
    assert "Copy recorded in the data lineage (Batch)" in hops.nth(2).get_by_role("tooltip").inner_text()

    personal = page.get_by_test_id("personal-data-list")
    for name in ("CRM", "Billing", "Warehouse", "Reporting"):
        assert personal.get_by_text(f"{name} {tag}", exact=True).count() == 1

    # The same question from the person's AI chat, through the connector.
    token = _connect_assistant(page, live_server, local_callback_server)
    overview = _ask(live_server, token, {})
    assert overview["entity_count"] == entity_rows
    assert {f"CRM {tag}", f"Billing {tag}", f"Warehouse {tag}", f"Reporting {tag}"} <= {
        a["name"] for a in overview["personal_data_applications"]}
    answer = _ask(live_server, token, {"element_id": seed["customer_element"]})
    assert answer["success"] is True
    body = answer["result"] if "result" in answer else answer
    names = [body["elements"][str(r["element_id"])]["name"] for r in body["rows"]]
    assert names == [f"CRM {tag}", f"Billing {tag}", f"Customer profile {tag}", f"Warehouse {tag}", f"Reporting {tag}"]
    assert body["truth_class"] == "derived"
    assert all(r["relation"]["provenance"]["text"] for r in body["rows"])
    assert any(c["type"] == "data_lineage" for c in body["citations"])
