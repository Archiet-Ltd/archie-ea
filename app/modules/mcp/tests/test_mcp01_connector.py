"""mcp01: what a real assistant connector found. Addresses are absolute; read-only tools say so."""
from __future__ import annotations

import json

from app.modules.mcp.tests.test_tools import (
    _make_element, _make_user, _mcp_call, _mcp_tools_list, _mint_oauth_token,
)


def _text(response):
    body = response if isinstance(response, dict) else response.get_json()
    return json.loads(body["result"]["content"][0]["text"])


def test_every_address_an_answer_returns_is_absolute(client, db_session, make_org, login_as):
    org = make_org("mcp-abs")
    user = _make_user(db_session, org, "mcp-abs@example.com")
    element = _make_element(db_session, org.id, "billing-service")
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    out = _mcp_call(client, token, "ask_impact", {"element_id": element.id})
    raw = json.dumps(out)
    base = client.application.config["PUBLIC_BASE_URL"].rstrip("/")
    assert base
    body = out
    for _ in range(3):
        body = body.get("result", body) if isinstance(body, dict) else body
    text = raw
    assert '"page_url": "/' not in text and '"api_url": "/' not in text, text[:400]
    assert "open at /" not in text
    assert (base + "/intelligence/answers/") in text
    assert (base + "/api/v1/intelligence/answers/") in text


def test_the_read_only_tools_say_what_they_are(client, db_session, make_org, login_as):
    org = make_org("mcp-ann")
    user = _make_user(db_session, org, "mcp-ann@example.com")
    token = _mint_oauth_token(client, db_session, org, user, login_as)
    tools = {t["name"]: t for t in _mcp_tools_list(client, token)}
    for name in ("ask_impact", "ask_strategy", "ask_portfolio", "ask_programme", "ask_risk",
                 "ask_accountability", "answer", "get_element", "search_elements"):
        ann = tools[name]["annotations"]
        assert ann["readOnlyHint"] is True and ann["destructiveHint"] is False, name
        assert ann["openWorldHint"] is False and ann["title"], name


# --- QA-041: one owner, read from the one owner store --------------------------------------------


def _payload(out):
    body = json.loads(out["result"]["content"][0]["text"])
    return body.get("data", body) if isinstance(body, dict) else body


def _owned_application(db_session, org, user, name):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    element = _make_element(db_session, org.id, name)
    application = ApplicationComponent(name=element.name, organization_id=org.id, archimate_element_id=element.id)
    db_session.add(application)
    db_session.flush()
    db_session.add(ApplicationOwner(application_id=application.id, user_id=user.id,
                                    organization_id=org.id, ownership_type="primary"))
    db_session.flush()
    return element


def test_get_element_ask_accountability_and_impact_rows_name_the_same_owner(client, db_session, make_org, login_as):
    from app.models import ArchiMateRelationship

    org = make_org("mcp-owner")
    user = _make_user(db_session, org, "mcp-owner@example.com")
    owned = _owned_application(db_session, org, user, "ledger")
    supplier = _make_element(db_session, org.id, "queue")
    db_session.add(ArchiMateRelationship(type="Serving", source_id=supplier.id, target_id=owned.id,
                                         organization_id=org.id))
    db_session.flush()
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    element = _payload(_mcp_call(client, token, "get_element", {"element_id": owned.id}))
    accountable = _payload(_mcp_call(client, token, "ask_accountability", {"element_id": owned.id}))
    impact = _payload(_mcp_call(client, token, "ask_impact", {"element_id": supplier.id}))

    names_element = {o["name"] for o in element["owners"]}
    names_accountable = {o["name"] for o in (accountable.get("result") or accountable)["owners"]}
    rows = (impact.get("result") or impact)["rows"]
    names_impact = {o["name"] for row in rows if row["element_id"] == owned.id for o in row.get("owners", [])}
    assert names_element and names_element == names_accountable == names_impact


def test_an_owner_typed_into_a_property_is_not_a_second_owner(client, db_session, make_org, login_as):
    org = make_org("mcp-owner2")
    user = _make_user(db_session, org, "mcp-owner2@example.com")
    element = _make_element(db_session, org.id, "orphan")
    element.custom_properties = {"owner": "Platform engineer", "data_classification": "internal"}
    db_session.flush()
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    got = _mcp_call(client, token, "get_element", {"element_id": element.id})
    text = got["result"]["content"][0]["text"]
    accountable = _payload(_mcp_call(client, token, "ask_accountability", {"element_id": element.id}))

    assert "Platform engineer" not in text
    assert _payload(got)["owners"] == []
    assert (accountable.get("result") or accountable)["owners"] == []
    assert "internal" in text, "other properties are kept"
