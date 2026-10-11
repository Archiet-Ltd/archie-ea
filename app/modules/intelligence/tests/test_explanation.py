"""Why a worked-out connection exists: the explanation behind "Why?".

Two organisations throughout. The explanation of a connection must name only
the caller's organisation's records -- its relationships, elements, people and
decisions -- and every link it carries must open the real record it names.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid

from app.modules.intelligence.tests.test_api_routes import (
    _insert_derived_row,
    _make_element,
    _make_user,
)


def _relationship(db_session, org_id, source, target, type_="Serving", created_by=None, created_at=None):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id,
        target_id=target.id,
        type=type_,
        organization_id=org_id,
        created_by_id=getattr(created_by, "id", created_by),
        created_at=created_at,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _decision(db_session, org_id, title, element_ids):
    from app.models.architecture_decision import ArchitectureDecision

    row = ArchitectureDecision(
        decision_id=f"XD-{uuid.uuid4().hex[:8]}",
        title=title,
        status="accepted",
        organization_id=org_id,
        archimate_element_ids=list(element_ids),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _named_user(db_session, org, first, last):
    user = _make_user(db_session, org)
    user.first_name = first
    user.last_name = last
    db_session.flush()
    return user


def _two_organisations(db_session, make_org):
    """Organisation A holds  Portal --serves--> Gateway --serves--> Mainframe  and a
    connection worked out from Portal to Mainframe. Organisation B holds its own
    elements, relationship, person and decision, and nothing in A points at them
    except where a test plants it deliberately."""
    org_a = make_org("why-a")
    org_b = make_org("why-b")
    ada = _named_user(db_session, org_a, "Ada", "Lovelace")
    bob = _named_user(db_session, org_b, "Bob", "Foreign")

    portal = _make_element(db_session, org_a.id, f"Portal-{uuid.uuid4().hex[:6]}")
    gateway = _make_element(db_session, org_a.id, f"Gateway-{uuid.uuid4().hex[:6]}")
    mainframe = _make_element(db_session, org_a.id, f"Mainframe-{uuid.uuid4().hex[:6]}")
    drawn_at = _dt.datetime(2026, 9, 3, 10, 30)
    first = _relationship(db_session, org_a.id, gateway, portal, created_by=ada, created_at=drawn_at)
    second = _relationship(db_session, org_a.id, mainframe, gateway, created_by=None, created_at=drawn_at)
    decision_a = _decision(db_session, org_a.id, "Standardise on one integration platform", [gateway.id])

    b_one = _make_element(db_session, org_b.id, f"Secret-{uuid.uuid4().hex[:6]}")
    b_two = _make_element(db_session, org_b.id, f"Hidden-{uuid.uuid4().hex[:6]}")
    b_rel = _relationship(db_session, org_b.id, b_one, b_two, created_by=bob, created_at=drawn_at)
    decision_b = _decision(db_session, org_b.id, "Foreign decision", [b_one.id, gateway.id])

    return {
        "org_a": org_a, "org_b": org_b, "ada": ada, "bob": bob,
        "portal": portal, "gateway": gateway, "mainframe": mainframe,
        "first": first, "second": second, "decision_a": decision_a,
        "b_one": b_one, "b_two": b_two, "b_rel": b_rel, "decision_b": decision_b,
    }


def _hrefs(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "href" and item:
                yield item
            else:
                yield from _hrefs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _hrefs(item)


def test_the_explanation_names_each_drawn_link_who_drew_it_when_and_the_rule(
    app, db_session, make_org, client, login_as
):
    w = _two_organisations(db_session, make_org)
    row = _insert_derived_row(
        db_session, w["org_a"].id, w["mainframe"], w["portal"],
        chain=[w["second"].id, w["first"].id],
        chain_element_ids=[w["mainframe"].id, w["gateway"].id, w["portal"].id],
        depth=2, derived_type="Serving", rule_id="table:Serving:Serving",
    )
    db_session.commit()
    names = {k: w[k].name for k in ("portal", "gateway", "mainframe")}
    ids = {k: w[k].id for k in ("first", "second", "decision_a")}
    row_id = row.id

    login_as(client, w["ada"])
    resp = client.get(f"/api/v1/intelligence/derived/{row_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    why = resp.get_json()["data"]["explanation"]

    assert why["derived_id"] == row_id
    assert why["rule"] == "A serving link followed by a serving link gives a serving link, by the ArchiMate derivation table."
    assert why["complete"] is True
    links = why["links"]
    assert [link["relationship_id"] for link in links] == [ids["second"], ids["first"]]
    assert [link["position"] for link in links] == [1, 2]

    second, first = links
    assert second["sentence"] == f"{names['gateway']} depends on {names['mainframe']}."
    assert (second["source"]["name"], second["target"]["name"]) == (names["mainframe"], names["gateway"])
    # Nobody is recorded as having drawn the second link: said so, not invented.
    assert second["drawn_by"] is None and second["drawn_by_reason"] == "drawn_by_not_recorded"
    assert first["sentence"] == f"{names['portal']} depends on {names['gateway']}."
    assert first["drawn_by"] == "Ada Lovelace" and first["drawn_by_reason"] is None
    assert first["drawn_at"].startswith("2026-09-03T10:30")

    assert [d["id"] for d in why["decisions"]] == [ids["decision_a"]]
    assert why["decisions"][0]["title"] == "Standardise on one integration platform"


def test_the_explanation_lists_only_the_callers_organisations_facts(
    app, db_session, make_org, client, login_as
):
    """Organisation A's derived row is planted with a chain that runs through
    organisation B's relationship and element, and one of A's relationships is
    stamped with B's person. None of B's records may surface: the foreign link
    reads as no longer in the model, the foreign person as not recorded, and B's
    decision -- although it names one of A's elements -- is not listed."""
    w = _two_organisations(db_session, make_org)
    w["first"].created_by_id = w["bob"].id
    db_session.flush()
    row = _insert_derived_row(
        db_session, w["org_a"].id, w["gateway"], w["b_two"],
        chain=[w["first"].id, w["b_rel"].id],
        chain_element_ids=[w["gateway"].id, w["b_one"].id, w["b_two"].id],
        depth=2, derived_type="Serving", rule_id="table:Serving:Serving",
    )
    db_session.commit()
    row_id = row.id
    foreign = {"names": {w["b_one"].name, w["b_two"].name, "Foreign decision", "Bob", "Foreign"}}
    a_decision = w["decision_a"].id

    login_as(client, w["ada"])
    resp = client.get(f"/api/v1/intelligence/derived/{row_id}")
    assert resp.status_code == 200
    why = resp.get_json()["data"]["explanation"]

    text = json.dumps(why)
    for name in foreign["names"]:
        assert name not in text, name
    first, planted = why["links"]
    assert first["resolved"] is True and first["drawn_by"] is None
    # The planted id is the one A's own derived row stored; it resolves to
    # nothing of A's, so it is shown as not recorded, never as B's record.
    assert planted == {
        "position": 2, "relationship_id": None, "chain_id": w["b_rel"].id, "resolved": False,
        "reason": "relationship_not_recorded",
    }
    assert why["complete"] is False
    assert why["target"] == {"id": None, "name": None, "href": None, "reason": "element_not_found"}

    # The id-and-endpoint chain is the same links, not a second read.
    expanded = resp.get_json()["data"]["expanded_chain"]
    assert [e["id"] for e in expanded] == [w["first"].id, w["b_rel"].id]
    assert expanded[1] == {"id": w["b_rel"].id, "unresolved": True, "derived_from": row_id}
    assert (expanded[0]["source_id"], expanded[0]["target_id"]) == (
        first["source_id"], first["target_id"]
    )
    assert [d["id"] for d in why["decisions"]] == [a_decision]

    # Every record the explanation names is one of A's.
    element_ids = {
        end["id"]
        for link in why["links"] if link["resolved"]
        for end in (link["source"], link["target"])
    } | {why["source"]["id"]} - {None}
    assert element_ids == {w["gateway"].id, w["portal"].id}
    assert {link["relationship_id"] for link in why["links"]} - {None} == {w["first"].id}
    assert w["b_rel"].id not in {link["relationship_id"] for link in why["links"]}


