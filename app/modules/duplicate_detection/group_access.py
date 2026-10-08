"""Organisation check for duplicate-group status actions.

The group tables carry no organisation column, so a group belongs to the
organisation(s) of its member applications. Shared by the v1 and v2 routes.
"""

from flask import g
from flask_login import current_user
from sqlalchemy import select

from app import db
from app.middleware.tenant_decorators import is_platform_admin
from app.models.application_duplicate_detection import duplicate_group_members
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_duplicate_detection import UnifiedDuplicateGroup, unified_group_members


def group_visible_to_caller(group):
    """True for a platform admin, or when every member application of the
    group is visible to the caller's organisation. A group with no members
    cannot be shown to be the caller's, so it is refused."""
    if is_platform_admin(current_user):
        return True
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return False
    if isinstance(group, UnifiedDuplicateGroup):
        stmt = select(unified_group_members.c.application_id).where(
            unified_group_members.c.group_id == group.id
        )
    else:
        stmt = select(duplicate_group_members.c.application_id).where(
            duplicate_group_members.c.duplicate_group_id == group.id
        )
    member_ids = {row[0] for row in db.session.execute(stmt)}  # tenant-filtered: scoped via parent FK
    if not member_ids:
        return False
    visible = ApplicationComponent.query.filter(
        ApplicationComponent.id.in_(member_ids),
        ApplicationComponent.organization_id == org_id,
    ).count()
    return visible == len(member_ids)
