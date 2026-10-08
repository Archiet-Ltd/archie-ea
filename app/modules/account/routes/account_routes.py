"""
Account Routes (migrated).

Thin route layer - all business logic lives in AccountService.

Migrated from: app/account/views.py
URL prefix preserved: /account (applied via register() in __init__.py)

Endpoints:
- /login, /register, /logout
- /manage, /manage/info, /manage/change-password, /manage/change-email, /manage/change-email/<token>
- /reset-password, /reset-password/<token>
- /confirm-account, /confirm-account/<token>
- /join-from-invite/<user_id>/<token>
- /unconfirmed
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

from app.security.audit import audit_logger

_log = logging.getLogger(__name__)
from app.services import buy_intent
from app.services.rate_limiter import rate_limit

from . import mail_views
from ..services.account_service import AccountService
from ..forms.account_forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    LoginForm,
)

account_bp = Blueprint("account", __name__)

_svc = AccountService



@account_bp.route("/login", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))  # SECURITY: Brute-force protection on credential submits only
def login():
    """Log in an existing user."""
    # Opening /login while already signed in must not disturb the existing
    # session (it is still fully valid) -- send the user on rather than
    # re-rendering the sign-in form, which otherwise reads as an unexpected
    # sign-out even though the session was never touched. Same
    # already-authenticated guard as reset_password_request()/reset_password()
    # below, reused here rather than duplicated with new logic. Honour a
    # same-origin ?next= the way a successful login below already does --
    # arriving here signed in from a deep link (e.g. a bookmarked page whose
    # session just outlived a tab) must land back on that page, not always
    # the dashboard; safe_next_url() is the same allow-list guard against an
    # off-site next, reused rather than re-implemented here.
    if current_user.is_authenticated:
        from app.utils.safe_redirect import safe_next_url

        return redirect(
            safe_next_url(buy_intent.next_candidate(consume=True), url_for("dashboard.overview"))
        )
    form = LoginForm()
    if form.validate_on_submit():
        # COM-005: Check email-domain SSO config before password auth.
        # If the user's org has SSO configured and enabled, redirect to IdP.
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
            # R1-B12 PR 2 (TB-0144/PB-0100): an administrator must complete
            # multi-factor before the login finishes, whether they are
            # enrolling for the first time or entering a code from an
            # already-enrolled authenticator app. Checked before the
            # session-fixation reset below so a password alone never mints
            # a real session for an administrator account.
            from app.services import mfa_service

            if mfa_service.required_for(user):
                session["_mfa_pending_user_id"] = user.id
                session["_mfa_pending_remember"] = bool(form.remember_me.data)
                session["_mfa_pending_next"] = buy_intent.next_candidate(consume=True) or ""
                return redirect(url_for("account.mfa_challenge"))

            # Fix Session Fixation: Regenerate session ID after successful authentication
            # Read before the session is cleared: the chosen plan lives in it.
            _landing = buy_intent.next_candidate(consume=True)
            session.clear()
            session.modified = True
            _svc.login(user, form.remember_me.data)
            session.permanent = True
            try:
                audit_logger.log_authentication(success=True)
            except Exception as _exc:
                _log.warning("Audit log failed on login success: %s", _exc)
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
                safe_next_url(_landing, url_for("dashboard.overview"))
            )
        else:
            try:
                audit_logger.log_authentication(success=False)
            except Exception as _exc:
                _log.warning("Audit log failed on login failure: %s", _exc)
            # V-06: failed-login monitoring needs the attempt in the surfaced
            # audit table, attributed to the account it targeted where one exists.
            from app.services import auth_audit

            auth_audit.record_login_failure(form.email.data)
            flash("Invalid email or password.", "form-error")
    # Gather configured SSO providers for the login page buttons
    from app.auth.sso import sso_service

    oidc_providers = []
    if _sso_enabled():
        for key in sso_service.available_providers():
            cfg = sso_service.providers.get(key, {})
            oidc_providers.append({"key": key, "name": cfg.get("name", key.capitalize())})

    return render_template(
        "account/login.html",
        form=form,
        sso_providers=oidc_providers,
    )


def _mfa_pending_user():
    """Return the User this request is mid-MFA for, or None.

    The user is NOT yet logged in (flask-login's current_user is anonymous
    throughout this flow) -- the pending identity lives only in the signed
    session cookie, set by login()/the SSO callbacks right after password
    or IdP authentication succeeds and before any real session is minted.
    """
    from app.models.user import User

    user_id = session.get("_mfa_pending_user_id")
    if not user_id:
        return None
    # tenant-scoping-ok: pre-login MFA step, no org context yet -- this is
    # the one user the signed session cookie names as mid-login, the same
    # posture as the pre-auth SSO callback lookup below.
    return User.query.get(user_id)


def _complete_login_after_mfa(user):
    """Finish the login that _mfa_pending_user_id was holding open, mirroring
    login()'s own session-fixation reset and audit trail."""
    from app.services import auth_audit

    remember = bool(session.pop("_mfa_pending_remember", False))
    next_url = session.pop("_mfa_pending_next", "") or ""
    session.pop("_mfa_pending_user_id", None)

    session.clear()
    session.modified = True
    _svc.login(user, remember)
    session.permanent = True
    try:
        audit_logger.log_authentication(success=True)
    except Exception as _exc:
        _log.warning("Audit log failed on login success: %s", _exc)
    _login_entry = auth_audit.record_login_success(user)
    if _login_entry is not None:
        session["_login_audit_id"] = _login_entry.id
    flash("You are now logged in. Welcome back!", "success")

    from app.utils.safe_redirect import safe_next_url

    return redirect(safe_next_url(next_url, url_for("dashboard.overview")))


