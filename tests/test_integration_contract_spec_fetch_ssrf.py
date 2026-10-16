"""Integration contracts: spec fetch must not reach internal targets, and a
contract belongs to the organisation that owns its application.

Before the fix any signed-in user could save spec_url=http://169.254.169.254/...
on a contract and have the server fetch it (redirects followed, no size cap),
get the connection error text back, and read, change, delete or trigger the
fetch of any other organisation's contract by id.
"""

from __future__ import annotations

import json
import uuid

import pytest
from flask import current_app

pytestmark = pytest.mark.usefixtures("db_session")

METADATA_BODY = "iam-role-credentials-from-metadata-service"

INTERNAL_URLS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://127.0.0.1:5432/openapi.json",
    "http://localhost/openapi.json",
    "http://[::1]/",
    "http://10.0.0.5/spec",
    "http://192.168.1.10/spec",
    "file:///etc/passwd",
]
NON_ASCII_HOSTS = [
    "http://１２７.0.0.1/spec.json",  # full-width digits
    "http://127。0。0。1/spec.json",  # ideographic full stops
    "http://localhost。/spec.json",
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
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    user_a, user_b = _user(db_session, org_a, "alice"), _user(db_session, org_b, "bob")
    app_a = ApplicationComponent(name=f"A app {uuid.uuid4().hex[:6]}", organization_id=org_a.id)
    app_b = ApplicationComponent(name=f"B app {uuid.uuid4().hex[:6]}", organization_id=org_b.id)
    db_session.add_all([app_a, app_b])
    db_session.flush()

    class W:
        pass

    w = W()
    w.org_a, w.org_b, w.user_a, w.user_b, w.app_a, w.app_b = org_a, org_b, user_a, user_b, app_a, app_b
    w.as_a = lambda: (login_as(client, user_a), client)[1]
    w.as_b = lambda: (login_as(client, user_b), client)[1]
    return w


@pytest.fixture
def production_like(monkeypatch):
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(current_app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    # The stub answers for the host's own name too; that must not count as
    # "the server's own address".
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())


class _Resp:
    def __init__(self, status=200, body=b"{}", ctype="application/json", headers=None):
        self.status_code = status
        self._body = body if isinstance(body, bytes) else body.encode()
        self.headers = {"Content-Type": ctype, **(headers or {})}
        self.closed = False

    def iter_content(self, chunk_size=1):
        self.chunk_size = chunk_size
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        self.closed = True


@pytest.fixture
def http_calls(monkeypatch):
    import requests

    class Calls(list):
        reply = staticmethod(lambda: _Resp(body=json.dumps({"openapi": "3.0.0", "paths": {}})))

    calls = Calls()

    def fake_get(url, **kw):
        calls.append({"url": url, **kw})
        return calls.reply()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("unexpected POST"))
    return calls


def _create(client, app_id, **body):
    body.setdefault("name", "Contract")
    return client.post("/api/enterprise/contracts", json={"application_id": app_id, **body})


def _insert_contract(world, spec_url, app=None):
    from app import db
    from app.models.integration_contract import IntegrationContract

    c = IntegrationContract(
        name="Legacy", application_id=(app or world.app_a).id, spec_url=spec_url
    )
    db.session.add(c)
    db.session.flush()
    return c


# --- create / update ------------------------------------------------------------


@pytest.mark.parametrize("url", INTERNAL_URLS + NON_ASCII_HOSTS)
def test_create_refuses_internal_spec_url(world, production_like, http_calls, url):
    resp = _create(world.as_a(), world.app_a.id, spec_url=url)
    assert resp.status_code == 400, resp.get_json()
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.filter_by(application_id=world.app_a.id).count() == 0
    assert http_calls == []


@pytest.mark.parametrize("url", INTERNAL_URLS[:4] + NON_ASCII_HOSTS)
def test_update_refuses_internal_spec_url_and_changes_nothing(world, production_like, http_calls, url):
    cid = _create(world.as_a(), world.app_a.id, spec_url="https://api.example.com/openapi.json").get_json()["id"]
    resp = world.as_a().put(f"/api/enterprise/contracts/{cid}", json={"spec_url": url, "name": "renamed"})
    assert resp.status_code == 400, resp.get_json()
    from app.models.integration_contract import IntegrationContract

    row = IntegrationContract.query.get(cid)
    assert row.spec_url == "https://api.example.com/openapi.json"
    assert row.name == "Contract"


