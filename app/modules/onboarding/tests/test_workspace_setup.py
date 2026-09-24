"""Saving a Tell-us-more section sets up the organisation's workspace: real,
tenant-scoped rows in the model, not only a preference in
Organization.settings. See workspace_setup.py's own docstring for which
model each answer becomes and why."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_compliance_standard_creates_a_tenant_scoped_risk(app, db_session, make_org):
    from app.models.risk import Risk
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-compliance")
    tell_us_more.save_section(org, "compliance", {"standards": {"gdpr": "partial"}})

    risks = Risk.query.filter_by(organization_id=org.id).all()
    assert len(risks) == 1
    risk = risks[0]
    assert risk.title == "GDPR compliance"
    assert risk.likelihood == 3 and risk.impact == 3
    assert risk.archimate_element_id is not None

    from app.models.archimate_core import ArchiMateElement

    element = db_session.get(ArchiMateElement, risk.archimate_element_id)
    assert element.organization_id == org.id
    assert element.custom_properties.get("source") == "onboarding"
    assert element.custom_properties.get("onboarding_source") == "tell_us_more:compliance:gdpr"


def test_resaving_a_compliance_standard_updates_instead_of_duplicating(app, db_session, make_org):
    from app.models.risk import Risk
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-compliance-resave")
    tell_us_more.save_section(org, "compliance", {"standards": {"gdpr": "none"}})
    tell_us_more.save_section(org, "compliance", {"standards": {"gdpr": "full"}})

    risks = Risk.query.filter_by(organization_id=org.id).all()
    assert len(risks) == 1, "re-saving the same standard must update the existing risk, not add one"
    assert risks[0].likelihood == 1 and risks[0].impact == 2


def test_frameworks_in_use_creates_a_capability_with_no_fabricated_maturity(app, db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-frameworks")
    tell_us_more.save_section(org, "how_you_work", {"frameworks_in_use": ["itil-v4"]})

    cap = UnifiedCapability.query.filter_by(organization_id=org.id, source_table="onboarding").first()
    assert cap is not None
    assert cap.name == "ITIL v4"
    assert cap.status == "operational"
    assert cap.current_maturity_level is None, "an unassessed capability must stay unassessed, never a default level"


def test_frameworks_wanted_sets_a_target_not_a_measured_level(app, db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-frameworks-want")
    tell_us_more.save_section(org, "how_you_work", {"frameworks_want": ["togaf-enterprise-architecture"]})

    cap = UnifiedCapability.query.filter_by(organization_id=org.id, source_table="onboarding").first()
    assert cap is not None
    assert cap.current_maturity_level is None
    assert cap.target_maturity_level == 3


def test_transformation_template_creates_a_work_package_per_phase(app, db_session, make_org):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.archimate_core import ArchiMateElement
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-transformation")
    tell_us_more.save_section(org, "whats_changing", {"transformation_templates": ["crm"]})

    work_packages = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org.id)
        .all()
    )
    # CRM's own phases: Planning, Configuration, Migration, Launch.
    assert len(work_packages) == 4
    names = sorted(w.name for w in work_packages)
    assert any("Planning" in n for n in names)
    assert any("Launch" in n for n in names)
    for wp in work_packages:
        assert wp.status == "planned"
        assert wp.capability_names == ["Customer Experience"]


def test_resaving_a_transformation_template_does_not_duplicate_work_packages(app, db_session, make_org):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.archimate_core import ArchiMateElement
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-transformation-resave")
    tell_us_more.save_section(org, "whats_changing", {"transformation_templates": ["crm"]})
    tell_us_more.save_section(org, "whats_changing", {"transformation_templates": ["crm"]})

    work_packages = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org.id)
        .all()
    )
    assert len(work_packages) == 4


def test_implementation_answers_create_capabilities_and_technology_elements(app, db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.models.archimate_core import ArchiMateElement
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-implementation")
    tell_us_more.save_section(org, "how_you_build", {
        "implementation_type": "custom-development",
        "has_dev_team": True,
        "stack": "Next.js, PostgreSQL, Docker",
        "deployment_target": "cloud",
    })

    capabilities = UnifiedCapability.query.filter_by(organization_id=org.id, source_table="onboarding").all()
    cap_names = {c.name for c in capabilities}
    assert "Custom Application Development" in cap_names
    assert "Software engineering" in cap_names
    assert "Cloud operations" in cap_names

    tech_elements = ArchiMateElement.query.filter_by(organization_id=org.id, layer="technology").all()
    tech_names = {e.name for e in tech_elements}
    assert "Next.js" in tech_names
    assert "PostgreSQL" in tech_names


def test_workspace_records_are_isolated_between_organisations(app, db_session, make_org):
    from app.models.risk import Risk
    from app.modules.onboarding.services import tell_us_more

    org_a = make_org("ws-tenant-a")
    org_b = make_org("ws-tenant-b")

    tell_us_more.save_section(org_a, "compliance", {"standards": {"gdpr": "none"}})
    tell_us_more.save_section(org_b, "compliance", {"standards": {"gdpr": "full"}})

    risk_a = Risk.query.filter_by(organization_id=org_a.id).one()
    risk_b = Risk.query.filter_by(organization_id=org_b.id).one()
    assert risk_a.id != risk_b.id
    assert risk_a.likelihood == 5 and risk_b.likelihood == 1


def test_apply_section_is_a_no_op_for_an_empty_answers_dict(app, db_session, make_org):
    from app.modules.onboarding.services import workspace_setup

    org = make_org("ws-empty")
    assert workspace_setup.apply_section(org, "compliance", {}) == {}
    assert workspace_setup.apply_section(org, "compliance", None) == {}


# ---------------------------------------------------------------------------
# The Ask lenses read what onboarding just created (acceptance criterion:
# saving each section must make the record appear in the Risk and Programme
# answers, not only in the model).
# ---------------------------------------------------------------------------


def _make_user(db_session, org):
    import uuid

    from app.models.user import Role, User

    if Role.query.filter_by(default=True).first() is None:
        Role.insert_roles()

    user = User(
        email=f"ws-ask-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Ask",
        last_name="Reader",
        confirmed=True,
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_risk_ask_lens_reads_the_compliance_risk_it_created(app, db_session, make_org, client, login_as):
    from app.models.risk import Risk
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-ask-risk")
    user = _make_user(db_session, org)
    login_as(client, user)

    tell_us_more.save_section(org, "compliance", {"standards": {"iso27001": "substantial"}})
    risk = Risk.query.filter_by(organization_id=org.id).one()

    resp = client.get(f"/api/v1/intelligence/risk/{risk.archimate_element_id}")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()["data"]
    assert len(body["risks"]) == 1
    assert body["risks"][0]["title"] == "ISO 27001 compliance"
    assert body["risks"][0]["likelihood"] == 2 and body["risks"][0]["impact"] == 3


def test_programme_ask_lens_reads_the_work_packages_it_created(app, db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-ask-programme")
    user = _make_user(db_session, org)
    login_as(client, user)

    tell_us_more.save_section(org, "whats_changing", {"transformation_templates": ["cloud-migration"]})
    work_package = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org.id)
        .first()
    )

    resp = client.get(f"/api/v1/intelligence/programme/{work_package.archimate_element_id}")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()["data"]
    assert len(body["work_packages"]) == 1
    assert "Cloud Migration" in body["work_packages"][0]["name"]


# ---------------------------------------------------------------------------
# Orphan cleanup: re-saving with fewer answers removes the old records
# ---------------------------------------------------------------------------


def test_deselecting_a_compliance_standard_removes_its_risk(app, db_session, make_org):
    from app.models.risk import Risk
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-orphan-compliance")
    tell_us_more.save_section(org, "compliance", {
        "standards": {"gdpr": "partial", "iso27001": "full"},
    })
    assert Risk.query.filter_by(organization_id=org.id).count() == 2

    tell_us_more.save_section(org, "compliance", {
        "standards": {"gdpr": "partial"},
    })
    risks = Risk.query.filter_by(organization_id=org.id).all()
    assert len(risks) == 1
    assert risks[0].title == "GDPR compliance"


def test_deselecting_a_framework_removes_its_capability(app, db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-orphan-frameworks")
    tell_us_more.save_section(org, "how_you_work", {
        "frameworks_in_use": ["agile-methodology", "devops-practices"],
    })
    assert UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="onboarding"
    ).count() == 2

    tell_us_more.save_section(org, "how_you_work", {
        "frameworks_in_use": ["agile-methodology"],
    })
    caps = UnifiedCapability.query.filter_by(
        organization_id=org.id, source_table="onboarding"
    ).all()
    assert len(caps) == 1
    assert caps[0].name == "Agile Methodology"


def test_deselecting_a_transformation_template_removes_its_work_packages(app, db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-orphan-transformation")
    tell_us_more.save_section(org, "whats_changing", {
        "transformation_templates": ["crm", "cloud-migration"],
    })

    wps_before = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org.id)
        .count()
    )
    assert wps_before > 4  # CRM has 4 phases, cloud-migration has its own

    tell_us_more.save_section(org, "whats_changing", {
        "transformation_templates": ["crm"],
    })
    wps_after = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org.id)
        .count()
    )
    assert wps_after == 4, "only CRM's 4 phases must remain"


def test_clearing_the_stack_field_removes_technology_elements(app, db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.onboarding.services import tell_us_more

    org = make_org("ws-orphan-stack")
    tell_us_more.save_section(org, "how_you_build", {
        "stack": "Next.js, PostgreSQL, Docker",
    })
    tech_before = ArchiMateElement.query.filter_by(
        organization_id=org.id, layer="technology"
    ).count()
    assert tech_before == 3

    tell_us_more.save_section(org, "how_you_build", {
        "stack": "Next.js",
    })
    tech_after = ArchiMateElement.query.filter_by(
        organization_id=org.id, layer="technology"
    ).all()
    assert len(tech_after) == 1
    assert tech_after[0].name == "Next.js"


def test_deselected_answer_cleanup_is_isolated_between_organisations(app, db_session, make_org):
    from app.models.risk import Risk
    from app.modules.onboarding.services import tell_us_more

    org_a = make_org("ws-orphan-iso-a")
    org_b = make_org("ws-orphan-iso-b")

    tell_us_more.save_section(org_a, "compliance", {
        "standards": {"gdpr": "partial", "iso27001": "full"},
    })
    tell_us_more.save_section(org_b, "compliance", {
        "standards": {"gdpr": "partial", "iso27001": "full"},
    })

    # Deselect iso27001 only in org A.
    tell_us_more.save_section(org_a, "compliance", {
        "standards": {"gdpr": "partial"},
    })

    risks_a = Risk.query.filter_by(organization_id=org_a.id).all()
    risks_b = Risk.query.filter_by(organization_id=org_b.id).all()
    assert len(risks_a) == 1
    assert risks_a[0].title == "GDPR compliance"
    assert len(risks_b) == 2, "org B's iso27001 risk must not be touched by org A's cleanup"
