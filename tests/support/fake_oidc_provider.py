"""A stand-in Google or Microsoft sign-in server for tests.

It is a small WSGI app on a thread at 127.0.0.1. It serves discovery, the
signing keys, an authorize step that sends the browser straight back with a
code for the claims a test queued, and a token step that checks the client
id and secret, the code, the redirect address and the PKCE S256 verifier
before it returns an RS256 id_token. A test can tell it to misbehave: wrong
nonce, issuer, audience or algorithm, an expired token, missing claims.

Two shapes: ``google`` (one fixed issuer) and ``microsoft`` (an issuer with a
``{tenantid}`` template, and ``tid`` and ``oid`` claims).
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
WORK_TENANT = "5f1a2b3c-4d5e-4f60-8a7b-9c0d1e2f3a4b"

CLIENT_ID = "stand-in-client-id"
CLIENT_SECRET = "stand-in-client-secret"

_KEY = RSAKey.generate_key(2048, parameters={"kid": "stand-in-key", "use": "sig"})


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):  # no request log lines
        pass


class _ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class FakeOIDCProvider:
    def __init__(self, shape="google", client_id=CLIENT_ID, client_secret=CLIENT_SECRET):
        assert shape in ("google", "microsoft")
        self.shape = shape
        self.client_id = client_id
        self.client_secret = client_secret
        self._lock = threading.Lock()
        self.reset()
        self._server = make_server("127.0.0.1", 0, self._app, server_class=_ThreadedServer, handler_class=_QuietHandler)
        self.port = self._server.server_port
        self.base = "http://127.0.0.1:%d" % self.port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # ---- addresses -------------------------------------------------
    @property
    def metadata_url(self):
        if self.shape == "microsoft":
            return self.base + "/common/v2.0/.well-known/openid-configuration"
        return self.base + "/.well-known/openid-configuration"

    @property
    def issuer_template(self):
        return self.base + "/{tenantid}/v2.0" if self.shape == "microsoft" else self.base

    def issuer_for(self, tid=None):
        return self.issuer_template.replace("{tenantid}", tid) if tid else self.issuer_template

    # ---- test controls ---------------------------------------------
    def reset(self):
        with self._lock:
            self.queued = []
            self.codes = {}
            self.token_requests = []
            self.userinfo_requests = 0
            self.authorize_requests = 0
            self.issued_tokens = []

    def queue(self, claims, **misbehave):
        """The next authorize step signs this person in with ``claims``.

        ``misbehave`` keys: nonce, issuer, audience, algorithm ("HS256"), expired (bool), drop (list of claim names).
        """
        with self._lock:
            self.queued.append((dict(claims), dict(misbehave)))

    def google_claims(self, sub, email, verified=True, **extra):
        claims = {"sub": sub, "email": email, "email_verified": verified, "given_name": "Ada", "family_name": "Founder"}
        claims.update(extra)
        return claims

    def microsoft_claims(self, oid, email=None, tid=PERSONAL_TENANT, edov=None, **extra):
        claims = {"sub": "pairwise-" + oid, "oid": oid, "tid": tid, "name": "Ada Founder"}
        if email is not None:
            claims["email"] = email
        if edov is not None:
            claims["xms_edov"] = edov
        claims.update(extra)
        return claims

    @property
    def token_strings(self):
        return [t for pair in self.issued_tokens for t in pair]

    # ---- WSGI --------------------------------------------------------
    def _app(self, environ, start_response):
        path = environ["PATH_INFO"]
        method = environ["REQUEST_METHOD"]
        query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        if path.endswith("/.well-known/openid-configuration"):
            return self._json(start_response, self._discovery())
        if path == "/keys":
            return self._json(start_response, {"keys": [_KEY.as_dict(private=False)]})
        if path == "/authorize":
            return self._authorize(start_response, query)
        if path == "/token" and method == "POST":
            size = int(environ.get("CONTENT_LENGTH") or 0)
            form = {k: v[0] for k, v in parse_qs(environ["wsgi.input"].read(size).decode()).items()}
            return self._token(start_response, form)
        if path == "/userinfo":
            with self._lock:
                self.userinfo_requests += 1
            return self._json(start_response, {"sub": "never-used"})
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]

    def _json(self, start_response, body, status="200 OK"):
        data = json.dumps(body).encode()
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(data)))])
        return [data]

    def _discovery(self):
        return {
            "issuer": self.issuer_template,
            "authorization_endpoint": self.base + "/authorize",
            "token_endpoint": self.base + "/token",
            "jwks_uri": self.base + "/keys",
            "userinfo_endpoint": self.base + "/userinfo",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    def _authorize(self, start_response, query):
        with self._lock:
            self.authorize_requests += 1
            claims, misbehave = self.queued.pop(0) if self.queued else (None, {})
            if claims is None:
                start_response("400 Bad Request", [("Content-Type", "text/plain")])
                return [b"nothing queued"]
            code = secrets.token_urlsafe(16)
            self.codes[code] = {
                "claims": claims, "misbehave": misbehave, "nonce": query.get("nonce"),
                "challenge": query.get("code_challenge"), "method": query.get("code_challenge_method"),
                "redirect_uri": query.get("redirect_uri"), "used": False,
            }
        target = query["redirect_uri"] + "?" + urlencode({"code": code, "state": query.get("state", "")})
        start_response("302 Found", [("Location", target), ("Content-Length", "0")])
        return [b""]

    def _token(self, start_response, form):
        with self._lock:
            self.token_requests.append(dict(form))
            record = self.codes.get(form.get("code", ""))
        if (form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret
                or record is None or record["used"] or form.get("redirect_uri") != record["redirect_uri"]):
            return self._json(start_response, {"error": "invalid_grant"}, "400 Bad Request")
        verifier = form.get("code_verifier", "")
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if record["method"] != "S256" or digest != record["challenge"]:
            return self._json(start_response, {"error": "invalid_grant", "detail": "pkce"}, "400 Bad Request")
        record["used"] = True
        id_token = self._sign(record)
        access_token = "access-" + secrets.token_urlsafe(24)
        with self._lock:
            self.issued_tokens.append((access_token, id_token))
        return self._json(start_response, {"access_token": access_token, "id_token": id_token, "token_type": "Bearer", "expires_in": 3600})

    def _sign(self, record):
        claims = dict(record["claims"])
        misbehave = record["misbehave"]
        now = int(time.time())
        tid = claims.get("tid")
        claims.setdefault("iss", misbehave.get("issuer") or self.issuer_for(tid))
        if misbehave.get("issuer"):
            claims["iss"] = misbehave["issuer"]
        claims["aud"] = misbehave.get("audience") or self.client_id
        claims["nonce"] = misbehave.get("nonce") or record["nonce"]
        claims["iat"] = now - 5
        claims["exp"] = now - 3600 if misbehave.get("expired") else now + 600
        for name in misbehave.get("drop", ()):
            claims.pop(name, None)
        if misbehave.get("algorithm") == "HS256":
            # Signed with the public key material as the shared secret.
            secret = json.dumps(_KEY.as_dict(private=False)).encode()
            return jwt.encode({"alg": "HS256", "kid": "stand-in-key"}, claims, OctKey.import_key(secret))
        return jwt.encode({"alg": "RS256", "kid": "stand-in-key"}, claims, _KEY)

    def close(self):
        self._server.shutdown()
        self._server.server_close()
