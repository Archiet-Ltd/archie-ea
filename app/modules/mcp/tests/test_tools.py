"""Tests for the MCP server: tool parity, static checks, and isolation.

Each tool test:
1. Creates an element in the test organisation
2. Calls the REST endpoint directly (as the session-authenticated user)
3. Calls the MCP tool through the JSON-RPC endpoint (as the OAuth-authenticated user)
4. Asserts the payloads match (byte-for-byte parity except for envelope fields)
"""

from __future__ import annotations

import json
import urllib.parse




def _make_user(db_session, org, email, enterprise_role=None):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer="application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _mcp_resource(app) -> str:
    """The one configured resource identifier (see oauth_provider.routes._mcp_resource_url).

    POST /oauth/authorize and POST /oauth/token both now make ``resource``
    mandatory (it must match this value exactly) -- omitting it, which this
    helper used to do, gets every request a 400 invalid_target instead of a
    code/token.
    """
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/mcp"


def _mint_oauth_token(client, db_session, org, user, login_as_fn, app=None) -> str:
    """Mint an OAuth access token through the real authorization-code flow."""
    from flask import current_app

    from app.modules.oauth_provider.models import OAuthClient

    oauth_client = OAuthClient.register(
        client_name="MCP Test Client",
        redirect_uris="http://localhost/callback",
    )

    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    resource = _mcp_resource(app if app is not None else current_app)

    login_as_fn(client, user)

    resp = client.post(
        "/oauth/authorize",
        data={
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "resource": resource,
            # The POST branch of authorize() requires an explicit "allow"/
            # "deny" decision (the consent form's two buttons) -- without it,
            # it 400s with "decision is required" before ever reaching the
            # code-issuing path. Missing alongside the resource field.
            "decision": "allow",
        },
        follow_redirects=False,
    )
    location = resp.headers["Location"]
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    auth_code = params["code"][0]

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://localhost/callback",
            "client_id": oauth_client.client_id,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    return resp.get_json()["access_token"]


