"""A kanban card links to ArchiMate elements through one stored relationship (the
junction table); the card's JSON id list is a mirror written by the one function,
and the phase / deliverable / element views are derived from those links.
Every path is scoped to the caller's organisation.
"""

from __future__ import annotations

import random
import uuid

import pytest


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Adm {label} {suffix}", slug=f"adm-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, first):
    from app.models.user import User

    user = User(
        first_name=first, last_name="T",
        email=f"{first.lower()}-{uuid.uuid4().hex[:6]}@example.test",
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _phase(db_session, order=1):
    from app.models.adm_kanban import ADMPhase

    suffix = uuid.uuid4().hex[:8]
    phase = ADMPhase(name=f"Phase {suffix}", code=suffix[:10], order=order)
    db_session.add(phase)
    db_session.flush()
    return phase


def _board(db_session, org, creator):
    from app.models.adm_kanban import KanbanBoard

    board = KanbanBoard(
        name="Board", board_type="architecture_development",
        created_by_id=creator.id, organization_id=org.id,
    )
    db_session.add(board)
    db_session.flush()
    return board


def _card(db_session, board, phase, creator, deliverable=None):
    from app.models.adm_kanban import KanbanCard

    card = KanbanCard(
        title="Card", adm_phase_id=phase.id, board_id=board.id, card_type="task",
        status="todo", created_by_id=creator.id, organization_id=board.organization_id,
        togaf_deliverable=deliverable,
    )
    db_session.add(card)
    db_session.flush()
    return card


def _element(db_session, org, name):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


@pytest.fixture
def world(db_session):
    from types import SimpleNamespace

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    ua, ub = _user(db_session, org_a, "Ada"), _user(db_session, org_b, "Bob")
    p1, p2 = _phase(db_session, 1), _phase(db_session, 2)
    board_a, board_b = _board(db_session, org_a, ua), _board(db_session, org_b, ub)
    els_a = [_element(db_session, org_a, f"A{i}") for i in range(4)]
    els_b = [_element(db_session, org_b, f"B{i}") for i in range(2)]
    db_session.commit()
    return SimpleNamespace(
        org_a=org_a, org_b=org_b, ua=ua, ub=ub, p1=p1, p2=p2,
        board_a=board_a, board_b=board_b, els_a=els_a, els_b=els_b,
    )


def _junction_ids(db_session, card):
    from app.models.adm_kanban_junctions import CardArchiMateElement

    rows = (
        db_session.query(CardArchiMateElement)
        .filter_by(card_id=card.id).order_by(CardArchiMateElement.id).all()
    )
    return [r.archimate_element_id for r in rows]


# -- the one write function ---------------------------------------------------------


def test_link_three_elements_creates_three_rows_and_matching_mirror(db_session, world):
    from app.services.adm_card_element_links import change_card_elements

    card = _card(db_session, world.board_a, world.p1, world.ua)
    ids = [e.id for e in world.els_a[:3]]
    change_card_elements(card, ids, mode="add", organization_id=world.org_a.id)
    db_session.commit()

    assert _junction_ids(db_session, card) == ids
    assert card.archimate_element_ids == ids

    change_card_elements(card, [ids[1]], mode="remove", organization_id=world.org_a.id)
    db_session.commit()
    assert _junction_ids(db_session, card) == [ids[0], ids[2]]
    assert card.archimate_element_ids == [ids[0], ids[2]]


def test_mirror_and_junction_never_disagree(db_session, world):
    from app.services.adm_card_element_links import change_card_elements, get_card_element_ids

    rng = random.Random(7)
    card = _card(db_session, world.board_a, world.p1, world.ua)
    pool = [e.id for e in world.els_a]
    for _ in range(40):
        mode = rng.choice(["add", "remove", "replace"])
        picked = rng.sample(pool, rng.randint(0, len(pool)))
        change_card_elements(card, picked, mode=mode, organization_id=world.org_a.id)
        db_session.commit()
        stored = _junction_ids(db_session, card)
        assert len(stored) == len(set(stored))
        assert card.archimate_element_ids == stored
        assert get_card_element_ids(card) == stored


def test_replace_with_foreign_element_is_refused_and_changes_nothing(db_session, world):
    from app.services.adm_card_element_links import CardElementLinkError, change_card_elements

    card = _card(db_session, world.board_a, world.p1, world.ua)
    mine = world.els_a[0].id
    change_card_elements(card, [mine], mode="add", organization_id=world.org_a.id)
    db_session.commit()

    for mode in ("add", "replace"):
        with pytest.raises(CardElementLinkError):
            change_card_elements(
                card, [mine, world.els_b[0].id], mode=mode, organization_id=world.org_a.id
            )
    assert _junction_ids(db_session, card) == [mine]
    assert card.archimate_element_ids == [mine]


def test_other_organisation_cannot_change_the_card(db_session, world):
    from app.services.adm_card_element_links import CardElementLinkError, change_card_elements

    card = _card(db_session, world.board_a, world.p1, world.ua)
    with pytest.raises(CardElementLinkError):
        change_card_elements(card, [world.els_b[0].id], mode="add", organization_id=world.org_b.id)
    with pytest.raises(CardElementLinkError):
        change_card_elements(card, [world.els_a[0].id], mode="add", organization_id=world.org_b.id)
    assert _junction_ids(db_session, card) == []


def test_legacy_mirror_ids_are_adopted_valid_ones_only(db_session, world):
    from app.services.adm_card_element_links import change_card_elements

    card = _card(db_session, world.board_a, world.p1, world.ua)
    mine, theirs = world.els_a[0].id, world.els_b[0].id
    card.archimate_element_ids = [mine, theirs, 999999]  # written before the one function existed
    db_session.commit()

    change_card_elements(card, [world.els_a[1].id], mode="add", organization_id=world.org_a.id)
    db_session.commit()
    assert _junction_ids(db_session, card) == [mine, world.els_a[1].id]
    assert card.archimate_element_ids == [mine, world.els_a[1].id]


# -- routes go through the function --------------------------------------------------


def test_create_and_update_card_routes_write_junction_rows(db_session, world, client, login_as):
    from app.models.adm_kanban import KanbanCard

    login_as(client, world.ua)
    ids = [world.els_a[0].id, world.els_a[1].id]
    resp = client.post(
        f"/api/adm-kanban/boards/{world.board_a.id}/cards",
        json={"title": "T", "adm_phase_id": world.p1.id, "archimate_element_ids": ids},
    )
    assert resp.status_code == 201, resp.get_json()
    card = db_session.get(KanbanCard, resp.get_json()["id"])
    assert _junction_ids(db_session, card) == ids and card.archimate_element_ids == ids

    resp = client.put(f"/api/adm-kanban/cards/{card.id}", json={"archimate_element_ids": [ids[1]]})
    assert resp.status_code == 200, resp.get_json()
    db_session.expire_all()
    card = db_session.get(KanbanCard, card.id)
    assert _junction_ids(db_session, card) == [ids[1]] and card.archimate_element_ids == [ids[1]]


def test_create_and_update_card_routes_refuse_foreign_elements(db_session, world, client, login_as):
    from app.models.adm_kanban import KanbanCard

    login_as(client, world.ua)
    before = db_session.query(KanbanCard).filter_by(board_id=world.board_a.id).count()
    resp = client.post(
        f"/api/adm-kanban/boards/{world.board_a.id}/cards",
        json={"title": "T", "adm_phase_id": world.p1.id, "archimate_element_ids": [world.els_b[0].id]},
    )
    assert resp.status_code == 400, resp.get_json()
    assert db_session.query(KanbanCard).filter_by(board_id=world.board_a.id).count() == before

    card = _card(db_session, world.board_a, world.p1, world.ua)
    db_session.commit()
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}", json={"archimate_element_ids": [world.els_b[0].id]}
    )
    assert resp.status_code == 400, resp.get_json()
    db_session.expire_all()
    assert _junction_ids(db_session, db_session.get(KanbanCard, card.id)) == []


