"""Connector admin routes — the one connectors page and its change path.

Routes in this module are the single in-product surface for connector
configuration. Every connector change reaches the model only as an approval
proposal in the acting organisation's own queue, unless that organisation has
delegated the action type (``Organization.settings`` key
``connector_action_delegation``); a write with neither is refused. All
handlers resolve the organisation from ``g.current_org_id`` (the active
organisation), never the user's home organisation.

- GET  /admin/connectors              — one page listing every connector of
                                        the signed-in organisation with
                                        health, last synchronisation, the
                                        masked credential state and the count
                                        of pending connector proposals
- POST /admin/connectors              — add/update a connector (credential →
                                        per-organisation vault, change →
                                        approval proposal or direct when
                                        delegated)
- POST /admin/connectors/<id>/sync    — propose (or run, when delegated) a
                                        connector sync
- POST /admin/connectors/<id>/delete  — propose (or do, when delegated) a
                                        connector removal

Legacy per-connector forms (M365, Jira, ServiceNow) keep their endpoints for
existing entry points and their save handlers route through the same change
path, so no connector write bypasses the approval queue or the framework.

Credentials live only in the per-organisation vault; configuration rows never
carry a secret, and nothing in this module reads or writes credentials any
other way.
"""

import json as _json
import logging

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.decorators import admin_required
from app.models.connector_config import _is_sensitive_key

logger = logging.getLogger(__name__)

m365_connector_bp = Blueprint("m365_connector", __name__, url_prefix="/admin/connectors")


def _active_org_id():
    """The organisation the request is acting as (``g.current_org_id``),
    never the user's home organisation. Admin authority and every connector
    write are judged in the active organisation."""
    from flask import g

    return getattr(g, "current_org_id", None)


def _require_active_org() -> int:
    org_id = _active_org_id()
    if org_id is None:
        abort(403, "No organisation is active for this request.")
    return org_id


def _permit_connector_type(connector_type: str):
    """Refuse an unpermitted connector type before anything is stored."""
    from app.modules.intelligence.services.connector_allowlist import (
        assert_connector_permitted,
    )

    assert_connector_permitted(connector_type)


# ---------------------------------------------------------------------------
# The one connectors page
# ---------------------------------------------------------------------------


@m365_connector_bp.route("", methods=["GET"])
@login_required
@admin_required
def connectors_index():
    """The one connectors page: every connector of the signed-in
    organisation with health, last synchronisation, the masked credential
    state, and how many connector changes are pending in the organisation's
    approval queue."""
    org_id = _require_active_org()
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.services.connector_framework import (
        CONNECTOR_APPROVAL_ENTITY_TYPE,
        CONNECTOR_TYPE_LABELS,
        list_org_connectors,
        org_has_delegated_action,
    )

    connectors = list_org_connectors(org_id)
    pending_count = AIChatCRUDApproval.query.filter_by(
        organization_id=org_id,
        entity_type=CONNECTOR_APPROVAL_ENTITY_TYPE,
        status=ApprovalStatus.PENDING,
    ).count()

    return render_template(
        "admin/connectors/index.html",
        connectors=connectors,
        pending_count=pending_count,
        connector_type_labels=CONNECTOR_TYPE_LABELS,
        delegated_actions={
            action: org_has_delegated_action(org_id, action)
            for action in ("create", "update", "delete", "sync")
        },
        inbox_url=url_for("unified_ai_chat.approval_inbox"),
    )


