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
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from app.services.saml_service import SAMLService, SAMLVerificationError
from app.services.sso_service import SSONotConfiguredError


ACS = "https://app.example.com/auth/sso/callback/saml"
SP = "https://app.example.com/auth/sso/metadata/1"
IDP = "https://idp.example.com"
REQUEST_ID = "_request-1"


class _FakeConfig:
    def __init__(self, cert_pem, sp_entity_id=SP, organization_id=1, idp_entity_id=IDP):
        self.idp_x509_cert = cert_pem
        self.idp_metadata_url = None
        self.idp_entity_id = idp_entity_id
        self.sp_entity_id = sp_entity_id
        self.organization_id = organization_id

    def sp_entity_id_or_default(self, base_url):
        return self.sp_entity_id


@pytest.fixture
def org_id(app, db_session, make_org):
    return make_org("saml").id


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
    audience=SP,
    not_before_delta=datetime.timedelta(minutes=-1),
    not_on_or_after_delta=datetime.timedelta(minutes=5),
    given_name="Alice",
    issuer=IDP,
    recipient=ACS,
    in_response_to=REQUEST_ID,
    confirmation_delta=datetime.timedelta(minutes=5),
    assertion_id=None,
    with_conditions=True,
    with_audience=True,
    with_confirmation=True,
    with_issuer=True,
    conditions_not_on_or_after=None,
    first_confirmation_in_response_to=None,
):
    import signxml

    now = datetime.datetime.now(datetime.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    not_before = (now + not_before_delta).strftime(fmt)
    not_on_or_after = conditions_not_on_or_after or (now + not_on_or_after_delta).strftime(fmt)
    confirm_until = (now + confirmation_delta).strftime(fmt)
    issue_instant = now.strftime(fmt)
    assertion_id = assertion_id or "_a" + uuid.uuid4().hex
    irt = f' InResponseTo="{in_response_to}"' if in_response_to else ""
    confirmation = (
        f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData Recipient="{recipient}" NotOnOrAfter="{confirm_until}"{irt}/>'
        f"</saml:SubjectConfirmation>"
        if with_confirmation
        else ""
    )
    if first_confirmation_in_response_to:
        # A second bearer confirmation, listed before the real one, answering another request.
        confirmation = (
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData Recipient="{recipient}" NotOnOrAfter="{confirm_until}" '
            f'InResponseTo="{first_confirmation_in_response_to}"/></saml:SubjectConfirmation>'
        ) + confirmation
    audience_xml = (
        f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>"
        if with_audience
        else ""
    )
    conditions = (
        f'<saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">{audience_xml}</saml:Conditions>'
        if with_conditions
        else ""
    )
    issuer_xml = f"<saml:Issuer>{issuer}</saml:Issuer>" if with_issuer else ""
    assertion_xml = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="{assertion_id}" Version="2.0" IssueInstant="{issue_instant}">
{issuer_xml}
<saml:Subject><saml:NameID>{email}</saml:NameID>{confirmation}</saml:Subject>
{conditions}
<saml:AttributeStatement>
<saml:Attribute Name="givenName"><saml:AttributeValue>{given_name}</saml:AttributeValue></saml:Attribute>
<saml:Attribute Name="urn:oasis:names:tc:SAML:attribute:subject-id"><saml:AttributeValue>subject-{email}</saml:AttributeValue></saml:Attribute>
</saml:AttributeStatement>
</saml:Assertion>"""

    root = etree.fromstring(assertion_xml.encode())
    signed_root = signxml.XMLSigner(
        method=signxml.methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256"
    ).sign(root, key=key_pem.encode(), cert=cert_pem.encode())
    return base64.b64encode(etree.tostring(signed_root)).decode()


def _verify(config, b64, request_id=REQUEST_ID):
    return SAMLService().verify_and_parse_response(config, b64, "https://app.example.com", request_id)


def test_verify_and_parse_response_accepts_a_validly_signed_assertion(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    claims = _verify(_FakeConfig(cert_pem, organization_id=org_id), _signed_assertion_b64(key_pem, cert_pem))
    assert claims["email"] == "alice@acme.com" and claims["given_name"] == "Alice"


def test_a_tampered_assertion_is_refused(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    raw = bytearray(base64.b64decode(_signed_assertion_b64(key_pem, cert_pem)))
    raw[raw.find(b"Alice")] = ord("X")
    with pytest.raises(SAMLVerificationError):
        _verify(_FakeConfig(cert_pem, organization_id=org_id), base64.b64encode(bytes(raw)).decode())


def test_a_signature_from_the_wrong_key_is_refused(_idp_keypair, org_id):
    """Another IdP's perfectly valid signature must not verify against this organisation's certificate."""
    _k, cert_pem = _idp_keypair
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "other-idp.example.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    other_cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(other_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1)).sign(other_key, hashes.SHA256())
    )
    other_key_pem = other_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    b64 = _signed_assertion_b64(other_key_pem, other_cert.public_bytes(serialization.Encoding.PEM).decode())
    with pytest.raises(SAMLVerificationError):
        _verify(_FakeConfig(cert_pem, organization_id=org_id), b64)


def test_an_expired_assertion_is_refused(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    b64 = _signed_assertion_b64(
        key_pem, cert_pem, not_before_delta=datetime.timedelta(minutes=-30),
        not_on_or_after_delta=datetime.timedelta(minutes=-10),
    )
    with pytest.raises(SAMLVerificationError, match="expired"):
        _verify(_FakeConfig(cert_pem, organization_id=org_id), b64)


def test_the_wrong_audience_is_refused(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    with pytest.raises(SAMLVerificationError, match="audience"):
        _verify(_FakeConfig(cert_pem, organization_id=org_id),
                _signed_assertion_b64(key_pem, cert_pem, audience="https://someone-elses-app.example.com"))


def test_a_missing_audience_or_conditions_is_refused(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    with pytest.raises(SAMLVerificationError, match="audience"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, with_audience=False))
    with pytest.raises(SAMLVerificationError, match="Conditions"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, with_conditions=False))


def test_the_issuer_must_be_the_identity_provider_the_administrator_entered(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    with pytest.raises(SAMLVerificationError, match="different identity provider"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, issuer="https://evil.example"))
    with pytest.raises(SAMLVerificationError, match="no issuer"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, with_issuer=False))
    with pytest.raises(SAMLVerificationError, match="entity id"):
        _verify(_FakeConfig(cert_pem, organization_id=org_id, idp_entity_id=None), _signed_assertion_b64(key_pem, cert_pem))


def test_in_response_to_is_required_and_must_match_this_login(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    with pytest.raises(SAMLVerificationError, match="does not match"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, in_response_to="_other"))
    with pytest.raises(SAMLVerificationError, match="which sign-in"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, in_response_to=None))
    with pytest.raises(SAMLVerificationError, match="cannot be matched"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem), request_id=None)


def test_the_subject_confirmation_must_be_addressed_to_this_service_and_current(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    with pytest.raises(SAMLVerificationError, match="not addressed"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, recipient="https://evil.example/acs"))
    with pytest.raises(SAMLVerificationError, match="not addressed"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, confirmation_delta=datetime.timedelta(minutes=-10)))
    with pytest.raises(SAMLVerificationError, match="bearer"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, with_confirmation=False))


def test_an_assertion_cannot_be_used_twice_and_a_refused_one_is_not_remembered(_idp_keypair, org_id, make_org):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    b64 = _signed_assertion_b64(key_pem, cert_pem, assertion_id="_replayed")
    assert _verify(config, b64)["email"] == "alice@acme.com"
    with pytest.raises(SAMLVerificationError, match="already been used"):
        _verify(config, b64)
    # The same id under another organisation is a different assertion for a different login.
    other = _FakeConfig(cert_pem, organization_id=make_org("saml-other").id)
    assert _verify(other, b64)["email"] == "alice@acme.com"
    # An assertion refused for another reason is not consumed.
    bad = _signed_assertion_b64(key_pem, cert_pem, assertion_id="_not-consumed", issuer="https://evil.example")
    with pytest.raises(SAMLVerificationError):
        _verify(config, bad)
    from app.models.sso_assertion_id import SSOAssertionId

    assert SSOAssertionId.query.filter_by(assertion_id="_not-consumed").count() == 0


def test_a_certificate_is_required(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    config.idp_x509_cert = None
    with pytest.raises(SSONotConfiguredError, match="idp_x509_cert"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem))


def test_malformed_base64_is_refused(_idp_keypair, org_id):
    _k, cert_pem = _idp_keypair
    with pytest.raises(SAMLVerificationError, match="base64"):
        _verify(_FakeConfig(cert_pem, organization_id=org_id), "not-valid-base64!!!")


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


def test_confirmations_are_alternatives_whatever_their_order(_idp_keypair, org_id):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    # A confirmation for another request listed first does not hide the one that answers this login.
    claims = _verify(config, _signed_assertion_b64(key_pem, cert_pem, first_confirmation_in_response_to="_other"))
    assert claims["email"] == "alice@acme.com"
    # With only confirmations for other requests the response is still refused.
    with pytest.raises(SAMLVerificationError, match="does not match"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, in_response_to="_other", first_confirmation_in_response_to="_another"))


@pytest.mark.parametrize("stamp", ["2000-01-01T00:00:00+00:00", "next tuesday", "2099-01-01 00:00:00"])
def test_an_unreadable_expiry_is_refused_not_replaced_by_the_current_time(_idp_keypair, org_id, stamp):
    key_pem, cert_pem = _idp_keypair
    config = _FakeConfig(cert_pem, organization_id=org_id)
    with pytest.raises(SAMLVerificationError, match="not a readable time"):
        _verify(config, _signed_assertion_b64(key_pem, cert_pem, conditions_not_on_or_after=stamp))
