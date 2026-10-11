"""
OrgRole — per-organisation role assignments for RBAC (COM-007).

One row per (organization, user) pair. Role hierarchy: org_admin > architect > viewer.
"""

from datetime import datetime

from app import db

VALID_ORG_ROLES = ("org_admin", "architect", "viewer")

ROLE_HIERARCHY = {
    "org_admin": 2,
    "architect": 1,
    "viewer": 0,
}


class OrgRole(db.Model):  # migration-exempt
    """Stores the role a user holds within a specific organisation."""

    __tablename__ = "org_roles"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "user_id", name="uq_org_role_user"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    role = db.Column(db.String(50), nullable=False, default="viewer")
    granted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @classmethod
    def get_role(cls, org_id, user_id):
        """Return the role string for user in org, or None if no row exists."""
        record = cls.query.filter_by(
            organization_id=org_id, user_id=user_id
        ).first()
        return record.role if record else None

    @classmethod
    def set_role(cls, org_id, user_id, role, granted_by_id=None):
        """Upsert the role for user in org. Caller must commit the session."""
        if role not in VALID_ORG_ROLES:
            raise ValueError(
                f"Invalid role '{role}'. Must be one of {VALID_ORG_ROLES}"
            )
        record = cls.query.filter_by(
            organization_id=org_id, user_id=user_id
        ).first()
        if record is None:
            record = cls(organization_id=org_id, user_id=user_id)
            db.session.add(record)
        record.role = role
        record.granted_by = granted_by_id
        db.session.flush()
        return record

    def __repr__(self):
        return (
            f"<OrgRole org={self.organization_id} "
            f"user={self.user_id} role={self.role}>"
        )


def apply_admin_role_change(user, new_role):
    """Change ``user``'s global Role to ``new_role``, keeping the per-organisation
    OrgRole grant, the denormalised ``is_org_admin`` column, and a platform
    admin's Permission.ADMINISTER in step -- the one implementation shared by
    every admin page that lets an operator set a user's Role directly (the
    platform admin's "change account type" page, in both the v1 and v2 admin
    services). Neither service re-implements this state transition.

    Routes every crossing of the Administrator boundary through
    ``User.grant_org_admin`` / ``revoke_org_admin``, so a platform admin's
    Permission.ADMINISTER is never stripped by an org-scoped role change:
    revoke_org_admin() no-ops for a platform admin regardless of which role
    was picked in the form, exactly as every other revoke site already
    behaves (team page, organisation-admin toggle, remove-from-org,
    delete-organisation).
    """
    was_admin = user.is_admin()
    wants_admin = bool(new_role is not None and new_role.name == "Administrator")

    if wants_admin and not was_admin:
        user.grant_org_admin()
        OrgRole.set_role(user.organization_id, user.id, "org_admin")
    elif was_admin and not wants_admin:
        user.revoke_org_admin(fallback_role=new_role)
        if not user.is_admin():
            # The revoke actually took effect (not a platform-admin no-op):
            # the per-organisation grant is gone too.
            OrgRole.query.filter_by(
                organization_id=user.organization_id, user_id=user.id
            ).delete(synchronize_session=False)
    else:
        user.role = new_role


def apply_admin_role_grant_for_new_user(user, role):
    """Write the per-organisation OrgRole grant and the denormalised column
    for a newly created user whose Role is Administrator, through
    ``User.grant_org_admin`` -- the one implementation shared by both admin
    services' ``create_user``, so a new Administrator account agrees with
    the team page and database-level guards from the moment it exists."""
    if role is not None and role.name == "Administrator":
        user.grant_org_admin()
        OrgRole.set_role(user.organization_id, user.id, "org_admin")
