"""Continue with Google and Continue with Microsoft.

Three routes, registered once whichever account blueprint is active:

* ``POST /account/oauth/<provider>/start``: begins a sign-in (CSRF protected).
* ``GET  /account/oauth/<provider>/callback``: the provider sends the person back here.
* ``POST /account/oauth/<provider>/disconnect``: removes a connected provider.

The OIDC client is :class:`app.services.sso_service.SSOService`. Which providers
are on is decided by :mod:`app.auth.sso`; a provider that is off answers 404.
:meth:`AccountService.sign_in_external_identity` decides who the identity is.
This module owns the redirects, the single-use flow record in the session, the
two-factor gate and the session itself.
"""
import hmac
import logging
import time

from flask import Blueprint, abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user, login_required

from app import db
from app.auth import sso as platform
from app.security.audit import audit_logger
from app.services import buy_intent
from app.services.rate_limiter import rate_limit
from app.utils.safe_redirect import is_safe_next_url, safe_next_url

from .services.account_service import AccountService

_log = logging.getLogger(__name__)

oauth_bp = Blueprint("account_oauth", __name__, url_prefix="/account/oauth")

#: How long a started sign-in may take before it is refused.
FLOW_SECONDS = 10 * 60

SESSION_FLOW = "oauth_flow"
SESSION_PENDING = "oauth_pending_link"


@oauth_bp.app_context_processor
def _inject_buttons():
    def social_sign_in_buttons():
        return platform.platform_sign_in_buttons(request.host)

    return {"social_sign_in_buttons": social_sign_in_buttons}


@oauth_bp.app_context_processor
def _inject_sign_in_methods():
    def sign_in_methods():
        """What the Sign-in methods card shows for the signed-in person; ``None`` when no provider is on."""
        from app.models.sign_in_identity import SignInIdentity

        status = platform.platform_sign_in_status()
        on = [key for key in platform.PLATFORM_LABELS if status[key]["configured"]]
        if not on or not current_user.is_authenticated:
            return None
        rows = SignInIdentity.query.filter(
            SignInIdentity.user_id == current_user.id, SignInIdentity.provider.in_(on)
        ).all()
        by_provider = {row.provider: row for row in rows}
        return {
            "connected": [
                {"key": key, "label": platform.PLATFORM_LABELS[key], "email": by_provider[key].email_at_link,
                 "connected_at": by_provider[key].created_at}
                for key in on if key in by_provider
            ]
        }

    return {"sign_in_methods": sign_in_methods}


def pending_email():
    """The address a Google or Microsoft connection is waiting on, for the sign-in form, or None."""
    pending = session.get(SESSION_PENDING)
    if isinstance(pending, dict) and float(pending.get("expires_at") or 0) > time.time():
        return pending.get("email")
    return None


def _cancelled(label):
    return "Sign-in with {} was cancelled or did not complete. Please try again.".format(label)


STALE = "That sign-in took too long or was started in another browser. Please try again."


def _back_to_sign_in(message, category="form-error"):
    flash(message, category)
    return redirect(url_for("account.login"))


def _home():
    return url_for("dashboard.overview")


@oauth_bp.route("/<provider>/start", methods=["POST"])
@rate_limit(10, "1m")
def start(provider):
    """Send the person to the provider's sign-in screen."""
    from app.services.sso_service import SSONotConfiguredError, SSOService

    config = platform.platform_provider(provider)
    host = request.host.lower()
    redirect_uri = platform.platform_redirect_uri(host, provider)
    if config is None or redirect_uri is None:
        abort(404)
    if current_user.is_authenticated:
        return redirect(_home())
    try:
        result = SSOService().initiate_oidc_flow(config, redirect_uri)
    except SSONotConfiguredError:
        _log.warning("could not start %s sign-in", provider, exc_info=True)
        return _back_to_sign_in(_cancelled(config.label))
    given_next = request.form.get("next")
    session[SESSION_FLOW] = {
        "provider": provider,
        "state": result["state"],
        "nonce": result["nonce"],
        "code_verifier": result["code_verifier"],
        "redirect_uri": redirect_uri,
        "next": given_next if is_safe_next_url(given_next) else (buy_intent.next_candidate() or ""),
        "started_at": time.time(),
    }
    return redirect(result["redirect_url"])


