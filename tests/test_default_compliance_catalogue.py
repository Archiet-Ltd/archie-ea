"""The default global compliance catalogue and the seeder that loads it.

The catalogue is shared master data (no organisation column). The seeder is
idempotent: a second run changes nothing, a deleted control comes back, and an
edited title is left alone.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

DEFAULT_CODES = ["ISO - 27001", "SOC-2", "GDPR", "NIS2", "DORA", "EU-AI-ACT"]
REPO_ROOT = Path(__file__).resolve().parents[1]


def _service():
    from app.services.compliance.regulatory_framework_service import (
        RegulatoryFrameworkService,
    )

    return RegulatoryFrameworkService


def _clear(db_session, codes):
    """Remove the named frameworks and their controls inside the test transaction."""
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    for fw in RegulatoryFramework.query.filter(RegulatoryFramework.code.in_(codes)).all():
        ComplianceControl.query.filter_by(framework_id=fw.id).delete()
        db_session.delete(fw)
    db_session.flush()


def _framework(code):
    from app.models.compliance_models import RegulatoryFramework

    return RegulatoryFramework.query.filter_by(code=code).all()


def _controls(code):
    from app.models.compliance_models import ComplianceControl

    fw = _framework(code)[0]
    return ComplianceControl.query.filter_by(framework_id=fw.id).all()


@pytest.fixture
def empty_catalogue(db_session):
    _clear(db_session, DEFAULT_CODES)
    return db_session


# ── acceptance 1: six frameworks, each with controls, ISO 27001 has 93 ──


def test_empty_catalogue_gets_six_frameworks_each_with_controls(empty_catalogue):
    result = _service().seed_default_catalogue()
    assert result["frameworks_added"] == 6
    for code in DEFAULT_CODES:
        assert len(_framework(code)) == 1, code
        assert len(_controls(code)) >= 10, code


def test_iso_27001_has_all_93_annex_a_controls(empty_catalogue):
    _service().seed_default_catalogue()
    controls = _controls("ISO - 27001")
    codes = {c.control_code for c in controls}
    assert len(controls) == 93
    assert all(re.fullmatch(r"A\.[5-8]\.\d{1,2}", c) for c in codes)
    per_theme = {t: sum(1 for c in codes if c.startswith(f"A.{t}.")) for t in "5678"}
    assert per_theme == {"5": 37, "6": 8, "7": 14, "8": 34}


def test_framework_headers_are_official(empty_catalogue):
    _service().seed_default_catalogue()
    for code in DEFAULT_CODES:
        fw = _framework(code)[0]
        assert fw.official_url and fw.official_url.startswith("https://"), code
        assert fw.standard_version, code
        assert fw.jurisdiction, code
        assert fw.status == "active", code


# ── acceptance 2: idempotent in both directions ──


def test_second_run_adds_nothing(empty_catalogue):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    _service().seed_default_catalogue()
    frameworks = RegulatoryFramework.query.count()
    controls = ComplianceControl.query.count()
    again = _service().seed_default_catalogue()
    assert again["frameworks_added"] == 0
    assert again["controls_added"] == 0
    assert RegulatoryFramework.query.count() == frameworks
    assert ComplianceControl.query.count() == controls


def test_deleted_control_is_restored_and_edited_title_is_kept(empty_catalogue, db_session):
    _service().seed_default_catalogue()
    controls = {c.control_code: c for c in _controls("GDPR")}
    victim = controls["Article - 17"]
    edited = controls["Article - 15"]
    edited.title = "Locally reworded title"
    db_session.delete(victim)
    db_session.flush()

    result = _service().seed_default_catalogue()

    assert result["frameworks_added"] == 0
    assert result["controls_added"] == 1
    after = {c.control_code: c for c in _controls("GDPR")}
    assert "Article - 17" in after
    assert after["Article - 15"].title == "Locally reworded title"
    assert len(after) == len(controls)


# ── acceptance 3: existing codes are kept; manufacturing seeding is unchanged ──


def test_existing_codes_are_topped_up_not_renamed_or_duplicated(empty_catalogue, db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    fw = RegulatoryFramework(
        code="ISO - 27001", name="Existing header name", category="security", status="active"
    )
    db_session.add(fw)
    db_session.flush()
    db_session.add(
        ComplianceControl(framework_id=fw.id, control_code="A.8.10", title="Existing title")
    )
    db_session.flush()

    _service().seed_default_catalogue()

    rows = _framework("ISO - 27001")
    assert len(rows) == 1
    assert rows[0].name == "Existing header name"
    controls = _controls("ISO - 27001")
    assert len(controls) == 93
    assert {c.control_code: c.title for c in controls}["A.8.10"] == "Existing title"


def test_manufacturing_seeding_gives_the_same_frameworks_and_controls(db_session):
    svc = _service()
    expected = {}
    for fw in copy.deepcopy(svc.MANUFACTURING_FRAMEWORKS):
        expected[fw["code"]] = len(fw.get("controls", []))
    _clear(db_session, list(expected))

    added = svc.seed_manufacturing_frameworks()

    assert added == len(expected)
    for code, count in expected.items():
        assert len(_framework(code)) == 1, code
        assert len(_controls(code)) == count, code
    assert svc.seed_manufacturing_frameworks() == 0


# ── acceptance 4: data integrity ──


def test_every_control_is_complete_and_codes_are_unique_per_framework():
    catalogue = _service().load_default_catalogue()
    assert [f["code"] for f in catalogue["frameworks"]] == DEFAULT_CODES
    for fw in catalogue["frameworks"]:
        assert fw["official_url"], fw["code"]
        codes = [c["control_code"] for c in fw["controls"]]
        assert len(codes) == len(set(codes)), fw["code"]
        for c in fw["controls"]:
            assert c["control_code"] and c["title"] and c["official_reference"], (
                fw["code"],
                c,
            )
            assert len(c["control_code"]) <= 50 and len(c["title"]) <= 500


def test_a_duplicate_control_code_inside_a_framework_is_rejected():
    svc = _service()
    bad = {
        "catalogue_version": "x",
        "frameworks": [
            {
                "code": "X",
                "name": "X",
                "official_url": "https://example.org",
                "controls": [
                    {"control_code": "1", "title": "a", "official_reference": "1"},
                    {"control_code": "1", "title": "b", "official_reference": "1"},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="duplicate"):
        svc.validate_default_catalogue(bad)


def test_a_control_without_an_official_reference_is_rejected():
    bad = {
        "catalogue_version": "x",
        "frameworks": [
            {
                "code": "X",
                "name": "X",
                "official_url": "https://example.org",
                "controls": [{"control_code": "1", "title": "a"}],
            }
        ],
    }
    with pytest.raises(ValueError, match="official_reference"):
        _service().validate_default_catalogue(bad)


# ── acceptance 5: adoption copies exactly the framework's controls ──


def test_adopting_a_seeded_framework_copies_exactly_its_controls(
    empty_catalogue, db_session, make_org, tenant_ctx
):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.models.user import User

    _service().seed_default_catalogue()
    org = make_org("adopt")
    user = User(email=f"adopt-{org.id}@example.org", organization_id=org.id, password_hash="x")
    db_session.add(user)
    db_session.flush()
    fw = _framework("NIS2")[0]
    expected = {c.id for c in _controls("NIS2")}

    with tenant_ctx(org.id):
        adoption = ApplicabilityService.adopt_framework(org.id, fw.id, user.id)
        copied = {
            r.control_id
            for r in ApplicationComplianceControl.query.filter_by(adoption_id=adoption.id).all()
        }

    assert copied == expected and len(copied) >= 10


# ── acceptance 6: command registered, wired into deployment, logs the version ──


def test_command_is_registered_and_logs_the_catalogue_version(empty_catalogue, app, caplog):
    assert "seed-compliance-catalogue" in app.cli.commands
    version = _service().load_default_catalogue()["catalogue_version"]
    with caplog.at_level("INFO"):
        result = app.test_cli_runner().invoke(args=["seed-compliance-catalogue"])
    assert result.exit_code == 0, result.output
    assert version in result.output or version in caplog.text
    assert len(_framework("EU-AI-ACT")) == 1


def test_deployment_initialisation_runs_the_command():
    script = (REPO_ROOT / "scripts" / "database" / "deploy-schema.sh").read_text()
    assert "seed-compliance-catalogue" in script
