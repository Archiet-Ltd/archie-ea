"""Vendors found from the organisation's website, its domain records and what a person tells the assistant.

A detection is a proposal in the one approvals inbox and nothing else. Approving it records the vendor, the
vendor product, the application it supplies and the catalogue link, in that organisation only, and proposes
the catalogue's architecture as a second change. Rejecting it leaves nothing and it is not proposed again.
Every lookup here reads recorded pages and DNS answers; nothing goes to the network.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# A startup's home page as fetched: Stripe checkout and the Intercom widget load from the vendors' own
# addresses; Okta is only mentioned in the text, which must not count.
HOME_PAGE = """<html><head><title>Lantern Quay</title>
<script src="https://js.stripe.com/v3/"></script>
<script async src="https://widget.intercom.io/widget/abc123"></script>
</head><body><p>Sign in with Okta coming soon.</p><a href="/pricing">Pricing</a></body></html>"""
PRICING_PAGE = """<html><body><form action="https://checkout.stripe.com/pay/cs_test"></form></body></html>"""

# The domain's records as a resolver returned them.
DNS_ANSWERS = {
    ("lanternquay.example", "MX"): ["1 aspmx.l.google.com.", "5 alt1.aspmx.l.google.com."],
    ("lanternquay.example", "TXT"): ["v=spf1 include:_spf.google.com ~all", "okta-verification=5b2c1d"],
    ("lanternquay.example", "NS"): ["kate.ns.cloudflare.com.", "rob.ns.cloudflare.com."],
}


def recorded_resolver(name, record_type):
    return list(DNS_ANSWERS.get((name, record_type), []))


def _catalogue(db_session, slug, product_name, vendor_name):
    """The catalogue product for ``slug`` with published facts, as the reviewed catalogue holds it."""
    from app.models.vendor.vendor_catalogue import VendorCatalogueFact
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.models.vendor.vendor_product import VendorProductDetail, VendorProductFamily

    product = VendorProductDetail.query.filter_by(catalogue_slug=slug).first()
    if product is None:
        vendor = VendorOrganization.query.filter_by(name=vendor_name).first()
        if vendor is None:
            vendor = VendorOrganization(name=vendor_name)
            db_session.add(vendor)
            db_session.flush()
        family = VendorProductFamily(vendor_id=vendor.id, family_name=f"{product_name} {uuid.uuid4().hex[:6]}",
                                     family_code=f"T-{uuid.uuid4().hex[:8]}")
        db_session.add(family)
        db_session.flush()
        product = VendorProductDetail(family_id=family.id, product_name=product_name, catalogue_slug=slug)
        db_session.add(product)
        db_session.flush()
    else:
        # A database that has had the reviewed catalogue loaded holds this product already: within this test's
        # transaction it reads as the test's own product, its earlier facts no longer published.
        product.product_name = product_name
        for fact in VendorCatalogueFact.query.filter_by(product_detail_id=product.id, review_state="published"):
            fact.review_state = "draft"
        db_session.flush()
    published = VendorCatalogueFact.query.filter_by(product_detail_id=product.id, review_state="published")
    if published.filter(VendorCatalogueFact.archimate_type.isnot(None)).count() == 0:
        for kind, name, atype in (("summary", "What it does", None),
                                  ("component", "Admin console", "ApplicationComponent"),
                                  ("interface", "REST API", "ApplicationInterface"),
                                  ("service", "Single sign-on", "ApplicationService")):
            fact = VendorCatalogueFact.query.filter_by(product_detail_id=product.id, kind=kind, name=name).first()
            if fact is None:
                fact = VendorCatalogueFact(product_detail_id=product.id, kind=kind, name=name)
                db_session.add(fact)
            fact.archimate_type, fact.detail, fact.evidence = atype, f"{product_name}: {name}", name.lower()
            fact.source_url, fact.checked_on = f"https://example.com/{slug}", date(2026, 10, 25)
            db_session.flush()
            fact.publish("Catalogue review")
        db_session.flush()
    return product


@pytest.fixture
def catalogue(db_session):
    return {
        slug: _catalogue(db_session, slug, name, vendor)
        for slug, name, vendor in (
            ("stripe", "Stripe", "Stripe"),
            ("intercom", "Intercom", "Intercom"),
            ("okta", "Okta Workforce Identity", "Okta"),
            ("google-workspace", "Google Workspace", "Google"),
            ("cloudflare", "Cloudflare", "Cloudflare"),
            ("slack", "Slack", "Salesforce"),
            ("datadog", "Datadog", "Datadog"),
        )
    }


def _user(db_session, org, role_name, permissions):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        role = Role(name=role_name, permissions=permissions)
        db_session.add(role)
        db_session.flush()
    user = User(email=f"{role_name.lower()}-{uuid.uuid4().hex[:8]}@example.com", first_name="Ada",
                last_name="Okafor", organization_id=org.id, role=role, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def orgs(db_session, make_org):
    from app.models.user import Permission

    a, b = make_org("vendors-a"), make_org("vendors-b")
    return {
        "a": a, "b": b,
        "a_admin": _user(db_session, a, "Administrator", Permission.ADMINISTER),
        "a_viewer": _user(db_session, a, "Viewer", 0),
        "b_admin": _user(db_session, b, "Administrator", Permission.ADMINISTER),
    }


def _counts(org_id):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.vendor.vendor_catalogue import VendorCatalogueLink
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    return {
        "vendors": VendorOrganization.query.count(),
        "vendor_products": VendorProduct.query.count(),
        "applications": ApplicationComponent.query.filter_by(organization_id=org_id).count(),
        "links": VendorCatalogueLink.query.filter_by(organization_id=org_id).count(),
    }


def _pending(org_id, operation_type="vendor_detection"):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    return AIChatCRUDApproval.query.filter_by(organization_id=org_id, source_table="vendor_detection",
                                              operation_type=operation_type,
                                              status=ApprovalStatus.PENDING).all()


def _by_slug(org_id):
    return {json.loads(a.operation_payload)["catalogue_slug"]: a for a in _pending(org_id)}


def test_pages_find_the_products_they_load_not_the_ones_they_mention():
    from app.services.vendor_detection import detect_from_pages

    found = {d.slug: d for d in detect_from_pages([
        {"url": "https://lanternquay.example/", "html": HOME_PAGE},
        {"url": "https://lanternquay.example/pricing", "html": PRICING_PAGE},
    ])}
    assert set(found) == {"stripe", "intercom"}
    assert found["stripe"].evidence[0] == {"kind": "page", "url": "https://lanternquay.example/",
                                           "detail": "https://js.stripe.com/v3/"}
    assert len(found["stripe"].evidence) == 2, "both pages that load Stripe are evidence"


def test_domain_records_find_mail_identity_and_dns_providers():
    from app.services.vendor_detection import detect_from_dns, domain_of

    assert domain_of("https://www.lanternquay.example/about") == "lanternquay.example"
    found = {d.slug: d for d in detect_from_dns("lanternquay.example", recorded_resolver)}
    assert set(found) == {"google-workspace", "okta", "cloudflare"}
    assert found["okta"].evidence == [{"kind": "dns", "url": "", "detail": "TXT okta-verification=5b2c1d"}]


def test_a_detection_is_a_proposal_and_records_nothing(orgs, catalogue, tenant_ctx):
    from app.services.vendor_detection import detect_for_site

    org = orgs["a"]
    with tenant_ctx(org.id):
        before = _counts(org.id)
        result = detect_for_site(org.id, "https://www.lanternquay.example/", [
            {"url": "https://lanternquay.example/", "html": HOME_PAGE}], recorded_resolver)
        assert _counts(org.id) == before
        proposals = _by_slug(org.id)
    assert set(proposals) == {"stripe", "intercom", "google-workspace", "okta", "cloudflare"}
    assert {p["source"] for p in result["proposed"]} == {"website", "dns"}
    stripe = proposals["stripe"]
    assert stripe.entity_type == "vendor" and stripe.user_id is None
    assert "js.stripe.com" in stripe.summary and "your website" in stripe.summary
    payload = json.loads(stripe.operation_payload)
    assert payload["catalogue_product_id"] == catalogue["stripe"].id
    assert payload["evidence"][0]["url"] == "https://lanternquay.example/"


def test_the_same_product_is_never_proposed_twice(orgs, catalogue, tenant_ctx):
    from app.services.vendor_detection import detect_for_site, propose_from_names

    org = orgs["a"]
    pages = [{"url": "https://lanternquay.example/", "html": HOME_PAGE}]
    with tenant_ctx(org.id):
        detect_for_site(org.id, "https://lanternquay.example/", pages, recorded_resolver)
        again = detect_for_site(org.id, "https://lanternquay.example/", pages, recorded_resolver)
        said = propose_from_names(org.id, ["Stripe", "Slack"], words="we use Stripe and Slack")
        assert again["proposed"] == []
        assert "Stripe" in said["known"]
        assert [p["product"] for p in said["proposed"]] == ["Slack"]
        assert len([a for a in _pending(org.id) if json.loads(a.operation_payload)["catalogue_slug"] == "stripe"]) == 1


def test_the_people_who_decide_are_told_and_the_bell_opens_the_inbox(orgs, catalogue, tenant_ctx):
    from app.models.models import Notification
    from app.services.vendor_detection import INBOX_PATH, detect_for_site

    org = orgs["a"]
    with tenant_ctx(org.id):
        detect_for_site(org.id, "https://lanternquay.example/", [
            {"url": "https://lanternquay.example/", "html": HOME_PAGE}], recorded_resolver)
        admin_bell = Notification.query.filter_by(user_id=orgs["a_admin"].id, event_type="vendor_detection").all()
        viewer_bell = Notification.query.filter_by(user_id=orgs["a_viewer"].id, event_type="vendor_detection").all()
    assert len(admin_bell) == 1
    assert admin_bell[0].url == INBOX_PATH == "/ai-chat/approvals/inbox?source=vendor_detection"
    assert admin_bell[0].message.startswith("5 vendors your organisation appears to use: ")
    assert viewer_bell == [], "a Viewer cannot decide proposals, so is not asked to"


def test_approving_records_the_vendor_product_and_link_in_that_organisation_only(
        orgs, catalogue, tenant_ctx, client, login_as):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.vendor.vendor_catalogue import VendorCatalogueLink
    from app.services import vendor_register
    from app.services.vendor_detection import propose_from_names

    org, other = orgs["a"], orgs["b"]
    with tenant_ctx(org.id):
        propose_from_names(org.id, ["Okta"], words="we use Okta")
        approval_id = _by_slug(org.id)["okta"].id
    login_as(client, orgs["a_admin"])
    resp = client.post(f"/ai-chat/approvals/{approval_id}/approve")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_json()
    assert body["success"] is True

    with tenant_ctx(org.id):
        app_row = ApplicationComponent.query.filter_by(organization_id=org.id, name="Okta Workforce Identity").one()
        assert app_row.lifecycle_status == "operational"
        assert app_row.primary_vendor_product is not None
        assert app_row.primary_vendor_product.vendor_organization.name == "Okta"
        link = VendorCatalogueLink.query.filter_by(organization_id=org.id,
                                                   vendor_product_id=app_row.vendor_product_id).one()
        assert link.product_detail_id == catalogue["okta"].id
        assert app_row.vendor_organization_id in vendor_register.vendor_ids(org.id)
        assert len(_pending(org.id, "vendor_architecture")) == 1
    with tenant_ctx(other.id):
        assert ApplicationComponent.query.filter_by(organization_id=other.id, name="Okta Workforce Identity").count() == 0
        assert VendorCatalogueLink.query.filter_by(organization_id=other.id).count() == 0
        assert _pending(other.id, "vendor_architecture") == []


def test_the_architecture_is_its_own_approval_and_cites_the_catalogue(
        orgs, catalogue, tenant_ctx, client, login_as):
    from app.models import ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.services.vendor_detection import propose_from_names

    org = orgs["a"]
    with tenant_ctx(org.id):
        propose_from_names(org.id, ["Okta"])
        approval_id = _by_slug(org.id)["okta"].id
    login_as(client, orgs["a_admin"])
    assert client.post(f"/ai-chat/approvals/{approval_id}/approve").get_json()["success"] is True
    with tenant_ctx(org.id):
        before = ArchiMateElement.query.filter_by(organization_id=org.id).count()
        architecture = _pending(org.id, "vendor_architecture")[0]
        assert "Okta Workforce Identity" in architecture.summary
    login_as(client, orgs["a_admin"])
    body = client.post(f"/ai-chat/approvals/{architecture.id}/approve").get_json()
    assert body["success"] is True, body
    with tenant_ctx(org.id):
        app_row = ApplicationComponent.query.filter_by(organization_id=org.id, name="Okta Workforce Identity").one()
        added = ArchiMateElement.query.filter_by(organization_id=org.id).count() - before
        assert added == 3
        api = ArchiMateElement.query.filter_by(organization_id=org.id, name="Okta Workforce Identity REST API").one()
        assert api.type == "ApplicationInterface"
        assert (api.custom_properties or {}).get("vendor_catalogue", {}).get("source_url") == "https://example.com/okta"
        links = ArchiMateRelationship.query.filter_by(organization_id=org.id, source_id=app_row.archimate_element_id).all()
        assert {(r.type, r.target_id) for r in links} >= {("Composition", api.id)}
        assert {r.type for r in links} >= {"Composition", "Realization"}


def test_rejecting_leaves_nothing_and_it_is_not_proposed_again(orgs, catalogue, tenant_ctx, client, login_as):
    from app.services.vendor_detection import propose_from_names

    org = orgs["a"]
    with tenant_ctx(org.id):
        propose_from_names(org.id, ["Datadog"])
        approval_id = _by_slug(org.id)["datadog"].id
        before = _counts(org.id)
    login_as(client, orgs["a_admin"])
    resp = client.post(f"/ai-chat/approvals/{approval_id}/reject", json={"reason": "We moved off Datadog"})
    assert resp.get_json()["success"] is True
    with tenant_ctx(org.id):
        assert _counts(org.id) == before
        again = propose_from_names(org.id, ["Datadog"])
    assert again["proposed"] == [] and again["known"] == ["Datadog"]


def test_another_organisation_cannot_see_or_decide_the_proposal(orgs, catalogue, tenant_ctx, client, login_as):
    from app.services.vendor_detection import propose_from_names

    org = orgs["a"]
    with tenant_ctx(org.id):
        propose_from_names(org.id, ["Slack"])
        approval_id = _by_slug(org.id)["slack"].id
        before = _counts(org.id)
    login_as(client, orgs["b_admin"])
    queue = client.get("/ai-chat/approvals/queue").get_json()
    assert approval_id not in [a["id"] for a in queue.get("approvals", [])]
    resp = client.post(f"/ai-chat/approvals/{approval_id}/approve")
    assert resp.status_code == 404
    with tenant_ctx(org.id):
        assert _counts(org.id) == before
        assert len(_pending(org.id)) == 1


def test_the_assistant_turns_named_products_into_proposals(orgs, catalogue, tenant_ctx):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    org, user = orgs["a"], orgs["a_admin"]
    with tenant_ctx(org.id):
        before = _counts(org.id)
        result = ToolExecutor(user.id).execute(ToolCall(
            id="t1", name="propose_vendors",
            arguments={"names": ["Okta", "Slack", "Datadog", "Acme Billing"],
                       "request": "we use Okta, Slack and Datadog, and Acme Billing"}))
        assert result["success"] is True, result
        assert sorted(p["product"] for p in result["proposed"]) == ["Datadog", "Okta Workforce Identity", "Slack"]
        assert result["not_in_catalogue"] == ["Acme Billing"]
        assert _counts(org.id) == before
        okta = _by_slug(org.id)["okta"]
    payload = json.loads(okta.operation_payload)
    assert payload["source"] == "chat" and payload["requested_by"] == user.id
    assert payload["evidence"][0]["detail"] == "we use Okta, Slack and Datadog, and Acme Billing"


def test_the_crawl_proposes_vendors_from_the_pages_it_read(orgs, catalogue, tenant_ctx, monkeypatch):
    from app.modules.organization.website_crawl import service

    org = orgs["a"]
    seen = {}

    def fake_detect(org_id, site, pages, resolver=None):
        seen.update(org_id=org_id, site=site, urls=[p["url"] for p in pages], resolver=resolver)
        return {"proposed": [], "known": [], "not_in_catalogue": []}

    monkeypatch.setattr("app.services.vendor_detection.detect_for_site", fake_detect)
    service._detect_vendors(org.id, "https://lanternquay.example/",
                            [{"url": "https://lanternquay.example/", "html": HOME_PAGE}])
    assert seen["org_id"] == org.id and seen["urls"] == ["https://lanternquay.example/"]
    assert seen["resolver"]("lanternquay.example", "MX") == [], "tests never look up live DNS"


# A hostile page: every vendor address on it is a link, a javascript: or data: address, text, an address on
# another host that only names the vendor, or a stylesheet. None of it is a product the page loads.
HOSTILE_PAGE = """<html><head><link rel="stylesheet" href="https://widget.intercom.io/widget.css"></head><body>
<a href="javascript:window.__xss2=1//widget.intercom.io/widget/abc">Chat with us</a>
<a href="https://js.stripe.com/v3/">How we take payments</a>
<p>Payments by js.stripe.com, chat by widget.intercom.io and designs at figma.com/embed.</p>
<script src="https://evil.example/load?next=https://js.stripe.com/v3/"></script>
<script src="https://js.stripe.com.evil.example/v3/"></script>
<iframe src="javascript://widget.intercom.io/%0Aalert(1)"></iframe>
<iframe src="https://www.figma.com/file/abc"></iframe>
<form action="data:text/html,checkout.stripe.com"></form>
</body></html>"""


def test_only_web_addresses_a_script_frame_or_form_loads_are_evidence():
    from app.services.vendor_detection import detect_from_pages

    assert detect_from_pages([{"url": "https://lanternquay.example/", "html": HOSTILE_PAGE}]) == []
    loaded = detect_from_pages([{"url": "https://lanternquay.example/", "html": (
        '<script src="//widget.intercom.io/widget/abc"></script>'
        '<iframe src="https://www.figma.com/embed?url=x"></iframe>'
        '<form action="https://checkout.stripe.com/pay/cs_test"></form>')}])
    assert {d.slug: d.evidence[0]["detail"] for d in loaded} == {
        "intercom": "//widget.intercom.io/widget/abc",
        "figma": "https://www.figma.com/embed?url=x",
        "stripe": "https://checkout.stripe.com/pay/cs_test",
    }


def test_the_inbox_cards_say_what_was_found_where_and_by_whom_in_words(orgs, catalogue, tenant_ctx, client, login_as):
    from app.services.vendor_detection import detect_for_site, propose_from_names

    org, admin = orgs["a"], orgs["a_admin"]
    with tenant_ctx(org.id):
        detect_for_site(org.id, "https://lanternquay.example/", [
            {"url": "https://lanternquay.example/", "html": HOME_PAGE}], recorded_resolver)
        propose_from_names(org.id, ["Slack"], words="we use Slack", requested_by=admin.id)
    login_as(client, admin)
    queue = client.get("/ai-chat/approvals/queue").get_json()
    cards = {a["arguments"]["catalogue_slug"]: a for a in queue["approvals"]
             if a["operation_type"] == "vendor_detection"}
    assert set(cards) >= {"stripe", "okta", "slack"}
    words = {slug: (c["operation_label"], c["entity_label"], c["source_label"], c["requester"]["display_name"])
             for slug, c in cards.items()}
    assert words["stripe"] == ("Vendor found", "Vendor", "Found by reading your website", "Reading your website")
    assert words["okta"] == ("Vendor found", "Vendor", "Found in your domain records", "Your domain records")
    assert words["slack"] == ("Vendor found", "Vendor", "From your chat", "Ada Okafor")
    for card in queue["approvals"]:
        for text in (card["operation_label"], card["entity_label"], card["source_label"] or "",
                     card["requester"]["display_name"]):
            assert "_" not in text and "Unknown requester" not in text, card

