"""Shared helper functions for the Applications module routes."""

import logging
from collections import defaultdict

from flask import render_template
from werkzeug.exceptions import NotFound

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement

logger = logging.getLogger(__name__)


def _verify_app_in_org(app_id, org_id, *, raise_not_found=False):
    """Return the application only when it belongs to ``org_id``."""
    app = ApplicationComponent.query.filter_by(
        id=app_id,
        organization_id=org_id,
    ).first()
    if app is None and raise_not_found:
        raise NotFound()
    return app


def _query_archimate_by_layer(app_id):
    """Load all ArchiMate elements for an app, grouped by (layer, type).

    Collapses ~40 individual ArchiMateElement queries into a single DB query
    with Python-side grouping.
    """
    try:
        elements = ArchiMateElement.query.filter(
            ArchiMateElement.application_component_id == app_id
        ).all()
    except Exception:
        return defaultdict(list)

    grouped = defaultdict(list)
    for elem in elements:
        layer = (elem.layer or "other").lower()
        grouped[(layer, elem.type or "Unknown")].append(elem)
    return grouped


def _load_domain_model_elements(app_id, archimate_grouped):
    """Query domain-specific model tables and merge with ArchiMate elements.

    Returns dict of element lists, deduplicated by ID.
    """
    from app.models.application_layer import (
        ApplicationInterface,
        ApplicationService,
        DataObject,
    )
    from app.models.business_capabilities import Capability
    from app.models.business_layer import (
        BusinessActor,
        BusinessRole,
        BusinessService,
    )
    from app.models.technology_layer import Device, Node, SystemSoftware

    result = {}

    def _merge(archimate_list, direct_list):
        return list({e.id: e for e in (archimate_list + direct_list)}.values())

    def _safe_query(model, **kwargs):
        try:
            if hasattr(model, "application_component_id"):
                return model.query.filter_by(**kwargs).all()
        except Exception as e:
            logger.debug("Optional query for %s failed: %s", model.__name__ if hasattr(model, "__name__") else model, e)
        return []

    # Technology layer
    result["technology_nodes"] = _merge(
        archimate_grouped.get(("technology", "Node"), []),
        _safe_query(Node, application_component_id=app_id),
    )
    result["technology_devices"] = _merge(
        archimate_grouped.get(("technology", "Device"), []),
        _safe_query(Device, application_component_id=app_id),
    )
    result["technology_software"] = _merge(
        archimate_grouped.get(("technology", "SystemSoftware"), []),
        _safe_query(SystemSoftware, application_component_id=app_id),
    )
    result["technology_services"] = archimate_grouped.get(
        ("technology", "TechnologyService"), []
    )
    result["technology_interfaces"] = archimate_grouped.get(
        ("technology", "TechnologyInterface"), []
    )

    # Business layer
    result["business_services"] = _merge(
        archimate_grouped.get(("business", "BusinessService"), []),
        _safe_query(BusinessService, application_component_id=app_id),
    )
    result["business_processes"] = archimate_grouped.get(
        ("business", "BusinessProcess"), []
    )
    result["business_actors"] = _merge(
        archimate_grouped.get(("business", "BusinessActor"), []),
        _safe_query(BusinessActor, application_component_id=app_id),
    )
    result["business_roles"] = _merge(
        archimate_grouped.get(("business", "BusinessRole"), []),
        _safe_query(BusinessRole, application_component_id=app_id),
    )

    # Application layer
    result["application_services"] = _merge(
        archimate_grouped.get(("application", "ApplicationService"), []),
        _safe_query(ApplicationService, application_component_id=app_id),
    )
    result["application_interfaces_archimate"] = _merge(
        archimate_grouped.get(("application", "ApplicationInterface"), []),
        _safe_query(ApplicationInterface, application_component_id=app_id),
    )
    result["data_objects"] = _merge(
        archimate_grouped.get(("application", "DataObject"), []),
        _safe_query(DataObject, application_component_id=app_id),
    )

    # Strategy layer
    result["strategy_capabilities"] = _merge(
        archimate_grouped.get(("strategy", "Capability"), []),
        _safe_query(Capability, application_component_id=app_id),
    )
    result["strategy_resources"] = archimate_grouped.get(("strategy", "Resource"), [])
    result["courses_of_action"] = archimate_grouped.get(
        ("strategy", "CourseOfAction"), []
    )
    result["value_streams"] = archimate_grouped.get(("strategy", "ValueStream"), [])

    return result


