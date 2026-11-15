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
from app.services import buy_intent
from app.services.rate_limiter import rate_limit

from app.modules.account.forms.account_forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    LoginForm,
)
from app.modules.account.routes import mail_views
from app.modules.account.services.account_service import AccountService
from app.modules.account.services.held_account import redirect_held_account

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
    if request.method == "GET":
        from app.modules.account.oauth_routes import pending_email

        form.email.data = pending_email() or form.email.data
    if form.validate_on_submit():
        # COM-005: Check email-domain SSO config before password auth.
        # config_for_login decides (one decision shared with the v1 login).
        _sso_conflict = False
        try:
            from app.services.sso_service import SSODomainConflictError, SSOService

            _sso_cfg = SSOService().config_for_login(form.email.data, form.password.data)
            if _sso_cfg is not None:
                # Keep where the person was going (a connector set-up, say) through the identity provider.
                session["_post_login_next"] = buy_intent.next_candidate() or ""
                return redirect(
                    url_for("sso.sso_initiate", email=form.email.data)
                )
        except SSODomainConflictError:
            # Two enabled configurations claim this domain: neither SSO nor
            # password sign-in is offered for it (already audited).
            _sso_conflict = True
        except Exception as _sso_exc:
            _log.warning("SSO domain check failed; falling back to the password path", exc_info=True)

        user = None if _sso_conflict else _svc.authenticate(form.email.data, form.password.data)
        if user is not None:
            # R1-B12 PR 2 (TB-0144/PB-0100): an administrator must complete
            # multi-factor before the login finishes, whether they are
            # enrolling for the first time or entering a code from an
            # already-enrolled authenticator app. Checked before the
            # session-fixation reset below so a password alone never mints
            # a real session for an administrator account. Mirrors the v1
            # account_routes.py login() gate exactly -- USE_ACCOUNT_GUARDRAILS
            # chooses which of the two is registered, so both must agree.
            from app.services import mfa_service

            mfa_service.start_window_if_due(user)
            if mfa_service.required_for(user) and not mfa_service.may_defer_enrolment(user):
                session["_mfa_pending_user_id"] = user.id
                session["_mfa_pending_remember"] = bool(form.remember_me.data)
                session["_mfa_pending_next"] = buy_intent.next_candidate(consume=True) or ""
                return redirect(url_for("account.mfa_challenge"))

            # Fix Session Fixation: Regenerate session ID after successful authentication
            # Read before the session is cleared: the chosen plan lives in it.
            _landing = buy_intent.next_candidate(consume=True)
            # The sign-in call resets the session itself (and finishes a pending Google or Microsoft connection first).
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
                safe_next_url(_landing, url_for("dashboard.overview"))
            )
        else:
            if _sso_conflict:
                flash(
                    "Sign-in for this email domain is unavailable because it is claimed by "
                    "more than one organisation. Contact your administrator.",
                    "form-error",
                )
            try:
                audit_logger.log_authentication(success=False)
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — audit log is fire-and-forget
                pass
            # V-06: failed-login monitoring needs the attempt in the surfaced
            # audit table, attributed to the account it targeted where one exists.
            from app.services import auth_audit

            auth_audit.record_login_failure(form.email.data)
            if not _sso_conflict:
                flash("Invalid email or password.", "form-error")
    return render_template("account/login.html", form=form)