@m365_connector_bp.route("", methods=["POST"])
@login_required
@admin_required
def connectors_save():
    """Add or update a connector of the signed-in organisation.

    The credential (if any) is stored in the per-organisation vault
    immediately, encrypted with the organisation's own key, so the change
    can be executed later without the secret ever travelling again. The
    change itself is then queued as an approval proposal in this
    organisation's queue, or applied directly when the organisation has
    delegated the action type. A write without an organisation context is
    refused.
    """
    from app.extensions import db
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_CREATE,
        CONNECTOR_ACTION_UPDATE,
        propose_or_apply_connector_change,
    )

    org_id = _require_active_org()

    connector_type = (request.form.get("connector_type") or "").strip().lower()
    existing_id = (request.form.get("connector_id") or "").strip() or None
    name = (request.form.get("name") or "").strip()
    enabled = request.form.get("enabled") == "1"
    credential = (request.form.get("credential") or "").strip()

    if not connector_type:
        flash("A connector type is required.", "error")
        return redirect(url_for("m365_connector.connectors_index"))

    try:
        _permit_connector_type(connector_type)
    except PermissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("m365_connector.connectors_index"))

    raw_config = (request.form.get("config_json") or "{}").strip()
    try:
        config = _json.loads(raw_config) if raw_config else {}
    except ValueError:
        flash("Configuration must be valid JSON.", "error")
        return redirect(url_for("m365_connector.connectors_index"))
    if not isinstance(config, dict):
        flash("Configuration must be a JSON object.", "error")
        return redirect(url_for("m365_connector.connectors_index"))

    sensitive_keys = [k for k in config if _is_sensitive_key(k)]
    if sensitive_keys:
        flash(
            "Configuration cannot hold a secret ("
            + ", ".join(sorted(sensitive_keys))
            + "). Put it in the credential field instead; it never travels as plain "
            "text in the approval queue the way the configuration box does.",
            "error",
        )
        return redirect(url_for("m365_connector.connectors_index"))

    from app.services.connector_framework import ConnectorURLRefused, validate_connector_config_urls

    try:
        validate_connector_config_urls(config)
    except ConnectorURLRefused as exc:
        flash(str(exc), "error")
        return redirect(url_for("m365_connector.connectors_index"))

    action = CONNECTOR_ACTION_UPDATE if existing_id else CONNECTOR_ACTION_CREATE
    payload = {
        "connector_type": connector_type,
        "connector_id": existing_id,
        "name": name or connector_type,
        "config": config,
        "status": "active" if enabled else "inactive",
        # This route is the only place that knows a credential is part of
        # this change, on the delegated path as much as the queued one --
        # carry that fact in the payload itself rather than having
        # apply_connector_change try to re-derive it from approval/vault
        # state later.
        "credential_provided": bool(credential),
    }
    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=action,
        connector_type=connector_type,
        summary=f"{'Update' if action == CONNECTOR_ACTION_UPDATE else 'Add'} connector "
        f"'{name or connector_type}'",
        payload=payload,
    )
    # M1: a credential must not take effect before the change it belongs to
    # is approved. When this organisation has delegated the action, the
    # change above already applied, so the credential can go straight into
    # the real row like it always did; when it is only queued, the secret
    # is staged under this approval instead and promoted on approval
    # (apply_connector_change) or discarded on rejection (reject_approval).
    if credential:
        vault = OrgCredentialVault()
        if result.get("queued"):
            vault.store_staged(
                org_id, connector_type, "credentials", _json.dumps({"credential": credential}),
                result["approval_id"],
            )
        else:
            vault.store_credentials(org_id, connector_type, {"credential": credential})
    db.session.commit()

    if result.get("queued"):
        flash(
            f"Connector change queued for approval (proposal "
            f"#{result['approval_id']}). It applies once an approver in this "
            f"organisation approves it.",
            "success",
        )
    else:
        flash("Connector saved.", "success")
    return redirect(url_for("m365_connector.connectors_index"))


@m365_connector_bp.route("/<string:connector_id>/sync", methods=["POST"])
@login_required
@admin_required
def connectors_sync(connector_id):
    """Propose a sync for one connector of the signed-in organisation, or run
    it directly when the organisation has delegated the sync action.

    On approval (or delegation) the framework runs the sync, records the
    SyncLog, updates health and last synchronisation, and writes an
    identifier crosswalk link per element touched.
    """
    from app.extensions import db
    from app.services.connector_framework import (
        CONNECTOR_ACTION_SYNC,
        _load_org_connector,
        propose_or_apply_connector_change,
        run_connector_sync,
    )

    org_id = _require_active_org()
    cfg = _load_org_connector(org_id, connector_id=connector_id)
    if cfg is None:
        abort(404, "Connector not found in this organisation.")
    cfg_id = cfg.id

    from app.services.connector_framework import org_has_delegated_action

    if org_has_delegated_action(org_id, CONNECTOR_ACTION_SYNC):
        result = run_connector_sync(org_id=org_id, connector_id=cfg_id, delegated=True)
        db.session.commit()
        if result.get("success"):
            flash(f"Sync completed for '{cfg.name}'.", "success")
        else:
            flash(f"Sync failed: {result.get('error', 'unknown error')}", "error")
        return redirect(url_for("m365_connector.connectors_index"))

    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=CONNECTOR_ACTION_SYNC,
        connector_type=cfg.connector_type,
        summary=f"Run sync for connector '{cfg.name}'",
        payload={"connector_id": cfg_id, "connector_type": cfg.connector_type},
    )
    db.session.commit()
    if result.get("queued"):
        flash(
            f"Sync queued for approval (proposal #{result['approval_id']}). "
            "It runs once an approver in this organisation approves it.",
            "success",
        )
    else:
        flash("Sync completed.", "success")
    return redirect(url_for("m365_connector.connectors_index"))


