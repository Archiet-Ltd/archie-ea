"""The infrastructure polling tool and the tenant-configured integration URLs
must not reach internal targets.

Before the fix: the assistant's poll_infrastructure tool probed any URL a chat
message steered it to (redirects followed, a distinct error per failure, so a
port-scan oracle), and a tenant administrator could save a ServiceNow, Jira or
Abacus address pointing at the cloud metadata service or an internal host -
which the server then called with the integration's credentials (the
ServiceNow client secret was even posted to it).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from flask import current_app

pytestmark = pytest.mark.usefixtures("db_session")

INTERNAL_URLS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:5432/",
    "http://localhost:6379/",
    "http://[::1]/",
    "http://10.0.0.5/",
    "http://192.168.1.10:8080/",
]
NON_ASCII_HOSTS = [
    "http://１２７.0.0.1/",  # full-width digits
    "http://127。0。0。1/",  # ideographic full stops
    "http://localhost。/",
]
SECRET = "client-secret-value-that-must-not-leave"


@pytest.fixture
def production_like(monkeypatch):
    """Behave as outside development; resolve every name to a public address."""
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(current_app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
    )


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
def two_users(db_session, make_org):
    org_a, org_b = make_org("a"), make_org("b")
    return org_a, org_b, _user(db_session, org_a, "alice"), _user(db_session, org_b, "bob")


# =============================================================================
# poll_infrastructure
# =============================================================================


class _Ctx:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Stands in for the no-redirect opener; records what it was asked to open."""

    def __init__(self):
        self.opened = []
        self.error = None

    def open(self, req, timeout=None):
        self.opened.append(req.full_url)
        if self.error:
            raise self.error
        return _Ctx()


@pytest.fixture
def opener(monkeypatch):
    import urllib.request

    import app.services.infrastructure_polling_service as svc

    fake = _FakeOpener()
    monkeypatch.setattr(svc, "_NO_REDIRECT_OPENER", fake)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: pytest.fail("urlopen follows redirects; not allowed")
    )
    return fake


def _poll(user, urls):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    return ToolExecutor(user.id)._tool_poll_infrastructure(
        {"include_abacus": False, "include_llm": False, "additional_urls": urls}
    )


@pytest.mark.parametrize("who", ["a", "b"])
def test_poll_tool_never_probes_internal_targets(two_users, production_like, opener, who):
    _, _, ua, ub = two_users
    all_urls = INTERNAL_URLS
    result = _poll(ua if who == "a" else ub, all_urls)
    assert opener.opened == []
    user_results = [r for r in result["results"] if r["source"] == "user_provided"]
    assert len(user_results) == len(all_urls)
    assert all(r["reachable"] is False for r in user_results)
    # One answer for every refusal: no per-failure detail to tell ports apart.
    assert {r["error"] for r in user_results} == {"not reachable"}
    assert {r["status_code"] for r in user_results} == {None}
    assert {r["latency_ms"] for r in user_results} == {None}


@pytest.mark.parametrize("url", NON_ASCII_HOSTS)
def test_poll_tool_never_probes_non_ascii_spellings_of_internal_hosts(two_users, production_like, opener, url):
    result = _poll(two_users[2], [url])
    assert opener.opened == []
    assert result["results"][0]["reachable"] is False


