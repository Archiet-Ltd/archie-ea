"""Slice 2: steward assignment, no-steward list, classification
proposal, acceptance and propagation along DataLineage.

Every new test fails on main and passes here. Two-organisation tests
prove the other organisation's rows are never returned, changed or named.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from app.modules.architecture.services.data_stewardship_service import DataStewardshipService


def _uid():
    return uuid.uuid4().hex[:8]


def _domain(db_session, org_id, criticality="mission_critical", name=None):
    from app.models.process_data import DataDomain

    d = DataDomain(
        name=name or f"Domain {_uid()}",
        criticality=criticality,
        organization_id=org_id,
    )
    db_session.add(d)
    db_session.flush()
    return d


def _entity(db_session, org_id, domain=None, name=None, **kw):
    from app.models.process_data import DataEntity

    domain = domain or _domain(db_session, org_id)
    e = DataEntity(
        name=name or f"Entity {_uid()}",
        domain_id=domain.id,
        organization_id=org_id,
        **kw,
    )
    db_session.add(e)
    db_session.flush()
    return e


def _user(db_session, org_id):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()
    u = User(
        email=f"stew-{_uid()}@example.com", first_name="T", last_name="U",
        organization_id=org_id, role=admin_role, confirmed=True,
    )
    db_session.add(u)
    db_session.flush()
    return u


def _lineage(db_session, org_id, source_entity, target_entity):
    """Create a DataLineage row linking source -> target."""
    from app.models.all_missing_models import DataLineage, data_lineage_entities

    lineage = DataLineage(
        name=f"Lineage {_uid()}",
        organization_id=org_id,
    )
    db_session.add(lineage)
    db_session.flush()

    db_session.execute(
        data_lineage_entities.insert().values(
            data_lineage_id=lineage.id,
            data_entity_id=source_entity.id,
            participation_type="source",
        )
    )
    db_session.execute(
        data_lineage_entities.insert().values(
            data_lineage_id=lineage.id,
            data_entity_id=target_entity.id,
            participation_type="target",
        )
    )
    db_session.flush()
    return lineage


# ======================================================================
# Steward assignment
# ======================================================================


class TestStewardAssignment:
    def test_assign_steward_persists_as_owner_row(self, db_session, make_org):
        """Assigning a steward creates an ApplicationOwner row with
        element_type='data_entity' and ownership_type='steward'."""
        org = make_org("steward-persist")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        steward = _user(db_session, org.id)
        db_session.commit()

        record = DataStewardshipService.set_data_entity_steward(
            entity_id=entity.id, user_id=steward.id,
            organization_id=org.id, assigned_by=steward.id,
        )
        db_session.commit()

        assert record.element_type == "data_entity"
        assert record.ownership_type == "steward"
        assert record.element_id == entity.id
        assert record.user_id == steward.id
        assert record.organization_id == org.id

    def test_assign_steward_twice_returns_existing(self, db_session, make_org):
        """Assigning the same steward twice returns the existing row."""
        org = make_org("steward-twice")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        steward = _user(db_session, org.id)
        db_session.commit()

        first = DataStewardshipService.set_data_entity_steward(
            entity_id=entity.id, user_id=steward.id,
            organization_id=org.id,
        )
        db_session.commit()
        second = DataStewardshipService.set_data_entity_steward(
            entity_id=entity.id, user_id=steward.id,
            organization_id=org.id,
        )
        assert first.id == second.id

    def test_assign_steward_from_another_org_is_refused(self, db_session, make_org):
        """Assigning a user from another organisation raises ValueError."""
        org_a = make_org("steward-org-a")
        org_b = make_org("steward-org-b")
        domain = _domain(db_session, org_a.id)
        entity = _entity(db_session, org_a.id, domain)
        foreign_user = _user(db_session, org_b.id)
        db_session.commit()

        with pytest.raises(ValueError, match="does not belong to this organisation"):
            DataStewardshipService.set_data_entity_steward(
                entity_id=entity.id, user_id=foreign_user.id,
                organization_id=org_a.id,
            )

    def test_assign_steward_to_foreign_entity_is_refused(self, db_session, make_org):
        """Assigning a steward to an entity in another org raises ValueError."""
        org_a = make_org("steward-entity-a")
        org_b = make_org("steward-entity-b")
        domain = _domain(db_session, org_a.id)
        entity_a = _entity(db_session, org_a.id, domain)
        user_b = _user(db_session, org_b.id)
        db_session.commit()

        with pytest.raises(ValueError, match="no data entity"):
            DataStewardshipService.set_data_entity_steward(
                entity_id=entity_a.id, user_id=user_b.id,
                organization_id=org_b.id,
            )

    def test_remove_steward(self, db_session, make_org):
        """Removing a steward deletes the owner row."""
        org = make_org("steward-remove")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        steward = _user(db_session, org.id)
        db_session.commit()

        record = DataStewardshipService.set_data_entity_steward(
            entity_id=entity.id, user_id=steward.id,
            organization_id=org.id,
        )
        db_session.commit()

        removed = DataStewardshipService.remove_data_entity_steward(
            owner_record_id=record.id, organization_id=org.id,
        )
        db_session.commit()
        assert removed is True

        from app.models.application_owner import ApplicationOwner
        assert ApplicationOwner.query.get(record.id) is None

    def test_remove_steward_noop_for_missing(self, db_session, make_org):
        """Removing a non-existent steward returns False."""
        org = make_org("steward-noop")
        result = DataStewardshipService.remove_data_entity_steward(
            owner_record_id=99999, organization_id=org.id,
        )
        assert result is False

    def test_two_orgs_stewards_never_cross(self, db_session, make_org):
        """Organisation A's stewards are invisible to organisation B."""
        org_a = make_org("steward-fence-a")
        org_b = make_org("steward-fence-b")
        domain_a = _domain(db_session, org_a.id)
        domain_b = _domain(db_session, org_b.id)
        entity_a = _entity(db_session, org_a.id, domain_a)
        entity_b = _entity(db_session, org_b.id, domain_b)
        user_a = _user(db_session, org_a.id)
        user_b = _user(db_session, org_b.id)
        db_session.commit()

        DataStewardshipService.set_data_entity_steward(
            entity_id=entity_a.id, user_id=user_a.id,
            organization_id=org_a.id,
        )
        DataStewardshipService.set_data_entity_steward(
            entity_id=entity_b.id, user_id=user_b.id,
            organization_id=org_b.id,
        )
        db_session.commit()

        from app.models.application_owner import ApplicationOwner

        a_stewards = ApplicationOwner.query.filter_by(
            element_type="data_entity", ownership_type="steward",
            organization_id=org_a.id,
        ).all()
        b_stewards = ApplicationOwner.query.filter_by(
            element_type="data_entity", ownership_type="steward",
            organization_id=org_b.id,
        ).all()
        assert len(a_stewards) == 1
        assert len(b_stewards) == 1
        assert a_stewards[0].element_id == entity_a.id
        assert b_stewards[0].element_id == entity_b.id


