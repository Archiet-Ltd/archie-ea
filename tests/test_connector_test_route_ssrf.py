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
    s.client = lambda: (login_as(client, user), client)[1]
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


# --- create route ---------------------------------------------------------------


@pytest.fixture
def n8n_calls(monkeypatch):
    from app.modules.codegen.services import connector_orchestrator as co

    calls = []

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "wf1"}

    def fake(self, method, path, **kw):
        calls.append((method, path, kw))
        return R()

    monkeypatch.setattr(co.ConnectorOrchestrator, "_n8n_request", fake)
    return calls


def _create(scene, base_url, target="https://app.example.com"):
    return scene_client(scene).post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={
            "connector_type": "rest_api",
            "credentials": {"base_url": base_url},
            "target_api_url": target,
        },
    )


def scene_client(scene):
    return scene.client()


@pytest.mark.parametrize(
    "base,target",
    [
        ("http://169.254.169.254/latest/meta-data/", "https://app.example.com"),
        ("https://api.example.com/", "http://169.254.169.254"),
        ("http://127.0.0.1:5432/", "https://app.example.com"),
        ("http://１２７.0.0.1/", "https://app.example.com"),
    ],
)
def test_create_refuses_internal_addresses_and_builds_no_workflow(scene, n8n_calls, base, target):
    resp = _create(scene, base, target)
    assert resp.status_code == 400
    assert n8n_calls == []
    assert "169.254" not in resp.get_data(as_text=True)


def test_create_with_public_addresses_builds_the_workflow(scene, n8n_calls):
    resp = _create(scene, "https://api.example.com/v1")
    assert resp.status_code == 201, resp.get_json()
    assert n8n_calls and n8n_calls[0][1] == "/api/v1/workflows"


def test_create_failure_text_is_generic(scene, monkeypatch):
    from app.modules.codegen.services import connector_orchestrator as co

    def boom(self, *a, **k):
        raise RuntimeError("n8n unreachable: http://10.1.2.3:5678 refused")

    monkeypatch.setattr(co.ConnectorOrchestrator, "_n8n_request", boom)
    resp = _create(scene, "https://api.example.com/v1")
    assert resp.status_code == 502
    assert "10.1.2.3" not in resp.get_data(as_text=True)


# --- every address a builder can call is validated -----------------------------


def _all_builders():
    from app.modules.codegen.services import connector_orchestrator as co

    builders = dict(co._WORKFLOW_BUILDERS)
    builders["(generic)"] = co._build_generic_workflow
    return builders


# Reviewed classification of every credential key any builder reads. A new key
# fails the test below until someone classifies it here.
ADDRESS_KEYS = {"instance_url", "server_url", "base_url", "host"}
NON_ADDRESS_KEYS = {
    "client_id", "client_secret", "client", "username", "password", "email",
    "api_token", "tenant_id", "spreadsheet_id", "credentials_json", "port",
    "database", "channel_id", "bot_token",
}


def _credential_keys(builder):
    import inspect
    import re

    return set(re.findall(r'credentials\.get\(\s*"([A-Za-z0-9_]+)"', inspect.getsource(builder)))


def test_every_credential_key_is_classified_and_address_keys_are_validated():
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator
    from app.utils.ssrf_guard import BlockedOutboundURL

    found = set()
    for builder in _all_builders().values():
        found |= _credential_keys(builder)
    assert found == ADDRESS_KEYS | NON_ADDRESS_KEYS, (
        "classify new credential keys: " + repr(sorted(found ^ (ADDRESS_KEYS | NON_ADDRESS_KEYS)))
    )
    for key in sorted(ADDRESS_KEYS):
        with pytest.raises(BlockedOutboundURL):
            ConnectorOrchestrator._check_workflow_urls(
                {key: "http://169.254.169.254/"}, "https://app.example.com"
            )


def test_nested_or_non_string_address_values_are_refused():
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator
    from app.utils.ssrf_guard import BlockedOutboundURL

    for creds in ({"instance_url": ["http://10.0.0.5"]}, {"webhook_url": 5}, "not-a-dict", None, []):
        with pytest.raises(BlockedOutboundURL):
            ConnectorOrchestrator._check_workflow_urls(creds, "https://app.example.com")