def _format_date(value):
    """Format a date value for display."""
    if not value:
        return None
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return str(value)


def _cascade_delete_application(app_id):
    """Delete all child records for an application before ORM deletion.

    Uses SAVEPOINTs so a missing table or wrong column never aborts the outer
    transaction.  Covers every FK → application_components row found via:

        SELECT kcu.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ...
        WHERE ccu.table_name = 'application_components'
        AND rc.delete_rule = 'NO ACTION'

    Verified against production DB 2026-04-06.
    """
    from sqlalchemy import text as _sql

    _deletes = [
        # ── junction / mapping tables ─────────────────────────────────────
        "DELETE FROM unified_application_capability_mapping WHERE application_component_id = :id",
        "DELETE FROM application_capability_mapping WHERE application_component_id = :id",
        "DELETE FROM application_capability_coverage WHERE application_component_id = :id",
        "DELETE FROM application_process_support WHERE application_component_id = :id",
        "DELETE FROM application_technology_mapping WHERE application_component_id = :id",
        "DELETE FROM application_interface_mapping WHERE application_component_id = :id",
        "DELETE FROM application_requirement_mapping WHERE application_component_id = :id",
        "DELETE FROM application_business_actor_mapping WHERE application_component_id = :id",
        "DELETE FROM application_component_vendor_products WHERE application_component_id = :id",
        "DELETE FROM unified_capability_application_mappings WHERE application_component_id = :id",
        # ── application_* child tables (column = application_component_id) ─
        "DELETE FROM application_collaborations WHERE application_component_id = :id",
        "DELETE FROM application_component_embeddings WHERE application_component_id = :id",
        "DELETE FROM application_custom_field_values WHERE application_component_id = :id",
        "DELETE FROM application_data_objects WHERE application_component_id = :id",
        "DELETE FROM application_events WHERE application_component_id = :id",
        "DELETE FROM application_functions WHERE application_component_id = :id",
        "DELETE FROM application_interactions WHERE application_component_id = :id OR source_component_id = :id OR target_component_id = :id",
        "DELETE FROM application_interfaces WHERE application_component_id = :id",
        "DELETE FROM application_performance_metrics WHERE application_component_id = :id",
        "DELETE FROM application_processes WHERE application_component_id = :id",
        # rationalization_benefits has FK to application_rationalization_scores — delete first
        "DELETE FROM rationalization_benefits WHERE application_id = :id",
        "DELETE FROM application_rationalization_scores WHERE application_component_id = :id",
        "DELETE FROM application_services WHERE application_component_id = :id",
        "DELETE FROM application_versioning WHERE application_component_id = :id",
        "DELETE FROM deployment_pipelines WHERE application_component_id = :id",
        "DELETE FROM refactoring_tracking WHERE application_component_id = :id",
        "DELETE FROM technical_debt WHERE application_component_id = :id",
        # ── application_* child tables (column = application_id) ───────────
        "DELETE FROM application_business_metrics WHERE application_id = :id",
        "DELETE FROM application_costs WHERE application_id = :id",
        "DELETE FROM application_documents WHERE application_component_id = :id",
        "DELETE FROM application_ownership WHERE application_id = :id",
        "DELETE FROM application_roadmap_items WHERE application_id = :id",
        "DELETE FROM application_roi WHERE application_id = :id",
        "DELETE FROM application_technology_instances WHERE application_id = :id",
        "DELETE FROM application_usage WHERE application_id = :id",
        "DELETE FROM architecture_documents WHERE application_id = :id",
        "DELETE FROM architecture_sessions WHERE application_id = :id",
        "DELETE FROM decommission_plans WHERE application_id = :id",
        "DELETE FROM governance_system_links WHERE application_id = :id",
        "DELETE FROM integration_contracts WHERE application_id = :id",
        "DELETE FROM rationalization_audit_entries WHERE application_id = :id",
        "DELETE FROM vendor_contracts WHERE application_id = :id",
        # ── tables with multiple FK columns pointing to application_components ─
        "DELETE FROM application_dependencies WHERE source_app_id = :id OR target_app_id = :id",
        "DELETE FROM application_replacements WHERE legacy_app_id = :id OR replacement_app_id = :id",
        "DELETE FROM application_similarity_analysis WHERE app_1_id = :id OR app_2_id = :id OR recommended_survivor = :id",
        "DELETE FROM data_flows WHERE source_app_id = :id OR target_app_id = :id",
        "DELETE FROM duplicate_analyses WHERE application_1_id = :id OR application_2_id = :id",
        "DELETE FROM replacement_plans WHERE source_app_id = :id OR target_app_id = :id",
        "DELETE FROM roadmap_deliverables WHERE source_application_id = :id",
        "DELETE FROM roadmap_gaps WHERE source_application_id = :id",
        "DELETE FROM solution_integration_flows WHERE source_app_id = :id OR target_app_id = :id",
        # ── consolidation / dedup tables ───────────────────────────────────
        "DELETE FROM application_consolidation_recommendations WHERE primary_app_id = :id",
        "DELETE FROM consolidation_list_entries WHERE application_id = :id",
        "DELETE FROM consolidation_recommendations WHERE target_application_id = :id",
        "DELETE FROM duplicate_group_members WHERE application_id = :id",
        "DELETE FROM duplicate_app_process_mapping WHERE application_id = :id",
        # ── portfolio / solution linkage ───────────────────────────────────
        "DELETE FROM portfolio_initiative_applications WHERE application_id = :id",
        "DELETE FROM solution_applications WHERE application_component_id = :id",
        "DELETE FROM process_application_mapping WHERE application_id = :id",
        # ── physical / technology layer elements ───────────────────────────
        "DELETE FROM physical_distribution_networks WHERE application_component_id = :id",
        "DELETE FROM physical_equipment WHERE application_component_id = :id",
        "DELETE FROM physical_facilities WHERE application_component_id = :id",
        "DELETE FROM physical_materials WHERE application_component_id = :id",
        "DELETE FROM technology_devices WHERE application_component_id = :id",
        "DELETE FROM technology_interfaces WHERE application_component_id = :id",
        "DELETE FROM technology_nodes WHERE application_component_id = :id",
        "DELETE FROM technology_services WHERE application_component_id = :id",
        "DELETE FROM technology_system_software WHERE application_component_id = :id",
        # ── business layer elements ────────────────────────────────────────
        "DELETE FROM business_objects WHERE application_component_id = :id",
        "DELETE FROM business_processes WHERE application_component_id = :id",
        "DELETE FROM business_roles WHERE application_component_id = :id",
        "DELETE FROM business_services WHERE application_component_id = :id",
        # ── misc ───────────────────────────────────────────────────────────
        "DELETE FROM batch_import_application WHERE committed_application_id = :id",
        "DELETE FROM integration_impact_registers WHERE target_app_id = :id",
        "DELETE FROM platform_migration_scopes WHERE source_platform_app_id = :id",
        "DELETE FROM data_stores WHERE app_component_id = :id",
        # ── arb / governance ───────────────────────────────────────────────
        "DELETE FROM arb_submission_packs WHERE solution_id = :id",
        # ── legacy column aliases kept for backward compat ─────────────────
        "DELETE FROM application_interface WHERE source_application_id = :id OR target_application_id = :id",
        "DELETE FROM application_cost_records WHERE application_id = :id",
        "DELETE FROM capability_governance_records WHERE application_id = :id",
        "DELETE FROM unified_group_members WHERE application_component_id = :id",
        "DELETE FROM architecture_session WHERE application_id = :id",
        "DELETE FROM consolidation_list_item WHERE application_id = :id",
    ]

    for _stmt in _deletes:
        _sp = db.session.begin_nested()
        try:
            db.session.execute(_sql(_stmt), {"id": app_id})
            _sp.commit()
        except Exception:
            _sp.rollback()
            logger.debug("cascade skip: %s", _stmt[:80], exc_info=False)


