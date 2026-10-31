"""The public site offers single sign-on and self-serve plans only once the released wave ships them."""

import re

import pytest

PAGES = ("/", "/features", "/pricing", "/team-annual-onboarding")
SSO_WORDS = re.compile(r"single sign-on|saml|openid connect", re.I)
SELF_SERVE = re.compile(
    r"buy, change and cancel|cancels from the billing page|from the billing page|Buy (Startup|Team), billed", re.I
)


def _text(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    html = resp.get_data(as_text=True)
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _sentences_with(pattern, text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if pattern.search(s)]
# Wider than SELF_SERVE: any sentence on any page that says a visitor can buy, change or cancel a plan.
PLAN_ACTION = re.compile(
    r"\b(buy|buys|buying|change|changes|changing|cancel|cancels|cancelling)\b[^.]{0,60}\b(plan|subscription)\b"
    r"|\bcancel (at any time|before)\b|\bcancelling stops\b|from the billing page|Buy (Startup|Team), billed",
    re.I,
)
DEFERRED = re.compile(r"coming|not yet|soon", re.I)


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "1")
    return app.test_client()


@pytest.mark.parametrize("path", PAGES)
def test_wave_one_offers_no_single_sign_on_and_no_self_serve_plan(client, monkeypatch, path):
    monkeypatch.setenv("RELEASE_WAVE", "1")
    text = _text(client, path)
    for sentence in _sentences_with(SSO_WORDS, text):
        assert re.search(r"coming|not yet|soon", sentence, re.I), f"{path}: {sentence}"
    assert not SELF_SERVE.search(text), path


def test_wave_one_pricing_says_plans_open_soon_and_keeps_every_price(client, monkeypatch):
    monkeypatch.setenv("RELEASE_WAVE", "1")
    text = _text(client, "/pricing")
    assert "Plans open for purchase soon" in text
    assert "free while it is in early access" in text
    for price in ("490", "290", "24,000"):
        assert price in text
    assert "Coming" in text


def test_wave_one_features_says_single_sign_on_is_coming(client, monkeypatch):
    monkeypatch.setenv("RELEASE_WAVE", "1")
    text = _text(client, "/features")
    assert "Single sign-on is coming" in text
    assert "SAML or OpenID Connect" not in text


@pytest.mark.parametrize("wave", ["4", None])
def test_wave_four_or_switch_off_the_lines_return(client, monkeypatch, wave):
    if wave is None:
        monkeypatch.delenv("RELEASE_WAVE", raising=False)
    else:
        monkeypatch.setenv("RELEASE_WAVE", wave)
    assert "single sign-on through SAML or OpenID Connect" in _text(client, "/features")
    assert "plans you can buy, change and cancel yourself" in _text(client, "/features")
    pricing = _text(client, "/pricing")
    assert "SAML sign-in" in pricing
    assert "Plans open for purchase soon" not in pricing
    assert "Single sign-on and the review-board workflow" in _text(client, "/")


def _every_served_public_page(client):
    """Every content page under content/pages that answers 200, by url, as body text."""
    from app.services.public_pages import load_all_pages

    served = {}
    for page in load_all_pages():
        resp = client.get(page.url)
        if resp.status_code == 200:
            html = resp.get_data(as_text=True)
            html = re.sub(r"<(head|script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
            served[page.url] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    return served


def test_wave_one_no_public_content_page_offers_single_sign_on_or_a_plan_purchase(client, monkeypatch):
    monkeypatch.setenv("RELEASE_WAVE", "1")
    served = _every_served_public_page(client)
    assert len(served) > 80, "the scan must cover the whole content tree"
    offending = []
    for url, text in sorted(served.items()):
        for sentence in _sentences_with(SSO_WORDS, text):
            if not DEFERRED.search(sentence):
                offending.append(f"{url}: {sentence}")
        for sentence in _sentences_with(PLAN_ACTION, text):
            if not sentence.rstrip().endswith("?"):
                offending.append(f"{url}: {sentence}")
    assert not offending, "\n".join(offending)


def test_wave_one_a_removed_span_never_runs_two_sentences_together(client, monkeypatch):
    monkeypatch.setenv("RELEASE_WAVE", "1")
    for url, text in _every_served_public_page(client).items():
        assert not re.search(r"[a-z][.!?][A-Z][a-z]", text.replace("e.g.", "").replace("i.e.", "")), url
