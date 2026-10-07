"""
SSO federation routes (COM-005, R1-B12 PR 2).

Blueprint: ``sso``

Routes
------
GET  /auth/sso/initiate?email=...    Look up domain config; redirect to IdP.
GET  /auth/sso/callback/oidc         Handle OIDC callback; provision user; login.
POST /auth/sso/callback/saml         Handle SAML Response; provision user; login.
GET  /admin/sso                      Show SSO config form (admin only).
POST /admin/sso                      Save SSO config (admin only).
POST /admin/sso/test                 Test the saved config without enforcing it (admin only).
"""

import logging

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user

from app.decorators import admin_required
from app.extensions import db
from app.services.billing_plans import PlanLimitReached
from app.services.sso_service import SSONotConfiguredError, SSOService

_log = logging.getLogger(__name__)

sso_bp = Blueprint("sso", __name__)
_svc = SSOService()


def _base_url() -> str:
    """This platform's own base URL, for building SAML SP entity ids and
    ACS URLs without a request context's scheme/host being stale behind a
    proxy (``X-Forwarded-*`` is already trusted by the app's proxy-fix
    middleware, so ``request.url_root`` reflects the real public origin)."""
    return request.url_root.rstrip("/")


# ---------------------------------------------------------------------------
# Public SSO initiation & callback
# ---------------------------------------------------------------------------


@sso_bp.route("/auth/sso/initiate")
def sso_initiate():
    """Initiate SSO login for the given email address.

    Query params:
        email (str): The user's email address.

    On success redirects to the IdP. On misconfiguration returns JSON error.
    """
    email = request.args.get("email", "").strip()
    if not email:
        return jsonify({"error": "email parameter required"}), 400

    config = _svc.get_config_for_email(email)
    if config is None or not config.enabled:
        return jsonify({"error": "No SSO configured for this email domain"}), 404

    return _begin_sso(config, email, test_mode=False)


def _begin_sso(config, email, *, test_mode: bool):
    """Shared initiation for the real sign-in path and the admin's test
    sign-in (R1-B12 PR 2, TB-0142/PB-0129) -- the same redirect, the same
    IdP round-trip, the same signature/claims verification either way. Only
    the callback's handling of a successful result differs (see
    ``_finish_sso`` below): a real sign-in provisions and logs the user in;
    a test run never does either, it only records what happened.
    """
    try:
        if config.protocol == "oidc":
            redirect_uri = url_for("sso.sso_callback_oidc", _external=True)
            result = _svc.initiate_oidc_flow(config, redirect_uri)
            session["sso_state"] = result["state"]
            session["sso_nonce"] = result["nonce"]
            session["sso_org_id"] = config.organization_id
            session["sso_email"] = email
            session["sso_test_mode"] = test_mode
            return redirect(result["redirect_url"])
        elif config.protocol == "saml":
            result = _svc.initiate_saml_flow(config, _base_url())
            session["saml_request_id"] = result["request_id"]
            session["sso_org_id"] = config.organization_id
            session["sso_email"] = email
            session["sso_test_mode"] = test_mode
            return redirect(result["redirect_url"])
        else:
            return jsonify({"error": f"Unknown SSO protocol: {config.protocol}"}), 400
    except SSONotConfiguredError as exc:
        _log.warning("SSO initiation failed for %s: %s", email, exc)
        return jsonify({"error": str(exc)}), 503


@sso_bp.route("/auth/sso/callback/oidc")
def sso_callback_oidc():
    """Handle the OIDC callback from the IdP.

    Validates the anti-CSRF ``state``, exchanges the code, provisions the
    user, logs them in, then redirects to /dashboard.
    """
    code = request.args.get("code", "")
    state = request.args.get("state", "")

    # Validate anti-CSRF state. A callback reached without a matching state
    # (no SSO flow was ever initiated in this session, or it was replayed/
    # tampered with) is simply a bad request -- it must not redirect into
    # /account/login, which re-renders the sign-in form and reads as ending
    # whatever session the visitor already has. Answer 400 directly and
    # leave the existing session (its _sid, its Flask-Login state) untouched;
    # nothing above this point has written to it other than the state pop.
    expected_state = session.pop("sso_state", None)
    if not expected_state or expected_state != state:
        return render_template("errors/400.html"), 400

    expected_nonce = session.pop("sso_nonce", None)
    org_id = session.pop("sso_org_id", None)
    session.pop("sso_email", None)
    test_mode = session.pop("sso_test_mode", False)

    try:
        from app.models.organization import Organization

        org = Organization.query.get(org_id) if org_id else None
        if org is None:
            flash("SSO configuration error: organisation not found.", "error")
            return redirect(url_for("account.login"))

        config = org.sso_config
        if config is None or (not config.enabled and not test_mode):
            flash("SSO is not enabled for this organisation.", "error")
            return redirect(url_for("account.login"))

        redirect_uri = url_for("sso.sso_callback_oidc", _external=True)
        userinfo = _svc.handle_oidc_callback(
            config, code, state, redirect_uri, expected_nonce=expected_nonce
        )
        return _finish_sso(config, org, userinfo, test_mode=test_mode)

    except SSONotConfiguredError as exc:
        _log.error("OIDC callback failed: %s", exc)
        if test_mode:
            return _record_test_result(org_id, "failure", str(exc))
        flash(f"SSO login failed: {exc}", "error")
        return redirect(url_for("account.login"))
    except PlanLimitReached as exc:
        # Just-in-time provisioning of a new person into a full plan: nothing
        # is saved and the person is told why, rather than "unexpected error".
        db.session.rollback()
        _log.info("OIDC provisioning refused by plan limit for org %s", org_id)
        flash(f"Your account could not be created. {exc}", "error")
        return redirect(url_for("account.login"))
    except Exception:
        _log.exception("Unexpected error during OIDC callback")
        if test_mode:
            return _record_test_result(org_id, "failure", "An unexpected error occurred.")
        flash("SSO login failed due to an unexpected error.", "error")
        return redirect(url_for("account.login"))


