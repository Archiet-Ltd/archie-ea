"""ISO 27001, SOC 2 and GDPR rows sit under the right code, say where they come from, carry no invented rating,
and an organisation that adopted the earlier lists gets its unedited rows corrected.

Two real organisations in the correction test: one tops up, the other must be left as it was.
"""

import re

import pytest
from flask import g

from app import db
from app.models.application_compliance import ApplicationComplianceControl
from app.models.compliance_models import ComplianceControl, RegulatoryFramework
from app.modules.account.services import onboarding_service as onboarding
from app.modules.compliance.services.applicability_service import ApplicabilityService
from app.seed_data.standard_control_totals import PUBLISHED_CONTROLS, scope_note
from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService
from tests.test_signup_canvases_and_frameworks import _choose, two  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")

# The 2017 trust services criteria: each identifier and a word its topic always contains.
SOC2_PUBLISHED = {
    "CC1.1": "integrity", "CC1.2": "board", "CC1.3": "authority", "CC1.4": "competent", "CC1.5": "account",
    "CC2.1": "information", "CC2.2": "inside", "CC2.3": "outside",
    "CC3.1": "objectives", "CC3.2": "risks", "CC3.3": "fraud", "CC3.4": "changes",
    "CC4.1": "checking", "CC4.2": "weaknesses",
    "CC5.1": "choosing", "CC5.2": "technology", "CC5.3": "policies",
    "CC6.1": "access controls", "CC6.2": "registering", "CC6.3": "removing access", "CC6.4": "physical",
    "CC6.5": "retiring", "CC6.6": "outside", "CC6.7": "moves", "CC6.8": "malicious",
    "CC7.1": "configuration", "CC7.2": "unusual", "CC7.3": "judging", "CC7.4": "responding", "CC7.5": "recovering",
    "CC8.1": "changes", "CC9.1": "disruption", "CC9.2": "vendors",
    "A1.1": "capacity", "A1.2": "backups", "A1.3": "recovery",
    "C1.1": "confidential", "C1.2": "disposing",
    "PI1.1": "defining", "PI1.2": "inputs", "PI1.3": "processing", "PI1.4": "outputs", "PI1.5": "storing",
    "P1.1": "privacy", "P2.1": "consent", "P3.1": "collecting", "P3.2": "consent",
    "P4.1": "stated", "P4.2": "retaining", "P4.3": "disposing", "P5.1": "access", "P5.2": "correcting",
    "P6.1": "disclosing", "P6.2": "record of authorised", "P6.3": "record of unauthorised",
    "P6.4": "commitments", "P6.5": "vendors", "P6.6": "breaches", "P6.7": "held and disclosed",
    "P7.1": "quality", "P8.1": "complaints",
}

# The published control names of ISO/IEC 27001:2022 Annex A, kept here only to check no title repeats one.
ISO_PUBLISHED_NAMES = """
Policies for information security|Information security roles and responsibilities|Segregation of duties|
Management responsibilities|Contact with authorities|Contact with special interest groups|Threat intelligence|
Information security in project management|Inventory of information and other associated assets|
Acceptable use of information and other associated assets|Return of assets|Classification of information|
Labelling of information|Information transfer|Access control|Identity management|Authentication information|
Access rights|Information security in supplier relationships|
Addressing information security within supplier agreements|
Managing information security in the ICT supply chain|Monitoring, review and change management of supplier services|
Information security for use of cloud services|Information security incident management planning and preparation|
Assessment and decision on information security events|Response to information security incidents|
Learning from information security incidents|Collection of evidence|Information security during disruption|
ICT readiness for business continuity|Legal, statutory, regulatory and contractual requirements|
Intellectual property rights|Protection of records|Privacy and protection of PII|
Independent review of information security|Compliance with policies, rules and standards for information security|
Documented operating procedures|Screening|Terms and conditions of employment|
Information security awareness, education and training|Disciplinary process|
Responsibilities after termination or change of employment|Confidentiality or non-disclosure agreements|
Remote working|Information security event reporting|Physical security perimeters|Physical entry|
Securing offices, rooms and facilities|Physical security monitoring|
Protecting against physical and environmental threats|Working in secure areas|Clear desk and clear screen|
Equipment siting and protection|Security of assets off-premises|Storage media|Supporting utilities|Cabling security|
Equipment maintenance|Secure disposal or re-use of equipment|User endpoint devices|Privileged access rights|
Information access restriction|Access to source code|Secure authentication|Capacity management|
Protection against malware|Management of technical vulnerabilities|Configuration management|Information deletion|
Data masking|Data leakage prevention|Information backup|Redundancy of information processing facilities|Logging|
Monitoring activities|Clock synchronization|Use of privileged utility programs|
Installation of software on operational systems|Networks security|Security of network services|
Segregation of networks|Web filtering|Use of cryptography|Secure development life cycle|
Application security requirements|Secure system architecture and engineering principles|Secure coding|
Security testing in development and acceptance|Outsourced development|
Separation of development, test and production environments|Change management|Test information|
Protection of information systems during audit testing
""".replace("\n", "").split("|")


