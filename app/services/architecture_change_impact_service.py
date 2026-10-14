"""CM-002: Architecture Change Impact Service — Phase H Change Management.

Assesses the blast radius of a change request by traversing ArchiMate
relationships (up to depth 2) to identify downstream application dependencies.
Calls TechnologyStackAuditService for technology context.

The walk itself delegates to the one canonical impact engine
(``IntelligenceQueryService.cross_layer_impact``) rather than running its
own BFS -- a separate traversal is exactly the "second impact engine"
PR 297 defect 4 flagged. Multi-seed reachability (a change request can
scope several applications at once) is the union of each seed's own
downstream-reachable set within the same hop budget, which is what the
old multi-source BFS computed too.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from app.models.application_portfolio import ApplicationComponent
from app.services.technology_stack_audit_service import TechnologyStackAuditService
from app.utils.route_guards import load_entity

logger = logging.getLogger(__name__)


def _blast_radius_tier(impact_radius: int) -> str:
    """Classify impact radius into a blast-radius tier.

    Tiers follow enterprise programme governance conventions:
      > 50 apps → "enterprise"   (cross-organisational impact)
      > 20 apps → "program"      (programme-level impact)
      >  5 apps → "project"      (project-level impact)
      else      → "local"        (contained / minimal impact)
    """
    if impact_radius > 50:
        return "enterprise"
    if impact_radius > 20:
        return "program"
    if impact_radius > 5:
        return "project"
    return "local"


class ArchitectureChangeImpactService:
    """Computes change impact / blast-radius for a ChangeRequest.

    Traverses ArchiMateRelationship edges (BFS, up to depth 2) from each
    application element in the change scope.  Returns a structured impact
    assessment dict aligned with the Phase H Change Management workflow.
    """

    MAX_HOPS = 2

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def assess_change_impact(self, change_request_id: int) -> Dict[str, Any]:
        """Return an impact assessment for the given change request.

        Parameters
        ----------
        change_request_id : int
            Primary key of the ChangeRequest row to assess.

        Returns
        -------
        dict with keys:
          change_request_id   : int
          scope_app_count     : int — apps explicitly in scope
          impact_radius       : int — unique downstream apps reached via traversal
          blast_radius_tier   : "enterprise" | "program" | "project" | "local"
          downstream_apps     : list[str] — names of downstream apps
          compliance_risk     : bool — True if any scoped app has compliance_score < 60
        On error:
          {"error": "not_found", "change_request_id": ...}
          {"error": "import_error", "change_request_id": ...}
        """
        # -------------------------------------------------------------- #
        # 1. Load ChangeRequest (guard ImportError for missing model)     #
        # -------------------------------------------------------------- #
        try:
            from app.models.architecture_review_board import ChangeRequest
        except ImportError:
            return {"error": "import_error", "change_request_id": change_request_id}

        # Not db.session.get()/Model.query.get() -- SQLAlchemy's primary-key
        # lookup path does not reliably run through do_orm_execute, so the
        # tenant with_loader_criteria filter installed there never applies
        # to it (see load_entity's own docstring). This was the actual
        # cross-org leak in defects 2/3: ChangeRequest now has
        # organization_id, but db.session.get() ignored it.
        change_request = load_entity(ChangeRequest, change_request_id)
        if change_request is None:
            return {"error": "not_found", "change_request_id": change_request_id}

        # -------------------------------------------------------------- #
        # 2. Derive scope_app_ids from the change request                 #
        # -------------------------------------------------------------- #
        scope_app_ids: List[int] = self._extract_scope_app_ids(change_request)

        # -------------------------------------------------------------- #
        # 3. Load ApplicationComponent rows for in-scope apps             #
        # -------------------------------------------------------------- #
        apps_in_scope: List[ApplicationComponent] = []
        if scope_app_ids:
            apps_in_scope = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(scope_app_ids)
            ).all()

        scope_app_count = len(apps_in_scope)

        # -------------------------------------------------------------- #
        # 4. Downstream walk via the canonical impact engine (≤ MAX_HOPS) #
        # -------------------------------------------------------------- #
        scope_element_ids: Set[int] = {
            a.archimate_element_id
            for a in apps_in_scope
            if a.archimate_element_id is not None
        }

        downstream_element_ids: Set[int] = self._downstream_reachable(scope_element_ids)

        # Map downstream element IDs → ApplicationComponent rows
        downstream_apps: List[ApplicationComponent] = []
        if downstream_element_ids:
            downstream_apps = ApplicationComponent.query.filter(
                ApplicationComponent.archimate_element_id.in_(downstream_element_ids)
            ).all()

        impact_radius = len({a.id for a in downstream_apps})
        downstream_app_names = [a.name for a in downstream_apps]

        # -------------------------------------------------------------- #
        # 5. Technology context via TechnologyStackAuditService           #
        # -------------------------------------------------------------- #
        TechnologyStackAuditService().audit_portfolio()

        # -------------------------------------------------------------- #
        # 6. Blast-radius tier                                            #
        # -------------------------------------------------------------- #
        tier = _blast_radius_tier(impact_radius)

        # -------------------------------------------------------------- #
        # 7. Compliance risk — true if any scoped app has low score       #
        # -------------------------------------------------------------- #
        compliance_risk: bool = self._has_compliance_risk(apps_in_scope)

        return {
            "change_request_id": change_request_id,
            "scope_app_count": scope_app_count,
            "impact_radius": impact_radius,
            "blast_radius_tier": tier,
            "downstream_apps": downstream_app_names,
            "compliance_risk": compliance_risk,
        }

    # ------------------------------------------------------------------ #
    # Pending approval (the one approval queue)                            #
    # ------------------------------------------------------------------ #

    # How many affected elements the preview names per changed element. The
    # answer states the true total and says so when it shows fewer, so a large
    # estate is never silently truncated and the per-row owner reads stay bounded.
    APPROVAL_PREVIEW_LIMIT = 25

    def assess_approval_impact(self, approval, organization_id: Optional[int]) -> Dict[str, Any]:
        """What approving this pending change would affect, in plain terms.

        ``approval`` is an ``AIChatCRUDApproval``. The walk is the canonical
        engine's (``IntelligenceQueryService.cross_layer_impact``, owners
        resolved by the engine itself); nothing here traverses the model.

        Every state is stated, never a blank: ``computed`` (something depends
        on it), ``no_dependents`` (it is in the model and nothing depends on
        it), ``not_in_model`` (a new record, or a kind of change that is not
        linked to the architecture model) and ``unavailable`` (no active
        organisation). The caller names the organisation explicitly: the
        automatic tenant filter is off when it is unset, so an unset
        organisation answers ``unavailable`` and reads nothing.
        """
        import json

        base: Dict[str, Any] = {
            "approval_id": approval.id,
            "state": "unavailable",
            "message": "Impact cannot be shown because no organisation is active.",
            "subjects": [],
            "subject_owners": [],
            "affected": {"services": [], "interfaces": [], "applications": [], "other": []},
            "total": 0,
            "shown": 0,
            "truncated": False,
        }
        from app.middleware.tenant_context import current_org_id

        # The caller's organisation, the approval's and the active one must be
        # the same; anything else reads nothing.
        if (
            organization_id is None
            or approval.organization_id != organization_id
            or current_org_id() != organization_id
        ):
            return base

        try:
            payload = json.loads(approval.operation_payload or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        targets = resolve_approval_targets(
            approval.operation_type, approval.entity_type, approval.entity_id, payload, organization_id
        )
        if not targets:
            base["state"] = "not_in_model"
            base["message"] = _not_in_model_message(approval)
            return base

        base["subjects"] = [t["name"] for t in targets]
        base["subject_owners"] = _owner_names(target_owner_rows(targets, organization_id), organization_id)

        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        affected = base["affected"]
        seen: Set[int] = {t["element_id"] for t in targets}
        total = 0
        truncated = False
        for target in targets:
            result = IntelligenceQueryService.cross_layer_impact(
                target["element_id"],
                include_derived=False,
                max_depth=self.MAX_HOPS,
                direction="downstream",
                with_owner=True,
                page_size=self.APPROVAL_PREVIEW_LIMIT,
            )
            elements = result.get("elements") or {}
            total += int(result.get("total") or 0)
            truncated = truncated or result.get("next_cursor") is not None
            for row in result.get("rows") or []:
                eid = row["element_id"]
                if eid in seen:
                    continue
                seen.add(eid)
                element = elements.get(str(eid)) or {}
                bucket = {
                    "ApplicationService": "services",
                    "ApplicationInterface": "interfaces",
                    "ApplicationComponent": "applications",
                }.get(element.get("type"), "other")
                affected[bucket].append(
                    {
                        "element_id": eid,
                        "name": element.get("name") or "Unnamed element",
                        "type": element.get("type"),
                        "owners": [
                            {"name": o.get("name") or "—", "role": o.get("role")}
                            for o in (row.get("owners") or [])
                        ],
                        "owner_unit": (row.get("owner") or {}).get("name"),
                    }
                )

        shown = sum(len(v) for v in affected.values())
        base["total"] = max(total, shown)
        base["shown"] = shown
        base["truncated"] = truncated
        if shown == 0:
            base["state"] = "no_dependents"
            base["message"] = (
                "Nothing in the architecture model depends on "
                + _join_names(base["subjects"])
                + ", so approving this affects nothing else."
            )
        else:
            base["state"] = "computed"
            base["message"] = _computed_message(base)
        return base

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _extract_scope_app_ids(self, change_request) -> List[int]:
        """Extract scope application IDs from the change request payload.

        Checks the following attributes in priority order:
        impact_assessment → new_values → old_values
        within each, looks for keys: scope_app_ids, app_ids, scope.
        """
        for attr in ("impact_assessment", "new_values", "old_values"):
            payload = getattr(change_request, attr, None)
            if isinstance(payload, dict):
                ids = (
                    payload.get("scope_app_ids")
                    or payload.get("app_ids")
                    or payload.get("scope")
                    or []
                )
                if ids and isinstance(ids, list):
                    return [int(i) for i in ids if i is not None]
        return []

    def _downstream_reachable(self, seed_element_ids: Set[int]) -> Set[int]:
        """Every element reachable downstream from any seed within MAX_HOPS.

        Delegates to IntelligenceQueryService.cross_layer_impact, the one
        canonical (tenant-fenced) walk -- one call per seed, union of the
        results. Equivalent to the old multi-source BFS: a node reachable
        within k hops of the seed SET is reachable within k hops of at
        least one individual seed.
        """
        if not seed_element_ids:
            return set()

        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        reachable: Set[int] = set()
        for seed_id in seed_element_ids:
            result = IntelligenceQueryService.cross_layer_impact(
                seed_id,
                include_derived=False,
                max_depth=self.MAX_HOPS,
                direction="downstream",
                with_owner=False,
            )
            for row in result.get("rows") or []:
                reachable.add(row["element_id"])

        reachable -= seed_element_ids
        return reachable

    def _has_compliance_risk(self, apps: List[ApplicationComponent]) -> bool:
        """Return True if any app in the list has user_satisfaction_score < 60.

        user_satisfaction_score is used as a proxy for compliance_score since
        ApplicationComponent does not have a dedicated compliance_score column.
        Returns False when no apps are provided or no scores are available.
        """
        for app in apps:
            score = app.user_satisfaction_score
            if score is not None and score < 60:
                return True
        return False


# ---------------------------------------------------------------------- #
# Approval targets: which architecture-model elements a pending change   #
# touches, and who owns them                                            #
# ---------------------------------------------------------------------- #

# The projection of BusinessCapability rows into UnifiedCapability records
# (app/commands/project_capabilities.py: SOURCE_TABLE).
_BUSINESS_CAPABILITY_SOURCE = "business_capability"


def _as_id(value: Any) -> Optional[int]:
    """A posted id as an int, or None (bools, blanks and junk are no id)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_approval_targets(
    operation_type: str,
    entity_type: str,
    entity_id: Optional[int],
    payload: Dict[str, Any],
    organization_id: Optional[int],
) -> List[Dict[str, Any]]:
    """The model elements an existing-thing change touches, in two reads.

    Three id spaces meet here and must not be mixed: an approval's
    ``application`` id is an ``ApplicationComponent`` id, its ``capability`` id
    is a ``BusinessCapability`` id (found on the projected ``UnifiedCapability``
    by provenance), and the capability-mapping payload names a
    ``UnifiedCapability`` id directly. A new record (create) has no dependents
    yet and resolves to nothing, as does any kind of change that is not linked
    to the model.

    Returns ``[{"kind", "element_id", "owner_key", "name"}]`` for the targets
    that are in ``organization_id`` and carry an architecture element. An unset
    organisation returns nothing.
    """
    if organization_id is None:
        return []

    app_ids: List[Optional[int]] = []
    business_capability_ids: List[Optional[int]] = []
    unified_capability_ids: List[Optional[int]] = []

    if operation_type in ("update", "delete") and entity_type == "application":
        app_ids.append(_as_id(entity_id))
    elif operation_type in ("update", "delete") and entity_type == "capability":
        business_capability_ids.append(_as_id(entity_id))
    elif operation_type == "link" and entity_type == "application_capability_mapping":
        app_ids.append(_as_id(payload.get("application_id")))
        business_capability_ids.append(_as_id(payload.get("capability_id")))
    elif operation_type == "create" and entity_type == "capability_mapping":
        app_ids.append(_as_id(payload.get("application_component_id")))
        unified_capability_ids.append(_as_id(payload.get("unified_capability_id")))

    app_list = [i for i in app_ids if i is not None]
    bc_list = [i for i in business_capability_ids if i is not None]
    uc_list = [i for i in unified_capability_ids if i is not None]
    if not (app_list or bc_list or uc_list):
        return []

    from app import db

    targets: List[Dict[str, Any]] = []

    if app_list:
        for comp in db.session.execute(
            db.select(ApplicationComponent)
            .where(ApplicationComponent.id.in_(app_list))
            .where(ApplicationComponent.organization_id == organization_id)
            .where(ApplicationComponent.deleted_at.is_(None))
            .order_by(ApplicationComponent.id)
        ).scalars():
            if comp.archimate_element_id is not None:
                targets.append(
                    {
                        "kind": "application",
                        "element_id": comp.archimate_element_id,
                        "owner_key": comp.id,
                        "name": comp.name,
                    }
                )

    if bc_list or uc_list:
        from app.models.unified_capability import UnifiedCapability

        match = []
        if bc_list:
            match.append(
                db.and_(
                    UnifiedCapability.source_table == _BUSINESS_CAPABILITY_SOURCE,
                    UnifiedCapability.source_id.in_([str(i) for i in bc_list]),
                )
            )
        if uc_list:
            match.append(UnifiedCapability.id.in_(uc_list))
        for cap in db.session.execute(
            db.select(UnifiedCapability)
            .where(UnifiedCapability.organization_id == organization_id)
            .where(db.or_(*match))
            .order_by(UnifiedCapability.id)
        ).scalars():
            if cap.archimate_element_id is not None:
                targets.append(
                    {
                        "kind": "capability",
                        "element_id": cap.archimate_element_id,
                        "owner_key": cap.id,
                        "name": cap.name,
                    }
                )
    return targets


