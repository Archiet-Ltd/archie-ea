"""Exception requests: a bad body gets a plain refusal, and one organisation
cannot create, read or change another organisation's exception requests."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.architecture_review_board import (
    ARBException,
    ARBGovernanceStandard,
    ARBReviewItem,
)
from app.models.user import User

pytestmark = pytest.mark.usefixtures("db_session")

URL = "/arb/api/exceptions"


def _user(db_session, org, role, label):
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id,
        enterprise_role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _item(db_session, org, submitter):
    item = ARBReviewItem(
        review_number=f"REV-{uuid.uuid4().hex[:10]}",
        title="Board review",
        review_type="architecture",
        submitter_id=submitter.id,
        organization_id=org.id,
    )
    db_session.add(item)
    db_session.flush()
    return item


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("exc-a"), make_org("exc-b")
    standard = ARBGovernanceStandard(
        code=f"STD-{uuid.uuid4().hex[:10]}", name="Use approved databases"
    )
    db_session.add(standard)
    db_session.flush()
    arb_a = _user(db_session, org_a, "arb_member", "arb-a")
    arb_b = _user(db_session, org_b, "arb_member", "arb-b")
    return {
        "standard": standard,
        "arb_a": arb_a,
        "arb_b": arb_b,
        "item_a": _item(db_session, org_a, arb_a),
        "item_b": _item(db_session, org_b, arb_b),
        "sa_a": _user(db_session, org_a, "solution_architect", "sa-a"),
    }


def _count(db_session, org_id):
    table = ARBException.__table__
    return db_session.execute(
        sa.select(sa.func.count()).select_from(table).where(table.c.organization_id == org_id)
    ).scalar_one()


def _row(db_session, exception_id):
    """The stored row by core select, so the reader's own tenant fence cannot hide it."""
    table = ARBException.__table__
    db_session.expire_all()
    return db_session.execute(
        sa.select(table).where(table.c.id == exception_id)
    ).mappings().one()


def _post(client, login_as, user, body, raw=False):
    login_as(client, user)
    if raw:
        return client.post(URL, data=body, content_type="text/plain")
    return client.post(URL, json=body)


def _valid(world, **extra):
    return {"standard_id": world["standard"].id, "exception_reason": "Legacy system", **extra}


def _assert_plain(resp, status, *needles):
    assert resp.status_code == status, resp.get_data(as_text=True)
    text = resp.get_data(as_text=True)
    for needle in needles:
        assert needle in text
    for leak in ("Traceback", "TypeError", "keyword argument", "positional argument", "Error:"):
        assert leak not in text


def test_unknown_field_is_refused_by_name_and_nothing_is_stored(
    client, login_as, db_session, world
):
    before = _count(db_session, world["arb_a"].organization_id)
    resp = _post(client, login_as, world["arb_a"], _valid(world, title="Waiver"))
    _assert_plain(resp, 400, "title")
    assert _count(db_session, world["arb_a"].organization_id) == before


def test_actor_cannot_be_named_in_the_body(client, login_as, db_session, world):
    resp = _post(
        client, login_as, world["arb_a"], _valid(world, requested_by_id=world["arb_b"].id)
    )
    _assert_plain(resp, 400, "requested_by_id")


def test_missing_required_fields_are_listed(client, login_as, db_session, world):
    resp = _post(client, login_as, world["arb_a"], {"scope": "one team"})
    _assert_plain(resp, 400, "standard_id", "exception_reason")
    assert _count(db_session, world["arb_a"].organization_id) == 0


@pytest.mark.parametrize(
    "extra, name",
    [
        ({"standard_id": "one"}, "standard_id"),
        ({"exception_reason": 7}, "exception_reason"),
        ({"requested_duration_days": True}, "requested_duration_days"),
        ({"exception_type": "forever"}, "exception_type"),
    ],
)
def test_wrong_types_are_refused(client, login_as, db_session, world, extra, name):
    resp = _post(client, login_as, world["arb_a"], {**_valid(world), **extra})
    _assert_plain(resp, 400, name)
    assert _count(db_session, world["arb_a"].organization_id) == 0


