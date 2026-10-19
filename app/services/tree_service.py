"""
Generic Tree Service — builds nested JSON from any SQLAlchemy model with a parent FK.

Usage:
    from app.services.tree_service import build_tree, TREE_REGISTRY

    # Get tree data for any registered model:
    data = build_tree("capability")
    # Returns: {"name": "Capabilities", "children": [{id, name, children, ...}, ...]}
"""

from app import db
from app.middleware.tenant_context import current_org_id


# ─── Tree Registry ────────────────────────────────────────────────────────────
# Each entry defines how to build a tree from a model.
#   model:       dotted import path to the SQLAlchemy model class
#   parent_fk:   the column name for the parent foreign key
#   name_field:  which field to use as the display name
#   fields:      extra fields to include in each node's JSON
#   root_label:  display name for the virtual root node
#   order_by:    list of column names to order siblings

TREE_REGISTRY = {
    "capability": {
        "model": "app.models.unified_capability.UnifiedCapability",
        "parent_fk": "parent_capability_id",
        "name_field": "name",
        "fields": [
            "description", "code", "level", "category", "domain_id",
            "specialization_type", "current_maturity_level", "target_maturity_level",
            "business_criticality", "business_owner",
        ],
        "root_label": "Capabilities",
        "order_by": ["level", "name"],
    },
    "apqc": {
        "model": "app.models.apqc_process.APQCProcess",
        "parent_fk": "parent_process_id",
        "name_field": "process_name",
        "fields": [
            "process_code", "process_description", "process_category",
            "process_type", "process_owner", "process_maturity",
            "improvement_priority", "industry_domain",
        ],
        "root_label": "APQC Processes",
        "order_by": ["process_code", "process_name"],
    },
    "business_process": {
        "model": "app.models.process_data.BusinessProcess",
        "parent_fk": "parent_process_id",
        "name_field": "name",
        "fields": [
            "description", "level", "process_type", "bpmn_type",
            "automation_percentage", "owner",
        ],
        "root_label": "Business Processes",
        "order_by": ["level", "name"],
    },
    "archimate": {
        "model": "app.models.archimate_core.ArchiMateElement",
        "parent_fk": "parent_id",
        "name_field": "name",
        "fields": [
            "description", "type", "layer", "scope",
            "building_block_type", "status",
        ],
        "root_label": "ArchiMate Elements",
        "order_by": ["layer", "type", "name"],
    },
    "requirement": {
        "model": "app.models.models.Requirement",
        "parent_fk": "parent_requirement_id",
        "name_field": "title",
        "fields": [
            "description", "requirement_type", "moscow_priority",
            "status", "story_points", "assigned_to",
        ],
        "root_label": "Requirements",
        "order_by": ["requirement_type", "title"],
    },
    "goal": {
        "model": "app.models.motivation.Goal",
        "parent_fk": "parent_goal_id",
        "name_field": "name",
        "fields": [
            "description", "goal_type", "priority", "status",
            "target_date", "measurement_criteria",
        ],
        "root_label": "Goals",
        "order_by": ["name"],
    },
    "work_package": {
        "model": "app.models.implementation_migration.WorkPackage",
        "parent_fk": "parent_id",
        "name_field": "name",
        "fields": [
            "description", "summary", "level", "status", "priority",
            "togaf_phase", "start_date", "target_date",
            "estimated_effort_hours",
        ],
        "root_label": "Work Packages",
        "order_by": ["level", "name"],
    },
    "solution": {
        "model": "app.models.solution_models.Solution",
        "parent_fk": "parent_solution_id",
        "name_field": "name",
        "fields": [
            "description", "adm_phase", "status", "solution_type",
            "complexity", "architecture_style",
        ],
        "root_label": "Solutions",
        "order_by": ["name"],
    },
    "compliance": {
        "model": "app.models.compliance_models.ComplianceRequirement",
        "parent_fk": "parent_requirement_id",
        "name_field": "title",
        "fields": [
            "description", "hierarchy_level", "framework_id",
            "control_id", "priority", "risk_if_not_met", "status",
        ],
        "root_label": "Compliance Requirements",
        "order_by": ["hierarchy_level", "title"],
    },
    "business_actor": {
        "model": "app.models.business_layer.BusinessActor",
        "parent_fk": "parent_actor_id",
        "name_field": "name",
        "fields": [
            "description", "actor_type", "organizational_level",
            "department", "division", "location", "headcount",
            "cost_center", "manager_name", "strategic_importance",
        ],
        "root_label": "Business Actors",
        "order_by": ["organizational_level", "name"],
    },
    "organization_unit": {
        "model": "app.models.enterprise_intelligence.OrganizationUnit",
        "parent_fk": "parent_unit_id",
        "name_field": "name",
        "fields": [
            "code", "description", "unit_type", "level",
            "head_of_unit", "cost_center_code", "annual_budget",
            "primary_location", "status",
        ],
        "root_label": "Organization Units",
        "order_by": ["level", "name"],
    },
    "portfolio_initiative": {
        "model": "app.models.enterprise_intelligence.PortfolioInitiative",
        "parent_fk": "parent_initiative_id",
        "name_field": "name",
        "fields": [
            "code", "description", "strategic_objective", "initiative_type",
            "status", "priority", "total_budget", "spent_to_date",
            "health_status", "completion_percentage",
        ],
        "root_label": "Portfolio Initiatives",
        "order_by": ["priority", "name"],
    },
    "system_boundary": {
        "model": "app.models.system_architecture.SystemBoundary",
        "parent_fk": "parent_boundary_id",
        "name_field": "name",
        "fields": [
            "description", "boundary_type", "system_name",
            "system_type", "system_category", "boundary_owner",
            "approval_status",
        ],
        "root_label": "System Boundaries",
        "order_by": ["boundary_type", "name"],
    },
    "reference_capability": {
        "model": "app.models.reference_models.ReferenceModelCapability",
        "parent_fk": "parent_capability_id",
        "name_field": "name",
        "fields": [
            "code", "description", "level", "sort_order",
            "archimate_element_type", "criticality", "is_core",
            "recommended_maturity_level",
        ],
        "root_label": "Reference Model Capabilities",
        "order_by": ["level", "sort_order", "name"],
    },
    # NOTE: SolutionRequirement excluded — model has columns not yet in DB schema.
    "task": {
        "model": "app.models.project_models.Task",
        "parent_fk": "parent_task_id",
        "name_field": "title",
        "fields": [
            "description", "status", "priority", "category",
            "assigned_to", "estimated_hours", "actual_hours", "due_date",
        ],
        "root_label": "Tasks",
        "order_by": ["priority", "title"],
    },
    "vendor_capability": {
        "model": "app.models.vendor_stack_hierarchy.VendorCapabilityHierarchy",
        "parent_fk": "parent_id",
        "name_field": "capability_name",
        "fields": [
            "capability_code", "capability_description", "level",
            "coverage_percentage", "maturity_level", "business_criticality",
            "automation_potential",
        ],
        "root_label": "Vendor Capabilities",
        "order_by": ["level", "capability_name"],
    },
    "vendor_service": {
        "model": "app.models.vendor_stack_hierarchy.VendorServiceCatalog",
        "parent_fk": "parent_service_id",
        "name_field": "service_name",
        "fields": [
            "service_code", "service_description", "service_type",
            "service_layer", "service_level", "lifecycle_stage",
            "sla_availability_percentage",
        ],
        "root_label": "Vendor Services",
        "order_by": ["service_layer", "service_name"],
    },
    "vendor_process": {
        "model": "app.models.vendor_stack_hierarchy.VendorProcessHierarchy",
        "parent_fk": "parent_id",
        "name_field": "process_name",
        "fields": [
            "process_code", "process_description", "process_level",
            "process_type", "automation_level", "maturity_level",
        ],
        "root_label": "Vendor Processes",
        "order_by": ["process_level", "process_name"],
    },
    "vendor_component": {
        "model": "app.models.vendor_stack_hierarchy.VendorComponentArchitecture",
        "parent_fk": "parent_component_id",
        "name_field": "component_name",
        "fields": [
            "component_code", "component_description", "component_type",
            "architectural_layer", "technology", "lifecycle_stage",
        ],
        "root_label": "Vendor Components",
        "order_by": ["architectural_layer", "component_name"],
    },
    "product_taxonomy": {
        "model": "app.models.vendor_taxonomy.ProductTaxonomy",
        "parent_fk": "parent_suite_id",
        "name_field": "canonical_name",
        "fields": [
            "display_name", "product_type", "category", "sub_category",
            "domain", "is_suite", "is_active",
        ],
        "root_label": "Product Taxonomy",
        "order_by": ["category", "canonical_name"],
    },
    "required_capability": {
        "model": "app.models.vendor_analysis.RequiredCapability",
        "parent_fk": "parent_capability_id",
        "name_field": "capability_name",
        "fields": [
            "capability_description", "category", "importance",
            "must_have", "weight_multiplier", "acceptance_criteria",
        ],
        "root_label": "Required Capabilities",
        "order_by": ["category", "capability_name"],
    },
}


