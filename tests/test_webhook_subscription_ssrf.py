"""Generic webhook subscriptions (/api/webhooks) must not reach internal targets.

Before the fix any signed-in user could register a subscription for
http://169.254.169.254/ (cloud metadata), have the server POST to it with
redirects followed and caller-chosen headers, and read the answer back out of
the delivery record. These tests drive the real routes as two users in two
organisations, with the HTTP layer stubbed so nothing ever leaves the machine.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

METADATA_BODY = "iam-role-credentials-from-metadata-service"

INTERNAL_URLS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://127.0.0.1:5432/",
    "http://localhost/admin",
    "http://[::1]/",
    "http://10.0.0.5/hook",
    "http://192.168.1.10/hook",
    "http://0.0.0.0/",
    "http://2130706433/",  # 127.0.0.1 as one decimal number
    "http://0x7f.0.0.1/",
    "ftp://example.com/hook",
    "file:///etc/passwd",
]
# Hosts whose non-ASCII spelling is turned into an ASCII (internal) one by the
# HTTP client but looks like an unrelated name to a string check.
NON_ASCII_HOSTS = [
    "http://１２７.0.0.1/",  # full-width digits
    "http://127。0。0。1/",  # ideographic full stops
    "http://localhost。/",
]


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


@pytest.fixture
def world(db_session, make_org, client, login_as):
    org_a, org_b = make_org("a"), make_org("b")
    user_a, user_b = _user(db_session, org_a, "alice"), _user(db_session, org_b, "bob")

    class W:
        pass

    w = W()
    w.org_a, w.org_b, w.user_a, w.user_b = org_a, org_b, user_a, user_b

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
    """Behave as outside development, and resolve every name to a public address
    so a test never touches the network."""
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "internal_targets_allowed", lambda: False)
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


class _Resp:
    def __init__(self, status=200, text="ok", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


@pytest.fixture
def http_calls(monkeypatch):
    """Record outbound POSTs. ``http_calls.reply`` is what the stub answers."""
    import requests

    class Calls(list):
        reply = staticmethod(lambda: _Resp())

    calls = Calls()

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return calls.reply()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("unexpected GET"))
    return calls


def _create(client, **body):
    body.setdefault("events", ["*"])
    return client.post("/api/webhooks/subscriptions", json=body)


def _stored(org_id):
    from app.models.webhook import WebhookSubscription

    return WebhookSubscription.query.filter_by(organization_id=org_id).all()


def _insert_legacy(world, url, headers=None):
    """A row written before the checks existed."""
    from app import db
    from app.models.webhook import WebhookSubscription

    sub = WebhookSubscription(
        id=str(uuid.uuid4()),
        organization_id=world.org_a.id,
        user_id=str(world.user_a.id),
        url=url,
        events=["*"],
        secret="s" * 32,
        headers=headers or {},
        is_active=True,
    )
    db.session.add(sub)
    db.session.flush()
    return sub


# --- create ---------------------------------------------------------------------


@pytest.mark.parametrize("url", INTERNAL_URLS + NON_ASCII_HOSTS)
def test_create_refuses_internal_target(world, production_like, http_calls, url):
    resp = _create(world.as_a(), url=url)
    assert resp.status_code == 400, resp.get_json()
    assert _stored(world.org_a.id) == []
    assert http_calls == []
    # The refusal does not say what the name resolved to.
    assert "93.184.216.34" not in resp.get_data(as_text=True)


def test_create_refuses_name_that_resolves_inside(world, monkeypatch, http_calls):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "internal_targets_allowed", lambda: False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    resp = _create(world.as_a(), url="https://metadata.attacker.example/")
    assert resp.status_code == 400
    assert _stored(world.org_a.id) == []
    assert "169.254" not in resp.get_data(as_text=True)


def test_create_accepts_ordinary_external_url_and_sends_without_redirects(
    world, production_like, http_calls
):
    c = world.as_a()
    ok = _create(c, url="https://hooks.example.com/in")
    assert ok.status_code == 201, ok.get_json()
    sid = ok.get_json()["data"]["id"]

    sent = world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    assert sent.status_code == 200, sent.get_json()
    assert len(http_calls) == 1
    assert http_calls[0]["url"] == "https://hooks.example.com/in"
    assert http_calls[0]["allow_redirects"] is False


def test_create_without_active_organisation_is_refused(world, production_like, monkeypatch):
    import app.services.webhook_service as svc

    monkeypatch.setattr(svc, "_active_org_id", lambda: None)
    resp = _create(world.as_a(), url="https://hooks.example.com/in")
    assert resp.status_code == 403
    from app.models.webhook import WebhookSubscription

    assert WebhookSubscription.query.filter_by(user_id=str(world.user_a.id)).count() == 0


# --- headers --------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "internal.service"},
        {"host": "internal.service"},
        {"Content-Length": "0"},
        {"Transfer-Encoding": "chunked"},
        {"Connection": "keep-alive"},
        {"Upgrade": "websocket"},
        {"Proxy-Authorization": "Basic eA=="},
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Webhook-Signature": "forged"},
        {"X-Ok": "a\r\nHost: internal"},
        {"Bad Name": "x"},
        {"X-Num": 5},
        "not-an-object",
        {f"X-H{i}": "v" for i in range(25)},
    ],
)
def test_create_refuses_dangerous_headers(world, production_like, http_calls, headers):
    resp = _create(world.as_a(), url="https://hooks.example.com/in", headers=headers)
    assert resp.status_code == 400, resp.get_json()
    assert _stored(world.org_a.id) == []


def test_user_headers_cannot_replace_content_type_or_signature(world, production_like, http_calls):
    ok = _create(
        world.as_a(), url="https://hooks.example.com/in", headers={"X-Api-Key": "k-123"}
    )
    assert ok.status_code == 201, ok.get_json()
    sid = ok.get_json()["data"]["id"]
    # A stored row that predates the header check tries to override ours.
    from app import db
    from app.models.webhook import WebhookSubscription

    sub = WebhookSubscription.query.get(sid)
    sub.headers = {"X-Api-Key": "k-123", "Content-Type": "text/evil", "X-Webhook-Signature": "forged"}
    db.session.flush()
    world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    sent = http_calls[0]["headers"]
    assert sent["Content-Type"] == "application/json"
    assert sent["X-Webhook-Signature"] != "forged"


def test_allowed_custom_header_is_sent(world, production_like, http_calls):
    ok = _create(world.as_a(), url="https://hooks.example.com/in", headers={"X-Api-Key": "k-123"})
    sid = ok.get_json()["data"]["id"]
    world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    assert http_calls[0]["headers"]["X-Api-Key"] == "k-123"


# --- update ---------------------------------------------------------------------


@pytest.mark.parametrize("url", INTERNAL_URLS[:5] + NON_ASCII_HOSTS)
def test_update_refuses_internal_target_and_changes_nothing(world, production_like, http_calls, url):
    ok = _create(world.as_a(), url="https://hooks.example.com/in")
    sid = ok.get_json()["data"]["id"]
    resp = world.as_a().put(f"/api/webhooks/subscriptions/{sid}", json={"url": url, "description": "x"})
    assert resp.status_code == 400, resp.get_json()
    from app.models.webhook import WebhookSubscription

    sub = WebhookSubscription.query.get(sid)
    assert sub.url == "https://hooks.example.com/in"
    assert sub.description == ""  # nothing from the refused update was applied


def test_update_refuses_bad_headers(world, production_like):
    ok = _create(world.as_a(), url="https://hooks.example.com/in")
    sid = ok.get_json()["data"]["id"]
    resp = world.as_a().put(f"/api/webhooks/subscriptions/{sid}", json={"headers": {"Host": "x"}})
    assert resp.status_code == 400


# --- send time ------------------------------------------------------------------


@pytest.mark.parametrize("url", INTERNAL_URLS[:4])
def test_stored_internal_url_is_not_called_at_send_time(world, production_like, http_calls, url):
    sub = _insert_legacy(world, url)
    resp = world.as_a().post(f"/api/webhooks/subscriptions/{sub.id}/test")
    assert resp.status_code in (200, 404)
    assert http_calls == []
    from app.models.webhook import WebhookDelivery

    delivery = WebhookDelivery.query.filter_by(subscription_id=sub.id).first()
    assert delivery is not None and delivery.status == "failed"
    assert delivery.response_body is None
    assert delivery.error_message == "Target not permitted"


def test_name_that_starts_resolving_inside_is_not_called(world, monkeypatch, http_calls):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(guard, "internal_targets_allowed", lambda: False)
    addr = {"ip": "93.184.216.34"}
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (addr["ip"], port))],
    )
    sid = _create(world.as_a(), url="https://rebind.example.com/in").get_json()["data"]["id"]
    addr["ip"] = "169.254.169.254"  # the DNS record changes after acceptance
    world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    assert http_calls == []


# --- redirects and response content ---------------------------------------------


def test_redirect_to_metadata_is_not_followed_and_not_recorded(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(
        302, text=METADATA_BODY, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    )
    sid = _create(world.as_a(), url="https://hooks.example.com/in").get_json()["data"]["id"]
    resp = world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    body = resp.get_data(as_text=True)
    assert all(call["allow_redirects"] is False for call in http_calls)
    assert all("169.254" not in call["url"] for call in http_calls)
    assert METADATA_BODY not in body
    from app.models.webhook import WebhookDelivery

    delivery = WebhookDelivery.query.filter_by(subscription_id=sid).first()
    assert delivery.response_body is None
    assert METADATA_BODY not in (delivery.error_message or "")
    assert METADATA_BODY not in str(delivery.to_dict())


@pytest.mark.parametrize("status", [200, 500])
def test_remote_answer_is_never_stored_or_returned(world, production_like, http_calls, status):
    http_calls.reply = lambda: _Resp(status, text=METADATA_BODY)
    sid = _create(world.as_a(), url="https://hooks.example.com/in").get_json()["data"]["id"]
    resp = world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    from app.models.webhook import WebhookDelivery

    delivery = WebhookDelivery.query.filter_by(subscription_id=sid).first()
    assert delivery.response_status == status  # the status code is kept
    assert delivery.response_body is None
    assert METADATA_BODY not in (delivery.error_message or "")
    assert METADATA_BODY not in str(delivery.to_dict())
    assert METADATA_BODY not in resp.get_data(as_text=True)


def test_old_stored_response_body_is_not_returned(world, production_like):
    from app import db
    from app.models.webhook import WebhookDelivery

    sub = _insert_legacy(world, "https://hooks.example.com/in")
    d = WebhookDelivery(
        id=str(uuid.uuid4()), subscription_id=sub.id, organization_id=world.org_a.id,
        event_type="x", payload={}, status="failed", response_body=METADATA_BODY,
        error_message="x",
    )
    db.session.add(d)
    db.session.flush()
    assert METADATA_BODY not in str(d.to_dict())


def test_connection_error_text_is_generic(world, production_like, monkeypatch):
    import requests

    def boom(url, **kw):
        raise requests.ConnectionError("HTTPConnectionPool(host='10.1.2.3', port=5432): refused")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    sid = _create(world.as_a(), url="https://hooks.example.com/in").get_json()["data"]["id"]
    resp = world.as_a().post(f"/api/webhooks/subscriptions/{sid}/test")
    from app.models.webhook import WebhookDelivery

    delivery = WebhookDelivery.query.filter_by(subscription_id=sid).first()
    assert delivery.error_message == "Connection failed"
    assert "10.1.2.3" not in resp.get_data(as_text=True)


# --- ownership by the active organisation ---------------------------------------


def test_other_organisation_cannot_read_change_test_or_delete(world, production_like, http_calls):
    sid = _create(world.as_a(), url="https://hooks.example.com/in").get_json()["data"]["id"]
    b = world.as_b()
    assert b.get(f"/api/webhooks/subscriptions/{sid}").status_code == 404
    assert b.put(f"/api/webhooks/subscriptions/{sid}", json={"description": "x"}).status_code == 404
    assert b.post(f"/api/webhooks/subscriptions/{sid}/test").status_code == 404
    assert b.delete(f"/api/webhooks/subscriptions/{sid}").status_code == 404
    assert http_calls == []
    listed = b.get("/api/webhooks/subscriptions").get_json()["data"]
    assert sid not in [row["id"] for row in listed]
    from app.models.webhook import WebhookSubscription

    sub = WebhookSubscription.query.get(sid)
    assert sub.is_active and sub.description == ""
    # And the owner still sees it.
    assert sid in [r["id"] for r in world.as_a().get("/api/webhooks/subscriptions").get_json()["data"]]


def test_same_user_in_another_active_organisation_sees_nothing(world, production_like, monkeypatch):
    """A user whose active organisation is not the one the subscription belongs
    to (switched organisation) must not reach it through the user id alone."""
    sid = _create(world.as_a(), url="https://hooks.example.com/in").get_json()["data"]["id"]
    import app.services.webhook_service as svc

    monkeypatch.setattr(svc, "_active_org_id", lambda: world.org_b.id)
    c = world.as_a()
    assert c.get(f"/api/webhooks/subscriptions/{sid}").status_code == 404
    assert c.get("/api/webhooks/subscriptions").get_json()["data"] == []


def test_unknown_active_organisation_sees_nothing(world, production_like, monkeypatch):
    sid = _create(world.as_a(), url="https://hooks.example.com/in").get_json()["data"]["id"]
    import app.services.webhook_service as svc

    monkeypatch.setattr(svc, "_active_org_id", lambda: None)
    c = world.as_a()
    assert c.get(f"/api/webhooks/subscriptions/{sid}").status_code == 404
    assert c.get("/api/webhooks/subscriptions").get_json()["data"] == []
    assert c.delete(f"/api/webhooks/subscriptions/{sid}").status_code == 404
