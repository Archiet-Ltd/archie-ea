# app/services/kanban_projection_service.py
"""
KanbanProjectionService — Virtual card projection for ADM Kanban v2.

Queries source entity models (Solution, ARBReviewItem, ConsolidationOpportunity,
Gap) and returns unified card-shaped dicts. No KanbanCard rows are created.
The source entity IS the card.
"""

import logging
from typing import Any, Collection, Dict, List, Optional

from flask import url_for

from app import db
from app.services.sap_activate_mapping import get_activate_stage

logger = logging.getLogger(__name__)

# Column definitions
COLUMNS = [
    {"id": "proposed", "label": "Proposed"},
    {"id": "under_development", "label": "Under Development"},
    {"id": "review", "label": "Review"},
    {"id": "approved", "label": "Approved"},
    {"id": "implementing", "label": "Implementing"},
    {"id": "done", "label": "Done"},
]

COLUMN_IDS = [c["id"] for c in COLUMNS]

# WIP limits per column (0 = unlimited)
WIP_LIMITS = {
    "proposed": 0,
    "under_development": 5,
    "review": 3,
    "approved": 5,
    "implementing": 4,
    "done": 0,
}

# ADM Phase definitions
ADM_PHASES = [
    {"code": "PRELIM", "label": "Preliminary", "order": 0},
    {"code": "A", "label": "Architecture Vision", "order": 1},
    {"code": "B", "label": "Business Architecture", "order": 2},
    {"code": "C", "label": "IS Architecture", "order": 3},
    {"code": "D", "label": "Technology Architecture", "order": 4},
    {"code": "E", "label": "Opportunities & Solutions", "order": 5},
    {"code": "F", "label": "Migration Planning", "order": 6},
    {"code": "G", "label": "Impl. Governance", "order": 7},
    {"code": "H", "label": "Change Management", "order": 8},
    {"code": "REQ", "label": "Requirements Mgmt", "order": 9},
]

for _phase in ADM_PHASES:
    _phase["activate_stage"] = get_activate_stage(_phase["code"])
del _phase

# Governance status -> kanban column
_SOLUTION_COLUMN_MAP = {
    "draft": "proposed",
    "proposed": "proposed",
    "in_progress": "under_development",
    "arb_review": "review",
    "approved": "approved",
    "rejected": "proposed",
    "deployed": "done",
    "deprecated": "done",
}

# Deployment status overrides when governance_status is "approved"
_DEPLOY_COLUMN_MAP = {
    "design": "implementing",
    "development": "implementing",
    "testing": "implementing",
    "production": "done",
}

# ADM Deliverable document_status -> kanban column
_DELIVERABLE_COLUMN_MAP = {
    "draft": "proposed",
    "in_progress": "under_development",
    "review": "review",
    "approved": "approved",
    "implementing": "implementing",
    "published": "done",
    "archived": "done",
}

# Reverse map: column -> document_status (for move)
_COLUMN_TO_DOC_STATUS = {
    "proposed": "draft",
    "under_development": "in_progress",
    "review": "review",
    "approved": "approved",
    "implementing": "implementing",
    "done": "published",
}

# KanbanCard status -> kanban column
_TASK_COLUMN_MAP = {
    "backlog": "proposed",
    "todo": "proposed",
    "in_progress": "under_development",
    "review": "review",
    "done": "done",
}

# Reverse map: column -> KanbanCard.status (for move)
_COLUMN_TO_TASK_STATUS = {
    "proposed": "todo",
    "under_development": "in_progress",
    "review": "review",
    "approved": "done",
    "implementing": "in_progress",
    "done": "done",
}

# Primary deliverable codes (get "critical" priority, others get "high")
_PRIMARY_DELIVERABLE_CODES = {
    "DEL-PRELIM-001", "DEL-A-001", "DEL-B-001", "DEL-C-001",
    "DEL-D-001", "DEL-E-001", "DEL-F-001", "DEL-G-001",
    "DEL-H-001", "DEL-REQ-001",
}

# H-03: valid ADM phase codes, for normalizing whatever a source entity happens
# to store (wrong case, stale value, free text). Every projected card MUST end
# up with a code in this set, or phase_counts silently undercounts relative to
# column_counts (the divergence QA found: 27 vs 28 on the same card set).
_VALID_PHASE_CODES = {p["code"] for p in ADM_PHASES}


