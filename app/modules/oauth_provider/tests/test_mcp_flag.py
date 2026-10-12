"""MCP_ENABLED=false: neither OAuth blueprint registers, and the bearer
identity loader is inert.

Deliberately a separate module from test_oauth_flow.py: the shared ``app``
fixture is session-scoped, so this module must be the only one in its pytest
invocation — run it with MCP_ENABLED unset (or "false"), never alongside the
rest of the suite which requires MCP_ENABLED=true.

    pytest app/modules/oauth_provider/tests/test_mcp_flag.py
"""

from __future__ import annotations


def test_no_oauth_or_mcp_route_when_disabled(app):
    assert app.config.get("MCP_ENABLED") is not True
    for rule in app.url_map.iter_rules():
        assert not rule.endpoint.startswith("oauth_provider."), rule.endpoint
        assert not rule.endpoint.startswith("oauth_metadata."), rule.endpoint
        assert not rule.endpoint.startswith("mcp."), rule.endpoint


def test_bearer_loader_is_inert_when_disabled(client, db_session, make_org, login_as):
    """Even a syntactically valid Authorization header resolves nobody."""
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    org = make_org("flagoff")
    user = User(
        email="flagoff@example.com", first_name="Flag", last_name="Off",
        organization_id=org.id, role=role, confirmed=True,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()

    resp = client.get("/dashboard/overview", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code in (302, 401, 403)
