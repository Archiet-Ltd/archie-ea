"""
Data Architecture Routes

Provides REST API endpoints and dashboard views for data architecture models:
- ConceptualDataModel
- LogicalDataModel
- PhysicalDataModel
- DataLineage
- DataTransformation
"""

import logging

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log
from app.modules.architecture.services import data_sor_service as sor

from app.models import (
    ConceptualDataModel,
    DataLineage,
    DataTransformation,
    LogicalDataModel,
    PhysicalDataModel,
)
from app.models.process_data import DataDomain
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

data_architecture_bp = Blueprint(
    "data_architecture", __name__, url_prefix="/architecture"
)


def _current_org_id():
    return getattr(g, "current_org_id", getattr(current_user, "organization_id", None))


def _system_of_record_application_id_from_form():
    application_id = request.form.get("application_id", type=int)
    if not application_id:
        return None
    org_id = _current_org_id()
    application = sor.get_application(org_id, application_id)
    if application is None:
        raise sor.DataSorError("Pick an application from your portfolio.")
    return application.id


def _domain_id_from_form(submitted_id, *, allow_default=False):
    org_id = _current_org_id()
    if submitted_id:
        domain = sor.get_domain(org_id, submitted_id)
        if domain is None:
            raise sor.DataSorError("Pick a data domain from your organisation.")
        return domain.id

    if not allow_default:
        return None

    default_domain = (
        DataDomain.query.filter(
            DataDomain.organization_id == org_id,
            DataDomain.name == "General",
        )
        .order_by(DataDomain.id)
        .first()
    )
    if default_domain is None:
        default_domain = DataDomain(
            name="General",
            description="Default data domain",
            organization_id=org_id,
        )
        db.session.add(default_domain)
        db.session.flush()
    return default_domain.id


# ============================================================================
# Page Routes
# ============================================================================