def test_create_accepts_external_url(world, production_like, http_calls):
    resp = _create(world.as_a(), world.app_a.id, spec_url="https://api.example.com/openapi.json")
    assert resp.status_code == 201, resp.get_json()


# --- fetch ----------------------------------------------------------------------


@pytest.mark.parametrize("url", INTERNAL_URLS[:5] + NON_ASCII_HOSTS)
def test_fetch_never_calls_a_stored_internal_url(world, production_like, http_calls, url):
    c = _insert_contract(world, url)
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 400
    assert http_calls == []


def test_fetch_of_name_that_now_resolves_inside_is_refused(world, monkeypatch, http_calls):
    import app.utils.ssrf_guard as guard

    monkeypatch.setitem(current_app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.169.254", port))],
    )
    c = _insert_contract(world, "https://rebind.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 400
    assert http_calls == []
    assert "169.254" not in resp.get_data(as_text=True)


def test_fetch_external_spec_works_without_redirects(world, production_like, http_calls):
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 200, resp.get_json()
    assert len(http_calls) == 1
    assert http_calls[0]["allow_redirects"] is False
    assert http_calls[0]["stream"] is True
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(c.id).spec_content == {"openapi": "3.0.0", "paths": {}}


def test_fetch_accepts_yaml(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(body="openapi: '3.0.0'\npaths: {}\n", ctype="application/yaml; charset=utf-8")
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 200, resp.get_json()


ALIAS_BOMB = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
    f"{chr(98 + i)}: &{chr(98 + i)} [" + ", ".join([f"*{chr(97 + i)}"] * 10) + "]\n" for i in range(8)
)


def test_fetch_refuses_a_yaml_alias_bomb_quickly_and_stores_nothing(world, production_like, http_calls):
    import time

    assert len(ALIAS_BOMB) < 1000  # a few hundred bytes that would expand to ~10^9 leaves
    http_calls.reply = lambda: _Resp(body=ALIAS_BOMB, ctype="application/yaml")
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    started = time.monotonic()
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert time.monotonic() - started < 5
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "Spec could not be parsed"}
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(c.id).spec_content is None


def test_fetch_refuses_a_cheap_but_huge_yaml_document_quickly(world, production_like, http_calls):
    import time

    body = "a: [" + ",".join(["[]"] * 600000) + "]\n"  # ~1.8 MB, inside the byte cap
    assert len(body) < 2 * 1024 * 1024
    http_calls.reply = lambda: _Resp(body=body, ctype="application/yaml")
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    started = time.monotonic()
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert time.monotonic() - started < 2
    assert resp.status_code == 502
    assert resp.get_json()["error"] in ("Spec could not be parsed", "Spec is too large")
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(c.id).spec_content is None


def test_fetch_node_budget_refuses_a_yaml_document_under_the_byte_cap(world, production_like, http_calls):
    import time

    body = "a: [" + ",".join(["[]"] * 70000) + "]\n"
    assert len(body) < 256 * 1024
    http_calls.reply = lambda: _Resp(body=body, ctype="application/yaml")
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    started = time.monotonic()
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert time.monotonic() - started < 2
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "Spec could not be parsed"}


def test_yaml_node_budget_boundary():
    """The budget itself, without a clock: just over it is refused, just under loads."""
    from app.modules.solutions_strategic.v2.routes import integration_contract_routes as r

    def doc(n):
        return ("a: [" + ",".join(["[]"] * n) + "]\n").encode()

    under = r._parse_spec(doc(r.MAX_YAML_NODES - 10), "yaml")
    assert len(under["a"]) == r.MAX_YAML_NODES - 10
    with pytest.raises(ValueError, match="could not be parsed"):
        r._parse_spec(doc(r.MAX_YAML_NODES + 10), "yaml")
    with pytest.raises(ValueError, match="could not be parsed"):
        r._parse_spec(("a: " + "[" * (r.MAX_YAML_DEPTH + 5) + "]" * (r.MAX_YAML_DEPTH + 5)).encode(), "yaml")


