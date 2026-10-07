"""R1-B81 (policy/issue/glossary slice): retention-policy breach check,
data issue raise/route/resolve, and the one-definition-per-term glossary.

Classification and the steward-picker writer are a different slice of
this same brief, blocked on R1-B03 PR 2 and R1-B07 PR 2 -- not owned here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class DataStewardshipService:
    @staticmethod
    def _tenant_predicate(model, organization_id: int):
        return model.organization_id == organization_id

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
