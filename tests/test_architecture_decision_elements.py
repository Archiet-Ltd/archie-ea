"""An architecture decision is recorded against the elements it affects and found
from them -- inside one organisation only.

Two organisations throughout: a decision can only point at its own
organisation's elements, and an element only lists its own organisation's
decisions.
"""

from __future__ import annotations

import json
import uuid

import pytest


def _user(db_session, org, first="Ada", last="Lovelace"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"adr-{uuid.uuid4().hex[:10]}@example.com",
        first_name=first,
        last_name=last,
        organization_id=org.id,
        role=role,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, label):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"{label} {uuid.uuid4().hex[:6]}",
        type="ApplicationComponent",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _decision(db_session, org, title, element_ids):
    from app.models.architecture_decision import ArchitectureDecision

    row = ArchitectureDecision(
        decision_id=f"XD-{uuid.uuid4().hex[:8]}",
        title=title,
        status="accepted",
        organization_id=org.id,
        archimate_element_ids=list(element_ids),
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(db_session, make_org):
    org_a = make_org("adr-a")
    org_b = make_org("adr-b")
    w = {
        "org_a": org_a,
        "org_b": org_b,
        "ada": _user(db_session, org_a),
        "bob": _user(db_session, org_b, "Bob", "Foreign"),
        "platform": _element(db_session, org_a, "Integration platform"),
        "portal": _element(db_session, org_a, "Customer portal"),
        "secret": _element(db_session, org_b, "Other organisation system"),
    }
    db_session.commit()
    return w


def test_a_decision_records_only_the_callers_own_elements(world, db_session, client, login_as):
    from app.models.architecture_decision import ArchitectureDecision

    title = f"Standardise on one integration platform {uuid.uuid4().hex[:6]}"
    picked = [world["platform"].id, world["secret"].id, "not-a-number", world["portal"].id]
    login_as(client, world["ada"])
    resp = client.post(
        "/architecture/decisions/new",
        data={
            "title": title,
            "status": "proposed",
            "context": "Three integration tools overlap.",
            "decision": "We will use one integration platform.",
            "consequences": "Two tools retire.",
            "archimate_element_ids": json.dumps(picked),
        },
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)

    stored = db_session.execute(
        db_session.query(ArchitectureDecision).filter_by(title=title).statement
    ).scalars().one()
    assert stored.organization_id == world["org_a"].id
    assert stored.archimate_element_ids == [world["platform"].id, world["portal"].id]


def test_a_malformed_element_list_records_the_decision_with_no_elements(
    world, db_session, client, login_as
):
    from app.models.architecture_decision import ArchitectureDecision

    title = f"Malformed pick {uuid.uuid4().hex[:6]}"
    login_as(client, world["ada"])
    resp = client.post(
        "/architecture/decisions/new",
        data={"title": title, "decision": "x", "archimate_element_ids": "{not json"},
    )
    assert resp.status_code == 302
    stored = db_session.execute(
        db_session.query(ArchitectureDecision).filter_by(title=title).statement
    ).scalars().one()
    assert stored.archimate_element_ids == []


def test_editing_a_decision_cannot_link_another_organisations_element(
    world, db_session, client, login_as
):
    decision = _decision(db_session, world["org_a"], "Edit me", [world["platform"].id])
    db_session.commit()
    login_as(client, world["ada"])
    resp = client.post(
        f"/architecture/decisions/{decision.id}/edit",
        data={
            "title": "Edit me",
            "decision": "x",
            "archimate_element_ids": json.dumps([world["secret"].id, world["portal"].id]),
        },
    )
    assert resp.status_code == 302
    db_session.refresh(decision)
    assert decision.archimate_element_ids == [world["portal"].id]


def test_decisions_are_found_from_the_element_they_affect_in_one_organisation_only(
    world, db_session, client, login_as
):
    ours = _decision(db_session, world["org_a"], "Ours about the platform", [world["platform"].id])
    _decision(db_session, world["org_a"], "Ours about the portal only", [world["portal"].id])
    # Organisation B's decision names A's element id: it must not surface for A.
    _decision(db_session, world["org_b"], "Theirs naming our id", [world["platform"].id, world["secret"].id])
    db_session.commit()

    login_as(client, world["ada"])
    page = client.get(f"/architecture/decisions/?element_id={world['platform'].id}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Ours about the platform" in html
    assert world["platform"].name in html
    assert "Ours about the portal only" not in html
    assert "Theirs naming our id" not in html
    assert f'/architecture/decisions/{ours.id}"' in html

    # The element page lists the same decision and offers to record another.
    login_as(client, world["ada"])
    element_page = client.get(f"/archimate/elements/{world['platform'].id}/impact")
    assert element_page.status_code == 200
    element_html = element_page.get_data(as_text=True)
    assert "Ours about the platform" in element_html
    assert "Theirs naming our id" not in element_html
    assert f"/architecture/decisions/new?element_id={world['platform'].id}" in element_html


def test_another_organisations_element_filter_shows_nothing_of_theirs(
    world, db_session, client, login_as
):
    _decision(db_session, world["org_b"], "Their secret decision", [world["secret"].id])
    db_session.commit()

    login_as(client, world["ada"])
    html = client.get(f"/architecture/decisions/?element_id={world['secret'].id}").get_data(as_text=True)
    assert "Their secret decision" not in html
    assert world["secret"].name not in html
    assert "That element is not recorded in your organisation." in html


def test_recording_a_decision_from_an_element_starts_with_that_element_chosen(
    world, db_session, client, login_as
):
    login_as(client, world["ada"])
    html = client.get(f"/architecture/decisions/new?element_id={world['platform'].id}").get_data(as_text=True)
    assert json.dumps(world["platform"].name) in html

    login_as(client, world["ada"])
    foreign = client.get(f"/architecture/decisions/new?element_id={world['secret'].id}").get_data(as_text=True)
    assert world["secret"].name not in foreign


def test_the_decision_page_links_each_element_to_its_own_page(world, db_session, client, login_as):
    decision = _decision(
        db_session, world["org_a"], "Linked", [world["platform"].id, world["secret"].id]
    )
    db_session.commit()
    login_as(client, world["ada"])
    html = client.get(f"/architecture/decisions/{decision.id}").get_data(as_text=True)
    assert f"/archimate/elements/{world['platform'].id}/impact" in html
    assert world["secret"].name not in html


def test_affecting_elements_answers_by_organisation_with_or_without_a_request(world, db_session):
    from app.models.architecture_decision import ArchitectureDecision

    ours = _decision(db_session, world["org_a"], "Ours", [world["platform"].id])
    legacy = _decision(db_session, world["org_a"], "Stored as text", [str(world["portal"].id)])
    _decision(db_session, world["org_b"], "Theirs", [world["platform"].id])
    db_session.commit()

    found = ArchitectureDecision.affecting_elements(
        [world["platform"].id, world["portal"].id], world["org_a"].id
    )
    assert {d.id for d in found} == {ours.id, legacy.id}
    assert ArchitectureDecision.affecting_elements([], world["org_a"].id) == []
    assert ArchitectureDecision.affecting_elements([world["platform"].id], None) == []


def test_a_decision_recorded_in_a_solution_design_is_found_from_its_element(
    world, db_session, client, login_as
):
    """The solution-design decision API records the elements in
    ``related_element_ids``; the element page, the element-filtered list and
    the one accessor all find it from there, still inside one organisation."""
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.solution_models import Solution

    solution = Solution(
        name=f"Integration consolidation {uuid.uuid4().hex[:6]}",
        organization_id=world["org_a"].id,
        created_by_id=world["ada"].id,
    )
    db_session.add(solution)
    db_session.commit()

    title = f"Retire the second message bus {uuid.uuid4().hex[:6]}"
    login_as(client, world["ada"])
    resp = client.post(
        f"/solutions/{solution.id}/decisions",
        json={"title": title, "related_element_ids": [world["platform"].id]},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    decision_id = resp.get_json()["data"]["id"]

    found = ArchitectureDecision.affecting_elements([world["platform"].id], world["org_a"].id)
    assert [d.id for d in found].count(decision_id) == 1
    assert decision_id not in {
        d.id for d in ArchitectureDecision.affecting_elements([world["platform"].id], world["org_b"].id)
    }

    login_as(client, world["ada"])
    element_html = client.get(f"/archimate/elements/{world['platform'].id}/impact").get_data(as_text=True)
    assert title in element_html

    login_as(client, world["ada"])
    list_html = client.get(f"/architecture/decisions/?element_id={world['platform'].id}").get_data(as_text=True)
    assert title in list_html

    # Another organisation never sees it from its own element pages.
    login_as(client, world["bob"])
    foreign = client.get(f"/architecture/decisions/?element_id={world['platform'].id}").get_data(as_text=True)
    assert title not in foreign


def test_a_decision_naming_an_element_in_both_columns_is_listed_once(world, db_session):
    from app.models.architecture_decision import ArchitectureDecision

    both = _decision(db_session, world["org_a"], "In both columns", [world["platform"].id])
    both.related_element_ids = [world["platform"].id]
    db_session.commit()

    found = ArchitectureDecision.affecting_elements([world["platform"].id], world["org_a"].id)
    assert [d.id for d in found].count(both.id) == 1
