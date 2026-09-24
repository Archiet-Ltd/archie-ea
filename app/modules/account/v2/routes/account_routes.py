"""
Account Routes v2 — guardrail-enabled.

Uses the new architecture:
- @timed_route for automatic metrics collection on all endpoints
- @guarded_route for auth-gated endpoints (manage, change-password, etc.)
- Observability (request_id in response headers)
- Consistent error handling via exception mappers

URL prefix preserved: /account (applied via register() in v2/__init__.py)
Blueprint name: account (same as v1 for url_for compatibility with shared AccountService)

All 14 routes + 1 before_app_request hook preserved exactly.
"""

import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

_log = logging.getLogger(__name__)

from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.security.audit import audit_logger
from app.services.rate_limiter import rate_limit

from app.modules.account.forms.account_forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    CreatePasswordForm,
    LoginForm,
    RegistrationForm,
    RequestResetPasswordForm,
    ResetPasswordForm,
)
from app.modules.account.services.account_service import AccountService

# Blueprint name MUST be "account" (not "account_v2") because the shared
# AccountService uses url_for("account.confirm", ...) etc.  The 3-tier
# fallback in _bootstrap/blueprints.py guarantees only one tier is active,
# so there is no name collision.
account_bp_v2 = Blueprint("account", __name__)
mark_blueprint_guardrailed(account_bp_v2)

_svc = AccountService


@account_bp_v2.route("/login", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))  # SECURITY: Brute-force protection on credential submits only
@timed_route
def login():
    """Log in an existing user."""
    form = LoginForm()
    if form.validate_on_submit():
        # COM-005: Check email-domain SSO config before password auth.
        try:
            from app.services.sso_service import SSOService

            _sso_svc = SSOService()
            _sso_cfg = _sso_svc.get_config_for_email(form.email.data)
            if _sso_cfg is not None and _sso_cfg.enabled:
                return redirect(
                    url_for("sso.sso_initiate", email=form.email.data)
                )
        except Exception as _sso_exc:
            _log.debug("SSO domain check failed (non-fatal): %s", _sso_exc)

        user = _svc.authenticate(form.email.data, form.password.data)
        if user is not None:
            # Fix Session Fixation: Regenerate session ID after successful authentication
            session.clear()
            session.modified = True
            _svc.login(user, form.remember_me.data)
            session.permanent = True
            try:
                audit_logger.log_authentication(success=True)
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — audit log is fire-and-forget
                pass
            # V-06: audit_logger writes to `audit_events`, which nothing
            # surfaces. Record the same event in `soc2_audit_log`, the table
            # /admin/audit-log actually reads, with IP and user agent.
            from app.services import auth_audit

            _login_entry = auth_audit.record_login_success(user)
            if _login_entry is not None:
                session["_login_audit_id"] = _login_entry.id
            flash("You are now logged in. Welcome back!", "success")
            # Prevent open redirect. The previous inline check (reject "//" and
            # "://") let "/\evil.com" through, which browsers normalise to
            # "//evil.com" and follow off-site.
            from app.utils.safe_redirect import safe_next_url

            return redirect(
                safe_next_url(request.args.get("next"), url_for("dashboard.overview"))
            )
        else:
            try:
                audit_logger.log_authentication(success=False)
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — audit log is fire-and-forget
                pass
            # V-06: failed-login monitoring needs the attempt in the surfaced
            # audit table, attributed to the account it targeted where one exists.
            from app.services import auth_audit

            auth_audit.record_login_failure(form.email.data)
            flash("Invalid email or password.", "form-error")
    return render_template("account/login.html", form=form)


@account_bp_v2.route("/register", methods=["GET", "POST"])
@rate_limit(5, "1m", methods=("POST",))  # SECURITY: Anti-abuse on registration submits only
@timed_route
def register():
    """Register a new user, and send them a confirmation email."""
    form = RegistrationForm()
    if form.validate_on_submit():
        (_svc.register_user(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            password=form.password.data,
        ))
        flash(f"Account created successfully. Welcome to {current_app.config['APP_NAME']}!", "success")
        return redirect(url_for("main.index"))
    return render_template("account/register.html", form=form)


