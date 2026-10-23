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
