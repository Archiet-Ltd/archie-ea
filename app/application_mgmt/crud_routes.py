"""
Core CRUD Routes for Application Management

Handles create, read (legacy redirect), update, and delete operations for ApplicationComponent.
"""

from datetime import datetime
from decimal import Decimal

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.models import ArchiMateElement, ArchiMateRelationship
from ..models.requirements import Requirement
from ..services.application_cost_accessor import set_annual_cost
from ..utils.html_sanitizer import sanitize_html
from ..utils.validators import (
    validate_application_name,
    validate_description,
    validate_integer,
    validate_string,
)
from . import application_mgmt
from .forms import ApplicationComponentForm
from .routes import _build_vendor_product_choices, _sync_vendor_products

@application_mgmt.route("/applications/create", methods=["GET", "POST"])
@login_required
def application_create():
    """Create new Application Component"""
    form = ApplicationComponentForm()
    vendor_choices = _build_vendor_product_choices()
    form.vendor_products.choices = vendor_choices

    # Check if this is a minimal form submission from the drawer (has basic fields but not full form fields)
    if request.method == "POST":
        has_drawer_fields = (
            request.form.get("name")
            and request.form.get("component_type")
            and request.form.get("deployment_status")
        )
        has_full_form_fields = request.form.get(
            "application_category"
        ) or request.form.get("architecture_style")

        # If it's a drawer submission (has basic fields but not full form), handle it directly
        if has_drawer_fields and not has_full_form_fields:
            validation_errors = []

            # Validate name (required)
            name = request.form.get("name", "")
            is_valid, validated_name, error = validate_application_name(name)
            if not is_valid:
                validation_errors.append(error)
            else:
                name = sanitize_html(validated_name)

            # Validate component_type (required)
            component_type = request.form.get("component_type", "")
            is_valid, validated_type, error = validate_string(
                component_type,
                max_length=100,
                min_length=1,
                field_name="component_type",
                required=True,
            )
            if not is_valid:
                validation_errors.append(error)
            else:
                component_type = validated_type

            # Validate deployment_status (required)
            deployment_status = request.form.get("deployment_status", "")
            is_valid, validated_status, error = validate_string(
                deployment_status,
                max_length=50,
                min_length=1,
                field_name="deployment_status",
                required=True,
            )
            if not is_valid:
                validation_errors.append(error)
            else:
                deployment_status = validated_status

            # Validate optional fields
            description = request.form.get("description", "")
            is_valid, validated_desc, error = validate_description(description)
            if not is_valid:
                validation_errors.append(error)
            else:
                description = sanitize_html(validated_desc) if validated_desc else ""

            technology_stack = request.form.get("technology_stack", "")
            is_valid, validated_tech, error = validate_string(
                technology_stack, max_length=500, field_name="technology_stack"
            )
            if not is_valid:
                validation_errors.append(error)
            else:
                technology_stack = (
                    sanitize_html(validated_tech) if validated_tech else ""
                )

            version = request.form.get("version", "")
            is_valid, validated_version, error = validate_string(
                version, max_length=50, field_name="version"
            )
            if not is_valid:
                validation_errors.append(error)
            else:
                version = sanitize_html(validated_version) if validated_version else ""

            business_domain = request.form.get("business_domain", "")
            is_valid, validated_domain, error = validate_string(
                business_domain, max_length=100, field_name="business_domain"
            )
            if not is_valid:
                validation_errors.append(error)
            else:
                business_domain = (
                    sanitize_html(validated_domain) if validated_domain else ""
                )

            business_owner = request.form.get("business_owner", "")
            is_valid, validated_owner, error = validate_string(
                business_owner, max_length=255, field_name="business_owner"
            )
            if not is_valid:
                validation_errors.append(error)
            else:
                business_owner = (
                    sanitize_html(validated_owner) if validated_owner else ""
                )

            # Validate user_count (optional integer)
            user_count_raw = request.form.get("user_count")
            user_count = None
            if user_count_raw:
                is_valid, validated_count, error = validate_integer(
                    user_count_raw, min_val=0, max_val=10000000, field_name="user_count"
                )
                if not is_valid:
                    validation_errors.append(error)
                else:
                    user_count = validated_count

            # Return validation errors if any
            if validation_errors:
                for error in validation_errors:
                    flash(error, "error")
                return redirect(url_for("unified_applications.application_list"))

            try:
                app = ApplicationComponent(
                    name=name,
                    description=description,
                    component_type=component_type,
                    technology_stack=technology_stack,
                    version=version,
                    deployment_status=deployment_status,
                    business_domain=business_domain,
                    business_owner=business_owner,
                    user_count=user_count,
                )

                db.session.add(app)
                db.session.commit()

                flash(
                    f'Application Component "{app.name}" created successfully!',
                    "success",
                )
                return redirect(
                    url_for(
                        "unified_applications.application_detail",
                        id=app.id,
                        tab="architecture",
                    )
                )
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error creating application from drawer: {e}")
                flash("Error creating application. Please try again.", "error")
                return redirect(url_for("unified_applications.application_list"))

    if form.validate_on_submit():
        selected_vendor_ids = [
            value for value in (form.vendor_products.data or []) if value
        ]
        desired_status = (form.deployment_status.data or "").lower()

        if desired_status == "production" and not selected_vendor_ids:
            message = "Production applications must select at least one vendor product."
            if not vendor_choices:
                message += " Add vendor products to the catalog first."
            flash(message, "error")
            return render_template(
                "applications/create.html",
                form=form,
                mode="create",
                app=None,
            )

        app = ApplicationComponent(
            name=form.name.data,
            description=form.description.data,
            component_type=form.component_type.data,
            application_category=form.application_category.data,
            architecture_style=form.architecture_style.data,
            technology_stack=form.technology_stack.data,
            version=form.version.data,
            deployment_status=form.deployment_status.data,
            business_domain=form.business_domain.data,
            business_owner=form.business_owner.data,
            product_manager=form.product_manager.data,
            development_team=form.development_team.data,
            technical_lead=form.technical_lead.data,
            architecture_domain=form.architecture_domain.data,
            support_team=form.support_team.data,
            user_count=form.user_count.data,
            business_criticality=form.business_criticality.data,
            programming_languages=form.programming_languages.data,
            frameworks=form.frameworks.data,
            primary_database=form.primary_database.data,
            cache_technology=form.cache_technology.data,
            message_queue=form.message_queue.data,
            repository_type=form.repository_type.data,
            version_control_url=form.version_control_url.data,
            main_branch=form.main_branch.data,
            deployment_model=form.deployment_model.data,
            cloud_provider=form.cloud_provider.data,
            deployment_region=form.deployment_region.data,
            container_image=form.container_image.data,
            kubernetes_namespace=form.kubernetes_namespace.data,
            user_type=form.user_type.data,
            concurrent_users_max=form.concurrent_users_max.data,
            average_daily_users=form.average_daily_users.data,
            geographic_distribution=form.geographic_distribution.data,
            response_time_target_ms=form.response_time_target_ms.data,
            throughput_target_tps=form.throughput_target_tps.data,
            current_response_time_ms=form.current_response_time_ms.data,
            current_throughput_tps=form.current_throughput_tps.data,
            scalability_model=form.scalability_model.data,
            max_instances=form.max_instances.data,
            min_instances=form.min_instances.data,
            sla_availability_percentage=form.sla_availability_percentage.data,
            current_uptime_percentage=form.current_uptime_percentage.data,
            disaster_recovery_enabled=form.disaster_recovery_enabled.data,
            rpo_hours=form.rpo_hours.data,
            rto_hours=form.rto_hours.data,
            backup_frequency=form.backup_frequency.data,
            last_backup_date=form.last_backup_date.data,
            authentication_method=form.authentication_method.data,
            authorization_model=form.authorization_model.data,
            encryption_at_rest=form.encryption_at_rest.data,
            encryption_in_transit=form.encryption_in_transit.data,
            pii_data_processed=form.pii_data_processed.data,
            gdpr_compliant=form.gdpr_compliant.data,
            compliance_tags=form.compliance_tags.data,
            last_security_audit_date=form.last_security_audit_date.data,
            last_penetration_test_date=form.last_penetration_test_date.data,
            interfaces_count=form.interfaces_count.data,
            dependencies_count=form.dependencies_count.data,
            integration_pattern=form.integration_pattern.data,
            exposes_api=form.exposes_api.data,
            api_documentation_url=form.api_documentation_url.data,
            primary_data_store=form.primary_data_store.data,
            database_size_gb=form.database_size_gb.data,
            data_retention_policy=form.data_retention_policy.data,
            data_classification=form.data_classification.data,
            master_data_source=form.master_data_source.data,
            license_type=form.license_type.data,
            license_cost_annual=form.license_cost_annual.data,
            infrastructure_cost_monthly=form.infrastructure_cost_monthly.data,
            development_cost_annual=form.development_cost_annual.data,
            maintenance_cost_annual=form.maintenance_cost_annual.data,
            cost_center=form.cost_center.data,
            go_live_date=form.go_live_date.data,
            last_major_release_date=form.last_major_release_date.data,
            next_planned_release_date=form.next_planned_release_date.data,
            end_of_life_date=form.end_of_life_date.data,
            retirement_date=form.retirement_date.data,
            replacement_application=form.replacement_application.data,
            ci_cd_pipeline_url=form.ci_cd_pipeline_url.data,
            build_tool=form.build_tool.data,
            automated_testing_coverage=form.automated_testing_coverage.data,
            deployment_frequency=form.deployment_frequency.data,
            mean_time_to_recovery_hours=form.mean_time_to_recovery_hours.data,
            change_failure_rate_percent=form.change_failure_rate_percent.data,
            monitoring_enabled=form.monitoring_enabled.data,
            monitoring_tool=form.monitoring_tool.data,
            logging_enabled=form.logging_enabled.data,
            logging_tool=form.logging_tool.data,
            tracing_enabled=form.tracing_enabled.data,
            tracing_tool=form.tracing_tool.data,
            apm_enabled=form.apm_enabled.data,
            health_check_url=form.health_check_url.data,
            code_quality_score=form.code_quality_score.data,
            technical_debt_hours=form.technical_debt_hours.data,
            bugs_count=form.bugs_count.data,
            vulnerabilities_count=form.vulnerabilities_count.data,
            code_coverage_percent=form.code_coverage_percent.data,
            last_code_quality_scan=form.last_code_quality_scan.data,
            documentation_url=form.documentation_url.data,
            architecture_diagram_url=form.architecture_diagram_url.data,
            runbook_url=form.runbook_url.data,
            user_manual_url=form.user_manual_url.data,
            tags=form.tags.data,
            notes=form.notes.data,
        )

        if form.total_cost_of_ownership.data is not None:
            set_annual_cost(app, Decimal(str(form.total_cost_of_ownership.data)))

        db.session.add(app)
        db.session.flush()

        if app.archimate_element_id:
            _sync_vendor_products(app.archimate_element_id, selected_vendor_ids)

        db.session.commit()

        flash(f'Application Component "{app.name}" created successfully!', "success")
        if (
            app.deployment_status or ""
        ).lower() == "production" and not selected_vendor_ids:
            flash(
                "Add at least one vendor product from the Vendor Footprint card to complete production readiness.",
                "warning",
            )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    if request.method == "GET" and vendor_choices:
        form.vendor_products.data = []

    return render_template(
        "applications/create.html", form=form, mode="create", app=None
    )