@oauth_bp.route("/<provider>/callback")
@rate_limit(10, "1m")
def callback(provider):
    """Finish a sign-in the provider has answered."""
    from app.services.sso_service import SSONotConfiguredError, SSOService

    config = platform.platform_provider(provider)
    if config is None:
        abort(404)
    flow = session.pop(SESSION_FLOW, None)
    if (
        not isinstance(flow, dict)
        or flow.get("provider") != provider
        or time.time() - float(flow.get("started_at") or 0) > FLOW_SECONDS
    ):
        return _back_to_sign_in(STALE)
    if not hmac.compare_digest(str(flow.get("state") or ""), request.args.get("state", "")):
        return _back_to_sign_in(_cancelled(config.label))
    if current_user.is_authenticated:
        flash("You're signed in as {}. Sign out first.".format(current_user.first_name or current_user.email), "info")
        return redirect(_home())
    code = request.args.get("code", "")
    if request.args.get("error") or not code:
        return _back_to_sign_in(_cancelled(config.label))
    try:
        claims = SSOService().handle_oidc_callback(
            config, code, flow["state"], flow["redirect_uri"],
            expected_nonce=flow["nonce"], code_verifier=flow["code_verifier"],
        )
    except SSONotConfiguredError:
        _record_outcome(provider, "failed")
        return _back_to_sign_in(_cancelled(config.label))
    if not claims:
        return _back_to_sign_in(_cancelled(config.label))

    from app.services import signup_attribution

    origin = signup_attribution.held()  # read before the session is reset
    outcome = AccountService.sign_in_external_identity(provider, claims)
    if outcome.status == "refused":
        _record_outcome(provider, "refused")
        return _back_to_sign_in(outcome.message)
    if outcome.status == "pending_link":
        session[SESSION_PENDING] = outcome.pending
        _record_outcome(provider, "pending_link")
        return _back_to_sign_in(
            "An Entelim account already uses {email}. Sign in to it once to connect {label}. "
            "After that, Continue with {label} signs you straight in.".format(
                email=outcome.pending["email"], label=config.label
            ),
            "form-info",
        )
    user = outcome.user
    if outcome.status == "created":
        _after_sign_up(user, origin)
    return _finish_sign_in(user, provider, flow.get("next") or "", created=outcome.status == "created")


def _after_sign_up(user, origin):
    """The same follow-up a password sign-up does: where the person came from, and the count."""
    from app.services import signup_attribution
    from app.services.public_analytics_service import log_signup_completed

    try:
        signup_attribution.record(user, origin)
        log_signup_completed()
    except Exception:
        db.session.rollback()
        _log.warning("sign-up follow-up failed for user %s", user.id, exc_info=True)


def _finish_sign_in(user, provider, next_url, created=False):
    """Two-factor gate, then the session. A provider replaces the password step only."""
    from app.services import auth_audit, mfa_service

    method = "oauth:" + provider
    mfa_service.start_window_if_due(user)
    if mfa_service.required_for(user) and not mfa_service.may_defer_enrolment(user):
        session["_mfa_pending_user_id"] = user.id
        session["_mfa_pending_remember"] = False
        session["_mfa_pending_next"] = next_url
        session["_mfa_pending_method"] = method
        return redirect(url_for("account.mfa_challenge"))

    AccountService.login(user, False)
    session.permanent = True
    try:
        audit_logger.log_authentication(success=True)
    except Exception:
        _log.warning("audit log failed on %s sign-in", provider, exc_info=True)
    entry = auth_audit.record_login_success(user, method=method)
    if entry is not None:
        session["_login_audit_id"] = entry.id
    if created and "account.welcome" in current_app.view_functions:
        return redirect(url_for("account.welcome"))
    flash("You are now logged in. Welcome back!", "success")
    return redirect(safe_next_url(next_url, _home()))


def _record_outcome(provider, outcome):
    """One line in the sign-in audit trail for a provider sign-in that did not end in a session."""
    from app.services import auth_audit

    try:
        auth_audit.record_login_failure(None, method="oauth:" + provider, outcome=outcome)
    except Exception:
        _log.warning("could not record the %s sign-in outcome", provider, exc_info=True)


@oauth_bp.route("/<provider>/disconnect", methods=["POST"])
@login_required
def disconnect(provider):
    """Remove a connected provider, unless it is the person's only way in."""
    from app.models.sign_in_identity import SignInIdentity

    if platform.platform_provider(provider) is None:
        abort(404)
    label = platform.PLATFORM_LABELS[provider]
    row = SignInIdentity.query.filter_by(user_id=current_user.id, provider=provider).first()
    if row is None:
        flash("{} is not connected to your account.".format(label), "info")
        return redirect(url_for("account.manage"))
    others = SignInIdentity.query.filter(
        SignInIdentity.user_id == current_user.id, SignInIdentity.provider != provider
    ).count()
    if current_user.password_hash is None and others == 0:
        flash("Set a password first: use Forgot password on the sign-in page.", "error")
        return redirect(url_for("account.manage"))
    db.session.delete(row)
    db.session.commit()
    flash("{} sign-in was disconnected from your account.".format(label), "success")
    return redirect(url_for("account.manage"))
