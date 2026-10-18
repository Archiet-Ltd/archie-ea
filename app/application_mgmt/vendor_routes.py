"""
Vendor Management Routes

Vendor organization and product management for application portfolio.
"""

from app.models.vendor.vendor_organization import VendorOrganization
from flask import redirect, url_for, render_template, request, jsonify, current_app, flash
from flask_login import login_required, current_user
from . import application_mgmt
from app import db
from app.middleware.tenant_decorators import is_platform_admin
from datetime import datetime
import os
from app.utils.pagination import safe_int_arg


def _vendor_write_denied(vendor):
    """Fail-closed authorisation for a write to the shared, platform-wide
    vendor catalogue (refuter review of PR 428, vendor-catalogue findings).

    True (deny) unless the caller is a genuine platform admin or the
    vendor's own recorded creator. The pattern this replaces treated a
    missing ``created_by_id`` (every vendor seeded by a migration or seed
    script, never created through the UI) as "no creator recorded, so
    anyone may act" -- an ordinary, non-admin, authenticated user could edit
    or delete any seeded vendor. ``created_by_id == current_user.id`` only
    ever matches a real creator; it never matches on an absent value.
    """
    if is_platform_admin(current_user):
        return False
    return vendor.created_by_id != current_user.id


# Legacy redirect — /dashboard/vendors → canonical vendor catalogue
@application_mgmt.route('/vendors')
@application_mgmt.route('/vendors/')
@login_required
def vendors_redirect():
    """Redirect legacy /dashboard/vendors URL to canonical vendor catalogue."""
    return redirect(url_for('unified_applications.vendors'), code=301)


# Vendor Organization Routes
@application_mgmt.route('/applications/vendors')
@login_required
def vendors_dashboard():
    """Display all vendor organizations with their product portfolios."""
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.models.solution_models import solution_vendor_products
    from sqlalchemy.orm import joinedload

    # Get query parameters
    vendor_type_filter = request.args.get('vendor_type', 'all')
    search_query = request.args.get('search', '')
    contract_filter = request.args.get('contract_status', 'all')
    page = max(1, safe_int_arg('page', 1, minimum=1))
    per_page = min(max(1, safe_int_arg('per_page', 20, minimum=1, maximum=500)), 100)

    # Build query with eager loading
    query = VendorOrganization.query.options(
        joinedload(VendorOrganization.products)
    )

    if vendor_type_filter != 'all':
        query = query.filter_by(vendor_type=vendor_type_filter)

    if search_query:
        query = query.filter(VendorOrganization.name.ilike(f'%{search_query}%'))

    if contract_filter != 'all':
        query = query.filter_by(contract_status=contract_filter)

    # Global stats (unfiltered)
    total_vendors = VendorOrganization.query.count()
    active_vendors = VendorOrganization.query.filter_by(status='active').count()
    strategic_vendors = VendorOrganization.query.filter(
        VendorOrganization.status == 'active',
        VendorOrganization.enterprise_readiness_score >= 70
    ).count()

    stats = {
        'total': total_vendors,
        'active': active_vendors,
        'strategic': strategic_vendors
    }

    # Order and paginate
    query = query.order_by(
        VendorOrganization.enterprise_readiness_score.desc().nullslast(),
        VendorOrganization.name
    )
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    vendors = pagination.items

    # Build metrics for visible vendors only (not all vendors)
    vendor_metrics = {}
    for vendor in vendors:
        product_ids = [product.id for product in (vendor.products or []) if product.id]
        linked_app_ids = set()
        capability_ids = set()
        coverage_values = []

        for product in vendor.products or []:
            for app_element in getattr(product, "application_components", []) or []:
                if getattr(app_element, "id", None):
                    linked_app_ids.add(app_element.id)
            for mapping in getattr(product, "capability_mappings", []) or []:
                if mapping.business_capability_id:
                    capability_ids.add(mapping.business_capability_id)
                if mapping.coverage_percentage is not None:
                    coverage_values.append(mapping.coverage_percentage)

        linked_solution_count = 0
        if product_ids:
            linked_solution_count = len({
                row.solution_id
                for row in db.session.execute(  # tenant-filtered: scoped via parent FK (vendor_product_id)
                    solution_vendor_products.select().where(
                        solution_vendor_products.c.vendor_product_id.in_(product_ids)
                    )
                ).fetchall()
                if row.solution_id
            })

        vendor_metrics[vendor.id] = {
            "linked_products": len(product_ids),
            "linked_applications": len(linked_app_ids),
            "linked_solutions": linked_solution_count,
            "capability_count": len(capability_ids),
            "average_capability_coverage": round(sum(coverage_values) / len(coverage_values), 1)
            if coverage_values else None,
        }

    return render_template('application_mgmt/vendor_list.html',
                          vendors=vendors,
                          stats=stats,
                          vendor_metrics=vendor_metrics,
                          vendor_type_filter=vendor_type_filter,
                          search_query=search_query,
                          contract_filter=contract_filter,
                          pagination=pagination)


