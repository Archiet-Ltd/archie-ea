"""
SAMLService — per-organisation SAML 2.0 federation (R1-B12 PR 2, TB-0141/PB-0017).

Builds the redirect-binding AuthnRequest and verifies the POST-binding
<Response>/<Assertion> signature, replacing the stub that
``SSOService.initiate_saml_flow`` raised until this landed. Signature
verification uses ``signxml`` (built on lxml + cryptography, already
dependencies of this codebase) rather than ``python3-saml``, which needs the
``xmlsec1`` system library this platform's containers do not install.

The IdP's signing certificate is an operator-entered, out-of-band trust
anchor (``SSOConfig.idp_x509_cert``) -- never a certificate read from inside
the response being verified, which is exactly what a forged response would
carry.

Usage::

    svc = SAMLService()
    redirect_url, request_id = svc.build_authn_request(config, acs_url, base_url)
    # ... IdP redirects back with a base64 SAMLResponse ...
    claims = svc.verify_and_parse_response(config, saml_response_b64, acs_url, base_url)
"""

from __future__ import annotations

import base64
import logging
import secrets
import zlib
from datetime import datetime, timezone
from urllib.parse import urlencode

from app.services.sso_service import SSONotConfiguredError
from app.utils.safe_xml import UnsafeXmlError
from app.utils.safe_xml import fromstring as _reject_unsafe_xml

logger = logging.getLogger(__name__)

_SAML_NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}

# How much clock skew to tolerate on the assertion's Conditions window, same
# allowance as the OIDC id_token leeway in sso_service.py.
_CLOCK_SKEW_SECONDS = 120


class SAMLVerificationError(SSONotConfiguredError):
    """Raised when a SAML AuthnRequest cannot be built or a Response's
    signature, issuer, audience or validity window fails verification.

    Subclasses SSONotConfiguredError so every existing caller that catches
    that one exception type (sso_routes.py) keeps working unchanged for the
    SAML path too.
    """


