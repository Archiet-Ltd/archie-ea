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
