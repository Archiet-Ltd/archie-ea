"""SCIM 2.0 provisioning endpoints (R1-B26 PR 1, TB-0143), mounted at ``/scim/v2``.

An organisation's identity provider creates, updates, reads, lists and
deactivates that organisation's users, and manages group membership, with a
per-organisation bearer token. Every route is a thin adapter over
``app.services.provisioning_service``; nothing here writes a user, token or
membership directly.

Bearer-only: the blueprint never reads the session cookie, so it is
CSRF-exempt (see ``csrf_coverage.BLUEPRINT_OPT_OUT``). The decorator below
resolves the token to its organisation and binds ``g.current_org_id``; every
query in the service additionally carries an explicit organisation predicate,
and an id from another organisation is answered exactly as a missing one.

Conformance: the subset of RFC 7643/7644 that identity providers use. PATCH
attribute paths this service does not manage (``displayName``, ``title``,
enterprise-extension attributes and the like) are accepted and ignored rather
than failing the whole request, because Entra ID and Okta send them on every
sync.
"""

from __future__ import annotations

import logging
import re
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request, url_for

from app.extensions import db
from app.services import auth_audit, provisioning_service
from app.services.billing_plans import PlanLimitReached
from app.services.provisioning_service import ProvisioningError
from app.services.rate_limiter import RateLimitExceeded, rate_limit

logger = logging.getLogger(__name__)

scim_bp = Blueprint("scim", __name__, url_prefix="/scim/v2")

SCIM_CONTENT_TYPE = "application/scim+json"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SPC_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
RESOURCE_TYPE_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Schema"

MAX_COUNT = 200
DEFAULT_COUNT = 100


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def scim_response(body, status=200, headers=None):
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Content-Type"] = SCIM_CONTENT_TYPE
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


def scim_error(status, detail, scim_type=None, headers=None):
    body = {"schemas": [ERROR_SCHEMA], "status": str(status), "detail": detail}
    if scim_type:
        body["scimType"] = scim_type
    return scim_response(body, status, headers)


@scim_bp.errorhandler(ProvisioningError)
def _handle_provisioning_error(exc):
    db.session.rollback()
    return scim_error(exc.status, exc.detail, exc.scim_type)


@scim_bp.errorhandler(PlanLimitReached)
def _handle_plan_limit(exc):
    db.session.rollback()
    return scim_error(403, str(exc))


@scim_bp.errorhandler(RateLimitExceeded)
def _handle_rate_limit(exc):
    return scim_error(
        429, "Too many requests. Slow down and retry.",
        headers={"Retry-After": str(exc.retry_after or 60)},
    )


# ---------------------------------------------------------------------------
# Authentication, tenant binding and throttling
# ---------------------------------------------------------------------------


def _bearer_value():
    header = request.headers.get("Authorization", "")
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


@rate_limit(20, "1m", key_func=lambda: f"scim-auth-fail:{request.remote_addr}")
def _failed_auth_gate():
    """Counts one failed authentication against the caller's address; raises
    RateLimitExceeded once 20 have landed in a minute."""
    return None


def _auth_failure(reason, row):
    _failed_auth_gate()
    auth_audit.record_scim_auth_failed(
        reason,
        organization_id=getattr(row, "organization_id", None),
        token_prefix=getattr(row, "token_prefix", None),
    )
    return scim_error(401, "Authentication failed.", headers={"WWW-Authenticate": "Bearer"})