@m365_connector_bp.route("/<string:connector_id>/delete", methods=["POST"])
@login_required
@admin_required
def connectors_delete(connector_id):
    """Propose removal of one connector, or remove it directly when the
    organisation has delegated the delete action. Removal also clears the
    connector's credentials from the per-organisation vault."""
    from app.extensions import db
    from app.services.connector_framework import (
        CONNECTOR_ACTION_DELETE,
        _load_org_connector,
        propose_or_apply_connector_change,
    )

    org_id = _require_active_org()
    cfg = _load_org_connector(org_id, connector_id=connector_id)
    if cfg is None:
        abort(404, "Connector not found in this organisation.")

    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=CONNECTOR_ACTION_DELETE,
        connector_type=cfg.connector_type,
        summary=f"Delete connector '{cfg.name}'",
        payload={"connector_id": cfg.id, "connector_type": cfg.connector_type},
    )
    db.session.commit()

    if result.get("queued"):
        flash(
            f"Connector removal queued for approval (proposal "
            f"#{result['approval_id']}).",
            "success",
        )
    else:
        flash("Connector removed.", "success")
    return redirect(url_for("m365_connector.connectors_index"))


def current_user_id():
    from flask_login import current_user

    return getattr(current_user, "id", None)


# ---------------------------------------------------------------------------
# COM-017: Microsoft 365 connector (legacy form, same change path)
# ---------------------------------------------------------------------------