@application_mgmt.route(
    "/applications/<int:id>",
    strict_slashes=False,
    endpoint="legacy_application_detail_redirect",
)
@login_required
def legacy_application_detail_redirect(id):
    """Legacy /dashboard/applications/<id> — redirect to canonical /applications/<id>."""
    return redirect(
        url_for("unified_applications.application_detail", id=id, **request.args),
        code=301,
    )


@application_mgmt.route("/applications/<int:id>/edit", methods=["GET", "POST"])
@login_required
def application_edit(id):
    """Edit Application Component"""
    app = ApplicationComponent.query.get_or_404(id)
    form = ApplicationComponentForm(obj=app)

    vendor_choices = _build_vendor_product_choices()
    form.vendor_products.choices = vendor_choices

    existing_vendor_ids = []
    # ApplicationComponent has archimate_element_id (FK) but no archimate_element
    # relationship; guard so the edit page renders instead of 500ing.
    _arch_elem = getattr(app, "archimate_element", None)
    if _arch_elem is not None:
        existing_vendor_ids = [vp.id for vp in getattr(_arch_elem, "vendor_products", [])]

    # Map booleans and specific fields for the form
    if request.method == "GET":
        form.vendor_products.data = existing_vendor_ids

    if form.validate_on_submit():
        selected_vendor_ids = [
            value for value in (form.vendor_products.data or []) if value
        ]
        desired_status = (form.deployment_status.data or "").lower()

        if desired_status == "production" and not selected_vendor_ids:
            message = "Production applications must retain at least one vendor product."
            if not vendor_choices:
                message += " Add vendor products to the catalog first."
            flash(message, "error")
            return render_template(
                "applications/edit.html",
                form=form,
                mode="edit",
                # The template reads `application`, not `app`. Passing only `app`
                # raised UndefinedError on the very first line ({{ application.name }}),
                # so the validation-failure branch 500'd instead of redisplaying
                # the form with its error message.
                application=app,
            )

        app.name = form.name.data
        app.description = form.description.data
        app.component_type = form.component_type.data
        app.application_category = form.application_category.data
        app.architecture_style = form.architecture_style.data
        app.technology_stack = form.technology_stack.data
        app.version = form.version.data
        app.deployment_status = form.deployment_status.data
        app.business_domain = form.business_domain.data
        app.business_owner = form.business_owner.data
        app.product_manager = form.product_manager.data
        app.development_team = form.development_team.data
        app.technical_lead = form.technical_lead.data
        app.architecture_domain = form.architecture_domain.data
        app.support_team = form.support_team.data
        app.user_count = form.user_count.data
        app.business_criticality = form.business_criticality.data
        app.programming_languages = form.programming_languages.data
        app.frameworks = form.frameworks.data
        app.primary_database = form.primary_database.data
        app.cache_technology = form.cache_technology.data
        app.message_queue = form.message_queue.data
        app.repository_type = form.repository_type.data
        app.version_control_url = form.version_control_url.data
        app.main_branch = form.main_branch.data
        app.deployment_model = form.deployment_model.data
        app.cloud_provider = form.cloud_provider.data
        app.deployment_region = form.deployment_region.data
        app.container_image = form.container_image.data
        app.kubernetes_namespace = form.kubernetes_namespace.data
        app.user_type = form.user_type.data
        app.concurrent_users_max = form.concurrent_users_max.data
        app.average_daily_users = form.average_daily_users.data
        app.geographic_distribution = form.geographic_distribution.data
        app.response_time_target_ms = form.response_time_target_ms.data
        app.throughput_target_tps = form.throughput_target_tps.data
        app.current_response_time_ms = form.current_response_time_ms.data
        app.current_throughput_tps = form.current_throughput_tps.data
        app.scalability_model = form.scalability_model.data
        app.max_instances = form.max_instances.data
        app.min_instances = form.min_instances.data
        app.sla_availability_percentage = form.sla_availability_percentage.data
        app.current_uptime_percentage = form.current_uptime_percentage.data
        app.disaster_recovery_enabled = form.disaster_recovery_enabled.data
        app.rpo_hours = form.rpo_hours.data
        app.rto_hours = form.rto_hours.data
        app.backup_frequency = form.backup_frequency.data
        app.last_backup_date = form.last_backup_date.data
        app.authentication_method = form.authentication_method.data
        app.authorization_model = form.authorization_model.data
        app.encryption_at_rest = form.encryption_at_rest.data
        app.encryption_in_transit = form.encryption_in_transit.data
        app.pii_data_processed = form.pii_data_processed.data
        app.gdpr_compliant = form.gdpr_compliant.data
        app.compliance_tags = form.compliance_tags.data
        app.last_security_audit_date = form.last_security_audit_date.data
        app.last_penetration_test_date = form.last_penetration_test_date.data
        app.interfaces_count = form.interfaces_count.data
        app.dependencies_count = form.dependencies_count.data
        app.integration_pattern = form.integration_pattern.data
        app.exposes_api = form.exposes_api.data
        app.api_documentation_url = form.api_documentation_url.data
        app.primary_data_store = form.primary_data_store.data
        app.database_size_gb = form.database_size_gb.data
        app.data_retention_policy = form.data_retention_policy.data
        app.data_classification = form.data_classification.data
        app.master_data_source = form.master_data_source.data
        app.license_type = form.license_type.data
        app.license_cost_annual = form.license_cost_annual.data
        app.infrastructure_cost_monthly = form.infrastructure_cost_monthly.data
        app.development_cost_annual = form.development_cost_annual.data
        app.maintenance_cost_annual = form.maintenance_cost_annual.data
        if form.total_cost_of_ownership.data is not None:
            set_annual_cost(app, Decimal(str(form.total_cost_of_ownership.data)))
        else:
            set_annual_cost(app, None)
        app.cost_center = form.cost_center.data
        app.go_live_date = form.go_live_date.data
        app.last_major_release_date = form.last_major_release_date.data
        app.next_planned_release_date = form.next_planned_release_date.data
        app.end_of_life_date = form.end_of_life_date.data
        app.retirement_date = form.retirement_date.data
        app.replacement_application = form.replacement_application.data
        app.ci_cd_pipeline_url = form.ci_cd_pipeline_url.data
        app.build_tool = form.build_tool.data
        app.automated_testing_coverage = form.automated_testing_coverage.data
        app.deployment_frequency = form.deployment_frequency.data
        app.mean_time_to_recovery_hours = form.mean_time_to_recovery_hours.data
        app.change_failure_rate_percent = form.change_failure_rate_percent.data
        app.monitoring_enabled = form.monitoring_enabled.data
        app.monitoring_tool = form.monitoring_tool.data
        app.logging_enabled = form.logging_enabled.data
        app.logging_tool = form.logging_tool.data
        app.tracing_enabled = form.tracing_enabled.data
        app.tracing_tool = form.tracing_tool.data
        app.apm_enabled = form.apm_enabled.data
        app.health_check_url = form.health_check_url.data
        app.code_quality_score = form.code_quality_score.data
        app.technical_debt_hours = form.technical_debt_hours.data
        app.bugs_count = form.bugs_count.data
        app.vulnerabilities_count = form.vulnerabilities_count.data
        app.code_coverage_percent = form.code_coverage_percent.data
        app.last_code_quality_scan = form.last_code_quality_scan.data
        app.documentation_url = form.documentation_url.data
        app.architecture_diagram_url = form.architecture_diagram_url.data
        app.runbook_url = form.runbook_url.data
        app.user_manual_url = form.user_manual_url.data
        app.tags = form.tags.data
        app.notes = form.notes.data
        app.updated_at = datetime.utcnow()

        if app.archimate_element_id is not None:
            _sync_vendor_products(app.archimate_element_id, selected_vendor_ids)

        db.session.commit()

        flash(f'Application Component "{app.name}" updated successfully!', "success")
        return redirect(url_for("unified_applications.application_detail", id=app.id))

    # Query ArchiMate elements for inline management
    application_functions = []
    application_processes = []
    data_objects = []

    # Strategy elements
    capabilities = []
    resources = []
    value_streams = []
    courses_of_action = []

    # Business elements
    business_actors = []
    business_roles = []
    business_processes = []
    business_functions = []

    # Technology elements
    nodes = []
    system_software = []
    technology_services = []

    # Implementation elements
    work_packages = []
    deliverables = []
    implementation_events = []
    plateaus = []

    # Motivation elements - query from database models directly
    from ..models.models import Outcome, Principle
    from ..models.motivation import Driver, Goal

    stakeholders = []
    requirements = Requirement.query.filter_by(application_component_id=app.id).all()
    drivers = Driver.query.filter_by(application_component_id=app.id).all()
    goals = Goal.query.filter_by(application_component_id=app.id).all()

    outcomes = (
        Outcome.query.filter_by(architecture_id=app.architecture_id).all()
        if getattr(app, "architecture_id", None)  # model-safety-ok
        else []
    )
    principles = Principle.query.filter_by(application_component_id=app.id).all()

    if app.archimate_element_id:
        # Get all child elements via composition (Application Layer)
        child_relationships = ArchiMateRelationship.query.filter_by(
            source_id=app.archimate_element_id, type="composition"
        ).all()

        for rel in child_relationships:
            element = db.session.get(ArchiMateElement, rel.target_id)
            if element:
                if element.type == "ApplicationFunction":
                    application_functions.append(element)
                elif element.type == "ApplicationProcess":
                    application_processes.append(element)
                elif element.type == "DataObject":
                    data_objects.append(element)

        # Strategy elements (App realizes Strategy)
        strategy_relationships = ArchiMateRelationship.query.filter_by(
            source_id=app.archimate_element_id, type="realization"
        ).all()

        for rel in strategy_relationships:
            element = db.session.get(ArchiMateElement, rel.target_id)
            if element:
                if element.type == "Capability":
                    capabilities.append(element)
                elif element.type == "Resource":
                    resources.append(element)
                elif element.type == "ValueStream":
                    value_streams.append(element)
                elif element.type == "CourseOfAction":
                    courses_of_action.append(element)
                # Also generic realizations
                elif element.type == "Requirement":
                    pass  # Handled differently or add here

        # Business elements (App serves Business OR App realizes Business Process - stick to realization for simplicity if consistent)
        # Actually usually App SERVES Business. So Source=App, Target=Business, Type=Serving.
        # Let's check 'serving' relationships where Source=App
        serving_relationships = ArchiMateRelationship.query.filter_by(
            source_id=app.archimate_element_id, type="serving"
        ).all()

        # Also check Realization for Business Functions sometimes
        realization_relationships = ArchiMateRelationship.query.filter_by(
            source_id=app.archimate_element_id, type="realization"
        ).all()

        # Combine relationships to scan
        for rel in serving_relationships + realization_relationships:
            element = db.session.get(ArchiMateElement, rel.target_id)
            if element:
                if element.type == "BusinessActor":
                    business_actors.append(element)
                elif element.type == "BusinessRole":
                    business_roles.append(element)
                elif element.type == "BusinessProcess":
                    business_processes.append(element)
                elif element.type == "BusinessFunction":
                    business_functions.append(element)

        # Technology elements (Node serves App)
        # So Target=App, Source=Node, Type=Serving
        tech_relationships = ArchiMateRelationship.query.filter_by(
            target_id=app.archimate_element_id, type="serving"
        ).all()

        for rel in tech_relationships:
            element = db.session.get(ArchiMateElement, rel.source_id)
            if element:
                if element.type == "Node":
                    nodes.append(element)
                elif element.type == "SystemSoftware":
                    system_software.append(element)
                elif element.type == "TechnologyService":
                    technology_services.append(element)

        # Implementation elements (Work Package/Deliverable realizes Application)
        # Scan realization and associated
        impl_relationships = ArchiMateRelationship.query.filter(
            (
                (ArchiMateRelationship.target_id == app.archimate_element_id)
                & (ArchiMateRelationship.type == "realization")
            )
            | (
                (ArchiMateRelationship.target_id == app.archimate_element_id)
                & (ArchiMateRelationship.type == "aggregation")
            )
            | (  # Plateau
                (ArchiMateRelationship.target_id == app.archimate_element_id)
                & (ArchiMateRelationship.type == "association")
            )  # Event
        ).all()

        for rel in impl_relationships:
            element = db.session.get(ArchiMateElement, rel.source_id)
            if element:
                if element.type == "WorkPackage":
                    work_packages.append(element)
                elif element.type == "Deliverable":
                    deliverables.append(element)
                elif element.type == "ImplementationEvent":
                    implementation_events.append(element)
                elif element.type == "Plateau":
                    plateaus.append(element)

        # Motivation elements
        # Stakeholder influences App (target=App, source=Stakeholder, type=Influence)
        # Requirement realized by App (target=Requirement, source=App, type=Realization)

        # 1. Stakeholders
        influence_rels = ArchiMateRelationship.query.filter_by(
            target_id=app.archimate_element_id, type="influence"
        ).all()
        for rel in influence_rels:
            element = db.session.get(ArchiMateElement, rel.source_id)
            if element and element.type == "Stakeholder":
                stakeholders.append(element)

        # 2. Requirements (already likely scanned but explicit check)
        real_rels = ArchiMateRelationship.query.filter_by(
            source_id=app.archimate_element_id, type="realization"
        ).all()
        for rel in real_rels:
            element = db.session.get(ArchiMateElement, rel.target_id)
            if element and element.type == "Requirement":
                requirements.append(element)

    return render_template(
        "applications/edit.html",
        form=form,
        mode="edit",
        # The template reads `application`, not `app`.
        application=app,
        application_functions=application_functions,
        application_processes=application_processes,
        data_objects=data_objects,
        capabilities=capabilities,
        resources=resources,
        value_streams=value_streams,
        courses_of_action=courses_of_action,
        business_actors=business_actors,
        business_roles=business_roles,
        business_processes=business_processes,
        business_functions=business_functions,
        nodes=nodes,
        system_software=system_software,
        technology_services=technology_services,
        work_packages=work_packages,
        deliverables=deliverables,
        implementation_events=implementation_events,
        plateaus=plateaus,
        stakeholders=stakeholders,
        requirements=requirements,
        drivers=drivers,
        goals=goals,
        outcomes=outcomes,
        principles=principles,
    )