def _scim_auth(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        raw = _bearer_value()
        if raw is None:
            return _auth_failure("missing_token", None)
        row, reason = provisioning_service.authenticate_scim_token(raw)
        if row is None or reason is not None:
            return _auth_failure(reason or "unknown_token", row)
        g.current_org_id = row.organization_id
        g.scim_token_id = row.id
        g.scim_token_prefix = row.token_prefix
        return view(*args, **kwargs)

    return wrapper


def _rate_key():
    return f"scim:{g.scim_token_id}"


def _route(rule, methods):
    """Register a route behind token authentication and the per-token throttle."""

    def decorator(view):
        guarded = _scim_auth(rate_limit(600, "1m", key_func=_rate_key)(view))
        return scim_bp.route(rule, methods=methods)(guarded)

    return decorator


def _org_id():
    return g.current_org_id


def _actor():
    return f"scim_token:{g.scim_token_prefix}"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _json_body():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise ProvisioningError("The request body must be a JSON object.", scim_type="invalidSyntax")
    return body


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ProvisioningError("active must be true or false.", scim_type="invalidValue")


def _paging():
    try:
        start = int(request.args.get("startIndex", 1))
        count = int(request.args.get("count", DEFAULT_COUNT))
    except ValueError:
        raise ProvisioningError("startIndex and count must be integers.", scim_type="invalidValue")
    return max(start, 1), max(0, min(count, MAX_COUNT))


_USER_FILTER = re.compile(
    r'^\s*(?P<attr>userName|externalId|emails(?:\[type\s+eq\s+"work"\])?\.value)\s+eq\s+"(?P<value>[^"]*)"\s*$',
    re.IGNORECASE,
)
_GROUP_FILTER = re.compile(r'^\s*displayName\s+eq\s+"(?P<value>[^"]*)"\s*$', re.IGNORECASE)


def _name_from(body):
    name = body.get("name")
    out = {}
    if isinstance(name, dict):
        if "givenName" in name:
            out["first_name"] = name.get("givenName") or ""
        if "familyName" in name:
            out["last_name"] = name.get("familyName") or ""
    return out


def _work_email(emails):
    if not isinstance(emails, list):
        return None
    chosen = None
    for item in emails:
        if not isinstance(item, dict) or not item.get("value"):
            continue
        if item.get("primary") is True or str(item.get("primary")).lower() == "true":
            return item["value"]
        if (item.get("type") or "").lower() == "work" and chosen is None:
            chosen = item["value"]
        if chosen is None:
            chosen = item["value"]
    return chosen


def _attrs_from_resource(body):
    attrs = {}
    if body.get("userName") is not None:
        attrs["email"] = str(body["userName"])
    elif body.get("emails") is not None:
        email = _work_email(body.get("emails"))
        if email:
            attrs["email"] = str(email)
    attrs.update(_name_from(body))
    if "externalId" in body:
        attrs["external_id"] = str(body["externalId"]) if body["externalId"] is not None else ""
    if "active" in body:
        attrs["active"] = _parse_bool(body["active"])
    return attrs


_USER_PREFIX = USER_SCHEMA + ":"


def _norm_path(path):
    path = (path or "").strip()
    if path.lower().startswith(_USER_PREFIX.lower()):
        path = path[len(_USER_PREFIX):]
    return path


def _apply_user_value(attrs, key, value, op):
    """Fold one (path, value) pair of a PATCH operation into ``attrs``."""
    key = _norm_path(key)
    lowered = key.lower()
    if lowered == "active":
        if op == "remove":
            return
        attrs["active"] = _parse_bool(value)
    elif lowered == "username":
        if op == "remove":
            raise ProvisioningError("userName cannot be removed.", scim_type="mutability")
        attrs["email"] = str(value)
    elif lowered in ("name.givenname", "name.familyname"):
        field = "first_name" if lowered.endswith("givenname") else "last_name"
        attrs[field] = "" if op == "remove" else (value if value is not None else "")
    elif lowered == "name":
        if op == "remove":
            attrs["first_name"] = ""
            attrs["last_name"] = ""
        elif isinstance(value, dict):
            attrs.update(_name_from({"name": value}))
    elif lowered == "externalid":
        attrs["external_id"] = "" if op == "remove" else (str(value) if value is not None else "")
    elif lowered.startswith("emails"):
        if op == "remove":
            return
        if isinstance(value, list):
            email = _work_email(value)
        elif isinstance(value, dict):
            email = value.get("value")
        else:
            email = value
        if email:
            attrs["email"] = str(email)
    # Anything else (displayName, title, extension attributes ...) is ignored.


def _attrs_from_patch(body):
    ops = body.get("Operations")
    if not isinstance(ops, list) or not ops:
        raise ProvisioningError("Operations must be a non-empty list.", scim_type="invalidSyntax")
    attrs = {}
    for operation in ops:
        if not isinstance(operation, dict):
            raise ProvisioningError("Each operation must be an object.", scim_type="invalidSyntax")
        op = str(operation.get("op", "")).strip().lower()
        if op not in ("add", "replace", "remove"):
            raise ProvisioningError("op must be add, replace or remove.", scim_type="invalidValue")
        path = operation.get("path")
        value = operation.get("value")
        if path:
            _apply_user_value(attrs, path, value, op)
        elif isinstance(value, dict):
            for key, inner in value.items():
                _apply_user_value(attrs, key, inner, op)
        else:
            raise ProvisioningError("An operation needs a path or an object value.", scim_type="noTarget")
    return attrs


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _user_resource(user):
    full = user.full_name() if (user.first_name or user.last_name) else None
    resource = {
        "schemas": [USER_SCHEMA],
        "id": str(user.id),
        "userName": user.email,
        "name": {
            "givenName": user.first_name or "",
            "familyName": user.last_name or "",
        },
        "emails": [{"value": user.email, "type": "work", "primary": True}],
        "active": bool(user.is_active),
        "meta": {
            "resourceType": "User",
            "location": url_for("scim.scim_get_user", user_id=str(user.id), _external=True),
        },
    }
    if full:
        resource["name"]["formatted"] = full
        resource["displayName"] = full
    if user.external_id:
        resource["externalId"] = user.external_id
    return resource


def _group_resource(group):
    org_id = _org_id()
    members = []
    for uid in provisioning_service.group_member_ids(org_id, group.id):
        user = provisioning_service.get_user(org_id, uid)
        if user is None:
            continue
        members.append({
            "value": str(user.id),
            "display": user.email,
            "$ref": url_for("scim.scim_get_user", user_id=str(user.id), _external=True),
        })
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(group.id),
        "displayName": group.sso_group_name,
        "members": members,
        "meta": {
            "resourceType": "Group",
            "location": url_for("scim.scim_get_group", group_id=str(group.id), _external=True),
        },
    }