# ======================================================================
# No-steward list
# ======================================================================


class TestNoStewardList:
    def test_critical_entity_with_no_steward_appears(self, db_session, make_org):
        """A critical entity with no steward appears in the list."""
        org = make_org("no-steward-appears")
        domain = _domain(db_session, org.id, criticality="mission_critical")
        entity = _entity(db_session, org.id, domain)
        db_session.commit()

        result = DataStewardshipService.list_critical_entities_with_no_steward(org.id)
        ids = [r["entity_id"] for r in result]
        assert entity.id in ids

    def test_critical_entity_with_steward_does_not_appear(self, db_session, make_org):
        """A critical entity with a steward does not appear in the list."""
        org = make_org("no-steward-excluded")
        domain = _domain(db_session, org.id, criticality="business_critical")
        entity = _entity(db_session, org.id, domain)
        steward = _user(db_session, org.id)
        db_session.commit()

        DataStewardshipService.set_data_entity_steward(
            entity_id=entity.id, user_id=steward.id,
            organization_id=org.id,
        )
        db_session.commit()

        result = DataStewardshipService.list_critical_entities_with_no_steward(org.id)
        ids = [r["entity_id"] for r in result]
        assert entity.id not in ids

    def test_non_critical_entity_does_not_appear(self, db_session, make_org):
        """A non-critical entity (important, supporting) does not appear."""
        org = make_org("no-steward-noncritical")
        domain = _domain(db_session, org.id, criticality="important")
        entity = _entity(db_session, org.id, domain)
        db_session.commit()

        result = DataStewardshipService.list_critical_entities_with_no_steward(org.id)
        ids = [r["entity_id"] for r in result]
        assert entity.id not in ids

    def test_two_orgs_no_steward_never_cross(self, db_session, make_org):
        """Organisation A's no-steward entities are invisible to B."""
        org_a = make_org("no-steward-fence-a")
        org_b = make_org("no-steward-fence-b")
        domain_a = _domain(db_session, org_a.id, criticality="mission_critical")
        domain_b = _domain(db_session, org_b.id, criticality="mission_critical")
        entity_a = _entity(db_session, org_a.id, domain_a)
        entity_b = _entity(db_session, org_b.id, domain_b)
        db_session.commit()

        result_a = DataStewardshipService.list_critical_entities_with_no_steward(org_a.id)
        result_b = DataStewardshipService.list_critical_entities_with_no_steward(org_b.id)
        assert entity_a.id in [r["entity_id"] for r in result_a]
        assert entity_b.id not in [r["entity_id"] for r in result_a]
        assert entity_b.id in [r["entity_id"] for r in result_b]
        assert entity_a.id not in [r["entity_id"] for r in result_b]


