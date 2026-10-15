"""Integration Contract Registry API — RUNTIME-02.

CRUD endpoints for IntegrationContract: real API endpoints, auth config,
and SLA parameters that the code generator uses instead of placeholder URLs.

Blueprint: integration_contract_bp, prefix /api/enterprise
"""

import json
import logging

import requests as http_requests
from flask import Blueprint, jsonify, request
from flask_login import login_required

from app import db
from app.models.integration_contract import (
    VALID_AUTH_METHODS,
    VALID_PROTOCOLS,
    VALID_SPEC_FORMATS,
    IntegrationContract,
)
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

from app.utils.route_guards import entity_in_active_org, require_entity_in_active_org
from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

#: Largest spec the server will read from a contract's spec_url.
MAX_SPEC_BYTES = 2 * 1024 * 1024
_SPEC_CHUNK_BYTES = 64 * 1024
_SPEC_FETCH_TIMEOUT = (5, 15)  # connect, read
_JSON_TYPES = {"application/json", "text/json"}
_YAML_TYPES = {"application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"}

integration_contract_bp = Blueprint(
    "integration_contract_api",
    __name__,
    url_prefix="/api/enterprise",
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _validate_contract_payload(data, is_update=False):
    """Return (cleaned_data, error_message). error_message is None on success."""
    if not is_update and not data.get("name"):
        return None, "name is required"

    cleaned = {}

    str_fields = [
        "name", "version", "base_url", "protocol", "auth_method",
        "spec_format", "spec_url", "sla_availability", "rate_limit",
        "owner_team", "documentation_url",
    ]
    for f in str_fields:
        if f in data:
            cleaned[f] = data[f]

    json_fields = ["auth_config", "spec_content", "environments"]
    for f in json_fields:
        if f in data:
            cleaned[f] = data[f]

    int_fields = ["application_id", "sla_latency_ms"]
    for f in int_fields:
        if f in data and data[f] is not None:
            try:
                cleaned[f] = int(data[f])
            except (ValueError, TypeError):
                return None, f"{f} must be an integer"

    # Validate enums
    if "protocol" in cleaned and cleaned["protocol"]:
        if cleaned["protocol"] not in VALID_PROTOCOLS:
            return None, f"protocol must be one of: {', '.join(sorted(VALID_PROTOCOLS))}"

    if "auth_method" in cleaned and cleaned["auth_method"]:
        if cleaned["auth_method"] not in VALID_AUTH_METHODS:
            return None, f"auth_method must be one of: {', '.join(sorted(VALID_AUTH_METHODS))}"

    if "spec_format" in cleaned and cleaned["spec_format"]:
        if cleaned["spec_format"] not in VALID_SPEC_FORMATS:
            return None, f"spec_format must be one of: {', '.join(sorted(VALID_SPEC_FORMATS))}"

    # Security: reject auth_config that contains actual secret values
    auth_cfg = cleaned.get("auth_config")
    if auth_cfg and isinstance(auth_cfg, dict):
        for key, val in auth_cfg.items():
            if isinstance(val, str) and key not in (
                "token_url", "authorize_url", "scope", "grant_type",
                "client_id_env", "client_secret_env", "api_key_env",
                "api_key_header", "username_env", "password_env",
                "cert_path_env", "key_path_env",
            ):
                # Allow env var references (ending in _env) and known URL fields
                if not key.endswith("_env") and "url" not in key.lower():
                    logger.warning(
                        "auth_config field %r may contain a secret — "
                        "use env var names (ending in _env) instead",
                        key,
                    )

    # The URL the server will later fetch. Refused here, and checked again at
    # fetch time (a name can start resolving somewhere else).
    if cleaned.get("spec_url"):
        err = _check_spec_url(cleaned["spec_url"])
        if err:
            return None, err

    return cleaned, None


def _check_spec_url(url):
    """Return an error message for a spec_url the server must not fetch, else None."""
    if not isinstance(url, str) or len(url) > 500:
        return "spec_url must be a URL of at most 500 characters"
    try:
        validate_user_supplied_url(url)
    except BlockedOutboundURL as exc:
        logger.warning("spec_url refused: %s", exc)
        return "spec_url is not an allowed address"
    return None


def _contract_in_active_org(contract_id):
    """The contract, only when its application belongs to the active organisation.

    IntegrationContract has no organisation column of its own; its application
    does. A contract with no application belongs to nobody and is never served.
    Unknown organisation, foreign organisation and missing row all answer None.
    """
    from app.models.application_portfolio import ApplicationComponent

    contract = IntegrationContract.query.get(contract_id)  # tenant-scoping-ok: organisation proven through its application below
    if contract is None or not entity_in_active_org(ApplicationComponent, contract.application_id):
        return None
    return contract


def _read_spec(url):
    """Fetch ``url`` with no redirects, a size cap and a JSON/YAML content type.

    Returns the parsed object, or raises ``ValueError`` with a message that is
    safe to show the caller. Nothing about the remote side (addresses, error
    text, body) is put in that message.
    """
    import yaml

    resp = http_requests.get(
        url,
        timeout=_SPEC_FETCH_TIMEOUT,
        allow_redirects=False,
        stream=True,
        headers={"Accept": "application/json, application/yaml"},
    )
    try:
        if 300 <= resp.status_code < 400:
            raise ValueError("Spec URL redirects; redirects are not followed")
        if resp.status_code >= 400:
            raise ValueError(f"Spec URL returned {resp.status_code}")
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        is_json = ctype in _JSON_TYPES or ctype.endswith("+json")
        is_yaml = ctype in _YAML_TYPES or ctype.endswith("+yaml")
        if not (is_json or is_yaml):
            raise ValueError("Spec URL must serve JSON or YAML")
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > MAX_SPEC_BYTES:
            raise ValueError("Spec is too large")
        chunks, total = [], 0
        for chunk in resp.iter_content(chunk_size=_SPEC_CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_SPEC_BYTES:
                raise ValueError("Spec is too large")
            chunks.append(chunk)
    finally:
        resp.close()
    raw = b"".join(chunks)
    try:
        data = json.loads(raw) if is_json else yaml.safe_load(raw)
    except Exception:  # parse detail can echo the content
        raise ValueError("Spec could not be parsed") from None
    if not isinstance(data, dict):
        raise ValueError("Spec must be a JSON or YAML object")
    return data


# ── CRUD ─────────────────────────────────────────────────────────────────


@integration_contract_bp.route("/contracts", methods=["POST"])
@login_required
def create_contract():
    """Create a new integration contract."""
    data = request.get_json(silent=True) or {}
    cleaned, err = _validate_contract_payload(data)
    if err:
        return jsonify({"error": err}), 400

    from app.models.application_portfolio import ApplicationComponent

    # A contract is owned through its application; one with none would belong
    # to no organisation, so it cannot be created.
    if cleaned.get("application_id") is None:
        return jsonify({"error": "application_id is required"}), 400
    if not entity_in_active_org(ApplicationComponent, cleaned["application_id"]):
        return jsonify({"error": "Application not found"}), 404

    contract = IntegrationContract(**cleaned)
    db.session.add(contract)
    db.session.commit()

    logger.info("Created IntegrationContract id=%d name=%r", contract.id, contract.name)
    return jsonify(contract.to_dict()), 201


@integration_contract_bp.route("/contracts", methods=["GET"])
@login_required
def list_contracts():
    """List contracts with optional filters: ?q=, ?protocol=, ?application_id=, ?auth_method=."""
    from app.middleware.tenant_context import current_org_id
    from app.models.application_portfolio import ApplicationComponent

    org_id = current_org_id()
    if org_id is None:
        return jsonify({"items": [], "total": 0, "page": 1, "per_page": 0, "pages": 0})
    # Only contracts whose application is in the active organisation.
    query = IntegrationContract.query.join(  # tenant-scoping-ok: joined to the active organisation's applications
        ApplicationComponent, ApplicationComponent.id == IntegrationContract.application_id
    ).filter(ApplicationComponent.organization_id == org_id)

    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(IntegrationContract.name.ilike(f"%{q}%"))

    protocol = request.args.get("protocol")
    if protocol:
        query = query.filter(IntegrationContract.protocol == protocol)

    auth_method = request.args.get("auth_method")
    if auth_method:
        query = query.filter(IntegrationContract.auth_method == auth_method)

    app_id = request.args.get("application_id")
    if app_id:
        try:
            query = query.filter(IntegrationContract.application_id == int(app_id))
        except (ValueError, TypeError):
            return jsonify({"error": "application_id must be an integer"}), 400

    page = safe_int_arg('page', 1, minimum=1)
    per_page = min(safe_int_arg('per_page', 50, minimum=1, maximum=500), 200)

    pagination = query.order_by(IntegrationContract.updated_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False,
    )

    return jsonify({
        "items": [c.to_dict() for c in pagination.items],
        "total": pagination.total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "pages": pagination.pages,
    })


@integration_contract_bp.route("/contracts/<int:contract_id>", methods=["GET"])
@login_required
def get_contract(contract_id):
    """Get a single contract by ID."""
    contract = _contract_in_active_org(contract_id)
    if not contract:
        return jsonify({"error": "Contract not found"}), 404
    return jsonify(contract.to_dict())


@integration_contract_bp.route("/contracts/<int:contract_id>", methods=["PUT"])
@login_required
def update_contract(contract_id):
    """Update an existing contract."""
    contract = _contract_in_active_org(contract_id)
    if not contract:
        return jsonify({"error": "Contract not found"}), 404

    data = request.get_json(silent=True) or {}
    cleaned, err = _validate_contract_payload(data, is_update=True)
    if err:
        return jsonify({"error": err}), 400

    if "application_id" in cleaned:
        from app.models.application_portfolio import ApplicationComponent

        if not entity_in_active_org(ApplicationComponent, cleaned["application_id"]):
            return jsonify({"error": "Application not found"}), 404
    elif "application_id" in data and data["application_id"] is None:
        return jsonify({"error": "application_id cannot be cleared"}), 400

    for key, value in cleaned.items():
        setattr(contract, key, value)

    db.session.commit()
    logger.info("Updated IntegrationContract id=%d", contract.id)
    return jsonify(contract.to_dict())


@integration_contract_bp.route("/contracts/<int:contract_id>", methods=["DELETE"])
@login_required
def delete_contract(contract_id):
    """Delete a contract."""
    contract = _contract_in_active_org(contract_id)
    if not contract:
        return jsonify({"error": "Contract not found"}), 404

    db.session.delete(contract)
    db.session.commit()
    logger.info("Deleted IntegrationContract id=%d", contract_id)
    return jsonify({"deleted": contract_id})


# ── Spec fetcher ─────────────────────────────────────────────────────────


@integration_contract_bp.route("/contracts/<int:contract_id>/fetch-spec", methods=["POST"])
@login_required
def fetch_spec(contract_id):
    """Fetch an OpenAPI/AsyncAPI spec from the contract's spec_url and store it."""
    contract = _contract_in_active_org(contract_id)
    if not contract:
        return jsonify({"error": "Contract not found"}), 404

    if not contract.spec_url:
        return jsonify({"error": "No spec_url configured on this contract"}), 400

    # Checked again here: rows saved before the check existed, and names that
    # have since started resolving to an internal address.
    if _check_spec_url(contract.spec_url):
        return jsonify({"error": "spec_url is not an allowed address"}), 400

    try:
        spec_data = _read_spec(contract.spec_url)
    except ValueError as exc:  # raised by _read_spec with a caller-safe message
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:  # connection detail stays in the log
        logger.warning("Spec fetch failed for contract %s: %s", contract_id, type(exc).__name__)
        return jsonify({"error": "Failed to fetch spec"}), 502

    contract.spec_content = spec_data
    # Auto-detect format from spec content
    if not contract.spec_format:
        if "openapi" in spec_data:
            contract.spec_format = "openapi"
        elif "asyncapi" in spec_data:
            contract.spec_format = "asyncapi"

    db.session.commit()
    logger.info(
        "Fetched spec for IntegrationContract id=%d from %s",
        contract.id, contract.spec_url,
    )
    return jsonify({
        "message": "Spec fetched and stored",
        "spec_format": contract.spec_format,
        "spec_keys": list(spec_data.keys()) if isinstance(spec_data, dict) else None,
    })


# ── Application-scoped listing ───────────────────────────────────────────


@integration_contract_bp.route(
    "/applications/<int:app_id>/contracts", methods=["GET"]
)
@login_required
def list_contracts_for_app(app_id):
    """List all contracts for a specific application."""
    from app.models.application_portfolio import ApplicationComponent

    require_entity_in_active_org(ApplicationComponent, app_id, description="Application not found")

    contracts = (
        IntegrationContract.query  # tenant-scoping-ok: application proven in the active organisation above
        .filter_by(application_id=app_id)
        .order_by(IntegrationContract.name)
        .all()
    )
    return jsonify({
        "application_id": app_id,
        "items": [c.to_dict() for c in contracts],
        "total": len(contracts),
    })
