"""
ArchiMate 3.2 Role-based Viewpoint Service

Provides 16 standard viewpoints mapped to architect roles for filtering
elements in the CRUD dashboard, model viewer, and Composer.

Invariants enforced by get_viewpoint_data():
1. Scope required — no solution_id returns scope_required flag
2. Element type filtering via allowed_types
3. Relationship type filtering via allowed_relationships
4. Relationships with hidden endpoints are hidden
"""

import logging

from app.models.constants import ArchiMateRelationshipType

logger = logging.getLogger(__name__)


def _normalize_rel_type(rel_type):
    """Normalize relationship type for comparison.

    DB has mixed formats: 'serving', 'ServingRelationship', 'Realization'.
    Viewpoint definitions use lowercase: 'serving', 'realization'.
    """
    return ArchiMateRelationshipType.normalize(rel_type) or 'association'


STANDARD_VIEWPOINTS = {
    'basic': {
        'name': 'Basic',
        'description': 'Shows all elements and relationships',
        'layers': ['Business', 'Application', 'Technology', 'Strategy', 'Motivation', 'Implementation', 'Physical'],
        'element_types': [],  # empty = all types
        'allowed_relationships': [],  # empty = all types
        'roles': ['Enterprise', 'Business', 'Application', 'Data', 'Integrations', 'Technical', 'Technology'],
        'category': 'basic',
        # Whole-portfolio viewpoint: "all elements and relationships" is not
        # answerable from one solution's scope. See enterprise_scope in
        # get_viewpoint_data()'s docstring.
        'enterprise_scope': True,
    },
    'layered': {
        'name': 'Layered',
        'description': 'All elements organised in horizontal layer bands: strategy, motivation, business, '
                       'application, technology, physical, implementation',
        'layers': ['Strategy', 'Motivation', 'Business', 'Application', 'Technology', 'Physical', 'Implementation'],
        'layer_order': ['strategy', 'motivation', 'business', 'application', 'technology', 'physical',
                        'implementation'],
        'element_types': [],  # all types, grouped by layer
        # Whole-portfolio viewpoint (roles: ['Enterprise'] only) -- see
        # enterprise_scope in get_viewpoint_data()'s docstring.
        'enterprise_scope': True,
        'allowed_relationships': [],
        'roles': ['Enterprise'],
        'category': 'composite',
    },
    'stakeholder': {
        'name': 'Stakeholder',
        'description': 'Stakeholder → Driver → Goal → Assessment motivation chain',
        'layers': ['Motivation'],
        'element_types': ['Stakeholder', 'Driver', 'Goal', 'Assessment'],
        'allowed_relationships': ['association', 'influence', 'realization', 'aggregation', 'composition'],
        'roles': ['Enterprise', 'Business'],
        'category': 'motivation',
    },
    'actor_cooperation': {
        'name': 'Actor Cooperation',
        'description': 'Shows business actors, roles, and collaborations',
        'layers': ['Business'],
        'element_types': ['BusinessActor', 'BusinessRole', 'BusinessCollaboration', 'BusinessInterface'],
        'allowed_relationships': ['composition', 'aggregation', 'assignment', 'association'],
        'roles': ['Business', 'Enterprise'],
        'category': 'basic',
    },
    'business_process': {
        'name': 'Business Process Cooperation',
        'description': 'Shows business processes, functions, and services',
        'layers': ['Business'],
        'element_types': ['BusinessProcess', 'BusinessFunction', 'BusinessEvent', 'BusinessService', 'BusinessObject'],
        'allowed_relationships': ['triggering', 'flow', 'access', 'serving', 'realization', 'assignment',
                                  'composition', 'aggregation'],
        'roles': ['Business', 'Enterprise'],
        'category': 'basic',
    },
    'application_usage': {
        'name': 'Application Usage',
        'description': 'Shows how applications support business processes',
        'layers': ['Business', 'Application'],
        'element_types': ['BusinessProcess', 'BusinessFunction', 'BusinessService', 'ApplicationComponent',
                          'ApplicationService', 'ApplicationInterface', 'DataObject'],
        'allowed_relationships': ['serving', 'access', 'realization', 'flow', 'triggering'],
        'roles': ['Application', 'Integrations'],
        'category': 'application',
    },
    'application_cooperation': {
        'name': 'Application Cooperation',
        'description': 'Shows relationships between application components',
        'layers': ['Application'],
        'element_types': ['ApplicationComponent', 'ApplicationInterface', 'ApplicationService',
                          'ApplicationCollaboration', 'DataObject'],
        'allowed_relationships': ['serving', 'flow', 'realization', 'composition', 'aggregation',
                                  'triggering', 'access'],
        'roles': ['Application', 'Integrations'],
        'category': 'application',
    },
    'technology': {
        'name': 'Technology',
        'description': 'Shows the technology infrastructure',
        'layers': ['Technology'],
        'element_types': ['Node', 'Device', 'SystemSoftware', 'TechnologyService', 'TechnologyInterface',
                          'Path', 'CommunicationNetwork', 'Artifact'],
        'allowed_relationships': ['composition', 'aggregation', 'assignment', 'serving', 'realization', 'flow'],
        'roles': ['Technical', 'Technology'],
        'category': 'technology',
    },
    'technology_usage': {
        'name': 'Technology Usage',
        'description': 'Shows how technology supports applications',
        'layers': ['Application', 'Technology'],
        'element_types': ['ApplicationComponent', 'ApplicationService', 'Node', 'Device', 'SystemSoftware',
                          'TechnologyService', 'TechnologyInterface', 'Artifact'],
        'allowed_relationships': ['serving', 'assignment', 'realization', 'access'],
        'roles': ['Technical', 'Technology', 'Application'],
        'category': 'technology',
    },
    'implementation_deployment': {
        'name': 'Implementation & Deployment',
        'description': 'Shows deployment of application components on technology',
        'layers': ['Application', 'Technology', 'Implementation'],
        'element_types': ['ApplicationComponent', 'Node', 'Device', 'SystemSoftware', 'Artifact',
                          'WorkPackage', 'Deliverable'],
        'allowed_relationships': ['association', 'realization', 'triggering', 'composition', 'aggregation',
                                  'assignment'],
        'roles': ['Technical', 'Technology'],
        'category': 'implementation',
    },
    'information_structure': {
        'name': 'Information Structure',
        'description': 'Shows the structure of information used by the enterprise',
        'layers': ['Business', 'Application'],
        'element_types': ['BusinessObject', 'DataObject', 'Representation', 'Contract'],
        'allowed_relationships': ['composition', 'aggregation', 'association', 'realization', 'access'],
        'roles': ['Data', 'Business'],
        'category': 'information',
    },
    'service_realization': {
        'name': 'Service Realization',
        'description': 'Shows how services are realized by underlying components',
        'layers': ['Business', 'Application', 'Technology'],
        'element_types': ['BusinessService', 'BusinessProcess', 'ApplicationService', 'ApplicationComponent',
                          'TechnologyService', 'Node'],
        'allowed_relationships': ['realization', 'serving', 'composition', 'aggregation', 'access'],
        'roles': ['Application', 'Technical'],
        'category': 'service',
    },
    'motivation': {
        'name': 'Motivation',
        'description': 'Shows drivers, goals, principles, and requirements',
        'layers': ['Motivation'],
        'element_types': ['Stakeholder', 'Driver', 'Assessment', 'Goal', 'Outcome', 'Principle',
                          'Requirement', 'Constraint'],
        'allowed_relationships': ['association', 'influence', 'realization', 'aggregation', 'composition'],
        'roles': ['Enterprise', 'Business'],
        'category': 'motivation',
    },
    'strategy': {
        'name': 'Strategy',
        'description': 'Shows strategic capabilities and courses of action',
        'layers': ['Strategy', 'Business'],
        'element_types': ['Resource', 'Capability', 'CourseOfAction', 'ValueStream',
                          'BusinessProcess', 'BusinessFunction'],
        'allowed_relationships': ['realization', 'association', 'assignment', 'triggering', 'flow',
                                  'composition', 'aggregation', 'influence'],
        'roles': ['Enterprise'],
        'category': 'strategy',
    },
    'capability': {
        'name': 'Capability Map',
        'description': 'Shows the capability landscape of the enterprise',
        'layers': ['Strategy'],
        'element_types': ['Capability', 'Resource', 'CourseOfAction'],
        'allowed_relationships': ['composition', 'aggregation', 'assignment', 'realization', 'serving'],
        'roles': ['Enterprise', 'Business'],
        'category': 'strategy',
    },
    'migration': {
        'name': 'Migration & Implementation',
        'description': 'Shows work packages, plateaus, and gaps for migration',
        'layers': ['Implementation'],
        'element_types': ['WorkPackage', 'Deliverable', 'Plateau', 'Gap', 'ImplementationEvent'],
        'allowed_relationships': ['association', 'triggering', 'realization', 'composition', 'aggregation'],
        'roles': ['Enterprise', 'Technical'],
        'category': 'implementation',
    },
}

