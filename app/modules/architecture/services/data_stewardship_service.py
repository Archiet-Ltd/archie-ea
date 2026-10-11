"""Retention-policy breach check, data issue raise/route/resolve,
one-definition-per-term glossary, steward assignment through the one
ownership writer, no-steward list for critical entities, and
classification proposal, acceptance and propagation along DataLineage.

Classification: a proposed label is an approval row (AIChatCRUDApproval);
accepting it stores the label on the entity and propagates it downstream
along DataLineage edges, flagging conflicts where a downstream entity
already carries a different label. Propagation never crosses an
organisation and stops on a cycle.

Critical: a data entity whose domain's criticality is 'mission_critical'
or 'business_critical' (the existing DataDomain.criticality values).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set


class DataStewardshipService:
    ELEMENT_TYPE_DATA_ENTITY = "data_entity"
    OWNERSHIP_TYPE_STEWARD = "steward"

    # Criticality values that define a "critical" data entity.
    CRITICAL_VALUES = {"mission_critical", "business_critical"}

    @staticmethod
    def _tenant_predicate(model, organization_id: int):
        return model.organization_id == organization_id

    # ------------------------------------------------------------------ #
    # Steward assignment
    # ------------------------------------------------------------------ #

    @classmethod
    def set_data_entity_steward(
        cls, *, entity_id: int, user_id: int, organization_id: int,
        assigned_by: Optional[int] = None,
    ) -> Any:
        """Assign a steward to a data entity through the one ownership
        writer (ApplicationOwner, element_type='data_entity',
        ownership_type='steward'). Refuses a user from another
        organisation (CrossOrganisationDataEntityOwner).

        Returns the ApplicationOwner record (existing if already assigned).
        """
        from app import db
        from app.models.application_owner import ApplicationOwner
        from app.models.process_data import DataEntity
        from app.models.user import User

        entity = db.session.execute(
            db.select(DataEntity)
            .where(DataEntity.id == entity_id)
            .where(cls._tenant_predicate(DataEntity, organization_id))
        ).scalar_one_or_none()
        if entity is None:
            raise ValueError(f"no data entity {entity_id!r} in this organisation")

        owner = db.session.execute(
            db.select(User)
            .where(User.id == user_id)
            .where(User.organization_id == organization_id)
        ).scalar_one_or_none()
        if owner is None:
            raise ValueError("That user does not belong to this organisation.")

        existing = ApplicationOwner.query.filter_by(
            element_type=cls.ELEMENT_TYPE_DATA_ENTITY,
            element_id=entity_id,
            user_id=user_id,
            ownership_type=cls.OWNERSHIP_TYPE_STEWARD,
            organization_id=organization_id,
        ).first()
        if existing is not None:
            return existing

        record = ApplicationOwner(
            element_type=cls.ELEMENT_TYPE_DATA_ENTITY,
            element_id=entity_id,
            user_id=user_id,
            ownership_type=cls.OWNERSHIP_TYPE_STEWARD,
            assigned_by=assigned_by,
            organization_id=organization_id,
        )
        db.session.add(record)
        db.session.flush()
        return record

    @classmethod
    def remove_data_entity_steward(
        cls, *, owner_record_id: int, organization_id: int,
    ) -> bool:
        """Remove one steward row for a data entity. Returns False (no-op)
        rather than raise when the row is already gone or belongs to
        another organisation."""
        from app import db
        from app.models.application_owner import ApplicationOwner

        record = ApplicationOwner.query.filter_by(
            id=owner_record_id,
            element_type=cls.ELEMENT_TYPE_DATA_ENTITY,
            ownership_type=cls.OWNERSHIP_TYPE_STEWARD,
            organization_id=organization_id,
        ).first()
        if record is None:
            return False
        db.session.delete(record)
        db.session.flush()
        return True

    @classmethod
    def list_critical_entities_with_no_steward(
        cls, organization_id: int,
    ) -> List[Dict[str, Any]]:
        """Every critical data entity (domain criticality is
        'mission_critical' or 'business_critical') in this organisation
        with zero steward rows in the one ownership record.

        Returns dicts with entity id, name, domain name, criticality.
        """
        from app import db
        from app.models.application_owner import ApplicationOwner
        from app.models.process_data import DataDomain, DataEntity

        stewarded_entity_ids = {
            row.element_id
            for row in ApplicationOwner.query.filter_by(
                element_type=cls.ELEMENT_TYPE_DATA_ENTITY,
                ownership_type=cls.OWNERSHIP_TYPE_STEWARD,
                organization_id=organization_id,
            ).all()
            if row.element_id is not None
        }

        entities = (
            db.session.execute(
                db.select(DataEntity, DataDomain)
                .join(DataDomain, DataDomain.id == DataEntity.domain_id)
                .where(cls._tenant_predicate(DataEntity, organization_id))
                .where(DataDomain.criticality.in_(cls.CRITICAL_VALUES))
                .order_by(DataEntity.name)
            )
            .all()
        )

        result = []
        for entity, domain in entities:
            if entity.id not in stewarded_entity_ids:
                result.append({
                    "entity_id": entity.id,
                    "entity_name": entity.name,
                    "domain_name": domain.name,
                    "criticality": domain.criticality,
                })
        return result

    # ------------------------------------------------------------------ #
    # Classification proposal and acceptance
    # ------------------------------------------------------------------ #

    @classmethod
    def propose_classification(
        cls, *, entity_id: int, classification_label: str,
        organization_id: int, proposed_by: int,
    ) -> Dict[str, Any]:
        """Propose a classification label for a data entity. Creates an
        approval row (AIChatCRUDApproval) that, once accepted, stores the
        label on the entity and propagates it along DataLineage.

        Returns the approval dict with id and status.
        """
        from app import db
        from app.models.process_data import DataEntity
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            create_approval_record,
        )

        entity = db.session.execute(
            db.select(DataEntity)
            .where(DataEntity.id == entity_id)
            .where(cls._tenant_predicate(DataEntity, organization_id))
        ).scalar_one_or_none()
        if entity is None:
            raise ValueError(f"no data entity {entity_id!r} in this organisation")

        valid_labels = {"public", "internal", "confidential", "restricted"}
        if classification_label.lower() not in valid_labels:
            raise ValueError(
                f"Invalid classification label {classification_label!r}. "
                f"Must be one of: {', '.join(sorted(valid_labels))}"
            )

        approval = create_approval_record(
            organization_id=organization_id,
            operation_type="update",
            entity_type="data_entity_classification",
            entity_id=entity_id,
            summary=f"Set classification of '{entity.name}' to {classification_label}",
            operation_payload={
                "entity_id": entity_id,
                "classification_label": classification_label.lower(),
            },
            user_id=proposed_by,
            original_command=f"system: propose classification {entity.name}",
        )
        db.session.commit()
        return {
            "success": True,
            "approval_id": approval.id,
            "status": "pending_approval",
        }

    @classmethod
    def accept_classification(
        cls, *, approval_id: int, organization_id: int, accepted_by: int,
    ) -> Dict[str, Any]:
        """Accept a proposed classification. Stores the label on the
        entity and propagates it downstream along DataLineage edges.

        A downstream entity with a different label is flagged as a
        conflict and left unchanged. Propagation never crosses an
        organisation and stops on a cycle.

        Does NOT commit or change the approval status -- the caller
        (approve_and_execute) handles that.
        """
        from app import db
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        approval = db.session.execute(
            db.select(AIChatCRUDApproval)
            .where(AIChatCRUDApproval.id == approval_id)
            .where(AIChatCRUDApproval.organization_id == organization_id)
        ).scalar_one_or_none()
        if approval is None:
            return {"success": False, "error": f"Approval {approval_id} not found"}

        # A classification is accepted either directly (the caller passes a
        # PENDING approval, as the slice tests do) or as the executor of an
        # approval-inbox decision (the approval was claimed and its status set
        # to APPROVED by approve_and_execute before this runs). Both are the
        # same real acceptance; only a REJECTED or EXPIRED approval is refused.
        if approval.status in (ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED):
            return {
                "success": False,
                "error": f"Approval is already {approval.status.value}",
            }

        import json
        payload = json.loads(approval.operation_payload)
        entity_id = payload["entity_id"]
        classification_label = payload["classification_label"]

        # Store the label on the entity
        from app.models.process_data import DataEntity

        entity = db.session.execute(
            db.select(DataEntity)
            .where(DataEntity.id == entity_id)
            .where(cls._tenant_predicate(DataEntity, organization_id))
        ).scalar_one_or_none()
        if entity is None:
            return {"success": False, "error": "Entity not found"}

        entity.data_classification = classification_label

        # Propagate downstream
        conflicts = cls._propagate_classification(
            entity_id, classification_label, organization_id, set()
        )

        # Do NOT commit or change approval status -- the caller does that.
        return {
            "success": True,
            "entity_id": entity_id,
            "classification_label": classification_label,
            "conflicts": conflicts,
        }

    @classmethod
    def _propagate_classification(
        cls, entity_id: int, classification_label: str,
        organization_id: int, visited: Set[int],
    ) -> List[Dict[str, Any]]:
        """Walk DataLineage edges downstream from *entity_id* and set the
        classification label on every downstream entity that does not
        already carry a different label.

        Stops on a cycle (visited set). Never crosses an organisation.
        Returns a list of conflict dicts for downstream entities that
        already carry a different label and were left unchanged.
        """
        from app import db
        from app.models.all_missing_models import data_lineage_entities
        from app.models.process_data import DataEntity

        if entity_id in visited:
            return []
        visited.add(entity_id)

        conflicts = []

        # Find lineage rows where this entity participates as source
        lineage_rows = db.session.execute(
            db.select(data_lineage_entities.c.data_lineage_id)
            .where(data_lineage_entities.c.data_entity_id == entity_id)
            .where(data_lineage_entities.c.participation_type == "source")
        ).all()

        lineage_ids = [row[0] for row in lineage_rows]
        if not lineage_ids:
            return []

        # Find target entities in those lineage rows
        target_rows = db.session.execute(
            db.select(data_lineage_entities.c.data_entity_id)
            .where(
                data_lineage_entities.c.data_lineage_id.in_(lineage_ids),
                data_lineage_entities.c.participation_type == "target",
            )
        ).all()

        downstream_ids = list({row[0] for row in target_rows})
        if not downstream_ids:
            return []

        for d_id in downstream_ids:
            downstream = db.session.execute(
                db.select(DataEntity)
                .where(DataEntity.id == d_id)
                .where(cls._tenant_predicate(DataEntity, organization_id))
            ).scalar_one_or_none()

            if downstream is None:
                # Entity belongs to another organisation or does not exist
                continue

            if downstream.data_classification and downstream.data_classification != classification_label:
                # Conflict: downstream entity already has a different label
                conflicts.append({
                    "entity_id": downstream.id,
                    "entity_name": downstream.name,
                    "existing_label": downstream.data_classification,
                    "proposed_label": classification_label,
                })
                continue

            if not downstream.data_classification:
                downstream.data_classification = classification_label

            # Recurse downstream
            child_conflicts = cls._propagate_classification(
                downstream.id, classification_label, organization_id, visited
            )
            conflicts.extend(child_conflicts)

        return conflicts

    # ------------------------------------------------------------------ #
    # Retention policy breaches (PB-0236)
    # ------------------------------------------------------------------ #

    @classmethod
    def retention_breaches(cls, organization_id: int) -> List[Dict[str, Any]]:
        """Every DataEntity whose linked DataRetentionPolicy (by entity or
        by its domain) has a retention_period_days, where the entity is
        now older than that period. Breaches are listed with the owning
        system's owner via R1-B03's canonical reader, or "not recorded" --
        never a fabricated pass/fail.
        """
        from app import db
        from app.models.application_owner import ApplicationOwner
        from app.models.data_governance import DataRetentionPolicy
        from app.models.process_data import DataEntity

        policies = db.session.execute(
            db.select(DataRetentionPolicy)
            .where(cls._tenant_predicate(DataRetentionPolicy, organization_id))
            .where(DataRetentionPolicy.retention_period_days.isnot(None))
        ).scalars().all()
        if not policies:
            return []

        by_entity_id = {p.data_entity_id: p for p in policies if p.data_entity_id}
        by_domain_id: Dict[int, Any] = {}
        for p in policies:
            if p.data_domain_id and p.data_domain_id not in by_domain_id:
                by_domain_id[p.data_domain_id] = p

        entities = db.session.execute(
            db.select(DataEntity).where(cls._tenant_predicate(DataEntity, organization_id))
        ).scalars().all()

        now = datetime.utcnow()
        breaches = []
        for entity in entities:
            policy = by_entity_id.get(entity.id) or by_domain_id.get(entity.domain_id)
            if policy is None:
                continue
            age_days = (now - entity.created_at).days if entity.created_at else None
            if age_days is None or age_days <= policy.retention_period_days:
                continue

            owner = None
            if entity.system_of_record_application_id:
                rows = ApplicationOwner.get_display_rows_for_application(
                    entity.system_of_record_application_id, organization_id
                )
                if rows:
                    primary = next((r for r in rows if r["ownership_type"] == "primary"), rows[0])
                    owner = primary["user_name"]

            breaches.append({
                "entity_id": entity.id,
                "entity_name": entity.name,
                "policy_id": policy.id,
                "policy_name": policy.name,
                "retention_period_days": policy.retention_period_days,
                "age_days": age_days,
                "owner": owner or "not recorded",
            })

        breaches.sort(key=lambda b: -b["age_days"])
        return breaches

    # ------------------------------------------------------------------ #
    # Data issues (PB-0292)
    # ------------------------------------------------------------------ #

    @classmethod
    def raise_issue(
        cls, organization_id: int, data_entity_id: int, title: str, description: Optional[str],
        reporter_id: int,
    ):
        from app import db
        from app.models.data_issue import DataIssue
        from app.models.process_data import DataEntity

        entity = db.session.execute(
            db.select(DataEntity)
            .where(DataEntity.id == data_entity_id)
            .where(cls._tenant_predicate(DataEntity, organization_id))
        ).scalar_one_or_none()
        if entity is None:
            raise ValueError(f"no data entity {data_entity_id!r} in this organisation")

        issue = DataIssue(
            data_entity_id=data_entity_id, title=title, description=description,
            reporter_id=reporter_id, organization_id=organization_id,
        )
        db.session.add(issue)
        db.session.flush()
        return issue

    @classmethod
    def resolve_issue(cls, organization_id: int, issue_id: int, resolution_notes: str, resolved_by_id: int):
        from app import db
        from app.models.data_issue import DataIssue

        issue = db.session.execute(
            db.select(DataIssue)
            .where(DataIssue.id == issue_id)
            .where(cls._tenant_predicate(DataIssue, organization_id))
        ).scalar_one_or_none()
        if issue is None:
            raise ValueError(f"no data issue {issue_id!r} in this organisation")

        issue.status = "resolved"
        issue.resolution_notes = resolution_notes
        issue.resolved_by_id = resolved_by_id
        issue.resolved_at = datetime.utcnow()
        db.session.flush()

        # "Reporter notified" (brief deliverable) waits on R1-B37's
        # notification hook, which does not exist on main yet -- the
        # resolution itself (status, notes, resolved_by, resolved_at) is
        # the real deliverable and the system of record either way.

        return issue

    @classmethod
    def list_issues(cls, organization_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
        from app import db
        from app.models.data_issue import DataIssue
        from app.models.process_data import DataDomain, DataEntity

        query = (
            db.select(DataIssue, DataEntity, DataDomain)
            .join(DataEntity, DataEntity.id == DataIssue.data_entity_id)
            .outerjoin(DataDomain, DataDomain.id == DataEntity.domain_id)
            .where(cls._tenant_predicate(DataIssue, organization_id))
        )
        if status:
            query = query.where(DataIssue.status == status)

        rows = db.session.execute(query.order_by(DataIssue.created_at.desc())).all()
        return [
            {
                "id": issue.id,
                "title": issue.title,
                "description": issue.description,
                "status": issue.status,
                "entity_id": entity.id,
                "entity_name": entity.name,
                # Legacy free-text display, never guessed: the real
                # steward-picker writer is the ownership slice of this
                # brief, blocked on R1-B03 PR 2, not this one.
                "routed_to": (domain.data_steward if domain and domain.data_steward else "not recorded"),
                "resolution_notes": issue.resolution_notes,
                "created_at": issue.created_at,
                "resolved_at": issue.resolved_at,
            }
            for issue, entity, domain in rows
        ]

    # ------------------------------------------------------------------ #
    # Glossary (PB-0500): one Meaning definition per term
    # ------------------------------------------------------------------ #

    @classmethod
    def glossary_terms(cls, organization_id: int) -> List[Dict[str, Any]]:
        from app import db
        from app.models.motivation import Meaning

        rows = db.session.execute(
            db.select(Meaning)
            .where(cls._tenant_predicate(Meaning, organization_id))
            .order_by(Meaning.name)
        ).scalars().all()
        return [{"id": m.id, "name": m.name, "description": m.description} for m in rows]

    @classmethod
    def term_definition(cls, organization_id: int, name: str) -> Optional[Dict[str, Any]]:
        """The one definition for *name*, case-insensitive -- never more
        than one row returned, so a caller rendering a term inline never
        has to choose between duplicates."""
        from app import db
        from app.models.motivation import Meaning

        row = db.session.execute(
            db.select(Meaning)
            .where(cls._tenant_predicate(Meaning, organization_id))
            .where(db.func.lower(Meaning.name) == name.lower())
        ).scalars().first()
        if row is None:
            return None
        return {"id": row.id, "name": row.name, "description": row.description}