def _clear_cached_identity():
    """Clear flask-login's and the tenant middleware's per-app-context
    identity cache before a bearer-only call.

    ``db_session`` (tests/conftest.py) holds ONE app context open for the
    whole test, so ``g._login_user``/``g.current_org_id`` survive across
    nested ``client.post()`` calls -- the exact trap ``login_as``'s own
    docstring describes for session-cookie identity. Every test in this
    module calls ``login_as`` (directly, or through ``_mint_oauth_token``)
    immediately before a REST comparison call, so a bearer call made right
    afterwards must not silently inherit that cached identity instead of
    resolving fresh from its own token. A bearer call has no cookie to set,
    so it must clear the same cache directly instead of going through
    ``login_as``.
    """
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _mcp_call(client, token: str, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool through the JSON-RPC endpoint.

    Uses a brand-new, cookie-less test client bound to the same Flask app
    rather than the passed-in ``client``. A genuine MCP client authenticates
    with ONLY a bearer token -- no session cookie, no "_sid", ever -- but
    ``client`` here always carries a live, non-revoked session cookie left
    over from the ``login_as`` call ``_mint_oauth_token`` drove through it.
    flask-login resolves session-based identity before it ever tries the
    bearer request_loader (see LoginManager._load_user), so every call this
    helper made before this fix was actually authenticated by that lingering
    session cookie, not by ``token`` -- the tool-parity assertions still held
    because it was the same user either way, but it never actually exercised
    the bearer-only path PR #225 needed proven.
    """
    _clear_cached_identity()
    bearer_client = client.application.test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = bearer_client.post(
        "/mcp",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.get_json()


def _mcp_tools_list(client, token: str) -> list:
    """List registered tools. See _mcp_call's docstring for why this uses a
    fresh, cookie-less client rather than the passed-in ``client``."""
    _clear_cached_identity()
    bearer_client = client.application.test_client()
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = bearer_client.post(
        "/mcp",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.get_json()["result"]["tools"]


class TestMCPInitialize:
    """MCP lifecycle: initialize and tools/list."""

    def test_initialize(self, client):
        """POST /mcp with initialize returns protocol version and capabilities."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = client.post("/mcp", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["result"]["protocolVersion"] == "2025-11-25"
        assert "tools" in data["result"]["capabilities"]

    def test_tools_list_requires_auth(self, client):
        """tools/list without auth returns 401."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = client.post("/mcp", data=json.dumps(payload), content_type="application/json")
        assert resp.status_code == 401

    def test_tools_list_returns_ten_tools(self, client, db_session, make_org, login_as):
        """tools/list returns exactly the ten read-only tools."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-list@example.com")
        token = _mint_oauth_token(client, db_session, org, user, login_as)

        tools = _mcp_tools_list(client, token)
        tool_names = {t["name"] for t in tools}
        expected = {
            "ask_impact", "ask_strategy", "ask_portfolio", "ask_programme",
            "ask_risk", "ask_accountability", "search_elements", "get_element",
            "list_canvases", "get_canvas",
        }
        assert tool_names == expected

    def test_every_tool_has_title_and_read_only_hint(self, client, db_session, make_org, login_as):
        """Every tool carries a human-readable title and readOnlyHint: true."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-annotations@example.com")
        token = _mint_oauth_token(client, db_session, org, user, login_as)

        tools = _mcp_tools_list(client, token)
        for tool in tools:
            assert "name" in tool
            assert "description" in tool, f"Tool {tool['name']} missing description"
            assert len(tool["description"]) > 0, f"Tool {tool['name']} has empty description"
            assert "annotations" in tool, f"Tool {tool['name']} missing annotations"
            assert tool["annotations"].get("readOnlyHint") is True, \
                f"Tool {tool['name']} missing readOnlyHint"


class TestBearerOnlySessionLess:
    """A genuine MCP client authenticates with a bearer token ONLY -- no
    browser session, no cookie at all, ever. Before the session_policy.py fix
    (see app/_bootstrap/session_policy.py's bearer-request exemption), a
    request shaped exactly like this was rejected 401 "revoked" by the
    session idle-timeout/revocation policy, which ran on every authenticated
    request and failed closed on the missing "_sid" a bearer-only request
    never has. ``_mcp_call``/``_mcp_tools_list`` above now always build their
    own fresh, cookie-less client rather than reusing the session-bearing
    ``client`` fixture (see their docstrings), so every test in this module
    already exercises this path -- these two tests make that guarantee
    explicit and would fail first if that stopped being true.
    """

    def test_tools_list_with_no_session_cookie_succeeds(self, client, db_session, make_org, login_as):
        """tools/list over a bearer token, on a client that has never once
        carried a session cookie, succeeds."""
        org = make_org("mcp-bearer-only")
        user = _make_user(db_session, org, "mcp-bearer-only-list@example.com")
        token = _mint_oauth_token(client, db_session, org, user, login_as)

        # db_session (tests/conftest.py) holds ONE app context open for the
        # whole test, so flask-login's g._login_user cache from the login_as
        # call above (inside _mint_oauth_token) would otherwise survive into
        # this request and short-circuit flask-login's _load_user() before it
        # ever tries the bearer request_loader -- see _clear_cached_identity's
        # docstring above. A real, separate production HTTP request has no
        # such leftover g to begin with; this is purely this test harness's
        # shared-app-context artifact, not a product behaviour.
        _clear_cached_identity()
        bearer_only_client = client.application.test_client()
        session_cookie_name = client.application.config.get("SESSION_COOKIE_NAME", "session")
        assert bearer_only_client.get_cookie(session_cookie_name) is None, (
            "precondition: this client must never have carried a session cookie"
        )

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = bearer_only_client.post(
            "/mcp",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()
        assert len(data["result"]["tools"]) == 10

    def test_tools_call_with_no_session_cookie_succeeds(self, client, db_session, make_org, login_as):
        """A real tools/call (ask_impact) over a bearer token, on a client
        that has never once carried a session cookie, succeeds end-to-end --
        including the tool handler's internal
        call_internal_api(pass_session=True) hop to the REST intelligence
        route. This is the acceptance proof that a genuine MCP client (no
        cookie, ever) can actually use this connector, not just that the
        bearer loader itself resolves a user."""
        org = make_org("mcp-bearer-only")
        user = _make_user(db_session, org, "mcp-bearer-only-call@example.com")
        element = _make_element(db_session, org.id, "bearer-only-call-test")
        token = _mint_oauth_token(client, db_session, org, user, login_as)

        # See the matching comment in test_tools_list_with_no_session_cookie_
        # succeeds above: clears this test-harness-only g cache, not a
        # product behaviour.
        _clear_cached_identity()
        bearer_only_client = client.application.test_client()
        session_cookie_name = client.application.config.get("SESSION_COOKIE_NAME", "session")
        assert bearer_only_client.get_cookie(session_cookie_name) is None, (
            "precondition: this client must never have carried a session cookie"
        )

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ask_impact", "arguments": {"element_id": element.id}},
        }
        resp = bearer_only_client.post(
            "/mcp",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert "error" not in body, body
        result = json.loads(body["result"]["content"][0]["text"])
        assert result["success"] is True, result


class TestLensToolParity:
    """Each lens tool returns the same payload as its REST endpoint."""

    def test_ask_impact_parity(self, client, db_session, make_org, login_as):
        """ask_impact returns the same JSON as GET /api/v1/intelligence/impact/<id>."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-impact@example.com")
        element = _make_element(db_session, org.id, "impact-test")

        # REST call
        login_as(client, user)
        rest_resp = client.get(f"/api/v1/intelligence/impact/{element.id}")
        rest_data = rest_resp.get_json()

        # MCP call
        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_impact", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        # Both should succeed
        assert rest_data["success"] is True
        assert mcp_result["success"] is True
        # Core data matches (excluding timing fields that differ between requests)
        rest_summary = {k: v for k, v in rest_data["data"]["summary"].items()
                        if k != "latency_ms"}
        mcp_summary = {k: v for k, v in mcp_result["data"]["summary"].items()
                       if k != "latency_ms"}
        assert rest_summary == mcp_summary

    def test_ask_risk_parity(self, client, db_session, make_org, login_as):
        """ask_risk returns the same JSON as GET /api/v1/intelligence/risk/<id>."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-risk@example.com")
        element = _make_element(db_session, org.id, "risk-test")

        login_as(client, user)
        rest_resp = client.get(f"/api/v1/intelligence/risk/{element.id}")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_risk", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["success"] is True
        assert mcp_result["success"] is True
        assert rest_data["data"]["risks"] == mcp_result["data"]["risks"]

    def test_ask_portfolio_parity(self, client, db_session, make_org, login_as):
        """ask_portfolio returns the same JSON as GET /api/v1/intelligence/portfolio/<id>."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-portfolio@example.com")
        element = _make_element(db_session, org.id, "portfolio-test")

        login_as(client, user)
        rest_resp = client.get(f"/api/v1/intelligence/portfolio/{element.id}")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_portfolio", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["success"] is True
        assert mcp_result["success"] is True
        assert rest_data["data"] == mcp_result["data"]

    def test_ask_programme_parity(self, client, db_session, make_org, login_as):
        """ask_programme returns the same JSON as GET /api/v1/intelligence/programme/<id>."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-programme@example.com")
        element = _make_element(db_session, org.id, "programme-test")

        login_as(client, user)
        rest_resp = client.get(f"/api/v1/intelligence/programme/{element.id}")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_programme", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["success"] is True
        assert mcp_result["success"] is True
        assert rest_data["data"]["work_packages"] == mcp_result["data"]["work_packages"]

    def test_ask_strategy_parity(self, client, db_session, make_org, login_as):
        """ask_strategy returns the same JSON as GET /api/v1/intelligence/strategy/<id>."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-strategy@example.com")
        element = _make_element(db_session, org.id, "strategy-test")

        login_as(client, user)
        rest_resp = client.get(f"/api/v1/intelligence/strategy/{element.id}")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_strategy", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["success"] is True
        assert mcp_result["success"] is True
        assert rest_data["data"]["initiatives"] == mcp_result["data"]["initiatives"]

    def test_ask_accountability_returns_withdrawn_reason(self, client, db_session, make_org, login_as):
        """ask_accountability returns ownership_reader_not_built on every call."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-acct@example.com")
        element = _make_element(db_session, org.id, "acct-test")

        login_as(client, user)
        rest_resp = client.get(f"/api/v1/intelligence/accountability/{element.id}")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_accountability", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["success"] is True
        assert mcp_result["success"] is True
        # Both carry the withdrawn reason
        assert "ownership_reader_not_built" in str(rest_data["data"]["reasons"])
        assert "ownership_reader_not_built" in str(mcp_result["data"]["reasons"])
        assert rest_data["data"]["owners"] == []
        assert mcp_result["data"]["owners"] == []

    def test_unknown_element_returns_404(self, client, db_session, make_org, login_as):
        """A non-existent element id returns 404 from both REST and MCP."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-404@example.com")

        login_as(client, user)
        rest_resp = client.get("/api/v1/intelligence/impact/999999")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "ask_impact", {"element_id": 999999})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["success"] is False
        assert mcp_result["success"] is False
        assert rest_data["error"]["code"] == mcp_result["error"]["code"]