ROLE_DEFAULT_VIEWPOINT = {
    'Enterprise': 'strategy',
    'Business': 'business_process',
    'Application': 'application_usage',
    'Data': 'information_structure',
    'Integrations': 'application_cooperation',
    'Technical': 'technology_usage',
    'Technology': 'technology',
}


class ArchiMateViewpointService:
    """Service for role-based ArchiMate viewpoint filtering."""

    def get_viewpoints(self) -> dict:
        return STANDARD_VIEWPOINTS

    def get_viewpoint(self, key: str) -> dict:
        return STANDARD_VIEWPOINTS.get(key, STANDARD_VIEWPOINTS['basic'])

    def get_default_for_role(self, role: str) -> str:
        return ROLE_DEFAULT_VIEWPOINT.get(role, 'basic')

    def filter_elements_by_viewpoint(self, elements: list, viewpoint_key: str) -> list:
        vp = self.get_viewpoint(viewpoint_key)
        layers = [item.lower() for item in vp.get('layers', [])]
        types = vp.get('element_types', [])
        return [e for e in elements
                if (not layers or (e.get('layer') or '').lower() in layers)
                and (not types or e.get('element_type') in types)]


# ── SA-007 module-level helpers ───────────────────────────────────────────────

def get_available_viewpoints() -> list:
    """Return list of all viewpoint definitions for the SA-007 UI.

    Also appends CANVAS_TEMPLATES (Lean Canvas, Business Model Canvas,
    business case) with category 'canvas', by importing the config dict —
    the Composer's viewpoint dropdown lists them without a fourth catalogue.
    """
    result = []
    for vp_id, vp in STANDARD_VIEWPOINTS.items():
        result.append({
            'id': vp_id,
            'name': vp['name'],
            'description': vp.get('description', ''),
            'layers': vp.get('layers', []),
            'element_types': vp.get('element_types', []),
            'category': vp.get('category', 'other'),
        })

    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    for key, tpl in CANVAS_TEMPLATES.items():
        result.append({
            'id': key,
            'name': tpl['name'],
            'description': '',
            'layers': [],
            'element_types': [],
            'category': 'canvas',
        })
    return result