@account_bp.route("/mfa-challenge", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))  # SECURITY: brute-force protection on code submits
def mfa_challenge():
    """Multi-factor step for an administrator mid-login (R1-B12 PR 2,
    TB-0144/PB-0100).

    Reached only via the pending-user session key login()/the SSO callbacks
    set right after primary authentication succeeds -- there is no direct
    path to this route that skips the password or IdP check. An
    already-enrolled administrator enters a code from their authenticator
    app; one not yet enrolled is shown a fresh secret to scan and must prove
    they captured it correctly before MFA (and the login) is considered
    satisfied.
    """
    user = _mfa_pending_user()
    if user is None:
        flash("Your sign-in attempt expired. Please sign in again.", "error")
        return redirect(url_for("account.login"))

    from app.services import mfa_service

    if not user.mfa_enabled:
        secret = session.get("_mfa_enroll_secret")
        if not secret:
            secret = mfa_service.generate_secret()
            session["_mfa_enroll_secret"] = secret
        if request.method == "POST":
            code = request.form.get("code", "")
            try:
                mfa_service.enroll(user, secret, code)
            except mfa_service.MFAError as exc:
                flash(str(exc), "form-error")
                return render_template(
                    "account/mfa_enroll.html",
                    secret=secret,
                    provisioning_uri=mfa_service.provisioning_uri(user, secret),
                )
            session.pop("_mfa_enroll_secret", None)
            return _complete_login_after_mfa(user)
        return render_template(
            "account/mfa_enroll.html",
            secret=secret,
            provisioning_uri=mfa_service.provisioning_uri(user, secret),
        )

    if request.method == "POST":
        code = request.form.get("code", "")
        if mfa_service.verify_login_code(user, code):
            return _complete_login_after_mfa(user)
        flash("That code was not accepted. Try again.", "form-error")

    return render_template("account/mfa_challenge.html")


@account_bp.route("/register", methods=["GET", "POST"])
@rate_limit(5, "1m", methods=("POST",))  # SECURITY: Anti-abuse on registration submits only
def register():
    """Register a new user, and send them a confirmation email."""
    return mail_views.register_view()


@account_bp.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    try:
        audit_logger.log_logout()
    except Exception as _exc:
        _log.warning("Audit log failed on logout: %s", _exc)
    # V-06: logout is a session-forensics event too; record it in the surfaced
    # audit table while current_user is still resolvable.
    from app.services import auth_audit

    auth_audit.record_logout(current_user if current_user.is_authenticated else None)
    _svc.logout()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))


@account_bp.route("/manage", methods=["GET", "POST"])
@account_bp.route("/manage/info", methods=["GET", "POST"])
@login_required
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


@account_bp.route("/switch-organization", methods=["POST"])
@login_required
def switch_organization():
    """Switch the signed-in user's active organisation."""
    success, message = _svc.switch_active_organization(
        current_user, request.form.get("organization_id", type=int)
    )
    flash(message, "success" if success else "error")
    return redirect(url_for("account.manage"))