@m365_connector_bp.route("/m365", methods=["GET"])
@login_required
@admin_required
def m365_config():
    """Render the M365 connector configuration form for the active org."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import ConnectorConfig

    org_id = _require_active_org()
    cfg = (
        ConnectorConfig.query.filter_by(organization_id=org_id, connector_type="m365").first()
        or {}
    )
    cfg_data = (cfg.config or {}) if cfg else {}
    has_secret = (
        OrgCredentialVault().get_masked(org_id, "m365", "client_secret") is not None
    )
    return render_template(
        "admin/connectors/m365.html", cfg=cfg_data, has_secret=has_secret
    )


@m365_connector_bp.route("/m365", methods=["POST"])
@login_required
@admin_required
def m365_config_save():
    """Persist M365 connector configuration through the change path.

    The client secret goes to the per-organisation vault; the change itself
    is queued as an approval proposal (or applied directly when the org has
    delegated the action type)."""
    from app.extensions import db
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_UPDATE,
        _load_org_connector,
        propose_or_apply_connector_change,
    )

    org_id = _require_active_org()
    form = request.form
    tenant_id = (form.get("tenant_id") or "").strip()
    client_id = (form.get("client_id") or "").strip()
    client_secret_raw = (form.get("client_secret") or "").strip()
    site_id = (form.get("sharepoint_site_id") or "").strip()
    folder_path = (form.get("sharepoint_folder_path") or "").strip()
    teams_webhook_url = (form.get("teams_webhook_url") or "").strip()
    enabled = form.get("enabled") == "1"

    try:
        _permit_connector_type("m365")
    except PermissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("m365_connector.m365_config"))

    if teams_webhook_url:
        from app.services.m365_service import validate_teams_webhook_url

        try:
            validate_teams_webhook_url(teams_webhook_url)
        except ValueError:  # BlockedOutboundURL
            flash(
                "The Teams webhook address must be a Microsoft Teams incoming "
                "webhook (https).",
                "error",
            )
            return redirect(url_for("m365_connector.m365_config"))

    existing = _load_org_connector(org_id, connector_type="m365")
    action = CONNECTOR_ACTION_UPDATE if existing else "create"
    payload = {
        "connector_type": "m365",
        "name": "Microsoft 365",
        "description": "SharePoint blueprint export and Teams ARB notifications",
        "config": {
            "tenant_id": tenant_id,
            "client_id": client_id,
            "site_id": site_id,
            "folder_path": folder_path,
            "teams_webhook_url": teams_webhook_url,
            "enabled": enabled,
        },
        "status": "active" if enabled else "inactive",
        "credential_provided": bool(client_secret_raw),
    }
    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=action,
        connector_type="m365",
        summary="Update Microsoft 365 connector configuration" if existing
        else "Add Microsoft 365 connector",
        payload=payload,
    )
    # M1: stage the secret under this approval until it is genuinely
    # approved, unless the organisation delegates (already applied above).
    if client_secret_raw:
        vault = OrgCredentialVault()
        if result.get("queued"):
            vault.store_staged(org_id, "m365", "client_secret", client_secret_raw, result["approval_id"])
        else:
            vault.store(org_id=org_id, connector_type="m365", credential_type="client_secret", value=client_secret_raw)
    db.session.commit()

    if result.get("queued"):
        flash(
            "M365 configuration change queued for approval "
            f"(proposal #{result['approval_id']}).",
            "success",
        )
    else:
        flash("M365 configuration saved successfully.", "success")
    return redirect(url_for("m365_connector.m365_config"))


@m365_connector_bp.route("/m365/test", methods=["POST"])
@login_required
@admin_required
def m365_test():
    """Test M365 OAuth2 authentication and return JSON status.

    Reads the stored connector row of the active organisation. Legacy rows
    carry the encrypted secret on the row itself; the legacy service is left
    to them — new configuration proposals keep the secret in the per-
    organisation vault.
    """
    from app.services.connector_framework import ConnectorConfig
    from app.services.m365_service import M365Service, _token_cache

    org_id = _require_active_org()
    cfg = (
        ConnectorConfig.query.filter_by(organization_id=org_id, connector_type="m365").first()
    )
    if not cfg:
        return jsonify({
            "status": "error",
            "message": "No M365 configuration found. Save the config first.",
        })

    # Evict cached token to force a fresh fetch
    _token_cache.pop(cfg.id, None)

    svc = M365Service()
    token = svc._get_token(cfg)
    if token:
        return jsonify({
            "status": "ok",
            "message": "Authentication successful — token acquired from Microsoft.",
        })
    return jsonify({
        "status": "error",
        "message": "Failed to acquire token. Check tenant ID, client ID, and client secret.",
    })


# ---------------------------------------------------------------------------
# COM-008: ServiceNow CMDB connector (org-scoped, same change path)
# ---------------------------------------------------------------------------


@m365_connector_bp.route("/servicenow", methods=["GET"])
@login_required
@admin_required
def servicenow_config():
    """Display the ServiceNow connector configuration form for the active org."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import _load_org_connector

    org_id = _require_active_org()
    config = _load_org_connector(org_id, connector_type="servicenow")
    cfg = (config.config or {}) if config else {}
    has_secret = (
        OrgCredentialVault().get_masked(org_id, "servicenow", "client_secret") is not None
    )
    return render_template(
        "admin/connectors/servicenow.html",
        sn_config=config,
        cfg=cfg,
        has_secret=has_secret,
    )


