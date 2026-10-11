"""Workforce-transition analysis — the people dimension of a transformation.

ArchiMate 3.2 models roles structurally but not their CHANGE, and a real
transformation lives or dies on the people move: which roles retire, which are
created, who transitions from job A to job B, the headcount delta, and the skills
gap to close. Entelim's `BusinessRole` already carries all of this — `replacement_role_id`
(role-to-role transition), `deprecated_date`/`operational_status` (retirement),
`current_filled_positions` + `forecasted_demand` (headcount as-is → to-be),
`required_skills` (competencies) — but nothing ever READ those fields, so the data
sat orphaned. This service is the missing consumer: it reads the existing role data
into a transition picture. It creates no new store and invents nothing — an empty
estate returns empty structures, never a fabricated number.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Set


class WorkforceTransitionService:
    """Reads BusinessRole workforce fields into an as-is → to-be transition view."""

    RETIRED_STATUSES = {"deprecated", "retired", "sunset", "eliminated"}

    @classmethod
    def analyze(cls) -> Dict[str, Any]:
        """Return the workforce-transition picture for the current tenant.

        BusinessRole is a TenantMixin model, so within a request its query is
        org-scoped automatically. Outside a request there is no tenant context and
        the caller must scope — this is intended to run in a request.
        """
        from app.models.business_layer import BusinessRole

        roles: List[BusinessRole] = BusinessRole.query.all()
        by_id = {r.id: r for r in roles}

        transitions: List[Dict[str, Any]] = []
        for r in roles:
            tgt = by_id.get(r.replacement_role_id) if r.replacement_role_id else None
            if tgt is not None:
                transitions.append({
                    "from_role": r.name,
                    "from_role_id": r.id,
                    "to_role": tgt.name,
                    "to_role_id": tgt.id,
                    "headcount_from": r.current_filled_positions or 0,
                    "headcount_to": cls._target_headcount(tgt),
                })

        retiring = [
            {"role": r.name, "id": r.id,
             "reason": "deprecated" if r.deprecated_date else (
                 (r.operational_status or "retired") if (r.operational_status or "").lower() in cls.RETIRED_STATUSES
                 else "superseded"
             )}
            for r in roles
            if r.deprecated_date is not None
            or (r.operational_status or "").lower() in cls.RETIRED_STATUSES
            # F-21, Capgemini dry-run: a role with a replacement_role_id is being
            # transitioned away from even when nobody ever flipped its status —
            # it belongs on the "roles being retired" list too, or the KPI
            # undercounts every role this same service's transition-pairs
            # section already reports as being replaced.
            or r.replacement_role_id is not None
        ]

        current = sum((r.current_filled_positions or 0) for r in roles)
        target = sum(cls._target_headcount(r) for r in roles)

        # Skills gap: skills required by GROWING roles (target > current) that no
        # stable-or-shrinking role already supplies — i.e. what must be hired/trained.
        needed: Set[str] = set()
        supplied: Set[str] = set()
        for r in roles:
            skills = cls._skills(r)
            if cls._target_headcount(r) > (r.current_filled_positions or 0):
                needed |= skills
            else:
                supplied |= skills

        return {
            "role_count": len(roles),
            "transitions": transitions,
            "retiring_roles": retiring,
            "new_or_growing_roles": [
                {"role": r.name, "id": r.id,
                 "headcount_from": r.current_filled_positions or 0,
                 "headcount_to": cls._target_headcount(r)}
                for r in roles
                if cls._target_headcount(r) > (r.current_filled_positions or 0)
            ],
            "headcount": {"current": current, "target": target, "delta": target - current},
            "skills_gap": sorted(needed - supplied),
        }

    @classmethod
    def _target_headcount(cls, role) -> int:
        """To-be headcount for a role. A role that is retiring or being replaced
        contributes 0 to the target estate — its people move to the replacement,
        which carries its own forecast, so the total is not double-counted.
        Otherwise: forecasted demand where recorded, else current filled (a role
        with no forecast is assumed steady, not zeroed)."""
        if role.deprecated_date is not None:
            return 0
        if (role.operational_status or "").lower() in cls.RETIRED_STATUSES:
            return 0
        if role.replacement_role_id:
            return 0
        if role.forecasted_demand is not None:
            return role.forecasted_demand
        return role.current_filled_positions or 0

    @staticmethod
    def _skills(role) -> Set[str]:
        raw = getattr(role, "required_skills", None)
        if not raw:
            return set()
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            return set()
        return {str(s).strip() for s in value if str(s).strip()} if isinstance(value, list) else set()