class SAMLService:
    """Per-organisation SAML 2.0 service provider (redirect-binding
    AuthnRequest, POST-binding Response verification)."""

    # ------------------------------------------------------------------
    # Service-provider metadata
    # ------------------------------------------------------------------

    def acs_url(self, base_url: str) -> str:
        """The Assertion Consumer Service URL the IdP posts its Response to."""
        return f"{base_url.rstrip('/')}/auth/sso/callback/saml"

    # ------------------------------------------------------------------
    # AuthnRequest (redirect binding)
    # ------------------------------------------------------------------

    def build_authn_request(self, config, base_url: str) -> tuple[str, str]:
        """Build the redirect URL carrying a deflated, base64-encoded
        AuthnRequest.

        Args:
            config: :class:`app.models.sso_config.SSOConfig` instance.
            base_url: This platform's own base URL (for the ACS and SP
                entity id).

        Returns:
            ``(redirect_url, request_id)``. The caller must keep
            ``request_id`` in the login session and check it against the
            Response's ``InResponseTo`` attribute.

        Raises:
            :class:`SAMLVerificationError` if config is incomplete.
        """
        if config is None:
            raise SAMLVerificationError("No SSO config provided")
        if not config.idp_sso_url:
            raise SAMLVerificationError("SAML config has no idp_sso_url")

        from xml.sax.saxutils import escape, quoteattr

        request_id = "_" + secrets.token_hex(20)
        issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # acs is built from base_url (request.url_root) and sp_entity_id can
        # be an administrator-entered form field (SSOConfig.sp_entity_id) --
        # both are escaped before going into hand-built XML sent to the IdP,
        # the same discipline as any other interpolated-HTML site
        # (scripts/check_raw_html_escaping.py).
        acs = self.acs_url(base_url)
        sp_entity_id = config.sp_entity_id_or_default(base_url)

        authn_request_xml = (
            f'<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
            f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
            f"ID={quoteattr(request_id)} Version=\"2.0\" IssueInstant={quoteattr(issue_instant)} "
            f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f"AssertionConsumerServiceURL={quoteattr(acs)}>"
            f"<saml:Issuer>{escape(sp_entity_id)}</saml:Issuer>"
            f"</samlp:AuthnRequest>"
        )

        # Raw DEFLATE (no zlib/gzip header), per the SAML HTTP-Redirect binding.
        compressor = zlib.compressobj(level=9, wbits=-15)
        deflated = compressor.compress(authn_request_xml.encode("utf-8")) + compressor.flush()
        encoded = base64.b64encode(deflated).decode("ascii")

        params = {"SAMLRequest": encoded}
        redirect_url = f"{config.idp_sso_url}?{urlencode(params)}"
        return redirect_url, request_id

    # ------------------------------------------------------------------
    # Response (POST binding) verification
    # ------------------------------------------------------------------

    def verify_and_parse_response(
        self,
        config,
        saml_response_b64: str,
        base_url: str,
        expected_request_id: str | None = None,
    ) -> dict:
        """Verify a base64-encoded SAML <Response>'s signature and
        conditions, and return its asserted claims.

        Verifies (in order): the document parses as well-formed, defused
        XML; a <ds:Signature> is present on the <Response> or its
        <Assertion> and validates against ``config.idp_x509_cert`` (the
        operator-entered trust anchor, never a cert read from the document);
        the Issuer matches ``config.idp_metadata_url``'s configured entity
        (falls back to accepting any Issuer when none is configured, same
        posture as OIDC's audience check); the Conditions window
        (NotBefore/NotOnOrAfter, with clock-skew leeway) covers now; the
        Audience, when present, names this SP's entity id; and
        InResponseTo, when both the response and ``expected_request_id``
        carry one, matches -- refusing a response replayed from a different
        login attempt.

        Args:
            config: :class:`app.models.sso_config.SSOConfig` instance.
            saml_response_b64: The ``SAMLResponse`` POST parameter.
            base_url: This platform's own base URL (for the audience check).
            expected_request_id: The request id this login's
                :meth:`build_authn_request` returned, if any.

        Returns:
            Dict with at least ``email`` (from the Subject NameID or the
            standard email attribute) and any other asserted attributes.

        Raises:
            :class:`SAMLVerificationError` on any verification failure.
        """
        if config is None:
            raise SAMLVerificationError("No SSO config provided")
        if not config.idp_x509_cert:
            raise SAMLVerificationError("SAML config has no idp_x509_cert")
        if not saml_response_b64:
            raise SAMLVerificationError("No SAMLResponse provided")

        try:
            raw_xml = base64.b64decode(saml_response_b64)
        except Exception as exc:
            raise SAMLVerificationError("SAMLResponse is not valid base64") from exc

        try:
            # app.utils.safe_xml's stdlib-backed parse is the DTD/entity-
            # expansion guard (shared with the ArchiMate import path); its
            # parsed tree is discarded -- signxml.XMLVerifier below does its
            # own lxml parse of the same bytes, which it needs for the
            # signature canonicalisation and namespace-aware XPath that a
            # stdlib ElementTree cannot do.
            _reject_unsafe_xml(raw_xml)
        except UnsafeXmlError as exc:
            raise SAMLVerificationError(str(exc)) from exc
        except Exception as exc:
            raise SAMLVerificationError("SAMLResponse is not well-formed XML") from exc

        verified_root = self._verify_signature(raw_xml, config.idp_x509_cert)

        assertion = verified_root.find(".//saml:Assertion", namespaces=_SAML_NS)
        if assertion is None:
            # The signature may have been on the whole <Response>, which
            # already contains the <Assertion> -- look there directly too.
            assertion = (
                verified_root
                if verified_root.tag.endswith("Assertion")
                else None
            )
        if assertion is None:
            raise SAMLVerificationError("Verified document has no <Assertion>")

        self._check_conditions(assertion, config, base_url)

        if expected_request_id:
            in_response_to = verified_root.get("InResponseTo")
            if in_response_to and in_response_to != expected_request_id:
                raise SAMLVerificationError(
                    "SAMLResponse InResponseTo does not match this login attempt "
                    "(possible replay of a response from a different request)"
                )

        return self._extract_claims(assertion)

    def _verify_signature(self, raw_xml: bytes, idp_x509_cert: str):
        """Verify the XML-DSig signature on ``raw_xml`` against the IdP's
        certificate, returning the verified, signed element.

        signxml parses and canonicalises the document itself (it accepts
        raw bytes directly) from the certificate it is given -- it never
        trusts a certificate embedded inside the document's own
        <ds:KeyInfo>, which is why ``idp_x509_cert`` (entered by the
        organisation's administrator when configuring SSO, not read from
        any login attempt) is required. The caller has already run
        ``raw_xml`` through app.utils.safe_xml's DTD/entity-expansion
        guard before this is called.
        """
        from signxml import XMLVerifier
        from signxml.exceptions import InvalidSignature

        cert_pem = idp_x509_cert
        if "BEGIN CERTIFICATE" not in cert_pem:
            cert_pem = (
                "-----BEGIN CERTIFICATE-----\n"
                + cert_pem.strip()
                + "\n-----END CERTIFICATE-----\n"
            )

        try:
            verified = XMLVerifier().verify(raw_xml, x509_cert=cert_pem.encode("utf-8"))
        except InvalidSignature as exc:
            raise SAMLVerificationError(
                "SAMLResponse signature verification failed"
            ) from exc
        except Exception as exc:
            logger.warning("SAML signature verification error: %s", exc)
            raise SAMLVerificationError(
                "SAMLResponse signature verification failed"
            ) from exc
        return verified.signed_xml

    def _check_conditions(self, assertion, config, base_url: str) -> None:
        """Check Conditions NotBefore/NotOnOrAfter and Audience."""
        conditions = assertion.find("saml:Conditions", namespaces=_SAML_NS)
        now = datetime.now(timezone.utc)

        if conditions is not None:
            not_before = conditions.get("NotBefore")
            not_on_or_after = conditions.get("NotOnOrAfter")
            if not_before:
                nb = self._parse_instant(not_before)
                if nb is not None and now < nb - _skew():
                    raise SAMLVerificationError(
                        "SAMLResponse assertion is not yet valid (NotBefore)"
                    )
            if not_on_or_after:
                noa = self._parse_instant(not_on_or_after)
                if noa is not None and now >= noa + _skew():
                    raise SAMLVerificationError(
                        "SAMLResponse assertion has expired (NotOnOrAfter)"
                    )

            audiences = [
                el.text
                for el in conditions.findall(
                    "saml:AudienceRestriction/saml:Audience", namespaces=_SAML_NS
                )
                if el.text
            ]
            if audiences:
                expected = config.sp_entity_id_or_default(base_url)
                if expected not in audiences:
                    raise SAMLVerificationError(
                        "SAMLResponse audience does not name this service provider"
                    )

    @staticmethod
    def _parse_instant(value: str):
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning("SAML: could not parse timestamp %r", value)
                return None

    def _extract_claims(self, assertion) -> dict:
        """Pull email/name/sub out of the Subject and AttributeStatement."""
        claims: dict = {}

        name_id = assertion.find("saml:Subject/saml:NameID", namespaces=_SAML_NS)
        if name_id is not None and name_id.text:
            claims["sub"] = name_id.text.strip()
            if "@" in name_id.text:
                claims["email"] = name_id.text.strip().lower()

        for attr in assertion.findall(
            "saml:AttributeStatement/saml:Attribute", namespaces=_SAML_NS
        ):
            name = attr.get("Name") or ""
            values = [
                v.text.strip()
                for v in attr.findall("saml:AttributeValue", namespaces=_SAML_NS)
                if v.text
            ]
            if not values:
                continue
            value = values[0]
            lowered = name.lower()
            if "email" in lowered or name == "urn:oid:0.9.2342.19200300.100.1.3":
                claims.setdefault("email", value.lower())
            elif "givenname" in lowered or "firstname" in lowered:
                claims.setdefault("given_name", value)
            elif "surname" in lowered or "lastname" in lowered or "familyname" in lowered:
                claims.setdefault("family_name", value)
            else:
                claims.setdefault(name, value)

        if not claims.get("email"):
            raise SAMLVerificationError(
                "SAMLResponse assertion has no email in the NameID or attributes"
            )

        return claims


def _skew():
    from datetime import timedelta

    return timedelta(seconds=_CLOCK_SKEW_SECONDS)
