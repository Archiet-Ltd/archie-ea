"""The Entelim GitHub App: connect GitHub in one click, instead of pasting a token.

The founder registers one GitHub App (settings: ``GITHUB_APP_ID``, ``GITHUB_APP_SLUG``,
``GITHUB_APP_PRIVATE_KEY_PATH``; see ``.env.example``). A person installs it on their
GitHub organisation or account, GitHub sends them back to ``/admin/connectors/
github/installed`` with an ``installation_id``, and this module turns that id into
read access: a short-lived JWT signed with the App's private key, exchanged for an
hour-long installation access token. Reading itself is unchanged -- the installation
token stands in for the personal token the token form stores, at the one place that
token is read (``resolve_api_token``, read by ``connector_framework._credential_field_map``).

The query string a browser redirect carries is never trusted on its own: ``state`` is
a value this server signed and timed (30 minutes) for the organisation that started
the install; the ``code`` GitHub sends with it (the App's "Request user authorization
during installation" setting) is exchanged for the installing person's own GitHub
token, and the ``installation_id`` is stored only if that person's installations
(``GET /user/installations``) include it -- the App's own JWT sees every installation
of the App, so it cannot tell whose installation this is. The id is then confirmed
with GitHub's API (``GET /app/installations/{id}``), an id already bound to another
organisation is refused, and a forged or expired link is refused with a plain message
rather than an error page.

When the five settings are not all set, ``app_configured()`` is False and
``install_url()`` returns ``None``: the one-click button does not render, and the
token form -- unchanged -- is the only way to connect.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

STATE_SALT = "github-app-install"
STATE_MAX_AGE_SECONDS = 30 * 60  # the brief's 30-minute window
JWT_LIFETIME_SECONDS = 9 * 60  # GitHub refuses an App JWT good for more than 10 minutes


class GitHubAppError(RuntimeError):
    """A GitHub App call that failed. The message is safe to show to a person: it
    never carries a response body, a key or a token."""


class GitHubAppStateError(ValueError):
    """The signed ``state`` on the install callback did not check out -- missing,
    expired, forged, or issued for a different organisation. The message is plain
    and safe to flash."""


# --------------------------------------------------------------------- #
# Configuration                                                          #
# --------------------------------------------------------------------- #


def app_configured() -> bool:
    """Whether the five settings the one-click button needs are all set.

    The client id and secret are what let the server ask GitHub who completed an
    install; without them it cannot make the visibility check, so it offers no
    one-click path. Read from ``current_app.config`` in one place, so every caller
    agrees: with any of the five unset, the button does not render anywhere, and the pages
    that would have shown it fall back to exactly the token form.
    """
    config = current_app.config
    return bool(
        str(config.get("GITHUB_APP_ID") or "").strip()
        and str(config.get("GITHUB_APP_SLUG") or "").strip()
        and str(config.get("GITHUB_APP_PRIVATE_KEY_PATH") or "").strip()
        and str(config.get("GITHUB_APP_CLIENT_ID") or "").strip()
        and str(config.get("GITHUB_APP_CLIENT_SECRET") or "").strip()
    )


# --------------------------------------------------------------------- #
# The signed state: who started this installation, and when              #
# --------------------------------------------------------------------- #


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=STATE_SALT)


def sign_state(org_id: int, return_to: str = "connector") -> str:
    """A signed, timed value naming the organisation that started this install and
    where to send them back once it is confirmed (``"connector"`` or ``"welcome"``)."""
    return _serializer().dumps({"org_id": org_id, "return_to": return_to})


def read_state(state: str, org_id: int) -> str:
    """The ``return_to`` carried by *state*, once it is proven to be a state this
    server issued, within its window, for *org_id* itself. Raises
    ``GitHubAppStateError`` with a plain message otherwise -- never a stack trace."""
    if not state:
        raise GitHubAppStateError(
            "That GitHub connection link is missing its state. Start again from Connect GitHub."
        )
    try:
        payload = _serializer().loads(state, max_age=STATE_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise GitHubAppStateError(
            "This GitHub connection link has expired. Start again from Connect GitHub."
        ) from None
    except BadSignature:
        raise GitHubAppStateError(
            "That GitHub connection link is not valid. Start again from Connect GitHub."
        ) from None
    if not isinstance(payload, dict) or payload.get("org_id") != org_id:
        raise GitHubAppStateError(
            "This GitHub installation was started by a different organisation. "
            "Start again from Connect GitHub."
        )
    return_to = payload.get("return_to")
    return return_to if return_to in ("connector", "welcome") else "connector"


def install_url(org_id: int, return_to: str = "connector") -> Optional[str]:
    """The address that installs the App on GitHub and returns the person to
    ``/installed``, or ``None`` when the App is not configured on this server."""
    if not app_configured():
        return None
    slug = str(current_app.config["GITHUB_APP_SLUG"]).strip()
    state = sign_state(org_id, return_to)
    return f"https://github.com/apps/{quote(slug, safe='')}/installations/new?state={quote(state, safe='')}"


# --------------------------------------------------------------------- #
# The App JWT, and the two GitHub calls it authorises                    #
# --------------------------------------------------------------------- #


def _private_key() -> str:
    path = str(current_app.config.get("GITHUB_APP_PRIVATE_KEY_PATH") or "").strip()
    if not path:
        raise GitHubAppError("No GitHub App private key is configured on this server.")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise GitHubAppError(
            "The GitHub App private key could not be read on this server."
        ) from exc


def _app_jwt() -> str:
    """A short-lived JWT proving this server is the registered GitHub App, signed
    with its private key (RS256), as GitHub's App authentication requires."""
    import jwt

    app_id = str(current_app.config.get("GITHUB_APP_ID") or "").strip()
    if not app_id:
        raise GitHubAppError("No GitHub App id is configured on this server.")
    now = int(time.time())
    payload = {"iat": now - 30, "exp": now + JWT_LIFETIME_SECONDS, "iss": app_id}
    return jwt.encode(payload, _private_key(), algorithm="RS256")


