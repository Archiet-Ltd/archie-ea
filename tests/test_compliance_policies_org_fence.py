"""Compliance policies and violations belong to one organisation.

``compliance_policies`` and ``compliance_violations`` used to be global: a policy
an administrator of one organisation created was listed to every signed-in user
of every other. Two organisations, no mocked tenant context, real routes.
"""
from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "migrations" / "versions"


# --------------------------------------------------------------- pure checks


def test_both_models_are_tenant_models_with_a_per_organisation_name():
    from app.models.compliance_models import CompliancePolicy, ComplianceViolation
    from app.models.mixins.core import TenantMixin

    assert issubclass(CompliancePolicy, TenantMixin)
    assert issubclass(ComplianceViolation, TenantMixin)
    assert CompliancePolicy.__table__.c.name.unique is not True
    unique = {
        tuple(c.name for c in con.columns)
        for con in CompliancePolicy.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("organization_id", "name") in unique


def test_tables_are_no_longer_listed_as_unfenced():
    listed = {
        line.strip()
        for line in (REPO / "scripts" / "unfenced_tables.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "compliance_policies" not in listed
    assert "compliance_violations" not in listed


def test_fencing_revision_lists_both_tables_and_chains_after_the_column_revision():
    source = (VERSIONS / "20261016_compliance_org_rls.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    values = {
        t.id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name) and t.id in {"revision", "down_revision", "TENANT_TABLES"}
    }
    assert values["TENANT_TABLES"] == ("compliance_policies", "compliance_violations")
    assert values["down_revision"] == "20261016_compliance_org_id"
    assert len(values["revision"]) <= 32


def test_column_revision_fails_closed_and_does_not_assign_an_organisation_to_policies():
    source = (VERSIONS / "20261016_compliance_org_id.py").read_text(encoding="utf-8")
    # Violations inherit from their policy; no statement stamps an organisation on policies.
    assert not re.search(r"UPDATE\s+compliance_policies", source, re.I)
    assert "left NULL" in source
    assert "nullable=False" in source


# ---------------------------------------------------------------- the routes


def _user(db_session, org, role_name="Administrator"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"cfence-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.compliance_models import CompliancePolicy, ComplianceViolation

    class World:
        pass

    w = World()
    w.org_a, w.org_b = make_org("cfa"), make_org("cfb")
    w.admin_a, w.admin_b = _user(db_session, w.org_a), _user(db_session, w.org_b)
    w.policy_a = CompliancePolicy(organization_id=w.org_a.id, name="Shared name", policy_type="NIST")
    w.policy_b = CompliancePolicy(organization_id=w.org_b.id, name="Policy of B", policy_type="CIS")
    db_session.add_all([w.policy_a, w.policy_b])
    db_session.flush()
    w.violation_a = ComplianceViolation(
        organization_id=w.org_a.id, policy_id=w.policy_a.id, description="A violation", status="Open"
    )
    w.violation_b = ComplianceViolation(
        organization_id=w.org_b.id, policy_id=w.policy_b.id, description="B violation", status="Open"
    )
    db_session.add_all([w.violation_a, w.violation_b])
    db_session.commit()
    return w


def _count(where):
    from app import db

    return db.session.execute(text(f"select count(*) from compliance_violations where {where}")).scalar()  # nosec B608 - literal in this module


def _ids(response, key):
    assert response.status_code == 200, response.get_data(as_text=True)
    return {row["id"] for row in response.get_json()["data"][key]}


def test_policy_and_violation_lists_show_only_the_callers_organisation(client, login_as, world):
    login_as(client, world.admin_b)
    assert _ids(client.get("/enterprise/compliance/policies"), "policies") == {world.policy_b.id}
    assert _ids(client.get("/enterprise/compliance/violations"), "violations") == {world.violation_b.id}
    login_as(client, world.admin_a)
    assert _ids(client.get("/enterprise/compliance/policies"), "policies") == {world.policy_a.id}
    assert _ids(client.get("/enterprise/compliance/violations"), "violations") == {world.violation_a.id}


def test_dashboard_counts_only_the_callers_organisation(client, login_as, world):
    login_as(client, world.admin_b)
    data = client.get("/enterprise/compliance").get_json()["data"]
    assert data["total_policies"] == 1 and data["total_violations"] == 1


def test_search_returns_only_the_callers_organisation(client, login_as, world):
    login_as(client, world.admin_b)
    data = client.get("/enterprise/search?q=violation&type=compliance").get_json()["data"]
    assert [v["id"] for v in data["violations"]] == [world.violation_b.id]
    data = client.get("/enterprise/search?q=Shared&type=compliance").get_json()["data"]
    assert data["policies"] == []


def test_a_policy_created_by_one_organisation_is_not_listed_to_the_other(client, login_as, world):
    login_as(client, world.admin_a)
    created = client.post(
        "/enterprise/compliance/policies", json={"name": "Only for A", "type": "ISO", "description": "x"}
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    new_id = created.get_json()["data"]["id"]
    login_as(client, world.admin_b)
    assert new_id not in _ids(client.get("/enterprise/compliance/policies"), "policies")


def test_the_same_policy_name_is_allowed_in_two_organisations(client, login_as, world):
    # Organisation A already holds "Shared name"; B creating it succeeds, so a
    # 409 cannot be used to learn what another organisation named a policy.
    login_as(client, world.admin_b)
    created = client.post("/enterprise/compliance/policies", json={"name": "Shared name", "type": "ISO"})
    assert created.status_code == 201, created.get_data(as_text=True)
    again = client.post("/enterprise/compliance/policies", json={"name": "Shared name", "type": "ISO"})
    assert again.status_code == 409


def test_a_violation_cannot_be_logged_against_another_organisations_policy(client, login_as, world):
    login_as(client, world.admin_b)
    response = client.post(
        "/enterprise/compliance/violations",
        json={"policy_id": world.policy_a.id, "description": "planted", "severity": "High"},
    )
    assert response.status_code == 404
    assert _count("description = 'planted'") == 0


def test_a_violation_logged_in_one_organisation_carries_that_organisation(client, login_as, world):
    login_as(client, world.admin_b)
    response = client.post(
        "/enterprise/compliance/violations",
        json={"policy_id": world.policy_b.id, "description": "own", "severity": "Low"},
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    from app.models.compliance_models import ComplianceViolation

    row = ComplianceViolation.query.filter_by(id=response.get_json()["data"]["id"]).one()
    assert row.organization_id == world.org_b.id


def test_another_organisations_violation_cannot_be_changed(client, login_as, world):
    login_as(client, world.admin_b)
    response = client.put(
        f"/enterprise/compliance/violations/{world.violation_a.id}", json={"status": "Resolved"}
    )
    assert response.status_code == 404
    from app import db

    status = db.session.execute(
        text("select status from compliance_violations where id = :i"), {"i": world.violation_a.id}
    ).scalar()
    assert status == "Open"


def test_another_organisations_violation_cannot_be_deleted(client, login_as, world):
    login_as(client, world.admin_b)
    response = client.delete(f"/enterprise/compliance/violations/{world.violation_a.id}")
    assert response.status_code == 404
    # Raw SQL: the ORM would apply organisation B's filter, which is still the test context.
    from app import db

    still_there = db.session.execute(
        text("select count(*) from compliance_violations where id = :i"), {"i": world.violation_a.id}
    ).scalar()
    assert still_there == 1


def test_own_violation_can_still_be_changed_and_deleted(client, login_as, world):
    login_as(client, world.admin_a)
    changed = client.put(
        f"/enterprise/compliance/violations/{world.violation_a.id}", json={"status": "Resolved"}
    )
    assert changed.status_code == 200, changed.get_data(as_text=True)
    deleted = client.delete(f"/enterprise/compliance/violations/{world.violation_a.id}")
    assert deleted.status_code == 200, deleted.get_data(as_text=True)


def test_policy_id_sent_as_a_string_is_handled_and_still_fenced(client, login_as, world):
    login_as(client, world.admin_b)
    own = client.post(
        "/enterprise/compliance/violations",
        json={"policy_id": str(world.policy_b.id), "description": "string id", "severity": "Low"},
    )
    assert own.status_code == 201, own.get_data(as_text=True)
    other = client.post(
        "/enterprise/compliance/violations",
        json={"policy_id": str(world.policy_a.id), "description": "string id planted", "severity": "Low"},
    )
    assert other.status_code == 404


def test_dashboard_with_no_active_organisation_counts_nothing(app, db_session, world, monkeypatch):
    import app.modules.capabilities.routes.enterprise_crud_routes as routes

    monkeypatch.setattr(routes, "current_org_id", lambda: None)
    with app.test_request_context("/enterprise/compliance"):
        view = routes.compliance_dashboard
        view = getattr(view, "__wrapped__", view)
        data = view().get_json()["data"]
    assert data["total_policies"] == 0 and data["total_violations"] == 0


def test_write_roles_are_unchanged(client, login_as, db_session, world):
    member = _user(db_session, world.org_a, role_name="User")
    db_session.commit()
    login_as(client, member)
    response = client.post("/enterprise/compliance/policies", json={"name": "Nope", "type": "ISO"})
    assert response.status_code in (401, 403)


def test_with_no_active_organisation_search_and_reads_return_nothing(app, db_session, world, monkeypatch):
    from app.services.enterprise_search_service import EnterpriseSearchService

    monkeypatch.setattr("app.services.enterprise_search_service.current_org_id", lambda: None)
    results = EnterpriseSearchService.search("violation", "compliance")
    assert results["policies"] == [] and results["violations"] == [] and results["total"] == 0
