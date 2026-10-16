"""Existence guards for routes that take an entity id in the path.

Why this exists
---------------
A route that renders (or serialises) a report for an id that does not exist is
the exact failure CLAUDE.md's "never invent data" rule describes: the page shows
``0 / 100 — Incomplete`` or ``Total Applications: 0`` and the user cannot tell
that from a measured zero. The correct answer is 404.

Why not ``Model.query.get_or_404(id)``
--------------------------------------
``Query.get()`` short-circuits on an identity-map **hit** and returns the cached
object without emitting SQL, so ``do_orm_execute`` never runs and no tenant
predicate is applied (see CLAUDE.md, "Multi-tenancy is implicit"). Inside one
request that is harmless — one request is one tenant — but the helpers here
always emit a real ``SELECT ... WHERE id = :id``, which the tenant loader
criteria *does* decorate, so they are correct in a loop over tenants too.

Never widen a route with these: a row belonging to another organisation is
invisible to the query, so the caller gets 404, which is the same answer they
got before and the right one.
"""

from __future__ import annotations

from flask import abort


def load_entity(model, entity_id, *, id_column: str = "id"):
    """Return the row with ``id == entity_id``, or ``None``.

    Emits a real SELECT so tenant loader criteria apply.
    """
    if entity_id is None:
        return None
    column = getattr(model, id_column)
    try:
        return model.query.filter(column == entity_id).first()
    except Exception:  # pragma: no cover - defensive; a broken table is not a hit
        from app import db

        db.session.rollback()
        raise


def require_entity(model, entity_id, *, description: str | None = None, id_column: str = "id"):
    """Return the row, or ``abort(404)``.

    ``/api/`` paths are converted to JSON by the app-wide ``HTTPException``
    handler in ``app/_bootstrap/extensions.py``, so this is safe for both HTML
    pages and JSON endpoints under ``/api/``.
    """
    row = load_entity(model, entity_id, id_column=id_column)
    if row is None:
        abort(404, description=description or f"{model.__name__} {entity_id} not found")
    return row


def entity_exists(model, entity_id, *, id_column: str = "id") -> bool:
    """True when a row with that id is visible to the current tenant."""
    return load_entity(model, entity_id, id_column=id_column) is not None


def require_entity_json(model, entity_id, *, label: str | None = None, id_column: str = "id"):
    """Existence guard for a JSON endpoint whose path does not contain ``/api/``.

    Returns ``(row, None)`` on success and ``(None, response_tuple)`` when the
    entity is missing, so the caller can ``return response`` directly rather
    than relying on the ``/api/`` JSON conversion.
    """
    from flask import jsonify

    row = load_entity(model, entity_id, id_column=id_column)
    if row is None:
        name = label or model.__name__
        return None, (
            jsonify({
                "success": False,
                "error": f"{name} {entity_id} not found",
                "error_type": "not_found",
            }),
            404,
        )
    return row, None


# ---------------------------------------------------------------------------
# Strict active-organisation guards
# ---------------------------------------------------------------------------
#
# ``load_entity`` above relies on the automatic tenant filter. That filter is
# OFF when no active organisation is known (``g.current_org_id`` is None), so a
# route guarded only by it answers for every organisation in that case. The
# helpers below are for routes that reach a table with no organisation column
# of its own (a junction, a webhook, a bundle) through a parent record that has
# one: they name the active organisation explicitly and fail closed.


def entity_in_active_org(
    model, entity_id, *, id_column: str = "id", org_column: str = "organization_id"
) -> bool:
    """True only when the row exists AND belongs to the caller's active organisation.

    False when the active organisation is unknown (never "no filter"), when the
    id is None, and when the row belongs to another organisation. The caller
    should answer all three the same way (404) so existence is not an oracle.
    """
    return _load_in_active_org(model, entity_id, id_column, org_column) is not None


def require_entity_in_active_org(
    model,
    entity_id,
    *,
    description: str | None = None,
    id_column: str = "id",
    org_column: str = "organization_id",
):
    """Return the row in the caller's active organisation, or ``abort(404)``."""
    row = _load_in_active_org(model, entity_id, id_column, org_column)
    if row is None:
        abort(404, description=description or f"{model.__name__} {entity_id} not found")
    return row


def _load_in_active_org(model, entity_id, id_column, org_column):
    if entity_id is None:
        return None
    # Imported at call time so the active-organisation lookup is the one the
    # request middleware owns (and tests can stand in for "unknown").
    from app.middleware.tenant_context import current_org_id

    org_id = current_org_id()
    if org_id is None:
        return None
    return model.query.filter(
        getattr(model, id_column) == entity_id,
        getattr(model, org_column) == org_id,
    ).first()
