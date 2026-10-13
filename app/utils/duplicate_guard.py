"""Pre-write duplicate detection for named entities (ARCH-030).

The August 2026 QA sweep found the repository 46% duplicated at 145 ArchiMate
elements — 25 duplicate-name groups covering 67 rows, many triplicated — plus
four near-identical Solutions and an ``/applications/create`` endpoint that
returned ``201 Created`` three times in a row for a byte-identical name. There
was no duplicate check anywhere on the write path, and the AI agent creates
entities autonomously, so the duplication accrues at machine speed.

This module supplies the check. It is deliberately *application*-level rather
than a database ``UNIQUE`` constraint:

* ``flask reconcile-schema`` is ADD-COLUMN-only and cannot add a constraint, and
  deploys do not run ``flask db upgrade`` (see CLAUDE.md, "Schema management").
* 67 duplicate rows already exist in production, so a unique index would fail to
  build against the live data.

Tenancy
-------
Models inheriting ``TenantMixin`` already get ``WHERE organization_id =
g.current_org_id`` injected by ``do_orm_execute``, so the queries here add **no**
organization predicate for them — doing so would double-filter. For models that
are tenant-owned but not mixed in, pass ``organization_id=`` explicitly. For
genuinely global reference data (``VendorOrganization``) pass nothing.
"""

from __future__ import annotations

from typing import Any

from flask import jsonify

__all__ = [
    "normalize_name",
    "find_duplicate_by_name",
    "folded_column",
    "lock_name_for_write",
    "duplicate_conflict_response",
    "allow_duplicate_requested",
    "bulk_partition_new_vs_duplicate",
    "find_similar_entities",
]


def normalize_name(value: Any) -> str:
    """Casefold and collapse whitespace so 'HxGN  EAM ' == 'hxgn eam'."""
    if value is None:
        return ""
    return " ".join(str(value).split()).casefold()


def folded_column(column):
    """SQL twin of ``normalize_name``: collapse whitespace, trim, lowercase.

    The one folding expression for name and code comparisons in SQL, so the
    preview, the 409 and the code lookup agree on what "the same" means.
    """
    from app import db

    return db.func.lower(
        db.func.btrim(db.func.regexp_replace(column, r"\s+", " ", "g"))
    )


def find_duplicate_by_name(
    model,
    name: Any,
    *,
    name_field: str = "name",
    organization_id: int | None = None,
    extra_filters: list | None = None,
):
    """Return an existing row whose name matches ``name``, or ``None``.

    Matching is case-insensitive and whitespace-normalised, done in SQL so it
    uses an index scan rather than loading the table.

    Args:
        model: the mapped class to search.
        name: the candidate name.
        name_field: column holding the name (``title`` on some models).
        organization_id: pass ONLY for tenant-owned models that do **not**
            inherit ``TenantMixin``; mixed-in models are already filtered and
            passing this double-filters.
        extra_filters: further SQLAlchemy criteria to narrow the match, e.g.
            ``[ArchiMateElement.element_type == "ApplicationService"]``.

    Returns:
        The first matching instance, or ``None`` when the name is blank or no
        match exists.
    """
    normalized = normalize_name(name)
    if not normalized:
        return None


    column = getattr(model, name_field)
    query = model.query.filter(folded_column(column) == normalized)

    if organization_id is not None and hasattr(model, "organization_id"):
        # Only apply it where the middleware has not already — otherwise this is
        # the double-filter CLAUDE.md warns about. Outside a request context
        # (agent runner, CLI, scheduler) there is no g.current_org_id and the
        # explicit predicate is the *only* thing scoping the lookup, so a
        # missing one would silently compare names across every tenant.
        from flask import g, has_request_context

        already_scoped = has_request_context() and getattr(g, "current_org_id", None)
        if not already_scoped:
            query = query.filter(model.organization_id == organization_id)

    for criterion in extra_filters or []:
        query = query.filter(criterion)

    return query.first()



