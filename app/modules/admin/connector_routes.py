"""COM-017 — Microsoft 365 connector admin routes.

Provides admin UI for configuring the M365 integration:
    GET  /admin/connectors/m365       — render config form
    POST /admin/connectors/m365       — save config
    POST /admin/connectors/m365/test  — test OAuth2 token fetch

COM-008 — ServiceNow CMDB bidirectional connector admin routes:
    GET  /admin/connectors/servicenow        — render config form
    POST /admin/connectors/servicenow        — save config
    POST /admin/connectors/servicenow/sync   — trigger async CMDB pull (202)
    GET  /admin/connectors/servicenow/status — connector status JSON
"""

import logging
import threading

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import admin_required

logger = logging.getLogger(__name__)

m365_connector_bp = Blueprint("m365_connector", __name__, url_prefix="/admin/connectors")


@m365_connector_bp.route("/m365", methods=["GET"])
@login_required
@admin_required
def m365_config():
    """Render the M365 connector configuration form."""
    from app.services.connector_framework import ConnectorConfig

    cfg = ConnectorConfig.query.filter_by(connector_type="m365").first()
    cfg_data = (cfg.config or {}) if cfg else {}
    return render_template("admin/connectors/m365.html", cfg=cfg_data)


@m365_connector_bp.route("/m365", methods=["POST"])
@login_required
@admin_required
def m365_config_save():
    """Persist M365 connector configuration."""
    from app.extensions import db
    from app.services.connector_framework import ConnectorConfig
    from app.modules.codegen.services.credential_encryption import encrypt_credential

    form = request.form
    tenant_id = (form.get("tenant_id") or "").strip()
    client_id = (form.get("client_id") or "").strip()
    client_secret_raw = (form.get("client_secret") or "").strip()
    site_id = (form.get("sharepoint_site_id") or "").strip()
    folder_path = (form.get("sharepoint_folder_path") or "").strip()
    teams_webhook_url = (form.get("teams_webhook_url") or "").strip()
    enabled = form.get("enabled") == "1"

    cfg = ConnectorConfig.query.filter_by(connector_type="m365").first()

    # Preserve existing encrypted secret when the field is left blank
    if not client_secret_raw and cfg:
        client_secret_stored = (cfg.config or {}).get("client_secret", "")
    else:
        try:
            client_secret_stored = (
                encrypt_credential(client_secret_raw).decode() if client_secret_raw else ""
            )
        except RuntimeError:
            logger.exception("COM-017: Failed to encrypt M365 client secret")
            flash("Failed to encrypt client secret — check CREDENTIAL_ENCRYPTION_KEY.", "error")
            return redirect(url_for("m365_connector.m365_config"))

    config_data = {
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret_stored,
        "site_id": site_id,
        "folder_path": folder_path,
        "teams_webhook_url": teams_webhook_url,
        "enabled": enabled,
    }

    try:
        if cfg:
            cfg.config = config_data
            cfg.status = "active" if enabled else "inactive"
        else:
            cfg = ConnectorConfig(
                connector_type="m365",
                name="Microsoft 365",
                description="SharePoint blueprint export and Teams ARB notifications",
                config=config_data,
                status="active" if enabled else "inactive",
            )
            db.session.add(cfg)
        db.session.commit()
        flash("M365 configuration saved successfully.", "success")
    except Exception:
        db.session.rollback()
        logger.exception("COM-017: Failed to save M365 config")
        flash("Failed to save configuration.", "error")

    return redirect(url_for("m365_connector.m365_config"))


@m365_connector_bp.route("/m365/test", methods=["POST"])
@login_required
@admin_required
def m365_test():
    """Test M365 OAuth2 authentication and return JSON status."""
    from app.services.connector_framework import ConnectorConfig
    from app.services.m365_service import M365Service, _token_cache

    cfg = ConnectorConfig.query.filter_by(connector_type="m365").first()
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
# COM-008: ServiceNow CMDB connector routes
# ---------------------------------------------------------------------------

def _get_or_create_sn_config(org_id: int):
    """Return the ServiceNow ConnectorConfig for *org_id*, creating if absent."""
    from app.extensions import db
    from app.models.connector_config import OrgConnectorConfig

    config = OrgConnectorConfig.query.filter_by(
        organization_id=org_id, connector_type="servicenow"
    ).first()
    if config is None:
        config = OrgConnectorConfig(organization_id=org_id, connector_type="servicenow")
        db.session.add(config)
        db.session.commit()
    return config


@m365_connector_bp.route("/servicenow", methods=["GET"])
@login_required
@admin_required
def servicenow_config():
    """Display the ServiceNow connector configuration form."""
    org_id = getattr(current_user, "organization_id", None)
    sn_config = _get_or_create_sn_config(org_id) if org_id else None
    return render_template("admin/connectors/servicenow.html", sn_config=sn_config)


