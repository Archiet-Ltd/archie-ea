"""Per-type ArchiMate pages: tenant isolation and the write-role gate.

Two holes closed together:

* Six typed tables (constraints, strategy_resources, application_services,
  application_interactions, application_collaborations, deliverables) had no
  organisation column, so a user of one organisation could read, rename and
  delete another organisation's row through the per-type routes.
* No per-type write route required a write role: a read-only Viewer could
  create, edit and delete on every per-type page.

Uses the shared ``db_session`` / ``make_org`` / ``login_as`` fixtures.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# type key -> (layer URL segment, minimum kwargs to build a row)
UNSCOPED_SIX = {
    "Constraint": "motivation",
    "Resource": "strategy",
    "ApplicationService": "application",
    "ApplicationInteraction": "application",
    "ApplicationCollaboration": "application",
    "Deliverable": "implementation",
}


def _registry():
    from app.modules.architecture.routes.archimate_crud.routes import (
        LAYER_CONFIG,
        MODEL_REGISTRY,
    )

    return MODEL_REGISTRY, LAYER_CONFIG


def _layer_of(element_type):
    _, layer_config = _registry()
    for layer, cfg in layer_config.items():
        if element_type in cfg["elements"]:
            return layer
    raise AssertionError(f"{element_type} is in no layer")


def _make_user(db_session, org_id, label, role_name, enterprise_role="solution_architect"):
    from app.models.user import Permission, Role, User

    user = User(
        email=f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        perms = {"Viewer": 0, "Administrator": Permission.ADMINISTER}.get(
            role_name, Permission.GENERAL
        )
        role = Role(name=role_name, permissions=perms, index="main", default=False)
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


def _make_row(db_session, element_type, org_id, name):
    """Insert one typed row for *org_id*; organization_id is set explicitly."""
    registry, _ = _registry()
    model = registry[element_type]
    row = model(name=name)
    if hasattr(row, "description"):
        row.description = "isolation probe"
    if element_type == "Resource":
        row.code = f"R-{uuid.uuid4().hex[:10]}"
    row.organization_id = org_id
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def org_a(make_org):
    return make_org("typed-a")


@pytest.fixture
def org_b(make_org):
    return make_org("typed-b")


@pytest.fixture
def architect_b(db_session, org_b):
    return _make_user(db_session, org_b.id, "ArchitectB", "Architect")


@pytest.fixture
def viewer(db_session, org_a):
    return _make_user(db_session, org_a.id, "ViewerA", "Viewer")


@pytest.fixture
def architect_a(db_session, org_a):
    return _make_user(db_session, org_a.id, "ArchitectA", "Architect")


# --------------------------------------------------------------------- #
# Structure: the six tables, and nothing else, were unscoped.            #
# --------------------------------------------------------------------- #


def test_every_registered_typed_table_carries_an_organisation():
    registry, _ = _registry()
    unscoped = sorted(
        key
        for key, model in registry.items()
        if "organization_id" not in model.__table__.columns
    )
    assert unscoped == [], f"typed tables with no organisation column: {unscoped}"


def test_the_six_tables_are_tenant_mixin_models():
    from app.models.mixins import TenantMixin

    registry, _ = _registry()
    for key in UNSCOPED_SIX:
        assert issubclass(registry[key], TenantMixin), key


# --------------------------------------------------------------------- #
# Hole 1: a user of organisation B cannot reach organisation A's row.    #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("element_type", sorted(UNSCOPED_SIX))
class TestCrossOrganisationRefused:
    def _row_of_a(self, db_session, org_a, element_type):
        row = _make_row(db_session, element_type, org_a.id, f"A-only {element_type}")
        db_session.commit()
        return row

    def test_detail_refused(
        self, client, login_as, db_session, org_a, architect_b, element_type
    ):
        row = self._row_of_a(db_session, org_a, element_type)
        login_as(client, architect_b)
        layer = _layer_of(element_type)
        resp = client.get(f"/architecture/{layer}/{element_type}/{row.id}")
        assert resp.status_code == 404
        assert b"A-only" not in resp.data

    def test_edit_refused_and_row_unchanged(
        self, client, login_as, db_session, org_a, architect_b, element_type
    ):
        row = self._row_of_a(db_session, org_a, element_type)
        row_id, original = row.id, row.name
        login_as(client, architect_b)
        layer = _layer_of(element_type)
        resp = client.post(
            f"/architecture/{layer}/{element_type}/{row_id}/edit",
            json={"name": "renamed by the other organisation"},
        )
        assert resp.status_code == 404
        db_session.expire_all()
        registry, _ = _registry()
        from flask import g

        g.current_org_id = org_a.id
        assert registry[element_type].query.get(row_id).name == original

    def test_delete_refused_and_row_survives(
        self, client, login_as, db_session, org_a, architect_b, element_type
    ):
        row = self._row_of_a(db_session, org_a, element_type)
        row_id = row.id
        login_as(client, architect_b)
        layer = _layer_of(element_type)
        resp = client.post(
            f"/architecture/{layer}/{element_type}/{row_id}/delete", json={}
        )
        assert resp.status_code == 404
        db_session.expire_all()
        registry, _ = _registry()
        from flask import g

        g.current_org_id = org_a.id
        assert registry[element_type].query.get(row_id) is not None

    def test_list_does_not_show_other_organisation(
        self, client, login_as, db_session, org_a, architect_b, element_type
    ):
        self._row_of_a(db_session, org_a, element_type)
        login_as(client, architect_b)
        layer = _layer_of(element_type)
        resp = client.get(
            f"/architecture/{layer}/{element_type}",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 200
        assert all(
            "A-only" not in (e.get("name") or "") for e in resp.get_json()["elements"]
        )

    def test_bulk_delete_cannot_remove_other_organisation(
        self, client, login_as, db_session, org_a, architect_b, element_type
    ):
        row = self._row_of_a(db_session, org_a, element_type)
        row_id = row.id
        login_as(client, architect_b)
        layer = _layer_of(element_type)
        client.post(
            f"/architecture/{layer}/{element_type}/bulk-delete", json={"ids": [row_id]}
        )
        db_session.expire_all()
        registry, _ = _registry()
        from flask import g

        g.current_org_id = org_a.id
        assert registry[element_type].query.get(row_id) is not None


@pytest.mark.parametrize("element_type", sorted(UNSCOPED_SIX))
def test_owner_organisation_still_reaches_its_own_row(
    client, login_as, db_session, org_a, architect_a, element_type
):
    row = _make_row(db_session, element_type, org_a.id, f"own {element_type}")
    db_session.commit()
    login_as(client, architect_a)
    layer = _layer_of(element_type)
    resp = client.get(f"/architecture/{layer}/{element_type}/{row.id}")
    assert resp.status_code == 200


# --------------------------------------------------------------------- #
# Hole 2: a Viewer is refused every write on every per-type page.        #
# --------------------------------------------------------------------- #


def _all_types():
    registry, _ = _registry()
    return sorted(registry)


def test_there_are_58_per_type_pages():
    assert len(_all_types()) == 58


@pytest.mark.parametrize("element_type", _all_types())
class TestViewerRefusedWrites:
    def test_create_refused(self, client, login_as, viewer, element_type):
        login_as(client, viewer)
        layer = _layer_of(element_type)
        assert client.get(f"/architecture/{layer}/{element_type}/new").status_code == 403
        resp = client.post(
            f"/architecture/{layer}/{element_type}/new",
            json={"name": "viewer probe", "description": "x"},
        )
        assert resp.status_code == 403

    def test_edit_refused(self, client, login_as, viewer, element_type):
        login_as(client, viewer)
        layer = _layer_of(element_type)
        assert client.get(f"/architecture/{layer}/{element_type}/1/edit").status_code == 403
        resp = client.post(
            f"/architecture/{layer}/{element_type}/1/edit", json={"name": "viewer probe"}
        )
        assert resp.status_code == 403

    def test_delete_refused(self, client, login_as, viewer, element_type):
        login_as(client, viewer)
        layer = _layer_of(element_type)
        resp = client.post(f"/architecture/{layer}/{element_type}/1/delete", json={})
        assert resp.status_code == 403
        resp = client.post(
            f"/architecture/{layer}/{element_type}/bulk-delete", json={"ids": [1]}
        )
        assert resp.status_code == 403


def test_viewer_cannot_patch_element(client, login_as, viewer):
    login_as(client, viewer)
    resp = client.patch("/architecture/api/elements/1", json={"name": "viewer probe"})
    assert resp.status_code == 403


def test_viewer_cannot_run_health_repair(client, login_as, viewer):
    login_as(client, viewer)
    resp = client.post("/architecture/api/health-scorecard/repair", json={"test": "x"})
    assert resp.status_code == 403


@pytest.mark.parametrize("element_type", _all_types())
def test_viewer_reads_remain_open(client, login_as, viewer, element_type):
    login_as(client, viewer)
    layer = _layer_of(element_type)
    resp = client.get(
        f"/architecture/{layer}/{element_type}",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200


def test_architect_can_still_create(client, login_as, db_session, architect_a):
    login_as(client, architect_a)
    resp = client.post(
        "/architecture/application/ApplicationService/new",
        json={"name": "architect-created", "description": "x"},
    )
    assert resp.status_code in (200, 201, 302)
    assert resp.status_code != 403
