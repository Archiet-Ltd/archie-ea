"""PermissionGrant: one row of the per-organisation permission matrix.

A row says whether a subject (a role name, or a single user) may take one
action on one kind of object inside one organisation. The service in
``app.core.auth.permissions`` reads the rows; with no row the defaults in code
apply. Tenant-scoped through ``TenantMixin``, so every ORM query on this table
is filtered to the ambient organisation.
"""

from datetime import datetime

from app import db
from app.models.mixins.core import TenantMixin


class PermissionGrant(TenantMixin, db.Model):
    __tablename__ = "permission_grants"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "subject_type",
            "subject_key",
            "object_type",
            "action",
            name="uq_permission_grants_subject_action",
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    subject_type = db.Column(db.String(16), nullable=False)  # "role" or "user"
    subject_key = db.Column(db.Text, nullable=False)  # role string, or the user id
    object_type = db.Column(db.String(64), nullable=False)  # "application" in slice 1
    action = db.Column(db.String(16), nullable=False)  # create | read | edit | delete
    allowed = db.Column(db.Boolean, nullable=False)
    granted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return (
            f"<PermissionGrant org={self.organization_id} {self.subject_type}="
            f"{self.subject_key} {self.object_type}.{self.action}={self.allowed}>"
        )