@account_bp.route("/session/keepalive", methods=["GET"])
@login_required
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


@account_bp.route("/manage/notification-preferences", methods=["POST"])
@login_required
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


@account_bp.route("/manage/preferences", methods=["POST"])
@login_required
def save_preferences():
    """Save user preferences (notifications and display) for the current user.

    Mirrors the v2 endpoint so the /account/manage template works correctly
    on the rollback path (USE_ACCOUNT_GUARDRAILS=false).
    """
    from app import db

    form_type = request.form.get("form_type", "")
    known_keys = [
        "arb_decisions",
        "solution_updates",
        "assignment_changes",
        "weekly_digest",
        "mention_notifications",
    ]
    try:
        if form_type == "notifications":
            prefs = {key: (request.form.get(key) == "on") for key in known_keys}
            current_user.set_notification_preferences(prefs)
        elif form_type == "display":
            current_user.show_archimate_names = (request.form.get("show_archimate_names") == "on")
        else:
            flash("Unknown preference form type.", "error")
            return redirect(url_for("account.manage"))
        db.session.add(current_user)
        db.session.commit()
        flash("Preferences saved.", "success")
    except Exception as exc:
        _log.error("Failed to save preferences for user %s: %s", current_user.id, exc)
        db.session.rollback()
        flash("Could not save preferences. Please try again.", "error")
    return redirect(url_for("account.manage"))


@account_bp.route("/reset-password", methods=["GET", "POST"])
@rate_limit(5, "1m", methods=("POST",))  # SECURITY: each POST can send mail
def reset_password_request():
    """Respond to existing user's request to reset their password."""
    return mail_views.reset_request_view()


@account_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))
def reset_password(token):
    """Reset an existing user's password."""
    return mail_views.reset_view(token)


@account_bp.route("/manage/change-password", methods=["GET", "POST"])
@login_required
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
    # unconditionally, so omitting it raises UndefinedError and 500s the page.
    return render_template("account/manage.html", user=current_user, form=form)


@account_bp.route("/manage/change-email", methods=["GET", "POST"])
@login_required
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
    # unconditionally, so omitting it raises UndefinedError and 500s the page.
    return render_template("account/manage.html", user=current_user, form=form)


@account_bp.route("/manage/change-email/<token>", methods=["GET", "POST"])
@login_required
def change_email(token):
    """Change existing user's email with provided token."""
    success, message = _svc.confirm_email_change(current_user, token)
    flash_cat = "success" if success else "error"
    flash(message, flash_cat)
    return redirect(url_for("main.index"))


@account_bp.route("/confirm-account", methods=["GET", "POST"])
@login_required
@rate_limit(3, "1m", methods=("POST",))  # SECURITY: each POST sends mail
def confirm_request():
    """Respond to new user's request to confirm their account."""
    return mail_views.confirm_request_view()


@account_bp.route("/confirm-account/<token>")
def confirm(token):
    """Confirm new user's account with provided token."""
    return mail_views.confirm_view(token)


@account_bp.route("/join/<token>", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))
def join(token):
    """Accept an e-mailed invitation into an organisation by setting a password."""
    return mail_views.join_view(token)


@account_bp.route("/join-from-invite/<int:user_id>/<token>", methods=["GET", "POST"])
@rate_limit(10, "1m")
def join_from_invite(user_id, token):
    """Retired invitation link: it is served by the one invitation flow.

    Links of this shape carried a reusable signed token that was never
    stored. They set no password and send no mail any more; the token is
    handed to ``/join/<token>``, which refuses anything it did not issue.
    """
    return redirect(url_for("account.join", token=token))


@account_bp.route("/invitation/<int:invitation_id>/accept", methods=["POST"])
@login_required
def accept_invitation(invitation_id):
    """Accept a pending invitation and gain the offered role."""
    success, message = _svc.accept_invitation(current_user, invitation_id)
    flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@account_bp.route("/invitation/<int:invitation_id>/decline", methods=["POST"])
@login_required
def decline_invitation(invitation_id):
    """Decline a pending invitation — no role is granted."""
    success, message = _svc.decline_invitation(current_user, invitation_id)
    flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@account_bp.before_app_request