def test_fetch_refuses_a_deeply_nested_yaml_document(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(body="a: " + "[" * 500 + "]" * 500 + "\n", ctype="application/yaml")
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502


def test_fetch_large_json_spec_still_loads_quickly(world, production_like, http_calls):
    body = json.dumps({"openapi": "3.0.0", "paths": {f"/p{i}": {"get": {"summary": "x"}} for i in range(20000)}})
    assert len(body) > 512 * 1024  # over the YAML cap: JSON keeps the full cap
    http_calls.reply = lambda: _Resp(body=body, ctype="application/json")
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    assert world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec").status_code == 200


def test_fetch_ordinary_sized_yaml_spec_still_loads(world, production_like, http_calls):
    body = "openapi: '3.0.0'\npaths:\n" + "".join(f"  /p{i}:\n    get:\n      summary: x\n" for i in range(500))
    http_calls.reply = lambda: _Resp(body=body, ctype="application/yaml")
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    assert world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec").status_code == 200


def test_fetch_yaml_without_aliases_still_parses(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(
        body="openapi: '3.0.0'\ninfo: {title: T}\npaths: {}\n", ctype="text/yaml"
    )
    c = _insert_contract(world, "https://api.example.com/openapi.yaml")
    assert world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec").status_code == 200


@pytest.mark.parametrize(
    "body",
    ['{"openapi": "3.0.0", "paths": {}}', "openapi: '3.0.0'\npaths: {}\n"],
    ids=["json", "yaml"],
)
def test_fetch_accepts_plain_text_that_parses_to_a_spec(world, production_like, http_calls, body):
    http_calls.reply = lambda: _Resp(body=body, ctype="text/plain; charset=utf-8")
    c = _insert_contract(world, "https://raw.githubusercontent.com/o/r/main/openapi")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 200, resp.get_json()


@pytest.mark.parametrize(
    "body",
    ["<!doctype html><html><body>Sign in</body></html>", "just some words", "- a\n- b\n", ""],
    ids=["html", "prose", "list", "empty"],
)
def test_fetch_plain_text_that_is_not_a_spec_is_refused(world, production_like, http_calls, body):
    http_calls.reply = lambda: _Resp(body=body, ctype="text/plain")
    c = _insert_contract(world, "https://api.example.com/page")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502
    assert "Sign in" not in resp.get_data(as_text=True)


def test_fetch_asks_for_no_compression_and_refuses_compressed_answers(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(headers={"Content-Encoding": "gzip"})
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert http_calls[0]["headers"]["Accept-Encoding"] == "identity"
    assert resp.status_code == 502
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(c.id).spec_content is None


def test_fetch_reads_the_body_in_small_chunks(world, production_like, http_calls):
    seen = []

    class Spy(_Resp):
        def iter_content(self, chunk_size=1):
            seen.append(chunk_size)
            return super().iter_content(chunk_size)

    http_calls.reply = lambda: Spy(body=json.dumps({"openapi": "3.0.0"}))
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    assert world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec").status_code == 200
    assert seen == [8192]


def test_fetch_redirect_to_metadata_is_not_followed(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(
        302, body=METADATA_BODY, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
    )
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502
    assert len(http_calls) == 1 and http_calls[0]["allow_redirects"] is False
    assert METADATA_BODY not in resp.get_data(as_text=True)
    assert "169.254" not in resp.get_data(as_text=True)


def test_fetch_response_over_the_cap_is_refused(world, production_like, http_calls):
    import app.modules.solutions_strategic.v2.routes.integration_contract_routes as routes

    big = b'{"openapi": "3.0.0", "x": "' + b"a" * (routes.MAX_SPEC_BYTES + 10) + b'"}'
    http_calls.reply = lambda: _Resp(body=big)
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502
    assert "too large" in resp.get_json()["error"]
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(c.id).spec_content is None


def test_fetch_declared_length_over_the_cap_is_refused_before_reading(world, production_like, http_calls):
    import app.modules.solutions_strategic.v2.routes.integration_contract_routes as routes

    class NoRead(_Resp):
        def iter_content(self, chunk_size=1):
            pytest.fail("body must not be read")

    http_calls.reply = lambda: NoRead(headers={"Content-Length": str(routes.MAX_SPEC_BYTES + 1)})
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502


@pytest.mark.parametrize("ctype", ["text/html", "application/octet-stream", "image/png", ""])
def test_fetch_other_content_types_are_refused(world, production_like, http_calls, ctype):
    http_calls.reply = lambda: _Resp(body=METADATA_BODY, ctype=ctype)
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502
    assert METADATA_BODY not in resp.get_data(as_text=True)


def test_fetch_error_text_is_generic(world, production_like, monkeypatch):
    import requests

    def boom(url, **kw):
        raise requests.ConnectionError("HTTPConnectionPool(host='10.1.2.3', port=5432): connection refused")

    monkeypatch.setattr(requests, "get", boom)
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "Failed to fetch spec"}


def test_fetch_unparseable_body_does_not_echo_content(world, production_like, http_calls):
    http_calls.reply = lambda: _Resp(body="{" + METADATA_BODY)
    c = _insert_contract(world, "https://api.example.com/openapi.json")
    resp = world.as_a().post(f"/api/enterprise/contracts/{c.id}/fetch-spec")
    assert resp.status_code == 502
    assert METADATA_BODY not in resp.get_data(as_text=True)


# --- ownership by the active organisation ---------------------------------------


def test_other_organisation_cannot_read_change_delete_or_fetch(world, production_like, http_calls):
    cid = _create(world.as_a(), world.app_a.id, spec_url="https://api.example.com/openapi.json").get_json()["id"]
    b = world.as_b()
    assert b.get(f"/api/enterprise/contracts/{cid}").status_code == 404
    assert b.put(f"/api/enterprise/contracts/{cid}", json={"name": "taken"}).status_code == 404
    assert b.post(f"/api/enterprise/contracts/{cid}/fetch-spec").status_code == 404
    assert b.delete(f"/api/enterprise/contracts/{cid}").status_code == 404
    assert http_calls == []
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(cid).name == "Contract"
    assert cid in [i["id"] for i in world.as_a().get("/api/enterprise/contracts").get_json()["items"]]


def test_list_shows_only_the_active_organisations_contracts(world, production_like):
    mine = _create(world.as_a(), world.app_a.id).get_json()["id"]
    theirs = _create(world.as_b(), world.app_b.id).get_json()["id"]
    ids_a = [i["id"] for i in world.as_a().get("/api/enterprise/contracts").get_json()["items"]]
    assert mine in ids_a and theirs not in ids_a
    ids_b = [i["id"] for i in world.as_b().get("/api/enterprise/contracts").get_json()["items"]]
    assert theirs in ids_b and mine not in ids_b


def test_application_listing_refuses_a_foreign_application(world, production_like):
    _create(world.as_b(), world.app_b.id)
    assert world.as_a().get(f"/api/enterprise/applications/{world.app_b.id}/contracts").status_code == 404
    assert world.as_a().get(f"/api/enterprise/applications/{world.app_a.id}/contracts").status_code == 200


def test_cannot_attach_a_contract_to_a_foreign_application(world, production_like):
    assert _create(world.as_a(), world.app_b.id).status_code == 404
    mine = _create(world.as_a(), world.app_a.id).get_json()["id"]
    moved = world.as_a().put(f"/api/enterprise/contracts/{mine}", json={"application_id": world.app_b.id})
    assert moved.status_code == 404
    from app.models.integration_contract import IntegrationContract

    assert IntegrationContract.query.get(mine).application_id == world.app_a.id


def test_contract_without_an_application_is_not_served_or_creatable(world, production_like):
    assert world.as_a().post("/api/enterprise/contracts", json={"name": "orphan"}).status_code == 400
    from app import db
    from app.models.integration_contract import IntegrationContract

    orphan = IntegrationContract(name="orphan", application_id=None)
    db.session.add(orphan)
    db.session.flush()
    assert world.as_a().get(f"/api/enterprise/contracts/{orphan.id}").status_code == 404
    assert orphan.id not in [i["id"] for i in world.as_a().get("/api/enterprise/contracts").get_json()["items"]]


def test_unknown_active_organisation_sees_nothing(world, production_like, monkeypatch):
    cid = _create(world.as_a(), world.app_a.id).get_json()["id"]
    import app.middleware.tenant_context as tc

    monkeypatch.setattr(tc, "current_org_id", lambda: None)
    c = world.as_a()
    assert c.get(f"/api/enterprise/contracts/{cid}").status_code == 404
    assert c.get("/api/enterprise/contracts").get_json()["items"] == []
    assert c.delete(f"/api/enterprise/contracts/{cid}").status_code == 404
    assert c.get(f"/api/enterprise/applications/{world.app_a.id}/contracts").status_code == 404