def test_poll_tool_name_resolving_inside_is_not_probed(two_users, monkeypatch, opener):
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(current_app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    result = _poll(two_users[2], ["https://metadata.attacker.example/"])
    assert opener.opened == []
    assert result["results"][0]["error"] == "not reachable"
    assert "169.254" not in str(result)


def test_poll_tool_still_probes_an_ordinary_external_url(two_users, production_like, opener):
    result = _poll(two_users[2], ["https://status.example.com/health"])
    assert opener.opened == ["https://status.example.com/health"]
    row = result["results"][0]
    assert row["reachable"] is True and row["status_code"] == 200


def test_poll_tool_redirect_answer_is_generic(two_users, production_like, opener):
    import urllib.error

    opener.error = urllib.error.HTTPError(
        "https://status.example.com/", 302, "Found",
        {"Location": "http://169.254.169.254/latest/meta-data/"}, None,
    )
    row = _poll(two_users[2], ["https://status.example.com/"])["results"][0]
    assert row["reachable"] is False
    assert row["error"] == "not reachable"
    assert row["status_code"] is None
    assert "169.254" not in str(row)


def test_poll_tool_connection_error_text_is_generic(two_users, production_like, opener):
    import urllib.error

    opener.error = urllib.error.URLError("[Errno 111] Connection refused to 10.1.2.3:5432")
    row = _poll(two_users[2], ["https://status.example.com/"])["results"][0]
    assert row["error"] == "not reachable"
    assert "10.1.2.3" not in str(row) and "refused" not in str(row)


def test_poll_tool_rejects_a_non_list_argument(two_users, production_like, opener):
    result = _poll(two_users[2], "http://169.254.169.254/")
    assert result["success"] is False
    assert opener.opened == []


class _Hits(BaseHTTPRequestHandler):
    hits = None
    location = None

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        type(self).hits.append(self.path)
        if type(self).location:
            self.send_response(302)
            self.send_header("Location", type(self).location)
        else:
            self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_HEAD


def _serve(location=None):
    handler = type("H", (_Hits,), {"hits": [], "location": location})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def test_poll_probe_really_does_not_follow_a_redirect(two_users):
    """Real sockets, development configuration (loopback allowed): the redirect
    target must receive no request."""
    target, target_handler = _serve()
    redirector, redirect_handler = _serve(location=f"http://127.0.0.1:{target.server_port}/secret")
    try:
        row = _poll(two_users[2], [f"http://127.0.0.1:{redirector.server_port}/start"])["results"][0]
    finally:
        redirector.shutdown()
        target.shutdown()
    assert redirect_handler.hits == ["/start"]
    assert target_handler.hits == []
    assert row["reachable"] is False and row["error"] == "not reachable"


# =============================================================================
# ServiceNow service (tenant-configured instance URL)
# =============================================================================


@pytest.fixture
def servicenow_world(db_session, two_users, monkeypatch):
    from app import db
    from app.models.connector_config import ConnectorConfig

    org_a, org_b, ua, ub = two_users

    def configure(org, url):
        cfg = ConnectorConfig(
            organization_id=org.id, connector_type="servicenow", name="ServiceNow",
            config={"instance_url": url, "client_id": "cid", "enabled": True},
        )
        db.session.add(cfg)
        db.session.flush()
        return cfg

    secret_reads = []

    def fake_retrieve(self, **kw):
        secret_reads.append(kw)
        return SECRET

    from app.modules.codegen.services import credential_vault

    monkeypatch.setattr(credential_vault.OrgCredentialVault, "retrieve", fake_retrieve)
    w = SimpleNamespace(org_a=org_a, org_b=org_b, configure=configure, secret_reads=secret_reads)
    return w


@pytest.fixture
def requests_calls(monkeypatch):
    import requests

    class Calls(list):
        reply = None

    calls = Calls()

    class Resp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "t", "expires_in": 100, "result": []}

    def record(kind):
        def _call(url, **kw):
            calls.append({"kind": kind, "url": url, **kw})
            return calls_reply[0]()

        return _call

    calls_reply = calls.reply = [Resp]
    monkeypatch.setattr(requests, "get", record("get"))
    monkeypatch.setattr(requests, "post", record("post"))
    monkeypatch.setattr(requests, "request", lambda m, url, **kw: record(m)(url, **kw))
    return calls


@pytest.mark.parametrize("url", INTERNAL_URLS + NON_ASCII_HOSTS)
def test_servicenow_pull_never_calls_or_sends_the_secret_to_an_internal_instance(
    servicenow_world, production_like, requests_calls, url
):
    from app.services.connector_framework import ConnectorURLRefused
    from app.services.servicenow_connector_service import ServiceNowConnectorService

    servicenow_world.configure(servicenow_world.org_a, url)
    with pytest.raises(ConnectorURLRefused):
        ServiceNowConnectorService().pull_cmdb_inventory(servicenow_world.org_a.id)
    assert requests_calls == []
    assert servicenow_world.secret_reads == []  # the secret was not even read


