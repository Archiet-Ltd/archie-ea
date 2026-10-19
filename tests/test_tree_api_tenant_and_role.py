"""Generic tree API: write-role gate and tenant isolation.

Two holes closed together:

* ``/capability-map/api/trees/<key>`` create, edit and delete required only a
  signed-in user, so a read-only Viewer could change any of 21 hierarchies.
* Seven of the backing models (tasks, portfolio initiatives, system boundaries,
  compliance requirements, vendor capability and process hierarchies, required
  capabilities) had no organisation column, so one organisation could read,
  rename and delete another's rows. Two catalogues (APQC, product taxonomy) are
  shared by every organisation and are now writable by a platform administrator
  only, and shared reference-model capabilities are read-only to tenants.

Uses the shared ``db_session`` / ``make_org`` / ``login_as`` fixtures.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

BASE = "/capability-map/api/trees"

# Trees whose model was unscoped before this change.
NEWLY_SCOPED = [
    "compliance",
    "portfolio_initiative",
    "system_boundary",
    "task",
    "vendor_capability",
    "vendor_process",
    "required_capability",
]
PLATFORM = ["apqc", "product_taxonomy"]
HYBRID = ["capability", "reference_capability"]


def _registry():
    from app.services.tree_service import TREE_REGISTRY

    return TREE_REGISTRY


def _model(key):
    from app.services.tree_service import _import_model

    return _import_model(_registry()[key]["model"])


def _int_pk(key):
    """The node routes take ``<int:node_id>``; a model keyed by UUID (Task) is
    never reachable by id through them, only through the tree listing."""
    import sqlalchemy as sa

    (column,) = _model(key).__table__.primary_key.columns
    return isinstance(column.type, sa.Integer)


def _tenant_keys():
    return sorted(k for k in _registry() if k not in PLATFORM + HYBRID)


def _make_user(db_session, org_id, label, role_name):
    from app.models.user import Permission, Role, User

    user = User(
        email=f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="solution_architect",
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


@pytest.fixture
def org_a(make_org):
    return make_org("tree-a")


@pytest.fixture
def org_b(make_org):
    return make_org("tree-b")


@pytest.fixture
def architect_a(db_session, org_a):
    return _make_user(db_session, org_a.id, "ArchitectA", "Architect")


@pytest.fixture
def architect_b(db_session, org_b):
    return _make_user(db_session, org_b.id, "ArchitectB", "Architect")


@pytest.fixture
def viewer_a(db_session, org_a):
    return _make_user(db_session, org_a.id, "ViewerA", "Viewer")


# --------------------------------------------------------------------- #
# Structure: every tree is accounted for, and none is silently unscoped. #
# --------------------------------------------------------------------- #


def test_all_21_trees_are_classified_exactly_once():
    from app.services.tree_service import HYBRID_TREES, PLATFORM_TREES

    assert len(_registry()) == 21
    assert set(PLATFORM_TREES) == set(PLATFORM)
    assert set(HYBRID_TREES) == set(HYBRID)
    assert not set(PLATFORM_TREES) & set(HYBRID_TREES)
    assert set(PLATFORM) | set(HYBRID) <= set(_registry())


@pytest.mark.parametrize("key", _tenant_keys())
def test_every_tenant_tree_model_is_a_tenant_mixin(key):
    from app.models.mixins import TenantMixin

    assert issubclass(_model(key), TenantMixin), key
    assert "organization_id" in _model(key).__table__.columns


@pytest.mark.parametrize("key", NEWLY_SCOPED)
def test_newly_scoped_models_carry_a_required_organisation(key):
    column = _model(key).__table__.columns["organization_id"]
    assert column.nullable is False
    assert column.foreign_keys


@pytest.mark.parametrize("key", HYBRID)
def test_hybrid_models_keep_their_own_nullable_mechanism(key):
    from app.models.mixins import TenantMixin

    model = _model(key)
    assert not issubclass(model, TenantMixin)
    assert model.__table__.columns["organization_id"].nullable is True


def test_a_tenant_tree_whose_model_lost_its_mixin_refuses_writes(monkeypatch):
    from app.services import tree_service

    class Bare:  # no TenantMixin
        pass

    assert tree_service._write_gate("task", Bare).startswith(tree_service.FORBIDDEN)
    assert tree_service._write_gate("task", _model("task")) is None


# --------------------------------------------------------------------- #
# Hole 1: a Viewer is refused every write on every tree.                 #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(_registry()))
class TestViewerRefusedWrites:
    def test_create_refused(self, client, login_as, viewer_a, key):
        login_as(client, viewer_a)
        resp = client.post(f"{BASE}/{key}", json={"name": "viewer probe"})
        assert resp.status_code == 403

    def test_update_refused(self, client, login_as, viewer_a, key):
        login_as(client, viewer_a)
        resp = client.patch(f"{BASE}/{key}/1", json={"name": "viewer probe"})
        assert resp.status_code == 403

    def test_delete_refused(self, client, login_as, viewer_a, key):
        login_as(client, viewer_a)
        assert client.delete(f"{BASE}/{key}/1").status_code == 403


@pytest.mark.parametrize("key", sorted(_registry()))
def test_viewer_reads_remain_open(client, login_as, viewer_a, key):
    login_as(client, viewer_a)
    assert client.get(f"{BASE}/{key}").status_code == 200


def test_viewer_write_leaves_no_row(client, login_as, db_session, viewer_a):
    login_as(client, viewer_a)
    client.post(f"{BASE}/task", json={"name": "viewer-made task"})
    from flask import g

    g.current_org_id = viewer_a.organization_id
    assert _model("task").query.filter_by(title="viewer-made task").count() == 0


# --------------------------------------------------------------------- #
# Hole 2: a user of organisation B cannot reach organisation A's node.   #
# --------------------------------------------------------------------- #


def _seed(db_session, key, user, name):
    """Insert one node owned by ``user``'s organisation, with whatever parents the
    model requires. Several models cannot be created through the tree API at all
    (the API sets only the tree fields, not their mandatory foreign keys)."""
    from flask import g

    org_id = user.organization_id
    g.current_org_id = org_id  # listeners that insert linked rows read the context
    model = _model(key)
    name_field = _registry()[key]["name_field"]
    kwargs = {name_field: name, "organization_id": org_id}

    if key == "compliance":
        kwargs.update(description="isolation probe", requirement_type="regulatory")
    elif key == "system_boundary":
        from app.models.archimate_core import ArchiMateElement

        element = ArchiMateElement(
            name=name, type="Grouping", layer="application", organization_id=org_id
        )
        db_session.add(element)
        db_session.flush()
        kwargs.update(system_name=name, archimate_element_id=element.id)
    elif key == "task":
        from app.models.project_models import Project

        project = Project(name=f"P {name}", code=f"P{uuid.uuid4().hex[:8]}", organization_id=org_id)
        db_session.add(project)
        db_session.flush()
        kwargs.update(project_id=project.id)
    elif key in ("vendor_capability", "vendor_process", "vendor_component", "vendor_service"):
        from app.models.vendor_stack_template import VendorStackTemplate

        template = VendorStackTemplate(
            vendor_name=f"Probe {uuid.uuid4().hex[:8]}", name=f"T {uuid.uuid4().hex[:8]}", organization_id=org_id
        )
        db_session.add(template)
        db_session.flush()
        kwargs.update(template_id=template.id)
        if key == "vendor_capability":
            kwargs.update(level=1)
    elif key == "required_capability":
        from app.models.business_capabilities import BusinessCapability
        from app.models.vendor_analysis import OptionsAnalysis

        capability = BusinessCapability(name=f"BC {name}", organization_id=org_id)
        db_session.add(capability)
        db_session.flush()
        analysis = OptionsAnalysis(
            name=f"A {name}",
            capability_id=capability.id,
            created_by_id=user.id,
            organization_id=org_id,
        )
        db_session.add(analysis)
        db_session.flush()
        kwargs.update(analysis_id=analysis.id, importance="high")
    elif key in ("work_package", "solution", "goal", "requirement", "business_process",
                 "archimate", "business_actor", "organization_unit", "portfolio_initiative"):
        if "description" in _registry()[key]["fields"]:
            kwargs.update(description="isolation probe")
        if key == "archimate":
            kwargs.update(type="BusinessActor", layer="Business")
        if key == "requirement":
            kwargs.update(requirement_type="functional")

    row = model(**kwargs)
    db_session.add(row)
    db_session.commit()
    return row.id


def _count_for_org(key, org_id, node_id):
    from flask import g

    g.current_org_id = org_id
    return _model(key).query.filter_by(id=node_id).count()


@pytest.mark.parametrize("key", _tenant_keys())
class TestCrossOrganisationRefused:
    def test_owner_creates_and_reads_own_node(
        self, client, login_as, db_session, architect_a, key
    ):
        node_id = _seed(db_session, key, architect_a, f"A-only {key}")
        login_as(client, architect_a)
        if _int_pk(key):
            assert client.get(f"{BASE}/{key}/{node_id}").status_code == 200
        names = _flatten(client.get(f"{BASE}/{key}").get_json())
        assert f"A-only {key}" in names

    def test_node_refused(self, client, login_as, db_session, architect_a, architect_b, key):
        if not _int_pk(key):
            pytest.skip("node routes take an integer id; this model is keyed by UUID")
        node_id = _seed(db_session, key, architect_a, f"A-only {key}")
        login_as(client, architect_b)
        resp = client.get(f"{BASE}/{key}/{node_id}")
        assert resp.status_code == 404
        assert b"A-only" not in resp.data

    def test_tree_does_not_show_other_organisation(
        self, client, login_as, db_session, architect_a, architect_b, key
    ):
        _seed(db_session, key, architect_a, f"A-only {key}")
        login_as(client, architect_b)
        names = _flatten(client.get(f"{BASE}/{key}").get_json())
        assert f"A-only {key}" not in names

    def test_edit_refused_and_row_unchanged(
        self, client, login_as, db_session, org_a, architect_a, architect_b, key
    ):
        if not _int_pk(key):
            pytest.skip("node routes take an integer id; this model is keyed by UUID")
        node_id = _seed(db_session, key, architect_a, f"A-only {key}")
        login_as(client, architect_b)
        resp = client.patch(f"{BASE}/{key}/{node_id}", json={"name": "renamed by B"})
        assert resp.status_code == 404
        db_session.expire_all()
        name_field = _registry()[key]["name_field"]
        from flask import g

        g.current_org_id = org_a.id
        assert getattr(_model(key).query.get(node_id), name_field) == f"A-only {key}"

    def test_delete_refused_and_row_survives(
        self, client, login_as, db_session, org_a, architect_a, architect_b, key
    ):
        if not _int_pk(key):
            pytest.skip("node routes take an integer id; this model is keyed by UUID")
        node_id = _seed(db_session, key, architect_a, f"A-only {key}")
        login_as(client, architect_b)
        assert client.delete(f"{BASE}/{key}/{node_id}").status_code == 404
        db_session.expire_all()
        assert _count_for_org(key, org_a.id, node_id) == 1

    def test_cannot_parent_under_other_organisation(
        self, client, login_as, db_session, org_a, architect_a, architect_b, key
    ):
        if not _int_pk(key):
            pytest.skip("node routes take an integer id; this model is keyed by UUID")
        a_id = _seed(db_session, key, architect_a, f"A-only {key}")
        login_as(client, architect_b)
        resp = client.post(f"{BASE}/{key}", json={"name": "B child", "parent_id": a_id})
        assert resp.status_code == 400
        b_id = _seed(db_session, key, architect_b, "B root")
        resp = client.patch(f"{BASE}/{key}/{b_id}", json={"parent_id": a_id})
        assert resp.status_code == 400


def _flatten(tree):
    names = []
    for node in tree.get("children", []):
        names.append(node["name"])
        names.extend(_flatten(node))
    return names


# --------------------------------------------------------------------- #
# Platform catalogues: readable by all, writable by a platform admin only #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("key", PLATFORM)
class TestPlatformCatalogues:
    def test_organisation_architect_refused(self, client, login_as, architect_a, key):
        login_as(client, architect_a)
        assert client.post(f"{BASE}/{key}", json={"name": "probe"}).status_code == 403
        assert client.patch(f"{BASE}/{key}/1", json={"name": "probe"}).status_code == 403
        assert client.delete(f"{BASE}/{key}/1").status_code == 403

    def test_organisation_architect_cannot_alter_an_existing_row(
        self, client, login_as, db_session, architect_a, key
    ):
        model = _model(key)
        name_field = _registry()[key]["name_field"]
        kwargs = {name_field: f"shared {uuid.uuid4().hex[:8]}"}
        if key == "apqc":
            kwargs["process_code"] = f"T{uuid.uuid4().hex[:8]}"
        row = model(**kwargs)
        db_session.add(row)
        db_session.commit()
        original = getattr(row, name_field)
        login_as(client, architect_a)
        assert client.patch(f"{BASE}/{key}/{row.id}", json={"name": "hijacked"}).status_code == 403
        assert client.delete(f"{BASE}/{key}/{row.id}").status_code == 403
        db_session.expire_all()
        assert getattr(model.query.get(row.id), name_field) == original

    def test_reads_stay_open(self, client, login_as, architect_a, key):
        login_as(client, architect_a)
        assert client.get(f"{BASE}/{key}").status_code == 200


# --------------------------------------------------------------------- #
# Hybrid trees: shared rows are read-only to tenants, own rows editable. #
# --------------------------------------------------------------------- #


@pytest.fixture
def reference_model(db_session):
    from app.models.reference_models import ReferenceModel

    model = ReferenceModel(name=f"RM {uuid.uuid4().hex[:8]}", code=f"RM{uuid.uuid4().hex[:8]}")
    db_session.add(model)
    db_session.flush()
    return model


def _shared_reference_capability(db_session, reference_model):
    from app.models.reference_models import ReferenceModelCapability

    row = ReferenceModelCapability(
        reference_model_id=reference_model.id, name="Shared reference", organization_id=None
    )
    db_session.add(row)
    db_session.commit()
    return row


def _owned_reference_capability(db_session, reference_model, org_id, name):
    from app.models.reference_models import ReferenceModelCapability

    row = ReferenceModelCapability(
        reference_model_id=reference_model.id, name=name, organization_id=org_id
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestReferenceCapabilityTree:
    def test_tenant_reads_shared_rows(
        self, client, login_as, db_session, reference_model, architect_a
    ):
        shared = _shared_reference_capability(db_session, reference_model)
        login_as(client, architect_a)
        assert client.get(f"{BASE}/reference_capability/{shared.id}").status_code == 200
        assert "Shared reference" in _flatten(client.get(f"{BASE}/reference_capability").get_json())

    def test_tenant_cannot_edit_or_delete_shared_row(
        self, client, login_as, db_session, reference_model, architect_a
    ):
        shared = _shared_reference_capability(db_session, reference_model)
        login_as(client, architect_a)
        resp = client.patch(
            f"{BASE}/reference_capability/{shared.id}", json={"name": "hijacked"}
        )
        assert resp.status_code == 403
        assert client.delete(f"{BASE}/reference_capability/{shared.id}").status_code == 403
        db_session.expire_all()
        from app.models.reference_models import ReferenceModelCapability

        assert db_session.get(ReferenceModelCapability, shared.id).name == "Shared reference"

    def test_other_organisation_cannot_see_or_change_owned_row(
        self, client, login_as, db_session, reference_model, org_a, architect_b
    ):
        owned = _owned_reference_capability(db_session, reference_model, org_a.id, "A-only ref")
        login_as(client, architect_b)
        assert client.get(f"{BASE}/reference_capability/{owned.id}").status_code == 404
        assert "A-only ref" not in _flatten(
            client.get(f"{BASE}/reference_capability").get_json()
        )
        assert (
            client.patch(
                f"{BASE}/reference_capability/{owned.id}", json={"name": "renamed by B"}
            ).status_code
            == 404
        )
        assert client.delete(f"{BASE}/reference_capability/{owned.id}").status_code == 404
        db_session.expire_all()
        from app.models.reference_models import ReferenceModelCapability

        assert db_session.get(ReferenceModelCapability, owned.id).name == "A-only ref"

    def test_owner_can_edit_and_delete_own_row(
        self, client, login_as, db_session, reference_model, org_a, architect_a
    ):
        owned = _owned_reference_capability(db_session, reference_model, org_a.id, "A-only ref")
        login_as(client, architect_a)
        resp = client.patch(
            f"{BASE}/reference_capability/{owned.id}", json={"name": "renamed by A"}
        )
        assert resp.status_code == 200
        assert client.delete(f"{BASE}/reference_capability/{owned.id}").status_code == 200

    def test_delete_does_not_reparent_another_organisations_child(
        self, client, login_as, db_session, reference_model, org_a, org_b, architect_a
    ):
        """delete_node reparents a deleted node's children to its own parent.
        That reparenting query used no tenancy filter at all, so deleting a
        node org_a legitimately owns could silently rewrite another
        organisation's private child row -- a row org_a cannot even see
        through this API. Only a child visible to the caller (shared, or the
        caller's own) may be reparented; org_b's private child must come out
        of this delete untouched.
        """
        from app.models.reference_models import ReferenceModelCapability

        parent = _owned_reference_capability(db_session, reference_model, org_a.id, "A parent")
        own_child = _owned_reference_capability(db_session, reference_model, org_a.id, "A child")
        own_child.parent_capability_id = parent.id
        other_org_child = _owned_reference_capability(db_session, reference_model, org_b.id, "B child")
        other_org_child.parent_capability_id = parent.id
        db_session.commit()

        login_as(client, architect_a)
        assert client.delete(f"{BASE}/reference_capability/{parent.id}").status_code == 200

        db_session.expire_all()
        assert db_session.get(ReferenceModelCapability, own_child.id).parent_capability_id is None
        assert db_session.get(ReferenceModelCapability, other_org_child.id).parent_capability_id == parent.id


class TestUnifiedCapabilityTree:
    """UnifiedCapability keeps its own session listeners; they are correct."""

    def _make(self, db_session, org_id, name):
        from app.models.unified_capability import UnifiedCapability

        row = UnifiedCapability(name=name, organization_id=org_id)
        db_session.add(row)
        db_session.commit()
        return row

    def test_shared_row_is_readable_but_not_writable_by_a_tenant(
        self, client, login_as, db_session, architect_a
    ):
        shared = self._make(db_session, None, "Shared capability")
        login_as(client, architect_a)
        assert client.get(f"{BASE}/capability/{shared.id}").status_code == 200
        assert (
            client.patch(f"{BASE}/capability/{shared.id}", json={"name": "hijacked"}).status_code
            == 403
        )
        assert client.delete(f"{BASE}/capability/{shared.id}").status_code == 403
        db_session.expire_all()
        from app.models.unified_capability import UnifiedCapability

        assert db_session.get(UnifiedCapability, shared.id).name == "Shared capability"

    def test_other_organisation_row_is_invisible_and_untouchable(
        self, client, login_as, db_session, org_a, architect_b
    ):
        owned = self._make(db_session, org_a.id, "A-only capability")
        login_as(client, architect_b)
        assert client.get(f"{BASE}/capability/{owned.id}").status_code == 404
        assert (
            client.patch(f"{BASE}/capability/{owned.id}", json={"name": "renamed"}).status_code
            == 404
        )
        assert client.delete(f"{BASE}/capability/{owned.id}").status_code == 404
        assert "A-only capability" not in _flatten(client.get(f"{BASE}/capability").get_json())

    def test_owner_can_edit_own_row(self, client, login_as, db_session, org_a, architect_a):
        owned = self._make(db_session, org_a.id, "A-only capability")
        login_as(client, architect_a)
        assert (
            client.patch(f"{BASE}/capability/{owned.id}", json={"name": "renamed"}).status_code
            == 200
        )


# Trees whose rows can be created from the tree fields alone.
API_CREATABLE = ["business_process", "goal", "organization_unit", "portfolio_initiative"]


@pytest.mark.parametrize("key", API_CREATABLE)
def test_architect_can_still_create_edit_and_delete(client, login_as, architect_a, key):
    login_as(client, architect_a)
    resp = client.post(f"{BASE}/{key}", json={"name": "architect-made", "description": "x"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    node_id = resp.get_json()["id"]
    resp = client.patch(f"{BASE}/{key}/{node_id}", json={"name": "architect-renamed"})
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "architect-renamed"
    assert client.delete(f"{BASE}/{key}/{node_id}").status_code == 200
    assert client.get(f"{BASE}/{key}/{node_id}").status_code == 404


@pytest.mark.parametrize("key", API_CREATABLE)
def test_created_node_belongs_to_the_callers_organisation(
    client, login_as, db_session, org_a, architect_a, key
):
    login_as(client, architect_a)
    node_id = client.post(f"{BASE}/{key}", json={"name": "stamped", "description": "x"}).get_json()["id"]
    from flask import g

    g.current_org_id = org_a.id
    assert _model(key).query.get(node_id).organization_id == org_a.id
