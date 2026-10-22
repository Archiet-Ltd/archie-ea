"""Public-law packs in full, the sourced title corrections, and the seeder behaviours that keep
the one catalogue honest (slice 2 of the framework library).

Pins: DORA, NIS2, the EU AI Act and the UK GDPR are loaded article for article with their official
headings; the sourced GDPR and DORA corrections are applied; every control states whether it is a
Requirement or a Constraint; a control added to a framework later reaches the organisations that
already adopted it; legacy control codes fold onto the pack's codes instead of making a second
row; the applicability lookup finds a rule through any spelling of the framework code; the
counts never claim more than is loaded; and the licensed standards carry no licensed text.

Fixtures (app, db_session, make_org) come from app/modules/conftest.py.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

# code -> (articles in the source, controls loaded)
PUBLIC_LAW = {
    "DORA": (64, 64),
    "NIS2": (46, 61),
    "EU-AI-ACT": (113, 113),
    "UK-GDPR": (81, 81),
    "GDPR": (99, 99),
}
ARTICLE_CODE = re.compile(r"^(?:[A-Z0-9-]+-Art\.\d+|Art\. \d+[A-Z]*)$")


def _packs():
    return {pack["code"]: pack for pack in RegulatoryFrameworkService.load_library_packs()}


@pytest.fixture
def seeded(app, db_session):
    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    return RegulatoryFrameworkService


def _platform(app, fn):
    from app.jobs.tenant_safe_job import platform_scope

    with app.test_request_context("/"):
        with platform_scope("framework library test read"):
            return fn()


# --------------------------------------------------------------------------- #
# Packs                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", sorted(PUBLIC_LAW))
def test_each_public_law_pack_loads_and_its_count_matches_its_source(code):
    articles, loaded = PUBLIC_LAW[code]
    pack = _packs().get(code)
    assert pack is not None, f"{code} has no pack"
    controls = pack["controls"]
    assert len(controls) == loaded == pack["total_controls"]
    article_controls = [c for c in controls if ARTICLE_CODE.match(c["control_code"])]
    assert len(article_controls) == articles, f"{code}: one control per article of the source"
    assert len({c["control_code"] for c in controls}) == len(controls), "no repeated control code"
    assert all(c["title"].strip() for c in controls)


@pytest.mark.parametrize("code", ["DORA", "NIS2", "EU-AI-ACT", "UK-GDPR"])
def test_a_new_public_law_pack_names_its_source_and_gives_every_article_a_summary(code):
    pack = _packs()[code]
    assert pack["source_url"].startswith("https://")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", pack["source_text_date"])
    assert pack["content_level"] == "full"
    assert all((c["description"] or "").strip() for c in pack["controls"]), "a summary on every article"


@pytest.mark.parametrize("code", ["DORA", "NIS2", "EU-AI-ACT", "UK-GDPR"])
def test_every_control_says_whether_it_is_a_requirement_or_a_constraint(code):
    for control in _packs()[code]["controls"]:
        assert control["kind"] in ("Requirement", "Constraint"), (code, control["control_code"])


def test_the_prohibitions_are_constraints_and_the_duties_are_requirements():
    ai = {c["control_code"]: c for c in _packs()["EU-AI-ACT"]["controls"]}
    assert ai["EU-AI-ACT-Art.5"]["kind"] == "Constraint"
    assert ai["EU-AI-ACT-Art.9"]["kind"] == "Requirement"
    uk = {c["control_code"]: c for c in _packs()["UK-GDPR"]["controls"]}
    assert uk["Art. 9"]["kind"] == "Constraint" and uk["Art. 32"]["kind"] == "Requirement"


def test_dora_article_15_and_17_carry_their_own_titles():
    dora = {c["control_code"]: c for c in _packs()["DORA"]["controls"]}
    assert dora["DORA-Art.15"]["title"] == (
        "Further harmonisation of ICT risk management tools, methods, processes and policies")
    assert dora["DORA-Art.17"]["title"] == "ICT-related incident management process"
    assert dora["DORA-Art.15"]["category"] == "Chapter II: ICT risk management"
    assert _packs()["DORA"]["standard_version"] == "2022/2554"


DORA_TITLES = {
    "DORA-Art.5": "Governance and organisation",
    "DORA-Art.8": "Identification",
    "DORA-Art.12": "Backup policies and procedures, restoration and recovery procedures and methods",
    "DORA-Art.19": "Reporting of major ICT-related incidents and voluntary notification of significant cyber threats",
    "DORA-Art.24": "General requirements for the performance of digital operational resilience testing",
    "DORA-Art.26": "Advanced testing of ICT tools, systems and processes based on TLPT",
    "DORA-Art.28": "General principles",
    "DORA-Art.14": "Communication",
    "DORA-Art.18": "Classification of ICT-related incidents and cyber threats",
    "DORA-Art.25": "Testing of ICT tools and systems",
    "DORA-Art.27": "Requirements for testers for the carrying out of TLPT",
    "DORA-Art.29": "Preliminary assessment of ICT concentration risk at entity level",
    "DORA-Art.30": "Key contractual provisions",
    "DORA-Art.45": "Information-sharing arrangements on cyber threat information and intelligence",
}


def test_the_sourced_dora_corrections_and_additions_are_applied():
    dora = {c["control_code"]: c["title"] for c in _packs()["DORA"]["controls"]}
    for code, title in DORA_TITLES.items():
        assert dora[code] == title, code


GDPR_TITLES = {
    "Art. 8": "Conditions applicable to child's consent in relation to information society services",
    "Art. 10": "Processing of personal data relating to criminal convictions and offences",
    "Art. 12": "Transparent information, communication and modalities for the exercise of the rights of the data subject",
    "Art. 13": "Information to be provided where personal data are collected from the data subject",
    "Art. 14": "Information to be provided where personal data have not been obtained from the data subject",
    "Art. 17": "Right to erasure (‘right to be forgotten’)",
    "Art. 19": "Notification obligation regarding rectification or erasure of personal data or restriction of processing",
    "Art. 69": "Independence",
    "Art. 71": "Reports",
    "Art. 72": "Procedure",
    "Art. 73": "Chair",
    "Art. 75": "Secretariat",
    "Art. 76": "Confidentiality",
    "Art. 89": "Safeguards and derogations relating to processing for archiving purposes in the public interest, "
               "scientific or historical research purposes or statistical purposes",
    "Art. 96": "Relationship with previously concluded Agreements",
}


def test_the_fifteen_sourced_gdpr_title_corrections_are_applied():
    gdpr = {c["control_code"]: c["title"] for c in _packs()["GDPR"]["controls"]}
    for code, title in GDPR_TITLES.items():
        assert gdpr[code] == title, code


def test_the_nis2_points_without_a_heading_say_their_title_is_a_short_form():
    nis2 = {c["control_code"]: c for c in _packs()["NIS2"]["controls"]}
    points = [c for code, c in nis2.items() if "(" in code]
    assert len(points) == 15
    assert all(c.get("title_note") and c["parent_control_code"] in nis2 for c in points)
    assert nis2["NIS2-Art.23(4)(a)"]["title"] == "Early warning within 24 hours of awareness"
    assert not [c for code, c in nis2.items() if "(" not in code and c.get("title_note")]


def test_the_licensed_standards_carry_no_licensed_text():
    packs = _packs()
    for code in ("ISO-27001", "SOC-2", "PCI-DSS"):
        pack = packs[code]
        assert pack["content_level"] == "identifiers"
        assert pack["official_url"].startswith("https://")
        for control in pack["controls"]:
            assert control.get("description") is None, (code, control["control_code"])
            assert len(control["title"]) <= 120, (code, control["control_code"])


# --------------------------------------------------------------------------- #
# Counts                                                                       #
# --------------------------------------------------------------------------- #


def test_the_counts_for_the_public_law_packs_are_honest(db_session, seeded):
    counts = {row["code"]: row for row in seeded.library_counts()}
    for code, (_articles, loaded) in PUBLIC_LAW.items():
        row = counts[code]
        assert (row["loaded"], row["total"]) == (loaded, loaded), code
    assert counts["DORA"]["content_level"] == "full"
    assert "64 of 64" in seeded.describe_count(counts["DORA"])


def test_no_framework_reports_more_controls_than_it_says_it_holds(db_session, seeded):
    for row in seeded.library_counts():
        if row["total"] is not None:
            assert row["loaded"] <= row["total"], row["code"]


def test_dora_is_no_longer_a_sample_list(db_session, seeded):
    from app.models.compliance_models import RegulatoryFramework

    rows = db_session.query(RegulatoryFramework).filter(RegulatoryFramework.code == "DORA").all()
    assert len(rows) == 1 and rows[0].content_level == "full" and rows[0].total_controls == 64


def test_an_old_dora_row_with_the_wrong_titles_is_corrected_in_place(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    framework = RegulatoryFramework(code="DORA", name="DORA", category="security", content_level="sample")
    db_session.add(framework)
    db_session.flush()
    wrong = ComplianceControl(framework_id=framework.id, control_code="DORA-Art.15",
                              title="ICT incident management process", category="Incident Reporting")
    db_session.add(wrong)
    db_session.flush()
    wrong_id = wrong.id
    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    fixed = db_session.get(ComplianceControl, wrong_id)
    assert fixed.title.startswith("Further harmonisation of ICT risk management")
    assert fixed.category == "Chapter II: ICT risk management"
    count = db_session.query(ComplianceControl).filter_by(framework_id=framework.id).count()
    assert count == 64


# --------------------------------------------------------------------------- #
# Top-up for organisations that already adopted                                #
# --------------------------------------------------------------------------- #


def test_a_control_added_later_reaches_every_organisation_that_adopted_the_framework(
    app, db_session, make_org, seeded
):
    from app.models.application_compliance import ApplicationComplianceControl as Row
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from app.models.regulatory_framework import FrameworkAdoption
    from app.models.user import User
    import uuid

    org_a, org_b, org_c = make_org("topup-a"), make_org("topup-b"), make_org("topup-c")
    framework = db_session.query(RegulatoryFramework).filter_by(code="DORA").one()
    users = {}
    for org in (org_a, org_b):
        user = User(email=f"tu-{uuid.uuid4().hex[:8]}@example.com", first_name="T", last_name="U",
                    organization_id=org.id, confirmed=True)
        db_session.add(user)
        db_session.flush()
        users[org.id] = user
    for org in (org_a, org_b):
        ApplicabilityService.adopt_framework(org.id, framework.id, users[org.id].id)

    # A control the library gains later: remove it, then seed again as a later release would.
    later = db_session.query(ComplianceControl).filter_by(framework_id=framework.id, control_code="DORA-Art.14").one()
    later_id = later.id
    _platform(app, lambda: db_session.query(Row).filter_by(control_id=later_id).delete())
    db_session.delete(later)
    db_session.flush()

    # Org A has recorded work on another control; it must survive.
    def _mark():
        done = db_session.query(Row).filter_by(organization_id=org_a.id).first()
        done.implementation_status = "implemented"
        db_session.flush()
        return done.id, done.control_id

    done_id, done_control = _platform(app, _mark)

    RegulatoryFrameworkService.seed_manufacturing_frameworks()

    def _read():
        control = db_session.query(ComplianceControl).filter_by(framework_id=framework.id, control_code="DORA-Art.14").one()
        rows = db_session.query(Row).filter_by(control_id=control.id).all()
        adoptions = {a.organization_id: a.id for a in db_session.query(FrameworkAdoption).filter_by(framework_id=framework.id)}
        kept = db_session.get(Row, done_id)
        return rows, adoptions, kept.implementation_status

    rows, adoptions, kept_status = _platform(app, _read)
    assert {r.organization_id for r in rows} == {org_a.id, org_b.id}, "both adopting organisations receive it"
    assert all(r.implementation_status == "planned" for r in rows)
    assert {r.organization_id: r.adoption_id for r in rows} == {org_a.id: adoptions[org_a.id], org_b.id: adoptions[org_b.id]}
    assert org_c.id not in {r.organization_id for r in rows}, "an organisation that did not adopt gets nothing"
    assert kept_status == "implemented", "recorded work is left untouched"

    again = _platform(app, lambda: db_session.query(Row).filter_by(control_id=rows[0].control_id).count())
    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    assert _platform(app, lambda: db_session.query(Row).filter_by(control_id=rows[0].control_id).count()) == again == 2


# --------------------------------------------------------------------------- #
# Legacy codes                                                                 #
# --------------------------------------------------------------------------- #


def test_legacy_control_codes_fold_onto_the_pack_codes_and_make_no_duplicates(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    ids = {}
    for fw_code, codes in (("GDPR", ["Article - 32", "Article - 33"]), ("PCI-DSS", ["3.2.1", "8.2.1"])):
        framework = RegulatoryFramework(code=fw_code, name=fw_code, category="security")
        db_session.add(framework)
        db_session.flush()
        for code in codes:
            row = ComplianceControl(framework_id=framework.id, control_code=code, title=f"old {code}")
            db_session.add(row)
            db_session.flush()
            ids[code] = row.id
    RegulatoryFrameworkService.seed_manufacturing_frameworks()

    def codes(fw_code):
        framework = db_session.query(RegulatoryFramework).filter_by(code=fw_code).one()
        return [c.control_code for c in db_session.query(ComplianceControl)
                .filter_by(framework_id=framework.id).filter(ComplianceControl.is_active.isnot(False))]

    gdpr, pci = codes("GDPR"), codes("PCI-DSS")
    assert len(gdpr) == len(set(gdpr)) == 99 and "Article - 32" not in gdpr and "Art. 32" in gdpr
    assert len(pci) == len(set(pci)) == 12 and "3.2.1" not in pci and "8.2.1" not in pci
    assert db_session.get(ComplianceControl, ids["Article - 32"]).control_code == "Art. 32", "folded, not deleted"
    assert db_session.get(ComplianceControl, ids["3.2.1"]).control_code == "Req. 3"
    assert db_session.get(ComplianceControl, ids["8.2.1"]).control_code == "Req. 8"


def test_a_legacy_row_next_to_its_pack_row_is_flagged_not_deleted(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    framework = db_session.query(RegulatoryFramework).filter_by(code="GDPR").one()
    legacy = ComplianceControl(framework_id=framework.id, control_code="Article - 33", title="old")
    db_session.add(legacy)
    db_session.flush()
    legacy_id = legacy.id
    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    row = db_session.get(ComplianceControl, legacy_id)
    assert row is not None and row.is_active is False
    active = db_session.query(ComplianceControl).filter_by(framework_id=framework.id).filter(
        ComplianceControl.is_active.isnot(False)).count()
    assert active == 99


# --------------------------------------------------------------------------- #
# Scope key                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("spelling", ["ISO-27001", "ISO - 27001", "ISO27001", "iso 27001"])
def test_the_applicability_rule_is_found_through_any_spelling_of_the_framework_code(spelling):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    out_of_scope = SimpleNamespace(node_type="Mainframe")
    in_scope = SimpleNamespace(node_type="Virtual Machine")
    assert ApplicabilityService.evaluate_node(out_of_scope, spelling) is False
    assert ApplicabilityService.evaluate_node(in_scope, spelling) is True


def test_a_framework_without_rules_still_has_everything_in_scope():
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    assert ApplicabilityService.evaluate_node(SimpleNamespace(node_type="Mainframe"), "NIS2") is True