@application_mgmt.route('/applications/vendors/create', methods=['GET', 'POST'])
@login_required
def create_vendor():
    """Create a new vendor organization."""
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.vendors.forms import CreateVendorForm
    from flask_login import current_user

    form = CreateVendorForm()

    if form.validate_on_submit():
        try:
            # Create vendor organization
            vendor = VendorOrganization(
                name=form.name.data,
                description=form.description.data,
                vendor_type=form.vendor_type.data,
                website=form.website.data,
                headquarters_location=form.headquarters_location.data,
                enterprise_readiness_score=form.strategic_fit_score.data,
                status='active' if form.is_active.data else 'inactive',
                created_by_id=current_user.id
            )
            db.session.add(vendor)
            db.session.commit()

            flash(f'Vendor "{vendor.name}" created successfully.', 'success')
            return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor.id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Error creating vendor: {e}', exc_info=True)
            flash('Error creating vendor. Please try again.', 'error')

    # Convert FlaskForm to field metadata for standardized macro
    form_fields = [
        {
            'label': 'Vendor Name',
            'id': 'name',
            'name': 'name',
            'required': True,
            'type': 'text',
            'placeholder': '',
            'value': form.name.data or ''
        },
        {
            'label': 'Vendor Type',
            'id': 'vendor_type',
            'name': 'vendor_type',
            'required': True,
            'type': 'select',
            'options': [{'label': label, 'value': value} for value, label in form.vendor_type.choices],
            'value': form.vendor_type.data or ''
        },
        {
            'label': 'Description',
            'id': 'description',
            'name': 'description',
            'required': False,
            'type': 'textarea',
            'placeholder': 'Enter vendor description...',
            'rows': 4,
            'value': form.description.data or ''
        },
        {
            'label': 'Website',
            'id': 'website',
            'name': 'website',
            'required': False,
            'type': 'url',
            'placeholder': 'https://www.vendor.com',
            'value': form.website.data or ''
        },
        {
            'label': 'Headquarters Location',
            'id': 'headquarters_location',
            'name': 'headquarters_location',
            'required': False,
            'type': 'text',
            'placeholder': 'City, Country',
            'value': form.headquarters_location.data or ''
        },
        {
            'label': 'Enterprise Readiness Score (0-100)',
            'id': 'strategic_fit_score',
            'name': 'strategic_fit_score',
            'required': False,
            'type': 'number',
            'min': 0,
            'max': 100,
            'placeholder': '0-100',
            'help': 'Enter a score between 0-100 for vendor enterprise readiness (support, stability, roadmap).',
            'value': form.strategic_fit_score.data or ''
        },
        {
            'label': 'Is Active',
            'id': 'is_active',
            'name': 'is_active',
            'type': 'checkbox',
            'checked': form.is_active.data if form.is_active.data is not None else True
        }
    ]

    return render_template('vendors/create_simple.html',
                          form=form,
                          form_fields=form_fields,
                          form_action=url_for('application_mgmt.create_vendor'))


