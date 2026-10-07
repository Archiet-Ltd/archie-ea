from datetime import datetime  # migration-exempt

from sqlalchemy import text

from .. import db
from .mixins.core import TenantMixin


class SSOGroupRoleMapping(TenantMixin, db.Model):
    """Database-driven SSO group-to-role mapping (PLT-033).

    Replaces the hardcoded DEFAULT_GROUP_ROLE_MAP in app/auth/sso.py.
    Platform admins manage these via /admin/sso-settings.

    Pre-TenantMixin, this table had no tenant boundary at all -- any org's
    admin could see/edit every other org's SSO group->role mappings via
    /admin/sso-settings (a live config-leak and functional bug: a global
    UNIQUE(sso_group_name) meant two orgs could not both use a group named
    e.g. "Admins"), and app/auth/sso.py's login-time role lookup mixed every
    org's mappings together -- a real cross-tenant privilege-confusion risk.
    See app/commands/reconcile_schema.py's `_backfill_sso_mapping_organizations`
    and `_ensure_sso_mapping_tenant_unique_constraint` for the existing-database
    migration this needed: the old single-column UNIQUE constraint is replaced
    with a composite (organization_id, sso_group_name) one.
    """

    __tablename__ = "sso_group_role_mappings"

    id = db.Column(db.Integer, primary_key=True)
    sso_group_name = db.Column(db.String(200), nullable=False)
    role_name = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "sso_group_name",
            name="uq_sso_group_role_mappings_org_group",
        ),
    )

    def __repr__(self):
        return f"<SSOGroupRoleMapping {self.sso_group_name!r} -> {self.role_name!r}>"

    def to_dict(self):
        return {
            "id": self.id,
            "sso_group_name": self.sso_group_name,
            "role_name": self.role_name,
            "description": self.description or "",
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }


class EditableHTML(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    editor_name = db.Column(db.String(100), unique=True)
    value = db.Column(db.Text)

    @staticmethod
    def get_editable_html(editor_name):
        editable_html_obj = EditableHTML.query.filter_by(editor_name=editor_name).first()

        if editable_html_obj is None:
            editable_html_obj = EditableHTML(editor_name=editor_name, value="")
        return editable_html_obj


class ApplicationDocument(db.Model):
    """Model for storing application-related documents"""

    __tablename__ = "application_documents"

    id = db.Column(db.Integer, primary_key=True)

    # Organization (tenant isolation)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    application_component_id = db.Column(
        db.Integer, db.ForeignKey("application_components.id"), nullable=False
    )
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    file_name = db.Column(db.String(255), nullable=False)
    file_extension = db.Column(db.String(10), nullable=False)
    file_path = db.Column(db.String(500))  # For future use when files are actually saved
    file_size = db.Column(db.Integer)  # File size in bytes
    uploaded_by = db.Column(db.String(100))  # Display name of uploader (kept for templates)
    uploaded_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True, index=True
    )
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    organization = db.relationship("Organization", backref="application_documents")
    application = db.relationship(
        "ApplicationComponent", backref=db.backref("documents", lazy="dynamic")
    )
    uploader = db.relationship("User", foreign_keys=[uploaded_by_id])

    def __repr__(self):
        return f"<ApplicationDocument {self.title}>"


class ArchitectureDocument(db.Model):
    """
    Model for storing architecture-related documents for applications.
    Supports PDF, DOCX, PNG, JPG, XLSX, VSDX file types.
    """

    __tablename__ = "architecture_documents"

    id = db.Column(db.BigInteger, primary_key=True)
    application_id = db.Column(
        db.BigInteger, db.ForeignKey("application_components.id"), nullable=False, index=True
    )
    filename = db.Column(db.String(255), nullable=False)
    document_type = db.Column(
        db.String(100)
    )  # "Architecture Diagram", "Requirements", "Design Doc", etc.
    description = db.Column(db.Text)
    version = db.Column(db.String(50))
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.BigInteger)
    mime_type = db.Column(db.String(100))
    uploaded_by_id = db.Column(db.BigInteger, db.ForeignKey("users.id"))
    created_at = db.Column(  # tenant-exempt: model column default
        db.DateTime, default=text("CURRENT_TIMESTAMP"), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at = db.Column(  # tenant-exempt: model column default
        db.DateTime,
        default=text("CURRENT_TIMESTAMP"),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    application = db.relationship(
        "ApplicationComponent", backref=db.backref("architecture_documents", lazy="dynamic")
    )
    uploader = db.relationship(
        "User", backref=db.backref("uploaded_architecture_documents", lazy="dynamic")
    )

    # Document type choices for validation
    DOCUMENT_TYPES = [
        "Architecture Diagram",
        "Requirements Document",
        "Design Document",
        "Technical Specification",
        "Integration Guide",
        "Data Model",
        "API Documentation",
        "Security Assessment",
        "Other",
    ]

    # Allowed MIME types
    ALLOWED_MIME_TYPES = {
        "application/pdf": "pdf",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "image/png": "png",
        "image/jpeg": "jpg",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.visio": "vsd",
        "application/vnd.ms-visio.drawing": "vsdx",
    }

    def __repr__(self):
        return f"<ArchitectureDocument {self.filename}>"

    def to_dict(self):
        """Serialize document to dictionary for API responses."""
        return {
            "id": str(self.id),  # Convert to string to avoid serialization issues
            "application_id": str(self.application_id),
            "filename": self.filename,
            "document_type": self.document_type or "",
            "description": self.description or "",
            "version": self.version or "",
            "file_path": self.file_path,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "uploaded_by": None,  # Skip relationship to avoid serialization issues
            "uploaded_by_id": str(self.uploaded_by_id) if self.uploaded_by_id else None,
            "created_at": None,  # Skip datetime to avoid serialization issues
            "updated_at": None,
        }
