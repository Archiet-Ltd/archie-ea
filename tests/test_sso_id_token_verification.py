"""Verify id_token signature, claims and clock skew in the per-organisation
SSO flow.

Covers the _verify_id_token method on SSOService: signature, issuer,
audience, authorized party, expiry, issued-at, subject, nonce and
access-token-hash checks, algorithm pinning, and the once-only key refetch
on a rotated or unknown signing key. Also covers the /auth/sso/initiate and
/auth/sso/callback/oidc routes' handling of the login-session nonce.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import types
from unittest import mock

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(autouse=True)
def _registered_models(app):
    """Building an SSOConfig row here never touches the database, but the
    SQLAlchemy mapper for it (and for every other model reachable from it)
    is only fully configured once the application has been created, which
    is what the session-scoped ``app`` fixture does. No test in this file
    needs a database session for that; it only needs the models imported.
    """


@pytest.fixture(autouse=True)
def _fresh_jwks_cache():
    """The key set is cached in-process; every test starts with it empty."""
    from app.services.sso_service import SSOService

    SSOService._jwks_cache.clear()
    yield
    SSOService._jwks_cache.clear()


# ---------------------------------------------------------------------------
# Test key material (one RSA key pair, shared across tests)
# ---------------------------------------------------------------------------

_PRIVATE_KEY = rsa.generate_private_key(65537, 2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

_ISSUER = "https://idp.example.com"
_CLIENT_ID = "test-client-id"
_KID = "test-kid-1"
_JWKS_URI = "https://idp.example.com/jwks"
_NONCE = "nonce-issued-for-this-login"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwks():
    """Return a JWKS dict containing our test public key."""
    from joserfc.jwk import RSAKey

    jwk = RSAKey.import_key(
        _PUBLIC_KEY,
        {"kid": _KID, "use": "sig", "alg": "RS256"},
    ).as_dict()
    return {"keys": [jwk]}


def _make_token(claims=None, headers=None, key=None, drop=()):
    """Build and return a signed id_token string."""
    from joserfc import jwt
    from joserfc.jwk import RSAKey

    if key is None:
        key = _PRIVATE_KEY
    if headers is None:
        headers = {"alg": "RS256", "kid": _KID}
    base = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "user-1",
        "email": "test@example.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "nonce": _NONCE,
    }
    if claims:
        base.update(claims)
    for name in drop:
        base.pop(name, None)
    alg = headers.get("alg", "RS256")
    signing_key = RSAKey.import_key(key, {"kid": headers.get("kid", _KID), "alg": alg})
    return jwt.encode(headers, base, signing_key, algorithms=[alg])


def _none_alg_token(claims=None):
    """Build a hand-crafted token with header alg "none" and an empty signature."""
    base = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "user-1",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "nonce": _NONCE,
    }
    if claims:
        base.update(claims)
    header = {"alg": "none", "kid": _KID}
    header_b64 = _b64url(json.dumps(header).encode())
    payload_b64 = _b64url(json.dumps(base).encode())
    return f"{header_b64}.{payload_b64}."


def _hs256_token(key_bytes, claims=None):
    """Build a hand-crafted HS256 token, signed with an attacker-chosen key."""
    base = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "user-1",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "nonce": _NONCE,
    }
    if claims:
        base.update(claims)
    header = {"alg": "HS256", "kid": _KID}
    header_b64 = _b64url(json.dumps(header).encode())
    payload_b64 = _b64url(json.dumps(base).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(key_bytes, signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


def _make_discovery():
    return {
        "jwks_uri": _JWKS_URI,
        "issuer": _ISSUER,
    }


def _make_config():
    from app.models.sso_config import SSOConfig

    return SSOConfig(
        organization_id=1,
        protocol="oidc",
        client_id=_CLIENT_ID,
    )


def _mock_jwks_response():
    """Return a mock requests.get that returns our JWKS."""
    mock_resp = mock.MagicMock()
    mock_resp.json.return_value = _make_jwks()
    mock_resp.raise_for_status = lambda: None
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIdTokenVerification:
    def test_valid_token_passes_verification(self):
        token = _make_token()
        discovery = _make_discovery()
        config = _make_config()

        from app.services.sso_service import SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            claims = svc._verify_id_token(token, config, discovery, _NONCE)

        assert claims["sub"] == "user-1"
        assert claims["email"] == "test@example.com"
        assert claims["iss"] == _ISSUER
        assert claims["aud"] == _CLIENT_ID

    def test_bad_signature_is_refused(self):
        bad_key = rsa.generate_private_key(65537, 2048)
        token = _make_token(key=bad_key)
        discovery = _make_discovery()
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(token, config, discovery, _NONCE)

    def test_wrong_issuer_is_refused(self):
        token = _make_token({"iss": "https://evil-idp.example.com"})
        discovery = _make_discovery()
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(token, config, discovery, _NONCE)

    def test_wrong_audience_is_refused(self):
        token = _make_token({"aud": "wrong-client-id"})
        discovery = _make_discovery()
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(token, config, discovery, _NONCE)

    def test_expired_token_is_refused(self):
        token = _make_token({"exp": int(time.time()) - 3600})
        discovery = _make_discovery()
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(token, config, discovery, _NONCE)

    def test_missing_jwks_uri_is_refused(self):
        token = _make_token()
        discovery = {"issuer": _ISSUER}  # no jwks_uri
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with pytest.raises(SSONotConfiguredError):
            svc._verify_id_token(token, config, discovery, _NONCE)

    def test_missing_issuer_in_discovery_is_refused(self):
        token = _make_token()
        discovery = {"jwks_uri": _JWKS_URI}  # no issuer
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with pytest.raises(SSONotConfiguredError):
            svc._verify_id_token(token, config, discovery, _NONCE)

    def test_jwks_fetch_failure_is_refused(self):
        token = _make_token()
        discovery = _make_discovery()
        config = _make_config()

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.side_effect = OSError("connection refused")
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(token, config, discovery, _NONCE)

    def test_a_config_without_a_client_id_refuses_every_token(self):
        from app.models.sso_config import SSOConfig
        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        for client_id in (None, ""):
            config = SSOConfig(organization_id=1, protocol="oidc", client_id=client_id)
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(_make_token(), config, _make_discovery(), _NONCE)

    def test_no_exp_claim_is_refused(self):
        _refused(_make_token(drop=("exp",)))

    def test_no_iat_claim_is_refused(self):
        _refused(_make_token(drop=("iat",)))

    def test_no_sub_claim_is_refused(self):
        _refused(_make_token(drop=("sub",)))

    def test_missing_issuer_and_missing_audience_are_refused(self):
        _refused(_make_token(drop=("iss",)))
        _refused(_make_token(drop=("aud",)))

    @pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d", "not.base64!.at-all"])
    def test_malformed_tokens_are_refused(self, token):
        _refused(token)


def _verify(token, *, config=None, nonce=_NONCE, jwks_get=None, discovery=None, access_token=None):
    from app.services.sso_service import SSOService

    svc = SSOService()
    with mock.patch("requests.get") as mock_get:
        mock_get.return_value = _mock_jwks_response()
        if jwks_get is not None:
            mock_get.side_effect = jwks_get
        return svc._verify_id_token(
            token,
            config or _make_config(),
            discovery or _make_discovery(),
            nonce,
            access_token=access_token,
        )


def _refused(token, **kwargs):
    from app.services.sso_service import SSONotConfiguredError

    with pytest.raises(SSONotConfiguredError):
        _verify(token, **kwargs)


class TestNonceBinding:
    def test_matching_nonce_passes(self):
        assert _verify(_make_token())["nonce"] == _NONCE

    def test_token_without_a_nonce_claim_is_refused(self):
        _refused(_make_token(drop=("nonce",)))

    def test_token_from_another_login_is_refused(self):
        _refused(_make_token({"nonce": "nonce-of-someone-elses-login"}))

    def test_no_nonce_issued_for_this_login_refuses_even_a_valid_token(self):
        _refused(_make_token(), nonce="")
        _refused(_make_token(), nonce=None)


class TestClaimEdgeCases:
    def test_audience_as_an_array_containing_the_client_id_passes(self):
        claims = _verify(_make_token({"aud": ["other-client", _CLIENT_ID], "azp": _CLIENT_ID}))
        assert _CLIENT_ID in claims["aud"]

    def test_audience_array_without_the_client_id_is_refused(self):
        _refused(_make_token({"aud": ["other-client", "another-client"]}))

    def test_not_before_in_the_future_is_refused(self):
        _refused(_make_token({"nbf": int(time.time()) + 3600}))

    def test_issued_at_in_the_future_is_refused(self):
        _refused(_make_token({"iat": int(time.time()) + 3600}))


class TestForgedSignatures:
    def test_alg_none_is_refused(self):
        _refused(_none_alg_token())

    def test_hs256_signed_with_the_public_key_pem_bytes_is_refused(self):
        pem = _PUBLIC_KEY.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        _refused(_hs256_token(pem))

    def test_hs256_signed_with_the_jwk_json_is_refused(self):
        jwk_json = json.dumps(_make_jwks()["keys"][0]).encode()
        _refused(_hs256_token(jwk_json))


class TestAlgorithmPinning:
    def test_rs512_token_is_refused_when_only_rs256_is_advertised(self):
        token = _make_token(headers={"alg": "RS512", "kid": _KID})
        discovery = {**_make_discovery(), "id_token_signing_alg_values_supported": ["RS256"]}
        _refused(token, discovery=discovery)

    def test_any_token_is_refused_with_no_key_fetch_when_only_hs256_is_advertised(self):
        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        discovery = {**_make_discovery(), "id_token_signing_alg_values_supported": ["HS256"]}
        with mock.patch("requests.get") as mock_get:
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(_make_token(), _make_config(), discovery, _NONCE)
        assert mock_get.call_count == 0

    def test_rs256_token_passes_when_the_algorithm_field_is_absent(self):
        discovery = _make_discovery()
        assert "id_token_signing_alg_values_supported" not in discovery
        claims = _verify(_make_token(), discovery=discovery)
        assert claims["sub"] == "user-1"


class TestClockSkew:
    def test_iat_30_seconds_ahead_passes(self):
        claims = _verify(_make_token({"iat": int(time.time()) + 30}))
        assert claims["sub"] == "user-1"

    def test_nbf_30_seconds_ahead_passes(self):
        claims = _verify(_make_token({"nbf": int(time.time()) + 30}))
        assert claims["sub"] == "user-1"

    def test_exp_30_seconds_past_passes(self):
        claims = _verify(_make_token({"exp": int(time.time()) - 30}))
        assert claims["sub"] == "user-1"

    def test_exp_300_seconds_past_is_refused(self):
        _refused(_make_token({"exp": int(time.time()) - 300}))


class TestAuthorizedParty:
    def test_multi_audience_without_azp_is_refused(self):
        _refused(_make_token({"aud": [_CLIENT_ID, "other-client"]}))

    def test_multi_audience_with_azp_set_to_the_other_audience_is_refused(self):
        _refused(_make_token({"aud": [_CLIENT_ID, "other-client"], "azp": "other-client"}))

    def test_multi_audience_with_azp_set_to_the_client_id_passes(self):
        claims = _verify(_make_token({"aud": [_CLIENT_ID, "other-client"], "azp": _CLIENT_ID}))
        assert claims["azp"] == _CLIENT_ID

    def test_single_audience_with_a_mismatched_azp_is_refused(self):
        _refused(_make_token({"azp": "other-client"}))


class TestAccessTokenHash:
    def test_at_hash_mismatch_is_refused(self):
        _refused(_make_token({"at_hash": "not-the-real-hash"}), access_token="opaque-access-token-value")


class TestKeyCacheAndErrors:
    def test_the_key_set_is_fetched_once_within_the_ttl(self):
        from app.services.sso_service import SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            for _ in range(3):
                svc._verify_id_token(_make_token(), _make_config(), _make_discovery(), _NONCE)
        assert mock_get.call_count == 1

    def test_an_expired_cache_entry_is_refetched(self):
        from app.services.sso_service import SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            svc._verify_id_token(_make_token(), _make_config(), _make_discovery(), _NONCE)
            fetched_at, jwks = SSOService._jwks_cache[_JWKS_URI]
            SSOService._jwks_cache[_JWKS_URI] = (fetched_at - SSOService._JWKS_TTL_SECONDS - 1, jwks)
            svc._verify_id_token(_make_token(), _make_config(), _make_discovery(), _NONCE)
        assert mock_get.call_count == 2

    def test_a_rotated_signing_key_is_picked_up_without_waiting_for_the_ttl(self):
        from joserfc.jwk import RSAKey

        from app.services.sso_service import SSOService

        new_private = rsa.generate_private_key(65537, 2048)
        new_jwks = {
            "keys": [
                RSAKey.import_key(
                    new_private.public_key(),
                    {"kid": "rotated-kid", "use": "sig", "alg": "RS256"},
                ).as_dict()
            ]
        }
        rotated = mock.MagicMock()
        rotated.json.return_value = new_jwks
        rotated.raise_for_status = lambda: None

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            svc._verify_id_token(_make_token(), _make_config(), _make_discovery(), _NONCE)
            mock_get.return_value = rotated
            token = _make_token(key=new_private, headers={"alg": "RS256", "kid": "rotated-kid"})
            claims = svc._verify_id_token(token, _make_config(), _make_discovery(), _NONCE)
        assert claims["sub"] == "user-1"

    def test_a_bad_signature_refreshes_the_keys_once_and_stops(self):
        bad_key = rsa.generate_private_key(65537, 2048)
        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(_make_token(key=bad_key), _make_config(), _make_discovery(), _NONCE)
        assert mock_get.call_count == 2  # the first fetch, then one refresh

    def test_errors_do_not_reveal_the_key_set_address_or_the_library_text(self):
        from app.services.sso_service import SSONotConfiguredError

        with pytest.raises(SSONotConfiguredError) as fetch_failure:
            _verify(_make_token(), jwks_get=OSError("connection refused at 10.0.0.5"))
        assert _JWKS_URI not in str(fetch_failure.value)
        assert "10.0.0.5" not in str(fetch_failure.value)

        with pytest.raises(SSONotConfiguredError) as bad_token:
            _verify(_make_token({"iss": "https://evil-idp.example.com"}))
        assert str(bad_token.value) == "id_token verification failed"


class TestRefetchRules:
    def test_wrong_issuer_causes_exactly_one_key_fetch(self):
        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        with mock.patch("requests.get") as mock_get:
            mock_get.return_value = _mock_jwks_response()
            with pytest.raises(SSONotConfiguredError):
                svc._verify_id_token(
                    _make_token({"iss": "https://evil-idp.example.com"}),
                    _make_config(),
                    _make_discovery(),
                    _NONCE,
                )
        assert mock_get.call_count == 1

    def test_an_unknown_kid_whose_refetch_fails_keeps_the_key_error_as_the_cause(self):
        from joserfc.errors import InvalidKeyIdError

        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        token = _make_token(headers={"alg": "RS256", "kid": "unknown-kid"})
        responses = [_mock_jwks_response()]

        def _get(*args, **kwargs):
            if responses:
                return responses.pop()
            raise OSError("network unreachable")

        with mock.patch("requests.get", side_effect=_get):
            with pytest.raises(SSONotConfiguredError) as excinfo:
                svc._verify_id_token(token, _make_config(), _make_discovery(), _NONCE)
        assert isinstance(excinfo.value.__cause__, InvalidKeyIdError)


class TestFlowWiring:
    def _config(self):
        from app.models.sso_config import SSOConfig

        return SSOConfig(
            organization_id=1, protocol="oidc", client_id=_CLIENT_ID,
            idp_metadata_url="https://idp.example.com/.well-known",
        )

    def test_the_authorization_request_carries_a_fresh_nonce_that_is_returned(self):
        from urllib.parse import parse_qs, urlparse

        from app.services.sso_service import SSOService

        svc = SSOService()
        discovery = {"authorization_endpoint": "https://idp.example.com/authorize"}
        with mock.patch.object(SSOService, "_fetch_oidc_discovery", return_value=discovery):
            first = svc.initiate_oidc_flow(self._config(), "https://app.example.com/cb")
            second = svc.initiate_oidc_flow(self._config(), "https://app.example.com/cb")

        query = parse_qs(urlparse(first["redirect_url"]).query)
        assert query["nonce"] == [first["nonce"]] and len(first["nonce"]) >= 32
        assert first["nonce"] != second["nonce"] and first["nonce"] != first["state"]

    def test_the_callback_passes_the_expected_nonce_to_the_token_check(self):
        from app.services.sso_service import SSONotConfiguredError, SSOService

        svc = SSOService()
        discovery = {
            "token_endpoint": "https://idp.example.com/token",
            "jwks_uri": _JWKS_URI,
            "issuer": _ISSUER,
        }
        token_response = mock.MagicMock()
        token_response.json.return_value = {"id_token": _make_token()}
        token_response.raise_for_status = lambda: None
        with mock.patch.object(SSOService, "_fetch_oidc_discovery", return_value=discovery), mock.patch(
            "requests.post", return_value=token_response
        ), mock.patch("requests.get", return_value=_mock_jwks_response()):
            claims = svc.handle_oidc_callback(
                self._config(), "code", "state", "https://app.example.com/cb", expected_nonce=_NONCE
            )
            assert claims["sub"] == "user-1"

            with pytest.raises(SSONotConfiguredError):
                svc.handle_oidc_callback(self._config(), "code", "state", "https://app.example.com/cb")

    def test_the_callback_refuses_a_config_without_a_client_id(self):
        from app.models.sso_config import SSOConfig
        from app.services.sso_service import SSONotConfiguredError, SSOService

        config = SSOConfig(organization_id=1, protocol="oidc", client_id=None, idp_metadata_url="https://x")
        with pytest.raises(SSONotConfiguredError):
            SSOService().handle_oidc_callback(config, "code", "state", "https://app.example.com/cb")


class TestRouteWiring:
    def test_initiate_stores_the_returned_nonce_in_the_session(self, client):
        from app.modules.auth import sso_routes as routes_module

        config = types.SimpleNamespace(protocol="oidc", enabled=True, organization_id=7)

        with mock.patch.object(
            routes_module._svc, "get_config_for_email", return_value=config
        ), mock.patch.object(
            routes_module._svc,
            "initiate_oidc_flow",
            return_value={
                "redirect_url": "https://idp.example.com/authorize?x=1",
                "state": "state-abc",
                "nonce": "nonce-abc",
            },
        ):
            resp = client.get("/auth/sso/initiate?email=alice@acme.com")

        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess["sso_nonce"] == "nonce-abc"

    def test_callback_receives_the_stored_nonce_and_a_replay_is_refused(self, app, client):
        from app.models.organization import Organization
        from app.modules.auth import sso_routes as routes_module

        org = types.SimpleNamespace(sso_config=types.SimpleNamespace(enabled=True))
        received = {}

        def _handle_callback(config, code, state, redirect_uri, expected_nonce=None):
            received["nonce"] = expected_nonce
            return {"email": "alice@acme.com", "sub": "user-1"}

        with client.session_transaction() as sess:
            sess["sso_state"] = "state-abc"
            sess["sso_nonce"] = "nonce-abc"
            sess["sso_org_id"] = 7

        query = mock.MagicMock()
        query.get.return_value = org
        # Patching a class-level query property reads its current value
        # first (to restore it afterwards), and Flask-SQLAlchemy's query
        # property needs a current application to do that.
        with app.app_context(), mock.patch.object(Organization, "query", query), mock.patch.object(
            routes_module._svc, "handle_oidc_callback", side_effect=_handle_callback
        ), mock.patch.object(
            routes_module._svc, "provision_user", return_value=types.SimpleNamespace(id=1)
        ), mock.patch("app.services.session_registry.login_and_register"):
            resp = client.get("/auth/sso/callback/oidc?code=abc&state=state-abc")

        assert received["nonce"] == "nonce-abc"
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "sso_nonce" not in sess

        replay = client.get("/auth/sso/callback/oidc?code=abc&state=state-abc")
        assert replay.status_code == 400