def test_other_organisation_cannot_link_through_the_card_route(db_session, world, client, login_as):
    card = _card(db_session, world.board_a, world.p1, world.ua)
    db_session.commit()
    login_as(client, world.ub)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}", json={"archimate_element_ids": [world.els_b[0].id]}
    )
    assert resp.status_code in (403, 404), resp.get_json()
    assert _junction_ids(db_session, card) == []


# -- derived views -------------------------------------------------------------------


def _fixture_links(db_session, world):
    from app.services.adm_card_element_links import change_card_elements

    a0, a1, a2, a3 = [e.id for e in world.els_a]
    c1 = _card(db_session, world.board_a, world.p1, world.ua, deliverable="Architecture Vision")
    c2 = _card(db_session, world.board_a, world.p1, world.ua, deliverable="Architecture Vision")
    c3 = _card(db_session, world.board_a, world.p2, world.ua, deliverable="Architecture Contract")
    change_card_elements(c1, [a0, a1], mode="add", organization_id=world.org_a.id)
    change_card_elements(c2, [a1], mode="add", organization_id=world.org_a.id)
    change_card_elements(c3, [a1, a2], mode="add", organization_id=world.org_a.id)
    # organisation B has a card in the same phase and deliverable with its own element
    cb = _card(db_session, world.board_b, world.p1, world.ub, deliverable="Architecture Vision")
    change_card_elements(cb, [world.els_b[0].id], mode="add", organization_id=world.org_b.id)
    db_session.commit()
    return a0, a1, a2, a3