class TestElementTools:
    """search_elements and get_element tests."""

    def test_search_elements_finds_by_name(self, client, db_session, make_org, login_as):
        """search_elements returns matching elements."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-search@example.com")
        _make_element(db_session, org.id, "unique-search-term-xyz")

        login_as(client, user)
        rest_resp = client.get("/archimate/api/elements/search?q=unique-search-term-xyz&limit=10")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "search_elements",
                             {"query": "unique-search-term-xyz", "limit": 10})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert len(rest_data["data"]) == 1
        assert len(mcp_result["data"]) == 1
        assert rest_data["data"][0]["name"] == mcp_result["data"][0]["name"]

    def test_get_element_detail(self, client, db_session, make_org, login_as):
        """get_element returns element detail."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-detail@example.com")
        element = _make_element(db_session, org.id, "detail-test")

        login_as(client, user)
        rest_resp = client.get(f"/archimate/api/elements/{element.id}/detail")
        rest_data = rest_resp.get_json()

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "get_element", {"element_id": element.id})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert rest_data["name"] == mcp_result["name"]
        assert rest_data["type"] == mcp_result["type"]


class TestCanvasTools:
    """list_canvases and get_canvas tests."""

    def test_list_canvases_returns_lists(self, client, db_session, make_org, login_as):
        """list_canvases returns business_model_canvases and business_cases lists."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-canvas@example.com")

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "list_canvases", {})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert "business_model_canvases" in mcp_result
        assert "business_cases" in mcp_result
        assert isinstance(mcp_result["business_model_canvases"], list)
        assert isinstance(mcp_result["business_cases"], list)

    def test_get_canvas_business_model_not_found(self, client, db_session, make_org, login_as):
        """get_canvas for non-existent business model canvas returns error."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-bmc@example.com")

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "get_canvas",
                             {"canvas_type": "business_model", "canvas_id": 999999})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert mcp_result["success"] is False
        assert mcp_result["error"]["code"] == "NOT_FOUND"

    def test_get_canvas_business_case_not_found(self, client, db_session, make_org, login_as):
        """get_canvas for non-existent business case returns error."""
        org = make_org("mcp")
        user = _make_user(db_session, org, "mcp-bc@example.com")

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        mcp_resp = _mcp_call(client, token, "get_canvas",
                             {"canvas_type": "business_case", "canvas_id": 999999})
        mcp_result = json.loads(mcp_resp["result"]["content"][0]["text"])

        assert mcp_result["success"] is False
        assert mcp_result["error"]["code"] == "NOT_FOUND"


