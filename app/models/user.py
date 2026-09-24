from flask import current_app
from flask_login import AnonymousUserMixin, UserMixin
from itsdangerous import BadSignature, SignatureExpired
from itsdangerous import URLSafeTimedSerializer as Serializer
from sqlalchemy import event, func, select
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db, login_manager
from .validators import validate_email as validate_model_email

# ── Enterprise RBAC role constants (ENT-068, updated NS-001) ─────────────────────────
ROLE_SOLUTION_ARCHITECT = "solution_architect"
ROLE_ENTERPRISE_ARCHITECT = "enterprise_architect"
ROLE_BUSINESS_ARCHITECT = "business_architect"
ROLE_ARB_MEMBER = "arb_member"
ROLE_PORTFOLIO_MANAGER = "portfolio_manager"
ROLE_CTO = "cto"
ROLE_PROCUREMENT = "procurement"
ROLE_APPLICATION_MANAGER = "application_manager"
ROLE_PLATFORM_ADMIN = "platform_admin"
# Promoted from charter-only to assignable, 31 Aug 2026.
#
# security_architect: the solution blueprint scores a Security Viewpoint as one
# of its fifteen sections, and no persona owned it. A governance artefact the
# product grades with nobody accountable for it is the same defect shape as an
# ARB decision with no visible decider -- which this codebase has been fixing
# all week. TOGAF 9.2, which Entelim implements, names the role.
#
# data_architect: /architecture/data-architecture, data lineage and data
# stewardship all ship and were reachable only inside enterprise_architect's
# zone, which ARCH-123 recorded as a temporary fold ("no dedicated role yet")
# rather than a decision to leave them there.
#
# The other five charters (application_architect, integration_architect,
# systems_architect, business_analyst, product_analyst) stay charter-only: they
# own no surface the product grades, and two are analyst rather than architect
# roles. See ASPIRATIONAL in scripts/check_persona_vocabularies.py.
ROLE_SECURITY_ARCHITECT = "security_architect"
ROLE_DATA_ARCHITECT = "data_architect"

VALID_ROLES = [
    ROLE_SOLUTION_ARCHITECT,
    ROLE_ENTERPRISE_ARCHITECT,
    ROLE_BUSINESS_ARCHITECT,
    ROLE_ARB_MEMBER,
    ROLE_PORTFOLIO_MANAGER,
    ROLE_CTO,
    ROLE_PROCUREMENT,
    ROLE_APPLICATION_MANAGER,
    ROLE_PLATFORM_ADMIN,
    ROLE_SECURITY_ARCHITECT,
    ROLE_DATA_ARCHITECT,
]

# Role display names for UI
ROLE_DISPLAY_NAMES = {
    ROLE_SOLUTION_ARCHITECT: "Solution Architect",
    ROLE_ENTERPRISE_ARCHITECT: "Enterprise Architect",
    ROLE_BUSINESS_ARCHITECT: "Business Architect",
    ROLE_ARB_MEMBER: "ARB Member",
    ROLE_PORTFOLIO_MANAGER: "Portfolio Manager",
    ROLE_CTO: "CTO / CIO",
    ROLE_PROCUREMENT: "Procurement",
    ROLE_APPLICATION_MANAGER: "Application Manager",
    ROLE_PLATFORM_ADMIN: "Platform Admin",
    ROLE_SECURITY_ARCHITECT: "Security Architect",
    ROLE_DATA_ARCHITECT: "Data Architect",
}


class Permission:
    GENERAL = 0x01
    ADMINISTER = 0xFF


