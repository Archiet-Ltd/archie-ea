"""
Architecture CRUD Routes
Unified dashboard for managing Motivation, Strategy, and Business layer elements
"""

import re
from datetime import date, datetime
from decimal import Decimal

from flask import (
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_

from app import db
from app.core.auth.decorators import admit_model_changers
from app.decorators import require_roles
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
from app.models.archimate_element_types import StandardTypeTable, is_application_kind

logger = logging.getLogger(__name__)

# One write-role gate for every mutating route in this module, matching the
# generic Elements page (architecture_crud_routes.py): admin or architect.
# Reads stay open to any signed-in user.
write_role_required = admit_model_changers(require_roles("admin", "architect"))

# Model registry mapping element types to model classes
MODEL_REGISTRY = StandardTypeTable({
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
})

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


# Columns a person never needs to read; administrators see them under "Technical details".
# The provenance columns are said in words on the page (where the element came from, and the date card), so
# their raw values are kept to the Technical details panel.
_TECHNICAL_FIELDS = frozenset({
    "color", "auto_generated", "recorded_at", "source_kind", "source_ref", "source_label", "source_recorded_at",
})
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
_ACRONYMS = ("Acm", "Api", "Id", "Url", "Sap", "Rfc", "Bapi", "Arb")


def _plain_label(text):
    """snake_case key or enum-like value -> words ("gap_kind" -> "Gap kind"); acronyms stay upper case."""
    words = str(text).replace("_", " ").strip()
    if not words:
        return words
    words = words[0].upper() + words[1:].lower()
    for acronym in _ACRONYMS:
        words = re.sub(rf"\b{acronym}\b", acronym.upper(), words, flags=re.I)
    return words


def _plain_value(value):
    """Enum-like database values (snake_case strings) read as plain words; anything else is unchanged."""
    return _plain_label(value) if isinstance(value, str) and _SNAKE.match(value) else value


def _readable(value):
    """Any column value as words: nested dicts and lists recurse, a named object shows its name, nothing is a repr."""
    if isinstance(value, dict):
        named = value.get("name") or value.get("title")
        if named and isinstance(named, str) and len(value) <= 3:
            return named
        return "; ".join(f"{_plain_label(k)}: {_readable(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_readable(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(_plain_value(value))
    return str(getattr(value, "name", None) or getattr(value, "title", None) or "")


def _element_owner_target(element, element_type, organization_id):
    """The owner record this element's owner lives on: ``(kind, id)`` or None.

    Resolves the concrete element first: a page opened on the generic
    ``archimate_elements`` row of an application is the application whose
    ``archimate_element_id`` points at it, and an AI system is owned under its
    own element id. None means this type has no owner field.
    """
    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    if organization_id is None:
        return None
    if isinstance(element, ApplicationComponent):
        return ("application", element.id)
    if is_application_kind(element_type):
        if is_ai_system(element):
            return ("ai_system", element.id)
        component = ApplicationComponent.query.filter_by(
            archimate_element_id=element.id, organization_id=organization_id,
        ).first()
        return ("application", component.id) if component is not None else None
    if element_type == "BusinessProcess":
        if isinstance(element, BusinessProcess):
            return ("business_process", element.id)
        # A page opened on the generic element is the process that points at
        # it; a process modelled only as an element gets its record on the
        # first assignment.
        process = BusinessProcess.query.filter_by(
            archimate_element_id=element.id, organization_id=organization_id,
        ).first()
        return ("business_process", process.id) if process is not None else ("process_element", element.id)
    if element_type == "Gap":
        if isinstance(element, Gap):
            return ("gap", element.id)
        gap = Gap.query.filter_by(
            archimate_element_id=element.id, organization_id=organization_id,
        ).first()
        return ("gap", gap.id) if gap is not None else None
    if element_type == "Capability":
        # Capability owners are recorded on the unified capability this row
        # projects into (by provenance), never on the source row's own id.
        from app.models.unified_capability import UnifiedCapability

        unified = UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(element.id),
            organization_id=organization_id,
        ).first()
        return ("capability", unified.id) if unified is not None else None
    return None


def _element_owner_rows(element, element_type, organization_id):
    """Owner rows for the detail page, through the one ownership record's existing readers."""
    from app.models.application_owner import ApplicationOwner

    target = _element_owner_target(element, element_type, organization_id)
    if target is None:
        return []
    kind, target_id = target
    if kind == "application":
        return ApplicationOwner.get_display_rows_for_application(target_id, organization_id)
    if kind == "process_element":
        return []
    return ApplicationOwner.get_display_rows_for_element(kind, target_id, organization_id)


def _element_owner_url(element, element_type, organization_id):
    """Where the owner picker saves an owner for this element, or None."""
    target = _element_owner_target(element, element_type, organization_id)
    if target is None:
        return None
    kind, target_id = target
    if kind == "application":
        return url_for("unified_applications.add_owner", app_id=target_id)
    if kind == "capability":
        return url_for("capability_map.api_set_capability_owner", capability_id=target_id)
    if kind == "business_process":
        return url_for("archimate_crud.process_set_owner", process_id=target_id)
    if kind == "gap":
        return url_for("archimate_crud.gap_set_owner", gap_id=target_id)
    if kind == "process_element":
        return url_for("archimate_crud.process_element_set_owner", element_id=target_id)
    return url_for("ai_systems_genome.ai_system_set_owner", element_id=target_id)


def _element_owner_remove_urls(element, element_type, organization_id, owner_rows):
    """Owner rows with the address that clears each one, from the same target the picker saves on."""
    target = _element_owner_target(element, element_type, organization_id)
    if target is None:
        return owner_rows
    kind, target_id = target
    if kind == "process_element":
        return owner_rows
    endpoints = {
        "application": ("unified_applications.remove_owner", "app_id"),
        "capability": ("capability_map.api_delete_capability_owner", "capability_id"),
        "ai_system": ("ai_systems_genome.ai_system_delete_owner", "element_id"),
        "business_process": ("archimate_crud.process_delete_owner", "process_id"),
        "gap": ("archimate_crud.gap_delete_owner", "gap_id"),
    }
    endpoint, key = endpoints[kind]
    return [
        dict(row, remove_url=url_for(endpoint, owner_id=row["id"], **{key: target_id}))
        for row in owner_rows
    ]


@archimate_crud.route("/business-process/element/<int:element_id>/owners", methods=["POST"])
@login_required
def process_element_set_owner(element_id):
    """Record the first owner of a process that so far exists only as an element."""
    from app.core.auth.permissions import can

    org_id = g.current_org_id
    if not (can(current_user, "assign", "owner") or can(current_user, "edit", "application")):
        return jsonify({"message": "You cannot assign owners"}), 403
    element = ArchiMateElement.query.filter_by(
        id=element_id, type="BusinessProcess", organization_id=org_id,
    ).first()
    if element is None:
        return jsonify({"message": "Business process not found"}), 404
    from app.modules.business_architecture import bpmn_service

    process = bpmn_service.process_for_element(element, org_id)
    db.session.commit()
    return process_set_owner(process.id)


@archimate_crud.route("/business-process/<int:process_id>/owners", methods=["POST"])
@login_required
def process_set_owner(process_id):
    """Record an owner for a business process (a new primary replaces the old)."""
    from app.core.auth.permissions import can
    from app.services.element_ownership_service import set_element_owner

    org_id = g.current_org_id
    if not (can(current_user, "assign", "owner") or can(current_user, "edit", "application")):
        return jsonify({"message": "You cannot assign owners"}), 403
    data = request.get_json(silent=True) or {}
    if not data.get("user_id"):
        return jsonify({"message": "user_id is required"}), 400
    try:
        record = set_element_owner(
            element_type="business_process",
            element_id=process_id,
            user_id=data["user_id"],
            organization_id=org_id,
            ownership_type=data.get("ownership_type", "primary"),
            assigned_by=current_user.id,
            replace_primary=bool(data.get("replace")),
        )
    except ValueError as exc:  # includes CrossOrganisationElementOwner
        db.session.rollback()
        return jsonify({"message": str(exc)}), 400
    _mirror_process_owner(process_id, org_id)
    return jsonify({"owner": record.to_dict()}), 201


@archimate_crud.route(
    "/business-process/<int:process_id>/owners/<int:owner_id>", methods=["DELETE"]
)
@login_required
def process_delete_owner(process_id, owner_id):
    """Clear one owner of a business process."""
    from app.core.auth.permissions import can
    from app.models.application_owner import ApplicationOwner

    org_id = g.current_org_id
    if not (can(current_user, "assign", "owner") or can(current_user, "edit", "application")):
        return jsonify({"message": "You cannot assign owners"}), 403
    record = ApplicationOwner.query.filter_by(
        id=owner_id, element_type="business_process", element_id=process_id,
        organization_id=org_id,
    ).first()
    if record is None:
        return jsonify({"message": "Owner record not found"}), 404
    db.session.delete(record)
    db.session.commit()
    _mirror_process_owner(process_id, org_id)
    return "", 204


@archimate_crud.route("/gap/<int:gap_id>/owners", methods=["POST"])
@login_required
def gap_set_owner(gap_id):
    """Record an owner for a gap (a new primary replaces the old)."""
    from app.core.auth.permissions import can
    from app.services.element_ownership_service import set_element_owner

    org_id = g.current_org_id
    if not (can(current_user, "assign", "owner") or can(current_user, "edit", "application")):
        return jsonify({"message": "You cannot assign owners"}), 403
    data = request.get_json(silent=True) or {}
    if not data.get("user_id"):
        return jsonify({"message": "user_id is required"}), 400
    try:
        record = set_element_owner(
            element_type="gap",
            element_id=gap_id,
            user_id=data["user_id"],
            organization_id=org_id,
            ownership_type=data.get("ownership_type", "primary"),
            assigned_by=current_user.id,
            replace_primary=bool(data.get("replace")),
        )
    except ValueError as exc:  # includes CrossOrganisationElementOwner
        db.session.rollback()
        return jsonify({"message": str(exc)}), 400
    return jsonify({"owner": record.to_dict()}), 201


@archimate_crud.route("/gap/<int:gap_id>/owners/<int:owner_id>", methods=["DELETE"])
@login_required
def gap_delete_owner(gap_id, owner_id):
    """Clear one owner of a gap."""
    from app.core.auth.permissions import can
    from app.models.application_owner import ApplicationOwner

    org_id = g.current_org_id
    if not (can(current_user, "assign", "owner") or can(current_user, "edit", "application")):
        return jsonify({"message": "You cannot assign owners"}), 403
    record = ApplicationOwner.query.filter_by(
        id=owner_id, element_type="gap", element_id=gap_id, organization_id=org_id,
    ).first()
    if record is None:
        return jsonify({"message": "Owner record not found"}), 404
    db.session.delete(record)
    db.session.commit()
    return "", 204


def _mirror_process_owner(process_id, org_id):
    """Keep the process's owner text equal to its recorded primary owner (the owner record is the authority)."""
    from app.models.application_owner import ApplicationOwner

    process = BusinessProcess.query.filter_by(id=process_id, organization_id=org_id).first()
    if process is None:
        return
    rows = ApplicationOwner.get_display_rows_for_element("business_process", process_id, org_id)
    named = [r["user_name"] for r in rows if r["ownership_type"] == "primary" and r["user_name"] != "—"]
    process.process_owner = named[0] if named else None
    db.session.commit()


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
        label = _plain_label(col_name)

        # Format special types
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        elif hasattr(value, "strftime"):
            if getattr(value, "year", 1900) < 1900:
                # An empty date stored as year 1 is not a recorded date.
                continue
            value = value.strftime("%Y-%m-%d %H:%M")
        elif isinstance(value, float):
            value = f"{value:.2f}" if value != int(value) else str(int(value))
        elif isinstance(value, dict):
            # A dict column is internal structure, not prose -- render its
            # entries as "key: value" pairs rather than Python's repr(), which
            # leaked as a literal {'source_model': 'Risk'} on real screens.
            value = _readable(value)
        elif isinstance(value, (list, tuple)):
            value = _readable(value)
        else:
            value = _plain_value(value)

        fields.append({"label": label, "value": str(value), "technical": col_name in _TECHNICAL_FIELDS})

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
    kind_filter = request.args.get("kind", "").strip()  # "Epic", "Feature", etc.
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
                custom_by_ae_id = {}
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
                    custom_by_ae_id = {
                        row.id: row for row in ArchiMateElement.query.filter(ArchiMateElement.id.in_(linked_ae_ids))
                    }
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
                        if hasattr(elem, field_name):  # model-safety-ok: polymorphic ArchiMate elements
                            value = getattr(elem, field_name, None)
                        else:
                            value = _custom_field_value(elem, custom_by_ae_id.get(ae_id), field_name)
                        elem_dict[field_name] = _form_text(value)
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

    # A kind filter narrows the whole list before it is paged, so an Epic on a
    # later page is found and the total counts only what matches.
    if kind_filter:
        from app.services.element_profile_service import read_profile as _read_kind
        matching = []
        for el in all_elements:
            if el.get("element_type") != "WorkPackage":
                continue
            ae = db.session.get(ArchiMateElement, el["id"])
            if ae is not None and _read_kind(ae).get("kind") == kind_filter:
                matching.append(el)
        all_elements = matching

    total = len(all_elements)
    pages = math.ceil(total / per_page) if per_page > 0 else 1
    start = (page - 1) * per_page
    end = start + per_page
    page_elements = all_elements[start:end]

    # Enrich WorkPackage elements with their framework profile (kind, WSJF).
    try:
        from app.services.element_profile_service import read_profile
        for el in page_elements:
            if el.get("element_type") == "WorkPackage":
                ae = db.session.get(ArchiMateElement, el["id"])
                if ae is not None:
                    profile = read_profile(ae)
                    el["kind"] = profile.get("kind")
                    el["wsjf_score"] = profile.get("wsjf_score")
    except Exception as e:
        current_app.logger.warning(f"Could not enrich profiles: {e}")

    # Apply kind filter after enrichment.
    if kind_filter:
        page_elements = [el for el in page_elements if el.get("kind") == kind_filter]
        total = len(all_elements)
        pages = math.ceil(total / per_page) if per_page > 0 else 1

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


# Element types whose model declares a NOT NULL foreign key with no default
# that this generic create form never collects anywhere else (not in the
# name/description fields set unconditionally above, not in any
# field_configs.py entry): BusinessFunction.capability_id is required by the
# model (app/models/business_capabilities.py) but has no field here, so every
# submission reached db.session.commit() and failed the database's own NOT
# NULL constraint instead of a validated request. Checked explicitly in
# create_element() below, before any insert is attempted, the same way every
# other required-field check in this codebase already works (compare
# _add_archimate_element's "name" check a few hundred lines up, and
# create_process_link's "process_id" check in business_layer_routes.py).
REQUIRED_RELATIONSHIP_FIELDS = {
    "BusinessFunction": ("capability_id", "Capability"),
}


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/new", methods=["GET", "POST"])
@login_required
@write_role_required
def create_element(layer, element_type):
    """Create a new element"""
    model_class = MODEL_REGISTRY.get(element_type)

    if not model_class:
        flash(f"Element type {element_type} is not yet supported", "warning")
        return redirect(url_for("archimate_crud.dashboard"))

    if request.method == "POST":
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()

            # Required-relationship validation: reject before any insert is
            # attempted, rather than let the model's own NOT NULL constraint
            # catch it at commit time (see REQUIRED_RELATIONSHIP_FIELDS above).
            required_field = REQUIRED_RELATIONSHIP_FIELDS.get(element_type)
            relationship_field_value = None
            if required_field:
                field_name, field_label = required_field
                raw_value = data.get(field_name)
                try:
                    relationship_field_value = (
                        int(raw_value) if raw_value not in (None, "") else None
                    )
                except (TypeError, ValueError):
                    relationship_field_value = None

                if relationship_field_value is None:
                    error_message = f"{field_label} is required"
                    if request.is_json:
                        return (
                            jsonify({"success": False, "error": error_message}),
                            400,
                        )
                    flash(error_message, "error")
                    return (
                        render_template(
                            "archimate_crud/dashboard.html",
                            layer=layer,
                            element_type=element_type,
                            layer_config=LAYER_CONFIG,
                            selected_layer=_selected_layer_for(layer),
                            field_config=get_element_config(element_type),
                            form_data=create_empty_form_data(element_type),
                            element_field_configs=ELEMENT_FIELD_CONFIGS,
                        ),
                        400,
                    )

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

            # Apply the validated required relationship field (e.g.
            # BusinessFunction.capability_id) — not part of the legacy
            # field_mappings table or a field_configs.py entry, so
            # _set_model_fields above never touches it.
            if required_field and relationship_field_value is not None:
                setattr(element, required_field[0], relationship_field_value)

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
                _store_custom_fields(element, data, element_type, ae_row=archimate_element)

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

    # filter_by always emits SQL, so the tenant filter applies even when the row
    # is already in the session identity map (Query.get would skip it).
    element = None
    # ?ae=1 means the id is an archimate_elements id, which is what a connection row carries; without it
    # the id is the dedicated table's own id, which is what the lists carry. The two number spaces overlap,
    # so a connection must say which one it holds or it can open another element's page.
    if model_class and request.args.get("ae") == "1":
        if hasattr(model_class, "archimate_element_id"):  # model-safety-ok: polymorphic ArchiMate elements
            element = model_class.query.filter_by(archimate_element_id=element_id).first()
    elif model_class:
        element = model_class.query.filter_by(id=element_id).first()

    # Fall back to archimate_elements table (native ArchiMate elements)
    if element is None:
        element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
        model_class = ArchiMateElement
        # A link that said only "element" (search results) names the row's real type
        # for the page, its breadcrumb and the Edit and Delete controls.
        if element_type not in MODEL_REGISTRY and element.type in MODEL_REGISTRY:
            element_type = element.type

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
    all_fields = _get_display_fields(element, model_class)
    display_fields = [f for f in all_fields if not f["technical"]]
    # These two are the element's own dates; the owner's assignment date is
    # shown with the owner, so the labels say whose date each is.
    for field in display_fields:
        if field["label"] in ("Valid from", "Recorded at"):
            field["label"] = "Element " + field["label"][0].lower() + field["label"][1:]
    technical_fields = [f for f in all_fields if f["technical"]]
    display_fields = _with_custom_fields(display_fields, element, element_type)
    external_links = CrosswalkService.get_links_for_element(ae_id or element.id)
    fact_row = element if isinstance(element, ArchiMateElement) else (
        ArchiMateElement.query.filter_by(id=ae_id).first() if ae_id else None
    )
    source_facts = _source_facts(fact_row)
    owner_rows = _element_owner_remove_urls(
        element, element_type, getattr(element, "organization_id", None),
        _element_owner_rows(element, element_type, getattr(element, "organization_id", None)),
    )
    owner_assign_url = _element_owner_url(
        element, element_type, getattr(element, "organization_id", None)
    )
    # A capability not yet projected into the one capability store has no
    # record to save an owner on here; it keeps the link to the capability map.
    owner_assign_link = None
    if owner_assign_url is None and element_type == "Capability":
        owner_assign_link = url_for("capability_map.index", cap=element.id)
    # Only a type that has an owner field shows an Owner row at all; one that
    # has none must not claim a missing owner. A gap carries an owner field of
    # its own, so it states plainly when none is recorded.
    has_owner_field = (
        owner_assign_url is not None
        or element_type == "Capability"
        or bool(owner_rows)
    )
    # A business process written before the owner record existed keeps its owner as text.
    legacy_owner_text = (
        getattr(element, "process_owner", None) if element_type == "BusinessProcess" else None
    )
    if element_type == "Gap":
        # The Status figure above is the gap's resolution status, said once.
        display_fields = [f for f in display_fields if f["label"] != "Resolution status"]
    if element_type == "BusinessProcess":
        # The Owner row above is the one place the owner is shown.
        display_fields = [f for f in display_fields if f["label"] != "Process owner"]

    # The external-identifiers help names only connectors this organisation
    # can use today, read from the one connector registry. When none are live
    # it points at the Connectors page instead.
    from app.models.connector_config import ConnectorConfig

    live_connectors = ConnectorConfig.live_connector_types(
        getattr(element, "organization_id", None)
    )
    connectors_url = url_for("m365_connector.connectors_index")

    page = _element_page(element, element_type, ae_id, relationships)

    # A business process lists the decision tables it applies (None for every other type).
    decisions_applied = None
    decision_table_id = None
    if element_type == "BusinessFunction" and ae_id:
        from app.modules.architecture.services import decision_table_service

        decision_table_id = decision_table_service.decision_table_id(ae_id, getattr(element, "organization_id", None))
    if element_type == "BusinessProcess" and ae_id:
        from app.modules.architecture.services import decision_table_service

        decisions_applied = decision_table_service.decisions_applied_by(
            ae_id, getattr(element, "organization_id", None)
        )

    return render_template(
        "archimate_crud/detail.html",
        decisions_applied=decisions_applied,
        decision_table_id=decision_table_id,
        layer=layer,
        element_type=element_type,
        element=element,
        relationships=relationships,
        source_ae_id=ae_id,
        page=page,
        display_fields=display_fields,
        technical_fields=technical_fields,
        owner_rows=owner_rows,
        legacy_owner_text=legacy_owner_text,
        has_owner_field=has_owner_field,
        owner_assign_url=owner_assign_url,
        owner_assign_link=owner_assign_link,
        external_links=external_links,
        source_facts=source_facts,
        live_connectors=live_connectors,
        connectors_url=connectors_url,
        technology_cost=_technology_cost_panel(ae_id),
        layer_config=LAYER_CONFIG,
        registered_types=list(MODEL_REGISTRY),
    )


_SOURCE_FACT_LABELS = (
    ("github_main_language", "Main language at the source"),
    ("github_last_activity", "Last activity at the source"),
)


def _source_facts(ae_row):
    """The facts a connection last read into this element, as (label, value) pairs."""
    props = (getattr(ae_row, "acm_properties", None) or {}) if ae_row is not None else {}
    facts = []
    for key, label in _SOURCE_FACT_LABELS:
        entry = props.get(key)
        value = entry.get("value") if isinstance(entry, dict) else None
        if value:
            facts.append((label, _utc_stamp(value) if key == "github_last_activity" else str(value)))
    return facts


def _utc_stamp(value) -> str:
    """An ISO time from the source as ``DD Mon YYYY HH:MM UTC``, the date form the rest of that card uses.
    A value that is not a time is shown as it came."""
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return f"{moment:%d %b %Y %H:%M} UTC"


def _technology_cost_panel(ae_id):
    """The cost panel for a node or system software, or None for any other element."""
    from app.modules.architecture.services.application_technology_links import is_technology_element
    from app.services import technology_cost

    org_id = getattr(g, "current_org_id", None)
    element = ArchiMateElement.query.filter_by(id=ae_id).first() if ae_id else None
    if element is None or org_id is None or not is_technology_element(element):
        return None
    return {
        "element_id": element.id,
        "allocation": technology_cost.allocation(org_id, element.id),
        "categories": technology_cost.CATEGORIES,
        "category_labels": dict(technology_cost.CATEGORIES),
        "rule": technology_cost.RULE,
        "currencies": technology_cost.currency_codes(),
    }


@archimate_crud.route("/technology-costs/<int:element_id>", methods=["POST"])
@login_required
@write_role_required
def add_technology_cost(element_id):
    """Record the annual cost of one category against a node or system software."""
    from app.services import technology_cost

    org_id = g.current_org_id
    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
    back = url_for(
        "archimate_crud.detail_element", layer="technology", element_type=element.type,
        element_id=element.id, ae=1, _anchor="technology-cost",
    )
    try:
        technology_cost.record_cost(
            org_id, element.id, request.form.get("amount"), request.form.get("currency"),
            request.form.get("category"), request.form.get("period"),
        )
        db.session.commit()
    except technology_cost.TechnologyCostError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(back)
    flash("Cost recorded.", "success")
    return redirect(back)


@archimate_crud.route("/technology-costs/<int:element_id>/remove", methods=["POST"])
@login_required
@write_role_required
def remove_technology_cost(element_id):
    """Remove the cost recorded in one category against a node or system software."""
    from app.services import technology_cost

    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
    back = url_for(
        "archimate_crud.detail_element", layer="technology", element_type=element.type,
        element_id=element.id, ae=1, _anchor="technology-cost",
    )
    try:
        technology_cost.remove_cost(g.current_org_id, element.id, request.form.get("category"))
        db.session.commit()
    except technology_cost.TechnologyCostError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(back)
    flash("Cost removed.", "success")
    return redirect(back)


def _description_origin(ae_row):
    """Where this element and its first description came from, in words, from the element's provenance record."""
    kind = (getattr(ae_row, "source_kind", None) or "").strip().lower()
    label = (getattr(ae_row, "source_label", None) or "").strip()
    if kind == "import":
        return "This element was imported from %s." % (label or "a model file")
    if kind == "connector":
        return "This element was brought in by %s." % (label or "a connected tool")
    if kind == "derived":
        return "This element was worked out from the rest of the model, not entered by a person."
    if kind == "sample":
        return "This element is sample data."
    return ""


def _element_page(element, element_type, ae_id, relationships):
    """Everything the detail page says about this element in words, from the one impact walk."""
    from flask_login import current_user

    from app.modules.architecture.routes.archimate_crud import element_page
    from app.template_helpers import _plain_layer, _plain_name

    def href_for(layer_key, kind, target_ae_id):
        if layer_key not in LAYER_CONFIG or not kind or not target_ae_id:
            return None
        return url_for(
            "archimate_crud.detail_element", layer=layer_key, element_type=kind, element_id=target_ae_id, ae=1
        )

    ae_row = element if isinstance(element, ArchiMateElement) else (
        ArchiMateElement.query.filter_by(id=ae_id).first() if ae_id else None
    )
    common = dict(user=current_user, plain_name=_plain_name)
    connections = element_page.build_connections(
        ae_id, relationships, plain_layer=_plain_layer, layer_aliases=LAYER_ALIASES, href_for=href_for, **common
    )
    return {
        "subtitle": element_page.subtitle_for(element_type, current_user, _plain_name),
        # The dedicated table may hold no description while the model-graph row does; show whichever has words.
        "description": (
            getattr(element, "description", None) or getattr(ae_row, "description", None) or ""
        ).strip(),
        "description_origin": _description_origin(ae_row),
        "connections": connections,
        "lines": element_page.answer_lines(element_type, connections, **common),
        **element_page.plateau_facts(element, element_type, ae_row, href_for=href_for),
    }


def _typed_record(element, from_ae, element_type):
    """The record that holds an element's typed values (Goal type, service type, ...), or None.

    A dedicated record is its own; for a model-graph row it is the dedicated record that mirrors it.
    """
    if not from_ae:
        return element
    model_class = MODEL_REGISTRY.get(element_type)
    if model_class is None or not hasattr(model_class, "archimate_element_id"):
        return None
    return model_class.query.filter_by(  # tenant-scoping-ok: keyed by the id of an archimate_elements row already loaded under the tenant filter
        archimate_element_id=element.id
    ).first()


def _resolve_edit_target(element_type, element_id):
    """Find the row an edit or delete acts on, and the type it is, for one URL.

    A link that carries the registered type opens the dedicated record; a link
    that carries ``?ae=1`` or a type this registry does not hold (search links
    say ``element``) names an ``archimate_elements`` row, which is edited and
    deleted through the same handlers under the type that row really has.
    Returns ``(element, from_ae, resolved_type)``; ``element`` is None when
    nothing in this organisation answers.
    """
    model_class = MODEL_REGISTRY.get(element_type)
    if model_class is not None and request.args.get("ae") != "1":
        element = model_class.query.filter_by(id=element_id).first()
        if element is not None:
            return element, False, element_type
        element = ArchiMateElement.query.filter_by(id=element_id).first()
        return element, True, element_type
    element = ArchiMateElement.query.filter_by(id=element_id).first()
    resolved = element_type
    if element is not None and element.type and element.type != element_type:
        resolved = element.type
    return element, True, resolved


@archimate_crud.route(
    f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>/edit", methods=["GET", "POST"]
)
@login_required
@write_role_required
def update_element(layer, element_type, element_id):
    """Update an element"""
    element, _from_ae, element_type = _resolve_edit_target(element_type, element_id)
    model_class = MODEL_REGISTRY.get(element_type) or ArchiMateElement
    if element is None:
        if request.is_json:
            return jsonify({"success": False, "error": "Element not found"}), 404
        from flask import abort
        abort(404)

    if request.method == "POST":
        try:
            data = request.get_json() if request.is_json else request.form.to_dict()

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
            elif MODEL_REGISTRY.get(element_type) is not None and hasattr(
                MODEL_REGISTRY[element_type], "archimate_element_id"
            ):  # model-safety-ok: polymorphic ArchiMate elements
                # The dedicated record that mirrors this row says the same name.
                for mirror in MODEL_REGISTRY[element_type].query.filter_by(  # tenant-scoping-ok: keyed by the id of an archimate_elements row already loaded under the tenant filter
                    archimate_element_id=element.id
                ).all():
                    if hasattr(mirror, "name"):  # model-safety-ok: polymorphic ArchiMate elements
                        mirror.name = element.name
                    if hasattr(mirror, "description"):  # model-safety-ok: polymorphic ArchiMate elements
                        mirror.description = element.description
                    # Every typed value the form offers (Goal type, service type, ...) is kept on that record.
                    _set_model_fields(mirror, data, type(mirror), element_type)
            # As-is / to-be state (ArchiMateElement.plateau), if the form set it.
            # Works for a native ArchiMateElement (element itself) and a linked one.
            _apply_architecture_state(element, data)

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
                    **({"ae": 1} if _from_ae else {}),
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

    # The Edit link opens the edit form for this element over its layer page; saving
    # returns to the element's own page.
    typed_record = _typed_record(element, _from_ae, element_type)
    edit_target = {
        "id": element.id,
        "element_type": element_type,
        "name": getattr(element, "name", None) or getattr(element, "title", None) or "",
        "description": getattr(element, "description", None) or "",
        "ae": bool(_from_ae),
        # An element with no dedicated record has nowhere to keep typed values, so the form does not offer them.
        "typed": typed_record is not None,
        "return_url": url_for(
            "archimate_crud.detail_element", layer=layer, element_type=element_type,
            element_id=element.id, **({"ae": 1} if _from_ae else {}),
        ),
    }
    # Every value the form shows, from the same field list the form is built from, so a save that changes
    # only the name sends back what is stored.
    ae_row = element if _from_ae else (
        ArchiMateElement.query.filter_by(id=element.archimate_element_id).first()
        if getattr(element, "archimate_element_id", None) else None
    )
    for field in (ELEMENT_FIELD_CONFIGS.get(element_type) or {}).get("fields", []):
        name = field["name"]
        if name == "architecture_state":
            value = getattr(ae_row, "togaf_plateau", None)
            value = value if value in _ARCHITECTURE_STATES else ""
        elif typed_record is None:
            continue
        elif hasattr(typed_record, name):  # model-safety-ok: polymorphic ArchiMate elements
            value = getattr(typed_record, name, None)
        else:
            value = _custom_field_value(typed_record, ae_row, name)
        edit_target[name] = _form_text(value)
    return render_template(
        "archimate_crud/dashboard.html",
        layer=layer,
        initial_layer=layer,
        edit_target=edit_target,
        element_type=element_type,
        element=element,
        layer_config=LAYER_CONFIG,
        selected_layer=_selected_layer_for(layer),
        element_field_configs=ELEMENT_FIELD_CONFIGS,
    )


def _plain_table_words(table_name):
    """A table's records in plain words: "archimate_elements" reads "model elements"."""
    words = table_name.replace("_", " ")
    return "model " + words[len("archimate "):] if words.startswith("archimate ") else words


def _delete_blockers(ae_row):
    """What still points at this element and would stop its delete, as (words, count) pairs.

    Relationships are cleared by the delete and the dedicated record that
    mirrors the row goes with it; any other table holding a restricting
    foreign key to the row is a reference the person must remove first.
    """
    if ae_row is None:
        return []
    own = {m.__table__.name for m in set(MODEL_REGISTRY.values()) if hasattr(m, "__table__")}
    own.add("archimate_relationships")
    blockers = []
    for table in db.metadata.sorted_tables:
        if table.name in own:
            continue
        for fk in table.foreign_keys:
            if fk.column.table.name != "archimate_elements" or fk.column.name != "id":
                continue
            if (fk.ondelete or "").upper() in ("CASCADE", "SET NULL", "SET DEFAULT"):
                continue
            count = db.session.execute(
                db.select(db.func.count()).select_from(table).where(fk.parent == ae_row.id)
            ).scalar() or 0
            if count:
                blockers.append((_plain_table_words(table.name), count))
    return blockers


@archimate_crud.route(
    f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>/delete-check", methods=["GET"]
)
@login_required
def delete_check(layer, element_type, element_id):
    """Say whether this delete can happen, before the person is asked to confirm it."""
    from app.core.auth.permissions import can_change_model

    element, from_ae, element_type = _resolve_edit_target(element_type, element_id)
    if element is None:
        return jsonify({"allowed": False, "reason": "This element no longer exists."}), 404
    if not can_change_model(current_user):
        return jsonify({
            "allowed": False,
            "reason": "Only an architect can delete elements. Ask an administrator in Team settings.",
        })
    ae_row = element if from_ae else ArchiMateElement.query.filter_by(
        id=getattr(element, "archimate_element_id", None)
    ).first()
    blockers = _delete_blockers(ae_row)
    if blockers:
        listed = ", ".join(f"{w} ({n})" for w, n in blockers)
        return jsonify({
            "allowed": False,
            "reason": f"It is still used by {listed}. Remove those links first.",
        })
    ae_ids = [ae_row.id] if ae_row is not None else []
    relationships = ArchiMateRelationship.query.filter(
        db.or_(
            ArchiMateRelationship.source_id.in_(ae_ids),
            ArchiMateRelationship.target_id.in_(ae_ids),
        )
    ).count() if ae_ids else 0
    return jsonify({"allowed": True, "relationships": relationships})


@archimate_crud.route(
    f"/{_LAYER_URL_PATTERN}/<element_type>/<int:element_id>/delete", methods=["POST"]
)
@login_required
@write_role_required
def delete_element(layer, element_type, element_id):
    """Delete an element"""
    element, _from_ae, element_type = _resolve_edit_target(element_type, element_id)
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
        if MODEL_REGISTRY.get(element_type) is None:
            return redirect(url_for("archimate_crud.dashboard"))
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
                **({"ae": 1} if _from_ae else {}),
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
    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
    service = ArchiMateValidationService()
    issues = service.validate_element(element)
    return jsonify({'issues': issues, 'valid': len(issues) == 0})


@archimate_crud.route(f"/{_LAYER_URL_PATTERN}/<element_type>/bulk-delete", methods=["POST"])
@login_required
@write_role_required
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
            element = model_class.query.filter_by(id=element_id).first()
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
@write_role_required
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

    # filter_by always emits SQL, so the tenant filter applies even when the row
    # is already in the session identity map (Query.get would skip it).
    element = model_class.query.filter_by(id=element_id).first_or_404()
    relationships = _get_element_relationships(element, element_id)

    return jsonify({"success": True, "relationships": relationships})


@archimate_crud.route("/api/elements/<int:element_id>/detail")
@login_required
def api_element_detail(element_id):
    """ARC-006: Return full detail JSON for a single ArchiMate element.

    Returns element fields, outgoing/incoming relationships, solution linkages,
    and the element's framework profile (kind, WSJF score).
    """
    # filter_by always emits SQL, so the tenant filter applies even when the row
    # is already in the session identity map (Query.get would skip it).
    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()

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

    # Read the element's framework profile (kind, WSJF).
    profile = {}
    try:
        from app.services.element_profile_service import read_profile
        profile = read_profile(element)
    except Exception as e:
        current_app.logger.warning(f"Could not read profile for element {element_id}: {e}")

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
        "profile": profile,
    })