@account_bp_v2.route("/logout")
@login_required
@timed_route
def logout():
    """Log out the current user."""
    try:
        audit_logger.log_logout()
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — audit log is fire-and-forget
        pass
    # V-06: logout is a session-forensics event too; record it in the surfaced
    # audit table while current_user is still resolvable.
    from app.services import auth_audit

    auth_audit.record_logout(current_user if current_user.is_authenticated else None)
    _svc.logout()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))


@account_bp_v2.route("/manage", methods=["GET", "POST"])
@account_bp_v2.route("/manage/info", methods=["GET", "POST"])
@login_required
@timed_route
def manage():
    """Display a user's account information."""
    # V-06: last login is read back from the audit log rather than duplicated
    # into a User column, so the account page and /admin/audit-log cannot
    # disagree. The entry written by the CURRENT login is excluded, otherwise
    # "last login" would always read "just now".
    from app.services import auth_audit

    last_login_entry = auth_audit.last_login(
        current_user.id, before_id=session.get("_login_audit_id")
    )
    return render_template(
        "account/manage.html",
        user=current_user,
        form=None,
        last_login_entry=last_login_entry,
        recent_auth_events=auth_audit.recent_auth_events(current_user.id),
    )


@account_bp_v2.route("/session/keepalive", methods=["GET"])
@login_required
@timed_route
def session_keepalive():
    """F-07: cheap same-origin ping that refreshes the server idle stamp.

    The client-side warning used to ping /health, which is a liveness endpoint
    and says nothing about the session — and /health is deliberately exempt
    from the idle check so a background poll cannot keep an abandoned tab
    alive. This endpoint is not exempt: reaching it IS activity, and it is
    behind @login_required so an expired session gets the 401/redirect the
    before_request hook produces rather than a misleading 200.
    """
    from flask import jsonify

    return jsonify(
        {
            "ok": True,
            "idle_timeout_seconds": current_app.config.get(
                "SESSION_IDLE_TIMEOUT_SECONDS"
            ),
        }
    )


@account_bp_v2.route("/reset-password", methods=["GET", "POST"])
@timed_route
def reset_password_request():
    """Respond to existing user's request to reset their password."""
    if not current_user.is_anonymous:
        return redirect(url_for("main.index"))
    form = RequestResetPasswordForm()
    if form.validate_on_submit():
        from flask import current_app as _ca
        try:
            _svc.request_password_reset(form.email.data)
        except Exception:
            _ca.logger.exception("password reset request failed for %s", form.email.data)
        flash("If an account exists for {}, a password reset link has been sent.".format(form.email.data), "info")
        return redirect(url_for("account.login"))
    return render_template("account/reset_password.html", form=form)