def _client():
    """The same GitHub client the connector reads with (one client, one egress
    guard -- ``ALLOW_EXTERNAL_API_CALLS``), with no personal token: App calls carry
    their own bearer per request."""
    from app.services.api_clients.github_client import GitHubAPIClient

    return GitHubAPIClient(api_token=None)


def _app_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_app_jwt()}", "Accept": "application/vnd.github+json"}


RATE_LIMITED = "GitHub's rate limit was reached; try again in a few minutes."


def _failure(response, what: str) -> GitHubAppError:
    status = getattr(response, "status_code", None)
    detail = str(getattr(response, "error", "") or "").lower()
    if status == 429 or (status == 403 and "rate limit" in detail):
        return GitHubAppError(RATE_LIMITED)
    if status == 404:
        return GitHubAppError(f"GitHub could not find {what}. It may have been uninstalled.")
    if status in (401, 403):
        return GitHubAppError("GitHub refused this server's GitHub App credentials.")
    return GitHubAppError(f"GitHub could not confirm {what} right now. Try again in a moment.")


def installation_token(installation_id: int) -> str:
    """Exchange this server's App JWT for an hour-long installation access token --
    the one place a GitHub App installation becomes a bearer the reader can send."""
    response = _client().post(
        f"app/installations/{installation_id}/access_tokens", headers=_app_headers()
    )
    if not response.success or not isinstance(response.data, dict) or not response.data.get("token"):
        raise _failure(response, "an access token for this installation")
    return response.data["token"]