def normalize_phase_code(
    raw: Optional[str], valid_codes: Optional[Collection[str]] = None
) -> str:
    """Map any stored phase value onto a known ADM phase code, defaulting to 'A'."""
    allowed_codes = {
        str(code).strip().upper()
        for code in (valid_codes if valid_codes is not None else _VALID_PHASE_CODES)
        if code
    }
    if not raw:
        return "A"
    candidate = str(raw).strip().upper()
    if candidate in allowed_codes:
        return candidate
    return "A"


_normalize_phase_code = normalize_phase_code


class KanbanProjectionService:
    """Projects source entities into unified kanban card dicts."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    # -- Public API ----------------------------------------------------

    def get_cards(
        self,
        phase: Optional[str] = None,
        assignee: Optional[str] = None,
        assignee_id: Optional[int] = None,
        priority: Optional[str] = None,
        card_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return all projected cards with phase and column counts.

        Returns:
            {
                "cards": [...],
                "phase_counts": {"A": 3, "B": 2, ...},
                "column_counts": {"proposed": 5, ...}
            }
        """
        cards = []

        # Solutions
        if card_type is None or card_type == "solution":
            cards.extend(self._project_solutions(phase, assignee, assignee_id))

        # ADM Deliverables
        if card_type is None or card_type == "deliverable":
            cards.extend(self._project_deliverables(phase))

        # KanbanCards (manually-created governance tasks)
        if card_type is None or card_type == "task":
            cards.extend(self._project_kanban_cards(phase, assignee_id))

        # Sprint 3: additional entity types
        # if card_type is None or card_type == "arb":
        #     cards.extend(self._project_arb_items(phase))
        # if card_type is None or card_type == "wave":
        #     cards.extend(self._project_consolidation_waves(phase))
        # if card_type is None or card_type == "gap":
        #     cards.extend(self._project_gaps(phase))

        # Compute counts from ALL cards before filtering
        all_cards_for_counts = list(cards)

        # Apply priority filter
        if priority:
            cards = [c for c in cards if c.get("priority") == priority]
        phase_counts = {}
        for p in ADM_PHASES:
            phase_counts[p["code"]] = sum(
                1 for c in all_cards_for_counts if c.get("phase") == p["code"]
            )
        column_counts = {}
        for col in COLUMN_IDS:
            column_counts[col] = sum(
                1 for c in all_cards_for_counts if c.get("column") == col
            )

        return {
            "cards": cards,
            "phase_counts": phase_counts,
            "column_counts": column_counts,
        }

    def move_card(
        self, card_type: str, entity_id: int, to_column: str
    ) -> Dict[str, Any]:
        """
        Move a card to a new column by updating the source entity's status.

        Returns: {"success": True, "card": {...}} or {"success": False, "error": "..."}
        """
        if to_column not in COLUMN_IDS:
            return {"success": False, "error": f"Invalid column: {to_column}"}

        # WIP limit enforcement
        limit = WIP_LIMITS.get(to_column, 0)
        if limit > 0:
            current = self._count_column(to_column)
            if current >= limit:
                return {
                    "success": False,
                    "wip_exceeded": True,
                    "error": f"WIP limit reached for '{to_column}' ({current}/{limit}). Finish existing work before adding more.",
                    "limit": limit,
                    "current": current,
                    "column": to_column,
                }

        if card_type == "solution":
            return self._move_solution(entity_id, to_column)
        elif card_type == "deliverable":
            return self._move_deliverable(entity_id, to_column)
        elif card_type == "task":
            return self._move_kanban_card(entity_id, to_column)
        else:
            return {"success": False, "error": f"Unsupported card type: {card_type}"}

    def _count_column(self, column_id: str) -> int:
        """Count cards currently in a given column across all entity types."""
        from app.models.solution_models import Solution
        from app.models.adm_kanban import KanbanCard

        count = 0

        # Solutions: reverse map governance_status
        gov_statuses = [k for k, v in _SOLUTION_COLUMN_MAP.items() if v == column_id]
        deploy_statuses = [k for k, v in _DEPLOY_COLUMN_MAP.items() if v == column_id]
        if column_id == "approved":
            # approved with no deployment status mapping
            sol_count = Solution.query.filter(
                *self._board_solution_filters(),
                Solution.governance_status == "approved",
                ~Solution.deployment_status.in_(list(_DEPLOY_COLUMN_MAP.keys()))
                if deploy_statuses else db.true()
            ).count()
        else:
            sol_count = Solution.query.filter(
                *self._board_solution_filters(),
                Solution.governance_status.in_(gov_statuses)
            ).count()
            if deploy_statuses:
                sol_count += Solution.query.filter(
                    *self._board_solution_filters(),
                    Solution.governance_status == "approved",
                    Solution.deployment_status.in_(deploy_statuses)
                ).count()
        count += sol_count

        # KanbanCards: reverse map task status
        task_statuses = [k for k, v in _TASK_COLUMN_MAP.items() if v == column_id]
        if task_statuses:
            count += KanbanCard.query.filter(
                KanbanCard.status.in_(task_statuses)
            ).count()

        return count


    @staticmethod
    def _board_solution_filters():
        """Shared board-hygiene exclusions (UIQA-003): test-name signatures and
        contentless auto-named drafts never appear on the board — applied to BOTH
        the card projection and the column counts so they always agree."""
        from sqlalchemy import and_, or_

        from app.models.solution_models import Solution

        return (
            # AutoTest fixture-name exclusion removed; see
            # scripts/check_test_data_in_queries.py for why hiding test rows
            # from the product is the wrong half of the fix.
            ~Solution.name.like("ZZ %"),
            ~and_(
                Solution.name.like("Untitled Solution%"),
                or_(Solution.description.is_(None), Solution.description == ""),
            ),
        )

    def get_cycle_time_metrics(self) -> Dict[str, Any]:
        """
        Compute cycle time metrics from KanbanCard started_at/completed_at.

        Returns per-phase average cycle time, SLA status, and throughput.
        SLA thresholds: review=5d, under_development=14d (all others have no SLA).
        Batch-loads all cards in 2 queries to avoid N+1.
        """
        from datetime import datetime, timezone
        from app.models.adm_kanban import ADMPhase, KanbanCard

        SLA_DAYS = {
            "review": 5,
            "under_development": 14,
        }

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        phases = ADMPhase.query.order_by(ADMPhase.order).all()

        # Batch load all cards with started_at in one query each
        all_completed = KanbanCard.query.filter(  # model-safety-ok
            KanbanCard.started_at.isnot(None),
            KanbanCard.completed_at.isnot(None),
        ).all()

        all_in_progress = KanbanCard.query.filter(  # model-safety-ok
            KanbanCard.started_at.isnot(None),
            KanbanCard.completed_at.is_(None),
        ).all()

        # Group by phase_id
        completed_by_phase: dict = {}
        for card in all_completed:
            completed_by_phase.setdefault(card.adm_phase_id, []).append(card)

        in_progress_by_phase: dict = {}
        for card in all_in_progress:
            in_progress_by_phase.setdefault(card.adm_phase_id, []).append(card)

        phase_metrics = {}
        for phase in phases:
            completed_cards = completed_by_phase.get(phase.id, [])
            in_progress_cards = in_progress_by_phase.get(phase.id, [])

            cycle_times = []
            for card in completed_cards:
                delta = (card.completed_at - card.started_at).total_seconds() / 86400
                if delta >= 0:
                    cycle_times.append(delta)

            ages = []
            for card in in_progress_cards:
                ages.append((now - card.started_at).total_seconds() / 86400)

            avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None
            avg_age = round(sum(ages) / len(ages), 1) if ages else None

            phase_metrics[phase.code] = {
                "phase_code": phase.code,
                "phase_label": phase.name,
                "throughput": len(completed_cards),
                "in_progress_count": len(in_progress_cards),
                "avg_cycle_days": avg_cycle,
                "avg_age_days": avg_age,
            }

        # Per-column SLA status: group in-progress cards by column, check age vs SLA
        column_sla = {}
        for col_id, sla_days in SLA_DAYS.items():
            task_statuses = [k for k, v in _TASK_COLUMN_MAP.items() if v == col_id]
            if not task_statuses:
                continue
            col_cards = [c for c in all_in_progress if c.status in task_statuses]
            overdue_count = sum(
                1 for c in col_cards
                if (now - c.started_at).total_seconds() / 86400 > sla_days
            )
            column_sla[col_id] = {
                "sla_days": sla_days,
                "overdue_count": overdue_count,
                "status": "breach" if overdue_count > 0 else "ok",
            }

        return {
            "phase_metrics": phase_metrics,
            "column_sla": column_sla,
            "sla_thresholds": SLA_DAYS,
        }

    # -- Solution Projection -------------------------------------------

    def _project_solutions(
        self,
        phase: Optional[str] = None,
        assignee: Optional[str] = None,
        assignee_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Query Solution records and project to card dicts."""
        from app.models.solution_models import Solution

        # Board hygiene (UIQA-003): a kanban card for a contentless, auto-named
        # draft is noise — and E2E suites generate them continuously. Exclude
        # test-name signatures and empty "Untitled Solution" drafts from the
        # PROJECTION only (they stay visible in the solutions list, and reappear
        # here the moment they're renamed or given content).
        query = Solution.query.filter(*self._board_solution_filters())

        if phase and phase != "all":
            query = query.filter(Solution.adm_phase == phase)

        if assignee_id:
            query = query.filter(Solution.created_by_id == assignee_id)

        solutions = query.order_by(Solution.updated_at.desc()).all()
        cards = []
        for sol in solutions:
            try:
                cards.append(self._project_one_solution(sol))
            except Exception:
                self.logger.warning(
                    f"Failed to project solution {sol.id}", exc_info=True
                )
        return cards

    def _project_one_solution(self, sol) -> Dict[str, Any]:
        """Project a single Solution into a unified card dict."""
        # Column mapping
        column = self._solution_column(sol)

        # ADM phase completion
        adm_completed = []
        for p in "ABCDEFGH":
            if getattr(sol, f"adm_phase_{p.lower()}_completed_at", None):
                adm_completed.append(p)

        # Readiness score (arb_readiness may be list of dicts or list of strings)
        readiness = getattr(sol, "arb_readiness", []) or []
        readiness_score = sum(
            1 for r in readiness
            if isinstance(r, dict) and r.get("passed")
        )
        readiness_total = len(readiness)

        # ArchiMate artifact counts + phase gate validation
        artifacts = {}
        artifact_total = 0
        phase_gate = {"valid": True, "errors": [], "warnings": []}
        try:
            from app.models.solution_models import SolutionArchiMateElement
            rows = (
                db.session.query(
                    SolutionArchiMateElement.layer_type,
                    db.func.count(SolutionArchiMateElement.id),
                )
                .filter_by(solution_id=sol.id)
                .group_by(SolutionArchiMateElement.layer_type)
                .all()
            )
            for layer, count in rows:
                artifacts[layer] = count
                artifact_total += count
            phase_gate = sol.validate_phase_gate(sol.adm_phase or "A")
        except Exception:
            self.logger.debug(f"Could not load artifacts for solution {sol.id}", exc_info=True)

        # App count — try junction table first, then JSON field
        app_count = 0
        try:
            from app.models.solution_models import SolutionApplication
            app_count = db.session.query(db.func.count(SolutionApplication.id)).filter_by(
                solution_id=sol.id
            ).scalar() or 0
        except Exception:
            apps = getattr(sol, "in_scope_applications", None) or []
            app_count = len(apps) if isinstance(apps, list) else 0

        # Due date
        due = getattr(sol, "target_completion_date", None)
        due_str = due.isoformat() if due else None

        # Days in column (based on updated_at)
        days_in_column = None
        try:
            from datetime import datetime, timezone
            updated = getattr(sol, "updated_at", None)
            if updated:
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_in_column = (now - updated).days
        except Exception as exc:
            self.logger.debug("Could not compute days_in_column for solution %s: %s", sol.id, exc)

        # Link URL
        try:
            link_url = url_for(
                "solution_design.view_solution", solution_id=sol.id
            )
        except Exception:
            link_url = f"/solutions/{sol.id}"

        return {
            "id": f"solution:{sol.id}",
            "card_type": "solution",
            "entity_id": sol.id,
            "title": sol.name or "Untitled Solution",
            "subtitle": (sol.description or "")[:120],
            "phase": normalize_phase_code(sol.adm_phase),
            "column": column,
            "priority": getattr(sol, "complexity_level", "medium") or "medium",
            "owner": sol.solution_owner,
            "owner_id": getattr(sol, "created_by_id", None),
            "due_date": due_str,
            "blocker_count": 0,  # Sprint 2: auto-detected blockers
            "blockers": [],
            "meta": {
                "readiness_score": readiness_score,
                "readiness_total": readiness_total,
                "app_count": app_count,
                "budget_range": self._format_cost(
                    getattr(sol, "estimated_cost", None)
                ),
                "adm_completed": adm_completed,
                "adm_current": sol.adm_phase or "A",
                "complexity": getattr(sol, "complexity_level", None),
                "solution_type": getattr(sol, "solution_type", None),
                "governance_status": sol.governance_status,
                "deployment_status": getattr(sol, "deployment_status", None),
                "artifacts": artifacts,
                "artifact_total": artifact_total,
                "phase_gate": phase_gate,
                "days_in_column": days_in_column,
            },
            "link_url": link_url,
            "link_label": "Open Solution",
        }

    def _solution_column(self, sol) -> str:
        """Map Solution governance_status + deployment_status to column."""
        gov = getattr(sol, "governance_status", "draft") or "draft"

        # If approved, check deployment_status for more granular mapping
        if gov == "approved":
            deploy = getattr(sol, "deployment_status", None)
            if deploy and deploy in _DEPLOY_COLUMN_MAP:
                return _DEPLOY_COLUMN_MAP[deploy]
            return "approved"

        return _SOLUTION_COLUMN_MAP.get(gov, "proposed")

    # -- Solution Move -------------------------------------------------

    def _move_solution(self, entity_id: int, to_column: str) -> Dict[str, Any]:
        """Update a Solution's governance_status based on target column."""
        from app.models.solution_models import Solution

        sol = db.session.get(Solution, entity_id)
        if not sol:
            return {"success": False, "error": "Solution not found"}

        # Reverse map: column -> governance_status
        col_to_gov = {
            "proposed": "draft",
            "under_development": "in_progress",
            "review": "arb_review",
            "approved": "approved",
            "implementing": "approved",
            "done": "approved",
        }
        col_to_deploy = {
            "implementing": "development",
            "done": "production",
        }

        new_gov = col_to_gov.get(to_column)
        if new_gov is None:
            return {"success": False, "error": f"Cannot map column {to_column}"}

        sol.governance_status = new_gov

        if to_column in col_to_deploy:
            sol.deployment_status = col_to_deploy[to_column]
        elif to_column in ("proposed", "under_development", "review", "approved"):
            # Clear deployment status when moving back
            if sol.deployment_status:
                sol.deployment_status = None

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            self.logger.error(
                f"Failed to move solution {entity_id}", exc_info=True
            )
            return {"success": False, "error": "Database error"}

        return {"success": True, "card": self._project_one_solution(sol)}

    # -- Deliverable Projection ----------------------------------------

    def _project_deliverables(
        self, phase: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Query ADMDeliverable records and project to card dicts."""
        from app.models.adm_deliverable import ADMDeliverable

        query = ADMDeliverable.query

        if phase and phase != "all":
            query = query.filter(ADMDeliverable.phase == phase)

        phase_order = {item["code"]: item["order"] for item in ADM_PHASES}
        deliverables = query.all()
        deliverables.sort(
            key=lambda deliv: (
                phase_order.get(getattr(deliv, "phase", None), 999),
                getattr(deliv, "id", 0),
            )
        )
        cards = []
        for deliv in deliverables:
            try:
                cards.append(self._project_one_deliverable(deliv))
            except Exception:
                self.logger.warning(
                    f"Failed to project deliverable {deliv.id}", exc_info=True
                )
        return cards

    def _project_one_deliverable(self, deliv) -> Dict[str, Any]:
        """Project a single ADMDeliverable into a unified card dict."""
        phase_code = normalize_phase_code(getattr(deliv, "phase", None))
        doc_status = getattr(deliv, "document_status", None) or "draft"
        column = _DELIVERABLE_COLUMN_MAP.get(doc_status, "proposed")

        deliverable_code = getattr(deliv, "deliverable_code", None)
        is_primary = deliverable_code in _PRIMARY_DELIVERABLE_CODES
        priority = "critical" if is_primary else "high"

        elements = getattr(deliv, "archimate_elements", None) or []

        # Build a readable type label from deliverable_type
        type_label = (getattr(deliv, "deliverable_type", None) or "other").replace(
            "_", " "
        ).title()

        return {
            "id": f"deliverable:{deliv.id}",
            "card_type": "deliverable",
            "entity_id": deliv.id,
            "title": deliv.name or "Untitled Deliverable",
            "subtitle": (deliv.description or "")[:120],
            "phase": phase_code,
            "column": column,
            "priority": priority,
            "owner": None,
            "owner_id": getattr(deliv, "created_by_id", None),
            "due_date": None,
            "blocker_count": 0,
            "blockers": [],
            "meta": {
                "deliverable_type_label": type_label,
                "archimate_viewpoint": getattr(deliv, "archimate_viewpoint", None),
                "archimate_elements": elements,
                "element_count": len(elements),
                "document_status": doc_status,
                "document_version": getattr(deliv, "document_version", None) or "0.1",
            },
            "link_url": None,
            "link_label": "Deliverable",
        }

    # -- Deliverable Move ----------------------------------------------

    def _move_deliverable(self, entity_id: int, to_column: str) -> Dict[str, Any]:
        """Update an ADMDeliverable's document_status based on target column."""
        from app.models.adm_deliverable import ADMDeliverable

        deliv = db.session.get(ADMDeliverable, entity_id)
        if not deliv:
            return {"success": False, "error": "Deliverable not found"}

        new_status = _COLUMN_TO_DOC_STATUS.get(to_column)
        if new_status is None:
            return {"success": False, "error": f"Cannot map column {to_column}"}

        if not hasattr(deliv, "document_status"):
            return {
                "success": False,
                "error": "Deliverable status updates are unavailable for the current deliverable model",
            }

        deliv.document_status = new_status

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            self.logger.error(
                f"Failed to move deliverable {entity_id}", exc_info=True
            )
            return {"success": False, "error": "Database error"}

        return {"success": True, "card": self._project_one_deliverable(deliv)}

    # -- KanbanCard Projection -----------------------------------------

    def _project_kanban_cards(
        self,
        phase: Optional[str] = None,
        assignee_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Query KanbanCard rows and project to unified card dicts."""
        from app.models.adm_kanban import KanbanCard, ADMPhase

        query = KanbanCard.query.join(ADMPhase, KanbanCard.adm_phase_id == ADMPhase.id)

        if phase and phase != "all":
            query = query.filter(ADMPhase.code == phase)

        if assignee_id:
            query = query.filter(KanbanCard.assigned_to_id == assignee_id)

        cards_db = query.order_by(KanbanCard.updated_at.desc()).all()

        # Pre-build a status lookup to resolve dependencies without extra queries
        all_ids = {c.id for c in cards_db}
        # Collect any dependency IDs not already in our result set
        extra_ids: set = set()
        for card in cards_db:
            for dep_id in (card.depends_on or []):
                if dep_id not in all_ids:
                    extra_ids.add(dep_id)
        extra_cards: dict = {}
        if extra_ids:
            for c in KanbanCard.query.filter(KanbanCard.id.in_(extra_ids)).all():  # model-safety-ok
                extra_cards[c.id] = c.status

        status_by_id: dict = {c.id: c.status for c in cards_db}
        status_by_id.update(extra_cards)

        cards = []
        for card in cards_db:
            try:
                cards.append(self._project_one_kanban_card(card, status_by_id))
            except Exception:
                self.logger.warning(
                    f"Failed to project KanbanCard {card.id}", exc_info=True
                )
        return cards

    def _resolve_user_label(self, user_id, org_id=None) -> str:
        """Return display name for a user ID stored in card.assignee.

        ``card.assignee`` is set from the request, so the user is resolved only
        inside the card's own organisation: another organisation's user is
        never named, and a missing organisation names nobody.
        """
        if not user_id:
            return ''
        try:
            from app.utils.tenant_users import user_in_org
            u = user_in_org(user_id, org_id)
            if u:
                return ' '.join(filter(None, [u.first_name, u.last_name])).strip() or u.email
        except Exception as e:
            logger.debug("Could not resolve user display name for id=%s: %s", user_id, e)
        return ''

    def _project_one_kanban_card(self, card, status_by_id: Optional[Dict] = None) -> Dict[str, Any]:
        """Project a single KanbanCard into a unified card dict."""
        phase_code = normalize_phase_code(card.adm_phase.code if card.adm_phase else None)
        column = _TASK_COLUMN_MAP.get(card.status or "todo", "proposed")

        owner = None
        if card.assigned_to_id:
            from app.utils.tenant_users import user_in_org
            u = user_in_org(card.assigned_to_id, card.organization_id)
            if u:
                owner = ' '.join(filter(None, [u.first_name, u.last_name])).strip() or u.email

        # Blocker detection: count depends_on entries where the dependency is not done
        blockers = []
        depends_on = card.depends_on or []
        if depends_on and status_by_id:
            for dep_id in depends_on:
                dep_status = status_by_id.get(dep_id)
                if dep_status and dep_status != "done":
                    blockers.append({"id": dep_id, "status": dep_status})

        return {
            "id": f"task:{card.id}",
            "card_type": "task",
            "entity_id": card.id,
            "title": card.title,
            "subtitle": (card.description or "")[:120],
            "description": card.description or "",
            "phase": phase_code,
            "arch_domain": card.arch_domain or "Business",
            "column": column,
            "priority": card.priority or "medium",
            "owner": owner,
            "owner_id": card.assigned_to_id,
            "due_date": None,
            "blocker_count": len(blockers),
            "blockers": blockers,
            "meta": {
                "task_type": card.card_type or "requirement",
                "status": card.status or "todo",
                "board_id": card.board_id,
                "workflow_instance_id": card.workflow_instance_id,
            },
            "link_url": None,
            "link_label": "Task",
            "requirement_ids": card.requirement_ids or [],
            "goal_ids": card.goal_ids or [],
            "driver_ids": card.driver_ids or [],
            "principle_ids": card.principle_ids or [],
            "issue_type": card.issue_type or 'Task',
            "assignee": card.assignee,
            "assignee_label": self._resolve_user_label(card.assignee, card.organization_id),
            "story_points": card.story_points,
            "labels": card.labels or [],
            "acceptance_criteria": card.acceptance_criteria,
            "arch_layer": card.arch_layer,
            "progress_pct": card.progress_pct or 0,
            "target_start_date": card.target_start_date.strftime("%Y-%m-%d") if card.target_start_date else None,
            "target_end_date": card.target_end_date.strftime("%Y-%m-%d") if card.target_end_date else None,
            "togaf_deliverable": card.togaf_deliverable,
            "arch_element_type": card.arch_element_type,
            "jira_issue_key": card.jira_issue_key,
            "jira_push_status": card.jira_push_status,
            "requires_arb_signoff": card.requires_arb_signoff or False,
            "depends_on": card.depends_on or [],
            "blocks": [],
        }

    # -- KanbanCard Move -----------------------------------------------

    def _move_kanban_card(self, entity_id: int, to_column: str) -> Dict[str, Any]:
        """Update a KanbanCard's status based on target column."""
        from app.models.adm_kanban import KanbanCard

        card = db.session.get(KanbanCard, entity_id)
        if not card:
            return {"success": False, "error": "Task not found"}

        new_status = _COLUMN_TO_TASK_STATUS.get(to_column)
        if new_status is None:
            return {"success": False, "error": f"Cannot map column {to_column}"}

        card.status = new_status

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            self.logger.error(
                f"Failed to move KanbanCard {entity_id}", exc_info=True
            )
            return {"success": False, "error": "Database error"}

        return {"success": True, "card": self._project_one_kanban_card(card)}

    # -- Helpers -------------------------------------------------------

    @staticmethod
    def _format_cost(cost) -> str:
        """Format a numeric cost as a display string."""
        if cost is None:
            return ""
        try:
            cost_f = float(cost)
            if cost_f >= 1_000_000:
                return f"\u00a3{cost_f / 1_000_000:.1f}M"
            if cost_f >= 1_000:
                return f"\u00a3{cost_f / 1_000:.0f}k"
            return f"\u00a3{cost_f:,.0f}"
        except (ValueError, TypeError):
            return ""