def get_viewpoint_counts(solution_id: int) -> dict:
    """Return element counts per viewpoint for a given solution.

    Keys match STANDARD_VIEWPOINTS keys (used by dropdown template).
    Returns: {'application_cooperation': 12, 'motivation': 15, ...}
    """
    if not solution_id:
        return {}

    try:
        from app.models.archimate_core import ArchiMateElement
        from app.models.solution_models import SolutionArchiMateElement

        # Get all element IDs for this solution
        junctions = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
        element_ids = [j.element_id for j in junctions if j.element_id]

        if not element_ids:
            try:
                from app.models.archimate_core import ArchitectureModel
                sol_arch = ArchitectureModel.query.filter_by(solution_id=solution_id).first()
                if sol_arch:
                    element_ids = [e.id for e in ArchiMateElement.query.filter_by(
                        architecture_id=sol_arch.id).all()]
            except Exception as e:
                logger.warning('ArchitectureModel fallback failed in viewpoint counts: %s', e)

        if not element_ids:
            return {vp_id: 0 for vp_id in STANDARD_VIEWPOINTS}

        # Single query: get (id, type) for all solution elements
        elements_with_types = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(element_ids)
        ).with_entities(ArchiMateElement.id, ArchiMateElement.type).all()

        # Count matching elements per viewpoint
        counts = {}
        for vp_id, vp in STANDARD_VIEWPOINTS.items():
            allowed = set(vp.get('element_types', []))
            if not allowed:  # empty = wildcard
                counts[vp_id] = len(elements_with_types)
            else:
                counts[vp_id] = sum(1 for _, t in elements_with_types if t in allowed)
        return counts
    except Exception as e:
        logger.warning('Failed to compute viewpoint counts: %s', e)
        return {}


