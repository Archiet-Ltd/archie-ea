"""Shared fixtures and helpers for the Continue with Google / Microsoft tests.

Import the fixtures into a test module with
``from tests.support.social_sign_in import *  # noqa: F401,F403``.
"""
from urllib.parse import urlsplit

import pytest
import requests

from tests.support.fake_oidc_provider import CLIENT_ID, CLIENT_SECRET, FakeOIDCProvider

HOST = "entelim.tech"
OTHER_HOST = "entelim.org"

__all__ = [
    "HOST", "OTHER_HOST", "google_provider", "microsoft_provider", "social_config", "Browser",
    "providers_of", "CLIENT_ID", "CLIENT_SECRET",
]


@pytest.fixture(scope="session")
def google_provider():
    provider = FakeOIDCProvider("google")
    yield provider
    provider.close()


@pytest.fixture(scope="session")
def microsoft_provider():
    provider = FakeOIDCProvider("microsoft")
    yield provider
    provider.close()


def providers_of(google_provider, microsoft_provider):
    return {"google": google_provider, "microsoft": microsoft_provider}


@pytest.fixture
def social_config(app, monkeypatch, google_provider, microsoft_provider):
    """Both providers switched on and pointed at the stand-ins; returns the providers by key."""
    google_provider.reset()
    microsoft_provider.reset()
    cfg = app.config
    monkeypatch.setitem(cfg, "GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setitem(cfg, "GOOGLE_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setitem(cfg, "MICROSOFT_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setitem(cfg, "MICROSOFT_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setitem(cfg, "OAUTH_REDIRECT_HOSTS", "%s,%s" % (HOST, OTHER_HOST))
    monkeypatch.setitem(cfg, "OAUTH_TEST_METADATA_URL_GOOGLE", google_provider.metadata_url)
    monkeypatch.setitem(cfg, "OAUTH_TEST_METADATA_URL_MICROSOFT", microsoft_provider.metadata_url)
    monkeypatch.setitem(cfg, "ALLOW_INTERNAL_WEBHOOK_TARGETS", True)
    monkeypatch.setitem(cfg, "RATE_LIMITING_ENABLED", False)
    yield {"google": google_provider, "microsoft": microsoft_provider}


class Browser:
    """One person's browser: a cookie jar that talks to the app and to the stand-in provider."""

    def __init__(self, app, providers, host=HOST):
        self.app = app
        self.providers = providers
        self.host = host
        self.client = app.test_client()

    @property
    def base(self):
        return "https://%s" % self.host

    def get(self, path, **kw):
        return self.client.get(path, base_url=self.base, **kw)

    def post(self, path, data=None, **kw):
        return self.client.post(path, data=data or {}, base_url=self.base, **kw)

    def session(self):
        with self.client.session_transaction(base_url=self.base) as sess:
            return dict(sess)

    def set_session(self, **values):
        with self.client.session_transaction(base_url=self.base) as sess:
            sess.update(values)

    # ---- the three hops of a provider sign-in ---------------------
    def start(self, key, next_url=None):
        data = {"next": next_url} if next_url else {}
        return self.post("/account/oauth/%s/start" % key, data=data)

    def at_provider(self, start_response):
        """What the browser does at the provider's screen: follow the redirect, return the callback address."""
        assert start_response.status_code == 302, start_response.status_code
        hop = requests.get(start_response.headers["Location"], allow_redirects=False, timeout=10)
        assert hop.status_code == 302, hop.text
        return hop.headers["Location"]

    def callback(self, address):
        parts = urlsplit(address)
        return self.client.get(parts.path + ("?" + parts.query if parts.query else ""), base_url=self.base)

    def sign_in_with(self, key, claims, next_url=None, **misbehave):
        """The whole round trip. Returns the callback response (not followed)."""
        self.providers[key].queue(claims, **misbehave)
        started = self.start(key, next_url)
        return self.callback(self.at_provider(started))

    def password_sign_in(self, email, password):
        return self.post("/account/login", data={"email": email, "password": password})

    def is_signed_in(self):
        with self.client.session_transaction(base_url=self.base) as sess:
            return bool(sess.get("_user_id"))

    def signed_in_user_id(self):
        with self.client.session_transaction(base_url=self.base) as sess:
            raw = sess.get("_user_id")
        return int(raw) if raw else None
