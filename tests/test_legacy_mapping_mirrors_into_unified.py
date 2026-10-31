"""Every insert into the older application_capability_mapping table is mirrored into
unified_application_capability_mapping (the one home the coverage page, genome slice and chat read)."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org_with_projected_capability(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    org = make_org()
    code = "MIR-%s" % uuid.uuid4().hex[:6]
    bc = BusinessCapability(name="Mirror cap " + code, code=code, organization_id=org.id, level=1)
    app_row = ApplicationComponent(name="Mirror app " + code, organization_id=org.id)
    db_session.add_all([bc, app_row])
    db_session.flush()
    # Stands in for the write-time projection of the capability.
    unified = UnifiedCapability.query.filter_by(organization_id=org.id, source_table="business_capability",
                                                source_id=str(bc.id)).first()
    if unified is None:
        unified = UnifiedCapability(name=bc.name, code=code, level=1, organization_id=org.id,
                                    source_table="business_capability", source_id=str(bc.id))
        db_session.add(unified)
        db_session.flush()
    return org, bc, app_row, unified


def test_a_legacy_mapping_insert_appears_in_the_unified_table(db_session, make_org):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    org, bc, app_row, unified = _org_with_projected_capability(db_session, make_org)
    db_session.add(ApplicationCapabilityMapping(
        organization_id=org.id, application_component_id=app_row.id, business_capability_id=bc.id,
        support_level="full", coverage_percentage=70, is_active=True))
    db_session.flush()
    rows = UnifiedApplicationCapabilityMapping.query.filter_by(
        application_component_id=app_row.id, unified_capability_id=unified.id).all()
    assert len(rows) == 1
    assert rows[0].support_level == "full" and rows[0].coverage_percentage == 70


def test_a_pair_already_in_the_unified_table_is_not_doubled(db_session, make_org):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    org, bc, app_row, unified = _org_with_projected_capability(db_session, make_org)
    db_session.add(UnifiedApplicationCapabilityMapping(
        application_component_id=app_row.id, unified_capability_id=unified.id, support_level="partial"))
    db_session.flush()
    db_session.add(ApplicationCapabilityMapping(
        organization_id=org.id, application_component_id=app_row.id, business_capability_id=bc.id,
        support_level="full"))
    db_session.flush()
    assert UnifiedApplicationCapabilityMapping.query.filter_by(
        application_component_id=app_row.id, unified_capability_id=unified.id).count() == 1
