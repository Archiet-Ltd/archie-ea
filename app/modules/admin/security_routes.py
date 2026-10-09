"""Admin security dashboard.

This blueprint was declared but **never registered** — `/admin/security` 404'd
even though the route and its template both existed. `app/_bootstrap/blueprints.py`
registers `app/routes/security_api.py`, whose module-level variable is also called
`security_bp`; the Flask blueprint *name* there is "security", so this was never a
name collision Flask could have complained about. Nothing imported this module at
all. It is now registered from `_register_optional_standalone()` under the
unambiguous name "admin_security".

Two things were fixed while wiring it up:

* The header table was a **hardcoded literal** captioned as the live header set.
  It advertised an HSTS policy this app does not send at all. It now reports the
  headers the response to *this very request* carries, by running the app's own
  `add_security_headers` over a probe response — the same function, not a copy of
  its logic. If that function cannot be found, the page says so rather than
  inventing a plausible table.
* The page had no authentication of any kind, and neither did the POST that
  generates a secret key. Both are now `@login_required @admin_required`.
"""

import secrets

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from app.decorators import admin_required
from app.middleware.tenant_decorators import platform_admin_required
from app.services.security_hardening import SecurityMiddleware

admin_security_bp = Blueprint("admin_security", __name__)

_HEADER_FUNC_MODULE = "app._bootstrap.security"
_HEADER_FUNC_NAME = "add_security_headers"


def _live_security_headers():
    """The security headers this request's response will actually carry.

    Runs the app's registered ``add_security_headers`` over a throwaway HTML
    response inside the current request context. That function is pure — it only
    mutates the response it is handed — so this observes the real policy instead
    of restating it. Returns ``None`` when the function is not registered, so the
    caller can say "not determined" rather than show a table that looks measured.
    """
    from flask import Response

    for fn in current_app.after_request_funcs.get(None, []):
        if fn.__module__ == _HEADER_FUNC_MODULE and fn.__name__ == _HEADER_FUNC_NAME:
            probe = fn(Response("", content_type="text/html; charset=utf-8"))
            # Content-Length describes the empty probe body, not this page's
            # response, so reporting it would be the one made-up number here.
            return {
                k: v for k, v in sorted(probe.headers.items()) if k != "Content-Length"
            }
    return None


def _rate_limiter_installed():
    """True when ``SecurityMiddleware`` is actually wired into the request pipeline.

    Its ``_rate_limit_store`` is a class attribute that only fills up if something
    calls ``SecurityMiddleware.rate_limit``. Rendering an empty store without this
    check reads as "nothing has been rate limited", which is a different statement
    from "no rate limiter is running".
    """
    hooks = list(current_app.before_request_funcs.get(None, [])) + list(
        current_app.after_request_funcs.get(None, [])
    )
    return any(fn.__module__ == SecurityMiddleware.__module__ for fn in hooks)


@admin_security_bp.route("/admin/security", methods=["GET"])
@login_required
# Deployment-wide security posture (response headers, rate-limit store), not
# tenant data -- genuinely platform-wide. admin_required alone let any
# tenant's own admin reach it (R1 admin-rbac systemic fix).
@platform_admin_required
@admin_required
def security_dashboard():
    """Show the response headers and rate-limit state this deployment really has."""
    return render_template(
        "admin/security.html",
        headers=_live_security_headers(),
        rate_limit_stats=(
            sorted(SecurityMiddleware._rate_limit_store.items())
            if _rate_limiter_installed()
            else None
        ),
    )


@admin_security_bp.route("/api/admin/security/rotate-secret", methods=["POST"])
@login_required
@platform_admin_required
@admin_required
def rotate_secret():
    """Generate a candidate SECRET_KEY. Nothing is rotated by this call."""
    current_app.logger.info(
        "Candidate SECRET_KEY generated from %s (not applied)", request.remote_addr
    )
    return jsonify(
        {
            "rotated": False,
            "new_secret_stub": secrets.token_urlsafe(48),
            "instructions": (
                "A candidate SECRET_KEY has been generated. Nothing has been changed: "
                "to apply it, set SECRET_KEY in the environment and restart the server. "
                "Rotating it invalidates every existing session. Never share this key."
            ),
        }
    )