def lock_name_for_write(model, name: Any, *, organization_id: int | None = None) -> bool:
    """Serialise concurrent creates of the same name in the same tenant.

    ``find_duplicate_by_name`` is a check, and a check followed by an insert is
    a TOCTOU race. Verified on ``POST /applications/create``: sequential repeats
    correctly redirected to the first row, but five *simultaneous* identical
    posts created five rows and four created three — a double-clicked Save
    duplicating an application in a system of record.

    Why a lock and not a UNIQUE index. A unique index is the stronger
    guarantee and would be the first choice on a greenfield schema, but this
    schema cannot get one safely: ``flask reconcile-schema`` is ADD-COLUMN-only
    and deploys do not run ``flask db upgrade`` (CLAUDE.md, "Schema
    management"), so the index would have to be created out of band; and it
    would fail to build wherever duplicate rows already exist — which they do,
    and which the product deliberately permits via ``allow_duplicate``. An
    index cannot express "unique unless the caller opted in", so adopting one
    would silently delete that feature. A transaction-scoped advisory lock
    closes the race without a schema change, without a build that can fail on
    live data, and without removing the opt-out.

    Taken immediately BEFORE the duplicate check and held to the end of the
    transaction by Postgres, so the whole check-then-insert is serialised per
    (table, tenant, folded name). It blocks only writers of the identical name;
    unrelated creates are unaffected.

    Returns True when the lock was taken, False when the backend does not
    support advisory locks (non-PostgreSQL). Never raises: failing to lock must
    not turn a working create into a 500 — it degrades to the pre-existing
    application-level check.
    """
    from hashlib import blake2b

    from app import db

    normalized = normalize_name(name)
    if not normalized:
        return False

    if organization_id is None:
        try:
            from flask import g, has_request_context

            if has_request_context():
                organization_id = getattr(g, "current_org_id", None)
        except Exception:  # noqa: BLE001 - locking is best-effort
            organization_id = None

    table = getattr(model, "__tablename__", model.__name__)
    digest = blake2b(
        f"{table}:{organization_id}:{normalized}".encode("utf-8"), digest_size=8
    ).digest()
    # pg_advisory_xact_lock takes a signed bigint.
    key = int.from_bytes(digest, "big", signed=True)

    try:
        if db.session.bind is not None and db.session.bind.dialect.name != "postgresql":
            return False
        db.session.execute(db.text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
        return True
    except Exception:  # noqa: BLE001 - see docstring
        import logging

        logging.getLogger(__name__).warning(
            "Advisory lock for %s/%s unavailable; falling back to the "
            "application-level duplicate check alone",
            table,
            normalized,
            exc_info=True,
        )
        return False


def duplicate_conflict_response(entity_label: str, existing, name_field: str = "name"):
    """A 409 naming the record that already exists.

    Never merges and never silently rejects: the caller — including the AI agent
    — is told exactly what it collided with so it can decide whether to reuse
    the existing record or retry with ``allow_duplicate``.
    """
    existing_name = getattr(existing, name_field, None)
    return (
        jsonify(
            {
                "success": False,
                "error": (
                    f"{entity_label} named '{existing_name}' already exists. "
                    "Reuse it, choose a different name, or resend with "
                    "allow_duplicate=true if the duplicate is intentional."
                ),
                "code": "DUPLICATE_NAME",
                "duplicate_of": {"id": existing.id, "name": existing_name},
            }
        ),
        409,
    )


def bulk_partition_new_vs_duplicate(
    model,
    rows: list,
    *,
    name_key: str = "name",
    name_field: str = "name",
    organization_id: int | None = None,
    extra_filters: list | None = None,
):
    """ARCH-030(ii): merge-or-skip partitioning for bulk importers/connectors.

    Unlike ``find_duplicate_by_name`` (one row, used interactively -> 409),
    a bulk job processes hundreds of rows and a 409 would abort the whole
    batch on the first collision. This instead partitions the candidate rows
    into ``new`` (safe to insert) and ``skipped`` (normalized name already
    exists — in the DB *or* earlier in this same batch), using the identical
    ``normalize_name`` matching as the interactive guard so a row that would
    409 interactively is the same row that gets skipped here.

    Default policy is skip, never overwrite: a bulk job should not silently
    mutate a row it did not intend to touch. Callers that want upsert
    semantics should look the existing row up themselves (as several CSV
    importers already do) — this helper only tells you what's safe to create.

    Args:
        model: mapped class to check against.
        rows: list of dicts, each a would-be row's fields.
        name_key: key in each row dict holding the candidate name.
        name_field: column name on ``model`` holding the name.
        organization_id / extra_filters: see ``find_duplicate_by_name``.

    Returns:
        {
          "new": [rows safe to insert, in input order],
          "skipped": [{"row": row, "reason": str, "duplicate_of": id|None}],
        }
    """
    new_rows: list = []
    skipped: list = []
    seen_in_batch: dict[str, int] = {}

    for row in rows:
        candidate_name = row.get(name_key) if isinstance(row, dict) else getattr(row, name_key, None)
        normalized = normalize_name(candidate_name)

        if not normalized:
            skipped.append({"row": row, "reason": "missing_name", "duplicate_of": None})
            continue

        if normalized in seen_in_batch:
            skipped.append(
                {
                    "row": row,
                    "reason": "duplicate_within_batch",
                    "duplicate_of": None,
                }
            )
            continue

        existing = find_duplicate_by_name(
            model,
            candidate_name,
            name_field=name_field,
            organization_id=organization_id,
            extra_filters=extra_filters,
        )
        if existing is not None:
            skipped.append(
                {
                    "row": row,
                    "reason": "duplicate_of_existing_row",
                    "duplicate_of": existing.id,
                }
            )
            continue

        seen_in_batch[normalized] = 1
        new_rows.append(row)

    return {"new": new_rows, "skipped": skipped}


def find_similar_entities(
    model,
    name: Any,
    *,
    name_field: str = "name",
    organization_id: int | None = None,
    extra_filters: list | None = None,
    threshold: float = 0.72,
    limit: int = 5,
    max_candidates: int = 500,
):
    """S-06: near-duplicate advisory for the write path — moves the existing
    post-hoc fuzzy detector onto create, instead of only ever running after
    the fact.

    Reuses ``SimpleDuplicateService._calculate_name_similarity`` (the exact
    function ``app/services/simple_duplicate_service.py``'s batch detector
    already uses) rather than building a second one — same scoring, just
    called before the write commits instead of in a scheduled sweep.

    This is deliberately advisory, not a 409: composes with
    ``find_duplicate_by_name``'s exact-match 409 for the "same name" case;
    this covers "different name, same thing" (typos, reordering, partial
    matches) where blocking the write would be wrong — the caller decides
    whether to proceed having been shown what's similar.

    Returns a list of ``{"id", "name", "score"}`` for rows scoring >=
    ``threshold``, sorted by score descending, capped at ``limit``. Empty
    list when the name is blank or nothing scores high enough — never raises,
    so a caller can always do ``if similar: ...`` without a try/except.
    """
    if not str(name or "").strip():
        return []

    try:
        from app.services.simple_duplicate_service import SimpleDuplicateService

        query = model.query
        if organization_id is not None and hasattr(model, "organization_id"):
            from flask import g, has_request_context

            already_scoped = has_request_context() and getattr(g, "current_org_id", None)
            if not already_scoped:
                query = query.filter(model.organization_id == organization_id)
        for criterion in extra_filters or []:
            query = query.filter(criterion)

        candidates = query.limit(max_candidates).all()
        scored = []
        for row in candidates:
            other_name = getattr(row, name_field, None)
            score = SimpleDuplicateService._calculate_name_similarity(str(name), other_name)
            if score >= threshold and score < 1.0:  # 1.0 (exact) is duplicate_guard's job, not this one's
                scored.append({"id": row.id, "name": other_name, "score": round(score, 3)})

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:limit]
    except Exception:
        # Advisory only -- a failure here must never block or fail the write.
        return []


_TRUTHY = {"1", "true", "yes", "on"}


def allow_duplicate_requested(data=None) -> bool:
    """True when the caller explicitly opted into creating a duplicate.

    Honours ``?allow_duplicate=true`` on the query string, the same key in a
    JSON or form body, and the ``X-Allow-Duplicate`` header. Some organisations
    genuinely run two systems with the same name in different domains, so the
    escape hatch has to exist — it just has to be deliberate.
    """
    from flask import has_request_context, request

    if isinstance(data, dict):
        raw = data.get("allow_duplicate")
        if isinstance(raw, bool):
            if raw:
                return True
        elif raw is not None and str(raw).strip().lower() in _TRUTHY:
            return True
    elif data is not None:
        raw = data.get("allow_duplicate")
        if raw is not None and str(raw).strip().lower() in _TRUTHY:
            return True

    if not has_request_context():
        return False

    if str(request.args.get("allow_duplicate", "")).strip().lower() in _TRUTHY:
        return True
    if str(request.headers.get("X-Allow-Duplicate", "")).strip().lower() in _TRUTHY:
        return True
    return False
