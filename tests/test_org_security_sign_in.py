"""sign_in_refusal: the address rule for one person against one address."""

from types import SimpleNamespace

import pytest

from app.services import org_security


def _user(platform_admin=False):
    return SimpleNamespace(is_platform_admin=platform_admin, organization_id=1)


@pytest.fixture
def switch(monkeypatch):
    def configure(listed):
        monkeypatch.setattr(org_security, "_org_ids", lambda user: {1})
        values = {org_security.ADDRESS_SWITCH: True, org_security.ADDRESS_LIST: listed}
        monkeypatch.setattr(org_security, "get", lambda org_id, name, default=None: values.get(name, default))

    return configure


def test_listed_address_and_range_pass(switch):
    switch("203.0.113.9\n198.51.100.0/24")
    assert org_security.sign_in_refusal(_user(), "203.0.113.9") is None
    assert org_security.sign_in_refusal(_user(), "198.51.100.77") is None


def test_unlisted_unreadable_and_empty_addresses_are_refused(switch):
    switch("203.0.113.9")
    for ip in ("203.0.113.10", "not an address", "", None):
        assert org_security.sign_in_refusal(_user(), ip) == org_security.REFUSAL


def test_ipv6_range(switch):
    switch("2001:db8::/32")
    assert org_security.sign_in_refusal(_user(), "2001:db8::5") is None
    assert org_security.sign_in_refusal(_user(), "2001:db9::5") == org_security.REFUSAL


def test_platform_administrator_is_exempt_and_organisation_administrator_is_not(switch):
    switch("203.0.113.9")
    assert org_security.sign_in_refusal(_user(platform_admin=True), "198.51.100.1") is None
    org_admin = SimpleNamespace(is_platform_admin=False, is_org_admin=True, organization_id=1)
    assert org_security.sign_in_refusal(org_admin, "198.51.100.1") == org_security.REFUSAL
