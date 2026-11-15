"""One organisation's connector traffic never uses up another organisation's budget."""

from __future__ import annotations

import pytest

from app.modules.mcp.tests.test_rate_limit_org_budget import (  # noqa: F401  (fixtures)
    forget_counters,
    limiting_on,  # noqa: F401
    quick_tools,  # noqa: F401
    post,
    run_session,
)
from app.modules.mcp.tests.test_scope_enforcement import _mint_token_with_scope
from app.modules.mcp.tests.test_tools import _make_element, _make_user


@pytest.mark.usefixtures("limiting_on", "quick_tools")
def test_one_organisation_over_budget_does_not_refuse_another_at_the_same_moment(
    app, db_session, make_org
):
    first, second = make_org("mcp-cross-a"), make_org("mcp-cross-b")
    tokens, elements = {}, {}
    try:
        for org in (first, second):
            user = _make_user(db_session, org, f"cross-{org.id}@example.com")
            elements[org.id] = _make_element(db_session, org.id, "cross")
            tokens[org.id] = _mint_token_with_scope(app, user, org, scope="mcp:read")
        call = {"name": "ask_impact", "arguments": {"element_id": elements[first.id].id}}
        refused = None
        for n in range(200):
            reply = post(app, tokens[first.id], "tools/call", call, request_id=n)
            if reply.status_code == 429:
                refused = reply
                break
        assert refused is not None
        assert refused.get_json()["error"]["message"]
        assert int(refused.headers["Retry-After"]) > 0
        # The same address, the same moment: the second organisation's whole session is answered.
        sent = run_session(app, tokens[second.id], elements[second.id].id)
        # The second message is the initialized notification, which the transport answers 202.
        assert [r.status_code for r in sent] == [200, 202] + [200] * (len(sent) - 2)
        # And the first organisation is still refused.
        assert post(app, tokens[first.id], "tools/call", call).status_code == 429
    finally:
        forget_counters(app, [first.id, second.id])