@archimate_crud.route("/api/elements/<int:element_id>", methods=["PATCH"])
@login_required
@write_role_required
def api_element_patch(element_id):
    """ARC-006: Partial update of an ArchiMate element's core fields."""
    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
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
        if field in data and hasattr(element, field):  # model-safety-ok: polymorphic ArchiMate elements
            setattr(element, field, _column_value(element, field, data[field]))

    if element_type:
        _store_custom_fields(element, data, element_type)


def _column_value(element, field, value):
    """A posted value in the form its column holds.

    An empty value is stored as NULL when the column allows one, so a date, a number or a choice left empty
    is not sent to the database as an empty string. A date or number typed as text is read as one. A value
    left empty on a field that is already NULL stays NULL.
    """
    column = getattr(type(element), field, None)
    column = getattr(getattr(column, "property", None), "columns", [None])[0]
    if column is None or not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None if column.nullable else value
    kind = column.type
    if isinstance(kind, db.DateTime):
        return datetime.fromisoformat(text)
    if isinstance(kind, db.Date):
        return date.fromisoformat(text[:10])
    if isinstance(kind, db.Integer):
        return int(text)
    if isinstance(kind, (db.Float, db.Numeric)):
        return float(text) if isinstance(kind, db.Float) else Decimal(text)
    return value


