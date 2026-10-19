"""Portfolio search seed text must not cross organisations.

``PortfolioSearchService._get_entity_text`` resolves a short type key to a model
and reads its name and description. ``Session.get`` returns an identity-map hit
without emitting SQL, so the ORM tenant filter never runs for it; the lookup
therefore carries its own organisation predicate.
"""

import uuid

import pytest

from app.services.portfolio_search_service import PortfolioSearchService


def _make(entity_type, org_id, tag):
    name = f"{entity_type} {tag}"
    desc = f"description {tag}"
    if entity_type == "application":
        from app.models.application_portfolio import ApplicationComponent

        return ApplicationComponent(name=name, description=desc, organization_id=org_id)
    if entity_type == "capability":
        from app.models.business_capabilities import BusinessCapability

        return BusinessCapability(name=name, description=desc, organization_id=org_id)
    if entity_type == "archimate_element":
        from app.models.archimate_core import ArchiMateElement

        return ArchiMateElement(
            name=name, description=desc, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
    if entity_type == "solution":
        from app.models.solution_models import Solution

        return Solution(name=name, description=desc, organization_id=org_id)
    raise AssertionError(entity_type)


TENANT_TYPES = ["application", "capability", "archimate_element", "solution"]


@pytest.mark.parametrize("entity_type", TENANT_TYPES)
def test_other_orgs_record_is_not_returned(db_session, make_org, tenant_ctx, entity_type):
    org_a, org_b = make_org("a"), make_org("b")
    tag = uuid.uuid4().hex[:8]
    row = _make(entity_type, org_a.id, tag)
    db_session.add(row)
    db_session.flush()

    svc = PortfolioSearchService()
    with tenant_ctx(org_b.id):
        # Row is in the session identity map here: the case session.get leaked.
        assert svc._get_entity_text(entity_type, row.id) is None


@pytest.mark.parametrize("entity_type", TENANT_TYPES)
def test_same_org_record_is_returned(db_session, make_org, tenant_ctx, entity_type):
    org_a = make_org("a")
    tag = uuid.uuid4().hex[:8]
    row = _make(entity_type, org_a.id, tag)
    db_session.add(row)
    db_session.flush()

    with tenant_ctx(org_a.id):
        text = PortfolioSearchService()._get_entity_text(entity_type, row.id)
    assert text == f"{entity_type} {tag} description {tag}"


def test_vendor_is_shared_reference_data(db_session, make_org, tenant_ctx):
    """VendorOrganization is deliberately not tenant-owned (see its docstring and
    tests/test_vendor_tenancy_policy.py): every organisation sees the same
    catalogue, so there is no other-organisation record to withhold."""
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a, org_b = make_org("a"), make_org("b")
    tag = uuid.uuid4().hex[:8]
    row = VendorOrganization(name=f"vendor {tag}", description=f"description {tag}")
    db_session.add(row)
    db_session.flush()

    svc = PortfolioSearchService()
    for org in (org_a, org_b):
        with tenant_ctx(org.id):
            assert svc._get_entity_text("vendor", row.id) == f"vendor {tag} description {tag}"