class TestMeteringAndTenantContext:
    """Metering records carry the correct organization_id after Bearer auth."""

    def test_mcp_tool_call_metering_has_correct_org_id(self, client, db_session, make_org, login_as):
        """After a Bearer-token MCP tool call, the UsageEvent has the correct org_id."""
        org = make_org("mcp-meter")
        user = _make_user(db_session, org, "mcp-meter@example.com")
        element = _make_element(db_session, org.id, "meter-test")

        token = _mint_oauth_token(client, db_session, org, user, login_as)
        _mcp_call(client, token, "ask_impact", {"element_id": element.id})

        from app.models.usage_event import UsageEvent
        events = UsageEvent.query.filter_by(
            event_type="mcp_tool_call",
            resource_type="ask_impact",
        ).all()
        assert len(events) >= 1, "Expected at least one metering event"
        for event in events:
            assert event.organization_id == org.id, \
                f"UsageEvent org_id={event.organization_id}, expected {org.id}"


class TestStaticChecks:
    """Static assertions about the MCP module."""

    def test_no_mcp_tool_imports_model_directly(self):
        """No MCP tool module imports a model or service directly."""
        import ast
        import os

        tools_dir = os.path.join(
            os.path.dirname(__file__), "..", "tools"
        )
        forbidden_imports = {
            "app.models", "ArchiMateElement", "ArchiMateRelationship",
            "IntelligenceQueryService", "ApplicationComponent",
        }
        for filename in os.listdir(tools_dir):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            filepath = os.path.join(tools_dir, filename)
            with open(filepath) as f:
                try:
                    tree = ast.parse(f.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in forbidden_imports, \
                            f"{filename} imports {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert node.module not in forbidden_imports, \
                            f"{filename} imports from {node.module}"

    def test_search_elements_calls_only_canonical_route(self):
        """search_elements and get_element call only the canonical route."""
        import os

        element_tools_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "element_tools.py"
        )
        with open(element_tools_path) as f:
            source = f.read()

        # Must call the canonical route
        assert "/archimate/api/elements/search" in source
        assert "/archimate/api/elements/" in source

        # Must NOT call any of the three duplicates
        forbidden = [
            "adm_kanban_view_routes",
            "architecture_crud_routes",
            "solution_design_routes",
        ]
        for forbidden_name in forbidden:
            assert forbidden_name not in source, \
                f"element_tools.py references forbidden duplicate: {forbidden_name}"

    def test_mcp_blueprint_does_not_import_duplicate_search_routes(self):
        """The MCP blueprint never imports the three duplicate search routes."""
        import os

        mcp_dir = os.path.join(os.path.dirname(__file__), "..")
        forbidden = {
            "adm_kanban_view_routes",
            "architecture_crud_routes",
            "solution_design_routes",
        }
        for root, dirs, files in os.walk(mcp_dir):
            # Skip test directories
            if "tests" in root.split(os.sep):
                continue
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                filepath = os.path.join(root, filename)
                with open(filepath) as f:
                    source = f.read()
                for forbidden_name in forbidden:
                    assert forbidden_name not in source, \
                        f"{os.path.relpath(filepath)} references {forbidden_name}"

    def test_usage_event_model_has_mcp_tool_call_constant(self):
        """UsageEvent.EVENT_MCP_TOOL_CALL is defined and equals 'mcp_tool_call'."""
        from app.models.usage_event import UsageEvent
        assert hasattr(UsageEvent, "EVENT_MCP_TOOL_CALL"), \
            "UsageEvent missing EVENT_MCP_TOOL_CALL constant"
        assert UsageEvent.EVENT_MCP_TOOL_CALL == "mcp_tool_call"