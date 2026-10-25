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


def run_mfa_challenge(user, complete):
    """Answer one request on the challenge page for ``user``.

    ``complete(user)`` finishes the sign-in that was held open.

    * Not enrolled: one screen with the QR code and a code field; a right
      code turns two-factor on and shows the eight recovery codes once.
    * Just enrolled, confirming the codes were saved: finishes the sign-in.
    * Enrolled: a six-digit code, or (``use_recovery``) one unused recovery
      code, which stops working as soon as it is accepted.
    """
    from app.services import activity_record, mfa_service

    if request.method == "POST" and user.mfa_enabled and request.form.get("step") == "codes":
        # The codes are stored hashed and cannot be shown again, so this only
        # confirms they were saved and finishes the sign-in.
        if request.form.get("recovery_confirmed"):
            return complete(user)
        flash("Tick the box to confirm you have saved the recovery codes.", "form-error")
        return redirect(url_for("account.mfa_challenge"))

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
                    "account/mfa_enroll.html", step="codes", recovery_codes=recovery_codes
                )
        return render_template(
            "account/mfa_enroll.html",
            step="scan",
            secret=secret,
            provisioning_uri=uri,
            qr_data_uri=_qr_data_uri(uri),
            error=error,
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