@m365_connector_bp.route("/servicenow", methods=["POST"])
@login_required
@admin_required
def servicenow_config_save():
    """Save ServiceNow connector configuration through the change path.

    Non-secret settings land on the organisation's ConnectorConfig row when
    the proposal is approved; the client secret goes to the per-organisation
    vault at proposal time."""
    from app.extensions import db
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_UPDATE,
        _load_org_connector,
        propose_or_apply_connector_change,
    )

    org_id = _require_active_org()
    instance_url = (request.form.get("instance_url") or "").strip()
    client_id = (request.form.get("client_id") or "").strip()

    secret = (request.form.get("client_secret") or "").strip()

    raw_mapping = (request.form.get("field_mapping") or "{}").strip()
    try:
        mapping = _json.loads(raw_mapping)
    except ValueError:
        flash("field_mapping must be valid JSON.", "warning")
        mapping = {}

    ci_filter = (request.form.get("ci_query_filter") or "").strip()
    if ci_filter:
        mapping["ci_query_filter"] = ci_filter

    enabled = request.form.get("enabled") == "on"

    from app.services.connector_framework import ConnectorURLRefused, validate_connector_url

    try:
        instance_url = validate_connector_url(instance_url) if instance_url else ""
    except ConnectorURLRefused as exc:
        flash(str(exc), "error")
        return redirect(url_for("m365_connector.servicenow_config"))
    existing = _load_org_connector(org_id, connector_type="servicenow")
    action = CONNECTOR_ACTION_UPDATE if existing else "create"
    payload = {
        "connector_type": "servicenow",
        "name": "ServiceNow CMDB",
        "config": {
            "instance_url": instance_url,
            "client_id": client_id,
            "field_mapping": mapping,
            "enabled": enabled,
        },
        "status": "active" if enabled else "inactive",
        "credential_provided": bool(secret),
    }
    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=action,
        connector_type="servicenow",
        summary="Update ServiceNow connector configuration" if existing
        else "Add ServiceNow connector",
        payload=payload,
    )
    # M1: stage the secret under this approval until it is genuinely
    # approved, unless the organisation delegates (already applied above).
    if secret:
        vault = OrgCredentialVault()
        if result.get("queued"):
            vault.store_staged(org_id, "servicenow", "client_secret", secret, result["approval_id"])
        else:
            vault.store(org_id=org_id, connector_type="servicenow", credential_type="client_secret", value=secret)
    db.session.commit()

    if result.get("queued"):
        flash(
            "ServiceNow connector configuration change queued for approval "
            f"(proposal #{result['approval_id']}).",
            "success",
        )
    else:
        flash("ServiceNow connector configuration saved.", "success")
    return redirect(url_for("m365_connector.servicenow_config"))


@m365_connector_bp.route("/servicenow/sync", methods=["POST"])
@login_required
@admin_required
def servicenow_sync():
    """Route a ServiceNow sync through the organisation's approval queue (or
    run it directly when the org has delegated the sync action)."""
    from app.extensions import db
    from app.services.connector_framework import (
        CONNECTOR_ACTION_SYNC,
        _load_org_connector,
        org_has_delegated_action,
        propose_or_apply_connector_change,
        run_connector_sync,
    )

    org_id = _require_active_org()
    cfg = _load_org_connector(org_id, connector_type="servicenow")
    if cfg is None:
        return jsonify({"error": "ServiceNow connector is not configured."}), 404

    if org_has_delegated_action(org_id, CONNECTOR_ACTION_SYNC):
        result = run_connector_sync(org_id=org_id, connector_id=cfg.id, delegated=True)
        db.session.commit()
        return jsonify(result), 202

    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=CONNECTOR_ACTION_SYNC,
        connector_type="servicenow",
        summary="Run ServiceNow CMDB sync",
        payload={"connector_id": cfg.id, "connector_type": "servicenow"},
    )
    db.session.commit()
    if result.get("queued"):
        return jsonify(
            {
                "status": "queued_for_approval",
                "message": "Sync queued for approval.",
                "approval_id": result["approval_id"],
            }
        ), 202
    return jsonify(result), 202


@m365_connector_bp.route("/servicenow/status", methods=["GET"])
@login_required
@admin_required
def servicenow_status():
    """Return JSON status for the ServiceNow connector of the active org."""
    from app.services.connector_framework import _load_org_connector

    org_id = _require_active_org()
    config = _load_org_connector(org_id, connector_type="servicenow")

    if config is None:
        return jsonify({"enabled": False, "last_sync_at": None, "status": "not_configured"})

    enabled = bool((config.config or {}).get("enabled", False))
    return jsonify(
        {
            "enabled": enabled,
            "last_sync_at": (
                config.last_sync.isoformat() if config.last_sync else None
            ),
            "status": "active" if enabled else "disabled",
        }
    )


