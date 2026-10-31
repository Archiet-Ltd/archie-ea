"""The Entelim GitHub App: signed state, the App JWT, and the installation token
exchange, with GitHub's API stubbed at the client (``requests.Session.request``) --
no test here touches the network.

Two organisations are used throughout to prove the isolation the brief requires:
an installation one organisation stores is never readable, nor attachable, by
another.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.connectors import github_app

API = "https://api.github.com/"
LOGIN = "https://github.com/"

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------- #
# Key material: generated once per test session, never written to the   #
# repository. A throwaway RSA key, as the brief requires for JWT tests.  #
# --------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = key.public_key()
    return private_pem, public_key


@pytest.fixture
def private_key_path(tmp_path, rsa_keypair):
    """The private key as a temporary file the test writes and deletes -- this
    is the only place a GitHub App private key exists anywhere in this run."""
    private_pem, _public = rsa_keypair
    path = tmp_path / "github-app-private-key.pem"
    path.write_text(private_pem.decode("utf-8"))
    yield str(path)
    path.unlink(missing_ok=True)


@pytest.fixture
def configured_app(app, monkeypatch, private_key_path):
    """The five GitHub App settings, all set, for the duration of one test."""
    monkeypatch.setitem(app.config, "GITHUB_APP_ID", "5534377")
    monkeypatch.setitem(app.config, "GITHUB_APP_SLUG", "entelim")
    monkeypatch.setitem(app.config, "GITHUB_APP_PRIVATE_KEY_PATH", private_key_path)
    monkeypatch.setitem(app.config, "GITHUB_APP_CLIENT_ID", "Iv1.testclientid")
    monkeypatch.setitem(app.config, "GITHUB_APP_CLIENT_SECRET", "test-client-secret-not-real")
    return app


# --------------------------------------------------------------------- #
# Configuration                                                          #
# --------------------------------------------------------------------- #


def test_app_configured_false_when_any_setting_missing(app, monkeypatch):
    monkeypatch.setitem(app.config, "GITHUB_APP_ID", "")
    monkeypatch.setitem(app.config, "GITHUB_APP_SLUG", "")
    monkeypatch.setitem(app.config, "GITHUB_APP_PRIVATE_KEY_PATH", "")
    with app.app_context():
        assert github_app.app_configured() is False

    monkeypatch.setitem(app.config, "GITHUB_APP_ID", "123")
    monkeypatch.setitem(app.config, "GITHUB_APP_SLUG", "entelim")
    # private key path still unset
    with app.app_context():
        assert github_app.app_configured() is False


def test_app_configured_true_when_all_three_set(configured_app):
    with configured_app.app_context():
        assert github_app.app_configured() is True


def test_install_url_is_none_when_not_configured(app, monkeypatch):
    monkeypatch.setitem(app.config, "GITHUB_APP_ID", "")
    with app.app_context():
        assert github_app.install_url(1) is None


def test_install_url_names_the_slug_and_carries_a_signed_state(configured_app):
    with configured_app.app_context():
        url = github_app.install_url(42)
    assert url.startswith("https://github.com/apps/entelim/installations/new?state=")
    state = url.split("state=", 1)[1]
    with configured_app.app_context():
        assert github_app.read_state(state, 42) == "connector"


def test_install_url_carries_the_return_to(configured_app):
    with configured_app.app_context():
        url = github_app.install_url(7, return_to="welcome")
        state = url.split("state=", 1)[1]
        assert github_app.read_state(state, 7) == "welcome"


# --------------------------------------------------------------------- #
# Signed state: expiry, forgery, organisation isolation                 #
# --------------------------------------------------------------------- #


def test_sign_and_read_state_round_trip(configured_app):
    with configured_app.app_context():
        state = github_app.sign_state(99)
        assert github_app.read_state(state, 99) == "connector"


def test_read_state_rejects_missing_state(configured_app):
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppStateError, match="missing"):
            github_app.read_state("", 1)


def test_read_state_rejects_a_forged_state(configured_app):
    with configured_app.app_context():
        state = github_app.sign_state(1)
    tampered = state[:-1] + ("a" if state[-1] != "a" else "b")
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppStateError, match="not valid"):
            github_app.read_state(tampered, 1)


def test_read_state_rejects_an_expired_state(configured_app, monkeypatch):
    import itsdangerous.timed as timed_module

    issued_at = int(time.time()) - github_app.STATE_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(timed_module.time, "time", lambda: issued_at)
    with configured_app.app_context():
        state = github_app.sign_state(1)
    monkeypatch.undo()  # back to the real clock for the read
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppStateError, match="expired"):
            github_app.read_state(state, 1)


def test_read_state_rejects_a_state_issued_for_a_different_organisation(configured_app, make_org):
    """The two-organisation isolation case the acceptance criteria require:
    organisation B cannot attach an installation whose state names organisation A."""
    org_a, org_b = make_org("a"), make_org("b")
    with configured_app.app_context():
        state_for_a = github_app.sign_state(org_a.id)
        with pytest.raises(github_app.GitHubAppStateError, match="different organisation"):
            github_app.read_state(state_for_a, org_b.id)
        # The state is still good for the organisation it was actually issued for.
        assert github_app.read_state(state_for_a, org_a.id) == "connector"


# --------------------------------------------------------------------- #
# The App JWT                                                            #
# --------------------------------------------------------------------- #


def test_app_jwt_is_signed_rs256_and_names_the_app_id(configured_app, rsa_keypair):
    _private_pem, public_key = rsa_keypair
    with configured_app.app_context():
        token = github_app._app_jwt()
    decoded = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_aud": False})
    assert decoded["iss"] == "5534377"
    assert decoded["exp"] > decoded["iat"]
    assert decoded["exp"] - decoded["iat"] <= 10 * 60  # GitHub refuses a longer-lived App JWT


def test_app_jwt_raises_plainly_when_no_app_id(app, private_key_path, monkeypatch):
    monkeypatch.setitem(app.config, "GITHUB_APP_ID", "")
    monkeypatch.setitem(app.config, "GITHUB_APP_PRIVATE_KEY_PATH", private_key_path)
    with app.app_context():
        with pytest.raises(github_app.GitHubAppError, match="No GitHub App id"):
            github_app._app_jwt()


def test_app_jwt_raises_plainly_when_key_file_absent(app, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "GITHUB_APP_ID", "123")
    monkeypatch.setitem(app.config, "GITHUB_APP_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    with app.app_context():
        with pytest.raises(github_app.GitHubAppError, match="could not be read"):
            github_app._app_jwt()


# --------------------------------------------------------------------- #
# GitHub's API, stubbed at the client                                   #
# --------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.reason = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found"}.get(status, "Status")
        self.text = json.dumps(body)
        self.is_redirect = False

    def json(self):
        return self._body


@pytest.fixture
def github_api(configured_app, monkeypatch):
    """Serve canned responses for the three GitHub App endpoints this module calls,
    in place of ``requests.Session.request``. ``state.bundle`` maps a path to
    ``(status, body)``; ``state.calls`` records every request, with its Authorization
    header, so a test can prove the right bearer was sent for each call."""
    import requests

    monkeypatch.setitem(configured_app.config, "ALLOW_EXTERNAL_API_CALLS", True)
    state = SimpleNamespace(
        bundle={
            "app/installations/123": (200, {"account": {"login": "acme"}, "suspended_at": None}),
            "app/installations/123/access_tokens": (201, {"token": "ghs_mock_installation_token"}),
            "installation/repositories": (200, {"total_count": 3, "repositories": []}),
            "login/oauth/access_token": (200, {"access_token": "ghu_mock_user_token"}),
            "user/installations": (200, {"total_count": 1, "installations": [{"id": 123}]}),
        },
        calls=[],
    )

    def fake_request(self, method=None, url=None, headers=None, **kw):
        assert url.startswith((API, LOGIN)), "a request left for an unexpected host: %s" % url
        path = url[len(LOGIN):].strip("/") if url.startswith(LOGIN) else url[len(API):].strip("/")
        auth = {**self.headers, **(headers or {})}.get("Authorization")
        state.calls.append({"method": method, "path": path, "auth": auth})
        if path not in state.bundle:
            return _Resp(404, {"message": "Not Found"})
        status, body = state.bundle[path]
        return _Resp(status, body)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return state


def test_installation_token_exchanges_the_app_jwt(configured_app, github_api):
    with configured_app.app_context():
        token = github_app.installation_token(123)
    assert token == "ghs_mock_installation_token"
    call = next(c for c in github_api.calls if c["path"] == "app/installations/123/access_tokens")
    assert call["method"] == "POST"
    assert call["auth"].startswith("Bearer ")


def test_installation_token_raises_plainly_on_404(configured_app, github_api):
    github_api.bundle.pop("app/installations/123/access_tokens")
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppError, match="could not find"):
            github_app.installation_token(123)


def test_confirm_installation_returns_account_and_repository_count(configured_app, github_api):
    with configured_app.app_context():
        result = github_app.confirm_installation(123)
    assert result == {"account": "acme", "repository_count": 3}
    # The repository listing is read with the installation token, never the App JWT.
    call = next(c for c in github_api.calls if c["path"] == "installation/repositories")
    assert call["auth"] == "token ghs_mock_installation_token"


def test_confirm_installation_rejects_a_cancelled_installation(configured_app, github_api):
    github_api.bundle["app/installations/123"] = (200, {"account": {"login": "acme"}, "suspended_at": "2026-10-20T00:00:00Z"})
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppError, match="cancelled"):
            github_app.confirm_installation(123)


def test_confirm_installation_rejects_no_repositories_selected(configured_app, github_api):
    github_api.bundle["installation/repositories"] = (200, {"total_count": 0, "repositories": []})
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppError, match="no repositories"):
            github_app.confirm_installation(123)


def test_confirm_installation_rejects_an_unknown_installation(configured_app, github_api):
    github_api.bundle.pop("app/installations/123")
    with configured_app.app_context():
        with pytest.raises(github_app.GitHubAppError, match="could not find"):
            github_app.confirm_installation(999)


# --------------------------------------------------------------------- #
# The token provider: installation first, personal token otherwise      #
# --------------------------------------------------------------------- #


def test_resolve_api_token_mints_an_installation_token_when_one_is_stored(
    configured_app, make_org, tenant_ctx, github_api
):
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    org = make_org("installed")
    with tenant_ctx(org.id):
        OrgCredentialVault().store(org_id=org.id, connector_type="github", credential_type="installation_id", value="123")
        from app import db

        db.session.commit()
        token = github_app.resolve_api_token(org.id)
    assert token == "ghs_mock_installation_token"


def test_resolve_api_token_falls_back_to_the_personal_token(configured_app, make_org, tenant_ctx):
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    org = make_org("personal")
    with tenant_ctx(org.id):
        OrgCredentialVault().store(org_id=org.id, connector_type="github", credential_type="api_token", value="ghp_a_personal_token")
        from app import db

        db.session.commit()
        token = github_app.resolve_api_token(org.id)
    assert token == "ghp_a_personal_token"


def test_resolve_api_token_never_crosses_organisations(configured_app, make_org, tenant_ctx, github_api):
    """Organisation A installs the App; organisation B, which never installed
    anything, never receives A's installation token."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    org_a, org_b = make_org("a"), make_org("b")
    with tenant_ctx(org_a.id):
        OrgCredentialVault().store(org_id=org_a.id, connector_type="github", credential_type="installation_id", value="123")
        from app import db

        db.session.commit()

    with tenant_ctx(org_b.id):
        token_b = github_app.resolve_api_token(org_b.id)
    assert token_b == ""  # org B has nothing stored at all: no installation, no personal token

    with tenant_ctx(org_a.id):
        token_a = github_app.resolve_api_token(org_a.id)
    assert token_a == "ghs_mock_installation_token"