def _import_model(dotted_path):
    """Import a model class from a dotted path like 'app.models.foo.Bar'."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# ─── Tenant scope ─────────────────────────────────────────────────────────────
# Every registry entry is exactly one of:
#   tenant   - the model carries TenantMixin; the tenant middleware filters every
#              statement and stamps organization_id on insert.
#   hybrid   - shared reference rows (organization_id IS NULL) plus an
#              organisation's own rows. Readable by everyone, writable only on
#              the caller's own rows. UnifiedCapability enforces this in its own
#              session listeners; ReferenceModelCapability is enforced here.
#   platform - one catalogue shared by every organisation with no owner column;
#              only a platform administrator may change it.
HYBRID_TREES = {"capability", "reference_capability"}
PLATFORM_TREES = {"apqc", "product_taxonomy"}

FORBIDDEN = "Forbidden"


def tree_scope(tree_key):
    if tree_key in HYBRID_TREES:
        return "hybrid"
    if tree_key in PLATFORM_TREES:
        return "platform"
    return "tenant"


def _write_gate(tree_key, Model):
    """Return an error string when this caller may not write this tree, else None.

    Fails closed: a tenant-scoped tree whose model lost its TenantMixin refuses
    writes rather than serving every organisation's rows.
    """
    scope = tree_scope(tree_key)
    if scope == "platform":
        from flask_login import current_user

        from app.middleware.tenant_decorators import is_platform_admin

        if not is_platform_admin(current_user):
            return f"{FORBIDDEN}: this tree is shared by every organisation"
    elif scope == "tenant":
        from app.models.mixins import TenantMixin

        if not issubclass(Model, TenantMixin):
            return f"{FORBIDDEN}: tree is not organisation-scoped"
    return None


def _visible(Model, tree_key):
    """Criteria limiting a hybrid reference tree to shared plus own rows."""
    if tree_key != "reference_capability":
        return None
    from sqlalchemy import or_

    org_id = current_org_id()
    if org_id is None:
        return Model.organization_id.is_(None)
    return or_(Model.organization_id == org_id, Model.organization_id.is_(None))


def _load(Model, tree_key, node_id):
    """Fetch one row through the filtered statement path.

    ``Session.get`` returns an identity-map hit without emitting SQL, so the
    tenant filter never runs; a select always does.
    """
    stmt = db.select(Model).where(Model.id == node_id)
    visible = _visible(Model, tree_key)
    if visible is not None:
        stmt = stmt.where(visible)
    return db.session.execute(stmt).scalar_one_or_none()


def _owned_by_caller(Model, tree_key, row):
    """A hybrid reference row may only be changed by the organisation that owns it."""
    if tree_key != "reference_capability":
        return True
    org_id = current_org_id()
    return org_id is not None and row.organization_id == org_id


def _commit():
    """Commit; a refused reference/cross-organisation write becomes FORBIDDEN.

    Two distinct shapes land here for the same underlying reason -- a write
    this caller may not make under row-level security or the hybrid write
    policy -- and neither is a server defect worth a 500: ``PermissionError``,
    raised by application code, and ``StaleDataError``, raised by the ORM's
    own flush when an UPDATE/DELETE it expected to touch one row instead
    matched zero, because row-level security silently excluded that row from
    its own WHERE clause rather than raising (a shared reference row a
    reparenting delete tried to write through, for instance -- see
    ``delete_node``).
    """
    from sqlalchemy.orm.exc import StaleDataError

    try:
        db.session.commit()
    except (PermissionError, StaleDataError) as exc:
        db.session.rollback()
        return f"{FORBIDDEN}: {exc}"
    return None


def build_tree(tree_key):
    """
    Build nested tree JSON for a registered tree type.

    Returns: {"name": root_label, "children": [nested nodes...]}
    Each node has: id, name, parent_id, children, + configured fields.
    """
    if tree_key not in TREE_REGISTRY:
        raise ValueError(f"Unknown tree key: {tree_key}. Available: {list(TREE_REGISTRY.keys())}")

    config = TREE_REGISTRY[tree_key]
    Model = _import_model(config["model"])
    parent_fk = config["parent_fk"]
    name_field = config["name_field"]
    extra_fields = config["fields"]

    # Build order_by columns
    order_cols = []
    for col_name in config.get("order_by", ["name"]):
        col = getattr(Model, col_name, None)
        if col is not None:
            order_cols.append(col)

    query = Model.query
    visible = _visible(Model, tree_key)
    if visible is not None:
        query = query.filter(visible)
    rows = query.order_by(*order_cols).all() if order_cols else query.all()

    # Build node dicts
    by_id = {}
    for row in rows:
        node = {
            "id": row.id,
            "name": getattr(row, name_field, None) or f"#{row.id}",
            "parent_id": getattr(row, parent_fk, None),
            "children": [],
        }
        for f in extra_fields:
            val = getattr(row, f, None)
            if val is not None and hasattr(val, "isoformat"):
                val = val.isoformat()
            node[f] = val
        by_id[row.id] = node

    # Nest
    roots = []
    for row in rows:
        node = by_id[row.id]
        pid = getattr(row, parent_fk, None)
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    return {"name": config["root_label"], "children": roots}


def get_node(tree_key, node_id):
    """Get a single node by ID."""
    config = TREE_REGISTRY[tree_key]
    Model = _import_model(config["model"])
    row = _load(Model, tree_key, node_id)
    if not row:
        return None

    name_field = config["name_field"]
    node = {"id": row.id, "name": getattr(row, name_field, None)}
    for f in config["fields"]:
        val = getattr(row, f, None)
        if val is not None and hasattr(val, "isoformat"):
            val = val.isoformat()
        node[f] = val
    node["parent_id"] = getattr(row, config["parent_fk"], None)
    return node


def create_node(tree_key, data):
    """Create a new node. Returns (node_dict, error_string)."""
    config = TREE_REGISTRY[tree_key]
    Model = _import_model(config["model"])
    name_field = config["name_field"]
    parent_fk = config["parent_fk"]

    denied = _write_gate(tree_key, Model)
    if denied:
        return None, denied

    name = (data.get("name") or data.get(name_field) or "").strip()
    if not name:
        return None, "Name is required"

    kwargs = {name_field: name}
    parent_id = data.get("parent_id")
    if parent_id:
        parent = _load(Model, tree_key, parent_id)
        if not parent:
            return None, "Parent not found"
        kwargs[parent_fk] = parent_id

    # Set optional fields
    for f in config["fields"]:
        if f in data:
            kwargs[f] = data[f]

    if tree_key == "reference_capability":
        org_id = current_org_id()
        if org_id is None:
            return None, f"{FORBIDDEN}: no organisation in context"
        kwargs["organization_id"] = org_id

    row = Model(**kwargs)
    db.session.add(row)
    error = _commit()
    if error:
        return None, error
    return get_node(tree_key, row.id), None


def update_node(tree_key, node_id, data):
    """Update a node. Returns (node_dict, error_string)."""
    config = TREE_REGISTRY[tree_key]
    Model = _import_model(config["model"])
    parent_fk = config["parent_fk"]
    name_field = config["name_field"]

    denied = _write_gate(tree_key, Model)
    if denied:
        return None, denied

    row = _load(Model, tree_key, node_id)
    if not row:
        return None, "Not found"
    if not _owned_by_caller(Model, tree_key, row):
        return None, f"{FORBIDDEN}: shared reference rows are read-only"

    if "name" in data or name_field in data:
        setattr(row, name_field, data.get("name") or data.get(name_field))

    if "parent_id" in data:
        new_parent = data["parent_id"]
        if new_parent == node_id:
            return None, "Cannot parent to self"
        if new_parent is not None and not _load(Model, tree_key, new_parent):
            return None, "Parent not found"
        setattr(row, parent_fk, new_parent)

    for f in config["fields"]:
        if f in data:
            setattr(row, f, data[f])

    error = _commit()
    if error:
        return None, error
    return get_node(tree_key, node_id), None


def delete_node(tree_key, node_id):
    """Delete a node. Reparents children to deleted node's parent. Returns error_string or None."""
    config = TREE_REGISTRY[tree_key]
    Model = _import_model(config["model"])
    parent_fk = config["parent_fk"]

    denied = _write_gate(tree_key, Model)
    if denied:
        return denied

    row = _load(Model, tree_key, node_id)
    if not row:
        return "Not found"
    if not _owned_by_caller(Model, tree_key, row):
        return f"{FORBIDDEN}: shared reference rows are read-only"

    parent_id = getattr(row, parent_fk, None)
    children_query = Model.query.filter(getattr(Model, parent_fk) == node_id)
    visible = _visible(Model, tree_key)
    if visible is not None:
        children_query = children_query.filter(visible)
    children = children_query.all()
    for child in children:
        setattr(child, parent_fk, parent_id)

    # Some models (ReferenceModelCapability is one) declare their
    # self-referential children as an ORM relationship with
    # cascade="all, delete-orphan". That cascade acts on the relationship's
    # own in-memory collection, which setattr() on the raw foreign-key column
    # above does not update -- row.children still lists every child it did
    # before the reparenting, invisible children included, and
    # session.delete(row) would cascade-delete all of them instead of
    # leaving the reparented rows alone. Flushing the column updates first
    # and expiring row's own cached state forces SQLAlchemy to re-read its
    # relationships from the database, which now shows no more children,
    # before the delete (and its cascade, now with nothing left to cascade
    # to) is issued.
    db.session.flush()
    db.session.expire(row)

    db.session.delete(row)
    return _commit()
