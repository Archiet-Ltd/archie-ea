"""SCIM provisioning tables (R1-B26 PR 1, TB-0143).

``ScimToken`` is the per-organisation bearer credential an identity provider
presents at ``/scim/v2``. Only the sha256 digest is stored (the same
``account_token.digest`` the e-mail link tokens use); the raw value exists in
the response that issues it and nowhere else.

Deliberately **not** a ``TenantMixin`` model, for the reason ``UserSession``
is not: the lookup that resolves a bearer token to its organisation runs
before any tenant context exists, so a tenant filter would make the lookup
itself impossible. Every query on this table is keyed by ``token_hash`` or by
an explicit ``organization_id`` predicate. Do not add ``TenantMixin`` "for
consistency".

``ScimGroupMembership`` records which user sits in which SCIM group (a group
is an ``SSOGroupRoleMapping`` row). ``SSOGroupRoleMapping`` names the groups
but nothing recorded membership, and a membership PATCH needs it to recompute
the user's role through ``map_groups_to_role``.
"""

from datetime import datetime

from app.extensions import db
from app.models.mixins.core import TenantMixin


class ScimToken(db.Model):
    __tablename__ = "scim_tokens"
    __table_args__ = (
        db.Index("uq_scim_tokens_token_hash", "token_hash", unique=True),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = db.Column(db.String(64), nullable=False)
    token_prefix = db.Column(db.String(16), nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<ScimToken id={self.id} org={self.organization_id} prefix={self.token_prefix}>"


class ScimGroupMembership(TenantMixin, db.Model):
    __tablename__ = "scim_group_memberships"
    __table_args__ = (
        db.Index(
            "uq_scim_group_membership",
            "organization_id", "group_mapping_id", "user_id",
            unique=True,
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    group_mapping_id = db.Column(
        db.Integer,
        db.ForeignKey("sso_group_role_mappings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