def confirm_installation(installation_id: int) -> Dict[str, Any]:
    """Confirm an installation id with GitHub itself, using this server's App JWT --
    the query string a browser redirect carries is never trusted on its own.

    Raises ``GitHubAppError``, with a plain message, when the installation cannot be
    found, was cancelled (GitHub calls this "suspended"), or has no repositories
    selected. On success, returns the GitHub login the install is for and how many
    repositories it can read.
    """
    response = _client().get(
        f"app/installations/{installation_id}", headers=_app_headers(), use_cache=False
    )
    if not response.success or not isinstance(response.data, dict):
        raise _failure(response, "that installation")
    info = response.data
    if info.get("suspended_at"):
        raise GitHubAppError(
            "This GitHub installation was cancelled. Install the app again from Connect GitHub."
        )
    account = (info.get("account") or {}).get("login")
    if not account:
        raise GitHubAppError(
            "GitHub did not name an organisation or account for this installation."
        )

    token = installation_token(installation_id)
    repos = _client().get(
        "installation/repositories",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        use_cache=False,
    )
    if not repos.success or not isinstance(repos.data, dict):
        raise _failure(repos, "the repositories for this installation")
    total = repos.data.get("total_count")
    if not total:
        raise GitHubAppError(
            "This installation has no repositories selected. Choose at least one "
            "repository on GitHub, then connect again."
        )
    return {"account": account, "repository_count": total}


# --------------------------------------------------------------------- #
# Who completed the install: the person's own GitHub token               #
# --------------------------------------------------------------------- #


def exchange_code(code: str) -> str:
    """Exchange the one-time ``code`` GitHub sent back for the installing person's
    own user token. The code and the token are never logged, stored or echoed."""
    from app.services.api_clients.github_client import GitHubAPIClient

    config = current_app.config
    response = GitHubAPIClient(api_token=None, base_url="https://github.com").post(
        "login/oauth/access_token",
        json_data={
            "client_id": str(config.get("GITHUB_APP_CLIENT_ID") or "").strip(),
            "client_secret": str(config.get("GITHUB_APP_CLIENT_SECRET") or "").strip(),
            "code": code,
        },
        headers={"Accept": "application/json"},
    )
    if not response.success:
        raise _failure(response, "who completed this installation")
    data = response.data if isinstance(response.data, dict) else {}
    token = data.get("access_token")
    if not token:
        raise GitHubAppError(
            "GitHub did not confirm who completed this installation. Start again from Connect GitHub."
        )
    return token


def user_can_see_installation(user_token: str, installation_id: int) -> bool:
    """Whether the installations the person behind *user_token* can see include
    *installation_id* (``GET /user/installations``, every page)."""
    headers = {"Authorization": f"Bearer {user_token}", "Accept": "application/vnd.github+json"}
    seen = 0
    for page in range(1, 21):
        response = _client().get(
            "user/installations",
            params={"per_page": 100, "page": page},
            headers=headers,
            use_cache=False,
        )
        if not response.success or not isinstance(response.data, dict):
            raise _failure(response, "the installations you can see")
        batch = response.data.get("installations") or []
        if any(isinstance(item, dict) and item.get("id") == installation_id for item in batch):
            return True
        seen += len(batch)
        if not batch or seen >= (response.data.get("total_count") or 0):
            return False
    return False


def installation_bound_elsewhere(org_id: int, installation_id: int) -> bool:
    """Whether an organisation other than *org_id* already holds this installation."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    return bool(
        OrgCredentialVault().organisations_holding(
            "github", "installation_id", str(installation_id), excluding=org_id
        )
    )


# --------------------------------------------------------------------- #
# The token provider the reader actually calls                          #
# --------------------------------------------------------------------- #


def resolve_api_token(org_id: int) -> str:
    """What the reader sends: a fresh installation token when the organisation
    installed the App, otherwise the personal token it pasted. The one place
    either kind of credential becomes the bearer the GitHub client sends, so
    the connector itself has one read path regardless of how it was connected.
    """
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    vault = OrgCredentialVault()
    installation_id = vault.retrieve(org_id, "github", "installation_id")
    if installation_id:
        return installation_token(int(installation_id))
    return vault.retrieve(org_id, "github", "api_token") or ""
