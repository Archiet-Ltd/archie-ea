"""Document upload/update/download/delete and capability mapping CRUD routes."""

import logging

from flask import current_app, flash, g, redirect, request, send_file, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.utils import secure_filename

from app import db
from app.decorators import audit_log
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.services.rate_limiter import rate_limit

from . import unified_applications_bp

logger = logging.getLogger(__name__)


@unified_applications_bp.route(
    "/<int:id>/documents/<int:doc_id>/update", methods=["POST"]
)
@login_required
@audit_log("document_update")
def update_document_file(id, doc_id):
    """Update a document file metadata"""
    app = ApplicationComponent.query.get_or_404(id)

    # Validate CSRF token manually (route exempted from global CSRF)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(
            f"CSRF validation failed for doc {doc_id} app {id}: {e}"
        )
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    from app.models.miscellaneous import ApplicationDocument

    query = ApplicationDocument.query.filter_by(
        id=doc_id, application_component_id=id
    )
    if not getattr(current_user, "is_platform_admin", False):
        query = query.filter_by(organization_id=g.current_org_id)
    doc = query.first_or_404()

    try:
        doc.title = request.form.get("title", doc.title)
        doc.description = request.form.get("description", doc.description)
        db.session.commit()
        flash(f'Document "{doc.title}" updated successfully.', "success")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error updating document {doc_id} for app {id}: {exc}"
        )
        flash("Error updating document. Please try again.", "danger")

    return redirect(
        url_for(
            "unified_applications.application_detail", id=app.id, tab="architecture"
        )
    )


@unified_applications_bp.route(
    "/<int:application_id>/upload-document", methods=["POST"]
)
@login_required
@rate_limit(5, "1m")
@audit_log("document_upload")
def upload_document_file(application_id):
    """Upload a document file for an application"""
    import os

    app = ApplicationComponent.query.get_or_404(application_id)

    # Validate CSRF token manually (route exempted from global CSRF)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(
            f"CSRF validation failed for doc upload app {application_id}: {e}"
        )
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    try:
        from app.models.miscellaneous import ApplicationDocument

        file = request.files.get("file")
        if not file or not file.filename:
            flash("No file selected. Please choose a file to upload.", "warning")
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

        # Validate file extension using config
        allowed_extensions = current_app.config.get(
            "ALLOWED_EXTENSIONS",
            {
                "pdf",
                "docx",
                "doc",
                "xlsx",
                "xls",
                "png",
                "jpg",
                "jpeg",
                "vsdx",
                "txt",
                "csv",
            },
        )
        file_ext = (
            file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        )
        if file_ext not in allowed_extensions:
            flash(
                f"File type '.{file_ext}' not allowed. Allowed: {', '.join(sorted(allowed_extensions))}",
                "warning",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

        # Validate file size before processing (prevent large file DoS)
        max_size = current_app.config.get(
            "MAX_CONTENT_LENGTH", 16 * 1024 * 1024
        )  # Default 16MB
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > max_size:
            flash(
                f"File too large ({file_size // (1024 * 1024)}MB). Maximum: {max_size // (1024 * 1024)}MB",
                "warning",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

        document_title = request.form.get("title", file.filename)
        document_description = request.form.get("description", "")
        uploaded_by = (
            current_user.full_name() if current_user.is_authenticated else "Anonymous"
        )

        document = ApplicationDocument(
            application_component_id=app.id,
            title=document_title,
            description=document_description,
            file_name=file.filename,
            file_extension=file_ext.upper(),
            file_path=None,
            file_size=None,
            uploaded_by=uploaded_by,
        )
        db.session.add(document)
        db.session.flush()

        upload_folder = current_app.config.get(
            "UPLOAD_FOLDER", os.path.join(current_app.root_path, "uploads", "documents")
        )
        os.makedirs(upload_folder, exist_ok=True)

        safe_name = secure_filename(file.filename)
        unique_filename = f"{document.id}_{safe_name}"
        file_path = os.path.join(upload_folder, unique_filename)
        file.save(file_path)

        document.file_path = file_path
        document.file_size = os.path.getsize(file_path)
        db.session.commit()

        current_app.logger.info(
            f"Document '{document_title}' uploaded for app {application_id} by {uploaded_by}"
        )
        flash(f'Document "{document_title}" uploaded successfully!', "success")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error uploading document for app {application_id}: {exc}"
        )
        flash("Error uploading document. Please try again.", "danger")

    return redirect(
        url_for(
            "unified_applications.application_detail", id=app.id, tab="architecture"
        )
    )


@unified_applications_bp.route("/documents/<int:doc_id>/download")
@login_required
def download_document_file(doc_id):
    """Download a document file"""
    from pathlib import Path

    from app.models.miscellaneous import ApplicationDocument

    query = ApplicationDocument.query.filter_by(id=doc_id)
    if not getattr(current_user, "is_platform_admin", False):
        query = query.filter_by(organization_id=g.current_org_id)
    doc = query.first_or_404()

    # Tenant isolation: verify the document belongs to the caller's organisation.
    # The query above skips the organisation filter for platform administrators,
    # who can reach any document; verify_file_access provides a second line of
    # defence, including unrestricted access for platform admins.
    from app.middleware.tenant_files import verify_file_access
    if not verify_file_access(doc.organization_id):
        flash("Access denied.", "error")
        return redirect(url_for("unified_applications.application_list"))

    # Path traversal protection: ensure file_path is within allowed upload directory
    if not doc.file_path:
        flash("Document file path not found.", "warning")
        return redirect(url_for("unified_applications.application_list"))
    
    # Get upload folder from config (same as used by DocumentUploadService)
    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    upload_path = Path(upload_folder).resolve()
    file_path = Path(doc.file_path).resolve()
    
    # Ensure file_path is within upload directory (prevent path traversal)
    try:
        file_path.relative_to(upload_path)
    except ValueError:
        # Path is outside upload directory - potential path traversal attack
        current_app.logger.warning(
            f"Path traversal attempt blocked: {doc.file_path} outside {upload_folder}"
        )
        flash("Access denied: Invalid file path.", "error")
        return redirect(url_for("unified_applications.application_list"))
    
    if file_path.exists():
        return send_file(doc.file_path, as_attachment=True, download_name=doc.file_name)

    flash("Document file not found on server.", "warning")
    return redirect(url_for("unified_applications.application_list"))


@unified_applications_bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@audit_log("document_delete")
def delete_document_file(doc_id):
    """Delete a document file"""
    import os

    from app.models.miscellaneous import ApplicationDocument

    query = ApplicationDocument.query.filter_by(id=doc_id)
    if not getattr(current_user, "is_platform_admin", False):
        query = query.filter_by(organization_id=g.current_org_id)
    doc = query.first_or_404()
    app_id = doc.application_component_id

    # Tenant isolation: verify the document belongs to the caller's organisation.
    # The query above skips the organisation filter for platform administrators,
    # who can reach any document; verify_file_access provides a second line of
    # defence, including unrestricted access for platform admins.
    from app.middleware.tenant_files import verify_file_access
    if not verify_file_access(doc.organization_id):
        flash("Access denied.", "danger")
        return redirect(url_for("unified_applications.application_list"))

    # Validate CSRF token manually (consistent with other doc routes in this file)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app_id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(
            f"CSRF validation failed for doc delete {doc_id}: {e}"
        )
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app_id, tab="architecture"
            )
        )

    # Ownership check: only the uploader or an admin can delete
    if current_user.is_authenticated:
        is_owner = (
            hasattr(doc, "uploaded_by")
            and doc.uploaded_by
            and doc.uploaded_by == current_user.full_name()
        )
        is_admin = hasattr(current_user, "role") and current_user.role in ("admin", "architect")
        if not is_owner and not is_admin:
            flash("You do not have permission to delete this document.", "error")
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app_id,
                    tab="architecture",
                )
            )

    try:
        if doc.file_path and os.path.exists(doc.file_path):
            os.remove(doc.file_path)
        db.session.delete(doc)
        db.session.commit()
        current_app.logger.info(f"Document {doc_id} deleted for app {app_id}")
        flash("Document deleted successfully.", "success")
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error deleting document {doc_id}: {exc}")
        flash("Error deleting document. Please try again.", "danger")

    return redirect(
        url_for(
            "unified_applications.application_detail", id=app_id, tab="architecture"
        )
    )