# --------------------------------------------------------------------- #
# Round github-app01b: an installation is attached only by a person who  #
# can see it                                                            #
# --------------------------------------------------------------------- #


def _admin_client(app, db_session, login_as, org, label):
    from tests.test_connector_approval_flow import _make_admin

    user = _make_admin(db_session, org.id, label)
    client = app.test_client()
    login_as(client, user)
    return client


def _installed(client, state, installation_id="123", code="mock-code"):
    from urllib.parse import urlencode

    query = urlencode(
        {"installation_id": installation_id, "setup_action": "install", "state": state, "code": code}
    )
    return client.get("/admin/connectors/github/installed?" + query)


def _flashed(client):
    with client.session_transaction() as sess:
        return " ".join(message for _category, message in sess.get("_flashes", []))


def _stored_installation(org_id):
    """What the vault holds for *org_id*, read the way that organisation's own request would."""
    from flask import g

    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    g.current_org_id = org_id
    try:
        return OrgCredentialVault().retrieve(org_id, "github", "installation_id")
    finally:
        g.current_org_id = None


def test_organisation_b_cannot_attach_the_installation_of_organisation_a(
    configured_app, github_api, make_org, db_session, login_as
):
    """The swap: B starts an install (a valid state for B), then puts A's installation
    id in the address. B's GitHub user cannot see installation 123, so it is refused
    and nothing is stored for B."""
    org_a, org_b = make_org("a"), make_org("b")
    client = _admin_client(configured_app, db_session, login_as, org_b, "Bee")
    github_api.bundle["user/installations"] = (
        200,
        {"total_count": 1, "installations": [{"id": 999}]},
    )
    state_b = github_app.sign_state(org_b.id)

    _installed(client, state_b, installation_id="123")

    assert _stored_installation(org_b.id) is None
    assert _stored_installation(org_a.id) is None
    message = _flashed(client)
    assert "can't see that GitHub installation" in message or "cannot see that GitHub installation" in message
    assert "mock-code" not in message and "ghu_mock_user_token" not in message


