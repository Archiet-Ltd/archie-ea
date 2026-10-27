"""mcp01 minor: a relationship the product wrote under a declared source says where it came from."""
from __future__ import annotations

import json

from app.modules.mcp.tests.test_tools import _make_element, _make_user, _mcp_call, _mint_oauth_token


def _ask_rows(client, token, element_id):
    out = _mcp_call(client, token, "ask_impact", {"element_id": element_id})
    body = json.loads(out["result"]["content"][0]["text"])
    body = body.get("data", body)
    body = body.get("result", body)
    return {row["element_id"]: row["relation"]["provenance"]["text"] for row in body["rows"]}


def test_links_written_under_a_declared_source_are_never_origin_not_recorded(client, db_session, make_org, login_as):
    from app.models import ArchiMateRelationship
    from app.services.change_source import change_source

    org = make_org("mcp-origin2")
    user = _make_user(db_session, org, "mcp-origin2@example.com")
    root = _make_element(db_session, org.id, "root")
    via_import = _make_element(db_session, org.id, "via-import")
    via_connector = _make_element(db_session, org.id, "via-connector")
    via_system = _make_element(db_session, org.id, "via-system")
    via_person = _make_element(db_session, org.id, "via-person")
    bare = _make_element(db_session, org.id, "bare")
    for target, source, uid in ((via_import, "import", None), (via_connector, "connector", None),
                                (via_system, "system", None), (via_person, "screen", user.id)):
        with change_source(source, user_id=uid):
            db_session.add(ArchiMateRelationship(type="Serving", source_id=root.id, target_id=target.id,
                                                 organization_id=org.id))
            db_session.flush()
    db_session.add(ArchiMateRelationship(type="Serving", source_id=root.id, target_id=bare.id, organization_id=org.id))
    db_session.flush()
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    texts = _ask_rows(client, token, root.id)

    for element in (via_import, via_connector, via_system, via_person):
        assert texts[element.id] != "origin not recorded", (element.name, texts[element.id])
    assert texts[via_person.id].startswith("Drawn by"), texts[via_person.id]
    assert texts[bare.id] == "origin not recorded", "a bare script's link is never given an origin it did not have"