@sso_bp.route("/auth/sso/callback/saml", methods=["POST"])
def sso_callback_saml():
    """Handle the SAML 2.0 Response POSTed back by the IdP (R1-B12 PR 2,
    TB-0141/PB-0017).

    Verifies the assertion's signature against the organisation's
    configured IdP certificate, provisions the user, logs them in, then
    redirects to /dashboard. A verification failure answers 400 -- the
    request itself (an unsigned, expired, wrong-audience or forged
    assertion) is the thing that is invalid, not the server.
    """
    saml_response = request.form.get("SAMLResponse", "")
    expected_request_id = session.pop("saml_request_id", None)
    org_id = session.pop("sso_org_id", None)
    session.pop("sso_email", None)
    test_mode = session.pop("sso_test_mode", False)

    try:
        from app.models.organization import Organization

        org = Organization.query.get(org_id) if org_id else None
        if org is None:
            flash("SSO configuration error: organisation not found.", "error")
            return redirect(url_for("account.login"))

        config = org.sso_config
        if config is None or config.protocol != "saml" or (not config.enabled and not test_mode):
            flash("SAML SSO is not enabled for this organisation.", "error")
            return redirect(url_for("account.login"))

        claims = _svc.handle_saml_callback(
            config, saml_response, _base_url(), expected_request_id=expected_request_id
        )
        return _finish_sso(config, org, claims, test_mode=test_mode)

    except SSONotConfiguredError as exc:
        _log.error("SAML callback failed: %s", exc)
        if test_mode:
            return _record_test_result(org_id, "failure", str(exc))
        flash(f"SSO login failed: {exc}", "error")
        return render_template("errors/400.html"), 400
    except PlanLimitReached as exc:
        db.session.rollback()
        _log.info("SAML provisioning refused by plan limit for org %s", org_id)
        flash(f"Your account could not be created. {exc}", "error")
        return redirect(url_for("account.login"))
    except Exception:
        _log.exception("Unexpected error during SAML callback")
        if test_mode:
            return _record_test_result(org_id, "failure", "An unexpected error occurred.")
        flash("SSO login failed due to an unexpected error.", "error")
        return redirect(url_for("account.login"))


def _finish_sso(config, org, claims, *, test_mode: bool):
    """Shared post-verification step for both OIDC and SAML, and for both
    the real sign-in path and the admin's test sign-in (R1-B12 PR 2):

    - Real sign-in (test_mode=False): provision the user and log them in,
      with multi-factor gating an administrator's login exactly like the
      password path (see app.modules.account.routes.account_routes).
    - Test sign-in (test_mode=True): never provisions or logs anyone in --
      only records that the IdP returned a verified response and what
      claims it carried, then returns to the admin settings page.
    """
    if test_mode:
        preview = {k: v for k, v in claims.items() if k in ("email", "sub", "given_name", "family_name")}
        resp = _record_test_result(org.id, "success", "The identity provider returned a verified response.", preview)
        return resp

    user = _svc.provision_user(org, claims)

    from app.services import mfa_service, session_registry

    if not user.is_active:
        # R1-B26: a deactivated user is refused before any MFA step or session.
        flash(session_registry.INACTIVE_ACCOUNT_MESSAGE, "error")
        return redirect(url_for("account.login"))

    if mfa_service.required_for(user):
        session["_mfa_pending_user_id"] = user.id
        session["_mfa_pending_remember"] = False
        return redirect(url_for("account.mfa_challenge"))

    if not session_registry.login_and_register(user):
        flash(session_registry.INACTIVE_ACCOUNT_MESSAGE, "error")
        return redirect(url_for("account.login"))
    return redirect(url_for("dashboard.overview"))


