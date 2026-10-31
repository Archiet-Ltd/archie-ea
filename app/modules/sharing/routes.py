"""Revocable, read-only share links for architecture artefacts (BA-B1).

Three routes an owner uses (all behind ``login_required`` and CSRF) plus one
public route that takes no login at all:

    GET  /share/artefacts                       owner console — create, copy, revoke
    POST /share/artefacts/<artefact_type>       mint a token for one artefact
    POST /share/artefacts/<int:link_id>/revoke  revoke a token, immediately
    GET  /shared/<token>                        the public, read-only page

Why this exists: senior leadership will not log into an EA tool. A capability
framework that cannot travel to them is shelf-ware by construction.

Tenancy — the whole risk of this feature
----------------------------------------
``/shared/<token>`` runs with no user, so ``g.current_org_id`` is None and the
ORM tenant filter is a no-op (this is documented behaviour, not a bug). The
token is therefore the *only* thing standing between a request and one
organisation's data, and scope is derived from the resolved row:

    link = ArtefactShareLink.query.filter_by(token=token).first()   # unique index
    data = build_artefact(link.artefact_type, link.organization_id)

``build_artefact`` re-applies that organisation id both as an explicit predicate
and as the ORM filter's scope (see service.py). No value from the URL other than
the token itself ever reaches a query, and ``artefact_type`` comes off the row,
not the request — so a token cannot be pointed at a different artefact or a
different tenant by editing the URL.

Owner-side queries are scoped by hand too. ``ArtefactShareLink`` is deliberately
not a ``TenantMixin`` model (see its docstring), so nothing filters it for us;
every query below carries ``organization_id == g.current_org_id``. That is what
stops org A revoking — or even seeing — org B's link.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.datetime_helpers import utcnow
from app.extensions import db
from app.models.artefact_share import (
    SHAREABLE_ARTEFACTS,
    ArtefactShareLink,
    generate_share_token,
)
from app.modules.sharing.service import build_artefact
from app.utils.csrf_helper import require_csrf

artefact_share_bp = Blueprint("artefact_share", __name__)

#: Where the same artefact lives inside the app, for the owner console only.
#: Guarded at render time — a blueprint that failed to import would otherwise
#: BuildError and 500 this page (see CLAUDE.md, "Blueprints register non-fatally").
ARTEFACT_ENDPOINTS = {
    "maturity_heatmap": "capability_map.dashboard",
    "capability_map": "capability_map.index",
    "capability_roadmap": "main.capability_roadmap",
}


def _owner_org_id():
    """The organisation the logged-in owner may act on, or 403."""
    org_id = getattr(g, "current_org_id", None) or getattr(
        current_user, "organization_id", None
    )
    if not org_id:
        abort(403)
    return org_id


def _share_url(token: str) -> str:
    return url_for("artefact_share.public_view", token=token, _external=True)


# ── Owner side ────────────────────────────────────────────────────────────────


@artefact_share_bp.route("/share/artefacts")
@login_required
def console():
    """List the shareable artefacts and this organisation's links for each."""
    org_id = _owner_org_id()

    links = (
        ArtefactShareLink.query.filter(ArtefactShareLink.organization_id == org_id)
        .order_by(ArtefactShareLink.created_at.desc())
        .all()
    )

    by_type: dict[str, list] = {key: [] for key in SHAREABLE_ARTEFACTS}
    for link in links:
        by_type.setdefault(link.artefact_type, []).append(
            {
                "id": link.id,
                "token": link.token,
                "url": _share_url(link.token),
                "created_at": link.created_at,
                "revoked_at": link.revoked_at,
                "is_active": link.is_active,
                "view_count": link.view_count,
                "last_viewed_at": link.last_viewed_at,
            }
        )

    artefacts = []
    for key, title in SHAREABLE_ARTEFACTS.items():
        endpoint = ARTEFACT_ENDPOINTS.get(key)
        artefacts.append(
            {
                "key": key,
                "title": title,
                "internal_url": (
                    url_for(endpoint)
                    if endpoint and endpoint in current_app.view_functions
                    else None
                ),
                "links": by_type.get(key, []),
            }
        )

    return render_template("artefact_share/console.html", artefacts=artefacts)


@artefact_share_bp.route("/share/artefacts/<artefact_type>", methods=["POST"])
@login_required
@require_csrf
def create_link(artefact_type):
    """Mint a new share token for one artefact of the caller's organisation."""
    if artefact_type not in SHAREABLE_ARTEFACTS:
        abort(404)
    org_id = _owner_org_id()

    payload = request.get_json(silent=True) or {}
    label = (payload.get("label") or "").strip()[:200] or None

    link = ArtefactShareLink(
        token=generate_share_token(),
        artefact_type=artefact_type,
        organization_id=org_id,
        created_by_id=getattr(current_user, "id", None),
        label=label,
        created_at=utcnow(),
        view_count=0,
    )
    db.session.add(link)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to create share link for %s (org %s)", artefact_type, org_id
        )
        return jsonify({"error": "Could not create the share link."}), 500

    return (
        jsonify(
            {
                "success": True,
                "id": link.id,
                "token": link.token,
                "share_url": _share_url(link.token),
                "artefact_title": link.artefact_title,
            }
        ),
        201,
    )


@artefact_share_bp.route("/share/artefacts/<int:link_id>/revoke", methods=["POST"])
@login_required
@require_csrf
def revoke_link(link_id):
    """Revoke a link. The public route stops serving it on the next request.

    Scoped by ``organization_id`` in the same query that selects the row, so a
    caller from another organisation gets a 404 rather than a revoked link —
    an id from another tenant is indistinguishable from one that never existed.
    """
    org_id = _owner_org_id()

    link = ArtefactShareLink.query.filter(
        ArtefactShareLink.id == link_id,
        ArtefactShareLink.organization_id == org_id,
    ).first()
    if link is None:
        abort(404)

    if link.revoked_at is None:
        link.revoked_at = utcnow()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to revoke share link %s", link_id)
            return jsonify({"error": "Could not revoke the share link."}), 500

    return jsonify({"success": True, "id": link.id, "revoked": True})


# ── Public side — no login, read-only, one artefact ───────────────────────────


@artefact_share_bp.route("/shared/<token>")
def public_view(token):
    """Render one shared artefact for someone who has the link and no account.

    Everything here is a 404 rather than a 403: a wrong, revoked or expired
    token must be indistinguishable from one that was never issued, or the
    response confirms which tokens exist.
    """
    if not token or len(token) > 64:
        abort(404)

    link = ArtefactShareLink.query.filter(ArtefactShareLink.token == token).first()
    if link is None or not link.is_active:
        abort(404)
    if link.artefact_type not in SHAREABLE_ARTEFACTS:
        abort(404)

    try:
        data = build_artefact(link.artefact_type, link.organization_id)
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to build shared artefact %s for org %s",
            link.artefact_type,
            link.organization_id,
        )
        # No fabricated fallback: say the artefact could not be built.
        data = None

    organization = None
    try:
        from app.models.organization import Organization

        organization = db.session.get(Organization, link.organization_id)
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Could not load organisation %s for share link", link.organization_id
        )

    # Best-effort view accounting — never fail the page over it.
    try:
        link.view_count = (link.view_count or 0) + 1
        link.last_viewed_at = utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()

    return render_template(
        "artefact_share/public.html",
        link=link,
        artefact_type=link.artefact_type,
        artefact_title=link.artefact_title,
        organization_name=organization.name if organization else None,
        data=data,
        generated_at=utcnow(),
    )