@account_bp_v2.route("/reset-password/<token>", methods=["GET", "POST"])
@timed_route
def reset_password(token):
    """Reset an existing user's password."""
    if not current_user.is_anonymous:
        return redirect(url_for("main.index"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        success, message = _svc.reset_password(token, form.email.data, form.new_password.data)
        flash_cat = "form-success" if success else "form-error"
        flash(message, flash_cat)
        if success:
            return redirect(url_for("account.login"))
        return redirect(url_for("main.index"))
    return render_template("account/reset_password.html", form=form)


@account_bp_v2.route("/manage/change-password", methods=["GET", "POST"])
@login_required
@timed_route
def change_password():
    """Change an existing user's password."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        success, message, revoked_count = _svc.change_password(
            current_user, form.old_password.data, form.new_password.data
        )
        flash_cat = "form-success" if success else "form-error"
        flash(message, flash_cat)
        if success:
            if revoked_count is not None and revoked_count > 0:
                device_word = "device" if revoked_count == 1 else "devices"
                flash(
                    "{} other signed-in {} {} signed out.".format(
                        revoked_count, device_word, "was" if revoked_count == 1 else "were"
                    ),
                    "info",
                )
            elif revoked_count is None:
                flash(
                    "We could not confirm your other sessions were signed out. "
                    "Please sign out of other devices manually.",
                    "warning",
                )
            return redirect(url_for("main.index"))
    # user= is required: account/manage.html reads user.first_name / user.last_name
    # unconditionally, so omitting it raises UndefinedError and 500s the page. The
    # manage() route above passes it; these two did not.
    return render_template("account/manage.html", user=current_user, form=form)


@account_bp_v2.route("/manage/change-email", methods=["GET", "POST"])
@login_required
@timed_route
def change_email_request():
    """Respond to existing user's request to change their email."""
    form = ChangeEmailForm()
    if form.validate_on_submit():
        success, message = _svc.request_email_change(
            current_user, form.email.data, form.password.data
        )
        flash_cat = "warning" if success else "form-error"
        flash(message, flash_cat)
        if success:
            return redirect(url_for("main.index"))
    # user= is required: account/manage.html reads user.first_name / user.last_name
    # unconditionally, so omitting it raises UndefinedError and 500s the page. The
    # manage() route above passes it; these two did not.
    return render_template("account/manage.html", user=current_user, form=form)


@account_bp_v2.route("/manage/change-email/<token>", methods=["GET", "POST"])
@login_required
@timed_route
def change_email(token):
    """Change existing user's email with provided token."""
    success, message = _svc.confirm_email_change(current_user, token)
    flash_cat = "success" if success else "error"
    flash(message, flash_cat)
    return redirect(url_for("main.index"))


@account_bp_v2.route("/confirm-account")
@login_required
@timed_route
def confirm_request():
    """Respond to new user's request to confirm their account."""
    from flask import current_app as _ca
    if _ca.config.get("MAIL_USERNAME") or _ca.config.get("MAIL_PASSWORD"):
        try:
            _svc.send_confirmation_email(current_user)
        except Exception:
            _ca.logger.warning("confirmation email send failed")
    else:
        _ca.logger.info("mail not configured; skipping confirmation email")
    flash("A new confirmation link has been sent to {}.".format(current_user.email), "warning")
    return redirect(url_for("main.index"))


@account_bp_v2.route("/confirm-account/<token>")
@login_required
@timed_route
def confirm(token):
    """Confirm new user's account with provided token."""
    if current_user.confirmed:
        return redirect(url_for("main.index"))
    success, message = _svc.confirm_account(current_user, token)
    flash_cat = "success" if success else "error"
    flash(message, flash_cat)
    return redirect(url_for("main.index"))


@account_bp_v2.route("/join-from-invite/<int:user_id>/<token>", methods=["GET", "POST"])
@timed_route
def join_from_invite(user_id, token):
    """Confirm new user's account with provided token and prompt them to set a password."""
    if current_user is not None and current_user.is_authenticated:
        flash("You are already logged in.", "error")
        return redirect(url_for("main.index"))

    new_user, token_valid, message = _svc.join_from_invite(user_id, token)

    if new_user is None:
        return redirect(404)

    if not token_valid and new_user.password_hash is not None:
        flash(message, "error")
        return redirect(url_for("main.index"))

    if token_valid:
        form = CreatePasswordForm()
        if form.validate_on_submit():
            _svc.set_password(new_user, form.password.data)
            flash(
                "Your password has been set. After you log in, you can "
                'go to the "Your Account" page to review your account '
                "information and settings.",
                "success",
            )
            return redirect(url_for("account.login"))
        return render_template("account/join_invite.html", form=form)
    else:
        flash(message, "error")
    return redirect(url_for("main.index"))


@account_bp_v2.before_app_request
def before_request():
    """Force user to confirm email before accessing login-required routes."""
    if (
        current_user.is_authenticated
        and not current_user.confirmed
        and request.endpoint
        and request.endpoint[:8] != "account."
        and request.endpoint != "static"
    ):
        return redirect(url_for("account.unconfirmed"))


@account_bp_v2.route("/manage/notification-preferences", methods=["POST"])
@login_required
@timed_route
def save_notification_preferences():
    """PLT-017: Save in-app notification preferences for the current user."""
    from app import db

    known_keys = [
        "arb_decisions",
        "solution_updates",
        "assignment_changes",
        "weekly_digest",
        "mention_notifications",
    ]
    prefs = {key: (request.form.get(key) == "on") for key in known_keys}
    try:
        current_user.set_notification_preferences(prefs)
        db.session.add(current_user)
        db.session.commit()
        flash("Notification preferences saved.", "success")
    except Exception as exc:
        _log.error("Failed to save notification preferences for user %s: %s", current_user.id, exc)
        db.session.rollback()
        flash("Could not save preferences. Please try again.", "error")
    return redirect(url_for("account.manage"))


@account_bp_v2.route("/unconfirmed")
@timed_route
def unconfirmed():
    """Catch users with unconfirmed emails."""
    if current_user.is_anonymous or current_user.confirmed:
        return redirect(url_for("main.index"))
    return render_template("account/unconfirmed.html")


# ---------------------------------------------------------------------------
# SSO routes — feature-flag gated, 404 when not configured
# ---------------------------------------------------------------------------

def _sso_enabled():
    """Check if SSO feature flag is active."""
    try:
        from app.models.feature_flags import FeatureFlag

        flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
        return flag is not None and flag.is_active
    except Exception:
        return False


def _get_sso_oauth():
    """Lazy-init authlib OAuth registry."""
    from flask import current_app

    if hasattr(current_app, "_sso_oauth"):
        return current_app._sso_oauth

    try:
        from authlib.integrations.flask_client import OAuth
    except ImportError:
        return None

    oauth = OAuth(current_app)
    providers = current_app.config.get("SSO_PROVIDERS", {})
    for name, cfg in providers.items():
        if cfg.get("client_id"):
            oauth.register(name, **cfg)
    current_app._sso_oauth = oauth
    return current_app._sso_oauth


@account_bp_v2.route("/sso/<provider>")
@timed_route
def sso_login(provider):
    """Initiate SSO login flow for the given provider."""
    from flask import abort
    import secrets

    if not _sso_enabled():
        abort(404)

    oauth = _get_sso_oauth()
    if oauth is None:
        flash("SSO is not available. Please install authlib.", "error")
        return redirect(url_for("account.login"))

    client = oauth.create_client(provider)
    if client is None:
        abort(404)

    state = secrets.token_urlsafe(32)
    session["sso_state"] = state
    session["sso_provider"] = provider

    callback_url = url_for("account.sso_callback", provider=provider, _external=True)
    return client.authorize_redirect(callback_url, state=state)


@account_bp_v2.route("/sso/callback/<provider>")
@timed_route
def sso_callback(provider):
    """Handle SSO callback from identity provider."""
    from flask import abort

    if not _sso_enabled():
        abort(404)

    expected_state = session.pop("sso_state", None)
    received_state = request.args.get("state")
    if not expected_state or expected_state != received_state:
        flash("SSO authentication failed: invalid state parameter.", "error")
        return redirect(url_for("account.login"))

    oauth = _get_sso_oauth()
    if oauth is None:
        abort(500)

    client = oauth.create_client(provider)
    if client is None:
        abort(404)

    try:
        token = client.authorize_access_token()
        userinfo = token.get("userinfo") or client.userinfo()
    except Exception as exc:
        _log.error("SSO callback error for %s: %s", provider, exc)
        flash("SSO authentication failed. Please try again.", "error")
        return redirect(url_for("account.login"))

    from app import db
    from app.models import User
    from app.services import session_registry

    email = userinfo.get("email")
    if not email:
        flash("SSO provider did not return an email address.", "error")
        return redirect(url_for("account.login"))

    # tenant-scoping-ok: pre-auth SSO callback, no org context yet --
    # User.email is globally unique.
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            first_name=userinfo.get("given_name", ""),
            last_name=userinfo.get("family_name", ""),
            confirmed=True,
        )
        db.session.add(user)
        db.session.commit()

    session_registry.login_and_register(user)
    audit_logger.log("sso_login", user_id=user.id, detail=f"provider={provider}")
    return redirect(url_for("main.index"))
