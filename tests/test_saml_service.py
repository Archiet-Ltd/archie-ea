"""Verify SAML Response signature, conditions and claim extraction
(R1-B12 PR 2, TB-0141/PB-0017).

Builds a self-signed IdP certificate and a signed <Assertion> directly with
signxml -- the "local test identity provider" the brief's acceptance
criteria ask for, in unit-test form rather than a running server, since
SAMLService only ever needs a well-formed signed document and an
operator-entered certificate to verify against.
"""

from __future__ import annotations

import base64
import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from app.services.saml_service import SAMLService, SAMLVerificationError
from app.services.sso_service import SSONotConfiguredError


class _FakeConfig:
    def __init__(self, cert_pem, sp_entity_id="https://app.example.com/auth/sso/metadata/1"):
        self.idp_x509_cert = cert_pem
        self.idp_metadata_url = None
        self.sp_entity_id = sp_entity_id
        self.organization_id = 1

    def sp_entity_id_or_default(self, base_url):
        return self.sp_entity_id


@pytest.fixture(scope="module")
def _idp_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return key_pem, cert_pem


def _signed_assertion_b64(
    key_pem,
    cert_pem,
    *,
    email="alice@acme.com",
    audience="https://app.example.com/auth/sso/metadata/1",
    not_before_delta=datetime.timedelta(minutes=-1),
    not_on_or_after_delta=datetime.timedelta(minutes=5),
    given_name="Alice",
):
    import signxml

    now = datetime.datetime.now(datetime.timezone.utc)
    not_before = (now + not_before_delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    not_on_or_after = (now + not_on_or_after_delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    issue_instant = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    assertion_xml = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="_assertion1" Version="2.0" IssueInstant="{issue_instant}">
<saml:Issuer>https://idp.example.com</saml:Issuer>
<saml:Subject><saml:NameID>{email}</saml:NameID></saml:Subject>
<saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">
<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
</saml:Conditions>
<saml:AttributeStatement>
<saml:Attribute Name="givenName"><saml:AttributeValue>{given_name}</saml:AttributeValue></saml:Attribute>
</saml:AttributeStatement>
</saml:Assertion>"""

    root = etree.fromstring(assertion_xml.encode())
    signed_root = signxml.XMLSigner(
        method=signxml.methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256"
    ).sign(root, key=key_pem.encode(), cert=cert_pem.encode())
    signed_bytes = etree.tostring(signed_root)
    return base64.b64encode(signed_bytes).decode()


def test_verify_and_parse_response_accepts_a_validly_signed_assertion(_idp_keypair):
    key_pem, cert_pem = _idp_keypair
    b64 = _signed_assertion_b64(key_pem, cert_pem)

    claims = SAMLService().verify_and_parse_response(
        _FakeConfig(cert_pem), b64, "https://app.example.com"
    )

    assert claims["email"] == "alice@acme.com"
    assert claims["given_name"] == "Alice"


def test_verify_and_parse_response_refuses_a_tampered_assertion(_idp_keypair):
    key_pem, cert_pem = _idp_keypair
    b64 = _signed_assertion_b64(key_pem, cert_pem)
    signed_bytes = bytearray(base64.b64decode(b64))
    idx = signed_bytes.find(b"Alice")
    signed_bytes[idx] = ord("X")
    tampered_b64 = base64.b64encode(bytes(signed_bytes)).decode()

    with pytest.raises(SAMLVerificationError):
        SAMLService().verify_and_parse_response(
            _FakeConfig(cert_pem), tampered_b64, "https://app.example.com"
        )


def test_verify_and_parse_response_refuses_a_signature_from_the_wrong_key(_idp_keypair):
    """A different IdP's perfectly valid signature must not verify against
    this organisation's configured certificate -- the whole point of
    per-organisation IdP certificates."""
    _key_pem, cert_pem = _idp_keypair

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "other-idp.example.com")])
    other_cert = (
        x509.CertificateBuilder()
        .subject_name(other_subject)
        .issuer_name(other_subject)
        .public_key(other_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .sign(other_key, hashes.SHA256())
    )
    other_key_pem = other_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    other_cert_pem = other_cert.public_bytes(serialization.Encoding.PEM).decode()

    b64 = _signed_assertion_b64(other_key_pem, other_cert_pem)

    with pytest.raises(SAMLVerificationError):
        # Verifying against the FIRST idp's cert, not the one that actually signed it.
        SAMLService().verify_and_parse_response(
            _FakeConfig(cert_pem), b64, "https://app.example.com"
        )


def test_verify_and_parse_response_refuses_an_expired_assertion(_idp_keypair):
    key_pem, cert_pem = _idp_keypair
    b64 = _signed_assertion_b64(
        key_pem, cert_pem,
        not_before_delta=datetime.timedelta(minutes=-30),
        not_on_or_after_delta=datetime.timedelta(minutes=-10),
    )

    with pytest.raises(SAMLVerificationError, match="expired"):
        SAMLService().verify_and_parse_response(
            _FakeConfig(cert_pem), b64, "https://app.example.com"
        )


def test_verify_and_parse_response_refuses_the_wrong_audience(_idp_keypair):
    key_pem, cert_pem = _idp_keypair
    b64 = _signed_assertion_b64(key_pem, cert_pem, audience="https://someone-elses-app.example.com")

    with pytest.raises(SAMLVerificationError, match="audience"):
        SAMLService().verify_and_parse_response(
            _FakeConfig(cert_pem), b64, "https://app.example.com"
        )


def test_verify_and_parse_response_requires_an_idp_certificate(_idp_keypair):
    _key_pem, cert_pem = _idp_keypair
    b64 = _signed_assertion_b64(_key_pem, cert_pem)

    config = _FakeConfig(cert_pem)
    config.idp_x509_cert = None

    with pytest.raises(SSONotConfiguredError, match="idp_x509_cert"):
        SAMLService().verify_and_parse_response(config, b64, "https://app.example.com")


def test_verify_and_parse_response_refuses_malformed_base64(_idp_keypair):
    _key_pem, cert_pem = _idp_keypair

    with pytest.raises(SAMLVerificationError, match="base64"):
        SAMLService().verify_and_parse_response(
            _FakeConfig(cert_pem), "not-valid-base64!!!", "https://app.example.com"
        )


def test_build_authn_request_needs_idp_sso_url():
    config = _FakeConfig("irrelevant")
    config.idp_sso_url = None

    with pytest.raises(SSONotConfiguredError, match="idp_sso_url"):
        SAMLService().build_authn_request(config, "https://app.example.com")


def test_build_authn_request_produces_a_redirect_with_a_request_id():
    config = _FakeConfig("irrelevant")
    config.idp_sso_url = "https://idp.example.com/saml/sso"

    redirect_url, request_id = SAMLService().build_authn_request(config, "https://app.example.com")

    assert redirect_url.startswith("https://idp.example.com/saml/sso?SAMLRequest=")
    assert request_id.startswith("_")
    assert len(request_id) > 10
