"""The assistant connector is held to the same limiter as every other route.

An address on the administrator's deny list is refused at /mcp in the API envelope, before the
connector reads the request, and the refusal is logged on the Rate limits page's list.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

# Addresses in one block no other process uses (named by this process's id); only these rows are
# removed, so another run on the same database keeps its own entries and refusals.
_BLOCK = "10.%d.%d" % ((os.getpid() >> 8) & 255, os.getpid() & 255)
DENIED = _BLOCK + ".77"
OTHER = _BLOCK + ".78"


def _forget_own(connection):
    connection.execute(text("DELETE FROM rate_limit_rules WHERE value LIKE :p"), {"p": _BLOCK + ".%"})
    connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    # tenancy-ok: test cleanup of only the rows this run created, by address prefix,
    # under platform scope set on the line above.
    connection.execute(text("DELETE FROM rate_limit_violations WHERE address LIKE :p"), {"p": _BLOCK + ".%"})


@pytest.fixture
def limiting_on(app):
    from app import db

    was = app.config.get("RATE_LIMITING_ENABLED")
    app.config["RATE_LIMITING_ENABLED"] = True
    with app.app_context(), db.engine.begin() as connection:
        _forget_own(connection)
    yield
    with app.app_context(), db.engine.begin() as connection:
        _forget_own(connection)
    app.config["RATE_LIMITING_ENABLED"] = was


def test_a_denied_address_is_refused_at_the_connector(app, limiting_on):
    from app.services import rate_limiter

    with app.app_context():
        rate_limiter.add_rule("deny", "address", DENIED)
    client = app.test_client()
    refused = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                          environ_overrides={"REMOTE_ADDR": DENIED})
    assert refused.status_code == 403
    assert refused.get_json()["error"] == rate_limiter.DENIED_MESSAGE
    with app.app_context():
        rows = rate_limiter.violation_page(address=DENIED)["rows"]
    assert [r["route"] for r in rows] == ["POST /mcp"]
    # Another address reaches the connector (it answers for itself: unauthenticated is not "blocked").
    other = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        environ_overrides={"REMOTE_ADDR": OTHER})
    assert other.status_code != 403 or other.get_json().get("error") != rate_limiter.DENIED_MESSAGE
    assert other.headers.get("RateLimit-Limit")