def _custom_field_names(element, element_type):
    """The form's fields for this type that the element's own record has no column for."""
    return [
        name for name in get_element_field_names(element_type)
        if name != "architecture_state" and not hasattr(element, name)  # model-safety-ok: polymorphic ArchiMate elements
    ]


def _model_graph_row(element):
    """The archimate_elements row an element's values are kept on: itself, or the row its record links to."""
    if isinstance(element, ArchiMateElement):
        return element
    ae_id = getattr(element, "archimate_element_id", None)
    return ArchiMateElement.query.filter_by(id=ae_id).first() if ae_id else None


def _store_custom_fields(element, data, element_type, ae_row=None):
    """Keep each posted form value the element's record has no column for in the model-graph row's
    custom_properties, under the field's own key. An empty value removes the key."""
    names = [n for n in _custom_field_names(element, element_type) if n in data]
    ae_row = ae_row or _model_graph_row(element)
    if not names or ae_row is None:
        return
    props = dict(ae_row.custom_properties or {})
    for name in names:
        value = data[name]
        value = value.strip() if isinstance(value, str) else value
        if value in (None, ""):
            props.pop(name, None)
        else:
            props[name] = value
    ae_row.custom_properties = props


def _custom_field_value(element, ae_row, name):
    """The stored value of a form field that lives in custom_properties, or None."""
    return (getattr(ae_row, "custom_properties", None) or {}).get(name)


