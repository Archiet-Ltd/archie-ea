"""Tenant scoping for raw SQL.

Multi-tenancy is enforced by a ``do_orm_execute`` listener that rewrites ORM
statements (``app/middleware/tenant_isolation.py``). A ``db.text(...)`` statement
is not an ORM statement: it reaches PostgreSQL exactly as written, with no
``organization_id`` predicate, and returns every organisation's rows.

Two hand-rolled copies of this helper already existed — ``_org_scope`` in
``app/main/business_capability_management_routes.py`` and ``_org_filter`` in
``app/services/architecture_rag_service.py``. This is the shared one, so the
next caller finds it instead of writing a third.

Semantics, deliberately identical to the ORM listener's:

* Inside a tenant request, emit ``AND <prefix>organization_id = :org_id``.
* Callers reading shared-reference rows can opt into
  ``AND (<prefix>organization_id = :org_id OR <prefix>organization_id IS NULL)``.
* With no tenant context — CLI, scheduler, importers, unauthenticated — emit
  nothing by default, leaving the query global. Callers that must fail closed
  can opt into ``AND 1=0`` / ``WHERE 1=0``.

Because system contexts are unscoped, a service that runs in one and handles
more than one organisation must pass ``org_id`` explicitly rather than relying
on the fallback. ``organization_id_of`` covers the common case where the
organisation is already known from a loaded row.

Naming matters: ``scripts/check_raw_sql_tenancy.py`` recognises an interpolated
clause only when the variable name contains "org", so bind the result to
``_org_clause`` / ``_org_params`` (or another name containing "org") at the call
site.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

__all__ = ["current_org_id", "org_scope", "organization_id_of"]


def current_org_id() -> Optional[int]:
    """The active tenant's id, or None outside a tenant request.

    Safe to call with no application context at all — ``flask.g`` raises
    ``RuntimeError`` (not ``AttributeError``) when unbound, so ``getattr`` with
    a default does not protect against it.
    """
    from flask import g, has_app_context

    if not has_app_context():
        return None
    return getattr(g, "current_org_id", None)


def org_scope(
    prefix: str = "",
    keyword: str = "AND",
    org_id: Optional[int] = None,
    *,
    include_shared: bool = False,
    fail_closed: bool = False,
    param_name: str = "org_id",
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(clause, params)`` scoping a raw-SQL statement to one tenant.

    Args:
        prefix: table alias including the dot, e.g. ``"ae."``. Required whenever
            the statement joins more than one table carrying ``organization_id``,
            or PostgreSQL raises "column reference is ambiguous".
        keyword: ``"AND"`` when the statement already has a WHERE, ``"WHERE"``
            when it does not.
        org_id: scope to this organisation instead of the request's. Callers
            that run outside a request, or that iterate over organisations, must
            pass it — see the module docstring.
        include_shared: also include rows whose ``organization_id`` is NULL.
        fail_closed: return ``1=0`` when no organisation is known.
        param_name: bind-parameter name to use for the organisation id.

    Returns:
        ``(" AND ae.organization_id = :org_id", {"org_id": 7})``, or
        ``(" AND (ae.organization_id = :org_id OR ae.organization_id IS NULL)",
        {"org_id": 7})``, or ``("", {})`` when no organisation is known.
    """
    org = org_id if org_id is not None else current_org_id()
    if org is None:
        if fail_closed:
            return f" {keyword} 1=0", {}
        return "", {}
    column = f"{prefix}organization_id"
    if include_shared:
        return (
            f" {keyword} ({column} = :{param_name} OR {column} IS NULL)",
            {param_name: org},
        )
    return f" {keyword} {column} = :{param_name}", {param_name: org}


def organization_id_of(row: Any) -> Optional[int]:
    """The ``organization_id`` of an already-loaded row, or None.

    The most reliable scope for a service that runs under both a request and the
    CLI: the organisation comes from the entity being worked on rather than from
    ambient request state, so the two paths behave identically.
    """
    return getattr(row, "organization_id", None)