def test_an_installation_bound_to_another_organisation_is_refused(
    configured_app, github_api, make_org, db_session, login_as
):
    """Even a GitHub user who can see the installation cannot attach it to a second
    organisation: it already belongs to organisation A."""
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    org_a, org_b = make_org("a"), make_org("b")
    OrgCredentialVault().store(
        org_id=org_a.id, connector_type="github", credential_type="installation_id", value="123"
    )
    db_session.flush()
    client = _admin_client(configured_app, db_session, login_as, org_b, "Bee")
    # GitHub says B's user can see 123 (they are in A's GitHub organisation too).
    _installed(client, github_app.sign_state(org_b.id), installation_id="123")

    assert _stored_installation(org_b.id) is None
    assert "already connected to another organisation" in _flashed(client)


def test_a_missing_code_is_refused_and_nothing_is_stored(
    configured_app, github_api, make_org, db_session, login_as
):
    org_b = make_org("b")
    client = _admin_client(configured_app, db_session, login_as, org_b, "Bee")
    from urllib.parse import urlencode

    state = github_app.sign_state(org_b.id)
    client.get("/admin/connectors/github/installed?" + urlencode(
        {"installation_id": "123", "setup_action": "install", "state": state}))
    assert _stored_installation(org_b.id) is None
    assert "Start again from Connect GitHub" in _flashed(client)