def _list_response(resources, total, start):
    return scim_response({
        "schemas": [LIST_SCHEMA],
        "totalResults": total,
        "startIndex": start,
        "itemsPerPage": len(resources),
        "Resources": resources,
    })


def _user_or_404(user_id):
    user = provisioning_service.get_user(_org_id(), user_id)
    if user is None:
        raise provisioning_service.NotFoundError("Resource not found.")
    return user


def _group_or_404(group_id):
    group = provisioning_service.get_group(_org_id(), group_id)
    if group is None:
        raise provisioning_service.NotFoundError("Resource not found.")
    return group


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@_route("/Users", ["GET"])
def scim_list_users():
    from sqlalchemy import func

    from app.models.user import User

    start, count = _paging()
    query = provisioning_service.users_of_org(_org_id())
    flt = request.args.get("filter")
    if flt:
        match = _USER_FILTER.match(flt)
        if not match:
            raise ProvisioningError("Unsupported filter.", scim_type="invalidFilter")
        value = match.group("value")
        if match.group("attr").lower() == "externalid":
            query = query.filter(User.external_id == value)
        else:
            query = query.filter(func.lower(User.email) == value.strip().lower())
    total = query.count()
    rows = query.order_by(User.id).offset(start - 1).limit(count).all() if count else []
    return _list_response([_user_resource(u) for u in rows], total, start)


@_route("/Users/<user_id>", ["GET"])
def scim_get_user(user_id):
    return scim_response(_user_resource(_user_or_404(user_id)))


@_route("/Users", ["POST"])
def scim_create_user():
    attrs = _attrs_from_resource(_json_body())
    user, _created, _changed = provisioning_service.create_or_update_user(
        _org_id(), attrs, source=provisioning_service.SOURCE_SCIM, actor=_actor()
    )
    resource = _user_resource(user)
    return scim_response(resource, 201, {"Location": resource["meta"]["location"]})