def _mfa_pending_user():
    """Return the User this request is mid-MFA for, or None.

    Mirrors the v1 account_routes.py helper exactly -- see its docstring.
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
    method = session.pop("_mfa_pending_method", None) or "password"

    _svc.login(user, remember)
    session.permanent = True
    try:
        audit_logger.log_authentication(success=True)
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — audit log is fire-and-forget
        pass
    _login_entry = auth_audit.record_login_success(user, method=method)
    if _login_entry is not None:
        session["_login_audit_id"] = _login_entry.id
    flash("You are now logged in. Welcome back!", "success")

    from app.utils.safe_redirect import safe_next_url

    return redirect(safe_next_url(next_url, url_for("dashboard.overview")))


@account_bp_v2.route("/mfa-challenge", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))  # SECURITY: brute-force protection on code submits
@timed_route
def mfa_challenge():
    """Multi-factor step for an administrator mid-login (R1-B12 PR 2,
    TB-0144/PB-0100). Mirrors the v1 account_routes.py route exactly --
    see its docstring.
    """
    user = _mfa_pending_user()
    if user is None:
        flash("Your sign-in attempt expired. Please sign in again.", "error")
        return redirect(url_for("account.login"))

    from app.modules.account.mfa_flow import run_mfa_challenge

    return run_mfa_challenge(user, _complete_login_after_mfa)


@account_bp_v2.route("/mfa-enrol", methods=["GET", "POST"])
@login_required
@rate_limit(10, "1m", methods=("POST",))  # SECURITY: brute-force protection on code submits
@timed_route
def mfa_enrol():
    """Set up two-factor sign-in from a signed-in session (the first-week
    notice, an action that needs it, or a closed first week)."""
    from app.modules.account.mfa_flow import run_signed_in_enrolment

    return run_signed_in_enrolment()


@account_bp_v2.route("/register", methods=["GET", "POST"])
@rate_limit(5, "1h", methods=("POST",))  # SECURITY: sign-up is tighter than the generic write limit: 5 POSTs an hour per address
@timed_route
def register():
    """Register a new user, and send them a confirmation email."""
    return mail_views.register_view()


def _company_card():
    from app.modules.account.services import onboarding_service

    org = getattr(current_user, "organization", None)
    return onboarding_service.settings_card(org) if org is not None else None


# Sign-up after the account: bring the company in, then choose the AI provider.
from app.modules.admin.connector_routes import connections_access as _connections_access  # noqa: E402
from app.modules.account.routes import onboarding_views  # noqa: E402

onboarding_views.add_routes(account_bp_v2, _connections_access)


@account_bp_v2.route("/logout", methods=["GET", "POST"])
@login_required
@timed_route
def logout():
    """Log out the current user."""
    if request.method == "GET":
        # A link, prefetch or crawler fetching this URL must not end a session:
        # signing out is a POST with the form's token.
        from flask_wtf import FlaskForm

        return render_template("account/logout_confirm.html", form=FlaskForm())
    try:
        audit_logger.log_logout()
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — audit log is fire-and-forget
        pass
    # V-06: logout is a session-forensics event too; record it in the surfaced
    # audit table while current_user is still resolvable.
    from app.services import auth_audit

    auth_audit.record_logout(current_user if current_user.is_authenticated else None)
    _svc.logout()
    return redirect(url_for("account.login", timeout="signed_out"))


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
        company=_company_card(),
        last_login_entry=last_login_entry,
        recent_auth_events=auth_audit.recent_auth_events(current_user.id),
    )


@account_bp_v2.route("/switch-organization", methods=["POST"])
@login_required
@timed_route
def switch_organization():
    """Switch the signed-in user's active organisation."""
    success, message = _svc.switch_active_organization(
        current_user, request.form.get("organization_id", type=int)
    )
    flash(message, "success" if success else "error")
    return redirect(url_for("account.manage"))


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
@rate_limit(5, "1m", methods=("POST",))  # SECURITY: each POST can send mail
@timed_route
def reset_password_request():
    """Respond to existing user's request to reset their password."""
    return mail_views.reset_request_view()


@account_bp_v2.route("/reset-password/<token>", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))
@timed_route
def reset_password(token):
    """Reset an existing user's password."""
    return mail_views.reset_view(token)


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


@account_bp_v2.route("/confirm-account", methods=["GET", "POST"])
@login_required
@rate_limit(3, "1m", methods=("POST",))  # SECURITY: each POST sends mail
@timed_route
def confirm_request():
    """Respond to new user's request to confirm their account."""
    return mail_views.confirm_request_view()


@account_bp_v2.route("/confirm-account/<token>")
@timed_route
def confirm(token):
    """Confirm new user's account with provided token."""
    return mail_views.confirm_view(token)


@account_bp_v2.route("/join/<token>", methods=["GET", "POST"])
@rate_limit(10, "1m", methods=("POST",))
@timed_route
def join(token):
    """Accept an e-mailed invitation into an organisation by setting a password."""
    return mail_views.join_view(token)


@account_bp_v2.route("/join-from-invite/<int:user_id>/<token>", methods=["GET", "POST"])
@rate_limit(10, "1m")
@timed_route
def join_from_invite(user_id, token):
    """Retired invitation link: it is served by the one invitation flow.

    Links of this shape carried a reusable signed token that was never
    stored. They set no password and send no mail any more; the token is
    handed to ``/join/<token>``, which refuses anything it did not issue.
    """
    return redirect(url_for("account.join", token=token))


@account_bp_v2.route("/invitation/<int:invitation_id>/accept", methods=["POST"])
@login_required
@timed_route
def accept_invitation(invitation_id):
    """Accept a pending invitation and gain the offered role."""
    success, message = _svc.accept_invitation(current_user, invitation_id)
    flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@account_bp_v2.route("/invitation/<int:invitation_id>/decline", methods=["POST"])
@login_required
@timed_route
def decline_invitation(invitation_id):
    """Decline a pending invitation — no role is granted."""
    success, message = _svc.decline_invitation(current_user, invitation_id)
    flash(message, "success" if success else "error")
    return redirect(url_for("main.index"))


@account_bp_v2.before_app_request
def before_request():
    """Force user to confirm email before accessing login-required routes."""
    return redirect_held_account()


@account_bp_v2.route("/manage/preferences", methods=["POST"])
@login_required
@timed_route
def save_preferences():
    """Save user preferences (notifications and display) for the current user."""
    from app import db

    form_type = request.form.get("form_type", "")
    known_keys = [
        "arb_decisions",
        "solution_updates",
        "assignment_changes",
        "weekly_digest",
        "alert_email",
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


@account_bp_v2.route("/unconfirmed")
@timed_route
def unconfirmed():
    """Catch users with unconfirmed emails."""
    return mail_views.unconfirmed_view()
