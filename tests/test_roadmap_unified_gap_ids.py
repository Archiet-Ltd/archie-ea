"""A gap derived from a unified capability becomes planned work like any other gap."""

from __future__ import annotations

import uuid

import pytest

from tests.test_gap_work_package_link import world  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")

BASE = "/capability-map/api/roadmap"


def _capability(db_session, org, name, **extra):
    from app.models.unified_capability import UnifiedCapability

    row = UnifiedCapability(
        name=name, code="CU%s" % uuid.uuid4().hex[:8], level=1,
        organization_id=org.id, status="defined", **extra,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _stored_gaps(capability_id):
    from app.models.implementation_migration import Gap

    return Gap.query.filter_by(source_capability_type="unified", source_capability_id=capability_id).all()


def _work_packages(org, name):
    from app.services import work_package_service

    return [w for w in work_package_service.query_for(org.id).all() if w.name == name]


def test_unified_gap_id_becomes_a_work_package_on_a_real_gap(db_session, world, client, login_as):  # noqa: F811
    cap = _capability(db_session, world["a"], "Unified payments")
    db_session.commit()
    name = "Fix %s" % cap.code
    login_as(client, world["editor_a"])
    resp = client.post(f"{BASE}/work-packages", json={"name": name, "gap_id": "unified-%d" % cap.id})
    assert resp.status_code in (200, 201), resp.get_json()
    gaps = _stored_gaps(cap.id)
    assert len(gaps) == 1 and gaps[0].organization_id == world["a"].id
    assert len(_work_packages(world["a"], name)) == 1


def test_unified_gap_id_from_another_organisation_is_not_found_and_creates_nothing(
    db_session, world, client, login_as  # noqa: F811
):
    cap = _capability(db_session, world["a"], "Org A only")
    db_session.commit()
    name = "Steal %s" % cap.code
    login_as(client, world["editor_b"])
    resp = client.post(f"{BASE}/work-packages", json={"name": name, "gap_id": "unified-%d" % cap.id})
    assert resp.status_code == 404, resp.get_json()
    resp = client.post(
        f"{BASE}/gaps/add-from-capability",
        json={"capability_id": cap.id, "capability_type": "unified", "capability_name": "x"},
    )
    assert resp.status_code == 404, resp.get_json()
    assert _stored_gaps(cap.id) == []
    assert _work_packages(world["b"], name) == [] and _work_packages(world["a"], name) == []


def test_add_from_capability_takes_a_unified_capability(db_session, world, client, login_as):  # noqa: F811
    cap = _capability(db_session, world["a"], "Unified onboarding")
    db_session.commit()
    login_as(client, world["editor_a"])
    resp = client.post(
        f"{BASE}/gaps/add-from-capability",
        json={"capability_id": cap.id, "capability_type": "unified", "capability_name": cap.name},
    )
    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True, body
    assert [g.id for g in _stored_gaps(cap.id)] == [body["gap_id"]]


def test_a_business_gap_id_still_resolves_as_before(db_session, world, client, login_as):  # noqa: F811
    login_as(client, world["editor_a"])
    resp = client.post(f"{BASE}/work-packages", json={"name": "No such", "gap_id": "business-987654"})
    assert resp.status_code == 404, resp.get_json()
    resp = client.post(f"{BASE}/work-packages", json={"name": "Bad", "gap_id": "nonsense-1"})
    assert resp.status_code == 400, resp.get_json()


def test_hierarchy_path_names_the_real_type_of_a_parent_outside_the_live_set(
    db_session, world, client, login_as  # noqa: F811
):
    live_parent = _capability(db_session, world["a"], "Live parent")
    retired = _capability(db_session, world["a"], "Retired parent", retired_into_id=live_parent.id)
    child = _capability(db_session, world["a"], "Child", parent_capability_id=retired.id)
    db_session.commit()
    login_as(client, world["editor_a"])
    resp = client.get(f"{BASE}/gaps?capability_type=business")
    rows = (resp.get_json() or {}).get("gaps", [])
    mine = [g for g in rows if g["capability_id"] == child.id and g["capability_type"] == "unified"]
    assert mine, [g["id"] for g in rows]
    assert mine[0]["hierarchy_path"] == "unified-%d,unified-%d" % (retired.id, child.id)


def test_a_recorded_gap_is_never_also_listed_as_not_recorded(db_session, world, client, login_as):  # noqa: F811
    cap = _capability(db_session, world["a"], "Recorded once")
    other = _capability(db_session, world["a"], "Still a signal")
    db_session.commit()
    login_as(client, world["editor_a"])

    def listed():
        body = client.get(f"{BASE}/gaps?capability_type=business").get_json()
        text = str(body)
        return "Recorded once" in text, "Still a signal" in text

    assert listed() == (True, True)
    resp = client.post(
        f"{BASE}/gaps/add-from-capability",
        json={"capability_id": cap.id, "capability_type": "unified", "capability_name": cap.name},
    )
    assert resp.status_code == 200, resp.get_json()
    assert listed() == (False, True)
    assert other.id