@_route("/Users/<user_id>", ["PUT"])
def scim_replace_user(user_id):
    user = _user_or_404(user_id)
    attrs = _attrs_from_resource(_json_body())
    user, _created, _changed = provisioning_service.create_or_update_user(
        _org_id(), attrs, source=provisioning_service.SOURCE_SCIM, user=user, actor=_actor()
    )
    return scim_response(_user_resource(user))


@_route("/Users/<user_id>", ["PATCH"])
def scim_patch_user(user_id):
    user = _user_or_404(user_id)
    attrs = _attrs_from_patch(_json_body())
    user, _created, _changed = provisioning_service.create_or_update_user(
        _org_id(), attrs, source=provisioning_service.SOURCE_SCIM, user=user, actor=_actor()
    )
    return scim_response(_user_resource(user))


@_route("/Users/<user_id>", ["DELETE"])
def scim_delete_user(user_id):
    user = _user_or_404(user_id)
    provisioning_service.deactivate_user(
        user, reason=provisioning_service.REASON_LEAVER, actor=_actor()
    )
    return Response(status=204)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _member_ids(value):
    ids = []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ProvisioningError("members must be a list.", scim_type="invalidValue")
    for item in value:
        member = item.get("value") if isinstance(item, dict) else item
        if member in (None, ""):
            raise ProvisioningError("A member needs a value.", scim_type="invalidValue")
        ids.append(member)
    return ids


@_route("/Groups", ["GET"])
def scim_list_groups():
    start, count = _paging()
    name = None
    flt = request.args.get("filter")
    if flt:
        match = _GROUP_FILTER.match(flt)
        if not match:
            raise ProvisioningError("Unsupported filter.", scim_type="invalidFilter")
        name = match.group("value")
    groups = provisioning_service.list_groups(_org_id(), name)
    total = len(groups)
    page = groups[start - 1:start - 1 + count] if count else []
    return _list_response([_group_resource(g_) for g_ in page], total, start)


@_route("/Groups/<group_id>", ["GET"])
def scim_get_group(group_id):
    return scim_response(_group_resource(_group_or_404(group_id)))


@_route("/Groups", ["POST"])
def scim_create_group():
    body = _json_body()
    group, _created = provisioning_service.create_group(_org_id(), body.get("displayName"), _actor())
    if body.get("members"):
        provisioning_service.add_group_members(
            _org_id(), group, _member_ids(body["members"]), _actor()
        )
    resource = _group_resource(group)
    return scim_response(resource, 201, {"Location": resource["meta"]["location"]})


def _check_display_name(group, body):
    name = body.get("displayName")
    if name is not None and name != group.sso_group_name:
        raise ProvisioningError(
            "displayName cannot be changed; it names the mapping an administrator controls.",
            scim_type="mutability",
        )


@_route("/Groups/<group_id>", ["PUT"])
def scim_replace_group(group_id):
    group = _group_or_404(group_id)
    body = _json_body()
    _check_display_name(group, body)
    provisioning_service.set_group_members(
        _org_id(), group, _member_ids(body.get("members") or []), _actor()
    )
    return scim_response(_group_resource(group))


_MEMBER_PATH = re.compile(r'^members\[value\s+eq\s+"(?P<id>[^"]+)"\]$', re.IGNORECASE)


