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
                       'application, technology (physical elements included), implementation',
        'layers': ['Strategy', 'Motivation', 'Business', 'Application', 'Technology', 'Implementation'],
        'layer_order': ['strategy', 'motivation', 'business', 'application', 'technology', 'implementation'],
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
                   "principle", "requirement", "constraint", "meaning", "value",
                   # Measures and recorded evidence sit with the motivation
                   # elements they support (ArchiMate 3.2 motivation extension).
                   "metric", "evidencerecord"},
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
# Composite elements (Location, Grouping) and the Junction connectors belong to
# no layer in ArchiMate 3.2, and a stored type the registry does not know has no
# layer either. They open together as one view, so a card for them always has
# elements behind it.
COMPOSITE_LAYER = "composite"
COMPOSITE_TITLE = "Composite and other elements"
COMPOSITE_TYPES = frozenset({"location", "grouping", "junction", "andjunction", "orjunction"})

VALID_LAYER_KEYS = frozenset(LAYER_TYPES.keys()) | {COMPOSITE_LAYER}


def _types_for_layer(layer: str) -> list:
    """Return the ArchiMate element `type` strings (as actually stored --
    original casing varies, so callers should match case-insensitively) that
    belong to the given dashboard layer key.

    Returns an empty list for an unknown layer; callers are expected to have
    already validated `layer` against VALID_LAYER_KEYS before calling this.
    """
    return sorted(LAYER_TYPES.get(layer, set()))


def _type_key(type_name) -> str:
    """A stored element type as the layer map spells it: lower case, no spaces or underscores, so a
    model file's "Work Package" is the same type as "WorkPackage"."""
    return (type_name or "").lower().replace(" ", "").replace("_", "")


def _type_key_column():
    """The same spelling as _type_key, on the stored column."""
    from app import db as _db
    from app.models.archimate_core import ArchiMateElement

    lowered = _db.func.lower(_db.func.coalesce(ArchiMateElement.type, ""))
    return _db.func.replace(_db.func.replace(lowered, " ", ""), "_", "")


def _filter_to_layer(query, layer, layer_type_names):
    """Narrow an element query to one layer; the composite view takes every type no layer holds."""
    from app import db as _db

    key = _type_key_column()
    if layer == COMPOSITE_LAYER:
        return query.filter(_db.not_(key.in_(list(LAYER_TYPE_TO_LAYER))))
    return query.filter(key.in_(layer_type_names))


# The most elements one viewpoint answer carries.
VIEWPOINT_ELEMENT_LIMIT = 500

# The most elements a viewpoint opens on. Past this many a drawing cannot keep its
# names readable on one screen, so a whole-model opening answers with one count per
# layer instead, and a single layer opens on its most connected elements.
VIEWPOINT_OPENING_LIMIT = 40

LAYER_TITLES = {
    "motivation": "Motivation", "strategy": "Strategy", "business": "Business",
    "application": "Application", "technology": "Technology",
    "implementation": "Implementation & Migration",
    COMPOSITE_LAYER: COMPOSITE_TITLE,
}


def _omitted_note(omitted: int, layer, type_filtered: bool):
    """One plain sentence for why a viewpoint shows fewer elements than the store holds."""
    if omitted <= 0:
        return None
    count = "%d element%s" % (omitted, "" if omitted == 1 else "s")
    verb = "is" if omitted == 1 else "are"
    if layer:
        return "%s in other layers %s not shown" % (count, verb)
    if type_filtered:
        return "%s of other types %s not shown" % (count, verb)
    return "%s beyond the first %d %s not shown" % (count, VIEWPOINT_ELEMENT_LIMIT, verb)


def _most_connected(query, org_id):
    """Order an element query by how many relationships each element has, most first,
    so a capped answer keeps the elements a person can follow rather than an arbitrary slice."""
    from app import db as _db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    ends = _db.union_all(
        _db.select(ArchiMateRelationship.source_id.label("element_id")).where(
            ArchiMateRelationship.organization_id == org_id),
        _db.select(ArchiMateRelationship.target_id.label("element_id")).where(
            ArchiMateRelationship.organization_id == org_id),
    ).subquery()
    degree = (
        _db.select(ends.c.element_id, _db.func.count().label("n"))
        .group_by(ends.c.element_id)
        .subquery()
    )
    return (
        query.outerjoin(degree, degree.c.element_id == ArchiMateElement.id)
        .order_by(_db.func.coalesce(degree.c.n, 0).desc(), ArchiMateElement.id)
    )