@application_mgmt.route('/applications/vendors/<int:vendor_id>')
@login_required
def vendor_detail(vendor_id):
    """Redirect to canonical vendor detail page (unified_applications blueprint)."""
    return redirect(url_for('unified_applications.vendor_detail', vendor_id=vendor_id), code=301)


@application_mgmt.route('/api/vendors/<int:vendor_id>/analyze-document', methods=['POST'])
@login_required
def analyze_document_for_vendor(vendor_id):
    """
    Analyze uploaded document and extract ArchiMate elements for vendor.
    
    Accepts:
    - file: File to upload and analyze (PDF, DOCX, PPTX, images)
    - provider: LLM provider ('claude', 'openai', 'gemini')
    """
    from ..services.archimate.document_analysis_service import DocumentAnalysisService
    from ..services.archimate.document_upload_service import DocumentUploadService
    from werkzeug.utils import secure_filename
    
    vendor = VendorOrganization.query.get_or_404(vendor_id)
    if _vendor_write_denied(vendor):
        return jsonify({'error': 'Access denied'}), 403

    try:
        analysis_service = DocumentAnalysisService()
        provider = request.form.get('provider', 'claude')
        
        # Handle file upload
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Save uploaded file
        upload_folder = os.path.join(current_app.root_path, 'uploads', 'documents', 'vendor_analysis')
        os.makedirs(upload_folder, exist_ok=True)
        
        safe_filename = secure_filename(file.filename)
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{vendor_id}_{timestamp}_{safe_filename}"
        file_path = os.path.join(upload_folder, unique_filename)
        file.save(file_path)
        
        # Determine file type
        upload_service = DocumentUploadService(upload_folder)
        file_type = upload_service.get_file_type(file.filename) or 'document'
        
        # Run async analysis using shared event loop utility
        from app.services.core.async_utils import get_or_create_event_loop
        loop = get_or_create_event_loop()
        analysis_results = loop.run_until_complete(
            analysis_service.analyze_document_for_vendor(
                file_path=file_path,
                file_type=file_type,
                vendor_id=vendor_id,
                user_id=current_user.id if current_user.is_authenticated else None,
                provider=provider
            )
        )
        
        # Save analysis to database for history
        from ..models.document_analysis import DocumentAnalysis
        import hashlib
        import json
        
        # Calculate file hash for deduplication
        file_hash = None
        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
        
        # Get LLM interaction ID if available
        llm_interaction_id = None
        if analysis_results.get('llm_interactions') and len(analysis_results['llm_interactions']) > 0:
            llm_interaction_id = analysis_results['llm_interactions'][0].id if hasattr(analysis_results['llm_interactions'][0], 'id') else None
        
        analysis_record = DocumentAnalysis(
            entity_type='vendor',
            entity_id=vendor_id,
            file_name=file.filename,
            file_path=file_path,
            file_size=os.path.getsize(file_path) if os.path.exists(file_path) else None,
            file_hash=file_hash,
            mime_type=file.content_type,
            provider=provider,
            analysis_results=json.dumps(analysis_results),
            vendor_data=json.dumps(analysis_results.get('vendor_data', {})),
            archimate_elements=json.dumps(analysis_results.get('archimate_elements', [])),
            relationships=json.dumps(analysis_results.get('relationships', [])),
            validation_results=json.dumps(analysis_results.get('validation_results', {})),
            confidence=analysis_results.get('confidence', 'medium'),
            elements_count=len(analysis_results.get('archimate_elements', [])),
            relationships_count=len(analysis_results.get('relationships', [])),
            validation_errors_count=len(analysis_results.get('validation_results', {}).get('errors', [])),
            status='completed',
            analyzed_by_id=current_user.id if current_user.is_authenticated else None,
            llm_interaction_id=llm_interaction_id
        )
        db.session.add(analysis_record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'analysis_id': analysis_record.id,
            'analysis': {
                'vendor_data': analysis_results.get('vendor_data', {}),
                'archimate_elements': analysis_results.get('archimate_elements', []),
                'relationships': analysis_results.get('relationships', []),
                'validation_results': analysis_results.get('validation_results', {}),
                'confidence': analysis_results.get('confidence', 'medium'),
                'metadata': analysis_results.get('metadata', {})
            }
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error analyzing vendor document: {e}", exc_info=True)
        return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@application_mgmt.route('/api/vendors/<int:vendor_id>/apply-analysis', methods=['POST'])
@login_required
def apply_analysis_to_vendor(vendor_id):
    """
    Apply analysis results to vendor organization.
    
    Accepts JSON with analysis results from analyze_document_for_vendor.
    """
    from ..services.archimate.document_analysis_service import DocumentAnalysisService
    
    vendor = VendorOrganization.query.get_or_404(vendor_id)
    if _vendor_write_denied(vendor):
        return jsonify({'error': 'Access denied'}), 403

    try:
        data = request.get_json()
        if not data or 'analysis' not in data:
            return jsonify({'error': 'Analysis data required'}), 400
        
        analysis_service = DocumentAnalysisService()
        
        # Reconstruct analysis results format
        analysis_results = {
            'vendor_data': data['analysis'].get('vendor_data', {}),
            'archimate_elements': data['analysis'].get('archimate_elements', []),
            'relationships': data['analysis'].get('relationships', [])
        }
        
        # Apply analysis to vendor
        updated_vendor, created_elements = analysis_service.apply_analysis_to_vendor(
            vendor_id=vendor_id,
            analysis_results=analysis_results,
            user_id=current_user.id if current_user.is_authenticated else None
        )
        
        # Mark analysis as applied if analysis_id provided
        analysis_id = data.get('analysis_id')
        if analysis_id:
            from ..models.document_analysis import DocumentAnalysis
            from datetime import datetime
            analysis_record = DocumentAnalysis.query.get(analysis_id)
            if analysis_record:
                analysis_record.applied = True
                analysis_record.applied_at = datetime.utcnow()
                db.session.commit()
        
        return jsonify({
            'success': True,
            'vendor': {
                'id': updated_vendor.id,
                'name': updated_vendor.name,
                'display_name': updated_vendor.display_name
            },
            'archimate_elements_created': len(created_elements),
            'elements': [
                {
                    'id': elem.id,
                    'name': elem.name,
                    'type': elem.type,
                    'layer': elem.layer
                }
                for elem in created_elements
            ]
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error applying vendor analysis: {e}", exc_info=True)
        return jsonify({'error': 'An internal error occurred. Please try again.'}), 500


@application_mgmt.route('/applications/vendors/<int:vendor_id>/applications')
@login_required
def vendor_applications_portfolio(vendor_id):
    """Display all applications deployed from this vendor's products - dedicated portfolio view."""
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
    from app.models.vendor.vendor_organization import application_vendor_products
    from app.models.models import ArchiMateElement
    from app.models.application_layer import ApplicationComponent

    # Get vendor
    vendor = VendorOrganization.query.get_or_404(vendor_id)

    # Get deployed applications with vendor product details
    # Query ApplicationComponents linked to vendor products through application_vendor_products
    applications_query = db.session.query(
        ApplicationComponent,
        VendorProduct
    ).join(
        ArchiMateElement,
        ArchiMateElement.id == ApplicationComponent.archimate_element_id
    ).join(
        application_vendor_products,
        application_vendor_products.c.archimate_element_id == ArchiMateElement.id
    ).join(
        VendorProduct,
        VendorProduct.id == application_vendor_products.c.vendor_product_id
    ).filter(
        VendorProduct.vendor_organization_id == vendor_id
    ).all()
    # NB: no SQL DISTINCT — ApplicationComponent/VendorProduct carry JSON columns
    # and Postgres cannot DISTINCT on json ("no equality operator for type json").
    # Dedupe by (application id, product id) in Python instead.

    # Transform query results into application objects with product info
    applications_with_products = []
    _seen_pairs = set()
    for app, product in applications_query:
        pair = (app.id, product.id)
        if pair in _seen_pairs:
            continue
        _seen_pairs.add(pair)
        app_dict = {
            'application': app,
            'vendor_product': product,
            'vendor': vendor
        }
        applications_with_products.append(app_dict)

    # Calculate stats
    stats = {
        'total_applications': len(applications_with_products),
        'products_with_deployments': len(set(item['vendor_product'].id for item in applications_with_products)),
        'total_products': len(vendor.products)
    }

    return render_template('vendors/vendor_applications_portfolio.html',
                         vendor=vendor,
                         applications=applications_with_products,
                         stats=stats)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_vendor(vendor_id):
    """Edit vendor organization."""
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.vendors.forms import CreateVendorForm
    from datetime import datetime

    vendor = VendorOrganization.query.get_or_404(vendor_id)
    if _vendor_write_denied(vendor):
        flash('Access denied.', 'error')
        return redirect(url_for('application_mgmt.vendors_dashboard'))
    form = CreateVendorForm(obj=vendor)

    if form.validate_on_submit():
        try:
            # Update vendor
            vendor.name = form.name.data
            vendor.description = form.description.data
            vendor.vendor_type = form.vendor_type.data
            vendor.website = form.website.data
            vendor.headquarters_location = form.headquarters_location.data
            vendor.enterprise_readiness_score = form.strategic_fit_score.data
            vendor.status = 'active' if form.is_active.data else 'inactive'
            vendor.updated_at = datetime.utcnow()

            db.session.commit()
            flash(f'Vendor "{vendor.name}" updated successfully.', 'success')
            return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor.id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Error updating vendor: {e}', exc_info=True)
            flash('Error updating vendor. Please try again.', 'error')

    return render_template('vendors/edit.html', form=form, vendor=vendor)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/delete', methods=['POST'])
@login_required
def delete_vendor(vendor_id):
    """Delete vendor organization."""
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization.query.get_or_404(vendor_id)
    if _vendor_write_denied(vendor):
        flash('Access denied.', 'error')
        return redirect(url_for('application_mgmt.vendors_dashboard'))
    vendor_name = vendor.name

    try:
        db.session.delete(vendor)
        db.session.commit()
        flash(f'Vendor "{vendor_name}" deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error deleting vendor: {e}', exc_info=True)
        flash('Error deleting vendor. Please try again.', 'error')

    return redirect(url_for('application_mgmt.vendors_dashboard'))


# Vendor Templates Routes
@application_mgmt.route('/applications/vendor-templates')
@login_required
def vendor_templates():
    """Redirect to architecture vendor templates (temporary)"""
    return redirect(url_for('architecture.vendor_templates'), code=302)


@application_mgmt.route('/applications/vendor-templates/seed/<action>', methods=['POST'])
@login_required
def seed_vendor_templates(action):
    """Redirect to architecture seed templates (temporary)"""
    # Target endpoint architecture.seed_vendor_templates does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendor-templates/<int:template_id>/delete', methods=['POST'])
@login_required
def delete_vendor_template(template_id):
    """Redirect to architecture delete template (temporary)"""
    # Target endpoint architecture.delete_vendor_template does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendor-templates/<int:template_id>')
@login_required
def vendor_template_detail(template_id):
    """Redirect to architecture template detail (temporary)"""
    # Target endpoint architecture.vendor_template_detail does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendor-templates/<int:template_id>/instantiate', methods=['POST'])
@login_required
def instantiate_vendor_template(template_id):
    """Redirect to architecture instantiate template (temporary)"""
    # Target endpoint architecture.instantiate_vendor_template does not exist — return 404
    from flask import abort
    abort(404)


# Vendor Products Routes
@application_mgmt.route('/applications/vendors/<int:vendor_id>/products')
@login_required
def vendor_products(vendor_id):
    """Redirect to architecture vendor products (temporary)"""
    return redirect(url_for('architecture.vendor_products', vendor_id=vendor_id), code=302)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/products/create', methods=['GET', 'POST'])
@login_required
def create_product(vendor_id):
    """Redirect to architecture create product (temporary)"""
    # Target endpoint architecture.create_product does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/products/<int:product_id>')
@login_required
def product_detail(vendor_id, product_id):
    """Redirect to architecture product detail (temporary)"""
    # Target endpoint architecture.product_detail does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(vendor_id, product_id):
    """Redirect to architecture edit product (temporary)"""
    # Target endpoint architecture.edit_product does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/products/<int:product_id>/delete', methods=['POST'])
@login_required
def delete_product(vendor_id, product_id):
    """Redirect to architecture delete product (temporary)"""
    # Target endpoint architecture.delete_product does not exist — return 404
    from flask import abort
    abort(404)


@application_mgmt.route('/applications/vendors/<int:vendor_id>/products/<int:product_id>/capabilities', methods=['GET', 'POST'])
@login_required
def manage_product_capabilities(vendor_id, product_id):
    """Redirect to architecture manage capabilities (temporary)"""
    # Target endpoint architecture.manage_product_capabilities does not exist — return 404
    from flask import abort
    abort(404)


# Vendor Onboarding Routes
@application_mgmt.route('/applications/vendors/<int:vendor_id>/activate', methods=['POST'])
@login_required
def activate_vendor(vendor_id):
    """Activate a vendor from catalog to contracted status."""
    from app.services.vendor_onboarding_service import VendorOnboardingService
    from app.models.vendor.vendor_organization import VendorOrganization
    from datetime import datetime
    from flask import jsonify

    vendor = VendorOrganization.query.get_or_404(vendor_id)
    if _vendor_write_denied(vendor):
        flash('Access denied.', 'error')
        return redirect(url_for('application_mgmt.vendors_dashboard'))

    try:
        # Get form data
        contract_start = request.form.get('contract_start_date')
        contract_end = request.form.get('contract_end_date')
        contract_value = request.form.get('contract_value_annual')
        
        # Convert dates if provided
        start_date = datetime.strptime(contract_start, '%Y-%m-%d') if contract_start else None
        end_date = datetime.strptime(contract_end, '%Y-%m-%d') if contract_end else None
        value = float(contract_value) if contract_value else None
        
        # Activate vendor
        vendor = VendorOnboardingService.activate_vendor(
            vendor_id,
            contract_start_date=start_date,
            contract_end_date=end_date,
            contract_value=value
        )
        
        flash(f'{vendor.name} activated successfully! Status: {vendor.contract_status}', 'success')
        
        if request.headers.get('Accept') == 'application/json':
            return jsonify({
                'success': True,
                'vendor_id': vendor.id,
                'contract_status': vendor.contract_status
            })
        
        return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor_id))
        
    except Exception as e:
        current_app.logger.error(f'Error activating vendor: {e}', exc_info=True)
        flash('Error activating vendor. Please try again.', 'error')
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 400
        return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor_id))