def test_another_organisation_cannot_read_the_explanation_at_all(
    app, db_session, make_org, client, login_as
):
    w = _two_organisations(db_session, make_org)
    row = _insert_derived_row(
        db_session, w["org_a"].id, w["mainframe"], w["portal"],
        chain=[w["second"].id, w["first"].id],
        chain_element_ids=[w["mainframe"].id, w["gateway"].id, w["portal"].id],
        depth=2, derived_type="Serving", rule_id="table:Serving:Serving",
    )
    db_session.commit()
    row_id = row.id
    bob_id = w["bob"].id

    login_as(client, w["ada"])
    own = client.get(f"/api/v1/intelligence/derived/{row_id}")
    assert own.status_code == 200
    assert len(own.get_json()["data"]["explanation"]["links"]) == 2

    login_as(client, bob_id)
    foreign = client.get(f"/api/v1/intelligence/derived/{row_id}")
    assert foreign.status_code == 404
    assert "explanation" not in foreign.get_data(as_text=True)


def test_every_link_in_the_explanation_opens_the_real_record_it_names(
    app, db_session, make_org, client, login_as
):
    w = _two_organisations(db_session, make_org)
    row = _insert_derived_row(
        db_session, w["org_a"].id, w["mainframe"], w["portal"],
        chain=[w["second"].id, w["first"].id],
        chain_element_ids=[w["mainframe"].id, w["gateway"].id, w["portal"].id],
        depth=2, derived_type="Serving", rule_id="table:Serving:Serving",
    )
    db_session.commit()
    row_id = row.id

    login_as(client, w["ada"])
    why = client.get(f"/api/v1/intelligence/derived/{row_id}").get_json()["data"]["explanation"]

    named = {}
    for link in why["links"]:
        for end in (link["source"], link["target"]):
            named[end["href"]] = end["name"]
    for end in (why["source"], why["target"]):
        named[end["href"]] = end["name"]
    for decision in why["decisions"]:
        named[decision["href"]] = decision["title"]
    hrefs = list(_hrefs(why))
    assert hrefs and set(hrefs) == set(named)
    assert len(set(hrefs)) == 4  # three elements and one decision

    for href in set(hrefs):
        login_as(client, w["ada"])
        page = client.get(href)
        assert page.status_code == 200, (href, page.status_code)
        assert named[href] in page.get_data(as_text=True), href

    # The same links are A's records, not addresses anyone can open: B gets 404.
    # (A fresh identity map, as each real request has.)
    bob_id = w["bob"].id
    db_session.expunge_all()
    for href in set(hrefs):
        login_as(client, bob_id)
        assert client.get(href).status_code == 404, href