# ======================================================================
# Classification proposal and acceptance
# ======================================================================


class TestClassification:
    def test_propose_classification_creates_approval_row(self, db_session, make_org):
        """Proposing a classification creates a pending approval row."""
        org = make_org("class-propose")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        proposer = _user(db_session, org.id)
        db_session.commit()

        result = DataStewardshipService.propose_classification(
            entity_id=entity.id, classification_label="confidential",
            organization_id=org.id, proposed_by=proposer.id,
        )
        assert result["success"] is True
        assert result["status"] == "pending_approval"
        assert result["approval_id"] is not None

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        approval = db_session.get(AIChatCRUDApproval, result["approval_id"])
        assert approval is not None
        assert approval.operation_type == "update"
        assert approval.entity_type == "data_entity_classification"
        payload = json.loads(approval.operation_payload)
        assert payload["entity_id"] == entity.id
        assert payload["classification_label"] == "confidential"

    def test_propose_invalid_label_is_refused(self, db_session, make_org):
        """Proposing an invalid classification label raises ValueError."""
        org = make_org("class-invalid")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        proposer = _user(db_session, org.id)
        db_session.commit()

        with pytest.raises(ValueError, match="Invalid classification label"):
            DataStewardshipService.propose_classification(
                entity_id=entity.id, classification_label="invalid",
                organization_id=org.id, proposed_by=proposer.id,
            )

    def test_accept_classification_stores_label(self, db_session, make_org):
        """Accepting a classification stores the label on the entity."""
        org = make_org("class-accept")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        proposer = _user(db_session, org.id)
        approver = _user(db_session, org.id)
        db_session.commit()

        proposal = DataStewardshipService.propose_classification(
            entity_id=entity.id, classification_label="restricted",
            organization_id=org.id, proposed_by=proposer.id,
        )
        db_session.commit()

        result = DataStewardshipService.accept_classification(
            approval_id=proposal["approval_id"],
            organization_id=org.id,
            accepted_by=approver.id,
        )
        db_session.commit()

        assert result["success"] is True
        assert result["classification_label"] == "restricted"

        from app.models.process_data import DataEntity

        db_session.expire_all()
        reloaded = db_session.get(DataEntity, entity.id)
        assert reloaded.data_classification == "restricted"

    def test_accept_classification_propagates_downstream(self, db_session, make_org):
        """Accepting a classification propagates the label downstream
        along DataLineage."""
        org = make_org("class-propagate")
        domain = _domain(db_session, org.id)
        source = _entity(db_session, org.id, domain, name="Source")
        downstream = _entity(db_session, org.id, domain, name="Downstream")
        _lineage(db_session, org.id, source, downstream)
        proposer = _user(db_session, org.id)
        approver = _user(db_session, org.id)
        db_session.commit()

        proposal = DataStewardshipService.propose_classification(
            entity_id=source.id, classification_label="confidential",
            organization_id=org.id, proposed_by=proposer.id,
        )
        db_session.commit()

        result = DataStewardshipService.accept_classification(
            approval_id=proposal["approval_id"],
            organization_id=org.id,
            accepted_by=approver.id,
        )
        db_session.commit()

        assert result["success"] is True
        assert len(result["conflicts"]) == 0

        from app.models.process_data import DataEntity

        db_session.expire_all()
        assert db_session.get(DataEntity, source.id).data_classification == "confidential"
        assert db_session.get(DataEntity, downstream.id).data_classification == "confidential"

    def test_accept_classification_flags_conflict(self, db_session, make_org):
        """A downstream entity with a different label is flagged as a
        conflict and left unchanged."""
        org = make_org("class-conflict")
        domain = _domain(db_session, org.id)
        source = _entity(db_session, org.id, domain, name="Source")
        downstream = _entity(
            db_session, org.id, domain, name="Downstream",
            data_classification="public",
        )
        _lineage(db_session, org.id, source, downstream)
        proposer = _user(db_session, org.id)
        approver = _user(db_session, org.id)
        db_session.commit()

        proposal = DataStewardshipService.propose_classification(
            entity_id=source.id, classification_label="confidential",
            organization_id=org.id, proposed_by=proposer.id,
        )
        db_session.commit()

        result = DataStewardshipService.accept_classification(
            approval_id=proposal["approval_id"],
            organization_id=org.id,
            accepted_by=approver.id,
        )
        db_session.commit()

        assert result["success"] is True
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["entity_id"] == downstream.id
        assert result["conflicts"][0]["existing_label"] == "public"
        assert result["conflicts"][0]["proposed_label"] == "confidential"

        from app.models.process_data import DataEntity

        db_session.expire_all()
        # Source gets the label
        assert db_session.get(DataEntity, source.id).data_classification == "confidential"
        # Downstream keeps its existing label
        assert db_session.get(DataEntity, downstream.id).data_classification == "public"

    def test_accept_classification_stops_on_cycle(self, db_session, make_org):
        """Propagation terminates on a cycle without infinite recursion."""
        org = make_org("class-cycle")
        domain = _domain(db_session, org.id)
        a = _entity(db_session, org.id, domain, name="A")
        b = _entity(db_session, org.id, domain, name="B")
        _lineage(db_session, org.id, a, b)
        _lineage(db_session, org.id, b, a)  # cycle
        proposer = _user(db_session, org.id)
        approver = _user(db_session, org.id)
        db_session.commit()

        proposal = DataStewardshipService.propose_classification(
            entity_id=a.id, classification_label="internal",
            organization_id=org.id, proposed_by=proposer.id,
        )
        db_session.commit()

        result = DataStewardshipService.accept_classification(
            approval_id=proposal["approval_id"],
            organization_id=org.id,
            accepted_by=approver.id,
        )
        db_session.commit()

        assert result["success"] is True

        from app.models.process_data import DataEntity

        db_session.expire_all()
        assert db_session.get(DataEntity, a.id).data_classification == "internal"
        assert db_session.get(DataEntity, b.id).data_classification == "internal"

    def test_two_orgs_classification_never_crosses(self, db_session, make_org):
        """Organisation A's classification proposals and labels are
        invisible to B; propagation never reaches B's entities."""
        org_a = make_org("class-fence-a")
        org_b = make_org("class-fence-b")
        domain_a = _domain(db_session, org_a.id)
        domain_b = _domain(db_session, org_b.id)
        entity_a = _entity(db_session, org_a.id, domain_a)
        entity_b = _entity(db_session, org_b.id, domain_b)
        proposer_a = _user(db_session, org_a.id)
        proposer_b = _user(db_session, org_b.id)
        db_session.commit()

        # A proposes and accepts a classification
        proposal_a = DataStewardshipService.propose_classification(
            entity_id=entity_a.id, classification_label="confidential",
            organization_id=org_a.id, proposed_by=proposer_a.id,
        )
        db_session.commit()

        DataStewardshipService.accept_classification(
            approval_id=proposal_a["approval_id"],
            organization_id=org_a.id,
            accepted_by=proposer_a.id,
        )
        db_session.commit()

        # B proposes and accepts a different classification
        proposal_b = DataStewardshipService.propose_classification(
            entity_id=entity_b.id, classification_label="public",
            organization_id=org_b.id, proposed_by=proposer_b.id,
        )
        db_session.commit()

        DataStewardshipService.accept_classification(
            approval_id=proposal_b["approval_id"],
            organization_id=org_b.id,
            accepted_by=proposer_b.id,
        )
        db_session.commit()

        from app.models.process_data import DataEntity

        db_session.expire_all()
        assert db_session.get(DataEntity, entity_a.id).data_classification == "confidential"
        assert db_session.get(DataEntity, entity_b.id).data_classification == "public"

    def test_accept_classification_on_foreign_org_approval_not_found(self, db_session, make_org):
        """Accepting a classification approval from another org returns
        not found (foreign rows look absent)."""
        org_a = make_org("class-foreign-a")
        org_b = make_org("class-foreign-b")
        domain_a = _domain(db_session, org_a.id)
        entity_a = _entity(db_session, org_a.id, domain_a)
        proposer_a = _user(db_session, org_a.id)
        db_session.commit()

        proposal = DataStewardshipService.propose_classification(
            entity_id=entity_a.id, classification_label="internal",
            organization_id=org_a.id, proposed_by=proposer_a.id,
        )
        db_session.commit()

        result = DataStewardshipService.accept_classification(
            approval_id=proposal["approval_id"],
            organization_id=org_b.id,
            accepted_by=proposer_a.id,
        )
        assert result["success"] is False
        assert "not found" in result["error"]


