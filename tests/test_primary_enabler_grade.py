"""A generated work package is named for the capability the application is the primary
enabler of: in the seeded demonstration organisation, for every application linked to
several capabilities, in all four generators; and a support row graded the old way is
graded from the mapping's primary-enabler flag after the data migration."""
import importlib
import os
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

GENERATORS = [
    "app.services.roadmap_automation",
    "app.modules.solutions_strategic.v2.services.roadmap_automation",
    "app.services.roadmap_sync",
    "app.modules.solutions_strategic.v2.services.roadmap_sync",
]


def test_every_multi_capability_demo_application_is_named_for_its_primary_capability(
    app, db_session, tenant_ctx
):
    from app.commands import seed_demo_company as seed
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed.seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    org = Organization.query.filter_by(name=seed._ORG_NAME).one()
    multi = {name for name, _ in seed._ALSO_SUPPORTS}
    assert len(multi) >= 6
    with tenant_ctx(org.id):
        for name in sorted(multi):
            application = ApplicationComponent.query.filter_by(organization_id=org.id, name=name).one()
            primary = UnifiedCapability.query.filter_by(
                organization_id=org.id, code=seed._APP_PROFILE[name][3]
            ).one()
            for path in GENERATORS:
                named = importlib.import_module(path).primary_capability_name(application.id)
                assert named == primary.name, f"{path}: {name} named {named!r}, wanted {primary.name!r}"


def test_a_row_projected_the_old_way_is_regraded_from_the_mapping_flag(db_session, make_org):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Row
    from app.models.archimate_core import ArchiMateRelationship

    migration = importlib.import_module("migrations.versions.20261116_regrade_primary_enabler")
    from app.services.capability_support import record_capability_support
    from app.services.connector_framework import ensure_application_row  # noqa: F401
    from app.services.archimate_backbone import create_backbone_element

    org = make_org("regrade-a")
    sides = {}
    for who, flagged in (("primary", True), ("secondary", False)):
        element = create_backbone_element(
            element_type="Capability", layer="Strategy", name=f"{who} cap {uuid.uuid4().hex[:4]}",
            organization_id=org.id)
        bc = BusinessCapability(
            name=element.name, code=f"B{uuid.uuid4().hex[:7]}", level=1, category="core",
            business_domain="test", organization_id=org.id, archimate_element_id=element.id)
        db_session.add(bc)
        sides[who] = (element, bc, flagged)
    app_el = create_backbone_element(
        element_type="ApplicationComponent", layer="Application", name=f"App {uuid.uuid4().hex[:4]}",
        organization_id=org.id)
    db_session.flush()
    for who, (element, bc, flagged) in sides.items():
        record_capability_support(org.id, app_el.id, element.id, kind="realization")
    application = ApplicationComponent.query.filter_by(organization_id=org.id, archimate_element_id=app_el.id).one()
    for element, bc, flagged in sides.values():
        db_session.add(ApplicationCapabilityMapping(
            organization_id=org.id, application_component_id=application.id,
            business_capability_id=bc.id, is_primary_enabler=flagged, support_level="full",
            coverage_percentage=100))
    db_session.flush()
    rows = {
        who: Row.query.filter_by(
            application_component_id=application.id,
            archimate_relationship_id=ArchiMateRelationship.query.filter_by(
                organization_id=org.id, source_id=app_el.id, target_id=element.id, type="realization").one().id,
        ).one()
        for who, (element, _, _) in sides.items()
    }
    for row in rows.values():  # the old projection graded every realization link primary
        row.support_level = "primary"
    db_session.flush()

    changed = migration.regrade(db_session.connection())
    db_session.expire_all()

    assert changed >= 1
    assert rows["primary"].support_level == "primary"
    assert rows["secondary"].support_level == "linked"
    assert migration.regrade(db_session.connection()) == 0
