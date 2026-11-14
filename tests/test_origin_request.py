"""Criterion 3: OAuth/MCP metadata answers on both hosts with each host's own origin.

With PUBLIC_BASE_URL=https://entelim.tech and PUBLIC_BASE_URL_ALIASES=https://entelim.org:
- on host entelim.tech, metadata names https://entelim.tech (issuer, every endpoint, resource https://entelim.tech/mcp)
- on host entelim.org, metadata names https://entelim.org
- with a forged Host header (evil.example) and with the server's IP name, metadata names https://entelim.tech

Requires MCP_ENABLED=true before the app fixture is created.
Red at the line tip (no origin helper, metadata always names entelim.org), green at head.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest


@contextmanager
def _set_config(app, **pairs) -> None:
    """Override app config for one test, then restore it."""
    before = {k: app.config.get(k) for k in pairs}
    for k, v in pairs.items():
        app.config[k] = v
    try:
        yield
    finally:
        for k, v in before.items():
            app.config[k] = v


def _metadata_on_host(client, app, host: str, scheme: str = "https"):
    """Fetch both well-known metadata documents on a given host."""
    base_url = f"{scheme}://{host}"
    pr = client.get(f"{base_url}/.well-known/oauth-protected-resource")
    as_ = client.get(f"{base_url}/.well-known/oauth-authorization-server")
    assert pr.status_code == 200, f"protected resource on {host}: {pr.status_code}"
    assert as_.status_code == 200, f"authorization server on {host}: {as_.status_code}"
    return pr.get_json(), as_.get_json()


def test_metadata_on_entelim_tech_names_entelim_tech(client, app):
    """On host entelim.tech, metadata names https://entelim.tech."""
    with _set_config(app,
                     PUBLIC_BASE_URL="https://entelim.tech",
                     PUBLIC_BASE_URL_ALIASES="https://entelim.org"):
        pr, as_ = _metadata_on_host(client, app, "entelim.tech")
        assert pr["resource"] == "https://entelim.tech/mcp", pr
        assert pr["authorization_servers"] == ["https://entelim.tech"], pr
        assert as_["issuer"] == "https://entelim.tech", as_
        assert as_["authorization_endpoint"] == "https://entelim.tech/oauth/authorize", as_
        assert as_["token_endpoint"] == "https://entelim.tech/oauth/token", as_


def test_metadata_on_entelim_org_names_entelim_org(client, app):
    """On host entelim.org (an alias), metadata names https://entelim.org."""
    with _set_config(app,
                     PUBLIC_BASE_URL="https://entelim.tech",
                     PUBLIC_BASE_URL_ALIASES="https://entelim.org"):
        pr, as_ = _metadata_on_host(client, app, "entelim.org")
        assert pr["resource"] == "https://entelim.org/mcp", pr
        assert pr["authorization_servers"] == ["https://entelim.org"], pr
        assert as_["issuer"] == "https://entelim.org", as_
        assert as_["authorization_endpoint"] == "https://entelim.org/oauth/authorize", as_
        assert as_["token_endpoint"] == "https://entelim.org/oauth/token", as_


def test_metadata_on_forged_host_names_canonical(client, app):
    """Forged Host header (evil.example) gets the canonical origin."""
    with _set_config(app,
                     PUBLIC_BASE_URL="https://entelim.tech",
                     PUBLIC_BASE_URL_ALIASES="https://entelim.org"):
        pr, as_ = _metadata_on_host(client, app, "evil.example")
        assert pr["resource"] == "https://entelim.tech/mcp", pr
        assert pr["authorization_servers"] == ["https://entelim.tech"], pr
        assert as_["issuer"] == "https://entelim.tech", as_


def test_metadata_on_server_ip_names_canonical(client, app):
    """Server's IP address name gets the canonical origin."""
    with _set_config(app,
                     PUBLIC_BASE_URL="https://entelim.tech",
                     PUBLIC_BASE_URL_ALIASES="https://entelim.org"):
        pr, as_ = _metadata_on_host(client, app, "127.0.0.1", scheme="http")
        assert pr["resource"] == "https://entelim.tech/mcp", pr
        assert pr["authorization_servers"] == ["https://entelim.tech"], pr
        assert as_["issuer"] == "https://entelim.tech", as_