def _ids(rows):
    return [r["id"] for r in rows]


def test_derived_views_for_two_phases_three_cards_shared_elements(db_session, world):
    from app.services.adm_analytics_service import ADMAnalyticsService as S

    a0, a1, a2, a3 = _fixture_links(db_session, world)
    org = world.org_a.id

    assert _ids(S.elements_for_phase(world.p1, org)) == [a0, a1]
    assert _ids(S.elements_for_phase(world.p2.code, org)) == [a1, a2]
    assert _ids(S.elements_for_deliverable("Architecture Vision", org)) == [a0, a1]
    assert _ids(S.elements_for_deliverable("Architecture Contract", org)) == [a1, a2]

    phases = S.phase_for_element(a1, org)
    assert [p["code"] for p in phases] == [world.p1.code, world.p2.code]
    assert [p["card_count"] for p in phases] == [2, 1]
    assert [p["code"] for p in S.phase_for_element(a0, org)] == [world.p1.code]
    assert S.phase_for_element(a3, org) == []


def test_derived_views_return_nothing_across_organisations(db_session, world):
    from app.services.adm_analytics_service import ADMAnalyticsService as S

    _a0, a1, _a2, _a3 = _fixture_links(db_session, world)
    org_b = world.org_b.id
    assert _ids(S.elements_for_phase(world.p1, org_b)) == [world.els_b[0].id]
    assert _ids(S.elements_for_deliverable("Architecture Vision", org_b)) == [world.els_b[0].id]
    assert S.elements_for_phase(world.p2, org_b) == []
    assert S.phase_for_element(a1, org_b) == []
    assert S.elements_for_phase(world.p1, None) == []


def test_adm_links_endpoint_is_scoped_to_the_callers_organisation(db_session, world, client, login_as):
    _a0, a1, _a2, _a3 = _fixture_links(db_session, world)
    login_as(client, world.ub)
    resp = client.get(f"/api/ea/adm-links?phase={world.p1.code}")
    assert resp.status_code == 200
    assert _ids(resp.get_json()["elements"]) == [world.els_b[0].id]
    resp = client.get(f"/api/ea/adm-links?element_id={a1}")
    assert resp.get_json()["phases"] == []

    login_as(client, world.ua)
    resp = client.get(f"/api/ea/adm-links?element_id={a1}")
    assert [p["code"] for p in resp.get_json()["phases"]] == [world.p1.code, world.p2.code]
    assert client.get("/api/ea/adm-links").status_code == 400


def test_no_other_writer_of_the_card_id_list():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    assign = re.compile(r"card\.archimate_element_ids\s*=[^=]")
    kwarg = re.compile(r"^\s*archimate_element_ids\s*=\s*(?!Column)\S", re.M)
    field_list = re.compile(r'"archimate_element_ids",\s*"application_ids"')
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "adm_card_element_links.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if assign.search(text) or field_list.search(text) or ("KanbanCard(" in text and kwarg.search(text)):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
