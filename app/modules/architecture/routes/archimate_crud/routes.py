"""
Architecture CRUD Routes
Unified dashboard for managing Motivation, Strategy, and Business layer elements
"""

import copy
import re
from datetime import datetime

from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from sqlalchemy import or_

from app import db
from app.utils.tenant_users import escape_like_literal
from . import archimate_crud
from .services.ai_generation_service import AIGenerationService
from .services.field_configs import (
    create_empty_form_data,
    get_all_element_types,
    get_element_config,
    get_element_field_names,
)

# Application Layer imports
from app.models.application_layer import (
    ApplicationCollaboration,
    ApplicationComponent,
    ApplicationEvent,
    ApplicationFunction,
    ApplicationInteraction,
    ApplicationInterface,
    ApplicationProcess,
    ApplicationService,
    DataObject,
)
from app.models.archimate_business import Contract
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.modules.intelligence.services.crosswalk_service import CrosswalkService
from app.models.archimate_missing_elements import (
    MissingBusinessCollaboration,
    MissingBusinessInteraction,
    MissingBusinessInterface,
    Product,
    Stakeholder,
)

# Technology Layer behavioural elements. `TechnologyCollaborationFull`
# (technology_collaborations_full) is the ArchiMate "Technology Collaboration"
# used here rather than technology_layer.TechnologyCollaboration
# (technology_collaborations): the two map the same concept onto two tables, and
# the "Full" one is the only one any application code writes or reads
# (app/application_mgmt/detail_layer_routes.py), so it is where the rows are.
from app.models.archimate_technology import (
    TechnologyCollaborationFull,
    TechnologyEvent,
    TechnologyFunction,
    TechnologyInteraction,
    TechnologyProcess,
)
from app.models.business_capabilities import BusinessCapability, BusinessFunction
from app.models.business_layer import (
    BusinessActor,
    BusinessEvent,
    BusinessObject,
    BusinessRole,
    BusinessService,
)

# Implementation Layer imports
from app.models.implementation_migration import Deliverable as PlanningDeliverable
from app.models.implementation_migration import Gap
from app.models.implementation_migration import ImplementationEvent
from app.models.implementation_migration import Plateau
from app.models.implementation_migration import WorkPackage
from app.models.models import ConstraintElement, Outcome, Principle, Requirement
from app.models.motivation import Assessment, Driver, Goal, Meaning, Value

# Physical Layer imports
from app.models.physical_layer import (
    PhysicalDistributionNetwork,
    PhysicalEquipment,
    PhysicalFacility,
    PhysicalMaterial,
)
from app.models.process_data import BusinessProcess
from app.models.representation import Representation
from app.models.strategy_layer import CourseOfAction, StrategyResource

# Technology Layer imports
from app.models.technology_layer import (
    CommunicationNetwork,
    Device,
    Node,
    Path,
    SystemSoftware,
    TechnologyArtifact,
    TechnologyInterface,
    TechnologyService,
)
from app.models.unified_capability import ValueStream
import logging
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

# Model registry mapping element types to model classes
MODEL_REGISTRY = {
    # Motivation Layer
    "Stakeholder": Stakeholder,
    "Driver": Driver,
    "Assessment": Assessment,
    "Goal": Goal,
    "Outcome": Outcome,
    "Principle": Principle,
    "Requirement": Requirement,
    "Constraint": ConstraintElement,
    "Meaning": Meaning,
    "Value": Value,
    # Strategy Layer
    "Resource": StrategyResource,
    "Capability": BusinessCapability,  # BusinessCapability is the Strategy layer capability model
    "ValueStream": ValueStream,
    "CourseOfAction": CourseOfAction,
    # Business Layer
    "BusinessActor": BusinessActor,
    "BusinessRole": BusinessRole,
    "BusinessCollaboration": MissingBusinessCollaboration,
    "BusinessInterface": MissingBusinessInterface,
    "BusinessProcess": BusinessProcess,
    "BusinessFunction": BusinessFunction,
    "BusinessInteraction": MissingBusinessInteraction,
    "BusinessEvent": BusinessEvent,
    "BusinessService": BusinessService,
    "BusinessObject": BusinessObject,
    "Contract": Contract,
    "Representation": Representation,
    "Product": Product,
    # Application Layer
    "ApplicationComponent": ApplicationComponent,
    "ApplicationInterface": ApplicationInterface,
    "ApplicationService": ApplicationService,
    "ApplicationFunction": ApplicationFunction,
    "ApplicationProcess": ApplicationProcess,
    "ApplicationInteraction": ApplicationInteraction,
    "ApplicationEvent": ApplicationEvent,
    "ApplicationCollaboration": ApplicationCollaboration,
    "DataObject": DataObject,
    # Technology Layer — all 13 ArchiMate 3.2 types
    "Node": Node,
    "Device": Device,
    "SystemSoftware": SystemSoftware,
    "TechnologyCollaboration": TechnologyCollaborationFull,
    "TechnologyInterface": TechnologyInterface,
    "Path": Path,
    "CommunicationNetwork": CommunicationNetwork,
    "TechnologyFunction": TechnologyFunction,
    "TechnologyProcess": TechnologyProcess,
    "TechnologyInteraction": TechnologyInteraction,
    "TechnologyEvent": TechnologyEvent,
    "TechnologyService": TechnologyService,
    "Artifact": TechnologyArtifact,
    # Physical Layer
    "Equipment": PhysicalEquipment,
    "Facility": PhysicalFacility,
    "DistributionNetwork": PhysicalDistributionNetwork,
    "Material": PhysicalMaterial,
    # Implementation & Migration Layer
    "WorkPackage": WorkPackage,
    "Deliverable": PlanningDeliverable,
    "ImplementationEvent": ImplementationEvent,
    "Plateau": Plateau,
    "Gap": Gap,
}

# Layer configuration
# Accepted stored spellings per layer key. ArchiMate 3.2 names two layers with
# an ampersand ("Implementation & Migration"), and rows exist in both forms, so
# a count that matches only the short key silently loses them (ARCH-010).
LAYER_ALIASES = {
    "motivation": ["motivation"],
    "strategy": ["strategy"],
    "business": ["business"],
    "application": ["application"],
    "technology": ["technology"],
    "physical": ["physical"],
    "implementation": [
        "implementation",
        "implementation & migration",
        "implementation and migration",
        "implementation_migration",
    ],
}

LAYER_CONFIG = {
    "motivation": {
        "name": "Motivation Layer",
        "elements": [
            "Stakeholder",
            "Driver",
            "Assessment",
            "Goal",
            "Outcome",
            "Principle",
            "Requirement",
            "Constraint",
            "Meaning",
            "Value",
        ],
        "icon": "🎯",
    },
    "strategy": {
        "name": "Strategy Layer",
        "elements": ["Resource", "Capability", "ValueStream", "CourseOfAction"],
        "icon": "📊",
    },
    "business": {
        "name": "Business Layer",
        "elements": [
            "BusinessActor",
            "BusinessRole",
            "BusinessCollaboration",
            "BusinessInterface",
            "BusinessProcess",
            "BusinessFunction",
            "BusinessInteraction",
            "BusinessEvent",
            "BusinessService",
            "BusinessObject",
            "Contract",
            "Representation",
            "Product",
        ],
        "icon": "💼",
    },
    "application": {
        "name": "Application Layer",
        "elements": [
            "ApplicationComponent",
            "ApplicationInterface",
            "ApplicationService",
            "ApplicationFunction",
            "ApplicationProcess",
            "ApplicationInteraction",
            "ApplicationEvent",
            "ApplicationCollaboration",
            "DataObject",
        ],
        "icon": "🖥️",
    },
    "technology": {
        "name": "Technology Layer",
        "elements": [
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyCollaboration",
            "TechnologyInterface",
            "Path",
            "CommunicationNetwork",
            "TechnologyFunction",
            "TechnologyProcess",
            "TechnologyInteraction",
            "TechnologyEvent",
            "TechnologyService",
            "Artifact",
        ],
        "icon": "⚙️",
    },
    "physical": {
        "name": "Physical Layer",
        "elements": [
            "Equipment",
            "Facility",
            "DistributionNetwork",
            "Material",
        ],
        "icon": "🏭",
    },
    "implementation": {
        "name": "Implementation & Migration Layer",
        "elements": [
            "WorkPackage",
            "Deliverable",
            "ImplementationEvent",
            "Plateau",
            "Gap",
        ],
        "icon": "🚀",
    },
}

# User-facing layer language belongs beside the registry that defines the layer
# keys, while the ORM-oriented LAYER_CONFIG remains stable for API consumers.
# The colour values deliberately reference only the domain tokens already
# declared in shadcn_tokens.css.  ArchiMate's Physical extension shares the
# Technology layer's green family because the design system has no separate
# physical-layer token.
LAYER_PRESENTATION = {
    "motivation": {
        "title": "Motivation Architecture",
        "short_name": "Motivation",
        "description": "Goals, drivers, requirements and constraints that explain why the architecture changes",
        "color_token": "--layer-motivation",
    },
    "strategy": {
        "title": "Strategy Architecture",
        "short_name": "Strategy",
        "description": "Resources, capabilities, value streams and courses of action that shape strategic direction",
        "color_token": "--layer-strategy",
    },
    "business": {
        "title": "Business Architecture",
        "short_name": "Business",
        "description": "Actors, roles, processes, services and information that describe how the enterprise operates",
        "color_token": "--layer-business",
    },
    "application": {
        "title": "Application Architecture",
        "short_name": "Application",
        "description": "Components, services, interfaces, behaviours and data that support the enterprise",
        "color_token": "--layer-application",
    },
    "technology": {
        "title": "Technology Architecture",
        "short_name": "Technology",
        "description": "Nodes, platforms, networks, services and artifacts that provide the technical foundation",
        "color_token": "--layer-technology",
    },
    "physical": {
        "title": "Physical Architecture",
        "short_name": "Physical",
        "description": "Facilities, equipment, distribution networks and materials that anchor the physical estate",
        "color_token": "--layer-technology",
    },
    "implementation": {
        "title": "Implementation & Migration Architecture",
        "short_name": "Implementation",
        "description": "Work packages, deliverables, plateaus, events and gaps that govern architecture change",
        "color_token": "--layer-implementation",
    },
}


# JSON-serialisable typed field configs for every element type that has one,
# keyed by element_type. Handed to the dashboard template so the create modal
# can render typed fields instead of just name/description — see field_configs.py.
ELEMENT_FIELD_CONFIGS = {
    et: get_element_config(et).to_dict() for et in get_all_element_types()
}


# D-02: single source of truth for element_type -> layer, derived from
# LAYER_CONFIG (the same table that drives the by-layer tabs and dashboard) so
# there is exactly one place this mapping is authored. Consumed by
# create_element() below instead of trusting the URL's ``layer`` segment,
# which can disagree with element_type when a stale/hand-built link pairs the
# wrong layer with a type (e.g. .../business/ApplicationInterface/new) —
# that mismatch is how ApplicationInterface rows ended up stored with
# layer="Business" (D-02).
ELEMENT_TYPE_TO_LAYER: dict[str, str] = {
    element_type: layer_key
    for layer_key, cfg in LAYER_CONFIG.items()
    for element_type in cfg["elements"]
}