@data_architecture_bp.route("/data-architecture")
@login_required
def data_architecture_dashboard():
    """Data architecture dashboard."""
    try:
        from app.models.capability_to_vendor_mapping import (
            TechnicalCapabilityVendorMapping,
        )
        from app.models.technical_capability import ACMDomain, TechnicalCapability

        # Filter for Data & Analytics domains (Pattern 4 from plan)
        data_domains = [ACMDomain.DATA_STORAGE, ACMDomain.AI_ANALYTICS]
        data_caps_objs = TechnicalCapability.query.filter(
            TechnicalCapability.acm_domain.in_(data_domains)
        ).all()

        data_platform_stack = []
        # Batch prefetch all vendor mappings for these capabilities
        cap_ids = [cap.id for cap in data_caps_objs]
        all_mappings = (
            TechnicalCapabilityVendorMapping.query.filter(
                TechnicalCapabilityVendorMapping.technical_capability_id.in_(cap_ids)
            ).all()
            if cap_ids
            else []
        )
        mappings_by_cap = {}
        for m in all_mappings:
            mappings_by_cap.setdefault(m.technical_capability_id, []).append(m)

        for cap in data_caps_objs:
            # Look up pre-fetched vendor mappings for this capability
            mappings = mappings_by_cap.get(cap.id, [])
            for m in mappings:
                if m.vendor_product:
                    data_platform_stack.append(
                        {
                            "capability": cap.name,
                            "domain": cap.domain,
                            "product": m.vendor_product.name,
                            "vendor": m.vendor_product.vendor.name
                            if m.vendor_product.vendor
                            else "Internal",
                            # Carried so the dashboard row can link to the vendor
                            # record; without it the row names a supplier the
                            # user has no way to open.
                            "vendor_id": m.vendor_product.vendor.id
                            if m.vendor_product.vendor
                            else None,
                            "maturity": m.maturity_level,
                            "fit": m.fit_score,
                        }
                    )

        # Query real data model counts
        conceptual_count = 0
        logical_count = 0
        physical_count = 0
        data_lineage_count = 0
        archimate_data_count = 0
        archimate_rel_count = 0
        try:
            # Re-importing inside try/except block as in original code to handle potential missing models
            # although in this file we imported them at top level.
            # We will use the top level imports for consistency unless they are truly optional/missing.
            # The original code imported from app.models.all_missing_models which suggests they might be stubs?
            # But line 21 of architecture_routes.py imported them from app.models.
            # I will stick to app.models imports which I did at the top.

            conceptual_count = ConceptualDataModel.query.count()
            logical_count = LogicalDataModel.query.count()
            physical_count = PhysicalDataModel.query.count()
            # DEF-064, Capgemini dry-run: this counted DataLineage rows only,
            # while /architecture/data-lineage's "Recorded Lineage Edges"
            # (data_lineage_view below) counts those SAME rows plus
            # ArchiMateRelationship edges between DataObjects — a real,
            # already-drawn subset of lineage this tile just never counted.
            # Match the fuller, more complete definition rather than leave
            # two different numbers answering "how much lineage exists".
            from app.models.archimate_core import ArchiMateElement as _ArchiMateElement
            from app.models.archimate_core import ArchiMateRelationship as _ArchiMateRelationship

            _data_object_ids = [
                row.id for row in
                db.session.query(_ArchiMateElement.id).filter(_ArchiMateElement.type == "DataObject").all()
            ]
            data_lineage_count = DataLineage.query.count()
            if _data_object_ids:
                data_lineage_count += _ArchiMateRelationship.query.filter(
                    db.or_(
                        _ArchiMateRelationship.source_id.in_(_data_object_ids),
                        _ArchiMateRelationship.target_id.in_(_data_object_ids),
                    )
                ).count()
        except Exception:
            logger.debug(
                "Failed to query data architecture model counts", exc_info=True
            )

        try:
            from app.models.archimate_core import (
                ArchiMateElement,
                ArchiMateRelationship,
            )

            archimate_data_count = ArchiMateElement.query.filter(
                ArchiMateElement.layer.in_(["Application", "Technology"])
            ).count()
            # ADR-0008: ArchiMateRelationship is NOT tenant-scoped (no TenantMixin),
            # so a bare .count() sums every org's edges and disagrees with the
            # tenant-filtered traceability matrix. Count only edges whose endpoints
            # are this tenant's elements — the same relationships the matrix sees.
            tenant_element_ids = [
                row[0]
                for row in db.session.query(ArchiMateElement.id).all()
            ]
            if tenant_element_ids:
                archimate_rel_count = ArchiMateRelationship.query.filter(
                    ArchiMateRelationship.source_id.in_(tenant_element_ids),
                    ArchiMateRelationship.target_id.in_(tenant_element_ids),
                ).count()
            else:
                archimate_rel_count = 0
        except Exception:
            logger.debug(
                "Failed to query ArchiMate element/relationship counts", exc_info=True
            )

        return render_template(
            "enterprise/data_architecture_dashboard.html",
            data_stack=data_platform_stack,
            data_cap_count=len(data_caps_objs),
            conceptual_count=conceptual_count,
            logical_count=logical_count,
            physical_count=physical_count,
            data_lineage_count=data_lineage_count,
            archimate_data_count=archimate_data_count,
            archimate_rel_count=archimate_rel_count,
        )
    except Exception as e:
        from flask import flash

        db.session.rollback()
        current_app.logger.exception("Error loading data architecture dashboard: %s", e)
        flash("Error loading data architecture dashboard.", "error")
        return render_template(
            "enterprise/data_architecture_dashboard.html",
            data_stack=[],
            data_cap_count=None,
            conceptual_count=None,
            logical_count=None,
            physical_count=None,
            data_lineage_count=None,
            archimate_data_count=None,
            archimate_rel_count=None,
            load_error="The data architecture inventory could not be read.",
        )


# ============================================================================
# Data Architecture API Endpoints
# ============================================================================


