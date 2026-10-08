"""Ask, Twin map, Value streams at risk and Traceability: the pages of the
intelligence module's user interface.

Ask, Twin map and Value streams at risk only render a page shell. Their data
is fetched in the browser from endpoints that already exist: element search
from the ArchiMate picker endpoint, the impact answer and the
value-streams-at-risk answer from the intelligence API, so those pages carry
no query of their own and no way to read another tenant's rows. Traceability
renders its answer on the server from the traceability check service, which
reads the same tenant-fenced walk as the impact answer.

The provenance drawer is not a route. It opens over either page.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, render_template, request
from flask_login import login_required

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


def _derivation_status():
    """The tenant's worked-out connection state for the Ask page, or ``None``
    when it could not be read (the page then says so rather than showing
    counts of zero)."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        return None
    try:
        from app.modules.intelligence.services.derived_facts import derivation_status

        return derivation_status(int(org_id))
    except Exception:
        current_app.logger.warning("[MODULE] intelligence: derivation status unavailable")
        return None


@intelligence_ui.route("/ask", methods=["GET"])
@login_required
def ask():
    """Ask a plain business question about what depends on a chosen element."""
    workspace_is_empty = _workspace_is_empty()
    return render_template(
        "intelligence/ask.html",
        workspace_counts_available=workspace_is_empty is not None,
        workspace_is_empty=workspace_is_empty,
        derivation_status=_derivation_status(),
    )


@intelligence_ui.route("/twin-map", methods=["GET"])
@login_required
def twin_map():
    """Show what a chosen element connects to, banded by architecture layer.

    ``element`` (optional) is the id of the element to centre the map on, so a
    row on the Ask page can hand its element straight across. A value that is
    not a whole number is ignored and the page opens on its picker.
    """
    initial_element_id = request.args.get("element", type=int)
    workspace_is_empty = _workspace_is_empty()
    return render_template(
        "intelligence/twin_map.html",
        initial_element_id=initial_element_id,
        workspace_counts_available=workspace_is_empty is not None,
        workspace_is_empty=workspace_is_empty,
    )


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
