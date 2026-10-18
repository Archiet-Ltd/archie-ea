"""The shared framework catalogue has one source and one seeding command.

Main already seeded ISO 27001, SOC 2, GDPR and DORA from a hardcoded list; the
rework replaces those four entries with the default catalogue file (which also
adds NIS2 and the EU AI Act) and seeds both through the one existing command.
Everything here runs against the real seeder and real rows; the adoption cases
use two organisations.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from app.services.compliance.regulatory_framework_service import (
    RegulatoryFrameworkService as Service,
)

pytestmark = pytest.mark.usefixtures("db_session")

CATALOGUE_CODES = {"ISO - 27001", "SOC-2", "GDPR", "NIS2", "DORA", "EU-AI-ACT"}


@contextlib.contextmanager
def _no_tenant():
    """Run as the CLI does: no organisation context at all."""
    from flask import g

    had = hasattr(g, "current_org_id")
    saved = getattr(g, "current_org_id", None)
    if had:
        del g.current_org_id
    try:
        yield
    finally:
        if had:
            g.current_org_id = saved


def _seed():
    with _no_tenant():
        return Service.seed_manufacturing_frameworks()


def _catalogue():
    return Service.load_default_catalogue()


def test_default_catalogue_codes_have_one_source():
    hardcoded = {fw["code"] for fw in Service.MANUFACTURING_FRAMEWORKS}
    assert not (hardcoded & CATALOGUE_CODES), (
        "a code is defined both in the manufacturing list and in the catalogue file"
    )
    assert {fw["code"] for fw in _catalogue()["frameworks"]} == CATALOGUE_CODES


def test_there_is_one_seeding_command():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manage = open(os.path.join(root, "manage.py"), encoding="utf-8").read()
    deploy = open(os.path.join(root, "scripts", "database", "deploy-schema.sh"), encoding="utf-8").read()
    assert "def seed_framework_catalogue" in manage
    assert "seed-framework-catalogue" in deploy
    assert "seed-compliance-catalogue" not in manage + deploy
    assert not os.path.exists(os.path.join(root, "app", "commands", "seed_compliance_catalogue.py"))


def test_seeding_loads_the_catalogue_and_a_second_run_adds_nothing(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    _seed()
    for framework in _catalogue()["frameworks"]:
        row = RegulatoryFramework.query.filter_by(code=framework["code"]).one()
        assert (
            ComplianceControl.query.filter_by(framework_id=row.id).count()
            == len(framework["controls"])
        ), framework["code"]

    before = ComplianceControl.query.count()
    assert _seed() == 0
    assert ComplianceControl.query.count() == before


def test_known_wrong_titles_are_corrected_and_edited_titles_are_not(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    _seed()

    def control(fw_code, code):
        fw = RegulatoryFramework.query.filter_by(code=fw_code).one()
        return ComplianceControl.query.filter_by(framework_id=fw.id, control_code=code).first()

    # A database that loaded the rows main used to ship.
    for (fw_code, code), (wrong, _right) in Service.KNOWN_WRONG_CONTROL_TITLES.items():
        row = control(fw_code, code)
        if row is None:  # DORA Art.15 is not in the catalogue; the old release shipped it
            fw = RegulatoryFramework.query.filter_by(code=fw_code).one()
            row = ComplianceControl(
                framework_id=fw.id, control_code=code, title=wrong, category="x", priority="medium"
            )
            db_session.add(row)
        row.title = wrong
    edited = control("SOC-2", "CC1.1")
    edited.title = "Our own wording for CC1.1"
    db_session.flush()

    _seed()

    for (fw_code, code), (_wrong, right) in Service.KNOWN_WRONG_CONTROL_TITLES.items():
        assert control(fw_code, code).title == right, (fw_code, code)
    assert control("SOC-2", "CC1.1").title == "Our own wording for CC1.1"


def test_corrections_that_the_catalogue_lists_use_its_titles():
    titles = {
        (fw["code"], c["control_code"]): c["title"]
        for fw in _catalogue()["frameworks"]
        for c in fw["controls"]
    }
    for key, (_wrong, right) in Service.KNOWN_WRONG_CONTROL_TITLES.items():
        if key in titles:
            assert titles[key] == right, key


def test_scope_rules_apply_to_the_catalogue_spelling_of_a_code():
    from app.models.technology_layer import Node
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    for code in ApplicabilityService.FRAMEWORK_RULES:
        spelled_like_the_catalogue = {c for c in CATALOGUE_CODES if ApplicabilityService._rule_key(c) == code}
        if code == "ISO-27001":
            assert spelled_like_the_catalogue == {"ISO - 27001"}
    outside = Node(name="Mainframe", node_type="Mainframe", deployment_model="On-premises")
    inside = Node(name="VM", node_type="Virtual Machine", deployment_model="Cloud")
    assert ApplicabilityService.evaluate_node(outside, "ISO - 27001") is False
    assert ApplicabilityService.evaluate_node(inside, "ISO - 27001") is True
    assert ApplicabilityService.evaluate_node(outside, "ISO-27001") is False
    assert ApplicabilityService.rules_for("ISO - 27001") == ApplicabilityService.rules_for("ISO-27001") != []


def _user(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"catalogue-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Cat",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="security_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_organisations_that_already_adopted_get_the_new_controls(db_session, make_org, tenant_ctx):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    _seed()
    framework = RegulatoryFramework.query.filter_by(code="NIS2").one()
    total = ComplianceControl.query.filter_by(framework_id=framework.id).count()
    # The state before this catalogue version added one control.
    newest = (
        ComplianceControl.query.filter_by(framework_id=framework.id)
        .order_by(ComplianceControl.id.desc())
        .first()
    )
    newest_code = newest.control_code
    ComplianceControl.query.filter_by(id=newest.id).delete()
    db_session.flush()

    org_a, org_b = make_org("cat-a"), make_org("cat-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    with tenant_ctx(org_a.id):
        adoption_a = ApplicabilityService.adopt_framework(org_a.id, framework.id, user_a.id)
        a_id = adoption_a.id
        done = ApplicationComplianceControl.query.filter_by(adoption_id=a_id).first()
        done.implementation_status = "implemented"
        db_session.commit()
        done_id = done.id
    with tenant_ctx(org_b.id):
        adoption_b = ApplicabilityService.adopt_framework(org_b.id, framework.id, user_b.id)
        b_id = adoption_b.id
    with tenant_ctx(org_a.id):
        assert total - 1 == db_session.query(ApplicationComplianceControl).filter_by(adoption_id=a_id).count()

    _seed()  # the new catalogue version brings the control back

    restored = ComplianceControl.query.filter_by(
        framework_id=framework.id, control_code=newest_code
    ).one()
    for adoption_id, org in ((a_id, org_a), (b_id, org_b)):
        with tenant_ctx(org.id):
            rows = (
                db_session.query(ApplicationComplianceControl)
                .filter_by(adoption_id=adoption_id)
                .all()
            )
            assert len(rows) == total, "the adoption did not receive the new control"
            assert {r.organization_id for r in rows} == {org.id}, "a row landed in the other organisation"
            new_row = [r for r in rows if r.control_id == restored.id]
            assert len(new_row) == 1 and new_row[0].implementation_status == "planned"
    # Work already recorded in A is untouched.
    with tenant_ctx(org_a.id):
        assert (
            db_session.query(ApplicationComplianceControl).filter_by(id=done_id).one().implementation_status
            == "implemented"
        )


def test_a_control_without_a_code_is_named_not_a_key_error():
    broken = [
        {"code": "X-1", "name": "X", "controls": [{"title": "No code here", "criticality": "high"}]}
    ]
    with pytest.raises(ValueError, match="X-1.*no control_code"):
        Service.validate_framework_list(broken, "manufacturing list")


def test_the_shipped_lists_validate():
    Service.validate_framework_list(
        [dict(fw) for fw in Service.MANUFACTURING_FRAMEWORKS], "manufacturing list"
    )
    _catalogue()  # loads and validates the catalogue file itself