def _cleanup_application_relationships(app_id):
    """Clean up related records before deleting an application.

    Delegates to _cascade_delete_application which covers all NO-ACTION FK
    chains discovered on production 2026-04-06.
    """
    _cascade_delete_application(app_id)


def _delete_mirror_archimate_element(element_id):
    """Delete the ArchiMateElement that mirrors a deleted application.

    Every ApplicationComponent gets an ArchiMateElement created for it by the
    ``before_insert`` listener in ``app.models.application_portfolio``. Deleting
    the application without deleting that element left a permanent orphan in the
    architecture repository (finding C-02): the palette, relationship matrix and
    OEF export kept showing applications the user had already removed.

    Deliberately narrow: only the element the application itself points at, and
    only the ArchiMate relationships in which that element is an endpoint (those
    relationships describe the deleted application, so they cannot survive it).
    Nothing else that references the element is touched — if some other row
    (a diagram node, a parent link) still holds a foreign key, the delete fails
    inside its SAVEPOINT and is *reported* rather than cascaded through.

    Returns ``{"elements_deleted": int, "relationships_deleted": int,
    "errors": [str]}``. Never raises.
    """
    result = {"elements_deleted": 0, "relationships_deleted": 0, "errors": []}
    if not element_id:
        return result

    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    _sp = db.session.begin_nested()
    try:
        # ORM bulk delete: tenant-filtered by do_orm_execute inside a request.
        result["relationships_deleted"] = ArchiMateRelationship.query.filter(
            db.or_(
                ArchiMateRelationship.source_id == element_id,
                ArchiMateRelationship.target_id == element_id,
            )
        ).delete(synchronize_session=False)
        result["elements_deleted"] = ArchiMateElement.query.filter(
            ArchiMateElement.id == element_id
        ).delete(synchronize_session=False)
        _sp.commit()
    except Exception as exc:
        _sp.rollback()
        result["relationships_deleted"] = 0
        result["elements_deleted"] = 0
        result["errors"].append(
            f"ArchiMate element {element_id} could not be removed "
            f"({exc.__class__.__name__}); it is still referenced elsewhere"
        )
        logger.warning(
            "Mirror ArchiMate element %s not deleted", element_id, exc_info=True
        )
    return result


