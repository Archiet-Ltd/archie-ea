"""R1-B04 PR 2 fix round 2: roadmap builder views, the ArchiMate mirror, the flag seed.

1. A work package created on the roadmap builder appears in that screen's graph,
   critical path, timeline and summary, and no other organisation's rows do.
2. Every work package created through the one writer has its ArchiMate mirror;
   a merged or bridged row keeps the element it already has.
3. The implementation planning feature flag is seeded by the shared fixture.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _post(client, path, data):
    return client.post(path, data=json.dumps(data), content_type="application/json")


def _user(db_session, org, label):
    from tests.test_work_package_writer import _user as writer_user

    return writer_user(db_session, org, label)


def test_roadmap_builder_created_work_package_in_graph_and_timeline(
    client, login_as, db_session, make_org
):
    from app.services import work_package_service as svc

    org_a, org_b = make_org("r2-a"), make_org("r2-b")
    user_a = _user(db_session, org_a, "r2a")
    theirs = svc.create_work_package(
        organization_id=org_b.id, name="B-secret-%s" % uuid.uuid4().hex[:6],
        start_date="2026-02-01", end_date="2026-02-20",
    )
    db_session.flush()

    login_as(client, user_a)
    first = _post(client, "/api/roadmap-builder/work-packages", {
        "name": "R2 first", "start_date": "2026-02-01", "end_date": "2026-02-28"})
    assert first.status_code == 201, first.get_data(as_text=True)
    first_id = first.get_json()["data"]["work_package"]["id"]
    second = _post(client, "/api/roadmap-builder/work-packages", {
        "name": "R2 second", "start_date": "2026-03-01", "end_date": "2026-03-31",
        "dependencies": [first_id]})
    assert second.status_code == 201, second.get_data(as_text=True)
    second_id = second.get_json()["data"]["work_package"]["id"]
    assert svc.get_work_package(first_id, org_a.id) is not None  # unified ids

    # Dependency graph: both nodes and the edge.
    graph = client.get("/api/roadmap-builder/dependency-graph?include_plateaus=false")
    assert graph.status_code == 200, graph.get_data(as_text=True)
    data = graph.get_json()["data"]
    assert {n["id"] for n in data["nodes"]} == {"wp-%s" % first_id, "wp-%s" % second_id}
    assert [(e["source"], e["target"]) for e in data["edges"]] == [
        ("wp-%s" % first_id, "wp-%s" % second_id)]

    # Timeline, inside the date range.
    timeline = client.get("/api/roadmap-builder/timeline?start_date=2026-01-01&end_date=2026-12-31")
    assert timeline.status_code == 200, timeline.get_data(as_text=True)
    items = [i for g in timeline.get_json()["data"]["groups"] for i in g["items"]]
    assert {i["id"] for i in items} == {first_id, second_id}
    outside = client.get("/api/roadmap-builder/timeline?start_date=2027-01-01")
    assert outside.get_json()["data"]["total_work_packages"] == 0

    # Critical path.
    critical = client.get("/api/roadmap-builder/critical-path")
    assert critical.status_code == 200, critical.get_data(as_text=True)
    cp = critical.get_json()["data"]
    assert {a["id"] for a in cp["slack_analysis"]} == {first_id, second_id}
    assert cp["total_work_packages"] == 2

    # Summary.
    summary = client.get("/api/roadmap-builder/summary")
    assert summary.status_code == 200, summary.get_data(as_text=True)
    assert summary.get_json()["data"]["work_packages"]["total"] == 2

    # Organisation B's row appears in none of them.
    for resp in (graph, timeline, critical, summary):
        assert "B-secret-" not in resp.get_data(as_text=True)
        assert "wp-%s" % theirs.id not in resp.get_data(as_text=True)


def _element(element_id):
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement.query.get(element_id)


def test_writer_creates_archimate_mirror(client, login_as, db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage
    from app.services import work_package_service as svc
    from app.services.archimate_backbone import create_backbone_element

    org = make_org("r2-mirror")
    user = _user(db_session, org, "r2m")

    # Through the enterprise screen.
    login_as(client, user)
    resp = _post(client, "/enterprise/api/work-packages", {"name": "Enterprise made"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = svc.get_work_package(resp.get_json()["id"], org.id)
    assert created.archimate_element_id
    element = _element(created.archimate_element_id)
    assert (element.type, element.layer, element.organization_id) == (
        "WorkPackage", "Implementation", org.id)

    # Directly through the writer.
    direct = svc.create_work_package(organization_id=org.id, name="Direct made")
    assert direct.archimate_element_id
    element = _element(direct.archimate_element_id)
    assert (element.type, element.layer, element.organization_id) == (
        "WorkPackage", "Implementation", org.id)

    # A merged (bridged) row keeps the element it already has; no second one.
    existing = create_backbone_element(
        element_type="WorkPackage", layer="Implementation", name="Old element",
        organization_id=org.id)
    legacy = WorkPackage(name="Old row", organization_id=org.id,
                         archimate_element_id=existing.id)
    db_session.add(legacy)
    db_session.flush()
    copy = svc.get_by_source("work_packages", legacy.id, org.id)
    assert copy is not None
    assert copy.archimate_element_id == existing.id
    assert ArchiMateElement.query.filter_by(
        organization_id=org.id, type="WorkPackage", name="Old row").count() == 0
    assert ArchiMateElement.query.filter_by(
        organization_id=org.id, type="WorkPackage", name="Old element").count() == 1


def test_mirror_failure_is_a_writer_error(db_session, make_org, monkeypatch):
    from app.services import archimate_backbone, work_package_service as svc

    org = make_org("r2-fail")

    def refuse(obj, **kwargs):
        raise ValueError("no organisation")

    monkeypatch.setattr(archimate_backbone, "sync_archimate_element", refuse)
    with pytest.raises(svc.WorkPackageError):
        svc.create_work_package(organization_id=org.id, name="Will not mirror")


def test_feature_flag_seeded_by_fixture(client, login_as, db_session, make_org):
    org = make_org("r2-flag")
    user = _user(db_session, org, "r2f")
    login_as(client, user)
    resp = client.get("/implementation/api/deliverables")
    assert resp.status_code == 200, resp.get_data(as_text=True)
