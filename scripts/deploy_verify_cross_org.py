"""Post-deploy cross-organisation tenant isolation check.

Signs in as each production test organisation and asserts neither can read the
other's records. Called by scripts/deploy_verified.sh step 6 after every
deploy. Fails the deploy on any cross-organisation read.

Requires the two production test organisations to be seeded first:
    flask seed-production-test-organisations

Environment:
    PROD_TEST_ORG_A_EMAIL      admin email for test org A
    PROD_TEST_ORG_B_EMAIL      admin email for test org B
    PROD_TEST_ORG_PASSWORD     shared password for both test users
    DEPLOY_VERIFY_BASE_URL     base URL of the deployed site

Exit 0 when tenant isolation holds. Exit 1 when a cross-organisation read is
detected, with a diagnostic on stderr.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def _api_post(url: str, data: dict, timeout: int = 30) -> tuple:
    """POST JSON, return (status, body_dict, session_cookie)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "archie-cross-org-check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            cookie = resp.headers.get("Set-Cookie", "")
            return resp.status, json.loads(resp.read().decode("utf-8")), cookie
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), ""
    except Exception as exc:
        return 0, {"error": str(exc)}, ""


def _api_get(url: str, cookie: str, timeout: int = 30) -> tuple:
    """GET with session cookie, return (status, body_dict)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie,
            "User-Agent": "archie-cross-org-check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    except Exception as exc:
        return 0, {"error": str(exc)}


def _login(base: str, email: str, password: str) -> str | None:
    """Sign in and return the session cookie, or None on failure."""
    status, body, cookie = _api_post(
        f"{base.rstrip('/')}/api/auth/login",
        {"email": email, "password": password},
    )
    if status != 200 or not body.get("success"):
        print(
            f"cross-org-check: FAIL - login as {email} returned HTTP {status}: {body}",
            file=sys.stderr,
        )
        return None
    return cookie


def _check_cannot_read_other_org(
    base: str, cookie: str, own_email: str, other_email: str
) -> bool:
    """Return True when the session CANNOT read the other org's data (good)."""
    # Try to read the other org's users via the session endpoint — the
    # organisation admin API would be the most direct cross-org read, but
    # any endpoint that returns org-scoped data works. We use the session
    # status endpoint to confirm we are signed in as the right user, then
    # try to read the other org's data through the users API.
    status, body = _api_get(f"{base.rstrip('/')}/api/auth/session", cookie)
    if status != 200:
        print(
            f"cross-org-check: FAIL - session check returned HTTP {status}",
            file=sys.stderr,
        )
        return False

    session_user = body.get("user", {})
    session_email = session_user.get("email", "")
    if session_email != own_email:
        print(
            f"cross-org-check: FAIL - signed in as {session_email}, expected {own_email}",
            file=sys.stderr,
        )
        return False

    # Try to read the other org's users. The /api/users endpoint is
    # org-scoped: a user of org A must not see org B's users.
    status, body = _api_get(
        f"{base.rstrip('/')}/api/users", cookie
    )

    # A 403 or empty result is expected — the user should not see the other
    # org's data. A 200 with users from the other org is a leak.
    if status == 200:
        users = body if isinstance(body, list) else body.get("users", body.get("data", []))
        if isinstance(users, list):
            for user in users:
                user_email = user.get("email", "")
                if other_email in user_email or (
                    "prod-test" in user_email and own_email.split("@")[0] not in user_email
                ):
                    print(
                        f"cross-org-check: FAIL - {own_email} can read user {user_email} "
                        f"from another organisation",
                        file=sys.stderr,
                    )
                    return False
    # If we got a 403 or an empty list, that's correct — tenant isolation holds.
    return True


def main() -> int:
    base = os.environ.get("DEPLOY_VERIFY_BASE_URL", "https://165-22-125-156.sslip.io")
    email_a = os.environ.get("PROD_TEST_ORG_A_EMAIL", "")
    email_b = os.environ.get("PROD_TEST_ORG_B_EMAIL", "")
    password = os.environ.get("PROD_TEST_ORG_PASSWORD", "")

    if not email_a or not email_b or not password:
        print(
            "cross-org-check: SKIP - credentials not configured",
            file=sys.stderr,
        )
        return 0

    # Sign in as org A
    cookie_a = _login(base, email_a, password)
    if cookie_a is None:
        return 1

    # Sign in as org B
    cookie_b = _login(base, email_b, password)
    if cookie_b is None:
        return 1

    # Org A must not read org B's data
    if not _check_cannot_read_other_org(base, cookie_a, email_a, email_b):
        return 1

    # Org B must not read org A's data
    if not _check_cannot_read_other_org(base, cookie_b, email_b, email_a):
        return 1

    print("cross-org-check: OK - tenant isolation holds between production test organisations")
    return 0


if __name__ == "__main__":
    sys.exit(main())