# ---------------------------------------------------------------------------
# COM-009: Jira connector (legacy form, same change path)
# ---------------------------------------------------------------------------


def _get_jira_config(org_id: int):
    """Return the active org's Jira ConnectorConfig row, or None."""
    from app.services.connector_framework import ConnectorConfig

    return ConnectorConfig.query.filter_by(
        organization_id=org_id, connector_type="jira"
    ).first()


@m365_connector_bp.route("/jira", methods=["GET"])
@login_required
@admin_required
def jira_config():
    """Display the Jira connector configuration form for the active org."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    org_id = _require_active_org()
    connector = _get_jira_config(org_id)
    cfg = (connector.config or {}) if connector else {}
    has_secret = (
        OrgCredentialVault().get_masked(org_id, "jira", "api_token") is not None
    )
    return render_template(
        "admin/connectors/jira.html", connector=connector, cfg=cfg, has_secret=has_secret
    )


@m365_connector_bp.route("/jira", methods=["POST"])
@login_required
@admin_required
def jira_config_save():
    """Persist Jira connector configuration through the change path.

    The API token goes to the per-organisation vault; the change itself is
    queued as an approval proposal (or applied directly when the org has
    delegated the action type)."""
    from app.extensions import db
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_UPDATE,
        propose_or_apply_connector_change,
    )

    org_id = _require_active_org()
    instance_url = (request.form.get("instance_url") or "").strip().rstrip("/")
    email = (request.form.get("email") or "").strip()
    api_token_raw = (request.form.get("api_token") or "").strip()
    default_project_key = (request.form.get("default_project_key") or "ARCH").strip().upper()
    enabled = request.form.get("enabled") == "on"

    if not instance_url or not email:
        flash("Instance URL and email are required.", "error")
        return redirect(url_for("m365_connector.jira_config"))

    from app.services.connector_framework import ConnectorURLRefused, validate_connector_url

    try:
        instance_url = validate_connector_url(instance_url)
    except ConnectorURLRefused as exc:
        flash(str(exc), "error")
        return redirect(url_for("m365_connector.jira_config"))

    try:
        _permit_connector_type("jira")
    except PermissionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("m365_connector.jira_config"))

    existing = _get_jira_config(org_id)
    action = CONNECTOR_ACTION_UPDATE if existing else "create"
    payload = {
        "connector_type": "jira",
        "name": "Jira ALM Connector",
        "description": "Bidirectional Jira integration — ARB epics and backlog import.",
        "config": {
            "instance_url": instance_url,
            "email": email,
            "default_project_key": default_project_key,
            "enabled": enabled,
        },
        "status": "active" if enabled else "inactive",
        "credential_provided": bool(api_token_raw),
    }
    result = propose_or_apply_connector_change(
        org_id=org_id,
        actor_user_id=current_user_id(),
        action=action,
        connector_type="jira",
        summary="Update Jira connector configuration" if existing
        else "Add Jira connector",
        payload=payload,
    )
    # M1: stage the secret under this approval until it is genuinely
    # approved, unless the organisation delegates (already applied above).
    if api_token_raw:
        vault = OrgCredentialVault()
        if result.get("queued"):
            vault.store_staged(org_id, "jira", "api_token", api_token_raw, result["approval_id"])
        else:
            vault.store(org_id=org_id, connector_type="jira", credential_type="api_token", value=api_token_raw)
    db.session.commit()

    if result.get("queued"):
        flash(
            "Jira connector configuration change queued for approval "
            f"(proposal #{result['approval_id']}).",
            "success",
        )
    else:
        flash("Jira connector configuration saved.", "success")
    return redirect(url_for("m365_connector.jira_config"))


@m365_connector_bp.route("/jira/test", methods=["POST"])
@login_required
@admin_required
def jira_config_test():
    """Test Jira credentials by calling /rest/api/3/myself. Returns JSON."""
    from app.services.jira_connector_service import JiraConnectorService

    data = request.get_json() or {}
    instance_url = (data.get("instance_url") or "").strip().rstrip("/")
    email = (data.get("email") or "").strip()
    api_token = (data.get("api_token") or "").strip()

    if not instance_url or not email or not api_token:
        return jsonify(
            {"status": "error", "message": "instance_url, email, and api_token are required."}
        ), 400

    result = JiraConnectorService().test_connection(instance_url, email, api_token)
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code


# ---------------------------------------------------------------------------
# GitHub: connect, first read, review (builds a first model of the organisation)
# ---------------------------------------------------------------------------


def _github_state(org_id: int) -> dict:
    """What the connect page and its status route report: the connection, the latest read and the counts.
    Never the token, only whether one is stored."""
    from app.connectors import github as gh
    from app.models.job import JobStatus
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import _load_org_connector
    from app.services.job_queue_service import get_job_queue_service

    cfg = _load_org_connector(org_id, connector_type="github")
    if cfg is None:
        return {"state": "not_connected", "organization": None, "has_token": False, "counts": {}, "job": None}
    jobs = get_job_queue_service().list_jobs(org_id, limit=1, task=gh.FIRST_SYNC_TASK)
    job = jobs[0] if jobs else None
    state = {
        None: "connected",
        JobStatus.PENDING.value: "waiting",
        JobStatus.IN_PROGRESS.value: "reading",
        JobStatus.COMPLETED.value: "done",
        JobStatus.FAILED.value: "failed",
        JobStatus.CANCELLED.value: "failed",
    }.get(job.status if job else None, "connected")
    result = (job.result or {}) if job else {}
    if state == "done" and result.get("success") is False:
        state = "failed"
    repositories = result.get("repositories", 0)
    counts = {
        "repositories": repositories,
        "pending": gh.pending_total(org_id),
        **gh.found_summary(org_id),
        **gh.model_counts(org_id),
    }
    # A finished read that saw repositories but found no links between them is its
    # own plain state, not an error: the model is real, it just has no dependencies
    # yet. A read that saw no repositories at all is a second plain state.
    if state == "done":
        if repositories == 0:
            state = "no_repositories"
        elif counts["links"] == 0:
            state = "no_links"
    return {
        "state": state,
        "organization": (cfg.config or {}).get("organization"),
        "has_token": OrgCredentialVault().get_masked(org_id, "github", "api_token") is not None,
        "counts": counts,
        "job": {
            "id": job.id,
            "started_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": gh.connect_failure_message(result.get("error") or job.error) if state == "failed" else None,
        } if job else None,
    }


@m365_connector_bp.route("/github", methods=["GET"])
@login_required
@admin_required
def github_connect():
    """The one page that connects GitHub: paste a read-only token, and the first read starts at once."""
    org_id = _require_active_org()
    return render_template(
        "admin/connectors/github.html",
        status=_github_state(org_id),
        review_url=url_for("m365_connector.github_review"),
        status_url=url_for("m365_connector.github_status"),
    )


@m365_connector_bp.route("/github/status", methods=["GET"])
@login_required
@admin_required
def github_status():
    """JSON for the connect page to poll while the first read runs."""
    return jsonify(_github_state(_require_active_org()))


@m365_connector_bp.route("/github", methods=["POST"])
@login_required
@admin_required
def github_connect_save():
    """Store the read-only token in the organisation's vault, save the connection and start the first read.

    The admin who pastes the token on this page is the authority for it, so the connection is saved and
    the read started now. What the read finds is only ever proposals: nothing is in the model until it
    is approved (one action approves them all). The token goes to the vault and nowhere else: not into
    the connector's settings, a flash message or a response.
    """
    from app.connectors import github as gh
    from app.extensions import db
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import (
        CONNECTOR_ACTION_CREATE,
        CONNECTOR_ACTION_UPDATE,
        _load_org_connector,
        apply_connector_change,
    )
    from app.services.job_queue_service import get_job_queue_service

    org_id = _require_active_org()
    organization = (request.form.get("organization") or "").strip()
    token = (request.form.get("api_token") or "").strip()
    existing = _load_org_connector(org_id, connector_type="github")
    has_token = OrgCredentialVault().get_masked(org_id, "github", "api_token") is not None

    if not gh.is_valid_organization(organization):
        flash("Enter your GitHub organisation name exactly as it appears in github.com/<name>.", "error")
        return redirect(url_for("m365_connector.github_connect"))
    if not token and not has_token:
        flash("Paste a read-only GitHub token to connect.", "error")
        return redirect(url_for("m365_connector.github_connect"))
    if token and (len(token) > 255 or any(ch.isspace() for ch in token)):
        flash("That does not look like a GitHub token.", "error")
        return redirect(url_for("m365_connector.github_connect"))

    config = dict((existing.config or {}) if existing else {})
    config["organization"] = organization
    payload = {
        "connector_type": "github",
        "connector_id": existing.id if existing else None,
        "name": "GitHub",
        "config": config,
        "status": "active",
        "credential_provided": bool(token),
    }
    cfg = apply_connector_change(
        org_id=org_id,
        action=CONNECTOR_ACTION_UPDATE if existing else CONNECTOR_ACTION_CREATE,
        payload=payload,
        delegated=True,
    )
    if token:
        OrgCredentialVault().store(org_id=org_id, connector_type="github", credential_type="api_token", value=token)
    db.session.commit()
    get_job_queue_service().create_job(
        "GitHub: read repositories",
        gh.FIRST_SYNC_TASK,
        {"connector_id": cfg.id},
        organization_id=org_id,
    )
    flash("Connected. Reading your repositories now; this page shows when it is done.", "success")
    return redirect(url_for("m365_connector.github_connect"))


@m365_connector_bp.route("/github/sync", methods=["POST"])
@login_required
@admin_required
def github_resync():
    """Read the repositories again: adds what is new and changes nothing a person edited."""
    from app.connectors import github as gh
    from app.services.connector_framework import _load_org_connector
    from app.services.job_queue_service import get_job_queue_service

    org_id = _require_active_org()
    cfg = _load_org_connector(org_id, connector_type="github")
    if cfg is None:
        flash("Connect GitHub first.", "error")
        return redirect(url_for("m365_connector.github_connect"))
    get_job_queue_service().create_job(
        "GitHub: read repositories", gh.FIRST_SYNC_TASK, {"connector_id": cfg.id}, organization_id=org_id
    )
    flash("Reading your repositories again.", "success")
    return redirect(url_for("m365_connector.github_connect"))


@m365_connector_bp.route("/github/review", methods=["GET"])
@login_required
@admin_required
def github_review():
    """The review screen: every proposal the read made, grouped, with one Approve all."""
    from app.connectors import github as gh

    org_id = _require_active_org()
    return render_template(
        "admin/connectors/github_review.html",
        review=gh.pending_review(org_id),
        status=_github_state(org_id),
        classifications=gh.CLASSIFICATIONS,
    )


@m365_connector_bp.route("/github/review/approve-all", methods=["POST"])
@login_required
@admin_required
def github_review_approve_all():
    from app.connectors import github as gh

    org_id = _require_active_org()
    outcome = gh.approve_all_proposals(org_id, current_user_id())
    if outcome["failed"]:
        flash(
            f"Added {outcome['approved']} of {outcome['total']} to your model. "
            f"{len(outcome['failed'])} could not be added yet; they are still in the list.",
            "warning",
        )
    elif outcome["total"]:
        flash(f"Added {outcome['approved']} items to your model.", "success")
    else:
        flash("Nothing is waiting for review.", "info")
    return redirect(url_for("m365_connector.github_review"))


@m365_connector_bp.route("/github/review/<int:approval_id>/classify", methods=["POST"])
@login_required
@admin_required
def github_review_classify(approval_id):
    from app.connectors import github as gh
    from app.extensions import db

    org_id = _require_active_org()
    try:
        gh.change_classification(org_id, approval_id, (request.form.get("classification") or "").strip(), current_user_id())
        db.session.commit()
        flash("Changed.", "success")
    except (ValueError, LookupError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("m365_connector.github_review"))


@m365_connector_bp.route("/github/review/<int:approval_id>/skip", methods=["POST"])
@login_required
@admin_required
def github_review_skip(approval_id):
    from app.connectors import github as gh

    org_id = _require_active_org()
    try:
        outcome = gh.skip_proposal(org_id, approval_id, current_user_id())
        flash("Skipped." if outcome.get("success") else (outcome.get("error") or "Could not skip that."), "info")
    except LookupError as exc:
        flash(str(exc), "error")
    return redirect(url_for("m365_connector.github_review"))