def _plain(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _catalogue(code):
    return {
        (c.get("control_code") or c.get("control_id")): c
        for c in next(f for f in RegulatoryFrameworkService.load_frameworks_from_json() if f["code"] == code)["controls"]
    }


def test_each_soc_2_code_is_a_published_criterion_with_its_own_topic():
    rows = _catalogue("SOC-2")
    assert set(rows) == set(SOC2_PUBLISHED) and len(rows) == PUBLISHED_CONTROLS["SOC-2"] == 61
    wrong = {code: rows[code]["title"] for code, word in SOC2_PUBLISHED.items() if word not in rows[code]["title"].lower()}
    assert wrong == {}


def test_no_iso_or_soc_2_title_repeats_a_published_control_name():
    assert len(ISO_PUBLISHED_NAMES) == 93
    published = {_plain(name) for name in ISO_PUBLISHED_NAMES}
    for code in ("ISO - 27001", "SOC-2"):
        repeated = [c for c, row in _catalogue(code).items() if _plain(row["title"]) in published]
        assert repeated == [], code
    assert len({row["title"] for row in _catalogue("ISO - 27001").values()}) == 93


def test_no_row_carries_a_priority_and_every_row_names_its_reference():
    for code, prefix in (("ISO - 27001", "ISO/IEC 27001:2022 Annex A."), ("SOC-2", "TSC 2017 "), ("GDPR", "Regulation (EU) 2016/679, Article ")):
        for control, row in _catalogue(code).items():
            assert not row.get("priority"), (code, control)
            assert row["official_reference"].startswith(prefix), (code, control)


def test_gdpr_lists_the_duty_bearing_transfer_articles_and_says_what_the_list_is():
    numbers = {int(c.split(" - ")[1]) for c in _catalogue("GDPR")}
    assert {45, 47, 48, 49} <= numbers
    assert len(numbers) == PUBLISHED_CONTROLS["GDPR"] == 40
    note = scope_note("GDPR")
    assert "duty on a controller or processor" in note and "not every article" in note
    assert scope_note("ISO - 27001") is None


# What an earlier release loaded, by code: title, heading, reference (none), description, priority.
OLD_ISO = {"A.8.2": ("Access Control", "technical", "Ensure authorized access and prevent unauthorized access to systems", "critical")}
OLD_SOC = {"CC3.1": ("COSO Principle 14 — Internal communication", "Common Criteria",
                     "The entity internally communicates information to support internal control.", "medium"),
           "CC6.3": ("Security awareness and training", "Security",
                     "Personnel responsible for security are provided with appropriate training.", "medium")}
OLD_GDPR = {"Article - 32": ("Security of Processing", "technical",
                             "Implement appropriate technical and organizational measures", "critical")}


def _rows(code):
    framework = RegulatoryFramework.query.filter_by(code=code).one()
    return {r.control_code: r for r in ComplianceControl.query.filter_by(framework_id=framework.id)}


def _held(org):
    return db.session.execute(db.text(
        "SELECT count(*) FROM application_compliance_controls WHERE organization_id = :o"), {"o": org.id}).scalar()


def test_top_up_corrects_held_rows_an_organisation_never_edited_and_leaves_an_edited_one(client, login_as, two):
    for code in ("ISO - 27001", "SOC-2", "GDPR"):
        RegulatoryFrameworkService.seed_manufacturing_frameworks(codes={code})
    _choose(client, login_as, two["one_owner"], [], ["iso27001", "soc2", "gdpr"])
    _choose(client, login_as, two["two_owner"], [], ["iso27001", "soc2", "gdpr"])
    # the rows as the earlier lists left them in a database that has held them since
    for code, old in (("ISO - 27001", OLD_ISO), ("SOC-2", OLD_SOC), ("GDPR", OLD_GDPR)):
        for control, (title, heading, description, priority) in old.items():
            row = _rows(code)[control]
            row.title, row.category, row.description, row.priority = title, heading, description, priority
            row.official_reference = None
    edited = _rows("SOC-2")["CC6.3"]
    edited.title = "Training we run each spring"
    db.session.flush()
    second_before = _held(two["two"])

    g.pop("current_org_id", None)  # the job queue that runs this has no signed-in organisation
    onboarding.run_starter_data(two["one"].id)

    iso, soc, gdpr = _rows("ISO - 27001"), _rows("SOC-2"), _rows("GDPR")
    assert iso["A.8.2"].title == "Tight control of administrator-level access"
    assert iso["A.8.2"].official_reference == "ISO/IEC 27001:2022 Annex A.8.2" and iso["A.8.2"].priority is None
    assert soc["CC3.1"].title == "Stating objectives clearly" and soc["CC3.1"].official_reference == "TSC 2017 CC3.1"
    assert gdpr["Article - 32"].title == "Security of processing"
    assert gdpr["Article - 32"].category == "Controller and processor" and gdpr["Article - 32"].priority is None
    assert soc["CC6.3"].title == "Training we run each spring" and soc["CC6.3"].official_reference is None
    assert soc["CC6.5"].title.startswith("Retiring assets")
    assert second_before == _held(two["two"])
    assert {int(c.split(" - ")[1]) for c in gdpr} >= {45, 47, 49}


NEAR_REPEAT_RATIO = 0.9  # a title this close to a published one is the same name with a word or letter changed


def _closeness(a, b):
    """Share of characters two titles have in common, ignoring case, spaces and punctuation."""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, _plain(a), _plain(b)).ratio()


def test_no_iso_title_is_a_near_repeat_of_a_published_control_name():
    near = {}
    for code, row in _catalogue("ISO - 27001").items():
        best = max(ISO_PUBLISHED_NAMES, key=lambda name: _closeness(row["title"], name))
        if _closeness(row["title"], best) > NEAR_REPEAT_RATIO:
            near[code] = (row["title"], best)
    assert near == {}


def test_the_near_repeat_check_catches_a_published_name_with_one_word_or_letter_changed():
    assert _closeness("Clock synchronisation", "Clock synchronization") > NEAR_REPEAT_RATIO
    assert _closeness("Network security", "Networks security") > NEAR_REPEAT_RATIO
    assert _closeness("Secure architecture and engineering principles",
                      "Secure system architecture and engineering principles") > NEAR_REPEAT_RATIO
    assert _closeness("Tight control of administrator-level access", "Privileged access rights") < NEAR_REPEAT_RATIO