def test_a_rate_limit_is_named_as_one(configured_app, github_api, make_org, db_session, login_as):
    org_b = make_org("b")
    client = _admin_client(configured_app, db_session, login_as, org_b, "Bee")
    github_api.bundle["user/installations"] = (429, {"message": "slow down"})
    _installed(client, github_app.sign_state(org_b.id))
    assert "GitHub's rate limit was reached; try again in a few minutes" in _flashed(client)
    assert _stored_installation(org_b.id) is None


def test_a_403_rate_limit_is_named_as_one_but_a_plain_403_is_not(configured_app, github_api):
    with configured_app.app_context():
        limited = github_app._failure(
            SimpleNamespace(status_code=403, error="API rate limit exceeded for installation"), "x"
        )
        refused = github_app._failure(SimpleNamespace(status_code=403, error="Resource not accessible"), "x")
    assert "rate limit was reached" in str(limited)
    assert "rate limit" not in str(refused)


def test_a_refused_connection_leaves_no_installation_in_the_vault(
    configured_app, github_api, make_org, db_session, login_as, monkeypatch
):
    """The id is stored only once the connection is made. ``connect_and_start`` is made
    to refuse (as it does for an account GitHub cannot read), and the vault holds
    nothing afterwards. Its refusal is the one thing stubbed here."""
    from app.connectors import github as gh

    def refuse(*args, **kwargs):
        raise gh.ConnectRefused("GitHub could not read that account.")

    monkeypatch.setattr(gh, "connect_and_start", refuse)
    org_b = make_org("b")
    client = _admin_client(configured_app, db_session, login_as, org_b, "Bee")
    _installed(client, github_app.sign_state(org_b.id), installation_id="123")
    assert "could not read that account" in _flashed(client)
    assert _stored_installation(org_b.id) is None


def test_the_one_click_path_needs_the_client_id_and_secret(app, configured_app, monkeypatch):
    with configured_app.app_context():
        assert github_app.app_configured() is True
    for name in ("GITHUB_APP_CLIENT_ID", "GITHUB_APP_CLIENT_SECRET"):
        monkeypatch.setitem(configured_app.config, name, "")
        with configured_app.app_context():
            assert github_app.app_configured() is False
            assert github_app.install_url(1) is None
        monkeypatch.setitem(configured_app.config, name, "set-again")
