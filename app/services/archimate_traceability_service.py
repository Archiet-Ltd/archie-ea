"""ArchiMate Cross-Layer Traceability Service.

Traverses ArchiMate relationships to build upward/downward traceability chains
across Strategy, Business, Application, and Technology layers.

SA-002: get_traceability_chain() provides the 8-layer chain view.
"""

import logging

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app import db

logger = logging.getLogger(__name__)

TRACEABILITY_RELATIONSHIP_TYPES = [
    'RealizationRelationship', 'ServingRelationship', 'AssignmentRelationship',
    'CompositionRelationship', 'AggregationRelationship', 'AssociationRelationship',
    'AccessRelationship', 'InfluenceRelationship',
    # Bare capitalized variants (seed data without "Relationship" suffix)
    'Association', 'Realization', 'Serving', 'Influence', 'Composition',
    'Assignment', 'Access', 'Aggregation', 'Flow',
    # Lowercase variants (older seed data)
    'realization', 'serving', 'assignment', 'composition', 'aggregation',
    'association', 'access', 'influence', 'flow',
]

# Layer hierarchy — lower index = higher in the stack (Motivation is "highest")
_LAYER_RANK = {
    'motivation': 0,
    'strategy': 1,
    'business': 2,
    'application': 3,
    'technology': 4,
    'physical': 5,
    'data': 5,
    'implementation': 6,
}