def _with_custom_fields(display_fields, element, element_type):
    """The page's facts with each value the form keeps in custom_properties added under the form's own label.

    A model-graph row also lists custom_properties as one line of its own; the keys shown here are left out of
    it so no value is said twice.
    """
    record = _typed_record(element, isinstance(element, ArchiMateElement), element_type)
    ae_row = _model_graph_row(element)
    config = ELEMENT_FIELD_CONFIGS.get(element_type) or {}
    if record is None or ae_row is None or not config:
        return display_fields
    shown = []
    for field in config.get("fields", []):
        name = field["name"]
        if name not in _custom_field_names(record, element_type):
            continue
        value = _custom_field_value(record, ae_row, name)
        if value in (None, ""):
            continue
        labels = {opt["value"]: opt["label"] for opt in field.get("options") or []}
        shown.append({"label": field["label"], "value": str(labels.get(value, value)), "technical": False})
    if not shown:
        return display_fields
    keys = {f["name"] for f in config["fields"]}
    rest = {k: v for k, v in (ae_row.custom_properties or {}).items() if k not in keys}
    kept = [f for f in display_fields if f["label"] != "Custom properties"]
    if rest and len(kept) != len(display_fields):
        kept.append({"label": "Custom properties", "value": str(_readable(rest)), "technical": False})
    return kept + shown


