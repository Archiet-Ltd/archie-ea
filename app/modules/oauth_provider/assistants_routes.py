"""Connected assistants: see and revoke the AI assistants acting as a person.

One screen under account settings for the signed-in person, and one page in
the administrator's per-user area for an administrator who must cut an
account's assistants off. Both read ``oauth_tokens`` through the same helper
and revoke through ``OAuthToken.revoke_grant`` / ``revoke_all_for_user`` --
the same accessors the session-revocation path uses.
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.decorators import admin_required
from app.middleware.tenant_decorators import require_org_or_platform_admin
from app.models.user import User
from app.modules.oauth_provider.models import OAuthClient, OAuthToken

logger = logging.getLogger(__name__)

connected_assistants_bp = Blueprint("connected_assistants", __name__)


def mcp_connection_address() -> str:
    """The organisation's connection address for an AI chat client.

    One origin, configured once as ``PUBLIC_BASE_URL`` and published as the
    protected resource in the well-known metadata; the ``/mcp`` path is where
    a client points. Empty when the server is not configured for connections,
    so the page can say so plainly rather than show a broken address.
    """
    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/mcp" if base else ""


# One line per way a client connects, not one per brand: the server works with
# any client that speaks the standard remote-chat protocol over HTTP and signs
# in the same way a browser does, which is exactly what its own connector tests
# drive end to end. A client that needs a different transport is not listed.
SUPPORTED_CLIENTS = [
    {
        "name": "A desktop or editor AI chat that adds a remote connection",
        "detail": "Paste the address where it asks for a remote server, then sign in when it opens this site.",
    },
    {
        "name": "A command-line AI chat that adds an HTTP connection",
        "detail": "Add the address as an HTTP server; it opens this site to sign in the first time.",
    },
]


def connected_grants(user_id: int) -> list[dict]:
    """The assistants currently allowed to act as *user_id*, one row per grant."""
    # tenant-scoping-ok: oauth_tokens is a lookup-by-hash table with no tenant
    # fence; every read here is keyed to one user id, never to a caller's input.
    rows = (
        OAuthToken.query.filter(OAuthToken.user_id == user_id)  # tenant-scoping-ok: keyed to one user id; the token table has no tenant fence
        .order_by(OAuthToken.issued_at.asc())
        .all()
    )
    grants: dict[str, dict] = {}
    for token in rows:
        key = token.grant_id or f"token-{token.id}"
        entry = grants.setdefault(key, {
            "grant_id": key,
            "client_id": token.client_id,
            "scope": token.scope,
            "connected_at": token.issued_at,
            "last_used_at": None,
            "active": False,
        })
        if token.last_used_at and (entry["last_used_at"] is None or token.last_used_at > entry["last_used_at"]):
            entry["last_used_at"] = token.last_used_at
        if token.is_refresh_active or token.is_active:
            entry["active"] = True
            entry["scope"] = token.scope
    active = [g_ for g_ in grants.values() if g_["active"]]
    names = {}
    if active:
        # tenant-scoping-ok: platform-level client registry, not tenant-owned data
        for client in OAuthClient.query.filter(
            OAuthClient.client_id.in_({g_["client_id"] for g_ in active})
        ).all():
            names[client.client_id] = client.client_name
    for entry in active:
        entry["client_name"] = names.get(entry["client_id"]) or entry["client_id"]
    return sorted(active, key=lambda e: e["connected_at"], reverse=True)


@connected_assistants_bp.route("/account/manage/connected-assistants", methods=["GET"])
@login_required
def index():
    # The question the person just got a cited answer for travels here as the
    # element's id, so "Try it" and "Skip, ask here instead" carry it on. A
    # value that is not a whole number is ignored, and the page opens without
    # a question to try.
    try_element_id = request.args.get("element", type=int)
    ask_here_url = url_for("intelligence_ui.ask")
    if try_element_id:
        ask_here_url = url_for(
            "intelligence_ui.ask", question="impact", element=try_element_id
        )
    return render_template(
        "oauth/connected_assistants.html",
        grants=connected_grants(current_user.id),
        subject=current_user,
        admin_view=False,
        connection_address=mcp_connection_address(),
        supported_clients=SUPPORTED_CLIENTS,
        try_element_id=try_element_id,
        ask_here_url=ask_here_url,
    )


@connected_assistants_bp.route("/account/manage/connected-assistants/<grant_id>/revoke", methods=["POST"])
@login_required
def revoke(grant_id):
    count = OAuthToken.revoke_grant(grant_id, user_id=current_user.id)
    if count:
        flash("That assistant can no longer access your workspace.", "form-success")
    else:
        flash("That assistant was not connected.", "error")
    return redirect(url_for("connected_assistants.index"))


@connected_assistants_bp.route("/account/manage/connected-assistants/revoke-all", methods=["POST"])
@login_required
def revoke_all():
    count = OAuthToken.revoke_all_for_user(current_user.id)
    flash(
        "Every connected assistant was disconnected." if count else "No assistant was connected.",
        "form-success" if count else "error",
    )
    return redirect(url_for("connected_assistants.index"))


def _admin_target(user_id: int) -> User:
    require_org_or_platform_admin(g.current_org_id)
    # tenant-scoping-ok: org-scoped admin, restrict lookup to the current org.
    user = User.query.filter_by(id=user_id, organization_id=g.current_org_id).first()
    if user is None:
        abort(404)
    return user


@connected_assistants_bp.route("/admin/user/<int:user_id>/connected-assistants", methods=["GET"])
@login_required
@admin_required
def admin_user(user_id):
    user = _admin_target(user_id)
    return render_template(
        "oauth/connected_assistants.html",
        grants=connected_grants(user.id),
        subject=user,
        user=user,
        admin_view=True,
    )


@connected_assistants_bp.route("/admin/user/<int:user_id>/connected-assistants/revoke", methods=["POST"])
@login_required
@admin_required
def admin_revoke(user_id):
    user = _admin_target(user_id)
    count = OAuthToken.revoke_all_for_user(user.id)
    logger.info("admin %s revoked %s connector token(s) for user %s", current_user.id, count, user.id)
    flash(
        f"Disconnected every assistant for {user.full_name()}." if count
        else f"{user.full_name()} has no connected assistants.",
        "form-success" if count else "error",
    )
    return redirect(url_for("connected_assistants.admin_user", user_id=user.id))