class ArchiMateTraceabilityService:

    def get_element_chain(self, element_id: int, max_hops: int = 4) -> dict:
        """Traverse relationships from an element up/down the layer stack.

        Traversal follows relationships in BOTH directions (source <-> target);
        results are classified into 'upward' (higher-ranked layers) and
        'downward' (lower-ranked layers) based on layer position in the
        ArchiMate hierarchy, not on relationship directionality.
        """
        visited: set = set()
        chains = {'upward': [], 'downward': [], 'root': None}

        root = db.session.get(ArchiMateElement, element_id)
        if not root:
            return chains
        root_layer = (root.layer or '').lower()
        root_rank = _LAYER_RANK.get(root_layer, 3)
        chains['root'] = {
            'id': root.id, 'name': root.name,
            'layer': (root.layer or '').title(), 'type': root.type
        }

        all_connected: list = []
        self._traverse_all(element_id, all_connected, visited, max_hops, 0)

        for entry in all_connected:
            el_rank = _LAYER_RANK.get((entry.get('layer') or '').lower(), 3)
            if el_rank <= root_rank:
                chains['upward'].append(entry)
            else:
                chains['downward'].append(entry)

        return chains

    def _traverse_all(self, element_id, results, visited, max_hops, current_hop):
        """Follow ALL relationships (both source and target) regardless of direction."""
        if current_hop >= max_hops or element_id in visited:
            return
        visited.add(element_id)

        rels = ArchiMateRelationship.query.filter(
            db.or_(
                ArchiMateRelationship.source_id == element_id,
                ArchiMateRelationship.target_id == element_id,
            ),
            ArchiMateRelationship.type.in_(TRACEABILITY_RELATIONSHIP_TYPES)
        ).all()

        for rel in rels:
            next_id = rel.target_id if rel.source_id == element_id else rel.source_id
            if next_id in visited:
                continue
            el = db.session.get(ArchiMateElement, next_id)
            if not el:
                continue
            access_modifier = None
            if rel.type in ('AccessRelationship', 'Access', 'access'):
                access_modifier = getattr(rel, 'modifier', None)
            entry = {
                'id': el.id, 'name': el.name, 'layer': (el.layer or '').title(),
                'type': el.type, 'hop': current_hop + 1,
                'relationship_type': rel.type,
                'access_modifier': access_modifier,
                'plateau': getattr(el, 'togaf_plateau', None),
            }
            results.append(entry)
            self._traverse_all(next_id, results, visited, max_hops, current_hop + 1)

    def get_available_pivot_types(self) -> list:
        """Return deduplicated (layer, type, count) dicts for pivot type dropdown.

        Normalises layer to title-case before deduplication so seeds that stored
        lowercase layer values ('application') and later records using title-case
        ('Application') collapse into a single entry per type.

        Returns a list of dicts with 'type', 'layer', and 'count' keys,
        sorted by layer then type.
        """
        from sqlalchemy import func
        rows = (
            db.session.query(
                ArchiMateElement.layer,
                ArchiMateElement.type,
                func.count().label("cnt"),
            )
            .filter(ArchiMateElement.type.isnot(None), ArchiMateElement.layer.isnot(None))
            .group_by(ArchiMateElement.layer, ArchiMateElement.type)
            .order_by(ArchiMateElement.layer, ArchiMateElement.type)
            .all()
        )

        seen: set = set()
        result: list = []
        for row in rows:
            layer_title = (row[0] or "").title()
            el_type = row[1] or ""
            count = int(row[2]) if row[2] else 0
            key = (layer_title, el_type)
            if key not in seen:
                seen.add(key)
                result.append({"type": el_type, "layer": layer_title, "count": count})
            else:
                # Merge counts for same (layer_title, el_type) from case variants
                for item in result:
                    if item["type"] == el_type and item["layer"] == layer_title:
                        item["count"] += count
                        break
        return result

    def get_full_matrix(self, pivot_type='ApplicationComponent', pivot_layer=None, plateau=None,
                        limit=50, offset=0, search=None, scope=None) -> list:
        """Return rows for a cross-layer traceability matrix.

        Each row = one element of the specified pivot_type with its Strategy,
        Business, Application, and Technology chain.

        Args:
            pivot_type: ArchiMate element type to pivot on (default: ApplicationComponent).
            pivot_layer: ArchiMate layer to filter by. If None, inferred from DB.
            plateau: Optional plateau/lifecycle filter (e.g. Baseline, Transition, Target).
            limit: Maximum rows per page. None means no limit (used by export).
            offset: Row offset for pagination (default 0).
            search: Optional name search substring, case-insensitive (TRC-017).
            scope: Optional scope filter e.g. 'enterprise', 'application' (TRC-017).
        """
        if pivot_layer is None:
            # Infer the layer from the first element of this type
            sample = ArchiMateElement.query.filter_by(type=pivot_type).first()
            pivot_layer = sample.layer if sample else 'Application'

        query = ArchiMateElement.query.filter(
            db.func.lower(ArchiMateElement.layer) == pivot_layer.lower(),
            ArchiMateElement.type == pivot_type,
            # TRC-023: exclude seed/test records (tmp- prefix)
            ~ArchiMateElement.name.like('tmp-%'),
        )
        if plateau:
            query = query.filter(db.func.lower(ArchiMateElement.togaf_plateau) == plateau.lower())
        if search:
            query = query.filter(ArchiMateElement.name.ilike(f'%{search}%'))
        if scope:
            query = query.filter(db.func.lower(ArchiMateElement.scope) == scope.lower())

        if limit is not None:
            app_elements = query.order_by(ArchiMateElement.name).offset(offset).limit(limit).all()
        else:
            app_elements = query.order_by(ArchiMateElement.name).all()

        rows = []
        for app_el in app_elements:
            chain = self.get_element_chain(app_el.id, max_hops=3)
            if plateau:
                plateau_lower = plateau.lower()
                chain = {
                    'upward': [
                        e for e in chain['upward']
                        if (e.get('plateau') or '').lower() == plateau_lower
                    ],
                    'downward': [
                        e for e in chain['downward']
                        if (e.get('plateau') or '').lower() == plateau_lower
                    ],
                }
            all_chain = chain['upward'] + chain['downward']
            motivation_els = [e for e in all_chain if (e.get('layer') or '').lower() == 'motivation']
            strategy_els = [e for e in all_chain if (e.get('layer') or '').lower() == 'strategy']
            business_els = [e for e in all_chain if (e.get('layer') or '').lower() == 'business']
            tech_els = [e for e in all_chain if (e.get('layer') or '').lower() == 'technology']
            # DataObjects reachable via AccessRelationship (TRC-004)
            data_els = [e for e in all_chain if e.get('type') == 'DataObject']
            rows.append({
                'application': {'id': app_el.id, 'name': app_el.name, 'type': app_el.type},
                'motivation': motivation_els[:3],
                'strategy': strategy_els[:3],
                'business': business_els[:3],
                'technology': tech_els[:3],
                'data': data_els[:3],
                # The lists above are capped at three for display; the matrix cells
                # show these true counts (same chain, same filters, same org scope).
                'counts': {
                    'motivation': len(motivation_els), 'strategy': len(strategy_els),
                    'business': len(business_els), 'technology': len(tech_els),
                    'data': len(data_els),
                },
            })
        return rows

    def count_matrix_rows(self, pivot_type='ApplicationComponent', pivot_layer=None, plateau=None,
                          search=None, scope=None) -> int:
        """Return total row count for pagination metadata (TRC-016)."""
        if pivot_layer is None:
            sample = ArchiMateElement.query.filter_by(type=pivot_type).first()
            pivot_layer = sample.layer if sample else 'Application'
        query = ArchiMateElement.query.filter(
            db.func.lower(ArchiMateElement.layer) == pivot_layer.lower(),
            ArchiMateElement.type == pivot_type,
            ~ArchiMateElement.name.like('tmp-%'),
        )
        if plateau:
            query = query.filter(db.func.lower(ArchiMateElement.togaf_plateau) == plateau.lower())
        if search:
            query = query.filter(ArchiMateElement.name.ilike(f'%{search}%'))
        if scope:
            query = query.filter(db.func.lower(ArchiMateElement.scope) == scope.lower())
        return query.count()


# ── SA-002 — 8-layer traceability chain ─────────────────────────────────────

