"""The Teams webhook address of the M365 connector is checked at save and at send.

Before the fix any organisation administrator could save an arbitrary address
and the server POSTed ARB decision cards to it (following redirects). These
tests stub the HTTP layer and assert it is never called for refused targets.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

GOOD = [
    "https://contoso.webhook.office.com/webhookb2/abc@def/IncomingWebhook/x/y",
    "https://prod-12.westeurope.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke",
    "https://outlook.office.com/webhook/abc@def/IncomingWebhook/x/y",
    "https://abc123.4f.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/w1/triggers/manual/paths/invoke?api-version=1",
]
REFUSED = [
    "http://169.254.169.254/latest/meta-data/",
    "https://169.254.169.254/",
    "https://127.0.0.1:5432/",
    "https://localhost/hook",
    "https://[::1]/hook",
    "https://10.0.0.5/hook",
    "https://example.com/hook",  # public but not Microsoft
    "https://evil.webhook.office.com.attacker.example/hook",
    "https://webhook.office.com@169.254.169.254/",
    "https://outlook.office.com/api/v2.0/me",  # not under /webhook
    "http://contoso.webhook.office.com/x",  # not https
    "https://contoso.webhook.office.com\\@10.0.0.5/",
    "https://contoso.ｗebhook.office.com/x",  # non-ASCII host
    "ftp://contoso.webhook.office.com/x",
    "https://evilenvironment.api.powerplatform.com/x",
    "https://environment.api.powerplatform.com.attacker.example/x",
    "https://outlook.office.com/webhookx/abc",
    "https://outlook.office.com/webhook/../api/v2.0/me",
    "https://outlook.office.com/webhook/./x",
    "https://outlook.office.com/webhook\@10.0.0.5/x",
]


@pytest.fixture(autouse=True)
def production_like(app, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())


@pytest.fixture
def http_calls(monkeypatch):
    import requests

    calls = []

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_post(url, **kw):
        calls.append({"url": url, **kw})
        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def _send(url):
    from app.services.m365_service import M365Service

    svc = M365Service()
    svc._get_config = lambda: object()  # a configured, enabled connector
    svc.send_teams_notification(1, url, "t", "m", "/x")


@pytest.mark.parametrize("url", REFUSED)
def test_send_refuses_non_microsoft_or_internal_target(http_calls, url):
    _send(url)
    assert http_calls == []


@pytest.mark.parametrize("url", GOOD)
def test_send_posts_to_microsoft_webhook_without_redirects(http_calls, url):
    _send(url)
    assert len(http_calls) == 1
    assert http_calls[0]["url"] == url
    assert http_calls[0]["allow_redirects"] is False


def test_send_refuses_microsoft_name_resolving_inside(http_calls, monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    _send(GOOD[0])
    assert http_calls == []


def test_extra_host_suffix_from_configuration_is_honoured(app, http_calls, monkeypatch):
    monkeypatch.setitem(app.config, "TEAMS_WEBHOOK_EXTRA_HOST_SUFFIXES", (".hooks.corp.example",))
    _send("https://teams.hooks.corp.example/in")
    assert len(http_calls) == 1


def test_extra_suffix_without_leading_dot_does_not_admit_lookalike(app, http_calls, monkeypatch):
    monkeypatch.setitem(app.config, "TEAMS_WEBHOOK_EXTRA_HOST_SUFFIXES", ["corp.example"])
    _send("https://evilcorp.example/in")
    assert http_calls == []
    _send("https://teams.corp.example/in")
    assert len(http_calls) == 1


def test_refused_send_is_logged_with_organisation(http_calls, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        _send("https://10.0.0.5/hook")
    assert http_calls == []
    assert any("organisation 1" in r.getMessage() for r in caplog.records)


# --- save route ---------------------------------------------------------------


@pytest.fixture
def admin(db_session, make_org):
    from app.models.user import Permission, Role, User

    org = make_org("m365")
    user = User(
        email=f"m365-{uuid.uuid4().hex[:8]}@example.com",
        first_name="M",
        last_name="Admin",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_administrator",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER, index="main", default=False)
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


@pytest.fixture
def proposals(monkeypatch):
    import app.services.connector_framework as cf

    calls = []

    def fake(**kw):
        calls.append(kw)
        return {"queued": False, "approval_id": None}

    monkeypatch.setattr(cf, "propose_or_apply_connector_change", fake)
    return calls


def _save(client, login_as, admin, url):
    login_as(client, admin)
    return client.post(
        "/admin/connectors/m365",
        data={
            "tenant_id": "t",
            "client_id": "c",
            "teams_webhook_url": url,
            "enabled": "1",
        },
    )


@pytest.mark.parametrize("url", REFUSED)
def test_save_refuses_and_stores_nothing(client, login_as, admin, proposals, url):
    resp = _save(client, login_as, admin, url)
    assert resp.status_code == 302
    assert proposals == []


def test_save_accepts_microsoft_webhook(client, login_as, admin, proposals):
    resp = _save(client, login_as, admin, GOOD[0])
    assert resp.status_code == 302
    assert len(proposals) == 1
    assert proposals[0]["payload"]["config"]["teams_webhook_url"] == GOOD[0]


def test_save_without_webhook_still_works(client, login_as, admin, proposals):
    resp = _save(client, login_as, admin, "")
    assert resp.status_code == 302
    assert len(proposals) == 1


def test_generic_connector_save_cannot_carry_the_webhook_address(
    client, login_as, admin, proposals
):
    """The generic save refuses the key outright (it is treated as a secret),
    so the address can only enter through the validated M365 form."""
    import json

    for url in ("https://10.0.0.5/hook", GOOD[0]):
        login_as(client, admin)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "m365",
                "name": "x",
                "config_json": json.dumps({"teams_webhook_url": url}),
            },
        )
        assert resp.status_code == 302
    assert proposals == []
