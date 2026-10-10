from app import db
from app.models.organization import Organization
from app.models.user import User
from sqlalchemy.exc import IntegrityError

class OrgService:
    @staticmethod
    def create_org(name, admin_email, admin_password=None, admin_first_name=None, admin_last_name=None):
        from app.models.user import Role
        from app.models.user import Permission
        import re
        from werkzeug.security import generate_password_hash
        import uuid
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        if Organization.query.filter_by(slug=slug).first():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"
        org = Organization(name=name, slug=slug)
        db.session.add(org)
        db.session.flush()  # get org.id
        admin_role = Role.query.filter_by(name="Administrator").first()
        if not admin_role:
            admin_role = Role(name="Administrator", permissions=Permission.ADMINISTER)
            db.session.add(admin_role)
            db.session.flush()
        user = User(
            email=admin_email,
            password_hash=generate_password_hash(admin_password) if admin_password else None,
            first_name=admin_first_name,
            last_name=admin_last_name,
            organization_id=org.id,
            confirmed=True
        )
        # The user owns the organisation just created for them, so this is
        # always a grant in their own organisation.
        user.grant_org_admin()
        db.session.add(user)
        db.session.flush()  # get user.id
        from app.models.org_role import OrgRole
        OrgRole.set_role(org.id, user.id, 'org_admin', granted_by_id=user.id)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise
        return org, user

    @staticmethod
    def invite_member(org_id, email, inviter_id):
        from app.models.user import Role
        from app.models.user import Permission
        member_role = Role.query.filter_by(name="User").first()
        if not member_role:
            member_role = Role(name="User", permissions=Permission.GENERAL)
            db.session.add(member_role)
            db.session.flush()
        user = User(
            email=email,
            role=member_role,
            organization_id=org_id,
            is_org_admin=False,
            confirmed=False,
            status="invited"
        )
        db.session.add(user)
        db.session.commit()
        # Email sending stub
        print(f"Invite email sent to {email} for org {org_id}")
        return user

    @staticmethod
    def get_org_usage(org_id):
        users = User.query.filter_by(organization_id=org_id).count()
        # For apps and solutions, use 0 as stub (replace with real queries if models exist)
        return {"users": users, "apps": 0, "solutions": 0}
