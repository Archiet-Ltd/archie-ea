"""
Tenant context middleware — sets g.current_org_id on every request.

Reads the authenticated user's active organisation choice from the session,
validates it against OrgRole membership, and falls back to the user's home
organisation when no valid choice exists.

For unauthenticated requests, g.current_org_id stays None and the event
listener becomes a no-op (all data visible — used for login page, health
check, public APIs).
"""

import logging
import re

from flask import g, has_app_context, has_request_context, session
from flask_login import current_user

from app.utils.validators import sanitize_filename

logger = logging.getLogger(__name__)

ACTIVE_ORG_SESSION_KEY = "current_org_id"


def _session_org_id():
    if not has_request_context():
        return None
    raw = session.get(ACTIVE_ORG_SESSION_KEY)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        session.pop(ACTIVE_ORG_SESSION_KEY, None)
        return None


def user_can_access_org(user, org_id):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        org_id = int(org_id)
    except (TypeError, ValueError):
        return False
    if getattr(user, "organization_id", None) == org_id:
        return True

    from app.models.org_role import OrgRole

    return (
        OrgRole.query.filter_by(user_id=user.id, organization_id=org_id).first()
        is not None
    )


def accessible_organizations(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return []

    from app.models.org_role import OrgRole
    from app.models.organization import Organization

    org_ids = []
    home_org_id = getattr(user, "organization_id", None)
    if home_org_id is not None:
        org_ids.append(home_org_id)

    extra_org_ids = [
        row.organization_id
        for row in OrgRole.query.filter_by(user_id=user.id)
        .order_by(OrgRole.created_at.asc(), OrgRole.id.asc())
        .all()
    ]
    for org_id in extra_org_ids:
        if org_id not in org_ids:
            org_ids.append(org_id)

    if not org_ids:
        return []

    orgs = {
        org.id: org
        for org in Organization.query.filter(Organization.id.in_(org_ids))
        .order_by(Organization.name.asc())
        .all()
    }
    return [orgs[org_id] for org_id in org_ids if org_id in orgs]


def _resolve_active_organization():
    if not has_request_context() or not current_user.is_authenticated:
        return None, None

    from app.extensions import db
    from app.models.organization import Organization

    explicit_org_id = _session_org_id()
    if explicit_org_id is not None and user_can_access_org(current_user, explicit_org_id):
        return explicit_org_id, db.session.get(Organization, explicit_org_id)

    if explicit_org_id is not None:
        session.pop(ACTIVE_ORG_SESSION_KEY, None)

    home_org_id = getattr(current_user, "organization_id", None)
    home_org = getattr(current_user, "organization", None)
    if home_org_id is not None and (home_org is None or getattr(home_org, "id", None) != home_org_id):
        home_org = db.session.get(Organization, home_org_id)
    return home_org_id, home_org


def current_org():
    if has_app_context() and hasattr(g, "current_org"):
        return g.current_org
    org_id = current_org_id()
    if not has_app_context():
        return None
    if not hasattr(g, "current_org") and org_id is None:
        g.current_org = None
    return getattr(g, "current_org", None)


def clear_tenant_context_cache():
    if has_app_context():
        for attr in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, attr):
                delattr(g, attr)

    from app.extensions import db

    db.session.remove()


def current_org_id():
    """The active tenant's organization_id, or None outside a tenant request
    (CLI / system tasks / unauthenticated).

    Raw-SQL callers use this to scope queries the SAME way the ORM auto-filter
    does: apply ``organization_id = :org`` only when this is not None. In system
    contexts it is None, so the query stays global — matching the ORM event
    listener's no-op behaviour and keeping CLI/migrations/seeders working.
    """
    if has_app_context() and hasattr(g, "current_org_id"):
        return g.current_org_id

    org_id, org = _resolve_active_organization()
    if has_app_context():
        g.current_org_id = org_id
        g.current_org = org
    return org_id


def install_tenant_context(app):
    """Register before_request handler that sets g.current_org_id."""

    @app.before_request
    def set_tenant_context():
        if current_user.is_authenticated and hasattr(current_user, "organization_id"):
            g.current_org_id = current_org_id()
            g.current_org = current_org()
            from app.extensions import db
            from app.middleware.tenant_isolation import set_database_tenant_context

            set_database_tenant_context(db.session.connection(), g.current_org_id)
        else:
            g.current_org_id = None
            g.current_org = None

    @app.after_request
    def name_export_files(response):
        content_disposition = response.headers.get("Content-Disposition", "")
        org = current_org()
        if (
            org is None
            or "attachment;" not in content_disposition.lower()
            or "filename=" not in content_disposition.lower()
        ):
            return response

        match = re.search(r'filename="?([^";]+)"?', content_disposition, flags=re.IGNORECASE)
        if match is None:
            return response

        filename = match.group(1)
        prefix = sanitize_filename(
            getattr(org, "name", None) or getattr(org, "slug", None) or "organization"
        ) or "organization"
        if filename.startswith(f"{prefix}-"):
            return response

        response.headers["Content-Disposition"] = re.sub(
            r'filename="?([^";]+)"?',
            f'filename="{prefix}-{filename}"',
            content_disposition,
            count=1,
            flags=re.IGNORECASE,
        )
        return response