# ── Dashboard layer → ArchiMate element type map ────────────────────────────
# Single system of record for "which element types make up layer X" (ADR
# 0008 -- "one accessor per concept"). Previously duplicated inside
# app/modules/dashboard/v2/routes/dashboard_views.py as a local _LAYER_TYPES
# dict; that copy is now an import of this one so the dashboard card's count
# and the composer's layer filter can never drift apart. Six canonical
# dashboard layers -- ArchiMate 3.2 folds Physical (Equipment/Facility/
# Material) into Technology, so there is no separate 'physical' key here even
# though STANDARD_VIEWPOINTS['layered']['layer_order'] has a seventh entry
# for it; a composer 'layer=technology' filter must include those types to
# match what the dashboard card promised.
#
# There is a third, differently-shaped map in app/models/archimate_core.py
# (_ELEMENT_TYPE_LAYER, used for relationship-validity checks) -- left alone
# deliberately; reconciling all three is tracked as a follow-up, not part of
# this change.
LAYER_TYPES = {
    "motivation": {"stakeholder", "driver", "assessment", "goal", "outcome",
                   "principle", "requirement", "constraint", "meaning", "value"},
    "strategy": {"resource", "capability", "valuestream", "courseofaction"},
    "business": {"businessactor", "businessrole", "businesscollaboration",
                 "businessinterface", "businessprocess", "businessfunction",
                 "businessinteraction", "businessevent", "businessservice",
                 "businessobject", "contract", "representation", "product"},
    "application": {"applicationcomponent", "applicationcollaboration",
                    "applicationinterface", "applicationfunction",
                    "applicationinteraction", "applicationprocess",
                    "applicationevent", "applicationservice", "dataobject"},
    "technology": {"node", "device", "systemsoftware", "technologycollaboration",
                   "technologyinterface", "path", "communicationnetwork",
                   "technologyfunction", "technologyprocess", "technologyinteraction",
                   "technologyevent", "technologyservice", "artifact",
                   "equipment", "facility", "distributionnetwork", "material"},
    "implementation": {"workpackage", "deliverable", "implementationevent",
                       "plateau", "gap"},
}

# Reverse index: lowercased element type -> layer key. Used both here (to
# resolve a `layer` filter to a set of ArchiMateElement.type values) and by
# dashboard_views.py (to resolve a type to a layer for the count).
LAYER_TYPE_TO_LAYER = {t: layer for layer, ts in LAYER_TYPES.items() for t in ts}

# The set of layer keys a caller is allowed to filter by (composer `layer=`
# query param). Untrusted input must be allowlisted against exactly this set
# -- an unrecognised value is a 400, never a silent "no filter".
VALID_LAYER_KEYS = frozenset(LAYER_TYPES.keys())


def _types_for_layer(layer: str) -> list:
    """Return the ArchiMate element `type` strings (as actually stored --
    original casing varies, so callers should match case-insensitively) that
    belong to the given dashboard layer key.

    Returns an empty list for an unknown layer; callers are expected to have
    already validated `layer` against VALID_LAYER_KEYS before calling this.
    """
    return sorted(LAYER_TYPES.get(layer, set()))