@application_mgmt.route('/applications/vendors/<int:vendor_id>/products/<int:product_id>/deploy', methods=['POST'])
@login_required
def deploy_vendor_product(vendor_id, product_id):
    """Deploy a vendor product as an application instance."""
    from app.services.vendor_onboarding_service import VendorOnboardingService
    from app.models.vendor.vendor_organization import VendorOrganization
    from flask import jsonify

    vendor = VendorOrganization.query.get_or_404(vendor_id)
    if _vendor_write_denied(vendor):
        flash('Access denied.', 'error')
        return redirect(url_for('application_mgmt.vendors_dashboard'))

    try:
        # Get form data
        app_name = request.form.get('application_name')
        description = request.form.get('description')
        deployment_type = request.form.get('deployment_type', 'primary_system')
        criticality = request.form.get('criticality', 'business_critical')
        hosting_model = request.form.get('hosting_model', 'cloud')
        business_owner = request.form.get('business_owner')
        
        if not app_name:
            raise ValueError("Application name is required")
        
        # Deploy product
        application = VendorOnboardingService.deploy_vendor_product(
            product_id,
            application_name=app_name,
            description=description,
            deployment_type=deployment_type,
            criticality=criticality,
            hosting_model=hosting_model,
            business_owner=business_owner
        )
        
        flash(f'Application "{application.name}" deployed successfully!', 'success')
        
        if request.headers.get('Accept') == 'application/json':
            return jsonify({
                'success': True,
                'application_id': application.id,
                'application_name': application.name
            })
        
        return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor_id))
        
    except ValueError as e:
        current_app.logger.error(f'Deployment validation error: {e}', exc_info=True)
        flash('Deployment error. Please check your input and try again.', 'error')
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'success': False, 'error': 'Invalid input. Please check your data and try again.'}), 400
        return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor_id))
    except Exception as e:
        current_app.logger.error(f'Error deploying product: {e}', exc_info=True)
        flash('Error deploying product. Please try again.', 'error')
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500
        return redirect(url_for('application_mgmt.vendor_detail', vendor_id=vendor_id))