@data_architecture_bp.route("/data-lineage")
@login_required
def data_lineage_view():
    """ARCH-123: field-level lineage over the DataObject ArchiMateElement
    catalogue. Three real, derived sections, no fabricated data:
      1. DataLineage rows already grounded in a source/target DataObject.
      2. Existing ArchiMateRelationship edges between DataObjects (drawn
         elsewhere, e.g. the ArchiMate composer or an import).
      3. Semantic-similarity suggestions from the Data Stewardship
         reviewer's embedding engine — labelled as suggestions, never
         asserted as lineage until a human confirms them.
    """
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    data_objects = (
        ArchiMateElement.query.filter(ArchiMateElement.type == "DataObject")
        .order_by(ArchiMateElement.name)
        .all()
    )
    object_ids = [o.id for o in data_objects]

    grounded_lineage = []
    relationship_edges = []
    suggestions = []
    if object_ids:
        grounded_lineage = (
            DataLineage.query.filter(
                DataLineage.archimate_element_id.in_(object_ids)
            )
            .order_by(DataLineage.id.desc())
            .all()
        )
        relationship_edges = (
            ArchiMateRelationship.query.filter(
                db.or_(
                    ArchiMateRelationship.source_id.in_(object_ids),
                    ArchiMateRelationship.target_id.in_(object_ids),
                )
            )
            .order_by(ArchiMateRelationship.id)
            .all()
        )
        try:
            from app.modules.solutions_strategic.v2.services.data_stewardship_reviewer import (
                _semantic_pairs,
            )

            names = [o.name for o in data_objects if o.name]
            by_name = {o.name: o for o in data_objects if o.name}
            for a, b, sim in _semantic_pairs(names)[:10]:
                oa, ob = by_name.get(a), by_name.get(b)
                if oa and ob:
                    suggestions.append({"a": oa, "b": ob, "similarity": sim})
        except Exception:
            logger.debug("semantic lineage suggestions unavailable", exc_info=True)

    traced_ids = {row.archimate_element_id for row in grounded_lineage if row.archimate_element_id}
    traced_ids |= {
        row.target_archimate_element_id
        for row in grounded_lineage
        if row.target_archimate_element_id
    }
    for edge in relationship_edges:
        traced_ids.add(edge.source_id)
        traced_ids.add(edge.target_id)
    untraced = [o for o in data_objects if o.id not in traced_ids]

    return render_template(
        "enterprise/data_lineage.html",
        data_objects=data_objects,
        grounded_lineage=grounded_lineage,
        relationship_edges=relationship_edges,
        suggestions=suggestions,
        untraced=untraced,
    )


@data_architecture_bp.route("/data-lineage/create", methods=["POST"])
@login_required
@audit_log("create_data_lineage")
def create_data_lineage():
    """Create a real, grounded lineage row: both endpoints must be
    existing DataObject ArchiMateElements — never free text. Rejects
    anything that does not resolve to a real element."""
    from app import db
    from app.models.archimate_core import ArchiMateElement

    source_id = request.form.get("source_id", type=int)
    target_id = request.form.get("target_id", type=int)
    lineage_type = (request.form.get("lineage_type") or "").strip() or None

    source = ArchiMateElement.query.get(source_id) if source_id else None
    target = ArchiMateElement.query.get(target_id) if target_id else None

    if not source or source.type != "DataObject" or not target or target.type != "DataObject":
        flash("Both source and target must be existing data objects.", "error")
        return redirect(url_for("data_architecture.data_lineage_view"))
    if source.id == target.id:
        flash("A data object cannot flow into itself.", "error")
        return redirect(url_for("data_architecture.data_lineage_view"))

    row = DataLineage(
        name=f"{source.name} -> {target.name}",
        archimate_element_id=source.id,
        target_archimate_element_id=target.id,
        lineage_type=lineage_type,
        created_by_id=current_user.id if current_user.is_authenticated else None,
    )
    db.session.add(row)
    db.session.commit()
    flash("Lineage recorded.", "success")
    return redirect(url_for("data_architecture.data_lineage_view"))