class Role(db.Model):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    index = db.Column(db.String(64))
    default = db.Column(db.Boolean, default=False, index=True)
    permissions = db.Column(db.Integer)
    users = db.relationship("User", backref="role", lazy="dynamic")

    @staticmethod
    def insert_roles():
        # "Architect" is the DEFAULT role for new sign-ups. This platform is FOR
        # architects, and the CRUD route guards use require_roles("admin",
        # "architect") — so the default role MUST normalize to "architect", or
        # every normal user gets 403 on create/update/delete of their own data
        # (the "CRUD is broken" symptom). Architect gets GENERAL permission (NOT
        # ADMINISTER), so user.can(ADMINISTER)/is_admin() stays False and truly
        # admin-only areas (user mgmt, seeding) remain restricted.
        # (name, permissions, index, is_default)
        roles = {
            "User": (Permission.GENERAL, "main", False),
            "Architect": (Permission.GENERAL, "main", True),
            "Administrator": (Permission.ADMINISTER, "admin", False),
            # A-03 (S2): "no read-only role exists" — added as a selectable
            # Role row only. permissions=0 means user.can(GENERAL) and
            # user.can(ADMINISTER) both evaluate False, so a Viewer can
            # authenticate and read but every write-guarding
            # @permission_required/@admin_required check rejects them same
            # as an unauthenticated user would be rejected for admin routes.
            # Purely additive: existing roles/users are untouched, and no
            # route currently assigns this role automatically.
            "Viewer": (0, "main", False),
            # M-05/M-06 (S1): a distinct decision-making role, separate from
            # the default "Architect" contributor role. Purely additive —
            # existing roles/users are untouched and no route currently
            # assigns this role automatically. Not yet used as a hard gate
            # on ARBGovernanceService.record_decision (that would lock out
            # every existing production approver, all of whom are on the
            # default "Architect" role today); it exists so an organization
            # can start assigning it, and record_decision already rejects
            # any account whose role carries no GENERAL permission (e.g. a
            # pure Viewer) regardless of this role's presence.
            "Approver": (Permission.GENERAL, "main", False),
        }
        for r in roles:
            role = Role.query.filter_by(name=r).first()
            if role is None:
                role = Role(name=r)
            role.permissions = roles[r][0]
            role.index = roles[r][1]
            role.default = roles[r][2]
            db.session.add(role)
        db.session.commit()

    def __repr__(self):
        return f"<Role '{self.name}'>"


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}
    id = db.Column(db.Integer, primary_key=True)
    confirmed = db.Column(db.Boolean, default=False)
    first_name = db.Column(db.String(64), index=True)
    last_name = db.Column(db.String(64), index=True)
    email = db.Column(db.String(64), unique=True, index=True)
    password_hash = db.Column(
        db.String(255)
    )  # Increased to support modern scrypt hashes (159+ chars)
    # Primary role association — bitfield-based, used by user.can() / user.is_admin().
    # This is the authoritative mechanism for all auth checks in this codebase.
    # A second mechanism (UserRole junction table) exists in app.models.permission for
    # future granular RBAC. Do NOT mix them: use role_id / user.can() for auth guards.
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"))

    # Enterprise RBAC role (ENT-068) — granular role for access control.
    # Defaults to 'platform_admin' for backward compatibility (existing users get full access).
    enterprise_role = db.Column(
        db.String(50), nullable=False, default=ROLE_PLATFORM_ADMIN, server_default=ROLE_PLATFORM_ADMIN
    )

    # SSO / Enterprise Identity (S0-01)
    external_id = db.Column(db.String(255), index=True)
    sso_provider = db.Column(db.String(50))

    # Onboarding fields
    role_archetype = db.Column(
        db.String(50)
    )  # single-primary compatibility (architect, analyst, manager, compliance, engineer)
    role_archetypes = db.Column(db.Text)  # JSON array string for multiple roles
    # Primary value pipeline preference (optional)
    primary_value_pipeline = db.Column(db.String(64))
    company_name = db.Column(db.String(128))
    industry = db.Column(db.String(64))
    team_size = db.Column(db.String(20))
    app_count = db.Column(db.String(20))
    primary_concern = db.Column(db.Text)
    primary_frameworks = db.Column(db.Text)  # JSON array as string
    onboarding_completed_at = db.Column(db.DateTime)
    # Dashboard "Welcome to Entelim" one-line banner, dismissed once per
    # user, forever (shell-wave-1 Task 5). Deliberately separate from
    # onboarding_completed_at: that column is set by /dashboard/api/
    # onboarding-complete, which also rewrites enterprise_role -- reusing it for
    # a plain dismiss click would carry a side effect the UI never asks for.
    # Nullable, no backfill, tolerated when NULL (reconcile-schema adds it).
    welcome_banner_dismissed_at = db.Column(db.DateTime, nullable=True)
    generation_method = db.Column(db.String(50))  # text, upload, template
    first_architecture_id = db.Column(db.Integer)

    # Multi-tenancy: every user belongs to exactly one organization
    organization_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    is_org_admin = db.Column(db.Boolean, default=False)
    is_platform_admin = db.Column(db.Boolean, default=False)

    # PLT-018: Business unit scoping — links user to a BusinessActor (actor_type='Department' or similar)
    business_unit_id = db.Column(db.Integer, db.ForeignKey("business_actors.id"), nullable=True)  # migration-exempt

    # PLT-017: In-app notification preferences — JSON dict keyed by notification type.
    # Default: all types enabled. migration-exempt (DDL added in manage.py init_db)
    notification_preferences = db.Column(db.JSON, nullable=True)  # migration-exempt

    @staticmethod
    def normalize_email(email):
        if email is None:
            return None
        return email.strip().lower()

    @classmethod
    def find_by_email(cls, email):
        normalized_email = cls.normalize_email(email)
        if not normalized_email:
            return None
        return cls.query.filter(func.lower(cls.email) == normalized_email).first()

    @validates("email")
    def validate_email_field(self, key, value):
        return validate_model_email(value, key)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.role is None:
            if self.normalize_email(self.email) == self.normalize_email(
                current_app.config["ADMIN_EMAIL"]
            ):
                self.role = Role.query.filter_by(
                    permissions=Permission.ADMINISTER
                ).first()
            if self.role is None:
                self.role = Role.query.filter_by(default=True).first()

    def full_name(self):
        parts = [part for part in (self.first_name, self.last_name) if part]
        if parts:
            return " ".join(parts)
        return self.email or "Unknown User"

    def can(self, permissions):
        # Primary check: bitfield on Role model
        if (
            self.role is not None
            and self.role.permissions is not None
            and (self.role.permissions & permissions) == permissions
        ):
            return True
        # Fallback: check UserRole junction table for granular RBAC
        try:
            from app.models.permission import UserRole

            junction_roles = (
                db.session.query(Role)
                .join(UserRole, UserRole.role_id == Role.id)
                .filter(UserRole.user_id == self.id)
                .all()
            )
            for role in junction_roles:
                if role.permissions is not None and (role.permissions & permissions) == permissions:
                    return True
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
            db.session.rollback()  # Prevent transaction poisoning if user_roles table missing
        return False

    def is_admin(self):
        return self.can(Permission.ADMINISTER)

    @property
    def role_name(self):
        """Safe role name accessor — returns 'anonymous' if role is unset or DB is unavailable."""
        try:
            return self.role.name if self.role else "anonymous"
        except Exception:
            return "anonymous"

    @property
    def password(self):
        raise AttributeError("`password` is not a readable attribute")

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ---------------- Token Methods ----------------

    def generate_confirmation_token(self):
        s = Serializer(current_app.config["SECRET_KEY"])
        return s.dumps({"confirm": self.id})

    def generate_email_change_token(self, new_email):
        s = Serializer(current_app.config["SECRET_KEY"])
        return s.dumps({"change_email": self.id, "new_email": new_email})

    def generate_password_reset_token(self):
        s = Serializer(current_app.config["SECRET_KEY"])
        return s.dumps({"reset": self.id})

    def confirm_account(self, token, expiration=604800):
        s = Serializer(current_app.config["SECRET_KEY"])
        try:
            data = s.loads(token, max_age=expiration)
        except (BadSignature, SignatureExpired):
            return False
        if data.get("confirm") != self.id:
            return False
        self.confirmed = True
        db.session.add(self)
        db.session.commit()
        return True

    def change_email(self, token, expiration=3600):
        s = Serializer(current_app.config["SECRET_KEY"])
        try:
            data = s.loads(token, max_age=expiration)
        except (BadSignature, SignatureExpired):
            return False
        if data.get("change_email") != self.id:
            return False
        new_email = self.normalize_email(data.get("new_email"))
        if (
            new_email is None
            or (
                User.find_by_email(new_email) is not None
                and User.find_by_email(new_email).id != self.id
            )
        ):
            return False
        self.email = new_email
        db.session.add(self)
        db.session.commit()
        return True

    def reset_password(self, token, new_password, expiration=3600):
        s = Serializer(current_app.config["SECRET_KEY"])
        try:
            data = s.loads(token, max_age=expiration)
        except (BadSignature, SignatureExpired):
            return False
        if data.get("reset") != self.id:
            return False
        self.password = new_password
        db.session.add(self)
        db.session.commit()
        return True

    # ── Enterprise RBAC helpers (ENT-068) ────────────────────────────

    def has_role(self, *roles):
        """Check if user has any of the specified enterprise roles."""
        return self.enterprise_role in roles

    def can_edit_solutions(self):
        """Solution editing: solution_architect, enterprise_architect, business_architect, platform_admin."""
        return self.enterprise_role in (
            ROLE_SOLUTION_ARCHITECT,
            ROLE_ENTERPRISE_ARCHITECT,
            ROLE_BUSINESS_ARCHITECT,
            ROLE_PLATFORM_ADMIN,
        )

    def can_edit_archimate(self):
        """ArchiMate editing: enterprise_architect, business_architect, platform_admin."""
        return self.enterprise_role in (
            ROLE_ENTERPRISE_ARCHITECT,
            ROLE_BUSINESS_ARCHITECT,
            ROLE_PLATFORM_ADMIN,
        )

    def can_vote_arb(self):
        """ARB voting: arb_member, enterprise_architect, business_architect, platform_admin."""
        return self.enterprise_role in (
            ROLE_ARB_MEMBER,
            ROLE_ENTERPRISE_ARCHITECT,
            ROLE_BUSINESS_ARCHITECT,
            ROLE_PLATFORM_ADMIN,
        )

    def can_manage_portfolio(self):
        """Portfolio management: portfolio_manager, enterprise_architect, business_architect, platform_admin."""
        return self.enterprise_role in (
            ROLE_PORTFOLIO_MANAGER,
            ROLE_ENTERPRISE_ARCHITECT,
            ROLE_BUSINESS_ARCHITECT,
            ROLE_PLATFORM_ADMIN,
        )

    # PLT-017: Notification preference helpers
    _DEFAULT_NOTIFICATION_PREFS = {
        "arb_decisions": True,
        "solution_updates": True,
        "assignment_changes": True,
        "weekly_digest": True,
        "mention_notifications": True,
    }

    def get_notification_preference(self, key):  # model-safety-ok
        """Return True/False for a notification preference key. Defaults to True if not set."""
        import json
        raw = getattr(self, "notification_preferences", None)
        if raw is None:
            prefs = {}
        elif isinstance(raw, str):
            prefs = json.loads(raw)
        else:
            prefs = raw
        return prefs.get(key, self._DEFAULT_NOTIFICATION_PREFS.get(key, True))

    def set_notification_preferences(self, prefs_dict):
        """Replace notification_preferences with validated dict. Only known keys are stored."""
        known_keys = set(self._DEFAULT_NOTIFICATION_PREFS.keys())
        self.notification_preferences = {
            k: bool(v) for k, v in prefs_dict.items() if k in known_keys
        }

    def __repr__(self):
        return f"<User '{self.full_name()}'>"


