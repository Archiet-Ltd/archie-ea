"""
API and data routes for Application Management.
"""
# mass-deletion-ok — BE-179 removes 16 manual CSRF blocks replaced by global CSRFProtect

import hashlib
import json
import os
from datetime import datetime

from flask import current_app, flash, g, jsonify, request  # dead-code-ok
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.miscellaneous import ApplicationDocument
from app.utils.deprecation import deprecated_route
from . import application_mgmt
from .routes import _redirect_to_detail


@application_mgmt.route(
    "/applications/<int:application_id>/upload-document", methods=["POST"]
)
@login_required
def upload_document_file(application_id):
    """Upload a document file for an application"""
    app = ApplicationComponent.query.get_or_404(application_id)

    # csrf-ok: global CSRFProtect active

    try:
        # Handle file upload (template uses name="file")
        if "file" not in request.files:
            flash("No file provided", "warning")
            return _redirect_to_detail(app.id, tab="dependencies")

        file = request.files["file"]
        if file.filename == "":
            flash("No file selected", "warning")
            return _redirect_to_detail(app.id, tab="dependencies")

        # Get additional form fields
        document_title = request.form.get("title", file.filename)
        document_description = request.form.get("description", "")

        file_extension = (
            os.path.splitext(file.filename)[1][1:].upper()
            if file.filename
            else "UNKNOWN"
        )

        uploaded_by = (
            current_user.full_name() if current_user.is_authenticated else "Anonymous"
        )

        # Create document record in database (without file_path first to get ID)
        document = ApplicationDocument(
            application_component_id=app.id,
            title=document_title,
            description=document_description,
            file_name=file.filename,
            file_extension=file_extension,
            file_path=None,
            file_size=None,
            uploaded_by=uploaded_by,
        )

        db.session.add(document)
        db.session.flush()  # Get the document ID

        # Save file to disk
        upload_folder = os.path.join(current_app.root_path, "uploads", "documents")
        os.makedirs(upload_folder, exist_ok=True)

        # Use document ID in filename to ensure uniqueness
        safe_filename = secure_filename(file.filename)
        unique_filename = f"{document.id}_{safe_filename}"
        file_path = os.path.join(upload_folder, unique_filename)

        file.save(file_path)

        # Update document with file info
        document.file_path = file_path
        document.file_size = os.path.getsize(file_path)

        db.session.commit()

        flash(f'Document "{document_title}" uploaded successfully!', "success")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error uploading document for app {application_id}: {exc}"
        )
        flash(f"Error uploading document: {str(exc)}", "danger")

    return _redirect_to_detail(app.id, tab="dependencies")


