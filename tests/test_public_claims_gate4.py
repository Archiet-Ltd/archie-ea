"""Public claims gate: the five public pages promise no recommendations engine or simulation,
every paid-plan button reads "Talk to us" and leads to the contact form, and the form stores
a submission."""

import re
import uuid

import pytest

PAGES = ["/", "/about", "/features", "/pricing", "/architecture-health-check"]

BANNED = [
    "simulation",
    "scenario planning",
    "recommendations and",
    "buy startup",
    "buy team",
    "buy the health check",
    "plans you can buy",
    "early-access",
    "early access",
]


def _visible_text(html):
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).lower()


@pytest.mark.parametrize("path", PAGES)
def test_page_makes_no_banned_claims(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    text = _visible_text(resp.get_data(as_text=True))
    found = [phrase for phrase in BANNED if phrase in text]
    assert not found, f"{path} contains {found}"


def _buttons(html):
    found = {}
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.S):
        attrs, label = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        tid = re.search(r"data-testid=\"(buy-[a-z-]+)\"", attrs)
        href = re.search(r"href=\"([^\"]+)\"", attrs)
        if tid and href:
            found[tid.group(1)] = (label, href.group(1).replace("&amp;", "&"))
    return found


def test_paid_plan_buttons_say_talk_to_us_and_lead_to_the_contact_form(client):
    buttons = _buttons(client.get("/pricing").get_data(as_text=True))
    for key in ("startup", "team", "enterprise"):
        assert f"buy-{key}" in buttons, f"no button for {key}"
    for key, (label, href) in buttons.items():
        if key == "buy-community":
            assert label == "Start free"
            assert "account/register" in client.get(href).headers["Location"]
            continue
        assert label == "Talk to us", (key, label)
        assert "register" not in href and "checkout" not in href
        hop = client.get(href)
        assert hop.status_code == 302 and hop.headers["Location"].endswith("/contact"), (key, hop.headers)
        assert client.get("/contact").status_code == 200


def test_health_check_page_uses_talk_to_us(client):
    html = client.get("/architecture-health-check").get_data(as_text=True)
    assert "Talk to us" in html


def test_contact_form_stores_a_submission(client, db_session):
    from app.models.product_inquiry import ProductInquiry

    email = f"gate4-{uuid.uuid4().hex[:8]}@example.com"
    before = ProductInquiry.query.filter_by(email=email).count()
    resp = client.post(
        "/offers/inquire",
        data={"offer": "sales_enquiry", "family": "site", "slug": "contact", "email": email, "consent": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Thank you" in resp.get_data(as_text=True)
    assert ProductInquiry.query.filter_by(email=email, offer="sales_enquiry").count() == before + 1

# Phrases that were promised on public pages and are not delivered today. Each is
# checked against the page as a visitor reads it.
REMOVED = {
    # -- BEGIN removed phrases
    '/pricing': [
        'ai assistants that query your model',
        'save, share and export an answer',
        'scenarios and what-if',
        'import your model from files',
        'you keep your plan until',
        'cancelling stops the subscription',
        'changes the number of editor seats',
        'tax number',
        'produces an invoice',
        'invoice e-mail',
        'within two weeks of kickoff',
        'fixed-price services',
        'billing page',
        'billing portal',
    ],
    '/architecture-health-check': [
        'within two weeks of kickoff',
        'fixed-price',
        'price is fixed',
        'the clock starts at kickoff',
    ],
    '/team-annual-onboarding': [
        'pro-rata',
        'fixed-price assessment',
        'single sign-on',
        'export your data for 30 days',
        'from the billing page',
    ],
    '/': [
        'read access for your ai assistants opens',
        'read access for your own ai assistants',
        'every row shows its source',
    ],
    '/features': [
        'single sign-on',
        'plans you can change and cancel yourself',
        'audit and billing',
        'will be able to read the model securely',
        'cites its sources, explains itself',
        'every row shows its source',
        'shareable, reviewable and deliverable',
    ],
    '/about': [
        'cites the records it came from',
        'how it was worked out',
        'the same question gets the same answer everywhere',
        'opens read access for your own ai assistants',
        'opens in q1 2027',
    ],
    '/contact': [
        'live and priced',
    ],
    '/privacy': [
        'other organisations on the same instance cannot read it',
    ],
    '/vs/ardoq': [
        'fixed price and date',
    ],
    '/vs/avolution-abacus': [
        'fixed price and date',
    ],
    '/vs/bizzdesign-hopex': [
        'fixed price and date',
    ],
    '/vs/boc-adoit': [
        'fixed price and date',
        'connecting an outside assistant to your model is not something you can do today',
    ],
    '/vs/leanix': [
        'fixed price and date',
    ],
    '/vs/orbus-iserver': [
        'fixed price and date',
    ],
    '/vs/servicenow-apm': [
        'fixed price and date',
    ],
    '/vs/sparx-enterprise-architect': [
        'fixed price and date',
    ],
    '/modules/compliance-frameworks': [
        'data says against a framework',
    ],
    '/use-cases/duplicate-software-spend': [
        'spend recommendations (ai)',
    ],
    '/use-cases/business-case-for-the-cio': [
        'a ranked view of where to invest',
    ],
    # -- END removed phrases
}

REMOVED_CASES = [(path, phrase) for path, phrases in REMOVED.items() for phrase in phrases]


@pytest.mark.parametrize("path,phrase", REMOVED_CASES)
def test_undelivered_promise_is_gone(client, path, phrase):
    resp = client.get(path)
    assert resp.status_code == 200
    assert phrase not in _visible_text(resp.get_data(as_text=True)), f"{path} still says: {phrase}"


def test_jira_and_github_page_says_where_github_stands(client):
    resp = client.get("/use-cases/import-from-jira-and-github")
    assert resp.status_code == 200
    assert "github" in _visible_text(resp.get_data(as_text=True))