@_route("/Groups/<group_id>", ["PATCH"])
def scim_patch_group(group_id):
    group = _group_or_404(group_id)
    body = _json_body()
    ops = body.get("Operations")
    if not isinstance(ops, list) or not ops:
        raise ProvisioningError("Operations must be a non-empty list.", scim_type="invalidSyntax")
    org_id = _org_id()
    for operation in ops:
        if not isinstance(operation, dict):
            raise ProvisioningError("Each operation must be an object.", scim_type="invalidSyntax")
        op = str(operation.get("op", "")).strip().lower()
        if op not in ("add", "replace", "remove"):
            raise ProvisioningError("op must be add, replace or remove.", scim_type="invalidValue")
        path = (operation.get("path") or "").strip()
        value = operation.get("value")
        if not path:
            if not isinstance(value, dict):
                raise ProvisioningError("An operation needs a path or an object value.", scim_type="noTarget")
            _check_display_name(group, value)
            if "members" in value:
                _members_op(org_id, group, op, _member_ids(value["members"]))
            continue
        match = _MEMBER_PATH.match(path)
        if match:
            _members_op(org_id, group, "remove" if op == "remove" else op, [match.group("id")])
        elif path.lower() == "members":
            if op == "remove" and value is None:
                provisioning_service.set_group_members(org_id, group, [], _actor())
            else:
                _members_op(org_id, group, op, _member_ids(value))
        elif path.lower() == "displayname":
            _check_display_name(group, {"displayName": value})
        # other attributes are ignored
    return scim_response(_group_resource(group))


def _members_op(org_id, group, op, ids):
    if op == "add":
        provisioning_service.add_group_members(org_id, group, ids, _actor())
    elif op == "remove":
        provisioning_service.remove_group_members(org_id, group, ids, _actor())
    else:
        provisioning_service.set_group_members(org_id, group, ids, _actor())


@_route("/Groups/<group_id>", ["DELETE"])
def scim_delete_group(group_id):
    group = _group_or_404(group_id)
    provisioning_service.delete_group(_org_id(), group, _actor())
    return Response(status=204)


# ---------------------------------------------------------------------------
# Discovery (static; a token is still required)
# ---------------------------------------------------------------------------


@_route("/ServiceProviderConfig", ["GET"])
def scim_service_provider_config():
    return scim_response({
        "schemas": [SPC_SCHEMA],
        "documentationUri": "https://entelim.org",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_COUNT},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken",
            "name": "Bearer token",
            "description": "A per-organisation bearer token created in the SSO settings.",
            "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
            "primary": True,
        }],
        "meta": {
            "resourceType": "ServiceProviderConfig",
            "location": url_for("scim.scim_service_provider_config", _external=True),
        },
    })


_RESOURCE_TYPES = [
    {"id": "User", "name": "User", "endpoint": "/Users", "schema": USER_SCHEMA},
    {"id": "Group", "name": "Group", "endpoint": "/Groups", "schema": GROUP_SCHEMA},
]


@_route("/ResourceTypes", ["GET"])
def scim_resource_types():
    resources = [dict(rt, schemas=[RESOURCE_TYPE_SCHEMA], meta={"resourceType": "ResourceType"})
                 for rt in _RESOURCE_TYPES]
    return _list_response(resources, len(resources), 1)


def _attribute(name, type_="string", **extra):
    base = {"name": name, "type": type_, "multiValued": False, "required": False,
            "mutability": "readWrite", "returned": "default", "uniqueness": "none"}
    base.update(extra)
    return base


_SCHEMAS = [
    {
        "id": USER_SCHEMA, "name": "User", "description": "User Account",
        "schemas": [SCHEMA_SCHEMA],
        "attributes": [
            _attribute("userName", required=True, uniqueness="server"),
            _attribute("externalId"),
            _attribute("active", "boolean"),
            _attribute("name", "complex", subAttributes=[_attribute("givenName"), _attribute("familyName")]),
            _attribute("emails", "complex", multiValued=True,
                       subAttributes=[_attribute("value"), _attribute("type"), _attribute("primary", "boolean")]),
        ],
        "meta": {"resourceType": "Schema"},
    },
    {
        "id": GROUP_SCHEMA, "name": "Group", "description": "Group",
        "schemas": [SCHEMA_SCHEMA],
        "attributes": [
            _attribute("displayName", required=True, mutability="immutable"),
            _attribute("members", "complex", multiValued=True,
                       subAttributes=[_attribute("value", mutability="immutable")]),
        ],
        "meta": {"resourceType": "Schema"},
    },
]


@_route("/Schemas", ["GET"])
def scim_schemas():
    return _list_response(_SCHEMAS, len(_SCHEMAS), 1)
