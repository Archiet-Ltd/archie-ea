"""The two-factor challenge page, shared by both account route modules so
there is one implementation of enrolment, the sign-in code and the recovery
code (ADR 0008)."""

from __future__ import annotations

import base64
import io

from flask import flash, redirect, render_template, request, session, url_for


def _qr_data_uri(provisioning_uri: str) -> str:
    import qrcode

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def run_mfa_challenge(user, complete, form_action=None, context=None):
    """Answer one request on the challenge page for ``user``.

    ``complete(user)`` finishes the sign-in that was held open.

    * Not enrolled: one screen with the QR code and a code field; a right
      code turns two-factor on and shows the eight recovery codes once.
    * Just enrolled, confirming the codes were saved: finishes the sign-in.
    * Enrolled: a six-digit code, or (``use_recovery``) one unused recovery
      code, which stops working as soon as it is accepted.
    """
    from app.services import activity_record, mfa_service

    form_action = form_action or url_for("account.mfa_challenge")
    context = context or {}

    if request.method == "POST" and user.mfa_enabled and request.form.get("step") == "codes":
        # The codes are stored hashed and cannot be shown again, so this only
        # confirms they were saved and finishes the sign-in.
        if request.form.get("recovery_confirmed"):
            return complete(user)
        flash("Tick the box to confirm you have saved the recovery codes.", "form-error")
        return redirect(form_action)

    if not user.mfa_enabled:
        secret = session.get("_mfa_enroll_secret")
        if not secret:
            secret = mfa_service.generate_secret()
            session["_mfa_enroll_secret"] = secret
        uri = mfa_service.provisioning_uri(user, secret)
        error = None
        if request.method == "POST":
            try:
                recovery_codes = mfa_service.enroll(user, secret, request.form.get("code", ""))
            except mfa_service.MFAError as exc:
                error = str(exc)
            else:
                session.pop("_mfa_enroll_secret", None)
                return render_template(
                    "account/mfa_enroll.html", step="codes", recovery_codes=recovery_codes,
                    form_action=form_action, **context,
                )
        return render_template(
            "account/mfa_enroll.html",
            step="scan",
            secret=secret,
            provisioning_uri=uri,
            qr_data_uri=_qr_data_uri(uri),
            error=error,
            form_action=form_action,
            **context,
        )

    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        if request.form.get("use_recovery"):
            if mfa_service.use_recovery_code(user, code):
                activity_record.record_security_event("recovery_code_used", user)
                return complete(user)
        elif mfa_service.verify_login_code(user, code):
            activity_record.record_security_event("two_factor_passed", user)
            return complete(user)
        activity_record.record_security_event(
            "two_factor_failed", user, method="recovery code" if request.form.get("use_recovery") else "authenticator code"
        )
        error = "That code was not accepted. Try again."

    return render_template(
        "account/mfa_challenge.html", error=error, use_recovery=bool(request.form.get("use_recovery") or request.args.get("recovery"))
    )


# -- enrolment from a signed-in session ---------------------------------------------------------

RETURN_KEY = "_mfa_return_to"
TRIGGER_KEY = "_mfa_trigger"

# What the person was about to do, in the sentence the enrolment page opens with.
TRIGGER_LINES = {
    "invite": "Before you invite someone, set up two-factor sign-in. You'll come straight back here.",
    "credential": "Before you create an API credential, set up two-factor sign-in. You'll come straight back here.",
    "connector": "Before you change a connected source, set up two-factor sign-in. You'll come straight back here.",
    "sso": "Before you set up single sign-on, set up two-factor sign-in. You'll come straight back here.",
    "billing": "Before you change billing, set up two-factor sign-in. You'll come straight back here.",
    "require": "Before you change the two-factor rule, set up two-factor sign-in. You'll come straight back here.",
    "notice": "Set up two-factor sign-in now. It takes a minute.",
}


def send_to_enrolment(trigger, back):
    """Remember where the person was going and send them to enrol first."""
    from app.utils.safe_redirect import safe_next_url

    session[RETURN_KEY] = safe_next_url(back, url_for("dashboard.overview"))
    session[TRIGGER_KEY] = trigger
    return redirect(url_for("account.mfa_enrol"))


def enrol_first(trigger, back_endpoint=None):
    """Send an administrator who has not enrolled to enrol before this action,
    and back to ``back_endpoint`` (or this same page) afterwards. One decorator
    on every route that reaches beyond a new workspace's sole owner."""
    from functools import wraps

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from flask_login import current_user

            from app.services import mfa_service

            if current_user.is_authenticated and mfa_service.step_up_needed(current_user):
                back = url_for(back_endpoint) if back_endpoint else request.full_path.rstrip("?")
                return send_to_enrolment(trigger, back)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def run_signed_in_enrolment():
    """The enrolment page for a person who is already signed in."""
    from flask_login import current_user

    from app.services import mfa_service
    from app.utils.safe_redirect import safe_next_url

    if not current_user.is_authenticated:
        return redirect(url_for("account.login"))
    user = current_user._get_current_object()
    if not mfa_service.required_for(user) or (
        user.mfa_enabled and request.form.get("step") != "codes"
    ):
        return redirect(url_for("dashboard.overview"))

    def finish(done_user):
        target = safe_next_url(session.pop(RETURN_KEY, ""), url_for("dashboard.overview"))
        session.pop(TRIGGER_KEY, None)
        flash("Two-factor sign-in is on.", "success")
        return redirect(target)

    if request.method == "GET" and request.args.get("return"):
        session[RETURN_KEY] = safe_next_url(request.args.get("return"), url_for("dashboard.overview"))
        session.pop(TRIGGER_KEY, None)
    trigger = session.get(TRIGGER_KEY)
    open_window = mfa_service.may_defer_enrolment(user)
    context = {
        "reason": TRIGGER_LINES.get(trigger) if trigger else mfa_service.window_closed_reason(user),
        "days_left": mfa_service.window_days_left(user) if open_window else None,
        "later_url": session.get(RETURN_KEY) or url_for("dashboard.overview"),
    }
    return run_mfa_challenge(user, finish, form_action=url_for("account.mfa_enrol"), context=context)
