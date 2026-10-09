"""Roadmap builder dependency graph and timeline read the work package's real fields.

The dependency-graph and timeline routes crashed with an AttributeError as soon as
an organisation held any work package, because the service read attribute names the
unified work package does not have (``progress_percentage``, ``end_date``,
``assigned_to``). These tests pin the fix against the model's own roadmap
serialiser (``work_package_service.to_roadmap_dict``) and confirm the routes stay scoped to
the caller's organisation.

Written against the shared fixtures in tests/conftest.py (db_session rolls
everything back), per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest


def _make_user(db_session, org):
    from app.models.user import Role, User

    # Shared test-only password, not a per-file literal: the same constant
    # every smoke journey test authenticates with.
    from tests.smoke.conftest import PASSWORD

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"rb-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Roadmap",
        last_name="Builder",
        organization_id=org.id,
        # Pass the Role instance, not role_id: User.__init__ overwrites an
        # unresolved role_id with the default role at construction time.
        role=role,
        confirmed=True,
    )
    user.password = PASSWORD
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def two_org_work_packages(db_session, make_org):
    """Organisation A holds two dependent work packages; organisation B holds one."""
    from app.services import work_package_service as svc

    org_a = make_org("rb-a")
    org_b = make_org("rb-b")
    user_a = _make_user(db_session, org_a)

    wp_a1 = svc.create_work_package(
        organization_id=org_a.id,
        name=f"A-first-{uuid.uuid4().hex[:8]}",
        progress_percentage=40,
        start_date="2026-01-05",
        end_date="2026-03-31",
        owner_id=user_a.id,
    )
    wp_a2 = svc.create_work_package(
        organization_id=org_a.id,
        name=f"A-second-{uuid.uuid4().hex[:8]}",
    )
    svc.add_dependency(wp_a2.id, wp_a1.id, organization_id=org_a.id)
    wp_b1 = svc.create_work_package(
        organization_id=org_b.id,
        name=f"B-wp-{uuid.uuid4().hex[:8]}",
        progress_percentage=90,
    )
    db_session.flush()

    return {
        "org_a": org_a,
        "org_b": org_b,
        "user_a": user_a,
        "wp_a1": wp_a1,
        "wp_a2": wp_a2,
        "wp_b1": wp_b1,
    }


def test_dependency_graph_reads_the_work_package_and_scopes_to_the_caller(
    client, login_as, two_org_work_packages
):
    seed = two_org_work_packages
    login_as(client, seed["user_a"])

    resp = client.get("/api/roadmap-builder/dependency-graph?include_plateaus=false")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    node_ids = {n["id"] for n in data["nodes"]}
    assert node_ids == {f"wp-{seed['wp_a1'].id}", f"wp-{seed['wp_a2'].id}"}

    first_node = next(
        n for n in data["nodes"] if n["id"] == f"wp-{seed['wp_a1'].id}"
    )["data"]
    from app.services import work_package_service as svc

    expected_owner_name = svc.to_roadmap_dict(seed["wp_a1"], seed["org_a"].id)["owner_name"]
    assert first_node["progress"] == 40
    assert first_node["startDate"] == "2026-01-05"
    assert first_node["endDate"] == "2026-03-31"
    assert first_node["assignedTo"] == expected_owner_name

    assert len(data["edges"]) == 1
    edge = data["edges"][0]
    assert edge["source"] == f"wp-{seed['wp_a1'].id}"
    assert edge["target"] == f"wp-{seed['wp_a2'].id}"

    assert "B-wp-" not in resp.get_data(as_text=True)


def test_timeline_reads_the_work_package_and_scopes_to_the_caller(
    client, login_as, two_org_work_packages
):
    seed = two_org_work_packages
    login_as(client, seed["user_a"])

    resp = client.get("/api/roadmap-builder/timeline")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    all_items = [item for group in data["groups"] for item in group["items"]]
    item_ids = {item["id"] for item in all_items}
    assert item_ids == {seed["wp_a1"].id, seed["wp_a2"].id}

    first_item = next(item for item in all_items if item["id"] == seed["wp_a1"].id)
    assert first_item["end"] == "2026-03-31"
    assert first_item["progress"] == 40

    assert data["total_work_packages"] == 2
    assert "B-wp-" not in resp.get_data(as_text=True)


def test_timeline_date_filters_use_the_real_column(client, login_as, two_org_work_packages):
    seed = two_org_work_packages
    login_as(client, seed["user_a"])

    resp = client.get(
        "/api/roadmap-builder/timeline?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert resp.status_code == 200
    ids_in_range = {
        item["id"] for group in resp.get_json()["data"]["groups"] for item in group["items"]
    }
    assert seed["wp_a1"].id in ids_in_range

    resp2 = client.get("/api/roadmap-builder/timeline?start_date=2027-01-01")
    assert resp2.status_code == 200
    ids_out_of_range = {
        item["id"] for group in resp2.get_json()["data"]["groups"] for item in group["items"]
    }
    assert seed["wp_a1"].id not in ids_out_of_range


def test_timeline_plateau_marker_uses_the_real_column(
    client, login_as, db_session, two_org_work_packages
):
    from app.models.implementation_planning import ImplementationPlateau

    seed = two_org_work_packages
    plateau = ImplementationPlateau(
        name=f"Plateau-{uuid.uuid4().hex[:8]}",
        plateau_type="target",
        start_date=datetime(2026, 2, 1),
    )
    db_session.add(plateau)
    db_session.flush()

    login_as(client, seed["user_a"])
    resp = client.get("/api/roadmap-builder/timeline")
    assert resp.status_code == 200
    markers = resp.get_json()["data"]["plateau_markers"]
    marker = next(m for m in markers if m["id"] == plateau.id)
    assert marker["start"] == plateau.start_date.isoformat()


def test_timeline_group_by_assigned_to_and_invalid_value(
    client, login_as, two_org_work_packages
):
    seed = two_org_work_packages
    login_as(client, seed["user_a"])

    resp = client.get("/api/roadmap-builder/timeline?group_by=assigned_to")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    groups_by_name = {g["name"]: g["items"] for g in data["groups"]}
    from app.services import work_package_service as svc

    expected_owner_name = svc.to_roadmap_dict(seed["wp_a1"], seed["org_a"].id)["owner_name"]
    assert any(
        item["id"] == seed["wp_a1"].id for item in groups_by_name[expected_owner_name]
    )
    assert any(item["id"] == seed["wp_a2"].id for item in groups_by_name["unassigned"])

    resp2 = client.get("/api/roadmap-builder/timeline?group_by=to_dict")
    assert resp2.status_code == 200
    assert resp2.get_json()["data"]["group_by"] == "status"


def test_dependency_graph_gap_node_reads_the_gap_and_scopes_to_the_caller(
    client, login_as, db_session, make_org
):
    from app.models.implementation_migration import Gap

    org_a = make_org("rb-gap-a")
    org_b = make_org("rb-gap-b")
    user_a = _make_user(db_session, org_a)

    gap_a = Gap(
        name=f"A-gap-{uuid.uuid4().hex[:8]}",
        organization_id=org_a.id,
        resolution_status="identified",
        impact="high",
        severity="critical",
    )
    gap_b = Gap(
        name=f"B-gap-{uuid.uuid4().hex[:8]}",
        organization_id=org_b.id,
        resolution_status="identified",
        impact="high",
        severity="critical",
    )
    db_session.add_all([gap_a, gap_b])
    db_session.flush()

    login_as(client, user_a)
    resp = client.get(
        "/api/roadmap-builder/dependency-graph?include_plateaus=false&include_gaps=true"
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    gap_nodes = [n for n in data["nodes"] if n["type"] == "gap"]
    gap_ids = {n["id"] for n in gap_nodes}
    assert gap_ids == {f"gap-{gap_a.id}"}

    gap_node_data = gap_nodes[0]["data"]
    assert gap_node_data["impactLevel"] == "high"
    assert gap_node_data["urgency"] == "critical"

    assert "B-gap-" not in resp.get_data(as_text=True)
