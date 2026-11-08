"""The early-access pages: what a closed address answers, a hub of what is coming, and the waiting list."""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, abort, make_response, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.release_waves import EARLY_ACCESS_PREFIX, code_slug, group_by_slug, has_page, legacy_slug, moved_slug, wave_map
from app.services.rate_limiter import rate_limit

early_access_bp = Blueprint("early_access", __name__)

CONSENT_TEXT = "Used to tell you when this becomes available, and for nothing else."


def coming_groups() -> list[dict]:
    """The capabilities that open in a later release, once each, in alphabetical order.

    A capability with a page open in the first release is not listed as coming: the person can already use it."""
    doc = wave_map()
    open_now = {entry[1] for entry in doc["rules"].values() if entry[0] == "W1"}
    seen: dict[str, dict] = {}
    for entry in doc["rules"].values():
        wave, code = entry[0], entry[1]
        if code in open_now or wave in doc["pass_waves"] or wave in ("W1", "X", "RT") or code.startswith("BP:") or code in ("infra", "PG-RT", "PG-RTP", "PG-NEW", "PG-EA", "PG-OPS"):
            continue
        info = doc["groups"].get(code)
        if has_page(info) and info["title"] not in seen:
            seen[info["title"]] = {"slug": code_slug(code), "title": info["title"], "blurb": info["blurb"]}
    return [seen[k] for k in sorted(seen)]


def _page(slug: str, *, thanks: bool = False, error: str | None = None, title: str | None = None, blurb: str | None = None):
    found = group_by_slug(slug)
    if found is None:
        abort(404)
    _code, info = found
    if title:
        info = {**info, "title": title}
    if blurb:
        info = {**info, "blurb": blurb}
    out = make_response(render_template(
        "public/early_access.html", group_slug=slug, group=info, thanks=thanks, error=error,
        form_action=url_for("early_access.join", slug=slug),
    ))
    out.headers["X-Robots-Tag"] = "noindex"
    return out


def early_access_page(slug: str, *, title: str | None = None, blurb: str | None = None):
    """The early-access page for a capability group, optionally under the name and words of the page that led here."""
    return _page(slug, title=title, blurb=blurb)


@early_access_bp.route("/opportunities")
def opportunities():
    """The ranked opportunities for a signed-in person; the waiting-list page for a visitor."""
    if current_user.is_authenticated:
        return opportunities_page()
    return early_access_page(code_slug("PG-OPP"))


def opportunities_page():
    """The organisation's top 50 opportunities, each with its reason and the records it rests on."""
    from flask import current_app

    from app.decorators.requires_role import may_see_spend_pages
    from app.services import opportunity_service
    from app.utils.tenant import current_organization_id

    org_id = current_organization_id()
    if org_id is None:
        return render_template("opportunities/index.html", items=[], total=0, counts={}, load_error=None,
                               no_organisation=True, with_cost=False)
    # the one cost rule, the same question run_cost_line asks
    with_cost = may_see_spend_pages(current_user)
    try:
        found = opportunity_service.ranked_opportunities(org_id, with_cost=with_cost)
        error = None
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("Opportunities could not be worked out")
        found, error = {"items": [], "total": 0, "counts": {}}, "The opportunities could not be worked out."
    return render_template(
        "opportunities/index.html", items=found["items"], total=found["total"], load_error=error,
        counts=found["counts"], with_cost=with_cost, no_organisation=False,
    )


@early_access_bp.route("/recommendations")
def recommendations():
    """A signed-in person reads the recommendations drawn from their own model; a visitor gets the waiting list."""
    if not current_user.is_authenticated:
        return early_access_page(code_slug("PG-REC"))
    from app.middleware.tenant_context import current_org_id
    from app.services import recommendation_service

    org_id = current_org_id()
    if org_id is None:
        abort(403)
    return render_template("enterprise/recommendations.html", result=recommendation_service.recommendations(org_id))


@early_access_bp.route("/simulations")
def simulations():
    """A signed-in person gets the simulation screen; a visitor, or any wave that keeps it closed, the waiting list."""
    from flask_login import current_user

    from app.release_waves import is_released_endpoint

    if current_user.is_authenticated and is_released_endpoint("strategic.api_simulation"):
        from app.modules.solutions_strategic.v2.routes.strategic_routes import simulations_page

        return simulations_page()
    return early_access_page(code_slug("PG-SIM"))


@early_access_bp.route("/early-access")
def hub():
    return render_template("public/early_access_hub.html", groups=coming_groups())


@early_access_bp.route("/early-access/<slug>")
def page(slug):
    if group_by_slug(slug) is None:
        # an address an earlier release served: it moves permanently to the page that now carries the capability
        target = moved_slug(slug)
        if target is not None:
            return redirect(target, code=301)
    return _page(slug)


@early_access_bp.route("/early-access/<slug>/join", methods=["POST"])
@rate_limit(10, "1m", methods=("POST",))
def join(slug):
    from app.models.product_inquiry import ProductInquiry

    found = group_by_slug(slug)
    if found is None:
        # a form posted from an earlier address joins the list of the page that address moved to
        target = moved_slug(slug) or ""
        found = group_by_slug(target.rsplit("/", 1)[-1]) if target.startswith(EARLY_ACCESS_PREFIX + "/") else None
        if found is None:
            abort(404)
        slug = code_slug(found[0])
    email = (request.form.get("email") or "").strip().lower()
    if not email:
        return _page(slug, error="Please enter an email address.")
    if not request.form.get("consent"):
        return _page(slug, error="Please agree that we may email you about this.")
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        return _page(slug, error="Please enter a valid email address.")
    # one record per group, whichever form of the address the person joined from
    offer = f"early-access:{legacy_slug(found[0])}"[:50]
    if ProductInquiry.query.filter_by(email=email, offer=offer).first() is None:
        db.session.add(ProductInquiry(email=email, name=None, offer=offer, consent_text=CONSENT_TEXT))
        db.session.commit()
    return _page(slug, thanks=True)