@data_architecture_bp.route("/api/data-models")
@login_required
def api_data_models():
    """Get all data architecture models."""
    try:
        conceptual_models = ConceptualDataModel.query.limit(500).all()
        logical_models = LogicalDataModel.query.limit(500).all()
        physical_models = PhysicalDataModel.query.limit(500).all()

        return jsonify(
            {
                "conceptual_models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "business_domain": m.business_domain,
                        "scope": m.scope,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in conceptual_models
                ],
                "logical_models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "normalization_level": m.normalization_level,
                        "design_pattern": m.design_pattern,
                        "conceptual_model_id": m.conceptual_model_id,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in logical_models
                ],
                "physical_models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                        "database_type": m.database_type,
                        "deployment_environment": m.deployment_environment,
                        "logical_model_id": m.logical_model_id,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in physical_models
                ],
            }
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@data_architecture_bp.route("/api/data-lineage")
@login_required
def api_data_lineage():
    """Get all data lineage models."""
    try:
        lineage_models = DataLineage.query.limit(500).all()
        return jsonify(
            [
                {
                    "id": m.id,
                    "name": m.name,
                    "description": m.description,
                    "lineage_type": m.lineage_type,
                    "data_domain": m.data_domain,
                    "source_system": m.source_system,
                    "target_system": m.target_system,
                    "frequency": m.frequency,
                    "data_classification": m.data_classification,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in lineage_models
            ]
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@data_architecture_bp.route("/api/data-transformations")
@login_required
def api_data_transformations():
    """Get all data transformation models."""
    try:
        transformations = DataTransformation.query.limit(500).all()
        return jsonify(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "transformation_type": t.transformation_type,
                    "processing_language": t.processing_language,
                    "source_format": t.source_format,
                    "target_format": t.target_format,
                    "lineage_id": t.lineage_id,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in transformations
            ]
        )
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@data_architecture_bp.route("/api/data-models", methods=["POST"])
@login_required
@audit_log("create_data_model")
def create_data_model():
    """Create a new data model."""
    try:
        data = request.get_json()
        model_type = data.get("model_type")  # 'conceptual', 'logical', 'physical'

        if model_type == "conceptual":
            model = ConceptualDataModel(
                name=data["name"],
                description=data.get("description", ""),
                business_domain=data.get("business_domain"),
                scope=data.get("scope"),
                version=data.get("version"),
                data_steward=data.get("data_steward"),
                business_owner=data.get("business_owner"),
            )
        elif model_type == "logical":
            model = LogicalDataModel(
                name=data["name"],
                description=data.get("description", ""),
                conceptual_model_id=data.get("conceptual_model_id"),
                normalization_level=data.get("normalization_level"),
                design_pattern=data.get("design_pattern"),
                supports_transactions=data.get("supports_transactions", True),
                supports_concurrency=data.get("supports_concurrency", True),
            )
        elif model_type == "physical":
            model = PhysicalDataModel(
                name=data["name"],
                description=data.get("description", ""),
                logical_model_id=data.get("logical_model_id"),
                database_type=data.get("database_type"),
                database_version=data.get("database_version"),
                deployment_environment=data.get("deployment_environment"),
                schema_name=data.get("schema_name"),
            )
        else:
            return jsonify({"error": "Invalid model_type"}), 400

        from app import db

        db.session.add(model)
        db.session.commit()

        return (
            jsonify(
                {
                    "id": model.id,
                    "name": model.name,
                    "message": f"{model_type.title()} data model created successfully",
                }
            ),
            201,
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# ============================================================================
# Data Entity Catalog (REQ-DATA-001, REQ-DATA-002)
# ============================================================================


@data_architecture_bp.route("/data-entities")
@login_required
def data_entity_catalog():
    """Data entity catalog — browse, search, and filter data entities."""
    from app.models.process_data import DataDomain, DataEntity

    search = request.args.get("search", "").strip()
    classification = request.args.get("classification", "")
    domain_id = request.args.get("domain_id", "", type=str)
    entity_type = request.args.get("entity_type", "")

    query = DataEntity.query

    if search:
        query = query.filter(
            DataEntity.name.ilike(f"%{search}%")
            | DataEntity.business_name.ilike(f"%{search}%")
            | DataEntity.description.ilike(f"%{search}%")
        )
    if classification:
        query = query.filter(DataEntity.data_classification == classification)
    if domain_id:
        query = query.filter(DataEntity.domain_id == int(domain_id))
    if entity_type:
        query = query.filter(DataEntity.entity_type == entity_type)

    entities = query.order_by(DataEntity.name).all()
    domains = DataDomain.query.order_by(DataDomain.name).all()

    # Classification distribution for dashboard widget
    from sqlalchemy import func as sa_func
    classification_dist = dict(
        db.session.query(DataEntity.data_classification, sa_func.count(DataEntity.id))
        .group_by(DataEntity.data_classification)
        .all()
    )

    return render_template(
        "data_architecture/entity_catalog.html",
        entities=entities,
        domains=domains,
        search=search,
        classification=classification,
        domain_id=domain_id,
        entity_type=entity_type,
        classification_dist=classification_dist,
        total_count=len(entities),
    )


@data_architecture_bp.route("/data-entities/create", methods=["GET", "POST"])
@login_required
def create_data_entity():
    """Create a new data entity."""
    from flask import flash, redirect, url_for
    from app.models.process_data import DataDomain, DataEntity

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name is required.", "error")
            return redirect(request.url)

        try:
            domain_id = _domain_id_from_form(
                request.form.get("domain_id", type=int), allow_default=True
            )
            system_of_record_application_id = _system_of_record_application_id_from_form()
        except sor.DataSorError as exc:
            flash(str(exc), "error")
            return redirect(request.url)

        entity = DataEntity(
            name=name,
            business_name=request.form.get("business_name", "").strip() or None,
            description=request.form.get("description", "").strip() or None,
            domain_id=domain_id,
            entity_type=request.form.get("entity_type") or None,
            data_classification=request.form.get("data_classification") or None,
            contains_pii="contains_pii" in request.form,
            system_of_record_application_id=system_of_record_application_id,
            is_master_data="is_master_data" in request.form,
        )
        db.session.add(entity)
        db.session.commit()
        flash(f"Data entity '{name}' created.", "success")
        return redirect(url_for("data_architecture.data_entity_catalog"))

    domains = DataDomain.query.order_by(DataDomain.name).all()
    return render_template(
        "data_architecture/entity_form.html",
        entity=None,
        domains=domains,
        form_action="create",
        current_system_of_record_application=None,
    )


@data_architecture_bp.route("/data-entities/<int:entity_id>")
@login_required
def data_entity_detail(entity_id):
    """A data entity and its CRUD matrix: which applications create, read,
    update or delete it, read from the ArchiMate access relationships."""
    from app.models.process_data import DataEntity
    from app.modules.architecture.services.data_architecture_service import entity_access_matrix

    entity = DataEntity.query.filter_by(id=entity_id).first_or_404()
    return render_template(
        "data_architecture/entity_detail.html",
        entity=entity,
        access_rows=entity_access_matrix(entity),
    )


@data_architecture_bp.route("/data-entities/<int:entity_id>/access", methods=["POST"])
@login_required
def record_data_entity_access(entity_id):
    """Record which of create/read/update/delete an application performs on
    this data entity (one ArchiMate access relationship per application)."""
    from flask import flash, redirect, url_for
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import DataEntity
    from app.modules.architecture.services.data_architecture_service import record_entity_access

    entity = DataEntity.query.filter_by(id=entity_id).first_or_404()
    back = redirect(url_for("data_architecture.data_entity_detail", entity_id=entity.id))
    application_id = request.form.get("application_id", type=int)
    application = (
        ApplicationComponent.query.filter_by(id=application_id).first() if application_id else None
    )
    if application is None:
        flash("Choose an application from the list.", "error")
        return back
    try:
        record_entity_access(entity, application, request.form.getlist("operations"))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return back
    flash(f"Recorded how {application.name} uses {entity.name}.", "success")
    return back


@data_architecture_bp.route("/data-entities/<int:entity_id>/edit", methods=["GET", "POST"])
@login_required
def edit_data_entity(entity_id):
    """Edit an existing data entity."""
    from flask import flash, redirect, url_for
    from app.models.process_data import DataDomain

    entity = sor.get_entity(_current_org_id(), entity_id)
    if entity is None:
        return render_template("errors/404.html"), 404
    sor.backfill_system_of_record_application_links(_current_org_id(), [entity.id])
    db.session.refresh(entity)

    if request.method == "POST":
        try:
            domain_id = _domain_id_from_form(request.form.get("domain_id", type=int))
            system_of_record_application_id = _system_of_record_application_id_from_form()
        except sor.DataSorError as exc:
            flash(str(exc), "error")
            return redirect(request.url)

        entity.domain_id = domain_id or entity.domain_id
        entity.system_of_record_application_id = system_of_record_application_id
        entity.name = request.form.get("name", "").strip() or entity.name
        entity.business_name = request.form.get("business_name", "").strip() or None
        entity.description = request.form.get("description", "").strip() or None
        entity.entity_type = request.form.get("entity_type") or None
        entity.data_classification = request.form.get("data_classification") or None
        entity.contains_pii = "contains_pii" in request.form
        entity.is_master_data = "is_master_data" in request.form

        # ArchiMate is the backbone: keep the mirrored element's name (and
        # description) in sync on rename so the two never drift apart.
        if entity.archimate_element_id:
            from app.models.archimate_core import ArchiMateElement

            element = db.session.get(ArchiMateElement, entity.archimate_element_id)
            if element:
                element.name = entity.name
                element.description = entity.description or f"Data object for entity: {entity.name}"

        db.session.commit()
        flash(f"Data entity '{entity.name}' updated.", "success")
        return redirect(url_for("data_architecture.data_entity_catalog"))

    domains = DataDomain.query.order_by(DataDomain.name).all()
    return render_template(
        "data_architecture/entity_form.html",
        entity=entity,
        domains=domains,
        form_action="edit",
        current_system_of_record_application=sor.get_application(
            _current_org_id(), entity.system_of_record_application_id
        ),
    )


@data_architecture_bp.route("/data-entities/<int:entity_id>/delete", methods=["POST"])
@login_required
def delete_data_entity(entity_id):
    """Delete a data entity."""
    from flask import flash, redirect, url_for
    from app.models.process_data import DataEntity

    entity = DataEntity.query.get_or_404(entity_id)
    name = entity.name
    db.session.delete(entity)
    db.session.commit()
    flash(f"Data entity '{name}' deleted.", "success")
    return redirect(url_for("data_architecture.data_entity_catalog"))


@data_architecture_bp.route("/api/data-entities")
@login_required
def api_data_entities():
    """JSON API for data entities — supports search and filtering."""
    from app.models.process_data import DataEntity

    search = request.args.get("search", "").strip()
    limit = safe_int_arg('limit', 50, minimum=1, maximum=500)

    query = DataEntity.query
    if search:
        query = query.filter(DataEntity.name.ilike(f"%{search}%"))

    entities = query.order_by(DataEntity.name).limit(min(limit, 200)).all()

    return jsonify([
        {
            "id": e.id,
            "name": e.name,
            "business_name": e.business_name,
            "entity_type": e.entity_type,
            "data_classification": e.data_classification,
            "contains_pii": e.contains_pii,
            "data_quality_score": e.data_quality_score,
            "system_of_record": e.system_of_record,
        }
        for e in entities
    ])