def before_request():
    """Force user to confirm email before accessing login-required routes."""
    if (
        current_user.is_authenticated
        and not current_user.confirmed
        and request.endpoint[:8] != "account."
        and request.endpoint != "static"
    ):
        return redirect(url_for("account.unconfirmed"))


@account_bp.route("/unconfirmed")
def unconfirmed():
    """Catch users with unconfirmed emails."""
    return mail_views.unconfirmed_view()


# =========================================================================
# SSO / Enterprise Identity Routes (S0-01)
# Feature-flagged behind FeatureFlag(key='sso_authentication')
# =========================================================================


def _get_sso_oauth():
    """Lazy-init Authlib OAuth registry (returns None if authlib not installed)."""
    try:
        from authlib.integrations.flask_client import OAuth
    except ImportError:
        return None

    from flask import current_app

    if not hasattr(current_app, "_sso_oauth"):
        oauth = OAuth(current_app)
        providers = current_app.config.get("SSO_PROVIDERS", {})
        for name, cfg in providers.items():
            if cfg.get("client_id"):
                oauth.register(name, **cfg)
        current_app._sso_oauth = oauth

    return current_app._sso_oauth


def _sso_enabled():
    """Check if SSO feature flag is active."""
    try:
        from app.models.feature_flags import FeatureFlag

        flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
        return flag is not None and flag.is_active
    except Exception as e:
        _log.debug("SSO feature flag check failed: %s", e)
        return False


@account_bp.route("/sso/<provider>")
def sso_login(provider):
    """Initiate SSO login flow for the given provider."""
    from flask import abort

    if not _sso_enabled():
        abort(404)

    oauth = _get_sso_oauth()
    if oauth is None:
        flash("SSO is not available. Please install authlib.", "error")
        return redirect(url_for("account.login"))

    client = oauth.create_client(provider)
    if client is None:
        abort(404)

    # Anti-CSRF: generate state parameter
    import secrets

    state = secrets.token_urlsafe(32)
    session["sso_state"] = state
    session["sso_provider"] = provider

    callback_url = url_for("account.sso_callback", provider=provider, _external=True)
    return client.authorize_redirect(callback_url, state=state)


@account_bp.route("/sso/callback/<provider>")
def sso_callback(provider):
    """Handle SSO callback from identity provider."""
    from flask import abort, current_app

    if not _sso_enabled():
        abort(404)

    # Validate anti-CSRF state parameter
    expected_state = session.pop("sso_state", None)
    received_state = request.args.get("state")
    if not expected_state or expected_state != received_state:
        flash("SSO authentication failed: invalid state parameter.", "error")
        return redirect(url_for("account.login"))

    session.pop("sso_provider", None)

    oauth = _get_sso_oauth()
    if oauth is None:
        flash("SSO is not available.", "error")
        return redirect(url_for("account.login"))

    client = oauth.create_client(provider)
    if client is None:
        abort(404)

    try:
        token = client.authorize_access_token()
        userinfo = token.get("userinfo")
        if userinfo is None:
            userinfo = client.userinfo()
    except Exception as e:
        current_app.logger.error("SSO callback error for %s: %s", provider, e)
        flash("SSO authentication failed. Please try again.", "error")
        return redirect(url_for("account.login"))

    # Extract user identity from OIDC claims
    external_id = userinfo.get("sub", "")
    email = userinfo.get("email", "")
    first_name = userinfo.get("given_name", "")
    last_name = userinfo.get("family_name", "")

    if not external_id or not email:
        flash("SSO provider did not return required user information.", "error")
        return redirect(url_for("account.login"))

    # Find or create user by external_id
    from app import db
    from app.models.user import User

    # tenant-scoping-ok: pre-auth SSO callback, no org context yet -- scoped
    # by the (external_id, sso_provider) pair, which is unique per IdP.
    user = User.query.filter_by(external_id=external_id, sso_provider=provider).first()
    if user is None:
        # Try matching by email for existing password-auth users linking SSO
        user = User.find_by_email(email)
        if user is not None:
            user.external_id = external_id
            user.sso_provider = provider
        else:
            # Create new user
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                external_id=external_id,
                sso_provider=provider,
                confirmed=True,
            )
            db.session.add(user)

        db.session.commit()

    # Establish Flask-Login session (same as password login)
    from app.services import session_registry

    session_registry.login_and_register(user, remember=True)

    flash("Successfully signed in via SSO.", "success")
    return redirect(url_for("main.index"))