# ---------------- Anonymous User ----------------


class AnonymousUser(AnonymousUserMixin):
    def can(self, _):
        return False

    def is_admin(self):
        return False


login_manager.anonymous_user = AnonymousUser


@login_manager.user_loader
def load_user(user_id):
    from sqlalchemy.exc import OperationalError

    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    except OperationalError:
        # Database schema may not be initialized yet (tests/early startup).
        # Fail gracefully by returning None rather than raising.
        return None


@event.listens_for(User, "before_insert")
def _assign_default_organization(mapper, connection, target):
    """Guarantee every user has an organization (users.organization_id is NOT NULL).

    The email register, SSO and SAML flows all create a User without setting an
    organization, which on a fresh install (no org rows yet) violated the NOT NULL
    constraint and 500'd the very first sign-up. Assign the shared 'default'
    organization, creating it on first use. An explicitly-set organization_id
    (e.g. create_admin.py, invited users) is left untouched.
    """
    if target.organization_id is not None:
        return
    from app.models.organization import Organization

    orgs = Organization.__table__
    row = connection.execute(
        select(orgs.c.id).where(orgs.c.slug == "default").limit(1)
    ).first()
    if row is not None:
        target.organization_id = row[0]
    else:
        # Table.insert() applies the model's column defaults (plan, created_at, …),
        # and runs on the flush connection so it is safe inside before_insert.
        result = connection.execute(
            orgs.insert().values(name="Default Organization", slug="default")
        )
        target.organization_id = result.inserted_primary_key[0]