# ======================================================================
# Route tests
# ======================================================================


def _route_user(db_session, org_id, role="data_architect"):
    from app.models.user import User

    u = User(
        email=f"route.{role}.{_uid()}@example.com", first_name="R", last_name="T",
        organization_id=org_id, enterprise_role=role, confirmed=True,
    )
    u.password = "Passw0rd!x"
    db_session.add(u)
    db_session.flush()
    return u


def _route_app(db_session, org_id, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


class TestStewardRoutes:
    def test_assign_steward_route_persists(self, app, client, db_session, make_org, login_as):
        """POST to /data-governance/entities/<id>/steward assigns a steward."""
        org = make_org("route-steward")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        steward = _route_user(db_session, org.id)
        db_session.commit()

        login_as(client, steward)
        resp = client.post(
            f"/data-governance/entities/{entity.id}/steward",
            data={"user_id": steward.id},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Steward assigned" in resp.data

        from app.models.application_owner import ApplicationOwner

        rows = ApplicationOwner.query.filter_by(
            element_type="data_entity", element_id=entity.id,
            ownership_type="steward", organization_id=org.id,
        ).all()
        assert len(rows) == 1
        assert rows[0].user_id == steward.id

    def test_assign_steward_route_refuses_missing_user(self, app, client, db_session, make_org, login_as):
        """POST without a user_id shows an error."""
        org = make_org("route-steward-nouser")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        architect = _route_user(db_session, org.id)
        db_session.commit()

        login_as(client, architect)
        resp = client.post(
            f"/data-governance/entities/{entity.id}/steward",
            data={},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"A user must be selected" in resp.data

    def test_no_steward_page_shows_critical_entities(self, app, client, db_session, make_org, login_as):
        """GET /data-governance/no-steward lists critical entities with no steward."""
        org = make_org("route-no-steward")
        domain = _domain(db_session, org.id, criticality="mission_critical")
        entity = _entity(db_session, org.id, domain)
        architect = _route_user(db_session, org.id)
        db_session.commit()

        login_as(client, architect)
        resp = client.get("/data-governance/no-steward")
        assert resp.status_code == 200
        assert entity.name.encode() in resp.data

    def test_no_steward_page_refuses_unauthorised(self, app, client, db_session, make_org, login_as):
        """GET /data-governance/no-steward returns 403 for procurement."""
        org = make_org("route-no-steward-403")
        buyer = _route_user(db_session, org.id, "procurement")
        db_session.commit()

        login_as(client, buyer)
        resp = client.get("/data-governance/no-steward")
        assert resp.status_code == 403

    def test_remove_steward_route(self, app, client, db_session, make_org, login_as):
        """POST to /data-governance/entities/<id>/steward/<oid>/remove removes."""
        org = make_org("route-remove-steward")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        steward = _route_user(db_session, org.id)
        db_session.commit()

        from app.models.application_owner import ApplicationOwner

        record = ApplicationOwner(
            element_type="data_entity", element_id=entity.id,
            user_id=steward.id, ownership_type="steward",
            organization_id=org.id,
        )
        db_session.add(record)
        db_session.commit()

        login_as(client, steward)
        resp = client.post(
            f"/data-governance/entities/{entity.id}/steward/{record.id}/remove",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Steward removed" in resp.data
        assert ApplicationOwner.query.get(record.id) is None


class TestClassificationRoutes:
    def test_propose_classification_route(self, app, client, db_session, make_org, login_as):
        """POST to /data-governance/entities/<id>/classify creates an approval."""
        org = make_org("route-classify")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        architect = _route_user(db_session, org.id)
        db_session.commit()

        login_as(client, architect)
        resp = client.post(
            f"/data-governance/entities/{entity.id}/classify",
            data={"classification_label": "confidential"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Classification proposal created" in resp.data

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        approvals = AIChatCRUDApproval.query.filter_by(
            entity_type="data_entity_classification",
            organization_id=org.id,
        ).all()
        assert len(approvals) == 1
        payload = json.loads(approvals[0].operation_payload)
        assert payload["classification_label"] == "confidential"

    def test_propose_classification_route_refuses_empty_label(self, app, client, db_session, make_org, login_as):
        """POST without a classification_label shows an error."""
        org = make_org("route-classify-nolabel")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        architect = _route_user(db_session, org.id)
        db_session.commit()

        login_as(client, architect)
        resp = client.post(
            f"/data-governance/entities/{entity.id}/classify",
            data={},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"A classification label is required" in resp.data

    def test_entity_detail_shows_steward_and_classification_ui(self, app, client, db_session, make_org, login_as):
        """Entity detail page includes steward picker and classification UI."""
        org = make_org("route-detail-ui")
        domain = _domain(db_session, org.id)
        entity = _entity(db_session, org.id, domain)
        architect = _route_user(db_session, org.id)
        db_session.commit()

        login_as(client, architect)
        resp = client.get(f"/data-governance/entities/{entity.id}")
        assert resp.status_code == 200
        assert b"steward-picker" in resp.data
        assert b"classification-picker" in resp.data
        assert b"assign-steward-button" in resp.data
        assert b"propose-classification-button" in resp.data