"""What breaks if Payments service fails: each affected element is one row, in the chat's
data and in the connector's answer, and each organisation's sample company gives its own count."""
import json

from app.modules.mcp.tests.test_tools import _make_user, _mint_oauth_token
from tests.test_sample_company_cited import _load, _payments_id

AFFECTED = 11


def _ask(client, token, element_id):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "ask_impact", "arguments": {"element_id": element_id}}}
    resp = client.post("/mcp", data=json.dumps(payload), content_type="application/json",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    out = json.loads(resp.get_json()["result"]["content"][0]["text"])
    body = out.get("data", out)
    return body.get("result", body)


def _sample_answer(client, login_as, make_org, db_session, label):
    org = make_org()
    user = _make_user(db_session, org, "once-%s@example.com" % label, "enterprise_architect")
    db_session.commit()
    assert _load(client, login_as, user).status_code == 200
    token = _mint_oauth_token(client, db_session, org, user, login_as)
    return org, _ask(client, token, _payments_id(org.id))


def _names_once(answer):
    names = [answer["elements"][str(row["element_id"])]["name"] for row in answer["rows"]]
    assert len(names) == len(set(names)), names
    return names


def test_mcp_impact_answer_counts_each_affected_element_once(client, login_as, make_org, db_session):
    _org, answer = _sample_answer(client, login_as, make_org, db_session, "a")
    assert len(_names_once(answer)) == AFFECTED
    assert answer["summary"]["total"] == AFFECTED


def test_cross_org_each_sample_company_gives_its_own_count(client, login_as, make_org, db_session):
    org_a, first = _sample_answer(client, login_as, make_org, db_session, "b1")
    org_b, second = _sample_answer(client, login_as, make_org, db_session, "b2")
    own_a = {str(r["element_id"]) for r in first["rows"]}
    own_b = {str(r["element_id"]) for r in second["rows"]}
    assert own_a and own_b and own_a.isdisjoint(own_b)
    assert first["summary"]["total"] == len(first["rows"]) == AFFECTED
    assert second["summary"]["total"] == len(second["rows"]) == AFFECTED
    assert set(first["elements"]).isdisjoint(second["elements"])
