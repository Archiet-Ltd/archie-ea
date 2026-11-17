"""QA-033: after the demo seed (and a reset), the capability heat map has real domain rows
whose counts match the capabilities, not one 'No domain' strip."""
from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company


def _heatmap(tenant_ctx):
    from app import db
    from app.models.organization import Organization
    from app.modules.capabilities.services.capability_heatmap_service import CapabilityHeatmapService

    org_id = db.session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    with tenant_ctx(org_id):
        return org_id, CapabilityHeatmapService().get_maturity_heatmap()


def test_demo_heat_map_has_domain_rows_that_add_up(app, db_session, make_org, tenant_ctx):
    from app.models.business_capabilities import BusinessCapability

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
        org_id, heat = _heatmap(tenant_ctx)
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    rows = heat["domains"]
    named = [r for r in rows if r["has_domain"]]
    assert len(named) >= 4, [r["name"] for r in rows]
    assert {r["name"] for r in named} == {"Field Operations", "Supply and Fulfilment", "Governance and People", "Data and Integration", "Technology Operations"}
    assert not [r for r in rows if r["name"] == "No domain"]
    total = BusinessCapability.query.filter_by(organization_id=org_id).count()
    assert total == 24
    assert sum(r["total_capabilities"] for r in rows) == total
    assert heat["capabilities_without_domain"] == 0
    assert all(r["total_capabilities"] > 0 for r in named)


def test_a_second_seed_keeps_the_domains(app, db_session, make_org, tenant_ctx):
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
        seed_demo_company()
        _org, heat = _heatmap(tenant_ctx)
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    assert len([r for r in heat["domains"] if r["has_domain"]]) == 5
