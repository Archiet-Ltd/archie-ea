"""R1-B04 PR 2: one work package writer, readers on the one store, and the
"blocked by another programme" view.

Two-organisation tests: organisation A never sees or writes organisation B's
work packages through the writer or through any repointed screen, and a
dependency on another programme's work package appears in the blocked view for
the owning organisation only.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role="portfolio_manager"):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"wp-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _programme(db_session, org, name):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    programme = EnterpriseInitiative(
        name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id
    )
    db_session.add(programme)
    db_session.flush()
    return programme


def _wp(org, name, programme=None, **extra):
    from app.services import work_package_service

    return work_package_service.create_work_package(
        organization_id=org.id,
        name=name,
        enterprise_initiative_id=programme.id if programme else None,
        **extra,
    )


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("wp-a")
    org_b = make_org("wp-b")
    return {
        "a": org_a,
        "b": org_b,
        "user_a": _user(db_session, org_a, "pma"),
        "user_b": _user(db_session, org_b, "pmb"),
        "prog_a1": _programme(db_session, org_a, "Alpha"),
        "prog_a2": _programme(db_session, org_a, "Beta"),
        "prog_b1": _programme(db_session, org_b, "Gamma"),
        "prog_b2": _programme(db_session, org_b, "Delta"),
    }


# -- the writer -------------------------------------------------------------


def test_create_sets_organisation_and_programme(two_orgs):
    wp = _wp(two_orgs["a"], "Migrate billing", two_orgs["prog_a1"])
    assert wp.organization_id == two_orgs["a"].id
    assert wp.enterprise_initiative_id == two_orgs["prog_a1"].id


def test_create_refuses_another_organisations_programme(two_orgs):
    from app.services import work_package_service

    with pytest.raises(work_package_service.WorkPackageNotFound):
        _wp(two_orgs["a"], "Sneaky", two_orgs["prog_b1"])


def test_other_organisation_cannot_read_update_or_delete(two_orgs):
    from app.services import work_package_service as svc

    wp = _wp(two_orgs["a"], "Org A only")
    b = two_orgs["b"].id
    assert svc.get_work_package(wp.id, b) is None
    with pytest.raises(svc.WorkPackageNotFound):
        svc.update_work_package(wp.id, organization_id=b, name="hijack")
    with pytest.raises(svc.WorkPackageNotFound):
        svc.delete_work_package(wp.id, organization_id=b)
    with pytest.raises(svc.WorkPackageNotFound):
        svc.add_dependency(wp.id, wp.id, organization_id=b)
    assert svc.get_work_package(wp.id, two_orgs["a"].id).name == "Org A only"


def test_dependency_on_another_organisations_work_package_refused(two_orgs):
    from app.services import work_package_service as svc

    mine = _wp(two_orgs["a"], "Mine", two_orgs["prog_a1"])
    theirs = _wp(two_orgs["b"], "Theirs", two_orgs["prog_b1"])
    with pytest.raises(svc.WorkPackageNotFound):
        svc.add_dependency(mine.id, theirs.id, organization_id=two_orgs["a"].id)
    assert svc.dependency_ids(mine) == []


def test_self_dependency_refused(two_orgs):
    from app.services import work_package_service as svc

    wp = _wp(two_orgs["a"], "Loop")
    with pytest.raises(svc.WorkPackageError):
        svc.add_dependency(wp.id, wp.id, organization_id=two_orgs["a"].id)


# -- blocked by another programme ------------------------------------------


def test_blocked_view_lists_cross_programme_dependency(two_orgs):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    blocked = _wp(a, "Needs platform", two_orgs["prog_a1"])
    blocker = _wp(a, "Platform build", two_orgs["prog_a2"])
    svc.add_dependency(blocked.id, blocker.id, organization_id=a.id)

    result = svc.blocked_by_another_programme(a.id)
    assert [r["work_package"].id for r in result] == [blocked.id]
    assert [b.id for b in result[0]["blocked_by"]] == [blocker.id]


def test_same_programme_dependency_is_not_blocked(two_orgs):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    one = _wp(a, "One", two_orgs["prog_a1"])
    two = _wp(a, "Two", two_orgs["prog_a1"])
    svc.add_dependency(one.id, two.id, organization_id=a.id)
    assert svc.blocked_by_another_programme(a.id) == []


def test_finished_dependency_no_longer_blocks(two_orgs):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    one = _wp(a, "One", two_orgs["prog_a1"])
    two = _wp(a, "Two", two_orgs["prog_a2"])
    svc.add_dependency(one.id, two.id, organization_id=a.id)
    svc.update_work_package(two.id, organization_id=a.id, status="completed")
    assert svc.blocked_by_another_programme(a.id) == []


def test_blocked_view_is_for_the_owning_organisation_only(two_orgs):
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    blocked = _wp(a, "A blocked", two_orgs["prog_a1"])
    blocker = _wp(a, "A blocker", two_orgs["prog_a2"])
    svc.add_dependency(blocked.id, blocker.id, organization_id=a.id)
    other = _wp(b, "B work", two_orgs["prog_b1"])
    other2 = _wp(b, "B more", two_orgs["prog_b2"])
    svc.add_dependency(other.id, other2.id, organization_id=b.id)

    names_a = {r["work_package"].name for r in svc.blocked_by_another_programme(a.id)}
    names_b = {r["work_package"].name for r in svc.blocked_by_another_programme(b.id)}
    assert names_a == {"A blocked"}
    assert names_b == {"B work"}


def test_dependency_ids_planted_across_organisations_do_not_leak(two_orgs):
    """Even a raw cross-organisation id in work_dependencies is not followed."""
    from app.services import work_package_service as svc

    a, b = two_orgs["a"], two_orgs["b"]
    mine = _wp(a, "Mine", two_orgs["prog_a1"])
    theirs = _wp(b, "Theirs", two_orgs["prog_b1"])
    mine.work_dependencies = [theirs.id]
    assert svc.blocked_by_another_programme(a.id) == []


# -- repointed screens ------------------------------------------------------


def _json(client, method, path, data=None):
    return getattr(client, method)(
        path, data=json.dumps(data or {}), content_type="application/json"
    )


def test_implementation_api_scoped_to_caller_organisation(two_orgs, client, login_as):
    theirs = _wp(two_orgs["b"], "B secret", two_orgs["prog_b1"])
    mine = _wp(two_orgs["a"], "A visible", two_orgs["prog_a1"])
    login_as(client, two_orgs["user_a"])

    listing = client.get("/implementation/api/work-packages").get_json()
    names = {w["name"] for w in listing["work_packages"]}
    assert "A visible" in names and "B secret" not in names

    assert client.get(f"/implementation/api/work-packages/{mine.id}").status_code == 200
    assert client.get(f"/implementation/api/work-packages/{theirs.id}").status_code == 404
    assert _json(client, "put", f"/implementation/api/work-packages/{theirs.id}",
                 {"name": "hijack"}).status_code == 404
    assert client.delete(f"/implementation/api/work-packages/{theirs.id}").status_code == 404


def test_implementation_create_writes_to_unified_store_in_callers_organisation(
    two_orgs, client, login_as
):
    from app.models.unified_work_package import UnifiedWorkPackage

    login_as(client, two_orgs["user_a"])
    resp = _json(client, "post", "/implementation/api/work-packages", {
        "name": "Created via screen",
        "programme_id": two_orgs["prog_a1"].id,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    row = UnifiedWorkPackage.query.get(resp.get_json()["work_package_id"])
    assert row.organization_id == two_orgs["a"].id
    assert row.enterprise_initiative_id == two_orgs["prog_a1"].id


def test_implementation_create_refuses_other_organisations_programme(
    two_orgs, client, login_as
):
    login_as(client, two_orgs["user_a"])
    resp = _json(client, "post", "/implementation/api/work-packages", {
        "name": "Wrong programme",
        "programme_id": two_orgs["prog_b1"].id,
    })
    assert resp.status_code in (400, 404), resp.get_data(as_text=True)


def test_dependency_endpoint_and_blocked_api(two_orgs, client, login_as):
    a = two_orgs["a"]
    blocked = _wp(a, "Blocked one", two_orgs["prog_a1"])
    blocker = _wp(a, "Blocker one", two_orgs["prog_a2"])
    theirs = _wp(two_orgs["b"], "B work", two_orgs["prog_b1"])
    login_as(client, two_orgs["user_a"])

    ok = _json(client, "post", f"/implementation/work-packages/{blocked.id}/dependencies",
               {"dependency_id": blocker.id})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    refused = _json(client, "post", f"/implementation/work-packages/{blocked.id}/dependencies",
                    {"dependency_id": theirs.id})
    assert refused.status_code == 404

    data = client.get("/implementation/api/work-packages/blocked").get_json()
    assert data["total"] == 1
    assert data["blocked"][0]["name"] == "Blocked one"
    assert data["blocked"][0]["blocked_by"][0]["name"] == "Blocker one"

    page = client.get("/implementation/work-packages/blocked")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Blocked one" in html and "Blocker one" in html


def test_blocked_api_for_other_organisation_is_empty(two_orgs, client, login_as):
    from app.services import work_package_service as svc

    a = two_orgs["a"]
    blocked = _wp(a, "Blocked A", two_orgs["prog_a1"])
    blocker = _wp(a, "Blocker A", two_orgs["prog_a2"])
    svc.add_dependency(blocked.id, blocker.id, organization_id=a.id)

    login_as(client, two_orgs["user_b"])
    data = client.get("/implementation/api/work-packages/blocked").get_json()
    assert data["total"] == 0
    assert "Blocked A" not in client.get(
        "/implementation/work-packages/blocked"
    ).get_data(as_text=True)


def test_edit_and_create_forms_render(two_orgs, client, login_as):
    wp = _wp(two_orgs["a"], "Editable", two_orgs["prog_a1"])
    login_as(client, two_orgs["user_a"])
    assert client.get("/implementation/work-packages/create").status_code == 200
    page = client.get(f"/implementation/work-packages/{wp.id}/edit")
    assert page.status_code == 200
    assert "Depends on" in page.get_data(as_text=True)


def test_other_organisation_edit_form_is_404(two_orgs, client, login_as):
    theirs = _wp(two_orgs["b"], "B form", two_orgs["prog_b1"])
    login_as(client, two_orgs["user_a"])
    assert client.get(f"/implementation/work-packages/{theirs.id}/edit").status_code == 404


def test_roadmap_api_scoped_to_caller_organisation(two_orgs, client, login_as):
    mine = _wp(two_orgs["a"], "Roadmap A", two_orgs["prog_a1"], business_capability="Cap")
    theirs = _wp(two_orgs["b"], "Roadmap B", two_orgs["prog_b1"], business_capability="Cap")
    login_as(client, two_orgs["user_a"])

    names = {w["name"] for w in client.get("/api/roadmap/work-packages").get_json()["work_packages"]}
    assert "Roadmap A" in names and "Roadmap B" not in names
    assert client.get(f"/api/roadmap/work-packages/{mine.id}").status_code == 200
    assert client.get(f"/api/roadmap/work-packages/{theirs.id}").status_code == 404
    assert _json(client, "put", f"/api/roadmap/work-packages/{theirs.id}",
                 {"name": "hijack"}).status_code == 404
    assert client.delete(f"/api/roadmap/work-packages/{theirs.id}").status_code == 404


def test_capability_roadmap_writer_scoped_to_caller_organisation(two_orgs, client, login_as):
    from app.models.unified_work_package import UnifiedWorkPackage

    theirs = _wp(two_orgs["b"], "Cap B", two_orgs["prog_b1"], business_capability="Cap")
    login_as(client, two_orgs["user_a"])

    assert _json(client, "put", f"/api/capability-work-packages/{theirs.id}",
                 {"name": "hijack"}).status_code == 404
    assert client.delete(f"/api/capability-work-packages/{theirs.id}").status_code == 404

    created = _json(client, "post", "/api/capability-work-packages", {
        "name": "Cap A", "business_capability": "Cap",
        "start_date": "2026-01-01", "end_date": "2026-02-01",
    })
    assert created.status_code == 200, created.get_data(as_text=True)
    row = UnifiedWorkPackage.query.get(int(created.get_json()["work_package"]["id"]))
    assert row.organization_id == two_orgs["a"].id
    assert UnifiedWorkPackage.query.get(theirs.id).name == "Cap B"


def test_form_post_with_placeholder_status_creates_with_defaults(two_orgs, client, login_as):
    """The form's empty "Select Status" option keeps the default status."""
    from app.models.unified_work_package import UnifiedWorkPackage

    login_as(client, two_orgs["user_a"])
    resp = client.post("/implementation/work-packages/create", data={
        "name": "From the form", "status": "", "description": "",
        "programme_id": str(two_orgs["prog_a1"].id), "start_date": "", "end_date": "",
    })
    assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
    row = UnifiedWorkPackage.query.filter_by(name="From the form").one()
    assert row.status == "planned"
    assert row.organization_id == two_orgs["a"].id


def test_repointed_screens_count_the_same_organisation_rows(two_orgs, client, login_as):
    """The screens that now read the one store give one number per organisation."""
    _wp(two_orgs["a"], "Count one", two_orgs["prog_a1"], business_capability="Cap")
    _wp(two_orgs["a"], "Count two", two_orgs["prog_a2"], business_capability="Cap")
    _wp(two_orgs["b"], "Other org", two_orgs["prog_b1"], business_capability="Cap")
    login_as(client, two_orgs["user_a"])

    implementation = client.get("/implementation/api/work-packages").get_json()["work_packages"]
    roadmap = client.get("/api/roadmap/work-packages?per_page=1").get_json()["pagination"]["total"]
    assert len(implementation) == 2
    assert roadmap == 2