# The four Physical types. ArchiMate 3.2 folds them into Technology and the layer map above does too, so
# no screen counts Physical as a layer of its own; the Technology count holds them.
PHYSICAL_TYPES = frozenset({"equipment", "facility", "distributionnetwork", "material"})


def _register_only_count(dashboard_layer: str, org_id=None) -> int:
    """Records of the layer's own tables that have no model element (a gap entered in the gap register).

    The layer lists them and counts them, the model map does not draw them. A type held in a table that
    mirrors into the model is counted once, as its model element. Raises when a table cannot be read."""
    from app.modules.architecture.routes.archimate_crud.routes import LAYER_CONFIG, MODEL_REGISTRY

    keys = [dashboard_layer] + (["physical"] if dashboard_layer == "technology" else [])
    records = 0
    for key in keys:
        for type_name in LAYER_CONFIG.get(key, {}).get("elements", []):
            model_class = MODEL_REGISTRY.get(type_name)
            if not model_class:
                continue
            query = model_class.query
            if org_id is not None and hasattr(model_class, "organization_id"):
                query = query.filter(model_class.organization_id == org_id)
            if hasattr(model_class, "archimate_element_id"):
                query = query.filter(model_class.archimate_element_id.is_(None))
            records += query.count()
    return records


# A layer as an element's layer column may spell it (lower case, no spaces or underscores, "&" as "and").
# Physical is Technology's: its elements are counted and listed there.
STORED_LAYER_KEYS = {
    "motivation": "motivation", "strategy": "strategy", "business": "business", "application": "application",
    "technology": "technology", "physical": "technology", "implementation": "implementation",
    "implementationmigration": "implementation", "implementationandmigration": "implementation",
}


def _stored_layer_key(raw) -> str:
    """The stored layer column as the layer map spells it, or "" when it names no layer."""
    spelled = (raw or "").lower().replace("&", "and").replace(" ", "").replace("_", "")
    return STORED_LAYER_KEYS.get(spelled, "")


def element_layer(stored_layer, element_type) -> str:
    """The layer one element is counted in, whatever its type: the layer it is stored in. An element that
    names no layer is placed by its type, and one whose type no layer holds is the composite row."""
    return _stored_layer_key(stored_layer) or LAYER_TYPE_TO_LAYER.get(_type_key(element_type), COMPOSITE_LAYER)


def _stored_layer_column():
    """The same spelling as _stored_layer_key, on the stored column."""
    from app import db as _db
    from app.models.archimate_core import ArchiMateElement

    spelled = _db.func.replace(_db.func.coalesce(_db.func.lower(ArchiMateElement.layer), ""), "&", "and")
    return _db.func.replace(_db.func.replace(spelled, " ", ""), "_", "")


def layer_model_query(layer: str, org_id=None):
    """The model elements of one layer, the rows count_layers counts for it: every element stored in the layer,
    whatever its type (element_layer is the rule). Physical, which is no layer of its own, answers with the
    Physical types alone. With ``org_id`` the query names that organisation itself, as count_layers does,
    instead of relying on the tenant filter alone."""
    from app import db as _db
    from app.models.archimate_core import ArchiMateElement

    base = ArchiMateElement.query
    if org_id is not None:
        base = base.filter(ArchiMateElement.organization_id == org_id)
    if layer == "physical":
        return base.filter(_type_key_column().in_(list(PHYSICAL_TYPES)))
    stored = _stored_layer_column()
    unplaced = _db.not_(stored.in_(list(STORED_LAYER_KEYS)))
    if layer == COMPOSITE_LAYER:
        return base.filter(unplaced, _db.not_(_type_key_column().in_(list(LAYER_TYPE_TO_LAYER))))
    names = [name for name, key in STORED_LAYER_KEYS.items() if key == layer]
    typed = [t.lower() for t in _types_for_layer(layer)]
    return base.filter(_db.or_(stored.in_(names), _db.and_(unplaced, _type_key_column().in_(typed))))


