"""Per-organisation sign-in switches (two-factor, allowed addresses).

One accessor for the two switches an organisation's administrator can turn on
in Settings. They live in the existing ``system_settings`` key/value table
under an organisation-prefixed key (``org:<id>:<name>``), so one organisation's
switches can never be read for another organisation: every read and write
below names the organisation in the key. Nothing else reads these keys.
"""

from __future__ import annotations

import ipaddress
import json
import logging

logger = logging.getLogger(__name__)

TWO_FACTOR = "two-factor"
ADDRESS_SWITCH = "ip-whitelist"
ADDRESS_LIST = "ip-allowlist"
ORG_KEYS = (TWO_FACTOR, ADDRESS_SWITCH, ADDRESS_LIST)

REFUSAL = "Sign-in from this address is not allowed by your organisation. Ask your administrator to add it."
EMPTY_LIST_REASON = "Add at least one address before turning on allowed addresses, or everyone would be locked out."


def _key(org_id, name):
    return "org:%d:%s" % (int(org_id), name)


def get(org_id, name, default=None):
    from app import db

    if org_id is None:
        return default
    row = db.session.execute(
        db.text("SELECT value FROM system_settings WHERE key = :k"), {"k": _key(org_id, name)}
    ).first()
    if row is None or row[0] is None:
        return default
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return row[0]


def read_all(org_id):
    """The organisation's switches, keyed by their setting names."""
    return {
        TWO_FACTOR: bool(get(org_id, TWO_FACTOR, False)),
        ADDRESS_SWITCH: bool(get(org_id, ADDRESS_SWITCH, False)),
        ADDRESS_LIST: get(org_id, ADDRESS_LIST, "") or "",
    }


def parse_addresses(text):
    """Return (networks, invalid) from newline- or comma-separated text."""
    nets, invalid = [], []
    for raw in str(text or "").replace(",", "\n").splitlines():
        item = raw.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            invalid.append(item)
    return nets, invalid


def validate_and_store(org_id, settings):
    """Validate the org-scoped keys in ``settings`` and write them.

    Returns an error message (nothing written) or None.
    """
    from app import db

    present = {k: settings[k] for k in ORG_KEYS if k in settings}
    if not present:
        return None
    current = read_all(org_id)
    merged = dict(current)
    merged.update(present)
    nets, invalid = parse_addresses(merged[ADDRESS_LIST])
    if invalid:
        return "These are not valid addresses: %s. Use one IP address or range (for example 203.0.113.7 or 203.0.113.0/24) per line." % ", ".join(invalid)
    if merged[ADDRESS_SWITCH] and not nets:
        return EMPTY_LIST_REASON
    for name, value in present.items():
        if name in (TWO_FACTOR, ADDRESS_SWITCH):
            value = bool(value)
        else:
            value = str(value or "")
        db.session.execute(
            db.text(
                "INSERT INTO system_settings (key, value, updated_at) VALUES (:k, :v, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()"
            ),
            {"k": _key(org_id, name), "v": json.dumps(value)},
        )
    return None


def _org_ids(user):
    from app.services.rbac_service import rbac_service

    try:
        return rbac_service.org_ids_for(user)
    except Exception:
        logger.exception("org security: could not list organisations for user")
        return {user.organization_id} if getattr(user, "organization_id", None) else set()


def two_factor_required_for(user) -> bool:
    """True when any organisation the person belongs to has two-factor on."""
    if user is None:
        return False
    return any(bool(get(org_id, TWO_FACTOR, False)) for org_id in _org_ids(user))


def sign_in_refusal(user, ip):
    """The refusal sentence when ``ip`` is outside an organisation's allowed
    list for this person, else None. Platform administrators are never
    locked out. Every organisation the person belongs to with the switch on
    must allow the address."""
    if user is None or getattr(user, "is_platform_admin", False):
        return None
    for org_id in _org_ids(user):
        if not get(org_id, ADDRESS_SWITCH, False):
            continue
        nets, _ = parse_addresses(get(org_id, ADDRESS_LIST, ""))
        if not nets:
            continue  # switch cannot be saved empty; an empty list never locks anyone out
        try:
            addr = ipaddress.ip_address((ip or "").strip())
        except ValueError:
            return REFUSAL
        if not any(addr in n for n in nets):
            return REFUSAL
    return None


def client_address():
    """The address the request came from, as the nearest proxy saw it.

    The right-most X-Forwarded-For entry is the one our own proxy appended;
    anything to its left is supplied by the caller and is not trusted."""
    from flask import request

    forwarded = [p.strip() for p in (request.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
    return forwarded[-1] if forwarded else (request.remote_addr or "")


def check_sign_in(user):
    """The refusal sentence for a sign-in from a disallowed address, or None.

    The caller records the refusal like a failed sign-in."""
    return sign_in_refusal(user, client_address())