@application_mgmt.route("/applications/<int:id>/delete", methods=["POST"])
@login_required
def application_delete(id):
    """Delete Application Component"""
    # csrf-ok: global CSRFProtect active

    app = ApplicationComponent.query.get_or_404(id)
    app_name = app.name
    element_id = app.archimate_element_id

    try:
        from app.modules.applications.routes._helpers import (
            _delete_mirror_archimate_element,
        )

        db.session.delete(app)
        db.session.flush()
        # The mirror ArchiMate element goes with the application (finding C-02).
        mirror = _delete_mirror_archimate_element(element_id)
        db.session.commit()
        if mirror["errors"]:
            flash(
                f'Application Component "{app_name}" deleted, but its ArchiMate '
                "element could not be removed — it is still referenced elsewhere.",
                "warning",
            )
        else:
            flash(
                f'Application Component "{app_name}" deleted successfully!', "success"
            )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting application {id}: {e}")
        flash("Error deleting application. Please try again.", "error")
        return redirect(url_for("unified_applications.application_detail", id=id))

    return redirect(url_for("unified_applications.application_list"))


# ---------------------------------------------------------------------------
# Quick-fill endpoint — patch a single field to nudge completeness upward
# ---------------------------------------------------------------------------
_QUICK_FILL_ALLOWED = {
    "business_owner",
    "technical_owner",
    "annual_cost",
    "criticality",
    "description",
}