def count_layers(org_id, allowed_types=None) -> list:
    """The one count of every layer, read by the architecture dashboard's tabs and the Composer's layer cards.

    One row per layer in LAYER_TITLES order: ``model`` is the organisation's model elements stored in the
    layer, whatever their type (one that names no layer is placed by its type; types the map does not know are
    the composite row), ``register_only`` the records the layer lists that the model does not hold, and ``count`` their sum. ``count`` is None, never
    a partial figure, when the records could not be counted. Physical is not a row: its types are Technology's.
    """
    from app import db as _db
    from app.models.archimate_core import ArchiMateElement

    stored = _stored_layer_column()
    query = _db.select(stored, _db.func.lower(ArchiMateElement.type), _db.func.count()).group_by(
        stored, _db.func.lower(ArchiMateElement.type)).where(ArchiMateElement.organization_id == org_id)
    if allowed_types:
        query = query.where(ArchiMateElement.type.in_(allowed_types))
    model = {}
    for stored_layer, type_name, n in _db.session.execute(query).all():
        key = element_layer(stored_layer, type_name)
        model[key] = model.get(key, 0) + n
    rows = []
    for layer, title in LAYER_TITLES.items():
        try:
            records = 0 if layer == COMPOSITE_LAYER else _register_only_count(layer, org_id)
        except Exception as exc:  # a table that cannot be read leaves the layer uncounted, not under-counted
            logger.warning("count_layers: records of %s could not be counted: %s", layer, exc)
            _db.session.rollback()
            records = None
        rows.append({
            "layer": layer, "title": title, "model": model.get(layer, 0), "register_only": records,
            "count": None if records is None else model.get(layer, 0) + records,
        })
    return rows


def _layer_overview(vp, viewpoint_id, layers, allowed_types, matching, org_id):
    """The opening for a model too large to draw readably: one count per layer."""
    rows = {row["layer"]: row for row in count_layers(org_id, allowed_types)}
    present = {k: row for k, row in rows.items() if (row["count"] if row["count"] is not None else row["model"])}
    order = [k for k in vp.get('layer_order', layers or []) if k in present]
    order += [k for k in LAYER_TITLES if k in present and k not in order]
    overview = []
    for k in order:
        row = present[k]
        overview.append({
            'layer': k, 'title': row["title"], 'count': row["count"] if row["count"] is not None else row["model"],
            'not_on_model_map': row["register_only"] or 0,
        })
    return {
        'viewpoint_id': viewpoint_id,
        'viewpoint_name': vp['name'],
        'layer_order': vp.get('layer_order', layers or ['business']),
        'groups': {},
        'elements': [],
        'relationships': [],
        'total': 0,
        'overview': overview,
        'store_total': sum(row['count'] for row in overview),
    }


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
    store_total = None
    layer_total = None

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
                query = _filter_to_layer(query, layer, layer_type_names)
            matching = query.count()
            if layer is None and matching > VIEWPOINT_OPENING_LIMIT:
                return _layer_overview(vp, viewpoint_id, layers, allowed_types, matching, _current_org_id())
            cap = VIEWPOINT_OPENING_LIMIT if layer is not None else VIEWPOINT_ELEMENT_LIMIT
            cap = min(cap, VIEWPOINT_ELEMENT_LIMIT)
            elements = _most_connected(query, org_id=_current_org_id()).limit(cap).all()
            layer_total = matching
            element_ids = [e.id for e in elements]
            # What the rest of the product counts for this organisation (the
            # dashboard's element figure reads the same rows), so a viewpoint
            # that shows fewer can say how many it left out and why.
            store_total = ArchiMateElement.query.count()
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
                query = _filter_to_layer(query, layer, layer_type_names)
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

    result = {
        'viewpoint_id': viewpoint_id,
        'viewpoint_name': vp['name'],
        'layer_order': layer_order,
        'groups': grouped,
        'elements': serialised,
        'relationships': relationships_out,
        'total': len(serialised),
    }
    if layer_total is not None and layer_total > len(serialised) and layer is not None:
        result['layer_total'] = layer_total
        result['layer_omitted_note'] = (
            "the %d most connected of %d %s elements are shown"
            % (len(serialised), layer_total, LAYER_TITLES.get(layer, layer).lower())
        )
    if store_total is not None:
        result['store_total'] = store_total
        # With a layer open, "other layers" counts only the other layers: this
        # layer's own capped elements are said once, in layer_omitted_note.
        shown_or_in_layer = layer_total if (layer is not None and layer_total is not None) else len(serialised)
        result['omitted'] = store_total - shown_or_in_layer
        result['omitted_note'] = _omitted_note(
            store_total - shown_or_in_layer, layer, bool(allowed_types)
        )
    return result
