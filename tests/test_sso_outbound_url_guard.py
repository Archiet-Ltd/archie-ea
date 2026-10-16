"""SSO must not fetch internal addresses or send the client secret to them.

The metadata URL an administrator saves, and every URL read out of the fetched
discovery document (token, key set, userinfo), is checked with the shared
outbound guard before the server calls it, and no call follows redirects. The
HTTP layer is stubbed and asserted never called for refused targets; names
resolve through a stubbed resolver so nothing leaves the machine.
"""

from __future__ import annotations

import pytest

from test_sso_configuration_validation import _configuration_app

SECRET = "super-secret-client-value"

INTERNAL_URLS = [
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1:5432/",
    "https://localhost/x",
    "https://[::1]/x",
    "https://10.0.0.5/x",
    "https://192.168.1.10/x",
    "http://idp.example.com/x",  # not https
    "https://１２７.0.0.1/x",  # non-ASCII host
    "ftp://idp.example.com/x",
]


@pytest.fixture
def resolver(monkeypatch):
    """Production-like guard; ``resolver.answer`` is what every name resolves to."""
    import app.utils.ssrf_guard as guard

    class R:
        answer = "93.184.216.34"

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (R.answer, port))],
    )
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())
    return R


@pytest.fixture
def production_like(app, monkeypatch, resolver):
    monkeypatch.setitem(app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    with app.app_context():
        yield


@pytest.fixture
def http(monkeypatch):
    import requests

    class Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    class Calls(list):
        reply: dict = {}

    calls = Calls()

    def fake(method):
        def _call(url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return Resp(calls.reply)

        return _call

    monkeypatch.setattr(requests, "get", fake("GET"))
    monkeypatch.setattr(requests, "post", fake("POST"))
    return calls


def _svc():
    from app.services.sso_service import SSOService

    SSOService._discovery_cache.clear()
    SSOService._jwks_cache.clear()
    return SSOService()


def _config(url="https://idp.example.com/.well-known/openid-configuration"):
    from types import SimpleNamespace

    return SimpleNamespace(
        client_id="cid", client_secret=SECRET, idp_metadata_url=url, organization_id=1
    )


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_discovery_refuses_internal_metadata_url(production_like, http, url):
    from app.services.sso_service import SSONotConfiguredError

    with pytest.raises(SSONotConfiguredError) as exc:
        _svc()._fetch_oidc_discovery(url)
    assert http == []
    assert url not in str(exc.value) and "93.184" not in str(exc.value)


def test_discovery_refuses_name_resolving_inside(production_like, resolver, http):
    from app.services.sso_service import SSONotConfiguredError

    resolver.answer = "169.254.169.254"
    with pytest.raises(SSONotConfiguredError):
        _svc()._fetch_oidc_discovery("https://metadata.attacker.example/.well-known")
    assert http == []


def test_discovery_does_not_follow_redirects(production_like, http):
    http.reply = {"issuer": "https://idp.example.com"}
    doc = _svc()._fetch_oidc_discovery(
        "https://idp.example.com/.well-known/openid-configuration"
    )
    assert doc["issuer"] == "https://idp.example.com"
    assert http[0]["allow_redirects"] is False


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_token_endpoint_from_document_never_receives_the_secret(
    production_like, http, monkeypatch, url
):
    from app.services.sso_service import SSONotConfiguredError, SSOService

    doc = {"token_endpoint": url, "jwks_uri": "https://idp.example.com/jwks"}
    monkeypatch.setattr(SSOService, "_fetch_oidc_discovery", lambda self, u: doc)
    with pytest.raises(SSONotConfiguredError) as exc:
        _svc().handle_oidc_callback(_config(), "code", "state", "https://app.example.com/cb")
    assert http == []
    assert url not in str(exc.value)


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_userinfo_endpoint_from_document_is_not_called(
    production_like, http, monkeypatch, url
):
    from app.services.sso_service import SSOService

    doc = {"token_endpoint": "https://idp.example.com/token", "userinfo_endpoint": url}
    monkeypatch.setattr(SSOService, "_fetch_oidc_discovery", lambda self, u: doc)
    http.reply = {"access_token": "tok"}
    try:
        _svc().handle_oidc_callback(_config(), "code", "state", "https://app.example.com/cb")
    except Exception:  # noqa: BLE001 - no id_token either; only the calls matter
        pass
    assert [c["url"] for c in http] == ["https://idp.example.com/token"]
    assert http[0]["allow_redirects"] is False


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_jwks_uri_from_document_is_not_called(production_like, http, url):
    from app.services.sso_service import SSONotConfiguredError

    with pytest.raises(SSONotConfiguredError):
        _svc()._get_jwks(url)
    assert http == []


def test_public_identity_provider_flow_still_works(production_like, http, monkeypatch):
    from app.services.sso_service import SSOService

    doc = {
        "token_endpoint": "https://login.example-idp.com/token",
        "userinfo_endpoint": "https://graph.example-idp.com/oidc/userinfo",
    }
    monkeypatch.setattr(SSOService, "_fetch_oidc_discovery", lambda self, u: doc)
    http.reply = {"access_token": "tok", "sub": "u1", "email": "a@b.example"}
    claims = _svc().handle_oidc_callback(
        _config(), "code", "state", "https://app.example.com/cb"
    )
    assert claims["sub"] == "u1"
    assert [c["method"] for c in http] == ["POST", "GET"]
    assert all(c["allow_redirects"] is False for c in http)


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_admin_save_refuses_internal_metadata_url(monkeypatch, resolver, url):
    application, state = _configuration_app(monkeypatch)
    application.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = False
    before = dict(state.persisted)
    client = application.test_client()
    client.get("/test-entry")
    resp = client.post(
        "/admin/sso",
        data={
            "protocol": "oidc",
            "enabled": "on",
            "email_domain": "x.example",
            "idp_metadata_url": url,
        },
        follow_redirects=True,
    )
    assert b"SSO configuration saved." not in resp.data
    assert b"That address is not allowed or could not be reached" in resp.data
    assert state.persisted == before


def test_admin_save_accepts_public_metadata_url(monkeypatch, resolver):
    application, state = _configuration_app(monkeypatch)
    application.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = False
    client = application.test_client()
    client.get("/test-entry")
    resp = client.post(
        "/admin/sso",
        data={
            "protocol": "oidc",
            "enabled": "on",
            "email_domain": "x.example",
            "idp_metadata_url": "https://idp.example.com/.well-known/openid-configuration",
        },
        follow_redirects=True,
    )
    assert b"SSO configuration saved." in resp.data


@pytest.mark.parametrize(
    "value",
    [
        "http://www.okta.com/exk1abc2DEFghi3jk4l5",
        "http://adfs.corp.example/adfs/services/trust",
        "urn:federation:MicrosoftOnline",
    ],
)
def test_saml_save_keeps_reference_only_metadata_values(monkeypatch, resolver, value):
    application, state = _configuration_app(monkeypatch)
    application.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = False
    client = application.test_client()
    client.get("/test-entry")
    resp = client.post(
        "/admin/sso",
        data={"protocol": "saml", "email_domain": "x.example", "idp_metadata_url": value},
        follow_redirects=True,
    )
    assert b"SSO configuration saved." in resp.data
    assert state.persisted["idp_metadata_url"] == value


def test_unresolvable_host_gives_the_same_answer_as_a_refused_one(production_like, http, monkeypatch):
    import socket

    import app.utils.ssrf_guard as guard
    from app.services.sso_service import SSONotConfiguredError

    def nxdomain(host, port, *a, **k):
        raise socket.gaierror(11001, "no such host")

    monkeypatch.setattr(guard.socket, "getaddrinfo", nxdomain)
    with pytest.raises(SSONotConfiguredError) as exc:
        _svc()._fetch_oidc_discovery("https://nope.idp.example.com/.well-known")
    assert str(exc.value) == "That address is not allowed or could not be reached"
    assert http == []


def test_internal_address_says_not_allowed(production_like, http):
    from app.services.sso_service import SSONotConfiguredError

    with pytest.raises(SSONotConfiguredError) as exc:
        _svc()._fetch_oidc_discovery("https://169.254.169.254/x")
    assert str(exc.value) == "That address is not allowed or could not be reached"


def test_invalid_document_has_its_own_message(production_like, http):
    from app.services.sso_service import SSONotConfiguredError

    http.reply = ["not", "an", "object"]
    with pytest.raises(SSONotConfiguredError) as exc:
        _svc()._fetch_oidc_discovery("https://idp.example.com/.well-known")
    assert str(exc.value) == (
        "The identity provider did not return a valid configuration document"
    )