def _soft_delete_mirror_archimate_element(element_id, deleted_by=None):
    """Soft-delete the ArchiMateElement mirroring a soft-deleted application.

    Companion to ``_delete_mirror_archimate_element`` for the bulk-delete path
    (finding C-02 reopened by 9cda379): bulk-delete soft-deletes the
    ApplicationComponent for recoverability, but originally left the mirror
    element live, so it stayed visible in the composer palette, relationship
    matrix, OEF export and AI context after the user believed it was gone.

    Sets ``deleted_at``/``deleted_by`` rather than deleting the row, so the
    element (and, unlike the hard-delete path, its relationships) survive
    for a restore — the same recoverability 9cda379 bought for the
    application row. The unconditional soft-delete filter in
    ``app/middleware/tenant_isolation.py`` then hides it from every ORM read
    exactly the way a soft-deleted ApplicationComponent is hidden.

    Returns ``{"elements_deleted": int, "relationships_deleted": int,
    "errors": [str]}`` — ``elements_deleted``/``relationships_deleted`` here
    mean "hidden by this call", matching the vocabulary the hard-delete
    paths already report. Never raises.
    """
    from datetime import datetime

    result = {"elements_deleted": 0, "relationships_deleted": 0, "errors": []}
    if not element_id:
        return result

    from app.models.archimate_core import ArchiMateElement

    _sp = db.session.begin_nested()
    try:
        updated = (
            ArchiMateElement.query.filter(
                ArchiMateElement.id == element_id,
                ArchiMateElement.deleted_at.is_(None),
            ).update(
                {
                    ArchiMateElement.deleted_at: datetime.utcnow(),
                    ArchiMateElement.deleted_by: deleted_by,
                },
                synchronize_session=False,
            )
        )
        result["elements_deleted"] = updated
        _sp.commit()
    except Exception as exc:
        _sp.rollback()
        result["errors"].append(
            f"ArchiMate element {element_id} could not be hidden "
            f"({exc.__class__.__name__})"
        )
        logger.warning(
            "Mirror ArchiMate element %s not soft-deleted", element_id, exc_info=True
        )
    return result