@m365_connector_bp.route("/servicenow", methods=["POST"])
@login_required
@admin_required
def servicenow_config_save():
    """Save ServiceNow connector configuration."""
    import json as _json

    from app.extensions import db

    org_id = getattr(current_user, "organization_id", None)
    if not org_id:
        flash("No organisation found for current user.", "error")
        return redirect(url_for("m365_connector.servicenow_config"))

    config = _get_or_create_sn_config(org_id)
    config.instance_url = (request.form.get("instance_url") or "").strip()
    config.client_id = (request.form.get("client_id") or "").strip()

    secret = (request.form.get("client_secret") or "").strip()
    if secret:
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        OrgCredentialVault().store(
            org_id=org_id,
            connector_type="servicenow",
            credential_type="client_secret",
            value=secret,
        )

    raw_mapping = (request.form.get("field_mapping") or "{}").strip()
    try:
        mapping = _json.loads(raw_mapping)
    except ValueError:
        flash("field_mapping must be valid JSON.", "warning")
        mapping = {}

    ci_filter = (request.form.get("ci_query_filter") or "").strip()
    if ci_filter:
        mapping["ci_query_filter"] = ci_filter

    config.field_mapping = mapping
    config.enabled = request.form.get("enabled") == "on"
    db.session.commit()

    flash("ServiceNow connector configuration saved.", "success")
    return redirect(url_for("m365_connector.servicenow_config"))


@m365_connector_bp.route("/servicenow/sync", methods=["POST"])
@login_required
@admin_required
def servicenow_sync():
    """Trigger an async CMDB inventory pull. Returns 202 Accepted."""
    org_id = getattr(current_user, "organization_id", None)
    if not org_id:
        return jsonify({"error": "No organisation found for current user."}), 400

    def _do_sync(oid):
        from app.services.servicenow_connector_service import ServiceNowConnectorService

        try:
            ServiceNowConnectorService().pull_cmdb_inventory(oid)
        except Exception as exc:
            logger.error("Background ServiceNow sync failed for org %s: %s", oid, exc)

    threading.Thread(target=_do_sync, args=(org_id,), daemon=True).start()
    return jsonify({"status": "accepted", "message": "Sync started in background."}), 202


@m365_connector_bp.route("/servicenow/status", methods=["GET"])
@login_required
@admin_required
def servicenow_status():
    """Return JSON status for the ServiceNow connector."""
    org_id = getattr(current_user, "organization_id", None)
    if not org_id:
        return jsonify({"error": "No organisation."}), 400

    from app.models.connector_config import OrgConnectorConfig

    config = OrgConnectorConfig.query.filter_by(
        organization_id=org_id, connector_type="servicenow"
    ).first()

    if config is None:
        return jsonify({"enabled": False, "last_sync_at": None, "status": "not_configured"})

    return jsonify(
        {
            "enabled": config.enabled,
            "last_sync_at": config.last_sync_at.isoformat() if config.last_sync_at else None,
            "status": "active" if config.enabled else "disabled",
        }
    )


# ---------------------------------------------------------------------------
# COM-009: Jira connector routes
# ---------------------------------------------------------------------------


def _get_jira_config():
    """Return the Jira ConnectorConfig row, or None."""
    from app.services.connector_framework import ConnectorConfig
    return ConnectorConfig.query.filter_by(connector_type="jira").first()


@m365_connector_bp.route("/jira", methods=["GET"])
@login_required
@admin_required
def jira_config():
    """Display the Jira connector configuration form."""
    connector = _get_jira_config()
    cfg = (connector.config or {}) if connector else {}
    return render_template("admin/connectors/jira.html", connector=connector, cfg=cfg)


@m365_connector_bp.route("/jira", methods=["POST"])
@login_required
@admin_required
def jira_config_save():
    """Persist Jira connector configuration with encrypted API token."""
    from app.extensions import db
    from app.services.connector_framework import ConnectorConfig, ConnectorStatus
    from app.modules.codegen.services.credential_encryption import encrypt_credential

    instance_url = (request.form.get("instance_url") or "").strip().rstrip("/")
    email = (request.form.get("email") or "").strip()
    api_token_raw = (request.form.get("api_token") or "").strip()
    default_project_key = (request.form.get("default_project_key") or "ARCH").strip().upper()
    enabled = request.form.get("enabled") == "on"

    if not instance_url or not email:
        flash("Instance URL and email are required.", "error")
        return redirect(url_for("m365_connector.jira_config"))

    config = _get_jira_config()
    existing_cfg = (config.config or {}) if config else {}

    if api_token_raw:
        try:
            encrypted = encrypt_credential(api_token_raw)
            existing_cfg["api_token_encrypted"] = (
                encrypted.decode("utf-8") if isinstance(encrypted, bytes) else encrypted
            )
        except Exception:
            logger.exception("COM-009: Failed to encrypt Jira API token")
            flash("Failed to encrypt API token — check CREDENTIAL_ENCRYPTION_KEY.", "error")
            return redirect(url_for("m365_connector.jira_config"))

    existing_cfg["instance_url"] = instance_url
    existing_cfg["email"] = email
    existing_cfg["default_project_key"] = default_project_key
    existing_cfg["enabled"] = enabled

    try:
        if config is None:
            config = ConnectorConfig(
                connector_type="jira",
                name="Jira ALM Connector",
                description="Bidirectional Jira integration — ARB epics and backlog import.",
                config=existing_cfg,
                status=ConnectorStatus.ACTIVE.value if enabled else ConnectorStatus.INACTIVE.value,
            )
            db.session.add(config)
        else:
            config.config = existing_cfg
            config.status = ConnectorStatus.ACTIVE.value if enabled else ConnectorStatus.INACTIVE.value
        db.session.commit()
        flash("Jira connector configuration saved.", "success")
    except Exception:
        db.session.rollback()
        logger.exception("COM-009: Failed to save Jira config")
        flash("Failed to save configuration.", "error")

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