def test_servicenow_push_refuses_an_internal_instance(servicenow_world, production_like, requests_calls, monkeypatch):
    from app.services.connector_framework import ConnectorURLRefused
    from app.services.servicenow_connector_service import ServiceNowConnectorService

    servicenow_world.configure(servicenow_world.org_a, "http://169.254.169.254/")
    item = SimpleNamespace(
        title="t", review_number="1", decision_rationale="", decided_by=None, servicenow_change_id=None
    )
    import app.models.architecture_review_board as arb

    monkeypatch.setattr(arb.ARBReviewItem, "query", SimpleNamespace(get=lambda _id: item))
    monkeypatch.setattr(
        ServiceNowConnectorService, "_resolve_org_id", staticmethod(lambda sid: servicenow_world.org_a.id)
    )
    with pytest.raises(ConnectorURLRefused):
        ServiceNowConnectorService().push_arb_decision(1, 1)
    assert requests_calls == []
    assert servicenow_world.secret_reads == []


def test_servicenow_external_instance_works_without_redirects(servicenow_world, production_like, requests_calls):
    from app.services.servicenow_connector_service import ServiceNowConnectorService

    servicenow_world.configure(servicenow_world.org_a, "https://acme.service-now.com")
    try:
        ServiceNowConnectorService().pull_cmdb_inventory(servicenow_world.org_a.id)
    except AttributeError:
        pass  # unrelated, existing: the config view has no last_sync_at slot; the calls are what matter
    assert [c["kind"] for c in requests_calls] == ["post", "get"]
    assert all(c["allow_redirects"] is False for c in requests_calls)
    assert requests_calls[0]["url"] == "https://acme.service-now.com/oauth_token.do"
    assert servicenow_world.secret_reads  # an allowed host does get the secret