def _vendors_impl(
    joinedload,
    VendorOrganization,
    VendorProduct,
    VENDOR_DOMAINS,
    get_domain_color_classes,
    get_domain_filter_choices,
    get_domain_label,
    vendor_type_filter,
    domain_filter,
    contract_status_filter,
    search_query,
    page,
    per_page,
):
    """Shared vendor list implementation with filtering, pagination, and stats."""
    from app.models.vendor import application_vendor_products
    from sqlalchemy import func

    # Limit per_page to reasonable values
    per_page = min(max(per_page, 10), 100)

    # Build query with eager loading
    query = VendorOrganization.query.options(joinedload(VendorOrganization.products))

    if vendor_type_filter != "all":
        query = query.filter_by(vendor_type=vendor_type_filter)

    if search_query:
        query = query.filter(VendorOrganization.name.ilike(f"%{search_query}%"))

    # Filter by domain - need to filter vendors that have products in this domain
    if domain_filter != "all":
        vendor_ids_with_domain = (
            db.session.query(VendorProduct.vendor_organization_id)
            .filter(VendorProduct.product_family_name == domain_filter)
            .distinct()
        )
        query = query.filter(VendorOrganization.id.in_(vendor_ids_with_domain))

    # Filter by contract status
    if contract_status_filter != "all":
        if contract_status_filter == "catalog":
            query = query.filter(
                db.or_(
                    VendorOrganization.contract_status == "catalog",
                    VendorOrganization.contract_status.is_(None),
                )
            )
        else:
            query = query.filter(
                VendorOrganization.contract_status == contract_status_filter
            )

    # Order by strategic tier and name
    query = query.order_by(
        VendorOrganization.enterprise_readiness_score.desc().nullslast(),
        VendorOrganization.name,
    )

    # Paginate results
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    vendors = pagination.items

    # Compute ArchiMate element counts for each vendor (via application_vendor_products)
    vendor_ids = [v.id for v in vendors]
    if vendor_ids:
        arch_counts = dict(
            db.session.query(
                VendorProduct.vendor_organization_id,
                func.count(
                    func.distinct(application_vendor_products.c.archimate_element_id)
                ),
            )
            .join(
                application_vendor_products,
                application_vendor_products.c.vendor_product_id == VendorProduct.id,
            )
            .filter(VendorProduct.vendor_organization_id.in_(vendor_ids))
            .group_by(VendorProduct.vendor_organization_id)
            .all()
        )

        # APQC mapping counts - model doesn't exist yet, placeholder
        apqc_counts = {}

        for v in vendors:
            v.archimate_element_count = arch_counts.get(v.id, 0)
            v.apqc_process_count = apqc_counts.get(v.id, 0)
    else:
        for v in vendors:
            v.archimate_element_count = 0
            v.apqc_process_count = 0

    # Calculate statistics in a single aggregate query (3 counts → 1 query)
    from sqlalchemy import case, and_
    stats_row = db.session.query(
        db.func.count(VendorOrganization.id).label("total"),
        db.func.count(case(
            (VendorOrganization.status == "active", VendorOrganization.id),
        )).label("active"),
    ).one()
    total_vendors = stats_row.total
    active_vendors = stats_row.active

    # "Strategic" = vendor with >5 apps linked via vendor products.
    # enterprise_readiness_score is currently unpopulated (all NULL), so we
    # use application depth as the proxy for strategic importance instead.
    from sqlalchemy import text as _text
    strategic_row = db.session.execute(_text("""
        SELECT COUNT(*) FROM (
            SELECT vo.id
            FROM vendor_organizations vo
            JOIN vendor_products vp ON vp.vendor_organization_id = vo.id
            JOIN application_vendor_product_mappings m ON m.vendor_product_id = vp.id
            GROUP BY vo.id
            HAVING COUNT(DISTINCT m.application_component_id) > 5
        ) s
    """)).scalar()
    strategic_vendors = int(strategic_row or 0)

    # Count total products (separate table)
    total_products = VendorProduct.query.count()

    # Get domain distribution for stats
    domain_distribution = (
        db.session.query(
            VendorProduct.product_family_name, db.func.count(VendorProduct.id)
        )
        .group_by(VendorProduct.product_family_name)
        .all()
    )

    from datetime import datetime, timedelta

    try:
        now = datetime.utcnow()
        ninety_days = now + timedelta(days=90)

        # DEF-050, Capgemini dry-run: this summed
        # VendorOrganization.contract_value_annual / read
        # VendorOrganization.contract_status/contract_end_date — fields
        # nothing writes to. The real "Add Contract" flow
        # (app/modules/procurement) creates a VendorContract row (vendor_id
        # FK, annual_cost, status, end_date), a completely different table.
        # So "Portfolio ACV" stayed "—" no matter how many real, active
        # contracts existed. Sum from VendorContract instead.
        from app.models.application_portfolio import VendorContract

        acv_row = db.session.query(
            db.func.sum(VendorContract.annual_cost).label("total_acv"),
            db.func.count(
                db.case(
                    (
                        and_(
                            VendorContract.end_date >= now,
                            VendorContract.end_date <= ninety_days,
                        ),
                        VendorContract.id,
                    )
                )
            ).label("renewals_due"),
        ).filter(
            VendorContract.status == "active",
            VendorContract.vendor_id.isnot(None),
        ).one()

        total_acv = float(acv_row.total_acv or 0)
        renewals_due = int(acv_row.renewals_due or 0)
    except Exception:
        total_acv = 0
        renewals_due = 0

    stats = {
        "total": total_vendors,
        "active": active_vendors,
        "strategic": strategic_vendors,
        "total_products": total_products,
        "domain_distribution": {d[0] or "other": d[1] for d in domain_distribution},
        "total_acv": total_acv,
        "renewals_due": renewals_due,
    }

    # Populate the Type filter from the vendor types actually stored, so the
    # dropdown offers values that match rows (D12: it used to hard-code
    # Cloud/Enterprise/Hybrid, none of which are real vendor_type values).
    vendor_type_options = [
        r[0]
        for r in db.session.query(VendorOrganization.vendor_type)
        .filter(
            VendorOrganization.vendor_type.isnot(None),
            VendorOrganization.vendor_type != "",
        )
        .distinct()
        .order_by(VendorOrganization.vendor_type)
        .all()
    ]

    vendor_type_filter_options = [
        {"value": vt, "label": vt} for vt in vendor_type_options
    ]

    return render_template(
        "vendors/list.html",
        vendors=vendors,
        stats=stats,
        vendor_type_filter_options=vendor_type_filter_options,
        vendor_type_filter=vendor_type_filter,
        domain_filter=domain_filter,
        contract_status_filter=contract_status_filter,
        search_query=search_query,
        pagination=pagination,
        per_page=per_page,
        domain_choices=get_domain_filter_choices(),
        get_domain_label=get_domain_label,
        get_domain_color_classes=get_domain_color_classes,
        VENDOR_DOMAINS=VENDOR_DOMAINS,
    )