@application_mgmt.route(
    "/api/applications/<int:application_id>/analyze-document", methods=["POST"]
)
@login_required
def analyze_document_for_application(application_id):
    """
    Analyze uploaded document and extract ArchiMate elements for application.

    Accepts:
    - document_id: ID of uploaded document to analyze
    - OR file: New file to upload and analyze
    - provider: LLM provider ('claude', 'openai', 'gemini')
    """
    from ..services.archimate.document_analysis_service import DocumentAnalysisService
    from ..services.archimate.document_upload_service import DocumentUploadService

    ApplicationComponent.query.get_or_404(application_id)

    try:
        analysis_service = DocumentAnalysisService()
        provider = request.form.get("provider", "claude")

        # Check if analyzing existing document or uploading new one
        document_id = request.form.get("document_id")
        file = None
        file_name = None
        file_content_type = None

        if document_id:
            # Analyze existing document
            query = ApplicationDocument.query.filter_by(id=document_id)
            if not getattr(current_user, "is_platform_admin", False):
                query = query.filter_by(organization_id=g.current_org_id)
            document = query.first()

            if not document:
                return jsonify({"error": "Document not found."}), 404

            # Tenant isolation: verify the document belongs to the caller's
            # organisation. The query above skips the organisation filter for
            # platform administrators, who can reach any document;
            # verify_file_access provides a second line of defence, including
            # unrestricted access for platform admins.
            from app.middleware.tenant_files import verify_file_access

            if not verify_file_access(document.organization_id):
                return jsonify({"error": "Access denied."}), 403

            if document.application_component_id != application_id:
                return jsonify(
                    {"error": "Document does not belong to this application"}
                ), 400

            file_path = document.file_path
            file_name = document.file_name
            file_content_type = (
                f"application/{document.file_extension.lower()}"
                if document.file_extension
                else "application/octet-stream"
            )

            if not file_path or not os.path.exists(file_path):
                return jsonify({"error": "Document file not found"}), 404

            # Determine file type
            file_ext = os.path.splitext(file_path)[1].lower()
            if file_ext in [".png", ".jpg", ".jpeg", ".gif", ".svg"]:
                file_type = "image"
            elif file_ext in [".pdf", ".doc", ".docx", ".ppt", ".pptx"]:
                file_type = "document"
            elif file_ext in [".txt", ".md", ".html"]:
                file_type = "text"
            else:
                file_type = "document"
        else:
            # Upload new file
            if "file" not in request.files:
                return jsonify({"error": "No file provided"}), 400

            file = request.files["file"]
            if file.filename == "":
                return jsonify({"error": "No file selected"}), 400

            file_name = file.filename
            file_content_type = file.content_type

            # Save uploaded file
            upload_folder = os.path.join(
                current_app.root_path, "uploads", "documents", "analysis"
            )
            os.makedirs(upload_folder, exist_ok=True)

            safe_filename = secure_filename(file.filename)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{application_id}_{timestamp}_{safe_filename}"
            file_path = os.path.join(upload_folder, unique_filename)
            file.save(file_path)

            # Determine file type
            upload_service = DocumentUploadService(upload_folder)
            file_type = upload_service.get_file_type(file.filename) or "document"

        # Run async analysis using shared event loop utility
        from app.services.core.async_utils import get_or_create_event_loop

        loop = get_or_create_event_loop()
        analysis_results = loop.run_until_complete(
            analysis_service.analyze_document_for_application(
                file_path=file_path,
                file_type=file_type,
                application_id=application_id,
                user_id=current_user.id if current_user.is_authenticated else None,
                provider=provider,
            )
        )

        # Save analysis to database for history
        from ..models.document_analysis import DocumentAnalysis

        # Calculate file hash for deduplication
        file_hash = None
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()

        # Get LLM interaction ID if available
        llm_interaction_id = None
        llm_interactions = analysis_results.get("llm_interactions", [])
        if llm_interactions and len(llm_interactions) > 0:
            first_interaction = llm_interactions[0]
            if hasattr(first_interaction, "id"):
                llm_interaction_id = first_interaction.id
            elif isinstance(first_interaction, dict) and "id" in first_interaction:
                llm_interaction_id = first_interaction["id"]

        analysis_record = DocumentAnalysis(
            entity_type="application",
            entity_id=application_id,
            file_name=file_name,
            file_path=file_path,
            file_size=os.path.getsize(file_path) if os.path.exists(file_path) else None,
            file_hash=file_hash,
            mime_type=file_content_type or "application/octet-stream",
            provider=provider,
            analysis_results=json.dumps(analysis_results),
            application_data=json.dumps(analysis_results.get("application_data", {})),
            archimate_elements=json.dumps(
                analysis_results.get("archimate_elements", [])
            ),
            relationships=json.dumps(analysis_results.get("relationships", [])),
            validation_results=json.dumps(
                analysis_results.get("validation_results", {})
            ),
            confidence=analysis_results.get("confidence", "medium"),
            elements_count=len(analysis_results.get("archimate_elements", [])),
            relationships_count=len(analysis_results.get("relationships", [])),
            validation_errors_count=len(
                analysis_results.get("validation_results", {}).get("errors", [])
            ),
            status="completed",
            analyzed_by_id=current_user.id if current_user.is_authenticated else None,
            llm_interaction_id=llm_interaction_id,
        )
        db.session.add(analysis_record)
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "analysis_id": analysis_record.id,
                    "analysis": {
                        "application_data": analysis_results.get(
                            "application_data", {}
                        ),
                        "archimate_elements": analysis_results.get(
                            "archimate_elements", []
                        ),
                        "relationships": analysis_results.get("relationships", []),
                        "validation_results": analysis_results.get(
                            "validation_results", {}
                        ),
                        "confidence": analysis_results.get("confidence", "medium"),
                        "metadata": analysis_results.get("metadata", {}),
                    },
                }
            ),
            200,
        )

    except Exception as e:
        current_app.logger.error(f"Error analyzing document: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<int:application_id>/apply-analysis", methods=["POST"]
)
@login_required
def apply_analysis_to_application(application_id):
    """
    Apply analysis results to application component.

    Accepts JSON with analysis results from analyze_document_for_application.
    """
    from ..services.archimate.document_analysis_service import DocumentAnalysisService

    ApplicationComponent.query.get_or_404(application_id)

    try:
        data = request.get_json()
        if not data or "analysis" not in data:
            return jsonify({"error": "Analysis data required"}), 400

        analysis_service = DocumentAnalysisService()

        # Reconstruct analysis results format
        analysis_results = {
            "application_data": data["analysis"].get("application_data", {}),
            "archimate_elements": data["analysis"].get("archimate_elements", []),
            "relationships": data["analysis"].get("relationships", []),
        }

        # Apply analysis to application
        updated_app, created_elements = analysis_service.apply_analysis_to_application(
            application_id=application_id,
            analysis_results=analysis_results,
            user_id=current_user.id if current_user.is_authenticated else None,
        )

        # Mark analysis as applied if analysis_id provided
        analysis_id = data.get("analysis_id")
        if analysis_id:
            from ..models.document_analysis import DocumentAnalysis

            analysis_record = DocumentAnalysis.query.get(analysis_id)
            if analysis_record:
                analysis_record.applied = True
                analysis_record.applied_at = datetime.utcnow()
                db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "application": {
                        "id": updated_app.id,
                        "name": updated_app.name,
                        "description": updated_app.description,
                    },
                    "archimate_elements_created": len(created_elements),
                    "elements": [
                        {
                            "id": elem.id,
                            "name": elem.name,
                            "type": elem.type,
                            "layer": elem.layer,
                        }
                        for elem in created_elements
                    ],
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error applying analysis: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@application_mgmt.route(
    "/api/applications/<int:application_id>/analysis-history", methods=["GET"]
)
@login_required
def get_analysis_history(application_id):
    """Get analysis history for an application."""
    from ..models.document_analysis import DocumentAnalysis

    ApplicationComponent.query.get_or_404(application_id)

    analyses = (
        DocumentAnalysis.query.filter_by(
            entity_type="application", entity_id=application_id
        )
        .order_by(DocumentAnalysis.created_at.desc())
        .limit(10)
        .all()
    )

    return (
        jsonify(
            {"success": True, "analyses": [analysis.to_dict() for analysis in analyses]}
        ),
        200,
    )