@application_mgmt.route('/applications/<string:app_id>/link-vendor', methods=['POST'])
@login_required
def link_application_to_vendor(app_id):
    """Link an existing application to a vendor product."""
    from app.services.vendor_onboarding_service import VendorOnboardingService
    from flask import jsonify
    
    try:
        # Get form data
        product_id = request.form.get('vendor_product_id')
        deployment_type = request.form.get('deployment_type', 'primary_system')
        criticality = request.form.get('criticality', 'business_critical')
        hosting_model = request.form.get('hosting_model', 'cloud')
        
        if not product_id:
            raise ValueError("Vendor product ID is required")
        
        # Link application
        application, product = VendorOnboardingService.link_existing_application_to_vendor(
            app_id,
            int(product_id),
            deployment_type=deployment_type,
            criticality=criticality,
            hosting_model=hosting_model
        )
        
        flash(f'Application "{application.name}" linked to {product.name} successfully!', 'success')
        
        if request.headers.get('Accept') == 'application/json':
            return jsonify({
                'success': True,
                'application_id': application.id,
                'product_id': product.id
            })
        
        return redirect(request.referrer or url_for('application_mgmt.vendors_dashboard'))
        
    except ValueError as e:
        current_app.logger.error(f'Linking validation error: {e}', exc_info=True)
        flash('Linking error. Please check your input and try again.', 'error')
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'success': False, 'error': 'Invalid input. Please check your data and try again.'}), 400
        return redirect(request.referrer or url_for('application_mgmt.vendors_dashboard'))
    except Exception as e:
        current_app.logger.error(f'Error linking application: {e}', exc_info=True)
        flash('Error linking application. Please try again.', 'error')
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'success': False, 'error': 'An internal error occurred. Please try again.'}), 500
        return redirect(request.referrer or url_for('application_mgmt.vendors_dashboard'))


