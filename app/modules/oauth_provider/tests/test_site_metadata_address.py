"""OAuth and MCP metadata answer on the canonical origin and on each listed
alias, using the request's own origin; any other Host value (a forged header
or the server's IP address name) gets the canonical origin.

Run with PUBLIC_BASE_URL=https://entelim.tech,
PUBLIC_BASE_URL_ALIASES=https://entelim.org and MCP_ENABLED=true.
"""

import pytest

CANONICAL = "https://entelim.tech"
ALIAS = "https://entelim.org"


def _get(path, client, host):
    return client.get(
        path,
        headers={"Host": host},
        environ_overrides={"wsgi.url_scheme": "https"},
    )


CASES = [
    ("entelim.tech", CANONICAL),
    ("entelim.org", ALIAS),
    ("evil.example", CANONICAL),
    ("127.0.0.1", CANONICAL),
]


@pytest.mark.parametrize("host,expected", CASES)
def test_authorization_server_metadata_names_own_origin(app, client, host, expected):
    """RFC 8414 issuer and every endpoint carry the address the connector
    actually used, never an unlisted Host header."""
    response = _get("/.well-known/oauth-authorization-server", client, host)
    assert response.status_code == 200, (host, response.status_code)
    body = response.get_json()
    assert body["issuer"] == expected, (host, body.get("issuer"))
    assert body["authorization_endpoint"] == f"{expected}/oauth/authorize"
    assert body["token_endpoint"] == f"{expected}/oauth/token"
    assert body["revocation_endpoint"] == f"{expected}/oauth/revoke"
    assert body["registration_endpoint"] == f"{expected}/oauth/register"


@pytest.mark.parametrize("host,expected", CASES)
def test_protected_resource_metadata_names_own_origin(app, client, host, expected):
    """RFC 9728 resource and authorization servers carry the address the
    connector actually used, never an unlisted Host header."""
    response = _get("/.well-known/oauth-protected-resource", client, host)
    assert response.status_code == 200, (host, response.status_code)
    body = response.get_json()
    assert body["resource"] == f"{expected}/mcp", (host, body.get("resource"))
    assert body["authorization_servers"] == [expected], (
        host,
        body.get("authorization_servers"),
    )