def _form_text(value):
    """A stored value as the form's box holds it: a date as 2027-03-15, nothing as an empty box."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return value


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
@write_role_required
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


# ── Element profile routes (kind, WSJF) ─────────────────────────────────────


@archimate_crud.route("/api/elements/<int:element_id>/profile", methods=["GET"])
@login_required
def api_element_profile(element_id):
    """Read the element's framework profile (kind, WSJF score, properties)."""
    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
    try:
        from app.services.element_profile_service import read_profile
        profile = read_profile(element)
        return jsonify(profile)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@archimate_crud.route("/api/elements/<int:element_id>/profile", methods=["POST"])
@login_required
def api_element_set_kind(element_id):
    """Set the framework kind on an element.

    Only Architect and above (not Viewer) may edit.
    """
    from flask_login import current_user

    # Check enterprise_role: Viewer cannot edit.
    enterprise_role = getattr(current_user, "enterprise_role", "").lower()
    if enterprise_role == "viewer":
        return jsonify({"error": "Viewer cannot edit element kind."}), 403

    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
    data = request.get_json() or {}
    kind = data.get("kind")
    framework_id = data.get("framework_id", "safe")

    try:
        from app.services.element_profile_service import set_kind, read_profile
        if kind is None:
            set_kind(element, None, framework_id)
        else:
            set_kind(element, kind, framework_id)
        db.session.commit()
        return jsonify(read_profile(element))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@archimate_crud.route("/api/elements/<int:element_id>/profile/wsjf", methods=["POST"])
@login_required
def api_element_set_wsjf(element_id):
    """Set WSJF components on an element.

    Only Architect and above (not Viewer) may edit.
    """
    from flask_login import current_user

    # Check enterprise_role: Viewer cannot edit.
    enterprise_role = getattr(current_user, "enterprise_role", "").lower()
    if enterprise_role == "viewer":
        return jsonify({"error": "Viewer cannot edit WSJF components."}), 403

    element = ArchiMateElement.query.filter_by(id=element_id).first_or_404()
    data = request.get_json() or {}

    try:
        from app.services.element_profile_service import set_wsjf_components
        result = set_wsjf_components(
            element,
            business_value=data.get("business_value", 1),
            time_criticality=data.get("time_criticality", 1),
            risk_reduction=data.get("risk_reduction", 1),
            job_size=data.get("job_size", 1),
        )
        db.session.commit()
        return jsonify(result)
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