def get_traceability_chain(solution_id=None, include_bridge_links=False):
    """Build an 8-layer cross-layer traceability chain.

    TRC-001 / TRC-025: When solution_id is provided, filter layers to elements
    scoped to that solution via junction tables (solution_capability_mappings,
    solution_apqc_processes, solution_applications, solution_archimate_elements).
    Stakeholders, drivers, goals, requirements remain global when no direct
    solution junction exists. Also builds relationship_maps dicts.

    include_bridge_links (motivation bridge feature, additive & opt-in):
    when True and solution_id is set, adds a 'bridged_motivation' key listing
    the enterprise Driver/Goal/Outcome/Principle rows this solution's journey
    motivation was promoted to via MotivationBridgeLink (see
    app.services.motivation_bridge_service). Defaults to False so existing
    callers are unaffected — no new key, no behavior change.

    Returns a dict with lists of plain dicts (JSON-serialisable) plus
    a relationship_maps dict with 4 chain keys.
    Each layer falls back to an empty list on any error.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.models.apqc_process import APQCProcess
    from app.models.application_portfolio import ApplicationComponent

    result = {
        "stakeholders": [],
        "drivers": [],
        "goals": [],
        "requirements": [],
        "capabilities": [],
        "processes": [],
        "applications": [],
        "technology": [],
        "relationship_maps": {
            "driver_to_goals": {},
            "goal_to_requirements": {},
            "requirement_to_capabilities": {},
            "capability_to_apps": {},
        },
    }

    def _safe_orm(layer_key, query_fn, mapper):
        """Execute an ORM query and populate result[layer_key], rolling back on any error."""
        try:
            result[layer_key] = [mapper(r) for r in query_fn()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("SA-002 %s: %s", layer_key, exc)
            try:
                db.session.rollback()
            except Exception as rb_exc:  # noqa: BLE001
                logger.debug("SA-002 rollback failed for %s: %s", layer_key, rb_exc)

    # Layers without solution-scoped junction: query archimate_elements (canonical source)
    _safe_orm(
        "stakeholders",
        lambda: ArchiMateElement.query.filter(
            ArchiMateElement.type == "Stakeholder",
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
            ~ArchiMateElement.name.like("tmp-%"),
            ~ArchiMateElement.name.like("same %"),
            ~ArchiMateElement.name.like("<%"),
        ).order_by(ArchiMateElement.name).limit(200).all(),
        lambda r: {"id": r.id, "name": r.name, "concern": r.description or ""},
    )
    _safe_orm(
        "drivers",
        lambda: ArchiMateElement.query.filter(
            ArchiMateElement.type == "Driver",
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
            ~ArchiMateElement.name.like("tmp-%"),
            ~ArchiMateElement.name.like("<%"),
        ).order_by(ArchiMateElement.name).limit(200).all(),
        lambda r: {"id": r.id, "name": r.name, "description": r.description or ""},
    )
    _safe_orm(
        "goals",
        lambda: ArchiMateElement.query.filter(
            ArchiMateElement.type.in_(["Goal", "Outcome"]),
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
            ~ArchiMateElement.name.like("tmp-%"),
            ~ArchiMateElement.name.like("<%"),
        ).order_by(ArchiMateElement.name).limit(200).all(),
        lambda r: {"id": r.id, "name": r.name, "description": r.description or ""},
    )
    _safe_orm(
        "requirements",
        lambda: ArchiMateElement.query.filter(
            ArchiMateElement.type == "Requirement",
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
            ~ArchiMateElement.name.like("<%"),
        ).order_by(ArchiMateElement.name).limit(200).all(),
        lambda r: {"id": r.id, "name": r.name, "description": r.description or ""},
    )

    # TRC-025: Solution-scoped layers when solution_id is provided
    if solution_id is not None:
        try:
            from app.models.solution_models import (
                SolutionCapabilityMapping,
                solution_applications,
                SolutionArchiMateElement as SolutionArchiMateElementModel,
            )
            from app.models.solution_sad_models import SolutionAPQCProcess

            cap_ids = [
                r.capability_id for r in
                SolutionCapabilityMapping.query.filter(
                    SolutionCapabilityMapping.solution_id == solution_id,
                    SolutionCapabilityMapping.capability_id.isnot(None),
                ).all()
            ]
            q_cap = BusinessCapability.query.filter(BusinessCapability.id.in_(cap_ids)) if cap_ids else BusinessCapability.query.filter(False)
            _safe_orm(
                "capabilities",
                lambda q=q_cap: q.order_by(BusinessCapability.name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.name, "description": r.description or ""},
            )

            proc_ids = [
                r.apqc_process_id for r in
                SolutionAPQCProcess.query.filter(
                    SolutionAPQCProcess.solution_id == solution_id,
                ).all()
            ]
            q_proc = APQCProcess.query.filter(APQCProcess.id.in_(proc_ids)) if proc_ids else APQCProcess.query.filter(False)
            _safe_orm(
                "processes",
                lambda q=q_proc: q.order_by(APQCProcess.process_name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.process_name, "description": r.process_description or ""},
            )

            app_ids = db.session.query(solution_applications.c.application_component_id).filter(
                solution_applications.c.solution_id == solution_id,
            ).all()
            app_id_list = [r[0] for r in app_ids]
            q_app = ApplicationComponent.query.filter(ApplicationComponent.id.in_(app_id_list)) if app_id_list else ApplicationComponent.query.filter(False)
            _safe_orm(
                "applications",
                lambda q=q_app: q.order_by(ApplicationComponent.name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.name, "layer": r.application_type or ""},
            )

            tech_ids = [
                r.element_id for r in
                SolutionArchiMateElementModel.query.filter(
                    SolutionArchiMateElementModel.solution_id == solution_id,
                    db.func.lower(SolutionArchiMateElementModel.layer_type) == "technology",
                ).all()
            ]
            q_tech = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(tech_ids),
                db.func.lower(ArchiMateElement.layer) == "technology",
            ) if tech_ids else ArchiMateElement.query.filter(False)
            _safe_orm(
                "technology",
                lambda q=q_tech: q.order_by(ArchiMateElement.name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.name, "layer": r.type or ""},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("TRC-025 solution filter: %s", exc)
            try:
                db.session.rollback()
            except Exception as rb_exc:  # noqa: BLE001
                logger.debug("TRC-025 rollback: %s", rb_exc)
            # Fallback to global for capabilities, processes, applications, technology
            _safe_orm(
                "capabilities",
                lambda: BusinessCapability.query.order_by(BusinessCapability.name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.name, "description": r.description or ""},
            )
            _safe_orm(
                "processes",
                lambda: APQCProcess.query.order_by(APQCProcess.process_name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.process_name, "description": r.process_description or ""},
            )
            _safe_orm(
                "applications",
                lambda: ApplicationComponent.query.order_by(ApplicationComponent.name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.name, "layer": r.application_type or ""},
            )
            _safe_orm(
                "technology",
                lambda: ArchiMateElement.query.filter(
                    db.func.lower(ArchiMateElement.layer) == "technology"
                ).order_by(ArchiMateElement.name).limit(200).all(),
                lambda r: {"id": r.id, "name": r.name, "layer": r.type or ""},
            )
    else:
        _safe_orm(
            "capabilities",
            lambda: BusinessCapability.query.order_by(BusinessCapability.name).limit(200).all(),
            lambda r: {"id": r.id, "name": r.name, "description": r.description or ""},
        )
        _safe_orm(
            "processes",
            lambda: APQCProcess.query.order_by(APQCProcess.process_name).limit(200).all(),
            lambda r: {"id": r.id, "name": r.process_name, "description": r.process_description or ""},
        )
        _safe_orm(
            "applications",
            lambda: ApplicationComponent.query.order_by(ApplicationComponent.name).limit(200).all(),
            lambda r: {"id": r.id, "name": r.name, "layer": r.application_type or ""},
        )
        _safe_orm(
            "technology",
            lambda: ArchiMateElement.query.filter(
                db.func.lower(ArchiMateElement.layer) == "technology"
            ).order_by(ArchiMateElement.name).limit(200).all(),
            lambda r: {"id": r.id, "name": r.name, "layer": r.type or ""},
        )

    # TRC-001: Build relationship maps
    _build_relationship_maps(result, solution_id)

    # Motivation bridge (additive, opt-in — see get_bridged_motivation_for_solution)
    if include_bridge_links and solution_id is not None:
        result["bridged_motivation"] = get_bridged_motivation_for_solution(solution_id)

    return result


def get_bridged_motivation_for_solution(solution_id):
    """Return enterprise motivation elements bridged from a solution's journey
    motivation (SolutionDriver/SolutionGoal/SolutionOutcome/SolutionPrinciple)
    via MotivationBridgeLink.

    Additive helper for the motivation bridge feature (see
    app.services.motivation_bridge_service.promote_solution_motivation). Does
    not affect any existing traceability query — only used when explicitly
    called or when get_traceability_chain(..., include_bridge_links=True) is
    used. Returns [] on any error, missing table, or falsy solution_id.
    """
    if not solution_id:
        return []
    try:
        from app.models.motivation import MotivationBridgeLink

        links = MotivationBridgeLink.query.filter_by(solution_id=solution_id).all()
        return [
            {
                "id": link.enterprise_element_id,
                "type": link.enterprise_element_type,
                "archimate_element_id": link.archimate_element_id,
                "solution_element_type": link.solution_element_type,
                "solution_element_id": link.solution_element_id,
            }
            for link in links
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_bridged_motivation_for_solution: %s", exc)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("get_bridged_motivation_for_solution rollback: %s", rb_exc)
        return []


def _build_relationship_maps(result, solution_id=None):
    """TRC-001 / TRC-024: Build cross-layer relationship maps from FK chains.

    Populates result['relationship_maps'] with 4 dicts mapping element IDs
    to lists of related element IDs in the adjacent downstream layer.
    TRC-024: goal_to_requirements via Goal.archimate_element_id and Requirement.goal_id;
    requirement_to_capabilities via ApplicationRequirementMapping + ApplicationCapabilityMapping.
    """
    from app.models.motivation import Goal
    from app.models.requirements import Requirement
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.models.relationship_tables import ApplicationRequirementMapping

    maps = result["relationship_maps"]

    def _safe_map(layer_key, query_fn, src_fn, tgt_fn):
        """Execute an ORM query and build {source_id: [target_ids]} map."""
        try:
            rows = query_fn()
            mapping = {}
            for row in rows:
                src_id = int(src_fn(row))
                tgt_id = int(tgt_fn(row))
                mapping.setdefault(src_id, []).append(tgt_id)
            maps[layer_key] = mapping
        except Exception as exc:  # noqa: BLE001
            logger.warning("TRC-001 %s: %s", layer_key, exc)
            try:
                db.session.rollback()
            except Exception as rb_exc:  # noqa: BLE001
                logger.debug("TRC-001 rollback failed for %s: %s", layer_key, rb_exc)

    # Driver -> Goals (via goals.driver_id FK)
    _safe_map(
        "driver_to_goals",
        lambda: Goal.query.filter(Goal.driver_id.isnot(None)).all(),
        lambda r: r.driver_id,
        lambda r: r.id,
    )

    # Goal -> Requirements (TRC-024): Requirement.goal_id -> archimate_elements.id;
    # Goal.archimate_element_id links motivation.Goal to same. Map goals.id -> [requirement ids].
    try:
        reqs_with_goal = Requirement.query.filter(Requirement.goal_id.isnot(None)).all()
        ae_to_reqs = {}
        for req in reqs_with_goal:
            ae_id = int(req.goal_id)
            ae_to_reqs.setdefault(ae_id, []).append(int(req.id))
        goals_with_ae = Goal.query.filter(Goal.archimate_element_id.isnot(None)).all()
        goal_to_reqs = {}
        for goal in goals_with_ae:
            goal_to_reqs[int(goal.id)] = ae_to_reqs.get(int(goal.archimate_element_id), [])
        maps["goal_to_requirements"] = goal_to_reqs
    except Exception as exc:  # noqa: BLE001
        logger.warning("TRC-024 goal_to_requirements: %s", exc)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("TRC-024 rollback: %s", rb_exc)
        maps["goal_to_requirements"] = {}

    # Requirement -> Capabilities (TRC-024): via ApplicationRequirementMapping (req -> app)
    # and ApplicationCapabilityMapping (app -> capability). Aggregate req_id -> [capability_id].
    try:
        # tenant-scoping-ok: scoped via the TenantMixin FK parent
        # BusinessCapability, not ACM.organization_id -- that column is NULL
        # on every row in production, so a predicate on it would silently
        # empty this traceability chain. Joining BusinessCapability lets
        # do_orm_execute scope the join automatically. See e622d36 /
        # rationalization_scoring_service.py.
        cap_mappings = (
            # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
            ApplicationCapabilityMapping.query
            .join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .filter(
                ApplicationCapabilityMapping.business_capability_id.isnot(None),
                ApplicationCapabilityMapping.application_component_id.isnot(None),
            )
            .all()
        )
        app_to_caps = {}
        for row in cap_mappings:
            app_id = int(row.application_component_id)
            cap_id = int(row.business_capability_id)
            app_to_caps.setdefault(app_id, set()).add(cap_id)
        req_mappings = ApplicationRequirementMapping.query.filter(
            ApplicationRequirementMapping.requirement_id.isnot(None),
            ApplicationRequirementMapping.application_component_id.isnot(None),
        ).all()
        req_to_caps = {}
        for row in req_mappings:
            req_id = int(row.requirement_id)
            app_id = int(row.application_component_id)
            cap_ids = req_to_caps.setdefault(req_id, set())
            cap_ids.update(app_to_caps.get(app_id, ()))
        maps["requirement_to_capabilities"] = {
            req_id: list(cap_ids) for req_id, cap_ids in req_to_caps.items()
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("TRC-024 requirement_to_capabilities: %s", exc)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("TRC-024 rollback: %s", rb_exc)
        maps["requirement_to_capabilities"] = {}

    # Capability -> Applications (via application_capability_mapping)
    # tenant-scoping-ok: scoped via the TenantMixin FK parent
    # BusinessCapability in the join below, not ACM.organization_id -- see
    # the requirement_to_capabilities block above for why.
    _safe_map(
        "capability_to_apps",
        # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
        lambda: ApplicationCapabilityMapping.query
        .join(
            BusinessCapability,
            ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
        )
        .filter(
            ApplicationCapabilityMapping.business_capability_id.isnot(None),
            ApplicationCapabilityMapping.application_component_id.isnot(None),
        ).all(),
        lambda r: r.business_capability_id,
        lambda r: r.application_component_id,
    )


def get_gap_analysis(solution_id=None) -> dict:
    """TRC-003: Return coverage gaps and orphaned elements for the traceability chain.

    Uses ORM queries to find orphaned elements per layer (elements with
    no relationship to adjacent layers) and compute coverage ratios for each
    transition in the chain.

    Returns a dict with orphaned_* lists and coverage metrics.
    Falls back to empty lists and zero counts on any DB error.
    """
    from sqlalchemy import func
    from app.models.motivation import Driver, Goal
    from app.models.requirements import Requirement
    from app.models.business_capabilities import BusinessCapability
    from app.models.application_capability import ApplicationCapabilityMapping

    gap = {
        "orphaned_drivers": [],
        "orphaned_goals": [],
        "orphaned_requirements": [],
        "orphaned_capabilities": [],
        "coverage": {
            "drivers_with_goals": {"count": 0, "total": 0},
            "goals_with_requirements": {"count": 0, "total": 0},
            "requirements_with_capabilities": {"count": 0, "total": 0},
            "capabilities_with_apps": {"count": 0, "total": 0},
        },
        # Legacy fields for backward compatibility
        "total_drivers": 0,
        "total_goals": 0,
        "total_requirements": 0,
        "total_capabilities": 0,
        "unlinked_drivers": 0,
        "coverage_pct": 0,
    }

    def _safe(fn, fallback):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_gap_analysis failed: %s", exc)
            try:
                db.session.rollback()
            except Exception as rb_exc:  # noqa: BLE001
                logger.debug("get_gap_analysis rollback: %s", rb_exc)
            return fallback

    def _to_item(r):
        return {"id": int(r.id), "name": r.name or f"Item {r.id}"}

    # Drivers with no Goals
    gap["orphaned_drivers"] = _safe(lambda: [
        _to_item(d) for d in Driver.query.filter(
            ~Driver.id.in_(db.session.query(Goal.driver_id).filter(Goal.driver_id.isnot(None)))
        ).order_by(Driver.name).limit(100).all()
    ], [])

    # Goals with no Requirements — uses Requirement.goal_id → archimate_elements.id ← Goal.archimate_element_id FK
    def _orphaned_goals():
        linked_ae_ids = db.session.query(Requirement.goal_id).filter(Requirement.goal_id.isnot(None)).subquery()
        return [
            {"id": int(g.id), "name": g.name or f"Goal {g.id}"}
            for g in Goal.query.filter(
                ~Goal.archimate_element_id.in_(linked_ae_ids)
            ).order_by(Goal.name).limit(100).all()
        ]

    gap["orphaned_goals"] = _safe(_orphaned_goals, [])

    # Requirements with no Application link (proxy: application_component_id IS NULL)
    gap["orphaned_requirements"] = _safe(lambda: [
        {"id": int(r.id), "name": r.title or f"Req {r.id}"}
        for r in Requirement.query.filter(
            Requirement.application_component_id.is_(None)
        ).order_by(Requirement.id).limit(100).all()
    ], [])

    # Capabilities with no Application mappings
    mapped_cap_ids = db.session.query(ApplicationCapabilityMapping.business_capability_id).subquery()
    gap["orphaned_capabilities"] = _safe(lambda: [
        _to_item(c) for c in BusinessCapability.query.filter(
            ~BusinessCapability.id.in_(mapped_cap_ids)
        ).order_by(BusinessCapability.name).limit(100).all()
    ], [])

    # Coverage metrics
    total_drivers = _safe(lambda: db.session.query(func.count()).select_from(Driver).scalar(), 0)
    linked_drivers = total_drivers - len(gap["orphaned_drivers"])
    gap["coverage"]["drivers_with_goals"] = {"count": max(0, linked_drivers), "total": total_drivers}

    total_goals = _safe(lambda: db.session.query(func.count()).select_from(Goal).scalar(), 0)

    def _linked_goals_count():
        linked_ae_ids = db.session.query(Requirement.goal_id).filter(Requirement.goal_id.isnot(None)).subquery()
        return db.session.query(func.count()).select_from(Goal).filter(
            Goal.archimate_element_id.in_(linked_ae_ids)
        ).scalar() or 0

    linked_goals = _safe(_linked_goals_count, 0)
    gap["coverage"]["goals_with_requirements"] = {"count": linked_goals, "total": total_goals}

    total_reqs = _safe(lambda: db.session.query(func.count()).select_from(Requirement).scalar(), 0)
    linked_reqs = total_reqs - len(gap["orphaned_requirements"])
    gap["coverage"]["requirements_with_capabilities"] = {"count": max(0, linked_reqs), "total": total_reqs}

    total_caps = _safe(lambda: db.session.query(func.count()).select_from(BusinessCapability).scalar(), 0)
    linked_caps = total_caps - len(gap["orphaned_capabilities"])
    gap["coverage"]["capabilities_with_apps"] = {"count": max(0, linked_caps), "total": total_caps}

    # Legacy fields
    gap["total_drivers"] = total_drivers
    gap["total_goals"] = total_goals
    gap["total_requirements"] = total_reqs
    gap["total_capabilities"] = total_caps
    gap["unlinked_drivers"] = len(gap["orphaned_drivers"])

    return gap


def get_archimate_gap_analysis() -> dict:
    """ADR-0008: coverage/orphan analysis read from the ArchiMate backbone.

    Unlike ``get_gap_analysis`` (which counts the enterprise DOMAIN tables —
    ``motivation.Driver``, ``requirements.Requirement`` etc.), this reads the
    SAME store the cross-layer matrix and the pivot-type dropdown read:
    ``archimate_elements`` + ``archimate_relationships``. It exists so the
    ``/architecture/traceability`` page can show ONE consistent set of numbers.
    A tenant whose motivation entities live only as ArchiMate elements (created
    via the composer or the AI, with no domain row) previously saw the pivot
    dropdown count 3 Drivers while the coverage tile read "0 of 0" — the two
    surfaces were counting different stores. This closes that disagreement.

    Returns the same dict shape as ``get_gap_analysis`` so the template is
    unchanged; ``orphaned_*`` item ids here are ArchiMateElement ids.
    """
    from collections import defaultdict

    gap = {
        "orphaned_drivers": [],
        "orphaned_goals": [],
        "orphaned_requirements": [],
        "orphaned_capabilities": [],
        "coverage": {
            "drivers_with_goals": {"count": 0, "total": 0},
            "goals_with_requirements": {"count": 0, "total": 0},
            "requirements_with_capabilities": {"count": 0, "total": 0},
            "capabilities_with_apps": {"count": 0, "total": 0},
        },
        "total_drivers": 0,
        "total_goals": 0,
        "total_requirements": 0,
        "total_capabilities": 0,
        "unlinked_drivers": 0,
        "coverage_pct": 0,
    }

    try:
        elements = ArchiMateElement.query.with_entities(
            ArchiMateElement.id,
            ArchiMateElement.name,
            ArchiMateElement.type,
            ArchiMateElement.layer,
        ).filter(
            ArchiMateElement.type.isnot(None),
            ~ArchiMateElement.name.like("tmp-%"),
        ).all()

        id_set = {e.id for e in elements}
        type_by_id = {e.id: e.type for e in elements}
        layer_by_id = {e.id: (e.layer or "") for e in elements}
        name_by_id = {e.id: e.name for e in elements}

        # Undirected neighbour map, restricted to in-tenant elements so it counts
        # exactly the relationships the matrix traversal can see.
        neighbours = defaultdict(set)
        rels = ArchiMateRelationship.query.with_entities(
            ArchiMateRelationship.source_id,
            ArchiMateRelationship.target_id,
        ).filter(ArchiMateRelationship.type.in_(TRACEABILITY_RELATIONSHIP_TYPES)).all()
        for src, tgt in rels:
            if src in id_set and tgt in id_set:
                neighbours[src].add(tgt)
                neighbours[tgt].add(src)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_archimate_gap_analysis failed: %s", exc)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("get_archimate_gap_analysis rollback: %s", rb_exc)
        return gap

    def _neighbour_types(eid):
        return {type_by_id.get(n) for n in neighbours.get(eid, ())}

    def _neighbour_layers(eid):
        return {(layer_by_id.get(n) or "").lower() for n in neighbours.get(eid, ())}

    def _transition(source_types, target_test):
        """Return (connected_list, orphaned_list) of {id,name} dicts."""
        connected, orphaned = [], []
        for eid, etype in type_by_id.items():
            if etype not in source_types:
                continue
            item = {"id": int(eid), "name": name_by_id.get(eid) or f"Item {eid}"}
            if target_test(eid):
                connected.append(item)
            else:
                orphaned.append(item)
        connected.sort(key=lambda i: (i["name"] or "").lower())
        orphaned.sort(key=lambda i: (i["name"] or "").lower())
        return connected, orphaned

    driver_conn, driver_orph = _transition(
        {"Driver"}, lambda eid: bool(_neighbour_types(eid) & {"Goal", "Outcome"}))
    goal_conn, goal_orph = _transition(
        {"Goal", "Outcome"}, lambda eid: "Requirement" in _neighbour_types(eid))
    req_conn, req_orph = _transition(
        {"Requirement"}, lambda eid: "Capability" in _neighbour_types(eid))
    cap_conn, cap_orph = _transition(
        {"Capability"}, lambda eid: "application" in _neighbour_layers(eid))

    gap["orphaned_drivers"] = driver_orph[:100]
    gap["orphaned_goals"] = goal_orph[:100]
    gap["orphaned_requirements"] = req_orph[:100]
    gap["orphaned_capabilities"] = cap_orph[:100]

    def _cov(conn, orph):
        total = len(conn) + len(orph)
        return {"count": len(conn), "total": total}

    gap["coverage"]["drivers_with_goals"] = _cov(driver_conn, driver_orph)
    gap["coverage"]["goals_with_requirements"] = _cov(goal_conn, goal_orph)
    gap["coverage"]["requirements_with_capabilities"] = _cov(req_conn, req_orph)
    gap["coverage"]["capabilities_with_apps"] = _cov(cap_conn, cap_orph)

    gap["total_drivers"] = len(driver_conn) + len(driver_orph)
    gap["total_goals"] = len(goal_conn) + len(goal_orph)
    gap["total_requirements"] = len(req_conn) + len(req_orph)
    gap["total_capabilities"] = len(cap_conn) + len(cap_orph)
    gap["unlinked_drivers"] = len(driver_orph)
    return gap


def get_element_solution_map(element_ids):
    """Return {element_id: [{id, name, status, element_role}, ...]} for linked solutions.

    GLB-056: Queries the solution_archimate_elements junction table to find
    which Solutions are linked to each ArchiMate element.
    For per-layer lookup use get_element_solution_map_by_layer() (TRC-026).
    """
    if not element_ids:
        return {}
    from collections import defaultdict
    from app.models.solution_archimate_element import SolutionArchiMateElement

    result = defaultdict(list)
    try:
        from app.models.solution_models import Solution
        rows = db.session.query(
            SolutionArchiMateElement.element_id,
            Solution.id,
            Solution.name,
            Solution.status,
            SolutionArchiMateElement.element_role,
        ).join(
            Solution, Solution.id == SolutionArchiMateElement.solution_id
        ).filter(
            SolutionArchiMateElement.element_id.in_(element_ids)
        ).all()

        for elem_id, sol_id, sol_name, sol_status, role in rows:
            result[elem_id].append({
                "id": sol_id,
                "name": sol_name,
                "status": sol_status or "Unknown",
                "element_role": role or "primary",
            })
    except Exception as e:
        logger.warning("Error fetching element-solution map: %s", e)

    return dict(result)


def _fetch_solutions_for_technology(ids_set):
    """One query for technology layer (TRC-026)."""
    from app.models.solution_models import Solution
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from collections import defaultdict
    out = defaultdict(list)
    rows = db.session.query(
        SolutionArchiMateElement.element_id,
        Solution.id,
        Solution.name,
        Solution.status,
        SolutionArchiMateElement.element_role,
    ).join(
        Solution, Solution.id == SolutionArchiMateElement.solution_id
    ).filter(
        SolutionArchiMateElement.element_id.in_(ids_set)
    ).all()
    for elem_id, sol_id, sol_name, sol_status, role in rows:
        out[elem_id].append({
            "id": sol_id,
            "name": sol_name,
            "status": sol_status or "Unknown",
            "element_role": role or "primary",
        })
    return dict(out)


def _fetch_solutions_for_applications(ids_set):
    """One query for applications layer (TRC-026)."""
    from app.models.solution_models import Solution, solution_applications
    from collections import defaultdict
    out = defaultdict(list)
    rows = db.session.query(
        solution_applications.c.application_component_id,
        Solution.id,
        Solution.name,
        Solution.status,
    ).join(
        Solution, Solution.id == solution_applications.c.solution_id
    ).filter(
        solution_applications.c.application_component_id.in_(ids_set)
    ).all()
    for elem_id, sol_id, sol_name, sol_status in rows:
        out[elem_id].append({
            "id": sol_id,
            "name": sol_name,
            "status": sol_status or "Unknown",
            "element_role": "primary",
        })
    return dict(out)


def _fetch_solutions_for_processes(ids_set):
    """One query for processes layer (TRC-026)."""
    from app.models.solution_models import Solution
    from app.models.solution_sad_models import SolutionAPQCProcess
    from collections import defaultdict
    out = defaultdict(list)
    rows = db.session.query(
        SolutionAPQCProcess.apqc_process_id,
        Solution.id,
        Solution.name,
        Solution.status,
    ).join(
        Solution, Solution.id == SolutionAPQCProcess.solution_id
    ).filter(
        SolutionAPQCProcess.apqc_process_id.in_(ids_set)
    ).all()
    for elem_id, sol_id, sol_name, sol_status in rows:
        out[elem_id].append({
            "id": sol_id,
            "name": sol_name,
            "status": sol_status or "Unknown",
            "element_role": "primary",
        })
    return dict(out)


def _fetch_solutions_for_capabilities(ids_set):
    """One query for capabilities layer (TRC-026)."""
    from app.models.solution_models import Solution, SolutionCapabilityMapping
    from collections import defaultdict
    out = defaultdict(list)
    rows = db.session.query(
        SolutionCapabilityMapping.capability_id,
        Solution.id,
        Solution.name,
        Solution.status,
    ).join(
        Solution, Solution.id == SolutionCapabilityMapping.solution_id
    ).filter(
        SolutionCapabilityMapping.capability_id.in_(ids_set),
        SolutionCapabilityMapping.solution_id.isnot(None),
    ).all()
    for elem_id, sol_id, sol_name, sol_status in rows:
        out[elem_id].append({
            "id": sol_id,
            "name": sol_name,
            "status": sol_status or "Unknown",
            "element_role": "primary",
        })
    return dict(out)


def get_element_solution_map_by_layer(element_ids_by_layer):
    """TRC-026: Return {layer_key: {element_id: [{id, name, status, element_role}, ...]}}.

    Uses the correct junction table per layer so Linked Solutions panel is correct
    for capabilities, processes, applications, and technology. Layers without
    a solution junction (stakeholders, drivers, goals, requirements) get empty dicts.
    """
    result = {}
    for layer in ["stakeholders", "drivers", "goals", "requirements"]:
        result[layer] = {}
    ids_tech = element_ids_by_layer.get("technology") or []
    ids_app = element_ids_by_layer.get("applications") or []
    ids_proc = element_ids_by_layer.get("processes") or []
    ids_cap = element_ids_by_layer.get("capabilities") or []
    try:
        result["technology"] = _fetch_solutions_for_technology(set(int(x) for x in ids_tech)) if ids_tech else {}
    except Exception as e:
        logger.warning("TRC-026 technology: %s", e)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("TRC-026 rollback: %s", rb_exc)
        result["technology"] = {}
    try:
        result["applications"] = _fetch_solutions_for_applications(set(int(x) for x in ids_app)) if ids_app else {}
    except Exception as e:
        logger.warning("TRC-026 applications: %s", e)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("TRC-026 rollback: %s", rb_exc)
        result["applications"] = {}
    try:
        result["processes"] = _fetch_solutions_for_processes(set(int(x) for x in ids_proc)) if ids_proc else {}
    except Exception as e:
        logger.warning("TRC-026 processes: %s", e)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("TRC-026 rollback: %s", rb_exc)
        result["processes"] = {}
    try:
        result["capabilities"] = _fetch_solutions_for_capabilities(set(int(x) for x in ids_cap)) if ids_cap else {}
    except Exception as e:
        logger.warning("TRC-026 capabilities: %s", e)
        try:
            db.session.rollback()
        except Exception as rb_exc:  # noqa: BLE001
            logger.debug("TRC-026 rollback: %s", rb_exc)
        result["capabilities"] = {}
    return result