def test_servicenow_instance_that_starts_resolving_inside_is_refused(servicenow_world, monkeypatch, requests_calls):
    import app.utils.ssrf_guard as guard
    from app.services.connector_framework import ConnectorURLRefused
    from app.services.servicenow_connector_service import ServiceNowConnectorService

    monkeypatch.setitem(current_app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    servicenow_world.configure(servicenow_world.org_a, "https://acme.service-now.com")
    with pytest.raises(ConnectorURLRefused):
        ServiceNowConnectorService().pull_cmdb_inventory(servicenow_world.org_a.id)
    assert requests_calls == []


def test_servicenow_other_organisations_instance_is_not_used(servicenow_world, production_like, requests_calls):
    from app.services.servicenow_connector_service import ServiceNowConnectorService

    servicenow_world.configure(servicenow_world.org_b, "http://169.254.169.254/")
    # org A has no connector: nothing is called, org B's row is not read.
    assert ServiceNowConnectorService().pull_cmdb_inventory(servicenow_world.org_a.id) == {}
    assert requests_calls == []


# =============================================================================
# Jira service
# =============================================================================


@pytest.mark.parametrize("url", INTERNAL_URLS + NON_ASCII_HOSTS)
def test_jira_connection_test_never_calls_an_internal_instance(production_like, requests_calls, url):
    from app.services.jira_connector_service import JiraConnectorService

    result = JiraConnectorService().test_connection(url, "me@example.com", "tok")
    assert result["status"] == "error"
    assert requests_calls == []


def test_jira_connection_test_answers_are_generic(production_like, requests_calls):
    from app.services.jira_connector_service import JiraConnectorService

    class Resp:
        status_code = 401
        text = "internal-service-banner: secret detail"

    requests_calls.reply[0] = Resp
    result = JiraConnectorService().test_connection("https://acme.atlassian.net", "me@example.com", "tok")
    assert result == {"status": "error", "message": "HTTP 401"}
    assert requests_calls[0]["allow_redirects"] is False

    def boom():
        raise OSError("connect to 10.1.2.3:5432 refused")

    requests_calls.reply[0] = boom
    result = JiraConnectorService().test_connection("https://acme.atlassian.net", "me@example.com", "tok")
    assert result == {"status": "error", "message": "Connection failed"}


def test_jira_saved_internal_instance_is_treated_as_unconfigured(production_like, requests_calls, monkeypatch):
    from app.services.jira_connector_service import JiraConnectorService

    svc = JiraConnectorService()
    cfg = SimpleNamespace(config={"instance_url": "http://169.254.169.254", "enabled": True})
    assert svc._instance_url(cfg) == ""
    assert svc._handle_request.__name__  # exists
    from app.services.jira_connector_service import JiraConnectorError

    with pytest.raises(JiraConnectorError):
        svc._handle_request("GET", "http://169.254.169.254/rest/api/3/search", {})
    assert requests_calls == []


# =============================================================================
# app/connectors (aiohttp)
# =============================================================================


@pytest.fixture
def aiohttp_calls(monkeypatch):
    import aiohttp

    calls = []

    async def fake_request(self, method, str_or_url, **kw):
        calls.append({"method": method, "url": str(str_or_url), **kw})
        raise aiohttp.ClientError("stubbed: no network")

    monkeypatch.setattr(aiohttp.ClientSession, "_request", fake_request)
    return calls


def _connector(cls, base_url):
    from app.services.connector_framework import ConnectorType

    cfg = SimpleNamespace(
        id="c1", name="x", connector_type=ConnectorType.ALM,
        config={"base_url": base_url, "username": "u", "api_token": "t", "password": "p",
                "client_id": "i", "client_secret": SECRET, "project_keys": []},
    )
    return cls(cfg)


def _connector_classes():
    from app.connectors.abacus import AbacusConnector
    from app.connectors.jira import JiraALMConnector
    from app.connectors.servicenow import ServiceNowCMDBConnector

    return [JiraALMConnector, ServiceNowCMDBConnector, AbacusConnector]


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("url", INTERNAL_URLS + NON_ASCII_HOSTS)
def test_connectors_never_call_an_internal_base_url(production_like, aiohttp_calls, index, url):
    connector = _connector(_connector_classes()[index], url)
    try:
        ok = asyncio.run(connector.test_connection())
    except Exception:  # a refusal may surface as an error; the point is no request
        ok = False
    assert not ok
    assert aiohttp_calls == []


@pytest.mark.parametrize("index", [0, 1, 2])
def test_connectors_refuse_a_base_url_that_starts_resolving_inside(monkeypatch, aiohttp_calls, index):
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(current_app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    connector = _connector(_connector_classes()[index], "https://tenant.example.com")
    try:
        asyncio.run(connector.test_connection())
    except Exception:
        pass
    assert aiohttp_calls == []


@pytest.mark.parametrize("index", [0, 1, 2])
def test_connectors_still_call_an_ordinary_external_base_url(production_like, aiohttp_calls, index):
    connector = _connector(_connector_classes()[index], "https://tenant.example.com")
    try:
        asyncio.run(connector.test_connection())
    except Exception:
        pass
    assert len(aiohttp_calls) >= 1
    assert aiohttp_calls[0]["url"].startswith("https://tenant.example.com")


@pytest.mark.parametrize("index", [0, 1])
def test_connectors_really_do_not_follow_a_redirect(index):
    """Real sockets, development configuration (loopback allowed): the host the
    request is redirected to receives nothing."""
    target, target_handler = _serve()
    redirector, redirect_handler = _serve(location=f"http://127.0.0.1:{target.server_port}/stolen")
    try:
        connector = _connector(_connector_classes()[index], f"http://127.0.0.1:{redirector.server_port}")
        try:
            asyncio.run(connector.test_connection())
        except Exception:
            pass
    finally:
        redirector.shutdown()
        target.shutdown()
    assert redirect_handler.hits  # the configured host was asked
    assert target_handler.hits == []  # the redirect target never was


def test_abacus_next_link_must_stay_on_the_configured_host(production_like):
    from app.connectors.abacus import AbacusConnector
    from app.services.connector_framework import ConnectorURLRefused

    connector = _connector(AbacusConnector, "https://tenant.example.com")
    assert connector._same_origin_link("https://tenant.example.com/api/Components?$skip=100")
    for link in ("http://169.254.169.254/latest/", "https://elsewhere.example.org/api/Components"):
        with pytest.raises(ConnectorURLRefused):
            connector._same_origin_link(link)


# =============================================================================
# Save time: the administrator routes
# =============================================================================


def _admin(db_session, org, label):
    from app.models.user import Permission, Role, User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label, last_name="Admin",
        organization_id=org.id, confirmed=True, enterprise_role="platform_administrator",
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


def _no_proposal(org):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.models.connector_config import ConnectorConfig

    assert AIChatCRUDApproval.query.filter_by(organization_id=org.id, entity_type="connectors").count() == 0
    assert ConnectorConfig.query.filter_by(organization_id=org.id).count() == 0


@pytest.mark.parametrize("url", INTERNAL_URLS[:3] + NON_ASCII_HOSTS)
def test_servicenow_save_refuses_internal_instance(db_session, two_users, production_like, client, login_as, url):
    org_a = two_users[0]
    admin = _admin(db_session, org_a, "sn")
    login_as(client, admin)
    resp = client.post(
        "/admin/connectors/servicenow",
        data={"instance_url": url, "client_id": "cid", "field_mapping": "{}", "enabled": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"not an allowed address" in resp.data
    _no_proposal(org_a)


def test_servicenow_save_accepts_external_instance(db_session, two_users, production_like, client, login_as):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    org_a = two_users[0]
    login_as(client, _admin(db_session, org_a, "sn2"))
    client.post(
        "/admin/connectors/servicenow",
        data={"instance_url": "https://acme.service-now.com", "client_id": "cid", "field_mapping": "{}", "enabled": "on"},
    )
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_a.id, entity_type="connectors").count() == 1


@pytest.mark.parametrize("url", INTERNAL_URLS[:3] + NON_ASCII_HOSTS)
def test_jira_save_refuses_internal_instance(db_session, two_users, production_like, client, login_as, url):
    org_a = two_users[0]
    login_as(client, _admin(db_session, org_a, "jr"))
    resp = client.post(
        "/admin/connectors/jira",
        data={"instance_url": url, "email": "me@example.com", "api_token": "tok", "enabled": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"not an allowed address" in resp.data
    _no_proposal(org_a)


def test_jira_test_route_never_calls_an_internal_instance(db_session, two_users, production_like, client, login_as, requests_calls):
    login_as(client, _admin(db_session, two_users[0], "jt"))
    resp = client.post(
        "/admin/connectors/jira/test",
        json={"instance_url": "http://169.254.169.254", "email": "me@example.com", "api_token": "tok"},
    )
    assert resp.status_code == 400
    assert requests_calls == []


@pytest.mark.parametrize("key", ["base_url", "instance_url", "api_url", "endpoint"])
def test_generic_connector_save_refuses_internal_url_in_config(
    db_session, two_users, production_like, client, login_as, key
):
    org_a = two_users[0]
    login_as(client, _admin(db_session, org_a, "gc"))
    resp = client.post(
        "/admin/connectors",
        data={
            "connector_type": "servicenow", "name": "x", "enabled": "1",
            "config_json": '{"%s": "http://169.254.169.254/"}' % key,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"not an allowed address" in resp.data
    _no_proposal(org_a)


@pytest.mark.parametrize("url", INTERNAL_URLS[:3])
def test_abacus_and_jira_settings_forms_refuse_internal_base_url(app, db_session, two_users, production_like, url):
    from app.modules.admin.v2.routes.admin_routes import _base_url_field_allowed

    with app.test_request_context("/"):
        for url_value in (url, "http://169.254.169.254", "https://api.example.com/"):
            form = SimpleNamespace(base_url=SimpleNamespace(data=url_value, errors=[]))
            allowed = _base_url_field_allowed(form)
            assert allowed is (url_value == "https://api.example.com/")
            if allowed:
                assert form.base_url.data == "https://api.example.com"
            else:
                assert form.base_url.errors == ["The connector URL is not an allowed address"]