def _record_test_result(org_id, status: str, message: str, claims_preview: dict | None = None):
    """Write a test sign-in's outcome onto the organisation's SSOConfig and
    return to the admin settings page with a flash summarising it."""
    from datetime import datetime, timezone

    from app.models.sso_config import SSOConfig

    config = SSOConfig.query.filter_by(organization_id=org_id).first() if org_id else None
    if config is not None:
        result = {
            "status": status,
            "message": message,
            "tested_at": datetime.now(timezone.utc).isoformat(),
        }
        if claims_preview:
            result["claims_preview"] = claims_preview
        config.last_test_result = result
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _log.error("Failed to record SSO test result: %s", exc)

    if status == "success":
        flash("Test sign-in succeeded: the identity provider returned a verified response.", "success")
    else:
        flash(f"Test sign-in failed: {message}", "error")
    return redirect(url_for("sso.admin_sso"))


# ---------------------------------------------------------------------------
# Admin SSO configuration
# ---------------------------------------------------------------------------


@sso_bp.route("/admin/sso", methods=["GET", "POST"])
@admin_required
def admin_sso():
    """Show and save SSO configuration for the current user's organisation."""
    from app import db
    from app.models.sso_config import SSOConfig

    org_id = getattr(current_user, "organization_id", None)
    config = SSOConfig.query.filter_by(organization_id=org_id).first() if org_id else None

    if request.method == "POST":
        protocol = request.form.get("protocol", "oidc")
        email_domain = request.form.get("email_domain", "").strip()
        idp_metadata_url = request.form.get("idp_metadata_url", "").strip()
        client_id = request.form.get("client_id", "").strip()
        client_secret_raw = request.form.get("client_secret", "").strip()
        idp_sso_url = request.form.get("idp_sso_url", "").strip()
        idp_x509_cert = request.form.get("idp_x509_cert", "").strip()
        sp_entity_id = request.form.get("sp_entity_id", "").strip()
        enabled = request.form.get("enabled") == "on"

        # Validate before creating or changing a record: a rejected save must
        # leave the organisation's existing login configuration intact.
        if protocol not in {"oidc", "saml"}:
            flash("This SSO protocol is not supported. Choose OIDC or SAML.", "error")
            return redirect(url_for("sso.admin_sso"))
        if protocol == "saml" and enabled and not (idp_sso_url and idp_x509_cert):
            flash(
                "SAML SSO needs an IdP SSO URL and signing certificate "
                "before it can be enabled.",
                "error",
            )
            return redirect(url_for("sso.admin_sso"))

        if config is None:
            if not org_id:
                flash("Cannot save SSO config: no organisation associated.", "error")
                return redirect(url_for("sso.admin_sso"))
            config = SSOConfig(organization_id=org_id)
            db.session.add(config)

        config.protocol = protocol
        config.email_domain = email_domain
        config.idp_metadata_url = idp_metadata_url
        config.client_id = client_id
        if client_secret_raw:
            config.client_secret = client_secret_raw
        config.idp_sso_url = idp_sso_url or None
        config.idp_x509_cert = idp_x509_cert or None
        config.sp_entity_id = sp_entity_id or None
        config.enabled = enabled

        try:
            db.session.commit()
            flash("SSO configuration saved.", "success")
        except Exception as exc:
            db.session.rollback()
            _log.error("Failed to save SSO config: %s", exc)
            flash("Failed to save SSO configuration.", "error")

        return redirect(url_for("sso.admin_sso"))

    return render_template("admin/sso.html", config=config)


@sso_bp.route("/admin/sso/test", methods=["GET"])
@admin_required
def admin_sso_test_initiate():
    """Test the saved configuration without enforcing it (R1-B12 PR 2,
    TB-0142/PB-0129): the same redirect to the real IdP and the same
    signature/claims verification as a real sign-in, via ``_begin_sso`` /
    ``_finish_sso``'s shared ``test_mode`` branch -- but the matching
    callback never provisions a user or signs anyone in, it only records
    what the IdP returned on the config row and sends the administrator
    back here to see it. Available even while the config is disabled
    (``enabled=False``), which is the whole point of testing before
    enforcing it for every user with a matching email domain.
    """
    from app.models.sso_config import SSOConfig

    org_id = getattr(current_user, "organization_id", None)
    config = SSOConfig.query.filter_by(organization_id=org_id).first() if org_id else None
    if config is None:
        flash("Save an SSO configuration before testing it.", "error")
        return redirect(url_for("sso.admin_sso"))

    test_email = f"test@{config.email_domains[0]}" if config.email_domains else current_user.email
    # _begin_sso answers a JSON 503 itself on a setup error (missing
    # client_id/idp_sso_url etc.) -- there is nothing further to catch here.
    return _begin_sso(config, test_email, test_mode=True)
