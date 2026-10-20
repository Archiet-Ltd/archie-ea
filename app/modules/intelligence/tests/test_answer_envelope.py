"""R1-B30 (TB-0106): the answer envelope, the answer store and the answer
address, through the real routes.

Pins: an answer opened by its address shows the same rows and computed time
(a snapshot, not a recomputation); an address from one organisation answers 404
to another exactly like one never issued; with no active organisation nothing
is read or written; a question about a foreign element stores nothing; the
envelope states its truth class, confidence, citations and freshness.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/conftest.py.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest


def _user(db_session, org_id, role="enterprise_architect"):
    from app.models.user import User

    user = User(
        email=f"b30-{uuid.uuid4().hex[:10]}@example.com",
        first_name="B30",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _graph(db_session, org_id):
    s1 = _element(db_session, org_id, "S1")
    s2 = _element(db_session, org_id, "S2")
    shared = _element(db_session, org_id, "Shared")
    only = _element(db_session, org_id, "OnlyS1")
    r1 = _link(db_session, org_id, s1, shared)
    r2 = _link(db_session, org_id, s2, shared)
    r3 = _link(db_session, org_id, s1, only)
    db_session.commit()
    return s1, s2, shared, only, (r1, r2, r3)


def _ask(client, **body):
    return client.post("/api/v1/intelligence/answers", json=body)


def _same_body(resp):
    """The response body without its per-request metadata (id and timestamp)."""
    body = dict(resp.get_json())
    body.pop("meta", None)
    return body


def _count(db_session, org_id=None):
    from app.extensions import db
    from app.models.answer_record import AnswerRecord

    stmt = db.select(db.func.count()).select_from(AnswerRecord)
    if org_id is not None:
        stmt = stmt.where(AnswerRecord.organization_id == org_id)
    return db_session.execute(stmt).scalar_one()


# --- the envelope and the permanent address -----------------------------------


def test_asking_stores_the_answer_and_returns_the_envelope(app, db_session, make_org, client, login_as):
    org = make_org("env-store")
    user = _user(db_session, org.id)
    s1, s2, shared, only, rels = _graph(db_session, org.id)
    login_as(client, user)

    resp = _ask(client, question="set_impact", element_ids=[s1.id, s2.id])

    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert len(data["address"]) == 32
    assert data["page_url"] == f"/intelligence/answers/{data['address']}"
    assert data["question_kind"] == "set_impact"
    assert data["truth_class"] == "drawn"
    assert data["confidence"] is None and data["confidence_basis"] == "drawn_facts_only"
    assert {r["element_id"] for r in data["rows"]} == {shared.id, only.id}
    assert data["summary"]["total"] == 2
    assert data["freshness"]["computed_at"] == data["computed_at"]
    assert data["freshness"]["derivation_state"] in ("current", "not_computed", "stale", "partial")
    assert {"type": "relationship", "id": rels[0].id} in data["citations"]
    assert {"type": "relationship", "id": rels[2].id} in data["citations"]
    assert _count(db_session, org.id) == 1


def test_an_answer_opened_by_its_address_shows_the_same_rows_and_computed_time(
    app, db_session, make_org, client, login_as
):
    org = make_org("env-snapshot")
    user = _user(db_session, org.id)
    s1, s2, shared, only, _rels = _graph(db_session, org.id)
    login_as(client, user)
    asked = _ask(client, question="set_impact", element_ids=[s1.id, s2.id]).get_json()["data"]

    # The model changes after the answer was stored: a snapshot must not follow it.
    extra = _element(db_session, org.id, "LateDependant")
    _link(db_session, org.id, shared, extra)
    db_session.commit()

    login_as(client, user)
    opened = client.get(f"/api/v1/intelligence/answers/{asked['address']}")
    assert opened.status_code == 200
    reopened = opened.get_json()["data"]
    assert reopened["rows"] == asked["rows"]
    assert reopened["computed_at"] == asked["computed_at"]
    assert reopened["summary"] == asked["summary"]
    assert extra.id not in {r["element_id"] for r in reopened["rows"]}

    # A new question is a new answer at a new address, and does see the change.
    login_as(client, user)
    fresh = _ask(client, question="set_impact", element_ids=[s1.id, s2.id]).get_json()["data"]
    assert fresh["address"] != asked["address"]
    assert extra.id in {r["element_id"] for r in fresh["rows"]}


def test_the_stored_page_renders_the_snapshot(app, db_session, make_org, client, login_as):
    org = make_org("env-page")
    user = _user(db_session, org.id)
    s1, s2, shared, only, _rels = _graph(db_session, org.id)
    login_as(client, user)
    asked = _ask(client, question="set_impact", element_ids=[s1.id, s2.id]).get_json()["data"]

    login_as(client, user)
    page = client.get(asked["page_url"])
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert f'data-answer-address="{asked["address"]}"' in html
    assert asked["computed_at"] in html
    for name in ("S1", "S2", "Shared", "OnlyS1"):
        assert name in html
    assert 'data-element-id="%d"' % shared.id in html
    assert "Recorded by people" in html


def test_an_address_from_another_organisation_is_not_found_exactly_like_an_unissued_one(
    app, db_session, make_org, client, login_as
):
    org_a, org_b = make_org("env-iso-a"), make_org("env-iso-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    s1, s2, *_ = _graph(db_session, org_a.id)
    login_as(client, user_a)
    asked = _ask(client, question="set_impact", element_ids=[s1.id, s2.id]).get_json()["data"]

    login_as(client, user_b)
    foreign_api = client.get(f"/api/v1/intelligence/answers/{asked['address']}")
    login_as(client, user_b)
    never_issued_api = client.get(f"/api/v1/intelligence/answers/{uuid.uuid4().hex}")
    login_as(client, user_b)
    malformed_api = client.get("/api/v1/intelligence/answers/not-an-address")
    login_as(client, user_b)
    foreign_page = client.get(asked["page_url"])
    login_as(client, user_b)
    never_issued_page = client.get(f"/intelligence/answers/{uuid.uuid4().hex}")

    assert foreign_api.status_code == never_issued_api.status_code == malformed_api.status_code == 404
    assert _same_body(foreign_api) == _same_body(never_issued_api) == _same_body(malformed_api)
    assert foreign_page.status_code == never_issued_page.status_code == 404
    assert "S1" not in foreign_page.get_data(as_text=True)

    # The owner can still open it: the rows exist and only the caller changed.
    login_as(client, user_a)
    assert client.get(asked["page_url"]).status_code == 200


def test_the_list_shows_only_the_callers_organisations_answers(app, db_session, make_org, client, login_as):
    org_a, org_b = make_org("env-list-a"), make_org("env-list-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    a1, a2, *_ = _graph(db_session, org_a.id)
    b1, b2, *_ = _graph(db_session, org_b.id)
    login_as(client, user_a)
    mine = _ask(client, question="set_impact", element_ids=[a1.id, a2.id]).get_json()["data"]["address"]
    login_as(client, user_b)
    theirs = _ask(client, question="set_impact", element_ids=[b1.id, b2.id]).get_json()["data"]["address"]

    login_as(client, user_a)
    listed = client.get("/api/v1/intelligence/answers").get_json()["data"]["answers"]
    assert [a["address"] for a in listed] == [mine]
    assert theirs not in repr(listed)
    assert all("rows" not in a for a in listed)

    login_as(client, user_a)
    page = client.get("/intelligence/answers").get_data(as_text=True)
    assert f'data-answer-item="{mine}"' in page
    assert theirs not in page


def test_the_page_says_so_when_nothing_has_been_asked(app, db_session, make_org, client, login_as):
    org = make_org("env-empty")
    user = _user(db_session, org.id)
    login_as(client, user)

    html = client.get("/intelligence/answers").get_data(as_text=True)

    assert "No answers saved yet" in html
    assert "data-answers-list" not in html


# --- organisation scoping ------------------------------------------------------


def test_a_question_about_a_foreign_element_is_not_found_and_stores_nothing(
    app, db_session, make_org, client, login_as
):
    org_a, org_b = make_org("env-foreign-a"), make_org("env-foreign-b")
    user_a = _user(db_session, org_a.id)
    foreign = _element(db_session, org_b.id, "Foreign")
    own = _element(db_session, org_a.id, "Own")
    _link(db_session, org_a.id, own, _element(db_session, org_a.id, "OwnDependant"))
    db_session.commit()
    before = _count(db_session)

    responses = []
    for body in (
        {"question": "set_impact", "element_ids": [foreign.id]},
        {"question": "impact", "element_id": foreign.id},
        {"question": "risk_propagation", "element_id": foreign.id},
        {"question": "impact", "element_id": 999_999_999},
    ):
        login_as(client, user_a)
        responses.append(_ask(client, **body))

    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert _same_body(responses[1]) == _same_body(responses[3])
    assert _count(db_session) == before


def test_a_set_with_a_foreign_seed_stores_only_the_callers_graph(app, db_session, make_org, client, login_as):
    org_a, org_b = make_org("env-mixed-a"), make_org("env-mixed-b")
    user_a = _user(db_session, org_a.id)
    own = _element(db_session, org_a.id, "Own")
    own_dep = _element(db_session, org_a.id, "OwnDependant")
    _link(db_session, org_a.id, own, own_dep)
    foreign = _element(db_session, org_b.id, "ForeignSeed")
    foreign_dep = _element(db_session, org_b.id, "ForeignDependant")
    _link(db_session, org_b.id, foreign, foreign_dep)
    db_session.commit()
    login_as(client, user_a)

    data = _ask(client, question="set_impact", element_ids=[own.id, foreign.id]).get_json()["data"]

    assert [r["element_id"] for r in data["rows"]] == [own_dep.id]
    assert data["summary"]["unresolved_seed_ids"] == [foreign.id]
    assert "ForeignDependant" not in repr(data)


def test_with_no_active_organisation_nothing_is_read_or_written(
    app, db_session, make_org, client, login_as, monkeypatch
):
    org = make_org("env-no-org")
    user = _user(db_session, org.id)
    s1, s2, *_ = _graph(db_session, org.id)
    login_as(client, user)
    stored = _ask(client, question="set_impact", element_ids=[s1.id, s2.id]).get_json()["data"]
    before = _count(db_session)

    from app.modules.intelligence.routes import answer_routes
    from app.modules.intelligence.services import answer_envelope

    monkeypatch.setattr(answer_routes, "current_org_id", lambda: None)
    monkeypatch.setattr(answer_envelope, "current_org_id", lambda: None)

    login_as(client, user)
    asked = _ask(client, question="set_impact", element_ids=[s1.id, s2.id])
    login_as(client, user)
    opened = client.get(f"/api/v1/intelligence/answers/{stored['address']}")
    login_as(client, user)
    listed = client.get("/api/v1/intelligence/answers")
    login_as(client, user)
    page = client.get(stored["page_url"])
    login_as(client, user)
    index = client.get("/intelligence/answers")

    assert asked.status_code == 400
    assert opened.status_code == 400
    assert listed.status_code == 400
    assert page.status_code == 404
    assert index.status_code == 200
    assert "data-answers-list" not in index.get_data(as_text=True)
    assert "Choose an organisation first" in index.get_data(as_text=True)
    assert _count(db_session) == before


def test_the_service_refuses_to_store_without_an_organisation(app, db_session, make_org):
    from app.modules.intelligence.services import answer_envelope

    org = make_org("env-svc-no-org")
    s1, s2, *_ = _graph(db_session, org.id)
    before = _count(db_session)

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        with pytest.raises(answer_envelope.AnswerNotAskable) as refused:
            answer_envelope.ask_and_store("set_impact", {"element_ids": [s1.id, s2.id]})
        assert refused.value.reason == "no_tenant_context"
        assert answer_envelope.open_answer("a" * 32) is None
        assert answer_envelope.recent_answers() == []

    assert _count(db_session) == before


def test_a_switched_organisation_user_stores_and_opens_in_the_active_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.org_role import OrgRole

    home, active = make_org("env-switch-home"), make_org("env-switch-active")
    user = _user(db_session, home.id)
    db_session.add(OrgRole(organization_id=active.id, user_id=user.id, role="architect"))
    home_seed = _element(db_session, home.id, "HomeSeed")
    _link(db_session, home.id, home_seed, _element(db_session, home.id, "HomeDependant"))
    s1, s2, shared, only, _rels = _graph(db_session, active.id)
    db_session.commit()

    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = active.id
    asked = _ask(client, question="set_impact", element_ids=[s1.id, s2.id])
    assert asked.status_code == 201
    address = asked.get_json()["data"]["address"]

    from app.models.answer_record import AnswerRecord

    stored = db_session.query(AnswerRecord).filter_by(address=address).one()
    assert stored.organization_id == active.id, "stored under the ACTIVE organisation, not the home one"
    # The home organisation's element is not askable while the active one is selected.
    login_as(client, user)
    with client.session_transaction() as sess:
        sess["current_org_id"] = active.id
    assert _ask(client, question="impact", element_id=home_seed.id).status_code == 404

    # Back in the home organisation the address is not found.
    login_as(client, user)
    with client.session_transaction() as sess:
        sess.pop("current_org_id", None)
    assert client.get(f"/api/v1/intelligence/answers/{address}").status_code == 404


# --- question shapes -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": "nonsense", "element_id": 1},
        {"question": "impact"},
        {"question": "impact", "element_id": "7"},
        {"question": "impact", "element_id": True},
        {"question": "impact", "element_id": 0},
        {"question": "impact", "element_id": 1, "max_depth": 11},
        {"question": "impact", "element_id": 1, "include_derived": "yes"},
        {"question": "impact", "element_id": 1, "direction": "sideways"},
        {"question": "set_impact"},
        {"question": "set_impact", "element_ids": []},
        {"question": "set_impact", "element_ids": "1,2"},
        {"question": "set_impact", "element_ids": [1, "2"]},
        {"question": "set_impact", "filter": {}},
        {"question": "set_impact", "filter": {"type": 5}},
        {"question": "risk_propagation", "element_id": -3},
    ],
)
def test_a_malformed_question_is_a_400_and_stores_nothing(app, db_session, make_org, client, login_as, body):
    org = make_org("env-bad-%s" % uuid.uuid4().hex[:6])
    user = _user(db_session, org.id)
    db_session.commit()
    before = _count(db_session)
    login_as(client, user)

    resp = client.post("/api/v1/intelligence/answers", json=body)

    assert resp.status_code == 400
    assert _count(db_session) == before


def test_a_body_that_is_not_an_object_is_a_400(app, db_session, make_org, client, login_as):
    org = make_org("env-bad-body")
    user = _user(db_session, org.id)
    login_as(client, user)

    assert client.post("/api/v1/intelligence/answers", json=[1, 2]).status_code == 400
    login_as(client, user)
    assert client.post("/api/v1/intelligence/answers", data="x", content_type="text/plain").status_code == 400


def test_a_filter_that_matches_nothing_in_the_callers_organisation_is_a_400(
    app, db_session, make_org, client, login_as
):
    org_a, org_b = make_org("env-filter-a"), make_org("env-filter-b")
    user_a = _user(db_session, org_a.id)
    _element(db_session, org_b.id, "Elsewhere", type_="BusinessActor", layer="business")
    db_session.commit()
    before = _count(db_session)
    login_as(client, user_a)

    resp = _ask(client, question="set_impact", filter={"type": "BusinessActor"})

    assert resp.status_code == 400
    assert "no_seed_elements" in repr(resp.get_json())
    assert _count(db_session) == before


def test_the_single_element_answer_carries_the_same_rows_as_the_impact_route(
    app, db_session, make_org, client, login_as
):
    org = make_org("env-impact-same")
    user = _user(db_session, org.id)
    s1, s2, shared, only, _rels = _graph(db_session, org.id)
    login_as(client, user)
    route = client.get(f"/api/v1/intelligence/impact/{s1.id}").get_json()["data"]
    login_as(client, user)

    stored = _ask(client, question="impact", element_id=s1.id).get_json()["data"]

    assert [r["element_id"] for r in stored["rows"]] == [r["element_id"] for r in route["rows"]]
    assert stored["summary"]["explicit_count"] == route["summary"]["explicit_count"]
    assert stored["summary"]["total"] == route["total"]


def test_a_risk_propagation_answer_is_stored_with_its_path_and_renders(app, db_session, make_org, client, login_as):
    from app.models.risk import Risk

    org = make_org("env-risk")
    user = _user(db_session, org.id)
    service = _element(db_session, org.id, "Order handling", type_="BusinessService", layer="business")
    supplier = _element(db_session, org.id, "Cloud supplier", type_="BusinessActor", layer="business")
    rel = _link(db_session, org.id, supplier, service)
    db_session.add(Risk(organization_id=org.id, archimate_element_id=supplier.id, title="Supplier insolvency",
                        likelihood=3, impact=4))
    db_session.commit()
    login_as(client, user)

    data = _ask(client, question="risk_propagation", element_id=service.id).get_json()["data"]

    assert data["rows"][0]["title"] == "Supplier insolvency"
    assert data["rows"][0]["path_elements"] == [service.id, supplier.id]
    assert {"type": "relationship", "id": rel.id} in data["citations"]
    assert {"type": "risk", "id": data["rows"][0]["risk_id"]} in data["citations"]
    login_as(client, user)
    html = client.get(data["page_url"]).get_data(as_text=True)
    assert "Supplier insolvency" in html
    assert "Cloud supplier" in html and "Order handling" in html
    assert 'data-indirect="true"' in html


def test_an_empty_risk_answer_says_no_open_risk_reaches_it(app, db_session, make_org, client, login_as):
    org = make_org("env-risk-empty")
    user = _user(db_session, org.id)
    service = _element(db_session, org.id, "Quiet service", type_="BusinessService", layer="business")
    db_session.commit()
    login_as(client, user)

    data = _ask(client, question="risk_propagation", element_id=service.id).get_json()["data"]

    assert data["rows"] == [] and data["truth_class"] is None
    assert data["confidence_basis"] == "no_rows"
    login_as(client, user)
    html = client.get(data["page_url"]).get_data(as_text=True)
    assert "No open risk reaches this" in html


# --- envelope contents ----------------------------------------------------------


def _derived(db_session, org_id, source, target, confidence=0.6, stale=False, chain=(1, 2)):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    now = _dt.datetime.utcnow()
    row = DerivedRelationship(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Serving",
        rule_id="R1",
        chain=list(chain),
        chain_element_ids=[source.id, target.id],
        depth=len(chain),
        confidence=1.0,
        provenance="derivation",
        engine_version="v1",
        computed_at=now,
        stale=stale,
        stale_since=now if stale else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_a_derived_row_makes_the_answer_derived_with_the_lowest_confidence(app, db_session, make_org):
    from app.modules.intelligence.services import answer_envelope as env
    org = make_org("env-derived")
    a = _element(db_session, org.id, "A")
    c = _element(db_session, org.id, "C")
    db_session.commit()
    result = {
        "rows": [{
            "element_id": c.id,
            "relation": {"kind": "derived", "type": "Serving", "depth": 2, "chain": [11, 12], "derived_id": 77,
                         "confidence": 0.6, "stale": True, "computed_at": _dt.datetime(2026, 10, 1, 9, 0)},
        }, {
            "element_id": a.id,
            "relation": {"kind": "derived", "type": "Serving", "depth": 2, "chain": [13], "derived_id": 78,
                         "confidence": 0.9, "stale": False, "computed_at": _dt.datetime(2026, 10, 2, 9, 0)},
        }],
        "summary": {"derivation_state": "stale"},
        "reasons": [],
        "elements": {},
        "total": 2,
    }
    built = env.build_envelope(env.KIND_IMPACT, {"element_id": 1}, result)

    assert built["truth_class"] == "derived"
    assert built["confidence"] == 0.6 and built["confidence_basis"] == "lowest_derived_row"
    assert built["freshness"]["stale_rows"] == 1
    assert built["freshness"]["oldest_derived_computed_at"] == "2026-10-01T09:00:00"
    assert {"type": "derived_relationship", "id": 77} in built["citations"]
    assert {"type": "relationship", "id": 11} in built["citations"]
    assert [r["truth_class"] for r in built["rows"]] == ["derived", "derived"]


def test_a_drawn_row_beside_a_derived_one_keeps_the_row_drawn(app, db_session, make_org):
    from app.modules.intelligence.services import answer_envelope as env

    result = {
        "rows": [{
            "element_id": 5,
            "seed_ids": [1, 2],
            "relations": [
                {"kind": "derived", "depth": 2, "chain": [], "derived_id": 9, "confidence": 0.5, "stale": False},
                {"kind": "explicit", "depth": 1, "chain": [4], "derived_id": None, "confidence": None,
                 "stale": False},
            ],
        }],
        "summary": {"derivation_state": "current"},
        "reasons": [],
        "elements": {},
        "total": 1,
    }

    built = env.build_envelope(env.KIND_SET_IMPACT, {}, result)

    assert built["rows"][0]["truth_class"] == "drawn"
    assert built["truth_class"] == "drawn"
    assert built["confidence"] == 0.5


def test_a_stored_derived_answer_round_trips_through_the_json_column(app, db_session, make_org, client, login_as):
    org = make_org("env-derived-store")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _link(db_session, org.id, a, b)
    _derived(db_session, org.id, a, c)
    db_session.commit()
    login_as(client, user)

    asked = _ask(client, question="impact", element_id=a.id, include_derived=True).get_json()["data"]

    assert asked["truth_class"] == "derived"
    assert asked["confidence"] == 1.0 and asked["confidence_basis"] == "lowest_derived_row"
    derived_row = next(r for r in asked["rows"] if r["relation"]["kind"] == "derived")
    assert isinstance(derived_row["relation"]["computed_at"], str)
    login_as(client, user)
    reopened = client.get(f"/api/v1/intelligence/answers/{asked['address']}").get_json()["data"]
    assert reopened["rows"] == asked["rows"]
    assert reopened["freshness"] == asked["freshness"]


def test_rows_over_the_ceiling_are_cut_and_the_stored_answer_says_so(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app.modules.intelligence.services import answer_envelope, query_service

    org = make_org("env-truncate")
    user = _user(db_session, org.id)
    root = _element(db_session, org.id, "Root")
    for i in range(4):
        _link(db_session, org.id, root, _element(db_session, org.id, f"Dep{i}"))
    db_session.commit()
    monkeypatch.setattr(query_service, "SET_IMPACT_MAX_ROWS", 2)
    monkeypatch.setattr(answer_envelope, "SET_IMPACT_MAX_ROWS", 2)

    for body in ({"question": "set_impact", "element_ids": [root.id]}, {"question": "impact", "element_id": root.id}):
        login_as(client, user)
        data = _ask(client, **body).get_json()["data"]
        assert len(data["rows"]) == 2
        assert data["truncated"] is True
        assert "rows_truncated" in data["reasons"]
        assert data["summary"]["total"] == 4


# --- review round: bounded input, named seeds never dropped, linear cost --------


def test_a_huge_element_list_is_refused_quickly_without_being_walked(app, db_session, make_org, client, login_as):
    import time

    org = make_org("env-huge-list")
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    started = time.monotonic()
    resp = _ask(client, question="set_impact", element_ids=list(range(1, 40_001)))
    elapsed = time.monotonic() - started

    assert resp.status_code == 400
    assert elapsed < 1.0, elapsed
    assert _count(db_session) == 0


def test_a_request_body_over_the_ceiling_is_refused(app, db_session, make_org, client, login_as):
    org = make_org("env-big-body")
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)
    padding = "x" * (2 * 1024 * 1024)

    resp = client.post("/api/v1/intelligence/answers",
                       json={"question": "set_impact", "element_ids": [1], "note": padding})

    assert resp.status_code == 400
    assert _count(db_session) == 0


def test_a_named_element_is_never_dropped_by_a_filter_that_matches_many(
    app, db_session, make_org, client, login_as
):
    org = make_org("env-named-first")
    user = _user(db_session, org.id)
    named = _element(db_session, org.id, "NamedApp", type_="Node", layer="technology")
    dependant = _element(db_session, org.id, "NamedDependant", type_="Node", layer="technology")
    _link(db_session, org.id, named, dependant)
    for i in range(30):
        _element(db_session, org.id, f"Actor{i}", type_="BusinessActor", layer="business")
    db_session.commit()
    login_as(client, user)

    resp = _ask(client, question="set_impact", element_ids=[named.id], filter={"type": "BusinessActor"})

    assert resp.status_code == 201
    data = resp.get_json()["data"]
    assert named.id in data["summary"]["seed_element_ids"]
    assert dependant.id in [r["element_id"] for r in data["rows"]]
    assert "seed_limit_reached" in data["reasons"]
    assert len(data["summary"]["seed_element_ids"]) == 25


def test_the_statement_count_grows_linearly_with_the_number_of_seeds(app, db_session, make_org):
    import threading

    from sqlalchemy import event

    from app.extensions import db
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("env-query-count")
    seeds = []
    for i in range(25):
        seed = _element(db_session, org.id, f"Seed{i}")
        _link(db_session, org.id, seed, _element(db_session, org.id, f"Dep{i}"))
        seeds.append(seed.id)
    db_session.commit()

    shapes = {}

    def count_for(n):
        counter = {"n": 0}
        seen = shapes.setdefault(n, {})
        me = threading.get_ident()

        def listener(conn, cursor, statement, params, context, executemany):
            if threading.get_ident() == me:
                counter["n"] += 1
                shape = " ".join(statement.split())[:90]
                seen[shape] = seen.get(shape, 0) + 1

        with app.test_request_context("/"):
            from flask import g

            g.current_org_id = org.id
            event.listen(db.engine, "before_cursor_execute", listener)
            try:
                result = IntelligenceQueryService.set_impact(seeds[:n])
            finally:
                event.remove(db.engine, "before_cursor_execute", listener)
        assert len(result["rows"]) == n
        return counter["n"]

    counts = {n: count_for(n) for n in (1, 5, 25)}
    per_seed = 20
    fixed = 20
    for n, used in counts.items():
        # On failure, name the five most repeated statements for the smallest run
        # so a count that grows is attributed to a statement, not guessed at.
        top = sorted(shapes[1].items(), key=lambda kv: -kv[1])[:5]
        assert used <= fixed + per_seed * n, (n, counts, top)
    # Linear, not worse: 25 seeds cost no more than five times what 5 seeds cost, plus slack.
    assert counts[25] <= 5 * counts[5] + fixed, counts


# --- single points of failure (R1-B83 slice 1) -----------------------------


def _value_stream(db_session, org_id, name, code, archimate_element_id=None):
    from app.models.unified_capability import ValueStream

    vs = ValueStream(
        name=name,
        code=code,
        organization_id=org_id,
        archimate_element_id=archimate_element_id,
    )
    db_session.add(vs)
    db_session.flush()
    return vs


def test_single_points_of_failure_ranked_by_distinct_value_streams(
    app, db_session, make_org, client, login_as
):
    """Three value streams, twenty elements: rank equals distinct value
    streams reached; an element reached by two paths of one stream counts once.
    """
    org = make_org("spof-rank")
    user = _user(db_session, org.id)
    login_as(client, user)

    vse1 = _element(db_session, org.id, "VS1 element", type_="BusinessService", layer="business")
    vse2 = _element(db_session, org.id, "VS2 element", type_="BusinessService", layer="business")
    vse3 = _element(db_session, org.id, "VS3 element", type_="BusinessService", layer="business")
    _value_stream(db_session, org.id, "Value Stream One", "VS1", vse1.id)
    _value_stream(db_session, org.id, "Value Stream Two", "VS2", vse2.id)
    _value_stream(db_session, org.id, "Value Stream Three", "VS3", vse3.id)

    elements = [
        _element(db_session, org.id, f"Element {i:02d}", type_="ApplicationComponent", layer="application")
        for i in range(1, 21)
    ]

    # VS1 reaches e1, e2, e3
    _link(db_session, org.id, elements[0], vse1)
    _link(db_session, org.id, elements[1], vse1)
    _link(db_session, org.id, elements[2], vse1)
    # VS2 reaches e4, e5, e6
    _link(db_session, org.id, elements[3], vse2)
    _link(db_session, org.id, elements[4], vse2)
    _link(db_session, org.id, elements[5], vse2)
    # VS3 reaches e7, e8, e9
    _link(db_session, org.id, elements[6], vse3)
    _link(db_session, org.id, elements[7], vse3)
    _link(db_session, org.id, elements[8], vse3)
    # e10 reached by VS1 and VS2 (rank 2)
    _link(db_session, org.id, elements[9], vse1)
    _link(db_session, org.id, elements[9], vse2)
    # e11 reached by all three (rank 3)
    _link(db_session, org.id, elements[10], vse1)
    _link(db_session, org.id, elements[10], vse2)
    _link(db_session, org.id, elements[10], vse3)
    # e12 reached by VS1 via two paths (direct and via e13) - counts once
    _link(db_session, org.id, elements[11], vse1)
    _link(db_session, org.id, elements[12], elements[11])  # e13 -> e12
    _link(db_session, org.id, elements[12], vse1)          # e13 -> vse1
    # e13 reached by VS1 (rank 1)
    # e14 reached by VS2 via two paths (direct and via e15) - counts once
    _link(db_session, org.id, elements[13], vse2)
    _link(db_session, org.id, elements[14], elements[13])  # e15 -> e14
    _link(db_session, org.id, elements[14], vse2)          # e15 -> vse2
    # e15 reached by VS2 (rank 1)
    # e16 reached by VS3 via two paths (direct and via e17) - counts once
    _link(db_session, org.id, elements[15], vse3)
    _link(db_session, org.id, elements[16], elements[15])  # e17 -> e16
    _link(db_session, org.id, elements[16], vse3)          # e17 -> vse3
    # e17 reached by VS3 (rank 1)
    # e18 reached by all three (rank 3)
    _link(db_session, org.id, elements[17], vse1)
    _link(db_session, org.id, elements[17], vse2)
    _link(db_session, org.id, elements[17], vse3)
    # e19 reached by VS1 and VS2 (rank 2)
    _link(db_session, org.id, elements[18], vse1)
    _link(db_session, org.id, elements[18], vse2)
    # e20 reached by VS3 only (rank 1)
    _link(db_session, org.id, elements[19], vse3)
    db_session.commit()

    data = _ask(client, question="single_points_of_failure").get_json()["data"]

    # Rank 3 first (e11, e18), then rank 2 (e10, e19), then rank 1.
    ranks = {row["element_id"]: row["rank"] for row in data["rows"]}
    assert ranks[elements[10].id] == 1   # e11 - rank 3
    assert ranks[elements[17].id] == 2   # e18 - rank 3
    assert ranks[elements[9].id] == 3    # e10 - rank 2
    assert ranks[elements[18].id] == 4   # e19 - rank 2
    # e12 reached by VS1 via two paths counts once (rank 1, appears once)
    element_ids = [row["element_id"] for row in data["rows"]]
    assert element_ids.count(elements[11].id) == 1
    assert ranks[elements[11].id] > 4
    # Every row carries owner and supplier (blank when not recorded).
    for row in data["rows"]:
        assert "owner" in row
        assert "supplier" in row
        assert row["supplier"] == "" or isinstance(row["supplier"], str)


def test_single_points_of_failure_permanent_address_no_new_record(
    app, db_session, make_org, client, login_as
):
    """The permanent address returns the stored answer with no new AnswerRecord."""
    org = make_org("spof-address")
    user = _user(db_session, org.id)
    vse = _element(db_session, org.id, "VS element", type_="BusinessService", layer="business")
    el = _element(db_session, org.id, "Dependent", type_="ApplicationComponent", layer="application")
    _link(db_session, org.id, el, vse)
    _value_stream(db_session, org.id, "Value Stream", "VS", vse.id)
    db_session.commit()
    login_as(client, user)

    before = _count(db_session, org.id)
    asked = _ask(client, question="single_points_of_failure").get_json()["data"]
    assert _count(db_session, org.id) == before + 1

    address = asked["address"]
    opened = client.get(f"/api/v1/intelligence/answers/{address}")
    assert opened.status_code == 200
    reopened = opened.get_json()["data"]
    assert reopened["rows"] == asked["rows"]
    assert reopened["computed_at"] == asked["computed_at"]
    assert reopened["summary"] == asked["summary"]
    # No new AnswerRecord was created by opening.
    assert _count(db_session, org.id) == before + 1


def test_single_points_of_failure_two_organisations_isolated(
    app, db_session, make_org, client, login_as
):
    """Two organisations with identical element ids: B sees none of A's."""
    org_a, org_b = make_org("spof-org-a"), make_org("spof-org-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)

    vse_a = _element(db_session, org_a.id, "VS A", type_="BusinessService", layer="business")
    el_a = _element(db_session, org_a.id, "Dep A", type_="ApplicationComponent", layer="application")
    _link(db_session, org_a.id, el_a, vse_a)
    _value_stream(db_session, org_a.id, "Value Stream A", "VSA", vse_a.id)

    vse_b = _element(db_session, org_b.id, "VS B", type_="BusinessService", layer="business")
    el_b = _element(db_session, org_b.id, "Dep B", type_="ApplicationComponent", layer="application")
    _link(db_session, org_b.id, el_b, vse_b)
    _value_stream(db_session, org_b.id, "Value Stream B", "VSB", vse_b.id)
    db_session.commit()

    login_as(client, user_a)
    data_a = _ask(client, question="single_points_of_failure").get_json()["data"]
    assert len(data_a["rows"]) >= 1
    a_element_ids = {row["element_id"] for row in data_a["rows"]}

    login_as(client, user_b)
    data_b = _ask(client, question="single_points_of_failure").get_json()["data"]
    b_element_ids = {row["element_id"] for row in data_b["rows"]}

    assert a_element_ids.isdisjoint(b_element_ids)


def test_single_points_of_failure_no_value_streams_empty_answer(
    app, db_session, make_org, client, login_as
):
    """An organisation with no value streams gets the empty answer with a reason."""
    org = make_org("spof-no-vs")
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    data = _ask(client, question="single_points_of_failure").get_json()["data"]

    assert data["rows"] == []
    assert data["summary"]["total"] == 0
    assert "no_data_recorded" in data["reasons"] or data["reasons"]


def test_single_points_of_failure_truncated_walk_is_marked(
    app, db_session, make_org, client, login_as, monkeypatch
):
    """A truncated walk is marked."""
    import app.modules.intelligence.services.query_service as qs

    org = make_org("spof-truncate")
    user = _user(db_session, org.id)
    vse = _element(db_session, org.id, "VS element", type_="BusinessService", layer="business")
    _value_stream(db_session, org.id, "Value Stream", "VS", vse.id)

    # Create more elements than the truncation ceiling, all reaching the VS.
    elements = [
        _element(db_session, org.id, f"Dep {i:04d}", type_="ApplicationComponent", layer="application")
        for i in range(60)
    ]
    for el in elements:
        _link(db_session, org.id, el, vse)
    db_session.commit()
    login_as(client, user)

    monkeypatch.setattr(qs, "SET_IMPACT_MAX_ROWS", 50)

    data = _ask(client, question="single_points_of_failure").get_json()["data"]

    assert data["truncated"] is True
    assert len(data["rows"]) == 50
    assert data["summary"]["total"] > 50


def test_single_points_of_failure_rows_state_count_and_names_of_streams(
    app, db_session, make_org, client, login_as
):
    """Each row carries the number of distinct value streams and names them;
    two paths from one stream count once."""
    org = make_org("spof-count")
    user = _user(db_session, org.id)
    login_as(client, user)
    vse1 = _element(db_session, org.id, "VS1 element", type_="BusinessService", layer="business")
    vse2 = _element(db_session, org.id, "VS2 element", type_="BusinessService", layer="business")
    vs1 = _value_stream(db_session, org.id, "Order to Cash", "VS1", vse1.id)
    vs2 = _value_stream(db_session, org.id, "Procure to Pay", "VS2", vse2.id)
    both = _element(db_session, org.id, "Shared platform", type_="ApplicationComponent", layer="application")
    twice = _element(db_session, org.id, "Billing", type_="ApplicationComponent", layer="application")
    relay = _element(db_session, org.id, "Relay", type_="ApplicationComponent", layer="application")
    _link(db_session, org.id, both, vse1)
    _link(db_session, org.id, both, vse2)
    _link(db_session, org.id, twice, vse1)
    _link(db_session, org.id, twice, relay)
    _link(db_session, org.id, relay, vse1)
    db_session.commit()

    rows = {
        r["element_id"]: r
        for r in _ask(client, question="single_points_of_failure").get_json()["data"]["rows"]
    }
    assert rows[both.id]["value_stream_count"] == 2
    assert rows[both.id]["value_streams"] == [
        {"id": vs1.id, "name": "Order to Cash"},
        {"id": vs2.id, "name": "Procure to Pay"},
    ]
    assert rows[twice.id]["value_stream_count"] == 1
    assert rows[twice.id]["value_streams"] == [{"id": vs1.id, "name": "Order to Cash"}]


def test_single_points_of_failure_equal_counts_order_by_name_then_id(
    app, db_session, make_org, client, login_as
):
    """Equal counts order by name, the same on consecutive runs."""
    org = make_org("spof-tie")
    user = _user(db_session, org.id)
    login_as(client, user)
    vse = _element(db_session, org.id, "VS element", type_="BusinessService", layer="business")
    _value_stream(db_session, org.id, "Stream", "VS", vse.id)
    # Created in an order that is not alphabetical.
    for name in ("Zeta", "Mid", "Alpha"):
        _link(
            db_session,
            org.id,
            _element(db_session, org.id, name, type_="ApplicationComponent", layer="application"),
            vse,
        )
    db_session.commit()

    orders = []
    for _ in range(2):
        data = _ask(client, question="single_points_of_failure").get_json()["data"]
        orders.append([data["elements"][str(r["element_id"])]["name"] for r in data["rows"]])
    assert orders[0] == orders[1]
    assert orders[0] == ["Alpha", "Mid", "Zeta"]


def test_single_points_of_failure_stream_walks_are_capped_and_reported(
    app, db_session, make_org, client, login_as
):
    """With one more value stream than the cap, the answer is marked truncated
    and the summary reports streams considered of streams existing."""
    import app.modules.intelligence.services.query_service as qs

    cap = qs.SPOF_MAX_VALUE_STREAMS
    org = make_org("spof-cap")
    user = _user(db_session, org.id)
    login_as(client, user)
    for i in range(cap + 1):
        vse = _element(db_session, org.id, f"VS element {i:02d}", type_="BusinessService", layer="business")
        _value_stream(db_session, org.id, f"Stream {i:02d}", f"VS{i:02d}", vse.id)
        _link(
            db_session,
            org.id,
            _element(db_session, org.id, f"Dep {i:02d}", type_="ApplicationComponent", layer="application"),
            vse,
        )
    db_session.commit()

    data = _ask(client, question="single_points_of_failure").get_json()["data"]
    assert data["truncated"] is True
    assert data["summary"]["value_streams_considered"] == cap
    assert data["summary"]["value_streams_existing"] == cap + 1
    assert f"{cap} of {cap + 1} value streams" in data["summary"]["statement"]
    assert len(data["rows"]) == cap