def _canonical_layer_for_type(element_type, requested_layer):
    """Return the correct layer key for ``element_type``.

    Falls back to ``requested_layer`` only for element types the registry
    does not know about, so an unrecognised/custom type is not blocked from
    being created.
    """
    return ELEMENT_TYPE_TO_LAYER.get(element_type, requested_layer)


# ARCH-050: the browsing routes below take a bare "/<layer>/<element_type>"
# segment pair. Left unconstrained, that pattern is a catch-all matching ANY
# two path segments under /architecture/ — including "/architecture/element/99999999"
# (layer="element", element_type="99999999", neither a real layer) and
# "/architecture/elements/-1" or "/architecture/elements/abc" (layer="elements",
# element_type="-1"/"abc" — the int converter on the real detail route
# elements/<int:element_id> simply declines to match those, and THIS route
# silently absorbs them instead). Every miss then flashed a warning and
# redirected to the dashboard, which renders 200 — so a mistyped or malicious
# element id never reached a 404, it landed on the generic elements page.
# Restricting <layer> to the known LAYER_CONFIG keys makes the pattern only
# match real by-layer browsing URLs, so anything else correctly falls through
# to the app's 404 handler.
_LAYER_URL_PATTERN = "<any(" + ", ".join(LAYER_CONFIG.keys()) + "):layer>"


def _validated_layer_filter(layer, element_type):
    """Narrow a requested (layer, element_type) pair to what the registry knows.

    The By-Layer sidebar links hand these in on the query string. Anything the
    registry does not recognise is dropped rather than passed through: a filter
    that matches nothing renders as an active filter over an empty table, which
    reads as "you have no Nodes" rather than "that is not a type".
    """
    if layer not in LAYER_CONFIG:
        return None, None
    if element_type not in LAYER_CONFIG[layer]["elements"]:
        return layer, None
    return layer, element_type


# Fields to skip when auto-discovering displayable attributes
_SKIP_FIELDS = frozenset(
    {
        "id",
        "name",
        "title",
        "description",
        "created_at",
        "updated_at",
        "archimate_element_id",
        "architecture_id",
        "canonical_capability_id",
        "parent_capability_id",
        "deprecated_in_favor_of_id",
        "goal_id",
        "master_system_id",
        "parent_id",
        # ArchiMate identity — shown in header/identity card
        "type",
        "layer",
        "scope",
        "building_block_type",
        "plateau",
        "status",
        "organization_id",
    }
)


def _get_display_fields(element, model_class):
    """Introspect a model instance and return a list of (label, value) tuples
    for all non-empty, non-private, non-FK columns worth displaying."""
    from sqlalchemy import inspect as sa_inspect

    fields = []
    try:
        mapper = sa_inspect(model_class)
    except Exception:
        return fields

    for col in mapper.columns:
        col_name = col.key
        # Skip internal / already-displayed fields
        if col_name in _SKIP_FIELDS or col_name.startswith("_"):
            continue
        # Skip foreign keys (except those with domain meaning)
        if col.foreign_keys and col_name not in ("goal_id",):
            continue

        value = getattr(
            element, col_name, None
        )  # model-safety-ok: dynamic column iteration via sa_inspect mapper
        if value is None or value == "":
            continue
        # Skip 0 for numeric types only (not booleans)
        if not isinstance(value, bool) and value == 0:
            continue
        # An empty JSON dict/list column (e.g. a provenance or config column
        # with no data yet) has nothing to show -- str({}) rendering as a
        # literal "{}" on a real screen is worse than omitting the row.
        if isinstance(value, (dict, list)) and not value:
            continue

        # Build a human-readable label from snake_case, correcting acronyms
        # that .title() mangles (Acm -> ACM) rather than leaving the wrong
        # case on a screen a real architect reads.
        label = col_name.replace("_", " ").title()
        for acronym in ("Acm", "Api", "Id", "Url", "Sap", "Rfc", "Bapi", "Arb"):
            label = re.sub(rf"\b{acronym}\b", acronym.upper(), label)

        # Format special types
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        elif hasattr(value, "strftime"):
            value = value.strftime("%Y-%m-%d %H:%M")
        elif isinstance(value, float):
            value = f"{value:.2f}" if value != int(value) else str(int(value))
        elif isinstance(value, dict):
            # A dict column is internal structure, not prose -- render its
            # entries as "key: value" pairs rather than Python's repr(), which
            # leaked as a literal {'source_model': 'Risk'} on real screens.
            value = "; ".join(f"{k}: {v}" for k, v in value.items())
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)

        fields.append({"label": label, "value": str(value)})

    return fields


@archimate_crud.route("/")
@archimate_crud.route("/dashboard")
@login_required
def dashboard():
    """Main dashboard with tabs for each layer.

    ``?layer=`` and ``?element_type=`` pre-select a tab and a type filter, which
    is how the By-Layer sidebar navigation lands on the elements it names. Both
    are validated here rather than in the browser so the server decides what
    counts as a real filter.
    """
    initial_layer, initial_element_type = _validated_layer_filter(
        request.args.get("layer"), request.args.get("element_type")
    )
    selected_key = initial_layer or "motivation"
    presentation_config = {
        key: {**config, **LAYER_PRESENTATION[key], "key": key}
        for key, config in LAYER_CONFIG.items()
    }
    return render_template(
        "archimate_crud/dashboard.html",
        layer_config=presentation_config,
        selected_layer=presentation_config[selected_key],
        initial_layer=initial_layer,
        initial_element_type=initial_element_type,
    )


@archimate_crud.route("/api/field-configs")
@login_required
def api_field_configs():
    """Typed per-element-type form field configs, split out of the dashboard
    payload (ARCH-064). This is static configuration, identical for every
    request and every tenant — it does not belong inline in every dashboard
    HTML response. The dashboard template points the create/edit modal at
    this endpoint instead of receiving the ~16KB of ``tojson`` output inline
    on every page load; ``app/static/js/archimate_crud/dashboard.js``
    fetches it once in ``init()``.
    """
    resp = jsonify(ELEMENT_FIELD_CONFIGS)
    # Static per-deployment config, not per-tenant data — safe to cache in the
    # browser so a returning visitor doesn't refetch it every dashboard load.
    resp.cache_control.max_age = 3600
    resp.cache_control.private = True
    return resp


def _count_layer_elements(layer):
    """Total element count for one layer, or None if it could not be counted.

    Shared by the per-layer and batched endpoints so the two can never drift.

    Counts dedicated per-type tables (portfolio source) plus archimate_elements
    (architecture source), excluding typed rows that are mirrored into
    archimate_elements -- counting both sides reported every mirrored entity
    twice, and a tenant with 71 elements was told it had 142.

    Returns None, never a partial total, when any constituent count raises.
    The previous behaviour logged the failure and carried on with the remaining
    types, which produced an under-count that looked exactly like a real one:
    the user could not tell a measured total from a broken one. Per CLAUDE.md a
    value that was not measured must be None so the UI renders an em dash.
    """
    if layer not in LAYER_CONFIG:
        return None

    layer_types = LAYER_CONFIG[layer]["elements"]
    total = 0
    # Count from dedicated per-type tables, EXCLUDING rows that are mirrored into
    # archimate_elements — those are added below, and counting both sides made the
    # page report every mirrored entity twice. A tenant with 71 elements was told
    # it had 142 (71 typed rows + the same 71 mirrors). A model with no
    # archimate_element_id cannot be deduplicated this way, so it is counted whole.
    for etype in layer_types:
        model_class = MODEL_REGISTRY.get(etype)
        if not model_class:
            continue
        try:
            q = model_class.query
            if hasattr(model_class, "archimate_element_id"):
                q = q.filter(model_class.archimate_element_id.is_(None))
            total += q.count()
        except Exception as e:
            current_app.logger.warning(f"_count_layer_elements: count failed for {etype}: {e}")
            return None

    # Count from archimate_elements for this layer.
    # 17 Aug 2026 (ARCH-010): matched on the LAYER_CONFIG key alone, so an
    # element stored as "implementation & migration" — the ArchiMate 3.2 name
    # for the layer whose key here is "implementation" — matched nothing and
    # was dropped from every count. The dashboard headline was short by exactly
    # the size of that layer while its own tiles summed to the real total, and
    # the layer's elements were invisible in the UI. Match every spelling.
    try:
        _aliases = [a.lower() for a in LAYER_ALIASES.get(layer, [layer])]
        ae_count = ArchiMateElement.query.filter(
            db.func.lower(ArchiMateElement.layer).in_(_aliases),
        ).count()
        total += ae_count
    except Exception as e:
        current_app.logger.warning(f"_count_layer_elements: archimate_elements count failed for {layer}: {e}")
        return None

    return total


@archimate_crud.route("/api/layer/<layer>/count")
@login_required
def api_layer_count(layer):
    """Return the total element count for a layer using SQL COUNT — fast path
    used by the dashboard tab badges.  Avoids loading all rows into Python.

    Counts from both dedicated per-type tables (portfolio source) and
    archimate_elements (architecture source) to match the elements endpoint
    behaviour.  Dedicated-table rows and archimate_elements rows for the same
    logical element are counted once each (they have different numeric IDs in
    different tables, so an exact dedup requires a full scan — we accept the
    small over-count here in favour of O(1) SQL COUNT queries that never hang).
    """
    if layer not in LAYER_CONFIG:
        return jsonify({"success": False, "error": f"Unknown layer: {layer}"}), 404

    total = _count_layer_elements(layer)
    if total is None:
        # Counting failed; return null to indicate unknown count
        return jsonify({"layer": layer, "total": None})
    return jsonify({"layer": layer, "total": total})


@archimate_crud.route("/api/layer/counts")
@login_required
def api_layer_counts():
    """Return counts for ALL layers in one response.
    Used by the dashboard to avoid per-layer requests that trigger rate limits.
    """
    # A layer that could not be counted is null, never 0 -- the dashboard renders
    # null as an em dash and falls back to the per-layer endpoint for it.
    counts = {layer: _count_layer_elements(layer) for layer in LAYER_CONFIG}
    return jsonify({"counts": counts})