def test_an_unknown_rule_is_said_to_be_not_recorded_rather_than_guessed(
    app, db_session, make_org, client, login_as
):
    w = _two_organisations(db_session, make_org)
    row = _insert_derived_row(
        db_session, w["org_a"].id, w["gateway"], w["portal"],
        chain=[w["first"].id], chain_element_ids=[w["gateway"].id, w["portal"].id],
        depth=1, derived_type="Serving", rule_id="serving-through-serving",
    )
    db_session.commit()
    row_id = row.id

    login_as(client, w["ada"])
    why = client.get(f"/api/v1/intelligence/derived/{row_id}").get_json()["data"]["explanation"]
    assert why["rule"] is None
    assert why["rule_reason"] == "rule_not_recorded"


def test_the_rule_description_reads_the_rule_table_and_refuses_ids_it_did_not_produce():
    from app.services.archimate_derivation_service import describe_rule

    assert describe_rule("table:Serving:Access") == (
        "A serving link followed by an access link gives an access link, by the ArchiMate derivation table."
    )
    assert describe_rule("transparent:Composition:Serving").startswith(
        "A composition link followed by a serving link gives a serving link:"
    )
    assert describe_rule("fallback:Serving:Flow").startswith(
        "A serving link followed by a flow link gives an association link:"
    )
    # A branch label that does not match what the table would do is not believed.
    assert describe_rule("table:Serving:Flow") is None
    assert describe_rule("table:Nonsense:Serving") is None
    assert describe_rule(None) is None
    assert describe_rule("serving-through-serving") is None


def test_a_link_sentence_follows_the_one_wording_table_and_never_has_a_gap():
    from app.modules.intelligence.services.plain_terms import link_sentence

    assert link_sentence(source_name="Gateway", target_name="Portal", relation_type="Serving") == (
        "Portal depends on Gateway."
    )
    assert link_sentence(source_name="CRM", target_name="Customer", relation_type="Access") == (
        "CRM accesses Customer."
    )
    assert link_sentence(source_name=None, target_name="Portal", relation_type="Serving") is None
    assert link_sentence(source_name="A", target_name="B", relation_type="Unknown") is None


def test_every_absence_code_an_explanation_carries_is_in_the_one_vocabulary():
    """ADR 0008: absence codes come from reason_codes.REASON_CODES, never
    invented inline. Pins both the declared set and every code-shaped value
    explain_fact actually writes."""
    import inspect

    from app.modules.intelligence.services import explanation
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    assert explanation.EXPLANATION_REASON_CODES <= REASON_CODES
    assert "element_not_found" in explanation.EXPLANATION_REASON_CODES

    # No reason string literal in the module other than through the names
    # validated against the vocabulary.
    source = inspect.getsource(explanation)
    import re

    literals = set(re.findall(r'"([a-z]+(?:_[a-z]+)*_not_[a-z_]+)"', source))
    assert literals <= REASON_CODES, literals - REASON_CODES
