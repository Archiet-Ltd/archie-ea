"""Pure unit tests (no database): SAML stable-subject choice and domain spelling."""

from __future__ import annotations

import pytest
from lxml import etree

from app.models.sso_config import normalise_email_domain, split_email_domains
from app.services.saml_service import SAMLService, SAMLVerificationError

_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_EMAIL_FMT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
_UNSPEC = "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
_PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
_TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"
_SUBJECT_ID = "urn:oasis:names:tc:SAML:attribute:subject-id"
_PAIRWISE_ID = "urn:oasis:names:tc:SAML:attribute:pairwise-id"


def _assertion(name_id=None, fmt=None, attributes=()):
    assertion = etree.Element(f"{{{_NS}}}Assertion", nsmap={"saml": _NS})
    if name_id is not None:
        subject = etree.SubElement(assertion, f"{{{_NS}}}Subject")
        element = etree.SubElement(subject, f"{{{_NS}}}NameID")
        if fmt:
            element.set("Format", fmt)
        element.text = name_id
    statement = etree.SubElement(assertion, f"{{{_NS}}}AttributeStatement")
    for name, value in attributes:
        attribute = etree.SubElement(statement, f"{{{_NS}}}Attribute", Name=name)
        etree.SubElement(attribute, f"{{{_NS}}}AttributeValue").text = value
    return assertion


def _claims(*args, **kwargs):
    return SAMLService()._extract_claims(_assertion(*args, **kwargs))


EMAIL = ("email", "pat@acme.example")


def test_pairwise_id_beats_subject_id_and_persistent_nameid():
    claims = _claims("persist-1", _PERSISTENT,
                     [EMAIL, (_SUBJECT_ID, "subj-1"), (_PAIRWISE_ID, "pair-1")])
    assert claims["sub"] == "pair-1"


def test_subject_id_beats_persistent_nameid():
    claims = _claims("persist-1", _PERSISTENT, [EMAIL, (_SUBJECT_ID, "subj-1")])
    assert claims["sub"] == "subj-1"


def test_persistent_nameid_is_used_when_no_attribute_is_sent():
    assert _claims("persist-1", _PERSISTENT, [EMAIL])["sub"] == "persist-1"


def test_response_without_nameid_but_with_subject_id_is_accepted():
    claims = _claims(None, None, [EMAIL, (_SUBJECT_ID, "subj-9")])
    assert claims["sub"] == "subj-9"
    assert claims["email"] == "pat@acme.example"


@pytest.mark.parametrize("fmt", [_EMAIL_FMT, _UNSPEC, None])
def test_email_or_unspecified_nameid_is_an_address_never_the_subject(fmt):
    with pytest.raises(SAMLVerificationError) as refused:
        _claims("former.employee@acme.example", fmt)
    assert "stable" in str(refused.value)
    # With a stable attribute the NameID still supplies the address only.
    claims = _claims("former.employee@acme.example", fmt, [(_SUBJECT_ID, "subj-1")])
    assert claims["sub"] == "subj-1"
    assert claims["email"] == "former.employee@acme.example"


def test_reissued_email_nameid_cannot_take_over_the_former_employees_account():
    """The address is reissued to a new person; the IdP sends no stable subject
    for them. Their response must not yield the old subject, nor any subject."""
    old = _claims("jo@acme.example", _EMAIL_FMT, [(_SUBJECT_ID, "subj-old")])
    assert old["sub"] == "subj-old"
    with pytest.raises(SAMLVerificationError):
        _claims("jo@acme.example", _EMAIL_FMT)
    new = _claims("jo@acme.example", _EMAIL_FMT, [(_SUBJECT_ID, "subj-new")])
    assert new["sub"] == "subj-new" != old["sub"]


def test_transient_nameid_without_stable_attribute_is_refused():
    with pytest.raises(SAMLVerificationError):
        _claims("t-123", _TRANSIENT, [EMAIL])


def test_domain_spelling_is_normalised():
    plain = split_email_domains("bigcorp.com")
    assert split_email_domains("BigCorp.com.") == plain
    assert split_email_domains(" bigcorp.com. , ") == plain
    assert normalise_email_domain("m\u00fcnchen.example") == "xn--mnchen-3ya.example"
    assert split_email_domains("xn--mnchen-3ya.example") == split_email_domains("M\u00dcNCHEN.example.")