# Vendor Analysis Routes
@application_mgmt.route('/applications/vendors/api/analyze', methods=['POST'])
@login_required
def vendor_analyze_api():
    """API endpoint for vendor analysis."""
    try:
        from app.services.vendor_analysis_service import VendorAnalysisService
        
        data = request.get_json() or {}
        
        # Initialize service
        service = VendorAnalysisService()
        service.init_app(current_app)
        
        # Extract filters
        vendor_ids = data.get('vendor_ids')
        capability_ids = data.get('capability_ids')
        product_families = data.get('product_families')
        deployment_models = data.get('deployment_models')
        contract_statuses = data.get('contract_statuses')
        min_readiness_score = data.get('min_readiness_score')
        max_cost = data.get('max_cost')
        min_cost = data.get('min_cost')
        technology_stack = data.get('technology_stack')
        consider_existing_apps = data.get('consider_existing_apps', True)
        consider_existing_vendors = data.get('consider_existing_vendors', True)
        
        # Convert cost to Decimal if provided
        from decimal import Decimal
        if max_cost is not None:
            max_cost = Decimal(str(max_cost))
        if min_cost is not None:
            min_cost = Decimal(str(min_cost))
        
        # Run analysis
        analysis = service.analyze_vendors(
            vendor_ids=vendor_ids,
            capability_ids=capability_ids,
            product_families=product_families,
            deployment_models=deployment_models,
            contract_statuses=contract_statuses,
            min_readiness_score=min_readiness_score,
            max_cost=max_cost,
            min_cost=min_cost,
            technology_stack=technology_stack,
            consider_existing_apps=consider_existing_apps,
            consider_existing_vendors=consider_existing_vendors
        )
        
        return jsonify({
            'success': True,
            'analysis': analysis
        })
    
    except Exception as e:
        current_app.logger.error(f"Vendor analysis error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500


@application_mgmt.route('/applications/vendors/api/capabilities', methods=['GET'])
@login_required
def vendor_analysis_capabilities_api():
    """Get list of capabilities for filter selection."""
    try:
        from app.models.business_capabilities import BusinessCapability
        
        # Get all capabilities
        capabilities = BusinessCapability.query.order_by(
            BusinessCapability.level,
            BusinessCapability.name
        ).all()
        
        # Deduplicate by name - keep the most recent/complete capability for each name
        # Strategy: Use a dict keyed by name, keeping the capability with:
        # 1. Highest ID (most recent) if IDs are sequential
        # 2. Most complete data (has code, description, etc.)
        unique_capabilities = {}
        for cap in capabilities:
            name = cap.name
            if name not in unique_capabilities:
                # First occurrence - keep it
                unique_capabilities[name] = cap
            else:
                # Duplicate - decide which to keep
                existing = unique_capabilities[name]
                # Prefer capability with more complete data
                existing_score = sum([
                    1 if existing.code else 0,
                    1 if existing.description else 0,
                    1 if existing.category else 0
                ])
                new_score = sum([
                    1 if cap.code else 0,
                    1 if cap.description else 0,
                    1 if cap.category else 0
                ])
                
                # If new one has more complete data, or same completeness but higher ID, replace
                if new_score > existing_score or (new_score == existing_score and cap.id > existing.id):
                    unique_capabilities[name] = cap
        
        # Convert to list and sort
        deduplicated = list(unique_capabilities.values())
        deduplicated.sort(key=lambda c: (c.level or 1, c.name))
        
        return jsonify({
            'success': True,
            'capabilities': [
                {
                    'id': cap.id,
                    'name': cap.name,
                    'code': cap.code,
                    'level': cap.level,
                    'category': cap.category,
                    'description': cap.description
                }
                for cap in deduplicated
            ]
        })
    
    except Exception as e:
        current_app.logger.error(f"Capabilities API error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500


@application_mgmt.route('/applications/vendors/api/vendors', methods=['GET'])
@login_required
def vendor_analysis_vendors_api():
    """Get list of vendors for filter selection."""
    try:
        from app.models.vendor.vendor_organization import VendorOrganization
        
        vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()
        
        return jsonify({
            'success': True,
            'vendors': [
                {
                    'id': vendor.id,
                    'name': vendor.name,
                    'display_name': vendor.display_name,
                    'vendor_type': vendor.vendor_type,
                    'enterprise_readiness_score': vendor.enterprise_readiness_score,
                    'strategic_tier': vendor.strategic_tier,
                    'contract_status': vendor.contract_status or 'catalog'
                }
                for vendor in vendors
            ]
        })
    
    except Exception as e:
        current_app.logger.error(f"Vendors API error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'An internal error occurred. Please try again.'
        }), 500