def test_non_json_body_is_refused(client, login_as, db_session, world):
    resp = _post(client, login_as, world["arb_a"], "standard_id=1", raw=True)
    _assert_plain(resp, 400, "JSON")
    resp = _post(client, login_as, world["arb_a"], ["not", "an", "object"])
    _assert_plain(resp, 400, "JSON")
    assert _count(db_session, world["arb_a"].organization_id) == 0


def test_create_records_the_signed_in_person_and_their_organisation(
    client, login_as, db_session, world
):
    resp = _post(client, login_as, world["arb_a"], _valid(world, review_item_id=world["item_a"].id))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    row = _row(db_session, resp.get_json()["exception_id"])
    assert row["organization_id"] == world["arb_a"].organization_id
    assert row["requested_by_id"] == world["arb_a"].id
    assert row["review_item_id"] == world["item_a"].id


def test_other_organisation_cannot_attach_to_a_review_item(
    client, login_as, db_session, world
):
    before = _count(db_session, world["arb_b"].organization_id)
    resp = _post(client, login_as, world["arb_b"], _valid(world, review_item_id=world["item_a"].id))
    _assert_plain(resp, 404, "not found")
    assert _count(db_session, world["arb_b"].organization_id) == before
    assert _count(db_session, world["arb_a"].organization_id) == 0


def test_other_organisation_cannot_list_read_or_change_a_waiver(
    client, login_as, db_session, world
):
    created = _post(client, login_as, world["arb_a"], _valid(world))
    exception_id = created.get_json()["exception_id"]
    db_session.expire_all()

    login_as(client, world["arb_b"])
    listed = client.get(URL).get_json()["exceptions"]["exceptions"]
    assert exception_id not in [row["id"] for row in listed]

    login_as(client, world["arb_b"])
    assert client.get(f"{URL}/{exception_id}").status_code in (404, 405)

    db_session.expire_all()
    login_as(client, world["arb_b"])
    resp = client.put(f"{URL}/{exception_id}/approve", json={"approval_notes": "mine now"})
    _assert_plain(resp, 404, "not found")

    row = _row(db_session, exception_id)
    assert row["status"] == "requested"
    assert row["approved_by_id"] is None
    assert row["approval_notes"] is None
    assert row["organization_id"] == world["arb_a"].organization_id

    login_as(client, world["arb_a"])
    own = client.get(URL).get_json()["exceptions"]["exceptions"]
    assert exception_id in [r["id"] for r in own]


def test_approve_by_board_member_records_them_and_refuses_bad_bodies(
    client, login_as, db_session, world
):
    exception_id = _post(client, login_as, world["arb_a"], _valid(world)).get_json()["exception_id"]
    path = f"{URL}/{exception_id}/approve"

    for body, needle in (
        ({"approved_by_id": world["arb_b"].id}, "approved_by_id"),
        ({"approval_notes": 5}, "approval_notes"),
        ({"new_expires_at": "next week"}, "new_expires_at"),
    ):
        login_as(client, world["arb_a"])
        _assert_plain(client.put(path, json=body), 400, needle)
    login_as(client, world["arb_a"])
    _assert_plain(client.put(path, data="x", content_type="text/plain"), 400, "JSON")
    assert _row(db_session, exception_id)["status"] == "requested"

    login_as(client, world["arb_a"])
    ok = client.put(path, json={"approval_notes": "Accepted until the migration"})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    row = _row(db_session, exception_id)
    assert row["status"] == "approved"
    assert row["approved_by_id"] == world["arb_a"].id

    login_as(client, world["arb_a"])
    again = client.put(path, json={})
    _assert_plain(again, 409, "Cannot approve")


def test_a_person_without_a_board_role_cannot_approve(client, login_as, db_session, world):
    exception_id = _post(client, login_as, world["sa_a"], _valid(world)).get_json()["exception_id"]
    login_as(client, world["sa_a"])
    resp = client.put(f"{URL}/{exception_id}/approve", json={})
    _assert_plain(resp, 403, "role")
    assert _row(db_session, exception_id)["status"] == "requested"
