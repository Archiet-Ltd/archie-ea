"""Inbound API-key authentication for the Ifiok verifier.

This is the ONLY header-credential path in the application. A request presents
``Authorization: Bearer <secret>``. We hash it, look up an active VerifierApiKey
and — critically — set ``g.current_org_id`` to the key's organisation so the
existing tenant-isolation middleware (the ``do_orm_execute`` read filter, the
``before_flush`` auto-stamp, and the PostgreSQL row-level-security context) scopes
EVERY query and insert to that one organisation.

Without that line a session-less request leaves ``g.current_org_id`` as ``None``
and the tenant filter is a no-op — reads and writes would run unscoped across
every organisation (see app/middleware/tenant_isolation.py). That is the single
most important line in this file; the isolation tests assert it.

Decorators:
- ``@api_key_auth``   authenticate the key and engage its org (401 if absent/invalid)
- ``@require_scope``  require a scope on the key (403 otherwise)
- ``@require_org``    belt-and-suspenders: reject a key not bound to a given org (403)
"""

from datetime import datetime
from functools import wraps

from flask import g, jsonify, request

from app.extensions import db
from app.middleware.tenant_isolation import set_database_tenant_context


def _unauthorized(message):
    return jsonify({"error": "Unauthorized", "message": message}), 401


def _forbidden(message):
    return jsonify({"error": "Forbidden", "message": message}), 403


def _extract_bearer():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        return token or None
    return None


def api_key_auth(f):
    """Authenticate a VerifierApiKey and engage its organisation's tenant scope."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        from app.models.verifier_api_key import VerifierApiKey

        secret = _extract_bearer()
        if not secret:
            return _unauthorized("A Bearer API key is required for this endpoint")

        key = VerifierApiKey.query.filter_by(
            key_hash=VerifierApiKey.hash_secret(secret)
        ).first()
        if key is None or not key.is_active:
            return _unauthorized("Invalid or revoked API key")

        # CRITICAL: make this key request behave exactly like a session user in
        # the key's organisation, so every TenantMixin read/write is scoped and
        # new rows are stamped with this org. Never remove this.
        g.current_org_id = key.organization_id
        g.current_org = None
        g.api_key = key
        g.api_scopes = key.scope_list
        set_database_tenant_context(db.session.connection(), key.organization_id)

        # Best-effort last-used stamp; persisted with the route's own commit.
        key.last_used_at = datetime.utcnow()

        return f(*args, **kwargs)

    return wrapper


def require_scope(scope):
    """Require that the authenticated key carries ``scope`` (use after @api_key_auth)."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if scope not in getattr(g, "api_scopes", []):
                return _forbidden(f'This API key lacks the "{scope}" scope')
            return f(*args, **kwargs)

        return wrapper

    return decorator


def require_org(organization_id):
    """Hard-reject a key not bound to ``organization_id`` (defence in depth)."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if getattr(g, "current_org_id", None) != organization_id:
                return _forbidden("This API key is not scoped to the required organisation")
            return f(*args, **kwargs)

        return wrapper

    return decorator
