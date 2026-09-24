"""
Per-tenant file storage utilities.

Uploads are stored under /uploads/{org_id}/documents/ to isolate
file access between organizations.

Download routes must call verify_file_access() before serving files.
"""

import os

from flask import current_app, g


def get_tenant_upload_path(filename, subfolder="documents"):
    """Get the upload path scoped to the current tenant.

    Returns: /uploads/{org_id}/{subfolder}/{filename}
    Falls back to /uploads/shared/ when no tenant context (CLI, background).
    """
    base = current_app.config.get("UPLOAD_FOLDER", "uploads")
    org_id = getattr(g, "current_org_id", None)

    if org_id:
        return os.path.join(base, str(org_id), subfolder, filename)
    return os.path.join(base, "shared", subfolder, filename)


def ensure_tenant_upload_dir(subfolder="documents"):
    """Create the tenant-specific upload directory if it doesn't exist."""
    base = current_app.config.get("UPLOAD_FOLDER", "uploads")
    org_id = getattr(g, "current_org_id", None)

    if org_id:
        path = os.path.join(base, str(org_id), subfolder)
    else:
        path = os.path.join(base, "shared", subfolder)

    os.makedirs(path, exist_ok=True)
    return path


def verify_file_access(file_org_id):
    """Verify the current user can access a file owned by file_org_id.

    Returns True if access is allowed, False otherwise.
    Platform admins can access all files.
    """
    from flask import has_request_context
    from flask_login import current_user

    # Outside a request (CLI, background jobs) there is no tenant context to
    # enforce against, so access is allowed. Inside a request the checks below
    # fail closed.
    if not has_request_context():
        return True

    # Platform admins can access all files, even without a tenant context.
    if getattr(current_user, "is_platform_admin", False):
        return True

    # Inside a request, fail closed: a caller without a tenant context, or a
    # file without an owning organisation, cannot be authorised.
    if not hasattr(g, "current_org_id") or g.current_org_id is None:
        return False
    if file_org_id is None:
        return False

    # Same org — allow
    return g.current_org_id == file_org_id
