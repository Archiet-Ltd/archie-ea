"""Value streams at risk and Traceability: the pages of the intelligence
module's user interface. The old Ask address redirects into the AI chat.

Value streams at risk only renders a page shell. Its data
is fetched in the browser from endpoints that already exist: the
value-streams-at-risk answer from the intelligence API, so that page carries
no query of its own and no way to read another tenant's rows. Traceability
renders its answer on the server from the traceability check service, which
reads the same tenant-fenced walk as the impact answer.

The provenance drawer is not a route. It opens over those pages.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import db

intelligence_ui = Blueprint(
    "intelligence_ui",
    __name__,
    url_prefix="/intelligence",
    template_folder="../templates",
)


def _workspace_counts_available() -> bool:
    """Whether the shell's per-tenant counts can be read right now.

    Both pages show a "nothing is modelled yet" state only when those counts say
    the workspace is empty. The shell's own context processor answers a failed
    read with counts of zero, which is indistinguishable from an empty
    workspace, so this asks the same function directly and reports
    whether it worked. An unreadable count is never treated as an empty
    workspace.
    """
    if getattr(g, "current_org_id", None) is None:
        return False

    try:
        from app._bootstrap.context_processors import compute_nav_counts

        compute_nav_counts(getattr(g, "current_org_id", None))
        return True
    except Exception:
        current_app.logger.warning("[MODULE] intelligence: workspace counts unavailable")
        return False


def _workspace_is_empty() -> bool | None:
    """Whether the current organisation has no tenant-scoped modelled records.

    The sidebar's vendor count is global rather than tenant-scoped, so it is
    useful for navigation but not for deciding whether one organisation has an
    empty workspace. Ask and Twin map only gate on the tenant-scoped counts.
    ``None`` means the counts could not be read and the caller must not treat
    the workspace as empty.
    """
    try:
        from app._bootstrap.context_processors import compute_nav_counts

        counts = compute_nav_counts(getattr(g, "current_org_id", None))
    except Exception:
        current_app.logger.warning("[MODULE] intelligence: workspace counts unavailable")
        return None
    return (
        (counts.get("applications", 0) or 0)
        + (counts.get("elements", 0) or 0)
        + (counts.get("capabilities", 0) or 0)
    ) == 0


@intelligence_ui.route("/ask", methods=["GET"])
@login_required
def ask():
    """Old address of the Ask page; questions are asked in the AI chat.

    Bookmarks and old links keep working: ``q`` (optional) goes across to the
    chat's question box, so the person lands where they can ask it.
    """
    question = (request.args.get("q") or "").strip()[:2000]
    if question:
        return redirect(url_for("unified_ai_chat.index", q=question), code=302)
    return redirect(url_for("unified_ai_chat.index"), code=302)


def _most_connected_element_id():
    """The id of the element with the most relationships in the signed-in
    organisation (lowest id on a tie), or None when nothing is connected.

    Both queries run through the tenant fence, so another organisation's
    elements and links are never counted.
    """
    from sqlalchemy import func, union_all  # noqa: PLC0415

    from app.models import ArchiMateElement, ArchiMateRelationship  # noqa: PLC0415

    ends = union_all(
        db.select(ArchiMateRelationship.source_id.label("element_id")),
        db.select(ArchiMateRelationship.target_id.label("element_id")),
    ).subquery()
    row = db.session.execute(
        db.select(ArchiMateElement.id, func.count().label("links"))
        .join(ends, ends.c.element_id == ArchiMateElement.id)
        .group_by(ArchiMateElement.id)
        .order_by(func.count().desc(), ArchiMateElement.id)
        .limit(1)
    ).first()
    return row[0] if row else None


@intelligence_ui.route("/twin-map", methods=["GET"])
@login_required
def twin_map():
    """Old address of the twin map; the ArchiMate Composer now draws it.

    The twin map drew what one element connects to, which is the Composer's
    focus mode. Bookmarks and old links keep working: ``element`` (optional)
    goes across to ``/archimate/composer?element=<id>``. With none, the
    Composer opens on the organisation's most connected element, and says so
    (``most_connected=1``); an organisation with nothing connected gets the
    Composer's own start page. A value that is not a whole number is ignored.
    The Composer applies the organisation fence, so another organisation's
    element answers 404 there.
    """
    element_id = request.args.get("element", type=int)
    if element_id is not None:
        return redirect(url_for("archimate.composer_page", element=element_id), code=302)
    try:
        element_id = _most_connected_element_id()
    except Exception:
        current_app.logger.warning("[MODULE] intelligence: most connected element unavailable")
        element_id = None
    if element_id is None:
        return redirect(url_for("archimate.composer_page"), code=302)
    return redirect(url_for("archimate.composer_page", element=element_id, most_connected=1), code=302)


# The maturity threshold the value-streams-at-risk answer is asked for. The
# page shell opens on the same bounds and default as the API route that answers
# it; the browser then canonicalises the address bar to that same allowed
# threshold set.
VALUE_STREAMS_AT_RISK_THRESHOLDS = (1, 2, 3, 4, 5)
VALUE_STREAMS_AT_RISK_DEFAULT_THRESHOLD = 3


@intelligence_ui.route("/value-streams-at-risk", methods=["GET"])
@login_required
def value_streams_at_risk():
    """Show the organisation's value streams whose capabilities fall below a
    maturity threshold.

    The rows and counts are the intelligence API's own answer
    (``GET /api/v1/intelligence/value-streams-at-risk``), fetched in the
    browser; nothing is computed here. ``threshold`` (optional, 1 to 5,
    default 3) is the value the page opens on, so a reload or a shared link
    shows the same answer.
    """
    threshold = request.args.get("threshold", type=int)
    if threshold not in VALUE_STREAMS_AT_RISK_THRESHOLDS:
        threshold = VALUE_STREAMS_AT_RISK_DEFAULT_THRESHOLD
    return render_template(
        "intelligence/value_streams_at_risk.html",
        threshold=threshold,
        thresholds=VALUE_STREAMS_AT_RISK_THRESHOLDS,
    )


@intelligence_ui.route("/traceability", methods=["GET"])
@login_required
def traceability():
    """Check one element's chains up to a capability and down to technology.

    ``element`` (optional) is the id of the element to check. The answer is
    rendered on the server from ``TraceabilityCheckService.check``, which
    reads the same tenant-fenced walk as the impact answer; an id outside the
    caller's organisation renders the same "not found" state as a missing one.
    Candidate relationships are added through the existing relationship
    writer from the browser, then the page reloads and checks again.
    """
    element_id = request.args.get("element", type=int)
    result = None
    if element_id is not None:
        from app.modules.intelligence.services.traceability_check_service import (
            TraceabilityCheckService,
        )

        org_id = getattr(g, "current_org_id", None)
        if org_id is None:
            from flask_login import current_user

            org_id = getattr(current_user, "organization_id", None)
        result = TraceabilityCheckService.check(element_id, org_id)
    return render_template(
        "intelligence/traceability.html",
        element_id=element_id,
        result=result,
        workspace_counts_available=_workspace_counts_available(),
    )


__all__ = ["intelligence_ui"]