@application_mgmt.route("/api/test", methods=["GET"])
@login_required
def test_api():
    """Simple test endpoint"""
    return jsonify({"success": True, "message": "API working"})


@application_mgmt.route("/api/applications/table-data", methods=["GET"])
@login_required
@deprecated_route(
    canonical_endpoint="application_api.api_applications_table",
    deprecation_date="2026-02-10",
    migration_guide="Use /api/applications/table-data from application_api blueprint instead",
)
def get_applications_table_data():
    """API endpoint for server-side paginated table data"""
    try:
        from app.utils.api_response import success_response
        from sqlalchemy import inspect

        # Get query parameters with pagination bounds checking
        from app.utils.pagination import get_pagination_params

        page, per_page = get_pagination_params(default_per_page=50, max_per_page=100)
        search = request.args.get("search", "")
        component_type = request.args.get("type", "")
        status = request.args.get("status", "")

        # Schema-drift-safe: inspect actual DB columns
        table_columns = {
            col["name"] for col in inspect(db.engine).get_columns(ApplicationComponent.__tablename__)
        }

        # Only select columns that exist in DB
        safe_columns = ["id", "name", "description", "component_type", "deployment_status", "business_owner", "business_criticality"]
        selected_cols = [getattr(ApplicationComponent, col) for col in safe_columns if col in table_columns]

        # Build query using only safe columns
        query = db.session.query(*selected_cols)

        # Apply filters
        if search and "name" in table_columns and "description" in table_columns:
            query = query.filter(
                db.or_(
                    ApplicationComponent.name.ilike(f"%{search}%"),
                    ApplicationComponent.description.ilike(f"%{search}%"),
                )
            )

        if component_type and "component_type" in table_columns:
            query = query.filter(ApplicationComponent.component_type == component_type)

        if status and "deployment_status" in table_columns:
            query = query.filter(ApplicationComponent.deployment_status == status)

        # Paginate manually to avoid model-wide selects
        total = query.count()
        items = query.order_by(ApplicationComponent.name).offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 1

        # Prepare minimal table data using only safe columns
        table_data = []
        status_map = {
            "production": "Done",
            "done": "Done",
            "development": "In Process",
            "testing": "In Process",
            "staging": "In Process",
            "in_process": "In Process",
            "planned": "Not Started",
            "not_started": "Not Started",
        }

        for row in items:
            # Handle Row object from column-based query
            data = row._mapping if hasattr(row, '_mapping') else {col: getattr(row, col, None) for col in safe_columns}
            status_val = data.get("deployment_status", "planned") or "planned"
            status = status_map.get(status_val.lower(), "Not Started")

            table_data.append(
                {
                    "id": str(data.get("id", 0)),
                    "name": data.get("name") or "Unnamed",
                    "description": data.get("description") or "",
                    "component_type": data.get("component_type") or "Unknown",
                    "deployment_status": status,
                    "business_owner": data.get("business_owner") or "Unassigned",
                    "business_criticality": data.get("business_criticality") or "Medium",
                }
            )

        return success_response(
            {
                "data": table_data,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": pages,
                "has_prev": page > 1,
                "has_next": page < pages,
                "applications": table_data,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": pages,
                    "has_prev": page > 1,
                    "has_next": page < pages,
                },
            }
        )

    except Exception as e:
        try:
            db.session.rollback()
        except Exception as rollback_error:
            current_app.logger.warning(
                "Rollback failed in get_applications_table_data: %s",
                rollback_error,
                exc_info=True,
            )
        current_app.logger.error(
            f"Error in get_applications_table_data: {str(e)}", exc_info=True
        )
        from app.utils.api_response import server_error_response

        return server_error_response(
            message="Failed to retrieve applications table data",
            details={"debug_info": f"Exception type: {type(e).__name__}"},
        )
