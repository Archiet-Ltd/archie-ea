# migration-exempt
"""
Application Owner Model (NS-002)

Links users to applications they own for Application Manager persona filtering.
Part of North Star Persona MVP implementation.

ADR Reference: docs/adr/0011-application-manager-persona.md
"""

from datetime import datetime

from .. import db


class ApplicationOwner(db.Model):
    """
    Junction table linking users to applications they own.

    Supports multiple ownership types per application:
    - primary: Main accountable owner
    - backup: Secondary owner for coverage
    - technical: Technical/operations owner
    - business: Business/product owner

    Used by Application Manager persona to filter views to only owned apps.
    """
    __tablename__ = "application_owners"
    __table_args__ = (
        db.UniqueConstraint(
            "application_id", "user_id", "ownership_type",
            name="uq_application_owner_type"
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)

    # Foreign keys
    # Nullable per migrations/versions/20260926_relax_owner_app.py (ADR 0002
    # expand step): the database allows NULL so a later ownership record can
    # point at any element, not only an application.
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assigned_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Provenance fields for backfill
    source_table = db.Column(db.String(50), nullable=True)
    source_id = db.Column(db.Integer, nullable=True)

    # R1-B03 PR 2: a nullable, typed reference so this one ownership record
    # can own any element -- starting with capabilities -- without a second
    # owner table or a new owner column on the element's own model (both
    # forbidden by the brief). A row owns either an application
    # (application_id) or an element (element_type + element_id), never
    # both; enforced in the writer, not a DB constraint, since existing
    # rows already carry application_id with these two columns NULL.
    element_type = db.Column(db.String(30), nullable=True, index=True)
    element_id = db.Column(db.Integer, nullable=True)

    # Ownership details
    ownership_type = db.Column(
        db.String(50),
        nullable=False,
        default="primary",
    )  # 'primary', 'backup', 'technical', 'business'

    # Timestamps
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    application = db.relationship(
        "ApplicationComponent",
        backref=db.backref("owners", lazy="dynamic", cascade="all, delete-orphan"),
        foreign_keys=[application_id],
    )
    user = db.relationship(
        "User",
        backref=db.backref("owned_applications", lazy="dynamic"),
        foreign_keys=[user_id],
    )
    assigner = db.relationship(
        "User",
        foreign_keys=[assigned_by],
    )
    organization = db.relationship(
        "Organization",
        backref=db.backref("application_owners", lazy="dynamic"),
    )

    # Valid ownership types
    OWNERSHIP_TYPES = ["primary", "backup", "technical", "business"]
    OWNERSHIP_LABELS = {
        "primary": "Primary",
        "backup": "Backup",
        "technical": "Technical",
        "business": "Business",
    }

    # Element types this record may own via element_type/element_id.
    # Capability is the first; a later element type is added here, not as a
    # new owner table.
    ELEMENT_TYPES = ["capability"]

    @property
    def is_primary(self):
        """True when this row records the main accountable owner.

        services.py has always read `ownership.is_primary` - in get_owned_apps'
        summary and in the ownership breakdown - but no such attribute existed, so
        both raised AttributeError and returned a 500 the moment a user actually
        owned something. An empty portfolio skipped the loop entirely, which is
        why the pages looked healthy until the first ownership row was created.
        """
        return self.ownership_type == "primary"

    def __repr__(self):
        return f"<ApplicationOwner app={self.application_id} user={self.user_id} type={self.ownership_type}>"

    def to_dict(self):
        return {
            "id": self.id,
            "application_id": self.application_id,
            "element_type": self.element_type,
            "element_id": self.element_id,
            "user_id": self.user_id,
            "ownership_type": self.ownership_type,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "assigned_by": self.assigned_by,
            "organization_id": self.organization_id,
        }

    @classmethod
    def get_owners_for_application(cls, application_id, organization_id):
        """Get all owners for an application."""
        return cls.query.filter(
            cls.application_id == application_id,
            cls.organization_id == organization_id,
        ).all()

    @classmethod
    def get_display_rows_for_application(cls, application_id, organization_id):
        """Read one application's owners with tenant-fenced user display data."""
        from app.models.user import User

        owner_rows = cls.get_owners_for_application(application_id, organization_id)
        user_ids = [row.user_id for row in owner_rows if row.user_id is not None]
        users = {}
        if user_ids:
            users = {
                user.id: user
                for user in db.session.execute(
                    db.select(User)
                    .where(User.organization_id == organization_id)
                    .where(User.id.in_(user_ids))
                ).scalars()
            }

        display_rows = []
        for row in owner_rows:
            user = users.get(row.user_id)
            full_name = " ".join(part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part).strip()
            display_rows.append({
                "id": row.id,
                "user_id": row.user_id,
                "user_name": full_name or (user.email if user else "Unknown"),
                "user_email": user.email if user else None,
                "ownership_type": row.ownership_type,
                "ownership_type_label": cls.OWNERSHIP_LABELS.get(
                    row.ownership_type,
                    (row.ownership_type or "").capitalize(),
                ),
                "assigned_at": row.assigned_at.isoformat() if row.assigned_at else None,
                "assigned_by": row.assigned_by,
                # False when the owner has been deactivated (R1-B26): the
                # fact sheet marks them; the row stays until an administrator
                # transfers it from the leaver list.
                "owner_active": bool(user.is_active) if user is not None else True,
            })
        return display_rows

    @classmethod
    def get_applications_for_user(cls, user_id, organization_id):
        """Get all application IDs owned by a user."""
        return [
            row.application_id
            for row in cls.query.filter(
                cls.user_id == user_id,
                cls.organization_id == organization_id,
            ).all()
        ]

    @classmethod
    def is_owner(cls, user_id, application_id, organization_id):
        """Check if user owns an application."""
        return cls.query.filter(
            cls.user_id == user_id,
            cls.application_id == application_id,
            cls.organization_id == organization_id,
        ).first() is not None

    @classmethod
    def get_owners_for_element(cls, element_type, element_id, organization_id):
        """Get all owners for a non-application element (e.g. a capability)."""
        return cls.query.filter(
            cls.element_type == element_type,
            cls.element_id == element_id,
            cls.organization_id == organization_id,
        ).all()

    @classmethod
    def get_display_rows_for_element(cls, element_type, element_id, organization_id):
        """Read one element's owners with tenant-fenced user display data --
        the element-reference counterpart of get_display_rows_for_application."""
        from app.models.user import User

        owner_rows = cls.get_owners_for_element(element_type, element_id, organization_id)
        user_ids = [row.user_id for row in owner_rows if row.user_id is not None]
        users = {}
        if user_ids:
            users = {
                user.id: user
                for user in db.session.execute(
                    db.select(User)
                    .where(User.organization_id == organization_id)
                    .where(User.id.in_(user_ids))
                ).scalars()
            }

        display_rows = []
        for row in owner_rows:
            user = users.get(row.user_id)
            full_name = " ".join(part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part).strip()
            display_rows.append({
                "id": row.id,
                "user_id": row.user_id,
                "user_name": full_name or (user.email if user else "Unknown"),
                "user_email": user.email if user else None,
                "ownership_type": row.ownership_type,
                "ownership_type_label": cls.OWNERSHIP_LABELS.get(
                    row.ownership_type,
                    (row.ownership_type or "").capitalize(),
                ),
                "assigned_at": row.assigned_at.isoformat() if row.assigned_at else None,
                "assigned_by": row.assigned_by,
                # False when the owner has been deactivated (R1-B26): the
                # fact sheet marks them; the row stays until an administrator
                # transfers it from the leaver list.
                "owner_active": bool(user.is_active) if user is not None else True,
            })
        return display_rows

    @classmethod
    def is_owner_of_element(cls, user_id, element_type, element_id, organization_id):
        """Check if user owns a non-application element."""
        return cls.query.filter(
            cls.user_id == user_id,
            cls.element_type == element_type,
            cls.element_id == element_id,
            cls.organization_id == organization_id,
        ).first() is not None

    @classmethod
    def find_duplicate(
        cls,
        user_id,
        ownership_type,
        organization_id,
        *,
        application_id=None,
        element_type=None,
        element_id=None,
        exclude_owner_id=None,
    ):
        """The one duplicate rule: an existing row giving ``user_id`` the same
        ``ownership_type`` on the same application, or on the same element
        (``element_type`` + ``element_id``), in the same organisation.

        Used by the application owner writer routes and by the leaver
        transfer, so a person can never end up holding the same ownership
        twice by either path.
        """
        query = cls.query.filter(
            cls.user_id == user_id,
            cls.ownership_type == ownership_type,
            cls.organization_id == organization_id,
        )
        if application_id is not None:
            query = query.filter(cls.application_id == application_id)
        else:
            query = query.filter(
                cls.application_id.is_(None),
                cls.element_type == element_type,
                cls.element_id == element_id,
            )
        if exclude_owner_id is not None:
            query = query.filter(cls.id != exclude_owner_id)
        return query.first()

    def transfer_to(self, new_user_id, organization_id):
        """Hand this ownership to ``new_user_id``, in place.

        Updates ``user_id`` on this row, keeping ``ownership_type``, the
        application or element reference and the organisation. When the target
        already holds the same ownership on the same item the departing row is
        deleted instead, leaving exactly one. Returns ``(row, removed)`` where
        ``row`` is the surviving row. The caller validates the target user and
        commits.
        """
        if self.organization_id != organization_id:
            raise ValueError("ownership row belongs to a different organisation")
        duplicate = type(self).find_duplicate(
            new_user_id,
            self.ownership_type,
            organization_id,
            application_id=self.application_id,
            element_type=self.element_type,
            element_id=self.element_id,
            exclude_owner_id=self.id,
        )
        if duplicate is not None:
            db.session.delete(self)
            return duplicate, True
        self.user_id = new_user_id
        return self, False
