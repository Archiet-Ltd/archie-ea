from app import db
from app.models.user import User

class OrgService:
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
