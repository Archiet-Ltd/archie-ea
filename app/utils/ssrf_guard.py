"""Outbound-URL validation for server-side fetches (finding F-08).

F-08 flagged two integration config pages that accept a URL alongside OAuth
credentials. Only one of them turned out to be a real SSRF:

* **Salesforce** — ``instance_url`` is taken straight from the admin form and
  concatenated into ``requests.post(f"{instance_url}/services/oauth2/token")``
  in ``SalesforceDiscoveryService._get_token``. Nothing constrained the host.
* **Power Platform** — ``env_url`` is stored and echoed back but never fetched;
  every request goes to the fixed ``POWER_APPS_ENDPOINT`` constant. No SSRF.

``type="url"`` on the input constrains nothing server-side, so this module does
the check where it belongs: at the point of the fetch.

Two independent conditions must hold:

1. The host matches an allow-list of suffixes for the integration in question.
   This is the control that actually contains the risk — an attacker who can
   only reach ``*.salesforce.com`` cannot reach the metadata service.
2. Every IP the host resolves to is publicly routable. This is defence in depth
   against a permitted-but-hostile DNS record (``evil.salesforce.com.attacker``
   style tricks, or an internal split-horizon record), and it is why the check
   resolves rather than pattern-matching the string alone.

Redirects are the classic bypass: a permitted host that answers 302 to
``http://169.254.169.254/`` defeats a URL-string check entirely. Callers must
therefore either pass ``allow_redirects=False`` or re-validate each hop; the
convenience wrappers here do the former.
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class BlockedOutboundURL(ValueError):
    """Raised when a server-side fetch target fails validation."""


#: Salesforce serves orgs from these suffixes (classic, My Domain, sandboxes,
#: Experience Cloud sites and the legacy cloudforce hostnames).
SALESFORCE_HOST_SUFFIXES = (
    ".salesforce.com",
    ".force.com",
    ".cloudforce.com",
    ".salesforce.mil",
    ".sfcrmproducts.com",
)


def _is_public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local        # 169.254.0.0/16 — cloud metadata lives here
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def validate_outbound_url(url: str, allowed_host_suffixes=(), require_https=True) -> str:
    """Return the URL unchanged, or raise :class:`BlockedOutboundURL`.

    ``allowed_host_suffixes`` is mandatory in practice: passing an empty tuple
    reduces this to the private-range block alone, which is a weaker control
    and should only be used where no meaningful allow-list exists.
    """
    if not url or not isinstance(url, str):
        raise BlockedOutboundURL("No URL supplied")

    parsed = urlparse(url.strip())

    if parsed.scheme not in ("https", "http"):
        raise BlockedOutboundURL(
            f"Scheme {parsed.scheme!r} is not permitted for an outbound fetch"
        )
    if require_https and parsed.scheme != "https":
        raise BlockedOutboundURL("Outbound integration URLs must use https")

    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise BlockedOutboundURL("URL has no host")

    # Credentials in the URL (user:pass@host) are a redirect/parse-confusion
    # vector and are never legitimate here.
    if parsed.username or parsed.password:
        raise BlockedOutboundURL("Credentials embedded in URL are not permitted")

    if allowed_host_suffixes:
        if not any(
            host == s.lstrip(".") or host.endswith(s) for s in allowed_host_suffixes
        ):
            raise BlockedOutboundURL(
                f"Host {host!r} is not on the allow-list for this integration"
            )

    # Names that mean "this machine" whatever a resolver (or a test double) says.
    if host == "localhost" or host.endswith(".localhost"):
        raise BlockedOutboundURL(f"Host {host!r} is a loopback name")

    # A literal IP host skips DNS but must still be public.
    try:
        ipaddress.ip_address(host)
        literal_ip = True
    except ValueError:
        literal_ip = False

    if literal_ip:
        if not _is_public_ip(host):
            raise BlockedOutboundURL(
                f"Host {host!r} resolves to a non-public address"
            )
        return url

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise BlockedOutboundURL(f"Host {host!r} does not resolve ({exc})") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise BlockedOutboundURL(f"Host {host!r} does not resolve")
    for addr in addresses:
        if not _is_public_ip(addr):
            raise BlockedOutboundURL(
                f"Host {host!r} resolves to non-public address {addr}"
            )

    return url


def validate_salesforce_instance_url(url: str) -> str:
    """Validate a Salesforce ``instance_url`` before it is fetched."""
    return validate_outbound_url(url, allowed_host_suffixes=SALESFORCE_HOST_SUFFIXES)


def internal_targets_allowed() -> bool:
    """True only in development or testing configuration.

    Loopback and private addresses are legitimate webhook and compliance-check
    targets on a developer machine; in any other configuration they are the
    route to the host's own services and the cloud metadata address.
    """
    import os

    from flask import current_app

    if current_app.config.get("TESTING") or current_app.debug:
        return True
    return os.environ.get("FLASK_ENV", "").lower() in ("development", "testing")


def validate_user_supplied_url(url: str) -> str:
    """Check a URL a signed-in user gave us for the server to call.

    Used for webhook targets and compliance-check service URLs, where no
    host allow-list exists. Outside development and testing the host must
    resolve only to public addresses (no loopback, link-local, private or
    metadata). Callers must also send with ``allow_redirects=False``.
    Raises :class:`BlockedOutboundURL`.
    """
    if internal_targets_allowed():
        if not isinstance(url, str) or urlparse(url.strip()).scheme not in ("http", "https"):
            raise BlockedOutboundURL("URL must start with http:// or https://")
        return url
    return validate_outbound_url(url, allowed_host_suffixes=(), require_https=False)


def clamp_timeout(value, default=10, low=1, high=30) -> int:
    """Return an int timeout in [low, high]; ValueError if not numeric."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("timeout must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("timeout must be a number")
    return max(low, min(high, int(number)))
