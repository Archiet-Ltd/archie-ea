"""The code workbench connector "test connection" route must not be a port scanner.

Before the fix any solution member could post a base_url and have the server
GET it (following redirects) and learn "HTTP <status>" or the exception text.
These tests drive the real route with the HTTP layer stubbed.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

INTERNAL_URLS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:5432/",
    "http://localhost/admin",
    "http://[::1]/",
    "http://10.0.0.5/x",
    "http://192.168.1.10/x",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "http://１２７.0.0.1/",  # full-width digits
    "http://localhost。/",
]


@pytest.fixture(autouse=True)
def production_like(app, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setitem(app.config, "COMPLIANCE_APPROVED_CONNECTOR_TYPES", ("rest_api",))
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.text = ""
        self.headers = {}


@pytest.fixture
def http_calls(monkeypatch):
    import requests

    calls = []

    def fake_get(url, **kw):
        calls.append({"url": url, **kw})
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


@pytest.fixture
def scene(db_session, make_org, client, login_as):
    from werkzeug.security import generate_password_hash

    from app.models.solution_models import Solution
    from app.models.user import User

    org_a, org_b = make_org("cn-a"), make_org("cn-b")
    user = User(
        email=f"cn-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Cn",
        last_name="A",
        confirmed=True,
        organization_id=org_a.id,
        password_hash=generate_password_hash("x"),
    )
    db_session.add(user)
    db_session.flush()
    own = Solution(name="Own", organization_id=org_a.id, created_by_id=user.id)
    foreign = Solution(name="Foreign", organization_id=org_b.id, created_by_id=user.id)
    db_session.add_all([own, foreign])
    db_session.flush()

    class S:
        pass

    s = S()
    s.own, s.foreign, s.user = own, foreign, user

    def post(solution, url):
        login_as(client, user)
        return client.post(
            f"/solutions/{solution.id}/codegen/connectors/test",
            json={"connector_type": "rest_api", "credentials": {"base_url": url}},
        )

    s.post = post
    return s


@pytest.mark.parametrize("url", INTERNAL_URLS)
def test_internal_target_is_refused_and_never_called(scene, http_calls, url):
    resp = scene.post(scene.own, url)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is False
    assert http_calls == []
    # Nothing about the target, a status or the library's error text.
    assert "HTTP" not in body["message"]
    assert url not in body["message"]


def test_refused_and_unreachable_answer_the_same(scene, monkeypatch):
    import requests

    def boom(url, **kw):
        raise requests.exceptions.ConnectionError("Connection refused to 203.0.113.9")

    monkeypatch.setattr(requests, "get", boom)
    refused = scene.post(scene.own, "http://10.0.0.5/x").get_json()
    unreachable = scene.post(scene.own, "https://down.example.com/").get_json()
    assert refused == unreachable
    assert "203.0.113.9" not in unreachable["message"]


def test_name_resolving_inside_is_refused(scene, http_calls, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    body = scene.post(scene.own, "https://metadata.attacker.example/").get_json()
    assert body["success"] is False
    assert http_calls == []


def test_external_https_url_still_works_without_redirects(scene, http_calls):
    body = scene.post(scene.own, "https://api.example.com/health").get_json()
    assert body == {"success": True, "message": "HTTP 200"}
    assert len(http_calls) == 1
    assert http_calls[0]["url"] == "https://api.example.com/health"
    assert http_calls[0]["allow_redirects"] is False


def test_foreign_organisation_solution_is_not_found(scene, http_calls):
    resp = scene.post(scene.foreign, "https://api.example.com/health")
    assert resp.status_code == 404
    assert http_calls == []


def test_unknown_active_organisation_is_not_found(scene, http_calls, monkeypatch):
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    resp = scene.post(scene.own, "https://api.example.com/health")
    assert resp.status_code == 404
    assert http_calls == []


def test_sibling_route_also_fenced_to_active_organisation(scene, client, login_as):
    login_as(client, scene.user)
    resp = client.get(f"/solutions/{scene.foreign.id}/codegen/connectors")
    assert resp.status_code == 404
