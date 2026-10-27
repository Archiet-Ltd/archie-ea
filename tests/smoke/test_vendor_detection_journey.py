"""Journey: the website reading finds the vendors a startup uses, the bell tells its CTO, the CTO opens the
approvals inbox from the bell, reads where each vendor was found, confirms one, and finds it on Vendors with
its catalogue architecture proposed as the next change."""

import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HOME_PAGE = """<html><head><title>Lantern Quay</title>
<script src="https://js.stripe.com/v3/"></script>
<script async src="https://widget.intercom.io/widget/abc123"></script>
</head><body><p>Payments for harbour moorings.</p></body></html>"""
DNS_ANSWERS = {("lanternquay.example", "TXT"): ["okta-verification=5b2c1d"]}


def _seed(seeded):
    """Load the reviewed catalogue and run the detection step the website reading runs, on a recorded page
    and recorded DNS answers."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.jobs.tenant_safe_job import tenant_scope
        from app.models.user import User
        from app.models.vendor.vendor_product import VendorProductDetail
        from app.services import vendor_catalogue_service as svc
        from app.services.vendor_detection import detect_for_site

        svc.load_reviewed()
        org_id = User.query.filter_by(email=seeded["emails"]["cto"]).one().organization_id
        with tenant_scope(org_id):
            result = detect_for_site(
                org_id, "https://www.lanternquay.example/",
                [{"url": "https://lanternquay.example/", "html": HOME_PAGE}],
                lambda name, record_type: list(DNS_ANSWERS.get((name, record_type), [])))
        db.session.commit()
        stripe = VendorProductDetail.query.filter_by(catalogue_slug="stripe").one()
        return {"proposed": sorted(p["product"] for p in result["proposed"]), "stripe": stripe.product_name}


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_cto_confirms_a_vendor_found_on_the_website(page, live_server, seeded):
    seed = _seed(seeded)
    assert seed["stripe"] in seed["proposed"] and len(seed["proposed"]) == 3, seed

    _login(page, live_server, seeded["emails"]["cto"])
    page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.locator("main").get_by_text(seed["stripe"], exact=True).count() == 0, "nothing recorded before approval"

    # The bell says vendors were found and opens the inbox.
    # The header shows the badge by its display style; its count says how many are unread.
    page.wait_for_function(
        "() => { const b = document.getElementById('notif-badge');"
        " return b && getComputedStyle(b).display !== 'none'"
        " && document.getElementById('notif-badge-count').textContent.trim() === '1'; }",
        timeout=PAGE_TIMEOUT)
    page.locator("#notif-btn").click()
    alert = page.locator("a.notif-item", has_text="3 vendors your organisation appears to use")
    alert.wait_for(timeout=PAGE_TIMEOUT)
    alert.click()
    page.wait_for_url(lambda url: "/ai-chat/approvals/inbox" in url, timeout=PAGE_TIMEOUT)

    # Each proposal says where it was found.
    card = page.locator("[data-testid^=approval-card-]", has_text=seed["stripe"]).first
    card.wait_for(timeout=PAGE_TIMEOUT)
    assert "your website" in card.inner_text() and "js.stripe.com" in card.inner_text()
    okta = page.locator("[data-testid^=approval-card-]", has_text="okta-verification=5b2c1d")
    assert okta.count() == 1, "the DNS record is the evidence for Okta"
    # Every card is in words: no stored code and no unknown requester; each says where it was found.
    assert "Found by reading your website" in card.inner_text() and "Requested by: Reading your website" in card.inner_text()
    assert "Found in your domain records" in okta.inner_text()
    for text in page.locator("[data-testid^=approval-card-]").all_inner_texts():
        head = text.split("View technical detail")[0]
        assert "Unknown requester" not in head, head
        assert not re.search(r"\b[A-Za-z]+_[A-Za-z_]+\b", head.replace("okta-verification", "")), head

    card.get_by_role("button", name="Approve").click()
    card.wait_for(state="detached", timeout=PAGE_TIMEOUT)

    # The catalogue's architecture for it is the next proposal.
    page.reload(wait_until="domcontentloaded")
    page.locator("[data-testid^=approval-card-]",
                 has_text=f"Add the published architecture of {seed['stripe']} to your model").first.wait_for(timeout=PAGE_TIMEOUT)

    # The vendor is now one the organisation has.
    page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("main").get_by_text("Stripe", exact=True).first.wait_for(timeout=PAGE_TIMEOUT)