@application_mgmt.route("/api/applications/<int:app_id>/quick-fill", methods=["PATCH"])
@login_required
def application_quick_fill(app_id):
    """PATCH a single data-completeness field on an application.

    Body: {"field": "<field_name>", "value": "<value>"}
    Returns: {"success": true, "completeness_score": <int>}
    """
    app_obj = ApplicationComponent.query.get_or_404(app_id)

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "JSON body required"}), 400

    field = data.get("field", "")
    value = data.get("value", "")

    if field not in _QUICK_FILL_ALLOWED:
        return jsonify({
            "success": False,
            "error": f"Field '{field}' is not patchable. Allowed: {sorted(_QUICK_FILL_ALLOWED)}",
        }), 400

    if not isinstance(value, str) or not value.strip():
        return jsonify({"success": False, "error": "value must be a non-empty string"}), 400

    # Map quick-fill field names to actual model column names
    _FIELD_MAP = {
        "business_owner": "business_owner",
        "technical_owner": "technical_owner",
        "annual_cost": "license_cost_annual",
        "criticality": "business_criticality",
        "description": "description",
    }
    model_field = _FIELD_MAP[field]

    try:
        setattr(app_obj, model_field, value.strip())
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("quick_fill error app=%s field=%s: %s", app_id, field, e)
        return jsonify({"success": False, "error": "Database error"}), 500

    return jsonify({"success": True, "completeness_score": app_obj.completeness_score})