@pytest.mark.parametrize("key,ctype", [
    ("instance_url", "servicenow"),
    ("instance_url", "salesforce"),
    ("server_url", "sap"),
    ("webhook_url", "microsoft_teams"),
])
@pytest.mark.parametrize("url", [
    "http://169.254.169.254",
    "http://127.0.0.1:8080",
    "http://１２７.0.0.1",
])
def test_create_refuses_address_keys_of_every_connector(scene, n8n_calls, monkeypatch, key, ctype, url):
    monkeypatch.setitem(
        __import__("flask").current_app.config,
        "COMPLIANCE_APPROVED_CONNECTOR_TYPES",
        ("rest_api", "sap", "salesforce", "microsoft_teams"),
    )
    resp = scene.client().post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={"connector_type": ctype, "credentials": {key: url}, "target_api_url": "https://app.example.com"},
    )
    assert resp.status_code == 400
    assert n8n_calls == []


def test_servicenow_public_instance_builds_workflow_without_redirects(scene, n8n_calls):
    resp = scene.client().post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={
            "connector_type": "servicenow",
            "credentials": {"instance_url": "https://acme.service-now.com"},
            "target_api_url": "https://app.example.com",
        },
    )
    assert resp.status_code == 201, resp.get_json()
    nodes = n8n_calls[0][2]["json"]["nodes"]
    fetch = next(n for n in nodes if n["name"] == "Fetch")
    push = next(n for n in nodes if n["name"] == "Push")
    for node in (fetch, push):
        assert node["parameters"]["options"]["redirect"]["redirect"]["followRedirects"] is False


def test_sap_entity_is_encoded_and_dot_dot_refused(scene, n8n_calls, monkeypatch):
    monkeypatch.setitem(
        __import__("flask").current_app.config,
        "COMPLIANCE_APPROVED_CONNECTOR_TYPES",
        ("sap",),
    )
    ok = scene.client().post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={
            "connector_type": "sap",
            "credentials": {"server_url": "https://sap.example.com"},
            "object_mappings": {"Business Partner/x": {}},
            "target_api_url": "https://app.example.com",
        },
    )
    assert ok.status_code == 201, ok.get_json()
    fetch = next(n for n in n8n_calls[0][2]["json"]["nodes"] if n["name"] == "Fetch")
    assert "Business%20Partner%2Fx" in fetch["parameters"]["url"]
    bad = scene.client().post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={
            "connector_type": "sap",
            "credentials": {"server_url": "https://sap.example.com"},
            "object_mappings": {"../../admin": {}},
            "target_api_url": "https://app.example.com",
        },
    )
    assert bad.status_code == 400


# --- nothing is stored when a request is refused --------------------------------


@pytest.fixture
def vault_calls(monkeypatch):
    from app.modules.codegen.services import connector_orchestrator as co

    calls = []
    monkeypatch.setattr(
        co.CredentialVault, "store", lambda self, *a, **k: calls.append((a, k))
    )
    return calls


@pytest.mark.parametrize("credentials", [None, "text", ["a"], 5])
def test_non_object_credentials_are_refused_before_the_vault(scene, n8n_calls, vault_calls, credentials):
    resp = scene.client().post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={"connector_type": "servicenow", "credentials": credentials,
              "target_api_url": "https://app.example.com"},
    )
    assert resp.status_code == 400
    assert vault_calls == [] and n8n_calls == []


def test_route_refusal_never_reaches_the_vault(scene, n8n_calls, vault_calls):
    resp = scene.client().post(
        f"/solutions/{scene.own.id}/codegen/connectors",
        json={"connector_type": "servicenow",
              "credentials": {"instance_url": "http://169.254.169.254", "password": "x"},
              "target_api_url": "https://app.example.com"},
    )
    assert resp.status_code == 400
    assert vault_calls == [] and n8n_calls == []


def test_direct_call_with_internal_servicenow_instance_is_refused(scene, n8n_calls, vault_calls):
    from app.modules.codegen.services.connector_orchestrator import ConnectorOrchestrator
    from app.utils.ssrf_guard import BlockedOutboundURL

    with pytest.raises(BlockedOutboundURL):
        ConnectorOrchestrator().create_sync_workflow(
            solution_id=scene.own.id,
            connector_type="servicenow",
            credentials={"instance_url": "http://169.254.169.254"},
            object_mappings={},
            target_api_url="https://app.example.com",
        )
    assert vault_calls == [] and n8n_calls == []