@unified_applications_bp.route("/<int:id>/capability-mappings", methods=["POST"])
@login_required
@audit_log("capability_mapping_create")
def application_capability_mapping_create(id):
    """Attach an enterprise capability to an application component."""
    app = ApplicationComponent.query.get_or_404(id)

    # Validate CSRF token manually (route exempted from global CSRF)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(
            f"CSRF validation failed for capability mapping app {id}: {e}"
        )
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    try:
        capability_id = request.form.get("capability_id", type=int)
        if not capability_id:
            flash("Please select a capability to map.", "warning")
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        existing = ApplicationCapabilityMapping.query.filter_by(
            application_id=id, capability_id=capability_id
        ).first()
        if existing:
            flash("This capability is already mapped to this application.", "warning")
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )

        mapping = ApplicationCapabilityMapping(
            application_id=id,
            capability_id=capability_id,
        )
        db.session.add(mapping)
        db.session.commit()
        current_app.logger.info(f"Capability {capability_id} mapped to app {id}")
        flash("Capability mapped successfully.", "success")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error creating capability mapping for app {id}: {exc}"
        )
        flash("Error creating capability mapping. Please try again.", "danger")

    return redirect(
        url_for(
            "unified_applications.application_detail", id=app.id, tab="architecture"
        )
    )


@unified_applications_bp.route(
    "/<int:id>/capability-mappings/<int:mapping_id>/delete", methods=["POST"]
)
@login_required
@audit_log("capability_mapping_delete")
def application_capability_mapping_delete(id, mapping_id):
    """Detach a capability mapping from an application."""
    app = ApplicationComponent.query.get_or_404(id)

    # Validate CSRF token manually (route exempted from global CSRF)
    try:
        token = request.form.get("csrf_token")
        if not token:
            flash(
                "Security validation failed: CSRF token missing. Please refresh the page and try again.",
                "error",
            )
            return redirect(
                url_for(
                    "unified_applications.application_detail",
                    id=app.id,
                    tab="architecture",
                )
            )
        validate_csrf(token)
    except CSRFError as e:
        current_app.logger.warning(
            f"CSRF validation failed for capability mapping delete {mapping_id} app {id}: {e}"
        )
        flash(
            "Security validation failed. Please refresh the page and try again.",
            "error",
        )
        return redirect(
            url_for(
                "unified_applications.application_detail", id=app.id, tab="architecture"
            )
        )

    try:
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        mapping = ApplicationCapabilityMapping.query.filter_by(
            id=mapping_id, application_id=id
        ).first_or_404()
        db.session.delete(mapping)
        db.session.commit()
        current_app.logger.info(
            f"Capability mapping {mapping_id} removed from app {id}"
        )
        flash("Capability mapping removed.", "success")

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            f"Error deleting capability mapping {mapping_id} for app {id}: {exc}"
        )
        flash("Error removing capability mapping. Please try again.", "danger")

    return redirect(
        url_for(
            "unified_applications.application_detail", id=app.id, tab="architecture"
        )
    )
