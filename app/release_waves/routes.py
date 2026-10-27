"""The early-access pages: what a closed address answers, a hub of what is coming, and the waiting list."""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, abort, make_response, render_template, request, url_for

from app import db
from app.release_waves import code_slug, group_by_slug, wave_map
from app.services.rate_limiter import rate_limit

early_access_bp = Blueprint("early_access", __name__)

CONSENT_TEXT = "Used to tell you when this becomes available, and for nothing else."


def coming_groups() -> list[dict]:
    """The capabilities that open in a later release, once each, in alphabetical order."""
    doc = wave_map()
    seen: dict[str, dict] = {}
    for entry in doc["rules"].values():
        wave, code = entry[0], entry[1]
        if wave in doc["pass_waves"] or wave in ("W1", "X", "RT") or code.startswith("BP:") or code in ("infra", "PG-RT", "PG-RTP", "PG-NEW", "PG-EA", "PG-OPS"):
            continue
        info = doc["groups"].get(code)
        if info and info["title"] not in seen:
            seen[info["title"]] = {"slug": code_slug(code), "title": info["title"], "blurb": info["blurb"]}
    return [seen[k] for k in sorted(seen)]


def _page(slug: str, *, thanks: bool = False, error: str | None = None, title: str | None = None):
    found = group_by_slug(slug)
    if found is None:
        abort(404)
    _code, info = found
    if title:
        info = {**info, "title": title}
    out = make_response(render_template(
        "public/early_access.html", group_slug=slug, group=info, thanks=thanks, error=error,
        form_action=url_for("early_access.join", slug=slug),
    ))
    out.headers["X-Robots-Tag"] = "noindex"
    return out


def early_access_page(slug: str, *, title: str | None = None):
    """The early-access page for a capability group, optionally under the name of the page that led here."""
    return _page(slug, title=title)


@early_access_bp.route("/early-access")
def hub():
    return render_template("public/early_access_hub.html", groups=coming_groups())


@early_access_bp.route("/early-access/<slug>")
def page(slug):
    return _page(slug)


@early_access_bp.route("/early-access/<slug>/join", methods=["POST"])
@rate_limit(10, "1m", methods=("POST",))
def join(slug):
    from app.models.product_inquiry import ProductInquiry

    if group_by_slug(slug) is None:
        abort(404)
    email = (request.form.get("email") or "").strip().lower()
    if not email:
        return _page(slug, error="Please enter an email address.")
    if not request.form.get("consent"):
        return _page(slug, error="Please agree that we may email you about this.")
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError:
        return _page(slug, error="Please enter a valid email address.")
    offer = f"early-access:{slug}"[:50]
    if ProductInquiry.query.filter_by(email=email, offer=offer).first() is None:
        db.session.add(ProductInquiry(email=email, name=None, offer=offer, consent_text=CONSENT_TEXT))
        db.session.commit()
    return _page(slug, thanks=True)