def target_owner_rows(targets: List[Dict[str, Any]], organization_id: int) -> List[Any]:
    """The ``ApplicationOwner`` rows of the targets, in at most two reads.

    The one ownership store, through its own batch readers: applications by
    ``application_id``, capabilities by ``element_type='capability'`` and the
    ``UnifiedCapability`` id (never the element id or the source id).
    """
    from app.models.application_owner import ApplicationOwner

    app_keys = [t["owner_key"] for t in targets if t["kind"] == "application"]
    cap_keys = [t["owner_key"] for t in targets if t["kind"] == "capability"]
    rows: List[Any] = []
    if app_keys:
        for owner_rows in ApplicationOwner.get_owners_for_applications_batch(app_keys, organization_id).values():
            rows.extend(owner_rows)
    if cap_keys:
        for owner_rows in ApplicationOwner.get_owners_for_elements_batch(
            "capability", cap_keys, organization_id
        ).values():
            rows.extend(owner_rows)
    return rows


def _owner_names(owner_rows: List[Any], organization_id: int) -> List[Dict[str, Any]]:
    from app.models.application_owner import ApplicationOwner

    return [
        {"name": r["user_name"], "role": r["ownership_type_label"]}
        for r in ApplicationOwner.display_rows_for_owner_rows(owner_rows, organization_id)
    ]


def _join_names(names: List[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return "this item"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _not_in_model_message(approval) -> str:
    if approval.operation_type == "create":
        return "This creates something new, so nothing in the architecture model depends on it yet."
    return (
        "This kind of change is not linked to the architecture model, "
        "so there is no dependency picture to show for it."
    )


def _computed_message(answer: Dict[str, Any]) -> str:
    affected = answer["affected"]
    parts = []
    for key, singular, plural in (
        ("services", "service", "services"),
        ("interfaces", "interface", "interfaces"),
        ("applications", "application", "applications"),
        ("other", "other element", "other elements"),
    ):
        n = len(affected[key])
        if n:
            parts.append(f"{n} {singular if n == 1 else plural}")
    sentence = (
        f"Approving this changes {_join_names(answer['subjects'])}, which affects "
        + _join_names(parts)
        + "."
    )
    if answer["truncated"]:
        sentence += f" Showing the first {answer['shown']}."
    return sentence