def _sanitize_csv_value(value):
    """Escape CSV values that could be interpreted as formulas by spreadsheet software."""
    if not isinstance(value, str) or not value:
        return value
    if value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def calculate_match_confidence(application, vendor_product, method):
    """
    Calculate confidence score for application-vendor matching.

    Returns confidence percentage (0 - 100).
    """
    confidence = 0

    if method == "name":
        # Name-based matching using string similarity
        app_name = application.name.lower() if application.name else ""
        vendor_name = vendor_product.name.lower() if vendor_product.name else ""
        vendor_org_name = (
            vendor_product.vendor_organization.name.lower()
            if vendor_product.vendor_organization
            else ""
        )

        # Exact name match
        if app_name == vendor_name or app_name == vendor_org_name:
            confidence = 95
        # Partial name match
        elif vendor_name in app_name or app_name in vendor_name:
            confidence = 75
        elif vendor_org_name in app_name or app_name in vendor_org_name:
            confidence = 70
        # Word overlap
        else:
            app_words = set(app_name.split())
            vendor_words = set(vendor_name.split()) | set(vendor_org_name.split())
            overlap = len(app_words & vendor_words)
            if overlap > 0:
                confidence = min(50, 30 + (overlap * 10))

    elif method == "capability":
        # Capability-based matching
        confidence = 60  # Base confidence for capability method

        # Boost confidence if both have descriptions
        if application.description and vendor_product.description:
            app_desc = application.description.lower()
            vendor_desc = vendor_product.description.lower()

            # Check for keyword overlap
            common_keywords = [
                "system",
                "management",
                "service",
                "platform",
                "solution",
                "software",
            ]
            overlap = sum(
                1
                for keyword in common_keywords
                if keyword in app_desc and keyword in vendor_desc
            )
            if overlap > 0:
                confidence += overlap * 5

        confidence = min(95, confidence)

    elif method == "ai":
        # AI-powered matching (simplified for now)
        confidence = calculate_match_confidence(application, vendor_product, "name")

        if application.description and vendor_product.description:
            desc_confidence = calculate_match_confidence(
                application, vendor_product, "capability"
            )
            confidence = max(confidence, desc_confidence)

        # Add AI boost
        confidence = min(95, confidence + 10)

    return confidence


def get_matching_reason(application, vendor_product, method):
    """
    Generate human-readable reason for the match.
    """
    if method == "name":
        app_name = application.name or ""
        vendor_name = vendor_product.name or ""
        vendor_org_name = (
            vendor_product.vendor_organization.name
            if vendor_product.vendor_organization
            else ""
        )

        if app_name.lower() == vendor_name.lower():
            return f"Exact name match with vendor product '{vendor_name}'"
        elif app_name.lower() == vendor_org_name.lower():
            return f"Exact name match with vendor organization '{vendor_org_name}'"
        elif vendor_name.lower() in app_name.lower():
            return f"Name contains vendor product name '{vendor_name}'"
        elif vendor_org_name.lower() in app_name.lower():
            return f"Name contains vendor organization name '{vendor_org_name}'"
        else:
            return f"Name similarity between '{app_name}' and '{vendor_name}'"

    elif method == "capability":
        return "Capability overlap between application and vendor product offerings"

    elif method == "ai":
        return "AI-powered semantic analysis indicates strong relationship"

    return "Matching based on available data"
