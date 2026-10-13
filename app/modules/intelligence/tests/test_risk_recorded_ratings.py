"""Tests for the recorded-ratings blocks on the risk answer (L6).

Beside its risks, and entering no score, the answer carries the four risk words
recorded on the application component, the recorded risk level, priority and
mitigation of the work packages seeded on the element, and the review items of
the review sessions that name the element with their recorded 0-100 scores.
Nothing is aggregated, ranked or defaulted, and a foreign row never appears.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

import pytest
from sqlalchemy import event

RATING_KEYS = {
    "technical_risk", "business_risk", "vendor_risk", "obsolescence_risk", "scale", "reason",
    "truth_class",
}
PACKAGE_KEYS = {
    "work_package_id", "name", "risk_level", "priority", "risk_mitigation",
    "risk_level_default_possible", "priority_default_possible",
}
REVIEW_KEYS = {
    "review_item_id", "review_number", "title", "review_type", "status", "is_open", "session_id",
    "session_name", "compliance_score", "risk_score", "quality_score", "overall_score", "scale",
    "basis",
}


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _component(db_session, org_id, element, **ratings):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=element.name, organization_id=org_id, archimate_element_id=element.id, **ratings)
    db_session.add(comp)
    db_session.flush()
    return comp


def _risk(db_session, org_id, element):
    from app.models.risk import Risk

    row = Risk(organization_id=org_id, archimate_element_id=element.id, title="A risk", likelihood=3, impact=4)
    db_session.add(row)
    db_session.flush()
    return row


def _package(db_session, element, **kwargs):
    from app.models.unified_work_package import UnifiedWorkPackage

    wp = UnifiedWorkPackage(
        name=kwargs.pop("name", "Migrate"), archimate_element_id=element.id,
        business_capability="Test Capability", **kwargs,
    )
    db_session.add(wp)
    db_session.flush()
    return wp


def _user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"ratings-{uuid.uuid4().hex[:8]}@example.com", first_name="Test", last_name="User",
        organization_id=org.id, role=role, is_org_admin=True, confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _session(db_session, org_id, ids, name="Board"):
    from app.models.architecture_review_board import ArchitectureReviewBoard

    row = ArchitectureReviewBoard(
        organization_id=org_id, name=name, board_number=f"ARB-{uuid.uuid4().hex[:10]}",
        scheduled_date=datetime(2026, 10, 1), impacted_element_ids=ids,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _item(db_session, org_id, session, submitter, *, title="Review", status="submitted", **scores):
    from app.models.architecture_review_board import ARBReviewItem

    row = ARBReviewItem(
        organization_id=org_id, arb_session_id=session.id, submitter_id=submitter.id,
        review_number=f"REV-{uuid.uuid4().hex[:10]}", title=title, review_type="architecture",
        status=status, **scores,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _answer(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.risk_for_element(element_id)


class _Counter:
    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def touching(self, table):
        pattern = re.compile(rf"\bFROM {table}\b")
        return sum(1 for s in self.statements if pattern.search(s))


@pytest.fixture
def counter(app):
    from app import db

    c = _Counter()
    event.listen(db.engine, "before_cursor_execute", c)
    try:
        yield c
    finally:
        event.remove(db.engine, "before_cursor_execute", c)


def _world(db_session, make_org, slug):
    """Organisation A: an application with a component rated high, a critical
    work package, a review session naming it with two items, and a direct risk."""
    org = make_org(slug)
    app_a = _element(db_session, org.id, "AppA")
    _component(db_session, org.id, app_a, technical_risk="high")
    _risk(db_session, org.id, app_a)
    wp = _package(db_session, app_a, risk_level="critical", priority="high", risk_mitigation="Dual-source")
    submitter = _user(db_session, org)
    session = _session(db_session, org.id, [app_a.id], name="Q3 board")
    open_item = _item(
        db_session, org.id, session, submitter, title="Open item", status="submitted",
        compliance_score=80, risk_score=40, quality_score=70, overall_score=65,
    )
    closed_item = _item(db_session, org.id, session, submitter, title="Closed item", status="approved")
    db_session.commit()
    return org, app_a, wp, session, submitter, open_item, closed_item


# (1) all three blocks
def test_recorded_ratings_packages_and_review_items_are_carried_as_recorded(app, db_session, make_org):
    org, app_a, wp, session, submitter, open_item, closed_item = _world(db_session, make_org, "rr-1")

    result = _answer(app, org.id, app_a.id)

    assert result["recorded_ratings"]["technical_risk"] == "high"
    assert result["recorded_ratings"]["business_risk"] is None
    assert result["recorded_ratings"]["reason"] is None
    (package,) = result["work_package_risk"]
    assert package["risk_level"] == "critical" and package["priority"] == "high"
    assert package["risk_mitigation"] == "Dual-source"
    assert package["risk_level_default_possible"] is True
    first, second = result["review_items"]
    assert (first["review_item_id"], second["review_item_id"]) == (open_item.id, closed_item.id)
    assert first["is_open"] is True and second["is_open"] is False
    assert (first["compliance_score"], first["risk_score"], first["overall_score"]) == (80.0, 40.0, 65.0)
    assert first["session_name"] == "Q3 board" and first["scale"] == "0-100"
    assert first["basis"] == "recorded_by_review_board"
    assert result["review_items_reason"] is None and result["work_package_risk_reason"] is None


# (2) foreign sessions and items never appear; the red run proves the predicate
def _foreign_session(db_session, make_org, slug):
    org, app_a, wp, session, submitter, open_item, closed_item = _world(db_session, make_org, slug)
    org_b = make_org(f"{slug}-b")
    b_user = _user(db_session, org_b)
    foreign_session = _session(db_session, org_b.id, [app_a.id], name="B board")
    foreign_item = _item(db_session, org_b.id, foreign_session, b_user, title="B item")
    db_session.commit()
    return org, app_a, session, foreign_session, foreign_item


def _item_ids(answer):
    return {i["review_item_id"] for i in answer["review_items"] or []}


def test_foreign_session_naming_this_element_lists_nothing(app, db_session, make_org):
    org, app_a, session, foreign_session, foreign_item = _foreign_session(db_session, make_org, "rr-2a")

    ids = _item_ids(_answer(app, org.id, app_a.id))

    assert foreign_item.id not in ids and len(ids) == 2


def test_foreign_item_attached_to_this_session_lists_nothing(app, db_session, make_org):
    org, app_a, wp, session, submitter, open_item, closed_item = _world(db_session, make_org, "rr-2b")
    org_b = make_org("rr-2b-b")
    b_user = _user(db_session, org_b)
    stray = _item(db_session, org_b.id, session, b_user, title="Stray B item")
    db_session.commit()

    assert stray.id not in _item_ids(_answer(app, org.id, app_a.id))


def test_mutation_proof_review_predicate_keeps_the_foreign_rows_out(app, db_session, make_org, monkeypatch):
    from app import db
    from app.modules.intelligence.services import query_service
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org, app_a, session, foreign_session, foreign_item = _foreign_session(db_session, make_org, "rr-2m")

    def listed():
        monkeypatch.setattr(query_service, "current_org_id", lambda: org.id)
        with app.test_request_context("/"):
            return _item_ids(IntelligenceQueryService.risk_for_element(app_a.id))

    assert foreign_item.id not in listed()  # control

    monkeypatch.setattr(query_service, "_review_tenant_predicate", lambda model, org_id: db.true())
    with pytest.raises(AssertionError):
        assert foreign_item.id not in listed()


def test_element_of_another_organisation_answers_not_found_with_null_blocks(app, db_session, make_org):
    org, app_a, wp, session, submitter, open_item, closed_item = _world(db_session, make_org, "rr-3a")
    org_b = make_org("rr-3b")
    db_session.commit()

    result = _answer(app, org_b.id, app_a.id)

    assert result["recorded_ratings"] is None
    assert result["work_package_risk"] is None and result["work_package_risk_reason"] == "element_not_found"
    assert result["review_items"] is None and result["review_items_reason"] == "element_not_found"


# (4) (5) (6) (7) not recorded
def test_unrecorded_ratings_and_a_non_component_element(app, db_session, make_org):
    org = make_org("rr-4")
    bare = _element(db_session, org.id, "Bare")
    _component(db_session, org.id, bare)
    process = _element(db_session, org.id, "Process", type_="BusinessProcess", layer="business")
    db_session.commit()

    bare_ratings = _answer(app, org.id, bare.id)["recorded_ratings"]
    assert all(bare_ratings[k] is None for k in ("technical_risk", "business_risk", "vendor_risk", "obsolescence_risk"))
    assert bare_ratings["reason"] == "no_risk_recorded" and bare_ratings["scale"] == "low-medium-high-critical"

    assert _answer(app, org.id, process.id)["recorded_ratings"]["reason"] == "no_application_component"


def test_work_package_words_are_carried_as_recorded_including_a_default_and_a_null(app, db_session, make_org):
    from app import db

    org = make_org("rr-5")
    el = _element(db_session, org.id, "AppA")
    _risk(db_session, org.id, el)
    _package(db_session, el, name="Defaulted")
    explicit_null = _package(db_session, el, name="Explicit null")
    db_session.commit()
    db.session.execute(
        db.text("UPDATE unified_work_packages SET risk_level = NULL WHERE id = :id"), {"id": explicit_null.id}
    )
    db_session.commit()

    entries = {e["name"]: e for e in _answer(app, org.id, el.id)["work_package_risk"]}

    assert entries["Defaulted"]["risk_level"] == "medium"
    assert entries["Defaulted"]["risk_level_default_possible"] is True
    assert entries["Explicit null"]["risk_level"] is None

    empty = _element(db_session, org.id, "Empty")
    _risk(db_session, org.id, empty)
    db_session.commit()
    none_recorded = _answer(app, org.id, empty.id)
    assert none_recorded["work_package_risk"] is None
    assert none_recorded["work_package_risk_reason"] == "no_work_package_recorded"


def test_review_items_match_a_string_id_and_report_null_scores_and_status(app, db_session, make_org):
    org = make_org("rr-6")
    el = _element(db_session, org.id, "AppA")
    _risk(db_session, org.id, el)
    lonely = _element(db_session, org.id, "Lonely")
    _risk(db_session, org.id, lonely)
    user = _user(db_session, org)
    session = _session(db_session, org.id, [str(el.id)])
    item = _item(db_session, org.id, session, user, status="submitted")
    db_session.commit()

    from app import db

    db.session.execute(db.text("UPDATE arb_review_items SET status = NULL WHERE id = :id"), {"id": item.id})
    db_session.commit()

    matched = _answer(app, org.id, el.id)["review_items"][0]
    assert matched["review_item_id"] == item.id
    assert matched["is_open"] is None and matched["status"] is None
    assert all(matched[k] is None for k in ("compliance_score", "risk_score", "quality_score", "overall_score"))

    unmatched = _answer(app, org.id, lonely.id)
    assert unmatched["review_items"] is None
    assert unmatched["review_items_reason"] == "review_item_not_visible"


def test_the_no_risk_branch_still_computes_the_three_blocks(app, db_session, make_org):
    org = make_org("rr-7")
    el = _element(db_session, org.id, "AppA")
    _component(db_session, org.id, el, business_risk="medium")
    _package(db_session, el, risk_level="low")
    db_session.commit()

    result = _answer(app, org.id, el.id)

    assert result["risks"] == [] and result["reasons"] == ["no_risk_recorded"]
    assert result["recorded_ratings"]["business_risk"] == "medium"
    assert result["work_package_risk"][0]["risk_level"] == "low"


# (8) shapes
def test_block_and_entry_shapes(app, db_session, make_org):
    org, app_a, wp, session, submitter, open_item, closed_item = _world(db_session, make_org, "rr-8")

    result = _answer(app, org.id, app_a.id)

    assert set(result["recorded_ratings"].keys()) == RATING_KEYS
    assert all(set(e.keys()) == PACKAGE_KEYS for e in result["work_package_risk"])
    assert all(set(e.keys()) == REVIEW_KEYS for e in result["review_items"])


# (9) a constant number of selects
def _counts(app, counter, world):
    org, app_a = world[0], world[1]
    counter.statements.clear()
    _answer(app, org.id, app_a.id)
    return (
        counter.touching("architecture_review_boards"),
        counter.touching("arb_review_items"),
        counter.touching("unified_work_packages"),
    )


def test_review_selects_are_the_same_for_one_item_and_for_twenty(app, db_session, make_org, counter):
    one_world = _world(db_session, make_org, "rr-9a")
    many_world = _world(db_session, make_org, "rr-9b")
    org, app_a, wp, session, submitter = many_world[:5]
    for index in range(18):
        _item(db_session, org.id, session, submitter, title=f"Extra {index}")
    db_session.commit()

    one = _counts(app, counter, one_world)
    many = _counts(app, counter, many_world)

    assert one == many
    assert one[0] == 1 and one[1] == 1


# (10) nothing the risk answer already said changes
def test_risks_rows_and_summary_are_the_same_with_and_without_the_blocks(app, db_session, make_org):
    org = make_org("rr-10")
    el = _element(db_session, org.id, "AppA")
    tech = _element(db_session, org.id, "Tech", type_="Node", layer="technology")
    from app.models import ArchiMateRelationship

    db_session.add(ArchiMateRelationship(source_id=el.id, target_id=tech.id, type="Serving", organization_id=org.id))
    _component(db_session, org.id, el)
    _risk(db_session, org.id, el)
    db_session.commit()

    def stable(answer):
        risk = answer["risks"][0]
        summary = {k: v for k, v in risk["affected_summary"].items() if k != "latency_ms"}
        return risk["affected_rows"], summary, risk["risk_score"], risk["risk_level"], answer["reasons"]

    before = stable(_answer(app, org.id, el.id))
    _package(db_session, el, risk_level="high")
    db_session.commit()

    assert stable(_answer(app, org.id, el.id)) == before


# (11) nothing invented, aggregated or defaulted
def test_no_aggregate_key_no_invented_open_flag_and_no_value_beside_a_reason(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("rr-11")
    el = _element(db_session, org.id, "AppA")
    _risk(db_session, org.id, el)
    db_session.commit()

    result = _answer(app, org.id, el.id)
    text = json.dumps(result)

    for key in ('"max"', '"average"', '"worst"', '"standards"', '"sunset"'):
        assert key not in text
    assert result["recorded_ratings"]["reason"] in REASON_CODES
    assert all(result["recorded_ratings"][k] is None for k in ("technical_risk", "business_risk"))
    assert result["work_package_risk"] is None and result["work_package_risk_reason"] in REASON_CODES
    assert result["review_items"] is None and result["review_items_reason"] in REASON_CODES


# (12) route
def test_route_carries_the_five_keys(app, db_session, make_org, client, login_as):
    org, app_a, wp, session, submitter, open_item, closed_item = _world(db_session, make_org, "rr-12")
    user = _user(db_session, org)
    db_session.commit()

    login_as(client, user)
    data = client.get(f"/api/v1/intelligence/risk/{app_a.id}").get_json()["data"]

    for key in ("recorded_ratings", "work_package_risk", "work_package_risk_reason", "review_items",
                "review_items_reason"):
        assert key in data
    assert data["recorded_ratings"]["technical_risk"] == "high"
    assert len(data["review_items"]) == 2