def get_viewpoint_data(viewpoint_id: str, solution_id: int = None, layer: str = None) -> dict:
    """Return elements filtered for this viewpoint, grouped for layout.

    Enforces 4 invariants:
    1. Scope required — no solution_id returns scope_required flag, UNLESS
       the viewpoint declares 'enterprise_scope': True (whole-portfolio
       viewpoints like 'basic'/'layered', whose own descriptions promise
       "all elements" across the organisation, not one solution's model —
       for those, no solution_id means "show the whole tenant", not "show
       nothing until a solution is picked").
    2. Element type filtering via viewpoint's element_types
    3. Relationship type filtering via viewpoint's allowed_relationships
    4. Relationships with hidden endpoints are hidden (no dangling arrows)

    A canvas key (CANVAS_TEMPLATES) short-circuits before any of the above —
    canvases are never scope_required — and returns the standard shape with
    `zones` and `entries` present and empty. A later change fills them via
    a projection; until then the key is present so the Composer's payload
    shape never changes underneath it.
    """
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    if viewpoint_id in CANVAS_TEMPLATES:
        tpl = CANVAS_TEMPLATES[viewpoint_id]
        return {
            'viewpoint_id': viewpoint_id,
            'viewpoint_name': tpl['name'],
            'scope_required': False,
            'elements': [],
            'relationships': [],
            'total': 0,
            'layer_order': [],
            'groups': {},
            'zones': [],
            'entries': [],
        }

    vp = STANDARD_VIEWPOINTS.get(viewpoint_id, STANDARD_VIEWPOINTS['basic'])
    layers = [la.lower() for la in vp.get('layers', [])]
    allowed_types = vp.get('element_types', [])
    allowed_rels = set(vp.get('allowed_relationships', []))
    enterprise_scope = bool(vp.get('enterprise_scope'))

    # `layer` narrows by ArchiMateElement.type via the shared LAYER_TYPES map
    # (not the unreliable .layer column -- see module docstring above).
    # Callers (the API route) are responsible for 400ing an unknown value
    # before reaching here; an unrecognised value falls through to
    # `layer_type_names = []`, which -- to fail closed rather than silently
    # showing everything -- is treated as "match nothing", not "no filter".
    layer_type_names = None
    if layer:
        layer_type_names = [t.lower() for t in _types_for_layer(layer)]

    # ── Invariant 1: Scope required ──
    if not solution_id and not enterprise_scope:
        return {
            'viewpoint_id': viewpoint_id,
            'viewpoint_name': vp['name'],
            'scope_required': True,
            'elements': [],
            'relationships': [],
            'total': 0,
            'layer_order': vp.get('layer_order', layers or ['business']),
            'groups': {},
        }

    serialised = []
    relationships_out = []

    try:
        from app.models.archimate_core import ArchiMateElement

        is_enterprise_wide = not solution_id and enterprise_scope
        if is_enterprise_wide:
            # D5: the tenant-isolation listener (do_orm_execute) is a NO-OP,
            # not a deny, when g.current_org_id is unset — so an unscoped
            # ArchiMateElement.query here would return every tenant's rows if
            # this code path is ever reached outside a request context with an
            # org resolved. Fail closed instead of relying on that listener
            # alone for the whole-tenant path.
            from app.middleware.tenant_context import current_org_id as _current_org_id

            if not _current_org_id():
                return {
                    'viewpoint_id': viewpoint_id,
                    'viewpoint_name': vp['name'],
                    'scope_required': True,
                    'elements': [],
                    'relationships': [],
                    'total': 0,
                    'layer_order': vp.get('layer_order', layers or ['business']),
                    'groups': {},
                }
            # Whole-tenant path: ArchiMateElement carries TenantMixin, so a
            # bare .query is already scoped to g.current_org_id by the
            # do_orm_execute listener (app/middleware/tenant_isolation.py) --
            # no manual organization_id predicate needed or wanted here.
            query = ArchiMateElement.query
            if allowed_types:
                query = query.filter(ArchiMateElement.type.in_(allowed_types))
            if layer_type_names is not None:
                from app import db as _db
                query = query.filter(_db.func.lower(ArchiMateElement.type).in_(layer_type_names))
            elements = query.limit(500).all()
            element_ids = [e.id for e in elements]
        else:
            # Get solution's element IDs (junction + fallback)
            from app.models.solution_models import SolutionArchiMateElement
            junctions = (
                SolutionArchiMateElement.query
                .filter_by(solution_id=solution_id)
                .all()
            )
            element_ids = [j.element_id for j in junctions if j.element_id]

            # Fallback: if junction is empty, try ArchitectureModel path
            if not element_ids:
                try:
                    from app.models.archimate_core import ArchitectureModel
                    sol_arch = ArchitectureModel.query.filter_by(solution_id=solution_id).first()
                    if sol_arch:
                        arch_elements = ArchiMateElement.query.filter_by(architecture_id=sol_arch.id).all()
                        element_ids = [e.id for e in arch_elements]
                        logger.info('Solution %s: loaded %d elements via ArchitectureModel fallback', solution_id, len(element_ids))
                except Exception as e:
                    logger.warning('ArchitectureModel fallback failed for solution %s: %s', solution_id, e)

        if element_ids and not is_enterprise_wide:
            # ── Invariant 2: Element type filtering ──
            # (skipped for the enterprise-wide path above, which already
            # queried and filtered `elements` directly)
            query = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids))
            if allowed_types:
                query = query.filter(ArchiMateElement.type.in_(allowed_types))
            if layer_type_names is not None:
                from app import db as _db
                query = query.filter(_db.func.lower(ArchiMateElement.type).in_(layer_type_names))
            elements = query.limit(500).all()
        elif not is_enterprise_wide:
            elements = []

        # Build FILTERED element ID set (for Invariant 4 — hidden endpoints)
        filtered_ids = set(e.id for e in elements)

        serialised = [
            {
                'id': e.id,
                'name': e.name,
                'type': e.type or '',
                'layer': (e.layer or '').lower(),
                'description': e.description or '',
            }
            for e in elements
        ]

        # Query relationships from BOTH tables
        if element_ids:
            raw_rels = []
            try:
                from app.models.archimate_core import ArchiMateRelationship
                user_rels = (
                    ArchiMateRelationship.query
                    .filter(
                        ArchiMateRelationship.source_id.in_(element_ids),
                        ArchiMateRelationship.target_id.in_(element_ids),
                    )
                    .all()
                )
                for r in user_rels:
                    raw_rels.append({
                        'id': r.id,
                        'source_id': r.source_id,
                        'target_id': r.target_id,
                        'type': _normalize_rel_type(r.type),
                    })
            except Exception as e:
                logger.warning('Failed to load user relationships for solution %s: %s', solution_id, e)

            try:
                from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
                inf_rels = (
                    ArchitectureInferenceRelationship.query
                    .filter(
                        ArchitectureInferenceRelationship.source_id.in_(element_ids),
                        ArchitectureInferenceRelationship.target_id.in_(element_ids),
                    )
                    .all()
                )
                existing_pairs = {(r['source_id'], r['target_id'], r['type']) for r in raw_rels}
                for r in inf_rels:
                    rel_type = _normalize_rel_type(r.rel_type)
                    key = (r.source_id, r.target_id, rel_type)
                    if key not in existing_pairs:
                        raw_rels.append({
                            'id': r.id,
                            'source_id': r.source_id,
                            'target_id': r.target_id,
                            'type': rel_type,
                        })
                        existing_pairs.add(key)
            except Exception as e:
                logger.warning('Failed to load inference relationships for solution %s: %s', solution_id, e)

            # ── Invariant 3: Relationship type filtering ──
            # ── Invariant 4: Hide relationships with hidden endpoints ──
            for r in raw_rels:
                if r['source_id'] not in filtered_ids or r['target_id'] not in filtered_ids:
                    continue
                if allowed_rels and r['type'] not in allowed_rels:
                    continue
                relationships_out.append(r)

    except Exception as e:  # noqa: BLE001 — DB may not be initialised in fast-init
        # D4: a bare `serialised = []` here was indistinguishable from a
        # genuinely empty model (fabricated-data class per CLAUDE.md) — a 200
        # response with elements: [] on any failure told the caller nothing
        # went wrong. Surface an explicit error flag instead, and reset
        # relationships_out too so a failure partway through the relationship
        # loop can never leave dangling relationships alongside an empty
        # elements list (Invariant 4).
        logger.warning('get_viewpoint_data failed for viewpoint %s, solution %s: %s', viewpoint_id, solution_id, e)
        serialised = []
        relationships_out = []
        return {
            'viewpoint_id': viewpoint_id,
            'viewpoint_name': vp['name'],
            'error': True,
            'error_reason': 'Failed to load viewpoint data',
            'elements': [],
            'relationships': [],
            'total': 0,
            'layer_order': vp.get('layer_order', layers or ['business']),
            'groups': {},
        }

    # Group by layer for the layered viewpoint
    grouped: dict = {}
    layer_order = vp.get('layer_order', layers or ['business'])
    for layer_key in layer_order:
        grouped[layer_key] = [el for el in serialised if el['layer'] == layer_key]
    for el in serialised:
        if el['layer'] not in grouped:
            grouped[el['layer']] = grouped.get(el['layer'], []) + [el]

    return {
        'viewpoint_id': viewpoint_id,
        'viewpoint_name': vp['name'],
        'layer_order': layer_order,
        'groups': grouped,
        'elements': serialised,
        'relationships': relationships_out,
        'total': len(serialised),
    }
