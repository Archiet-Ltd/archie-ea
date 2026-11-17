"""Every framework a person can choose at sign-up loads its whole structure, up to the published count.

Two real organisations: one adopted ISO 27001 while the catalogue was short, one chooses it now.
"""

import pytest

from app import db
from app.models.compliance_models import ComplianceControl, RegulatoryFramework
from app.modules.account.services import onboarding_service as onboarding
from app.seed_data.standard_control_totals import PUBLISHED_CONTROLS, controls_loaded_text
from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService
from tests.test_signup_canvases_and_frameworks import _choose, two  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")


def _held(org, code="ISO - 27001"):
    """How many controls of the framework this organisation holds through its adoption."""
    from app.models.application_compliance import ApplicationComplianceControl

    return db.session.execute(db.text(
        "SELECT count(*) FROM application_compliance_controls x JOIN framework_adoptions a ON a.id = x.adoption_id "
        "JOIN regulatory_frameworks f ON f.id = a.framework_id "
        "WHERE x.organization_id = :org AND f.code = :code"), {"org": org.id, "code": code}).scalar()


def test_every_signup_framework_has_a_published_count_and_its_catalogue_reaches_it():
    by_code = {f["code"]: f for f in RegulatoryFrameworkService.load_frameworks_from_json()}
    for key, code in onboarding.FRAMEWORK_CATALOGUES.items():
        if code in ("DORA", "NIS2"):
            continue
        assert code in PUBLISHED_CONTROLS, "%s has no published count" % code
        codes = [c.get("control_code") or c.get("control_id") for c in by_code[code]["controls"]]
        assert len(codes) == len(set(codes)) == PUBLISHED_CONTROLS[code], code


def test_iso_27001_runs_from_a_5_1_to_a_8_34_and_has_no_loaded_line():
    codes = [c["control_code"] for c in RegulatoryFrameworkService.get_framework_by_code("ISO - 27001")["controls"]]
    assert len(codes) == 93 and codes[0] == "A.5.1" and codes[-1] == "A.8.34"
    for code, total in PUBLISHED_CONTROLS.items():
        assert controls_loaded_text(code, total) is None


def test_an_organisation_that_adopted_the_short_iso_catalogue_gets_the_rest_and_the_other_is_unaffected(
    client, login_as, two
):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    from app.models.regulatory_framework import FrameworkAdoption
    from app.models.application_compliance import ApplicationComplianceControl

    RegulatoryFrameworkService.seed_manufacturing_frameworks(codes={"ISO - 27001"})
    framework = RegulatoryFramework.query.filter_by(code="ISO - 27001").one()
    ApplicabilityService.adopt_framework(two["one"].id, framework.id, adopted_by_id=None, source="test")
    adoption = FrameworkAdoption.query.filter_by(organization_id=two["one"].id, framework_id=framework.id).one()
    # the state of an organisation that adopted while only three controls were loaded
    kept = [r.id for r in ApplicationComplianceControl.query.filter_by(
        organization_id=two["one"].id, adoption_id=adoption.id).limit(3)]
    ApplicationComplianceControl.query.filter(
        ApplicationComplianceControl.organization_id == two["one"].id,
        ApplicationComplianceControl.adoption_id == adoption.id,
        ~ApplicationComplianceControl.id.in_(kept),
    ).delete(synchronize_session=False)
    db.session.flush()
    assert _held(two["one"]) == 3

    _choose(client, login_as, two["one_owner"], [], ["iso27001"])
    _choose(client, login_as, two["two_owner"], [], ["iso27001"])
    assert _held(two["one"]) == 93
    assert _held(two["two"]) == 93