@archimate_crud.route("/api/layer/<layer>/elements")
@login_required
def api_layer_elements(layer):
    """Return all elements for a layer (all types combined) as JSON.

    Supports: search, element_type filter, pagination, sorting.
    """
    import math

    if layer not in LAYER_CONFIG:
        return jsonify({"success": False, "error": f"Unknown layer: {layer}"}), 404

    search = request.args.get("search", "").strip()
    type_filter = request.args.get("element_type", "").strip()
    source_filter = request.args.get("source", "").strip()  # "portfolio", "architecture", or ""
    page = safe_int_arg('page', 1, minimum=1)
    per_page = safe_int_arg('per_page', 25, minimum=1, maximum=500)
    sort_by = request.args.get("sort_by", "name")
    sort_order = request.args.get("sort_order", "asc")

    layer_types = LAYER_CONFIG[layer]["elements"]
    if type_filter:
        # Support comma-separated list of types (viewpoint filter) or single type
        requested_types = [t.strip() for t in type_filter.split(",") if t.strip()]
        query_types = [t for t in requested_types if t in layer_types] or layer_types
    else:
        query_types = layer_types

    all_elements = []
    if source_filter != "architecture":  # skip dedicated tables when filtering to architecture-only
        for etype in query_types:
            model_class = MODEL_REGISTRY.get(etype)
            if not model_class:
                continue
            try:
                q = model_class.query
                # Same fix as _count_layer_elements (ARCH — "a tenant with 71
                # elements was told it had 142"): a dedicated-table row that has
                # been mirrored into archimate_elements is added again below as
                # its own "architecture" entry — the seen_pairs check at line 728
                # keyed on (element_type, elem.id), never the actual mirror's
                # own id, so it never recognised the mirror as already seen. Every
                # correctly-mirrored element therefore appeared twice, as
                # Source=Portfolio and Source=Architecture with different ids.
                # Excluding mirrored rows here (they're still shown, once, via
                # the archimate_elements supplement below) fixes it the same way
                # the count endpoint was already fixed.
                if hasattr(model_class, "archimate_element_id"):
                    q = q.filter(model_class.archimate_element_id.is_(None))
                if search:
                    safe_search = escape_like_literal(search)
                    filters = []
                    if hasattr(
                        model_class, "name"
                    ):  # model-safety-ok: polymorphic ArchiMate elements
                        filters.append(model_class.name.ilike(f"%{safe_search}%", escape="\\"))
                    if hasattr(
                        model_class, "description"
                    ):  # model-safety-ok: polymorphic ArchiMate elements
                        filters.append(model_class.description.ilike(f"%{safe_search}%", escape="\\"))
                    if filters:
                        q = q.filter(or_(*filters))
                rows = q.all()  # model-safety-ok: small fixed set (max 10 layer types)
                # Batch-fetch the plateau off each row's linked ArchiMateElement in
                # one query rather than N+1 — same fix as the "architecture" branch
                # below (T-14-adjacent audit, 2 Sep 2026): the as-is/to-be state a
                # user sets on the create/edit form is togaf_plateau, a real column,
                # but this endpoint never read it back, so the client-side Plateau
                # filter (dashboard.js) — which reads a "plateau" key this dict never
                # had — silently matched nothing. Tagging worked; filtering by what
                # you tagged did not.
                linked_ae_ids = [
                    getattr(e, "archimate_element_id", None) for e in rows
                ]
                linked_ae_ids = [i for i in linked_ae_ids if i]
                plateau_by_ae_id = {}
                # H6: this branch always shipped rel_count=None for every
                # "portfolio"-sourced element (a Driver/Goal/etc. row synced
                # to ArchiMateElement per CLAUDE.md's "ArchiMate is the
                # backbone" rule) -- the "architecture" branch below computes
                # a real count via the exact same linked ae_id, this one just
                # never did. That made the dashboard's "Connected" tile read
                # 'Relationship data unavailable' for every layer whose
                # elements come from dedicated per-type tables (motivation
                # included) even when the elements API call itself succeeded.
                # One batched query per page, same shape as the plateau batch
                # already here, rather than N+1.
                rel_count_by_ae_id = {}
                if linked_ae_ids:
                    plateau_by_ae_id = dict(
                        db.session.query(
                            ArchiMateElement.id, ArchiMateElement.togaf_plateau
                        ).filter(ArchiMateElement.id.in_(linked_ae_ids))
                    )
                    rel_rows = (
                        db.session.query(ArchiMateRelationship.source_id)
                        .filter(ArchiMateRelationship.source_id.in_(linked_ae_ids))
                        .union_all(
                            db.session.query(ArchiMateRelationship.target_id).filter(
                                ArchiMateRelationship.target_id.in_(linked_ae_ids)
                            )
                        )
                        .all()
                    )
                    for (ae_id_hit,) in rel_rows:
                        rel_count_by_ae_id[ae_id_hit] = rel_count_by_ae_id.get(ae_id_hit, 0) + 1
                # F-08(a), Capgemini dry-run: the edit modal's typedFieldDefaults()
                # (dashboard.js) reads source[name] for each of this type's
                # configured fields (goal_type, driver_type, category, ...) —
                # this dict never had any of them, so every typed field looked
                # blank on reopening even though the write path (create/edit
                # POST -> _set_model_fields/_apply_architecture_state) worked
                # and persisted correctly. It was a read gap, not a write one.
                type_config = get_element_config(etype)
                typed_field_names = (
                    [f.name for f in type_config.fields if f.name != "architecture_state"]
                    if type_config else []
                )
                for elem in rows:
                    name = getattr(elem, "name", None) or getattr(
                        elem, "title", "Unnamed"
                    )  # model-safety-ok: polymorphic ArchiMate elements
                    status = getattr(elem, "status", None) or getattr(
                        elem, "operational_status", None
                    )  # model-safety-ok: polymorphic ArchiMate elements
                    ae_id = getattr(elem, "archimate_element_id", None)
                    elem_dict = {
                        "id": elem.id,
                        "name": name,
                        "description": getattr(elem, "description", "")
                        or "",  # model-safety-ok: polymorphic ArchiMate elements
                        "element_type": etype,
                        "status": status,
                        "layer": layer,
                        "source": "portfolio",
                        "properties": getattr(elem, "properties", None) or "",
                        "plateau": plateau_by_ae_id.get(ae_id) if ae_id else None,
                        # None (not 0) when there is no linked ArchiMateElement at
                        # all -- that is genuinely unmeasured, not "zero relationships".
                        "rel_count": rel_count_by_ae_id.get(ae_id, 0) if ae_id else None,
                    }
                    # architecture_state is the form's name for the plateau
                    # select; the API calls the same value "plateau" — map it
                    # under both keys so typedFieldDefaults finds it.
                    elem_dict["architecture_state"] = elem_dict["plateau"] or ""
                    for field_name in typed_field_names:
                        value = getattr(elem, field_name, None)
                        elem_dict[field_name] = value if value is not None else ""
                    all_elements.append(elem_dict)
            except Exception as e:
                current_app.logger.warning(f"Error querying {etype}: {e}")

    # Supplement: also pull from archimate_elements for any elements not already
    # found in dedicated tables (handles data imported via other paths).
    if source_filter != "portfolio":  # skip archimate_elements when filtering to portfolio-only
        try:
            seen_pairs = {(el["element_type"], el["id"]) for el in all_elements}
            # Same alias matching as api_layer_count (ARCH-010) — otherwise the
            # listing silently omits the elements the count now includes, and the
            # catalogue page keeps rendering the layer as empty.
            ae_q = ArchiMateElement.query.filter(
                db.func.lower(ArchiMateElement.layer).in_(
                    [a.lower() for a in LAYER_ALIASES.get(layer, [layer])]
                ),
                ArchiMateElement.type.in_(query_types),
            )
            if search:
                safe_s = escape_like_literal(search)
                ae_q = ae_q.filter(
                    or_(
                        ArchiMateElement.name.ilike(f"%{safe_s}%", escape="\\"),
                        ArchiMateElement.description.ilike(f"%{safe_s}%", escape="\\"),
                    )
                )
            for ae in ae_q.all():
                if (ae.type, ae.id) not in seen_pairs:
                    _rel_count = ArchiMateRelationship.query.filter(
                        db.or_(
                            ArchiMateRelationship.source_id == ae.id,
                            ArchiMateRelationship.target_id == ae.id,
                        )
                    ).count()
                    ae_dict = {
                        "id": ae.id,
                        "name": ae.name or "",
                        "description": ae.description or "",
                        "element_type": ae.type,
                        "status": None,
                        "layer": layer,
                        "source": "architecture",
                        "properties": ae.properties or "",
                        "plateau": ae.togaf_plateau,
                        "rel_count": _rel_count,
                    }
                    ae_dict["architecture_state"] = ae_dict["plateau"] or ""
                    # F-08(a): the F-04 dedup fix means every MIRRORED element
                    # (the overwhelming majority — anything created through
                    # the normal form) is now listed from here, not from the
                    # dedicated-table branch above. Without this lookup, this
                    # branch's elements would still lose their typed fields —
                    # trading the double-listing bug for a "typed fields only
                    # populate for the rare unmirrored row" bug instead.
                    ae_type_config = get_element_config(ae.type)
                    if ae_type_config:
                        dedicated_model = MODEL_REGISTRY.get(ae.type)
                        dedicated_row = None
                        if dedicated_model is not None and hasattr(dedicated_model, "archimate_element_id"):
                            dedicated_row = dedicated_model.query.filter_by(
                                archimate_element_id=ae.id
                            ).first()
                        for f in ae_type_config.fields:
                            if f.name == "architecture_state":
                                continue
                            value = getattr(dedicated_row, f.name, None) if dedicated_row else None
                            ae_dict[f.name] = value if value is not None else ""
                    all_elements.append(ae_dict)
        except Exception as e:
            current_app.logger.warning(f"Error supplementing from archimate_elements: {e}")

    reverse = sort_order == "desc"
    if sort_by in ("name", "element_type", "description", "status"):
        all_elements.sort(key=lambda x: (x.get(sort_by) or "").lower(), reverse=reverse)

    total = len(all_elements)
    pages = math.ceil(total / per_page) if per_page > 0 else 1
    start = (page - 1) * per_page
    end = start + per_page
    page_elements = all_elements[start:end]

    return jsonify(
        {
            "elements": page_elements,
            "pagination": {
                "page": page,
                "pages": pages,
                "per_page": per_page,
                "total": total,
                "has_next": page < pages,
                "has_prev": page > 1,
            },
            "layer": layer,
            "element_types": layer_types,
        }
    )


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>")
@login_required
def list_elements(layer, element_type):
    """List all elements of a specific type"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        flash(f"Element type {element_type} is not yet supported", "warning")
        return redirect(url_for("archimate_crud.dashboard"))

    # Get search/filter parameters
    search = request.args.get("search", "").strip()
    page = safe_int_arg('page', 1, minimum=1)
    per_page = safe_int_arg('per_page', 20, minimum=1, maximum=500)
    view_mode = request.args.get("view", "table", type=str)  # table or card

    # Build query
    query = model_class.query

    # Apply search filter (escape LIKE wildcards to prevent injection)
    if search:
        safe_search = escape_like_literal(search)
        if hasattr(model_class, "name"):
            query = query.filter(model_class.name.ilike(f"%{safe_search}%", escape="\\"))
        if hasattr(model_class, "description"):
            query = query.filter(
                or_(
                    model_class.description.ilike(f"%{safe_search}%", escape="\\"),
                    model_class.name.ilike(f"%{safe_search}%", escape="\\"),
                )
            )

    # Order by name
    if hasattr(model_class, "name"):
        query = query.order_by(model_class.name)
    elif hasattr(model_class, "title"):
        query = query.order_by(model_class.title)

    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    elements = pagination.items

    # Convert to dict for JSON serialization
    elements_data = []
    for elem in elements:
        elem_dict = {
            "id": elem.id,
            "name": getattr(
                elem, "name", getattr(elem, "title", "Unnamed")
            ),  # model-safety-ok: polymorphic ArchiMate elements
            "description": getattr(
                elem, "description", ""
            ),  # model-safety-ok: polymorphic ArchiMate elements
            "archimate_element_id": getattr(
                elem, "archimate_element_id", None
            ),  # model-safety-ok: polymorphic ArchiMate elements
        }
        # Add layer-specific fields
        if hasattr(elem, "status"):  # model-safety-ok: polymorphic ArchiMate elements
            elem_dict["status"] = elem.status
        if hasattr(
            elem, "operational_status"
        ):  # model-safety-ok: polymorphic ArchiMate elements
            elem_dict["status"] = elem.operational_status
        elements_data.append(elem_dict)

    # If AJAX request, return JSON
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(
            {
                "elements": elements_data,
                "pagination": {
                    "page": page,
                    "pages": pagination.pages,
                    "per_page": per_page,
                    "total": pagination.total,
                    "has_next": pagination.has_next,
                    "has_prev": pagination.has_prev,
                },
            }
        )

    initial_layer, initial_element_type = _validated_layer_filter(layer, element_type)
    presentation_config = {
        key: {**config, **LAYER_PRESENTATION[key], "key": key}
        for key, config in LAYER_CONFIG.items()
    }
    selected_key = initial_layer or "motivation"
    return render_template(
        "archimate_crud/dashboard.html",
        layer=layer,
        element_type=element_type,
        elements=elements,
        pagination=pagination,
        search=search,
        view_mode=view_mode,
        layer_config=presentation_config,
        selected_layer=presentation_config[selected_key],
        # dashboard.html is an Alpine app that fetches its own rows; without
        # these it would ignore the path it was reached by and open on the
        # default tab, showing a different layer than the URL asked for.
        initial_layer=initial_layer,
        initial_element_type=initial_element_type,
        element_field_configs=ELEMENT_FIELD_CONFIGS,
    )


def _selected_layer_for(layer):
    """The dashboard.html template's title block reads selected_layer.title
    unconditionally (`{% block title %}{{ selected_layer.title }}{% endblock %}`)
    — every render of this template needs it or the whole page 500s before any
    content renders, which is what GET .../<type>/new did (Capgemini walkthrough,
    F-18-adjacent: element creation was unreachable, not merely edit). Mirrors
    the same presentation_config lookup the dashboard() route already does."""
    presentation_config = {
        key: {**config, **LAYER_PRESENTATION[key], "key": key}
        for key, config in LAYER_CONFIG.items()
    }
    selected_key = layer if layer in presentation_config else "motivation"
    return presentation_config[selected_key]


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/new", methods=["GET", "POST"])
@login_required
def create_element(layer, element_type):
    """Create a new element"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        flash(f"Element type {element_type} is not yet supported", "warning")
        return redirect(url_for("archimate_crud.dashboard"))

    if request.method == "POST":
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()

            # Create model instance
            element = model_class()

            # Set basic fields
            if hasattr(
                element, "name"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                element.name = data.get("name", "").strip()
            elif hasattr(
                element, "title"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                element.title = data.get("name", "").strip()

            if hasattr(
                element, "description"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                element.description = data.get("description", "").strip()

            # Set layer-specific fields based on model
            _set_model_fields(element, data, model_class, element_type)

            # Auto-create ArchiMateElement if not provided
            if not element.archimate_element_id:
                archimate_element = ArchiMateElement(
                    name=element.name
                    if hasattr(element, "name")
                    else element.title,  # model-safety-ok: polymorphic ArchiMate elements
                    type=element_type,
                    layer=_canonical_layer_for_type(element_type, layer).capitalize(),
                    description=getattr(
                        element, "description", ""
                    ),  # model-safety-ok: polymorphic ArchiMate elements
                )
                db.session.add(archimate_element)
                db.session.flush()
                element.archimate_element_id = archimate_element.id

            # As-is / to-be state (ArchiMateElement.plateau), if the form set it.
            _apply_architecture_state(element, data)

            db.session.add(element)
            db.session.commit()

            if request.is_json:
                return jsonify(
                    {
                        "success": True,
                        "id": element.id,
                        "message": f"{element_type} created successfully",
                    }
                )

            flash(f"{element_type} created successfully", "success")
            return redirect(
                url_for(
                    "archimate_crud.detail_element",
                    layer=layer,
                    element_type=element_type,
                    element_id=element.id,
                )
            )

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error creating {element_type}: {str(e)}", exc_info=True
            )

            if request.is_json:
                return jsonify(
                    {"success": False, "error": "Invalid request parameters"}
                ), 400

            # f-prefix was missing, so the user was shown the literal text
            # "Error creating {element_type}." — braces and all.
            flash(f"Error creating {element_type}. Please try again.", "error")
            return render_template(
                "archimate_crud/dashboard.html",
                layer=layer,
                element_type=element_type,
                layer_config=LAYER_CONFIG,
                selected_layer=_selected_layer_for(layer),
                field_config=get_element_config(element_type),
                form_data=create_empty_form_data(element_type),
                element_field_configs=ELEMENT_FIELD_CONFIGS,
            )

    return render_template(
        "archimate_crud/dashboard.html",
        layer=layer,
        element_type=element_type,
        layer_config=LAYER_CONFIG,
        selected_layer=_selected_layer_for(layer),
        field_config=get_element_config(element_type),
        form_data=create_empty_form_data(element_type),
        element_field_configs=ELEMENT_FIELD_CONFIGS,
    )


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>")
@login_required
def detail_element(layer, element_type, element_id):
    """View/edit element details"""
    # Unknown layer (e.g. a malformed URL) would otherwise crash the template that
    # indexes layer_config by layer name; return 404 for an invalid layer.
    if layer not in LAYER_CONFIG:
        abort(404)
    model_class = MODEL_REGISTRY.get(element_type)

    element = None
    if model_class:
        element = model_class.query.get(element_id)

    # Fall back to archimate_elements table (native ArchiMate elements)
    if element is None:
        element = ArchiMateElement.query.get_or_404(element_id)
        model_class = ArchiMateElement

    # Get relationships
    relationships = _get_element_relationships(element, element_id)

    # The real archimate_elements.id to source a new relationship from (F-05(a)
    # "Add relationship" control) — a dedicated-table element reaches it via
    # archimate_element_id, same resolution _get_element_relationships uses.
    ae_id = None
    if hasattr(element, "archimate_element_id") and element.archimate_element_id:
        ae_id = element.archimate_element_id
    elif element.__class__.__name__ == "ArchiMateElement":
        ae_id = element.id

    # Auto-discover displayable fields from the model
    display_fields = _get_display_fields(element, model_class)
    external_links = CrosswalkService.get_links_for_element(ae_id or element.id)

    return render_template(
        "archimate_crud/detail.html",
        layer=layer,
        element_type=element_type,
        element=element,
        relationships=relationships,
        source_ae_id=ae_id,
        display_fields=display_fields,
        external_links=external_links,
        layer_config=LAYER_CONFIG,
    )


@archimate_crud.route(
    f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>/edit", methods=["GET", "POST"]
)
@login_required
def update_element(layer, element_type, element_id):
    """Update an element"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        flash(f"Element type {element_type} is not yet supported", "warning")
        return redirect(url_for("archimate_crud.dashboard"))

    element = model_class.query.get(element_id)
    # Fall back to archimate_elements for elements stored there directly
    _from_ae = False
    if element is None:
        element = ArchiMateElement.query.get(element_id)
        _from_ae = True
    if element is None:
        if request.is_json:
            return jsonify({"success": False, "error": "Element not found"}), 404
        from flask import abort
        abort(404)

    if request.method == "POST":
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()
            _ae_before = _archimate_element_state(element, _from_ae)

            # Update basic fields
            if hasattr(
                element, "name"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                element.name = data.get("name", "").strip()
            elif hasattr(
                element, "title"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                element.title = data.get("name", "").strip()

            if hasattr(
                element, "description"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                element.description = data.get("description", "").strip()

            if not _from_ae:
                # Update layer-specific fields only for dedicated model instances
                _set_model_fields(element, data, model_class, element_type)

                # Update ArchiMateElement if linked
                if getattr(element, "archimate_element_id", None):
                    archimate_element = ArchiMateElement.query.get(
                        element.archimate_element_id
                    )
                    if archimate_element:
                        archimate_element.name = (
                            element.name
                            if hasattr(element, "name")
                            else element.title  # model-safety-ok: polymorphic ArchiMate elements
                        )
                        archimate_element.description = getattr(
                            element, "description", ""
                        )  # model-safety-ok: polymorphic ArchiMate elements
            # As-is / to-be state (ArchiMateElement.plateau), if the form set it.
            # Works for a native ArchiMateElement (element itself) and a linked one.
            _apply_architecture_state(element, data)

            _record_element_update(_ae_before, _archimate_element_state(element, _from_ae))

            db.session.commit()

            if request.is_json:
                return jsonify(
                    {"success": True, "message": f"{element_type} updated successfully"}
                )

            flash(f"{element_type} updated successfully", "success")
            return redirect(
                url_for(
                    "archimate_crud.detail_element",
                    layer=layer,
                    element_type=element_type,
                    element_id=element.id,
                )
            )

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"Error updating {element_type}: {str(e)}", exc_info=True
            )

            if request.is_json:
                return jsonify(
                    {"success": False, "error": "Invalid request parameters"}
                ), 400

            flash("Error updating {element_type}. Please try again.", "error")

    return render_template(
        "archimate_crud/dashboard.html",
        layer=layer,
        element_type=element_type,
        element=element,
        layer_config=LAYER_CONFIG,
        selected_layer=_selected_layer_for(layer),
        element_field_configs=ELEMENT_FIELD_CONFIGS,
    )


def _archimate_element_state(element, from_ae):
    """(id, name, description, custom_properties) of the ArchiMate element behind ``element``."""
    target = element
    if not from_ae:
        linked = getattr(element, "archimate_element_id", None)
        target = ArchiMateElement.query.get(linked) if linked else None
    if target is None or not isinstance(target, ArchiMateElement):
        return None
    return {
        "id": target.id,
        "name": target.name,
        "description": target.description,
        "custom_properties": copy.deepcopy(target.custom_properties or {}),
    }


def _record_element_update(before, after):
    """Record an edit of an ArchiMate element in the organisation's audit trail.

    Same transaction as the edit; the restore-before-import screen reads these
    entries to offer later changes for applying again.
    """
    if not before or not after or before == after:
        return
    from flask import g
    from flask_login import current_user

    from app.models.audit_log import AuditLog
    from app.services.audit_log_service import AuditLogService

    changed = [k for k in ("name", "description", "custom_properties") if before.get(k) != after.get(k)]
    if not changed:
        return
    org_id = getattr(g, "current_org_id", None) or getattr(current_user, "organization_id", None)
    db.session.add(AuditLog(
        organization_id=org_id,
        user_id=getattr(current_user, "id", None),
        action="update",
        table_name="archimate_elements",
        record_id=after["id"],
        old_value={k: before[k] for k in changed},
        new_value={k: after[k] for k in changed},
        ip_address=AuditLogService._resolve_ip(),
        user_agent=(AuditLogService._resolve_ua() or None),
    ))


@archimate_crud.route(
    f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>/delete", methods=["POST"]
)
@login_required
def delete_element(layer, element_type, element_id):
    """Delete an element"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        if request.is_json:
            return jsonify(
                {"success": False, "error": "Element type not supported"}
            ), 400
        flash(f"Element type {element_type} is not yet supported", "warning")
        return redirect(url_for("archimate_crud.dashboard"))

    element = model_class.query.get(element_id)
    # Fall back to archimate_elements for elements stored there directly
    _from_ae = False
    if element is None:
        element = ArchiMateElement.query.get(element_id)
        _from_ae = True
    if element is None:
        if request.is_json:
            return jsonify({"success": False, "error": "Element not found"}), 404
        from flask import abort
        abort(404)

    try:
        # DEF-067, Capgemini dry-run pass 3: this deleted the ArchiMateElement
        # mirror (and, for a dedicated model, the row itself) with no cleanup
        # of ArchiMateRelationship rows still pointing at it.
        # archimate_relationships.source_id/target_id carry ON DELETE NO
        # ACTION, so any element with a relationship raised an
        # IntegrityError on commit — caught below and reported as the
        # actively misleading "Invalid request parameters" (this was never
        # a request-parameter problem). Clear relationships referencing
        # either id first so delete is robust regardless of how the
        # relationship got there.
        archimate_element = None
        if not _from_ae:
            if getattr(element, "archimate_element_id", None):
                archimate_element = ArchiMateElement.query.get(element.archimate_element_id)
        else:
            archimate_element = element

        ae_ids = [aid for aid in (element_id if _from_ae else None, getattr(archimate_element, "id", None)) if aid]
        if ae_ids:
            ArchiMateRelationship.query.filter(
                db.or_(
                    ArchiMateRelationship.source_id.in_(ae_ids),
                    ArchiMateRelationship.target_id.in_(ae_ids),
                )
            ).delete(synchronize_session=False)

        # DEF-067's actual live failure (found in server logs, and only
        # reproduced by clicking the real duplicated production element —
        # see DEF-004): the dashboard card for a DEF-004-duplicated element
        # links to the ArchiMateElement's own id, not its dedicated-model
        # twin's id, so model_class.query.get(element_id) returns None and
        # this falls into the `_from_ae` branch — which deleted ONLY the
        # archimate_elements row. The dedicated row (a *different* id, e.g.
        # stakeholders.id=2 with archimate_element_id=1287) was never found
        # or deleted, and it still held stakeholders_archimate_element_id_fkey
        # (ON DELETE NO ACTION) pointing at the row this branch just tried
        # to delete. Reordering session.delete() calls and even an explicit
        # flush did not help THIS path, because the dedicated row was never
        # looked up here at all. When falling back to the ArchiMateElement,
        # also find and delete any dedicated-model row of the same type that
        # mirrors it (the DEF-004 duplicate), before deleting the
        # ArchiMateElement row itself.
        if _from_ae:
            dedicated_model = MODEL_REGISTRY.get(getattr(element, "type", None))
            if dedicated_model is not None and hasattr(dedicated_model, "archimate_element_id"):
                duplicate_rows = dedicated_model.query.filter_by(
                    archimate_element_id=element.id
                ).all()
                for row in duplicate_rows:
                    db.session.delete(row)
                if duplicate_rows:
                    db.session.flush()

        db.session.delete(element)
        db.session.flush()
        if not _from_ae and archimate_element:
            db.session.delete(archimate_element)
        db.session.commit()

        if request.is_json:
            return jsonify(
                {"success": True, "message": f"{element_type} deleted successfully"}
            )

        flash(f"{element_type} deleted successfully", "success")
        return redirect(
            url_for(
                "archimate_crud.list_elements", layer=layer, element_type=element_type
            )
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Error deleting {element_type} {element_id}: {str(e)}", exc_info=True
        )

        # Never surface the raw exception (DEF-003: no toast may contain
        # psycopg2/SQL:/sqlalche) — the real detail goes to the log above.
        safe_message = f"Could not delete this {element_type}. It may still be referenced elsewhere in the model."

        if request.is_json:
            return jsonify({"success": False, "error": safe_message}), 400

        flash(safe_message, "error")
        return redirect(
            url_for(
                "archimate_crud.detail_element",
                layer=layer,
                element_type=element_type,
                element_id=element_id,
            )
        )


@archimate_crud.route("/api/archimate/validate", methods=["POST"])
@login_required
def validate_archimate_model():
    """Run full ArchiMate 3.2 metamodel validation across all elements and relationships."""
    from app.modules.architecture.services.archimate_validation_service import ArchiMateValidationService
    service = ArchiMateValidationService()
    results = service.validate_all()
    return jsonify(results)


@archimate_crud.route("/api/archimate/elements/<int:element_id>/validate", methods=["GET"])
@login_required
def validate_archimate_element(element_id):
    """Validate a single ArchiMate element against ArchiMate 3.2 metamodel rules."""
    from app.modules.architecture.services.archimate_validation_service import ArchiMateValidationService
    element = ArchiMateElement.query.get_or_404(element_id)
    service = ArchiMateValidationService()
    issues = service.validate_element(element)
    return jsonify({'issues': issues, 'valid': len(issues) == 0})


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/bulk-delete", methods=["POST"])
@login_required
def bulk_delete(layer, element_type):
    """Bulk delete elements"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        return jsonify({"success": False, "error": "Element type not supported"}), 400

    data = request.get_json()
    element_ids = data.get("ids", [])

    if not element_ids:
        return jsonify({"success": False, "error": "No elements selected"}), 400

    try:
        deleted_count = 0
        for element_id in element_ids:
            element = model_class.query.get(element_id)
            if element:
                # Delete linked ArchiMateElement
                if element.archimate_element_id:
                    archimate_element = ArchiMateElement.query.get(
                        element.archimate_element_id
                    )
                    if archimate_element:
                        db.session.delete(archimate_element)

                db.session.delete(element)
                deleted_count += 1

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"{deleted_count} {element_type}(s) deleted successfully",
                "deleted_count": deleted_count,
            }
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Error bulk deleting {element_type}: {str(e)}", exc_info=True
        )
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/export", methods=["GET"])
@login_required
def export_elements(layer, element_type):
    """Export elements to JSON/CSV"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        flash(f"Element type {element_type} is not yet supported", "warning")
        return redirect(url_for("archimate_crud.dashboard"))

    format_type = request.args.get("format", "json")
    element_ids = request.args.getlist("ids")

    if element_ids:
        elements = model_class.query.filter(model_class.id.in_(element_ids)).all()
    else:
        elements = model_class.query.limit(5000).all()

    if format_type == "json":
        data = []
        for elem in elements:
            elem_dict = _element_to_dict(elem)
            data.append(elem_dict)

        response = jsonify(data)
        response.headers["Content-Disposition"] = (
            f"attachment; filename={element_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        return response

    # CSV export
    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    # Write header
    if elements:
        first_elem = elements[0]
        headers = ["id", "name"]
        if hasattr(
            first_elem, "description"
        ):  # model-safety-ok: polymorphic ArchiMate elements
            headers.append("description")
        writer.writerow(headers)

        # Write data
        for elem in elements:
            row = [
                elem.id,
                getattr(elem, "name", getattr(elem, "title", "")),
            ]  # model-safety-ok: polymorphic ArchiMate elements
            if hasattr(
                elem, "description"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                row.append(
                    getattr(elem, "description", "")
                )  # model-safety-ok: polymorphic ArchiMate elements
            writer.writerow(row)

    output.seek(0)
    response = current_app.response_class(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f"attachment; filename={element_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return response


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/ai-generate", methods=["POST"])
@login_required
def ai_generate(layer, element_type):
    """AI-powered element generation from documents/internet"""
    try:
        data = request.get_json()
        prompt = data.get("prompt", "")
        context = data.get("context", {})

        if not prompt:
            return jsonify({"success": False, "error": "Prompt is required"}), 400

        # Use AI generation service
        ai_service = AIGenerationService()
        result = ai_service.generate_element(
            layer=layer, element_type=element_type, prompt=prompt, context=context
        )

        return jsonify({"success": True, "data": result})

    except Exception as e:
        current_app.logger.error(f"Error in AI generation: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>/relationships")
@login_required
def element_relationships(layer, element_type, element_id):
    """Get relationships for an element"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        return jsonify({"success": False, "error": "Element type not supported"}), 400

    element = model_class.query.get_or_404(element_id)
    relationships = _get_element_relationships(element, element_id)

    return jsonify({"success": True, "relationships": relationships})


@archimate_crud.route("/api/elements/<int:element_id>/detail")
@login_required
def api_element_detail(element_id):
    """ARC-006: Return full detail JSON for a single ArchiMate element.

    Returns element fields, outgoing/incoming relationships, and solution linkages.
    """
    element = ArchiMateElement.query.get_or_404(element_id)

    outgoing = []
    for rel in ArchiMateRelationship.query.filter_by(source_id=element_id).limit(50).all():
        target = ArchiMateElement.query.get(rel.target_id)
        outgoing.append({
            "id": rel.id,
            "type": rel.type or "",
            "target_id": rel.target_id,
            "target_name": target.name if target else f"#{rel.target_id}",
            "target_type": target.type if target else "",
            "target_layer": target.layer if target else "",
        })

    incoming = []
    for rel in ArchiMateRelationship.query.filter_by(target_id=element_id).limit(50).all():
        source = ArchiMateElement.query.get(rel.source_id)
        incoming.append({
            "id": rel.id,
            "type": rel.type or "",
            "source_id": rel.source_id,
            "source_name": source.name if source else f"#{rel.source_id}",
            "source_type": source.type if source else "",
            "source_layer": source.layer if source else "",
        })

    solutions = []
    try:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.models.solution_models import Solution
        for sae in SolutionArchiMateElement.query.filter_by(element_id=element_id).limit(20).all():
            sol = Solution.query.get(sae.solution_id) if sae.solution_id else None
            solutions.append({
                "solution_id": sae.solution_id,
                "solution_name": sol.name if sol else f"Solution #{sae.solution_id}",
                "element_role": sae.element_role or "",
                "created_at": sae.created_at.isoformat() if sae.created_at else None,
            })
    except Exception as e:
        current_app.logger.warning(f"Could not load solution linkages for element {element_id}: {e}")

    return jsonify({
        "id": element.id,
        "name": element.name,
        "type": element.type,
        "layer": element.layer,
        "description": element.description or "",
        "building_block_type": element.building_block_type or "",
        "outgoing": outgoing,
        "incoming": incoming,
        "solutions": solutions,
    })


@archimate_crud.route("/api/elements/<int:element_id>", methods=["PATCH"])
@login_required
def api_element_patch(element_id):
    """ARC-006: Partial update of an ArchiMate element's core fields."""
    element = ArchiMateElement.query.get_or_404(element_id)
    data = request.get_json() or {}

    allowed = {"name", "type", "layer", "description", "element_type"}
    for field in allowed:
        if field == "element_type":
            if "element_type" in data:
                element.type = data["element_type"]
        elif field in data and hasattr(element, field):
            setattr(element, field, data[field])

    try:
        db.session.commit()
        return jsonify({"success": True, "id": element.id})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"PATCH element {element_id} failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500




_ARCHITECTURE_STATES = ("Baseline", "Target", "Transition")


def _apply_architecture_state(element, data):
    """Set an element's as-is/to-be state (ArchiMateElement.plateau) from the form.

    Baseline = As-Is, Target = To-Be, Transition = interim. This is what lets a
    transformation programme hold a baseline architecture and a target
    architecture as distinct states of the model — before this, the plateau
    column existed but no create/edit path ever wrote it.

    Set-only: an empty or unrecognised value is ignored, never a clear. The edit
    modal does not pre-load the current state, so treating blank as "clear" would
    silently wipe an element's state on any unrelated edit — set-only makes that
    impossible. Applies to a native ArchiMateElement directly, or to the element
    linked from a domain model.
    """
    raw = (data.get("architecture_state") or "").strip()
    if raw not in _ARCHITECTURE_STATES:
        return
    if isinstance(element, ArchiMateElement):
        element.togaf_plateau = raw
        return
    ae_id = getattr(element, "archimate_element_id", None)
    if ae_id:
        ae = ArchiMateElement.query.get(ae_id)
        if ae is not None:
            ae.togaf_plateau = raw


def _set_model_fields(element, data, model_class, element_type=None):
    """Set model-specific fields from data.

    Applied fields come from two merged sources: the legacy ``field_mappings``
    table below, and (when ``element_type`` is given) the typed field names
    declared in ``services/field_configs.py`` for that type — the same config
    the create-modal renders fields from, so a field the UI can show is also a
    field this will persist. Either way a field is only ever set when the
    model actually declares the attribute (``hasattr``) and the caller
    actually posted it (``field in data``): an unknown/renamed field name in
    the payload is silently ignored here, never a 500. Shared verbatim by
    both create (POST /<layer>/<element_type>/new) and update
    (POST /<layer>/<element_type>/<id>/edit) so typed fields behave the same
    on both paths.
    """
    # Common fields
    common_fields = ["description", "status", "operational_status"]
    for field in common_fields:
        if (
            hasattr(element, field) and field in data
        ):  # model-safety-ok: polymorphic ArchiMate elements - not all models have all common fields
            setattr(element, field, data[field])

    # Model-specific field mapping
    field_mappings = {
        Driver: [
            "driver_type",
            "source",
            "urgency",
            "impact_scope",
            "impact_magnitude",
        ],
        Goal: [
            "goal_type",
            "category",
            "time_horizon",
            "target_value",
            "current_value",
        ],
        BusinessActor: ["actor_type", "location", "headcount", "cost_center"],
        BusinessRole: ["role_type", "authorization_level", "experience_years_required"],
        BusinessService: [
            "service_type",
            "business_criticality",
            "sla_availability_target",
        ],
        BusinessObject: ["data_classification", "contains_pii", "gdpr_scope"],
        StrategyResource: ["resource_type", "strategic_value", "competitive_advantage"],
        CourseOfAction: [
            "action_type",
            "strategic_theme",
            "risk_level",
            "progress_percentage",
        ],
        Stakeholder: [
            "stakeholder_type",
            "role",
            "department",
            "power_level",
            "interest_level",
        ],
        MissingBusinessCollaboration: [
            "collaboration_type",
            "purpose",
            "scope",
            "meeting_frequency",
        ],
        MissingBusinessInterface: [
            "interface_type",
            "access_method",
            "availability",
            "authentication_method",
        ],
        MissingBusinessInteraction: [
            "interaction_type",
            "trigger",
            "outcome",
            "frequency",
        ],
        Product: ["product_type", "product_category", "target_market", "pricing_model"],
    }

    allowed_fields = set(field_mappings.get(model_class, []))
    if element_type:
        allowed_fields.update(get_element_field_names(element_type))

    for field in allowed_fields:
        if (
            hasattr(element, field) and field in data
        ):  # model-safety-ok: polymorphic ArchiMate elements
            setattr(element, field, data[field])


def _get_element_relationships(element, element_id):
    """Get all relationships for an element"""
    relationships = []

    # Resolve the archimate_element_id to use for relationship lookup
    ae_id = None
    if hasattr(element, "archimate_element_id") and element.archimate_element_id:
        ae_id = element.archimate_element_id  # dedicated model linked to archimate_elements
    elif element.__class__.__name__ == "ArchiMateElement":
        ae_id = element.id  # element IS the archimate_elements row

    # Get ArchiMate relationships
    if ae_id:  # model-safety-ok: polymorphic ArchiMate elements
        # Outgoing relationships
        outgoing = ArchiMateRelationship.query.filter_by(
            source_id=ae_id
        ).all()
        for rel in outgoing:
            target_element = ArchiMateElement.query.get(rel.target_id)
            if target_element:
                relationships.append(
                    {
                        "type": rel.type,
                        "direction": "outgoing",
                        "target": {
                            "id": target_element.id,
                            "name": target_element.name,
                            "type": target_element.type,
                            "layer": target_element.layer,
                        },
                    }
                )

        # Incoming relationships
        incoming = ArchiMateRelationship.query.filter_by(
            target_id=ae_id
        ).all()
        for rel in incoming:
            source_element = ArchiMateElement.query.get(rel.source_id)
            if source_element:
                relationships.append(
                    {
                        "type": rel.type,
                        "direction": "incoming",
                        "source": {
                            "id": source_element.id,
                            "name": source_element.name,
                            "type": source_element.type,
                            "layer": source_element.layer,
                        },
                    }
                )

    return relationships


def _element_to_dict(element):
    """Convert element to dictionary"""
    elem_dict = {
        "id": element.id,
        "name": getattr(
            element, "name", getattr(element, "title", "Unnamed")
        ),  # model-safety-ok: polymorphic ArchiMate elements
    }

    # Add all attributes
    for key in element.__table__.columns.keys():
        if key != "id":
            value = getattr(
                element, key, None
            )  # model-safety-ok: dynamic column iteration via __table__.columns
            if value is not None:
                # Handle datetime
                if isinstance(value, datetime):
                    elem_dict[key] = value.isoformat()
                # Handle Decimal
                elif hasattr(value, "__float__"):
                    elem_dict[key] = float(value)
                else:
                    elem_dict[key] = value

    return elem_dict


# ---------------------------------------------------------------------------
# Architecture Repository Health Scorecard  (ENT-115)
# ---------------------------------------------------------------------------

@archimate_crud.route("/health")
@login_required
def repository_health():
    """Redirect to dashboard with health panel open."""
    return redirect(url_for("archimate_crud.dashboard", panel="health"), code=302)


@archimate_crud.route("/api/traceability/sankey")
@login_required
def api_traceability_sankey():
    """Return traceability data shaped for D3 Sankey diagram.

    Queries archimate_relationships directly (not the traceability service)
    to show real cross-layer element connections.
    """
    try:
        return _build_traceability_sankey_response()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Traceability sankey API error: %s", e)
        return jsonify({"error": "Could not build the traceability diagram"}), 500


def _build_traceability_sankey_response():
    from app.models.archimate_core import ArchiMateRelationship

    # Load all relationships with their source and target elements
    rels = db.session.query(
        ArchiMateRelationship.source_id,
        ArchiMateRelationship.target_id,
        ArchiMateRelationship.type,
    ).all()

    if not rels:
        return jsonify({"nodes": [], "links": [], "layer_counts": {}})

    # Collect all element IDs
    elem_ids = set()
    for src_id, tgt_id, _ in rels:
        elem_ids.add(src_id)
        elem_ids.add(tgt_id)

    # Load element details
    elements = {
        e.id: e
        for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(elem_ids)).all()
    }

    # Build nodes
    nodes_map = {}
    for eid, e in elements.items():
        layer = (e.layer or "application").lower()
        nodes_map[eid] = {
            "id": eid,
            "name": e.name or "",
            "element_type": e.type or "",
            "layer": layer,
        }

    # Build links (only between elements in DIFFERENT layers for Sankey flow)
    links = []
    seen = set()
    for src_id, tgt_id, rel_type in rels:
        src = elements.get(src_id)
        tgt = elements.get(tgt_id)
        if not src or not tgt:
            continue
        src_layer = (src.layer or "").lower()
        tgt_layer = (tgt.layer or "").lower()
        if src_layer == tgt_layer:
            continue  # Skip same-layer relationships for Sankey
        pair = (src_id, tgt_id)
        if pair in seen:
            continue
        seen.add(pair)
        links.append({"source": src_id, "target": tgt_id, "value": 1})

    # Layer counts
    layer_counts = {}
    for node in nodes_map.values():
        item = node["layer"]
        layer_counts[item] = layer_counts.get(item, 0) + 1

    return jsonify({
        "nodes": list(nodes_map.values()),
        "links": links,
        "layer_counts": layer_counts,
    })


@archimate_crud.route("/api/health-scorecard")
@login_required
def api_health_scorecard():
    """
    Return a JSON health scorecard for the ArchiMate repository.

    7 measurable tests that assess whether the repository is genuinely usable
    for architecture governance — not just populated with orphaned elements.
    """
    try:
        from sqlalchemy import func, text
        from app.models.application_portfolio import ApplicationComponent as AppComponent
        from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship as InfRel

        # ------------------------------------------------------------------ #
        # Fetch raw counts (union of legacy + inference relationship tables)  #
        # ------------------------------------------------------------------ #
        # Corrected 2026-10-08 (hotfix round 2): the previous version of this
        # comment claimed with_loader_criteria "only applies to ENTITY queries",
        # so a column query like func.count(Model.id) would leak every
        # organisation's rows unless wrapped in _scope(). Tested directly
        # against this app's own do_orm_execute listener: that's not the real
        # line. with_loader_criteria fires for any ORM-aware query through a
        # mapped attribute on a TenantMixin model -- func.count(ArchiMateElement.id)
        # included -- whether it selects the full entity or just a column, so
        # total_elements/legacy_rels below are already scoped without _scope();
        # it's kept anyway for explicitness, not because it's load-bearing. What
        # with_loader_criteria genuinely does not reach is (a) a model that isn't
        # a TenantMixin in the first place -- InfRel carries no organization_id,
        # which is why its counts below join through the (scoped) source
        # ArchiMateElement instead -- and (b) raw SQL / text(), which bypasses the
        # ORM layer entirely regardless of column-vs-entity shape -- see the
        # has_plateau and cross-layer-fallback raw queries further down, which
        # both need their own explicit organization_id predicate for that reason.
        from flask import g

        _org = getattr(g, "current_org_id", None)

        def _scope(query, model):
            return query.filter(model.organization_id == _org) if _org is not None else query

        total_elements = _scope(
            db.session.query(func.count(ArchiMateElement.id)), ArchiMateElement
        ).scalar() or 0
        legacy_rels = _scope(
            db.session.query(func.count(ArchiMateRelationship.id)), ArchiMateRelationship
        ).scalar() or 0
        # InfRel carries no organization_id (see architecture_inference_relationship.py);
        # scope it by joining to the source element, which does. source_id/target_id are
        # expected to agree on organisation for any row where both elements still exist and
        # belong to one org -- this join does not verify that agreement, it is a read-side
        # count fix only.
        inference_rels = _scope(
            db.session.query(func.count(InfRel.id)).join(
                ArchiMateElement, ArchiMateElement.id == InfRel.source_id
            ),
            ArchiMateElement,
        ).scalar() or 0
        total_rels = legacy_rels + inference_rels

        # Elements per layer
        layer_rows = (
            _scope(
                db.session.query(ArchiMateElement.layer, func.count(ArchiMateElement.id)),
                ArchiMateElement,
            )
            .group_by(ArchiMateElement.layer)
            .all()
        )
        layer_counts = {(r[0] or "unknown"): r[1] for r in layer_rows}

        EXPECTED_LAYERS = ["motivation", "strategy", "business", "application", "technology", "implementation"]
        # Also count PascalCase layers from newer elements
        for lay in ["Motivation", "Strategy", "Business", "Application", "Technology", "Implementation"]:
            if lay in layer_counts:
                lower_lay = lay.lower()
                layer_counts[lower_lay] = layer_counts.get(lower_lay, 0) + layer_counts.pop(lay)

        LAYER_THRESHOLD = 20

        # Elements that have any relationship (source or target) — check both tables
        legacy_ids = set()
        for row in _scope(
            db.session.query(ArchiMateRelationship.source_id), ArchiMateRelationship
        ).all():
            legacy_ids.add(row[0])
        for row in _scope(
            db.session.query(ArchiMateRelationship.target_id), ArchiMateRelationship
        ).all():
            legacy_ids.add(row[0])
        # InfRel carries no organization_id -- scope via the same join-through-
        # source-element pattern as inference_rels above (source_id and target_id
        # are expected to agree on organisation; this does not verify that).
        for row in _scope(
            db.session.query(InfRel.source_id).join(
                ArchiMateElement, ArchiMateElement.id == InfRel.source_id
            ),
            ArchiMateElement,
        ).all():
            legacy_ids.add(row[0])
        for row in _scope(
            db.session.query(InfRel.target_id).join(
                ArchiMateElement, ArchiMateElement.id == InfRel.source_id
            ),
            ArchiMateElement,
        ).all():
            legacy_ids.add(row[0])
        connected_count = (
            db.session.query(func.count(ArchiMateElement.id))
            .filter(ArchiMateElement.id.in_(legacy_ids))
            .scalar() or 0
        ) if legacy_ids else 0

        # Relationship types — union both tables
        rel_type_rows = (
            db.session.query(ArchiMateRelationship.type, func.count(ArchiMateRelationship.id))
            .group_by(ArchiMateRelationship.type)
            .all()
        )
        # Same join-through-ArchiMateElement-plus-_scope() pattern as inference_rels
        # above: InfRel carries no organization_id of its own.
        inf_type_rows = (
            _scope(
                db.session.query(InfRel.rel_type, func.count(InfRel.id)).join(
                    ArchiMateElement, ArchiMateElement.id == InfRel.source_id
                ),
                ArchiMateElement,
            )
            .group_by(InfRel.rel_type)
            .all()
        )
        rel_by_type = {}
        for r in rel_type_rows:
            rel_by_type[r[0] or "unknown"] = rel_by_type.get(r[0] or "unknown", 0) + r[1]
        for r in inf_type_rows:
            rel_by_type[r[0] or "unknown"] = rel_by_type.get(r[0] or "unknown", 0) + r[1]
        STRUCTURAL_TYPES = {"composition", "aggregation", "association"}
        semantic_rels = sum(v for k, v in rel_by_type.items() if (k or "").lower() not in STRUCTURAL_TYPES)

        # Cross-layer relationship pairs (excluding composition/aggregation within same layer)
        (db.session.query(
                ArchiMateElement.layer.label("src_layer"),
                func.count(ArchiMateRelationship.id).label("cnt"),
            )
            .join(ArchiMateRelationship, ArchiMateRelationship.source_id == ArchiMateElement.id)
            .join(
                ArchiMateElement.__table__.alias("tgt"),
                ArchiMateRelationship.target_id == db.literal_column("tgt.id"),
            )
            .filter(
                ArchiMateElement.layer != db.literal_column("tgt.layer"),
                ArchiMateRelationship.type.notin_(["composition", "aggregation"]),
            )
            .group_by(ArchiMateElement.layer)
            .all())
        # Fallback: count relationships crossing layers via raw SQL for reliability
        try:
            # Both union halves must carry the same org predicate as every other
            # count in this function -- raw SQL is not reached by with_loader_criteria
            # at all, scoped or not, so leaving either half unfiltered leaks every
            # organisation's cross-layer pairs to whoever is signed in. The predicate
            # is a static part of the query text (never built from the org id, which
            # is only ever bound as :org) so this isn't the string-built-SQL shape
            # bandit's B608 flags -- `:org IS NULL` makes the clause a no-op the same
            # way the old conditional-fragment version did, without an f-string.
            _cross_sql = """
                SELECT src_layer, tgt_layer, SUM(cnt) AS cnt FROM (
                    SELECT LOWER(COALESCE(src.layer,'?')) AS src_layer,
                           LOWER(COALESCE(tgt.layer,'?')) AS tgt_layer,
                           COUNT(*) AS cnt
                    FROM archimate_relationships r
                    JOIN archimate_elements src ON r.source_id = src.id
                    JOIN archimate_elements tgt ON r.target_id = tgt.id
                    WHERE LOWER(COALESCE(src.layer,'?')) <> LOWER(COALESCE(tgt.layer,'?'))
                      AND LOWER(COALESCE(r.type,'')) NOT IN ('composition','aggregation')
                      AND (:org IS NULL OR src.organization_id = :org)
                    GROUP BY 1, 2
                    UNION ALL
                    SELECT LOWER(COALESCE(src.layer,'?')) AS src_layer,
                           LOWER(COALESCE(tgt.layer,'?')) AS tgt_layer,
                           COUNT(*) AS cnt
                    FROM architecture_inference_relationship r
                    JOIN archimate_elements src ON r.source_id = src.id
                    JOIN archimate_elements tgt ON r.target_id = tgt.id
                    WHERE LOWER(COALESCE(src.layer,'?')) <> LOWER(COALESCE(tgt.layer,'?'))
                      AND LOWER(COALESCE(r.rel_type,'')) NOT IN ('composition','aggregation')
                      AND (:org IS NULL OR src.organization_id = :org)
                    GROUP BY 1, 2
                ) combined
                GROUP BY src_layer, tgt_layer
                ORDER BY cnt DESC
            """
            cross_pairs_rows = db.session.execute(text(_cross_sql), {"org": _org}).fetchall()
            cross_pairs = [{"from": row[0] or "?", "to": row[1] or "?", "count": row[2]} for row in cross_pairs_rows]
        except Exception:
            db.session.rollback()
            cross_pairs = []

        # Portfolio integration: how many app names appear as archimate elements
        try:
            app_names = [r[0].lower() for r in db.session.query(AppComponent.name).limit(500).all() if r[0]]
            archimate_names = [r[0].lower() for r in db.session.query(ArchiMateElement.name).all() if r[0]]
            matched = sum(1 for n in app_names if n in archimate_names)
            total_apps = len(app_names)
        except Exception:
            db.session.rollback()
            app_names, archimate_names, matched, total_apps = [], [], 0, 0

        # FK links (applications with archimate element references)
        try:
            fk_linked = (
                db.session.query(func.count(AppComponent.id))
                .filter(AppComponent.archimate_element_id.isnot(None))
                .scalar() or 0
            ) if hasattr(AppComponent, "archimate_element_id") else 0
        except Exception:
            db.session.rollback()
            fk_linked = 0

        # Motivation integrity: Driver→Goal linkage, Goal→Requirement linkage
        # Uses ArchiMateElement type filter (Driver/Goal models may not exist)
        # Checks BOTH legacy ArchiMateRelationship AND inference relationships
        try:
            driver_ids_q = db.session.query(ArchiMateElement.id).filter(
                ArchiMateElement.type.in_(["Driver", "driver"])
            )
            goal_ids_q = db.session.query(ArchiMateElement.id).filter(
                ArchiMateElement.type.in_(["Goal", "goal"])
            )
            driver_count = driver_ids_q.count()
            goal_count = goal_ids_q.count()

            driver_ids = driver_ids_q
            goal_ids = goal_ids_q

            # Drivers linked to Goals via legacy table
            dg_legacy = (
                db.session.query(func.count(func.distinct(ArchiMateRelationship.source_id)))
                .filter(ArchiMateRelationship.source_id.in_(driver_ids))
                .filter(ArchiMateRelationship.target_id.in_(goal_ids))
                .scalar() or 0
            )
            # Also check inference table (source_id links to Goal elements)
            dg_inference = (
                db.session.query(func.count(func.distinct(InfRel.source_id)))
                .filter(InfRel.source_id.in_(driver_ids))
                .filter(InfRel.target_id.in_(goal_ids))
                .scalar() or 0
            )
            drivers_linked = dg_legacy + dg_inference

            # Goals linked to Requirements
            req_elements = db.session.query(ArchiMateElement.id).filter(
                ArchiMateElement.type.ilike("%requirement%")
            )
            # Also include Outcome and Capability as valid downstream (engine creates these)
            outcome_cap = db.session.query(ArchiMateElement.id).filter(
                ArchiMateElement.type.in_(["Outcome", "Capability", "outcome", "capability"])
            )
            valid_targets = req_elements.union(outcome_cap)

            gr_legacy = (
                db.session.query(func.count(func.distinct(ArchiMateRelationship.source_id)))
                .filter(ArchiMateRelationship.source_id.in_(goal_ids))
                .filter(ArchiMateRelationship.target_id.in_(valid_targets))
                .scalar() or 0
            )
            gr_inference = (
                db.session.query(func.count(func.distinct(InfRel.source_id)))
                .filter(InfRel.source_id.in_(goal_ids))
                .filter(InfRel.target_id.in_(valid_targets))
                .scalar() or 0
            )
            goals_linked = gr_legacy + gr_inference
        except Exception:
            db.session.rollback()
            driver_count, goal_count, drivers_linked, goals_linked = 0, 0, 0, 0

        # Implementation & Migration layer element types
        im_types = ["WorkPackage", "Plateau", "Gap", "Deliverable"]
        im_counts = {}
        for etype in im_types:
            try:
                im_counts[etype] = (
                    db.session.query(func.count(ArchiMateElement.id))
                    .filter(ArchiMateElement.type.ilike(f"%{etype}%"))
                    .scalar() or 0
                )
            except Exception:
                db.session.rollback()
                im_counts[etype] = 0

        # Elements with plateau/lifecycle field set (used as lifecycle status proxy).
        # The query string was previously built but never executed, so has_plateau
        # was undefined -> NameError at the Test 2 computation below.
        from sqlalchemy import text as _sa_text

        has_plateau = 0
        try:
            # Raw SQL, same as the cross-layer fallback below -- never reached by
            # with_loader_criteria, so the org predicate has to be added here by hand.
            _plateau_sql = (
                "SELECT COUNT(*) FROM archimate_elements "
                "WHERE plateau IS NOT NULL AND plateau != ''"
            )
            _plateau_params = {}
            if _org is not None:
                _plateau_sql += " AND organization_id = :org"
                _plateau_params["org"] = _org
            has_plateau = (
                db.session.execute(_sa_text(_plateau_sql), _plateau_params).scalar()
                or 0
            )
        except Exception as exc:
            db.session.rollback()
            has_plateau = 0
            logger.debug("plateau count unavailable in api_health_scorecard: %s", exc)

        # ------------------------------------------------------------------ #
        # Compute tests                                                        #
        # ------------------------------------------------------------------ #

        # Test 1: Element Coverage — 5 of 6 layers must have 20+ elements
        layer_detail = []
        layers_passing = 0
        for lay in EXPECTED_LAYERS:
            cnt = layer_counts.get(lay, 0)
            passes = cnt >= LAYER_THRESHOLD
            if passes:
                layers_passing += 1
            layer_detail.append({"layer": lay, "count": cnt, "pass": passes})
        t1_pass = layers_passing >= 5
        t1 = {
            "pass": t1_pass,
            "metric": f"{layers_passing}/6",
            "total_elements": total_elements,
            "detail": layer_detail,
        }

        # Test 2: Lifecycle Status — 80% of elements with plateau assigned
        lifecycle_pct = round(has_plateau / total_elements * 100) if total_elements else 0
        t2_pass = lifecycle_pct >= 80
        t2 = {
            "pass": t2_pass,
            "metric": f"{lifecycle_pct}%",
            "value": lifecycle_pct,
            "detail": {"has_status": has_plateau, "total": total_elements},
        }

        # Test 3: Relationship Density — <30% orphans AND >30% semantic rels
        orphan_count = total_elements - connected_count
        orphan_pct = round(orphan_count / total_elements * 100) if total_elements else 100
        semantic_pct = round(semantic_rels / total_rels * 100) if total_rels else 0
        avg_per_elem = round(total_rels / total_elements, 1) if total_elements else 0
        sub_orphan = {"value": orphan_pct, "pass": orphan_pct < 30}
        sub_semantic = {"value": semantic_pct, "pass": semantic_pct > 30}
        t3_pass = sub_orphan["pass"] and sub_semantic["pass"]
        t3 = {
            "pass": t3_pass,
            "metric": f"{avg_per_elem} rels/elem",
            "detail": {
                "total_relationships": total_rels,
                "avg_per_element": avg_per_elem,
                "sub_tests": {"orphan_rate": sub_orphan, "semantic_ratio": sub_semantic},
            },
        }

        # Test 4: Cross-Layer Traceability — 4+ semantic cross-layer pairs
        t4_pass = len(cross_pairs) >= 4
        t4 = {
            "pass": t4_pass,
            "metric": f"{len(cross_pairs)} pairs",
            "detail": {"pairs": cross_pairs},
        }

        # Test 5: Portfolio Integration — 50% of apps matched in ArchiMate
        portf_pct = round(matched / total_apps * 100) if total_apps else 0
        t5_pass = portf_pct >= 50
        t5 = {
            "pass": t5_pass,
            "metric": f"{portf_pct}%",
            "value": portf_pct,
            "detail": {
                "total_apps": total_apps,
                "matched_by_name": matched,
                "linked_by_fk": fk_linked,
            },
        }

        # Test 6: Motivation Integrity — Driver→Goal ≥70%, Goal→Req ≥50%
        dg_pct = round(drivers_linked / driver_count * 100) if driver_count else 0
        gr_pct = round(goals_linked / goal_count * 100) if goal_count else 0
        sub_dg = {"linked": drivers_linked, "drivers": driver_count, "pct": dg_pct, "pass": dg_pct >= 70}
        sub_gr = {"linked": goals_linked, "goals": goal_count, "pct": gr_pct, "pass": gr_pct >= 50}
        t6_pass = sub_dg["pass"] and sub_gr["pass"]
        t6 = {
            "pass": t6_pass,
            "metric": f"{dg_pct}% / {gr_pct}%",
            "detail": {"sub_tests": {"driver_goal": sub_dg, "goal_req": sub_gr}},
        }

        # Test 7: Implementation & Migration — 10+ IM elements
        im_total = sum(im_counts.values())
        t7_pass = im_total >= 10
        t7 = {
            "pass": t7_pass,
            "metric": f"{im_total} elements",
            "detail": {
                "type_counts": im_counts,
                "required": {"workpackage": 2, "plateau": 2, "gap": 1, "deliverable": 2},
            },
        }

        # ------------------------------------------------------------------ #
        # Overall grade                                                        #
        # ------------------------------------------------------------------ #
        tests_passing = sum(1 for t in [t1, t2, t3, t4, t5, t6, t7] if t["pass"])
        if tests_passing == 7:
            grade, grade_label = "solid", "Solid Foundation"
        elif tests_passing >= 5:
            grade, grade_label = "usable", "Usable"
        elif tests_passing >= 3:
            grade, grade_label = "foundation", "Foundation Only"
        else:
            grade, grade_label = "not_ready", "Not Yet Ready"

        return jsonify({
            "score": tests_passing,
            "total": 7,
            "grade": grade,
            "grade_label": grade_label,
            "tests": {
                "element_coverage": t1,
                "lifecycle_status": t2,
                "relationship_density": t3,
                "cross_layer_trace": t4,
                "portfolio_integration": t5,
                "motivation_integrity": t6,
                "im_layer": t7,
            },
        })

    except Exception as e:
        current_app.logger.error("Health scorecard error: %s", e, exc_info=True)
        db.session.rollback()
        return jsonify({"error": "Failed to compute health scorecard", "detail": str(e)}), 500


@archimate_crud.route("/api/health-scorecard/repair", methods=["POST"])
@login_required
def api_health_scorecard_repair():
    """Run inference engine repair for a specific health test failure.

    Request JSON: {"test": "motivation_integrity"|"relationship_density"|"cross_layer_trace"|...}
    Response: {"repaired": int, "elements_created": [...], "before": {...}, "after": {...}}
    """
    try:
        from app.modules.architecture.services.inference_engine_service import (
            ArchiMateInferenceEngine,
        )

        data = request.get_json(silent=True) or {}
        test_name = data.get("test", "")
        dry_run = data.get("dry_run", False)

        engine = ArchiMateInferenceEngine(0)
        snap_before = engine.take_snapshot()

        created = []
        repaired_count = 0

        if test_name == "motivation_integrity":
            # Repair Goals missing Outcomes/Requirements, Drivers missing Goals
            goals = ArchiMateElement.query.filter(
                ArchiMateElement.type.in_(["Goal", "goal"])
            ).limit(50).all()
            for goal in goals:
                node = engine.graph.get_node(goal.id)
                if node and not dry_run:
                    result = engine.repair(goal.id)
                    created.extend([
                        {"type": e.element_type, "name": e.name, "id": e.id}
                        for e in result.elements_created
                    ])
                    repaired_count += len(result.elements_created)

        elif test_name == "relationship_density":
            # Repair orphan elements — give them chain connections
            all_elements = engine.graph.find_nodes(element_type=None, filters={})
            orphans = [
                e for e in all_elements
                if not engine.graph.get_neighbors(e.id, direction="both")
            ]
            for orphan in orphans[:30]:
                if not dry_run:
                    result = engine.repair(orphan.id)
                    created.extend([
                        {"type": e.element_type, "name": e.name, "id": e.id}
                        for e in result.elements_created
                    ])
                    repaired_count += len(result.elements_created)

        elif test_name == "cross_layer_trace":
            # Generate chains from root elements to create cross-layer links
            roots = engine._find_roots()
            for root in roots[:20]:
                if not dry_run:
                    result = engine.repair(root.id)
                    created.extend([
                        {"type": e.element_type, "name": e.name, "id": e.id}
                        for e in result.elements_created
                    ])
                    repaired_count += len(result.elements_created)

        elif test_name == "im_layer":
            # Generate WorkPackages/Deliverables from existing Gaps
            gaps = ArchiMateElement.query.filter(
                ArchiMateElement.type.in_(["Gap", "gap"])
            ).limit(20).all()
            for gap in gaps:
                node = engine.graph.get_node(gap.id)
                if node and not dry_run:
                    result = engine.repair(gap.id)
                    created.extend([
                        {"type": e.element_type, "name": e.name, "id": e.id}
                        for e in result.elements_created
                    ])
                    repaired_count += len(result.elements_created)

        else:
            return jsonify({
                "error": "Test '%s' does not support automated repair" % test_name,
                "repairable_tests": [
                    "motivation_integrity", "relationship_density",
                    "cross_layer_trace", "im_layer",
                ],
            }), 400

        if not dry_run:
            db.session.commit()

        snap_after = engine.take_snapshot()
        diff = engine.diff_snapshots(snap_before, snap_after)

        return jsonify({
            "test": test_name,
            "dry_run": dry_run,
            "repaired": repaired_count,
            "elements_created": created[:50],
            "diff": {
                "added_nodes": len(diff["added_nodes"]),
                "added_relationships": len(diff["added_relationships"]),
            },
        })

    except Exception as e:
        current_app.logger.error("Health repair error: %s", e, exc_info=True)
        db.session.rollback()
        return jsonify({"error": "Repair failed", "detail": str(e)}), 500
