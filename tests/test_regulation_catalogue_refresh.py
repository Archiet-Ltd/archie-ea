"""Organisations that already hold the first DORA rows get the corrected catalogue; descriptions are our own words;
one writer links an application to a requirement.

Two real organisations in the refresh test: what one organisation's top-up does never reaches the other's rows.
"""

import pathlib
import re

import pytest
from flask import g

from app import db
from app.models.application_compliance import ApplicationComplianceControl
from app.modules.account.services import onboarding_service as onboarding
from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService
from tests.test_regulation_catalogues import _application, _controls
from tests.test_signup_canvases_and_frameworks import _choose, _owner, two  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")

# What the first release loaded for DORA: fifteen articles, some under another heading or with another title,
# none with a reference.
FIRST_DORA = {
    "5": ("ICT governance and organisation", "ICT Risk Management"),
    "6": ("ICT risk management framework", "ICT Risk Management"),
    "7": ("ICT systems, protocols and tools", "ICT Risk Management"),
    "8": ("Identification of ICT-related incidents", "ICT Risk Management"),
    "9": ("Protection and prevention", "ICT Risk Management"),
    "10": ("Detection", "ICT Risk Management"),
    "11": ("Response and recovery", "ICT Risk Management"),
    "12": ("Backup policies and restoration", "ICT Risk Management"),
    "13": ("Learning and evolving", "ICT Risk Management"),
    "15": ("ICT incident management process", "Incident Reporting"),
    "17": ("Classification of ICT-related incidents", "Incident Reporting"),
    "19": ("Reporting of major incidents", "Incident Reporting"),
    "24": ("Digital operational resilience testing", "Resilience Testing"),
    "26": ("Threat-led penetration testing", "Resilience Testing"),
    "28": ("Third-party ICT risk strategy", "Third-Party Risk"),
}
FIRST_DESCRIPTIONS = {
    "5": "The management body shall define and oversee the ICT risk management framework.",
    "6": "A sound ICT risk management framework shall be established as part of the overall risk management system.",
    "7": "ICT systems and tools shall be identified, classified and adequately protected.",
    "8": "Mechanisms to promptly detect anomalous activities and ICT-related incidents shall be in place.",
    "9": "Measures shall be in place to protect ICT systems and prevent incidents.",
    "10": "Mechanisms to promptly detect anomalous activities shall be in place.",
    "11": "Comprehensive ICT business continuity policies and disaster recovery plans shall be in place.",
    "12": "Backup policies and restoration and recovery procedures shall be established.",
    "13": "Capabilities and staff shall be kept up to date with evolving ICT risk.",
    "15": "An ICT incident management process shall be established to detect, manage and notify ICT-related incidents.",
    "17": "ICT-related incidents shall be classified based on criteria including impact and duration.",
    "19": "Major ICT-related incidents shall be reported to the competent authority.",
    "24": "A comprehensive digital operational resilience testing programme shall be established.",
    "26": "Advanced threat-led penetration testing shall be carried out at least every three years.",
    "28": "A strategy on ICT third-party risk shall be established and regularly reviewed.",
}


def _held(org):
    """How many catalogue rows the organisation holds, counted in SQL so the signed-in organisation's filter is out of it."""
    return db.session.execute(db.text(
        "SELECT count(*) FROM application_compliance_controls WHERE organization_id = :o AND application_id IS NULL"),
        {"o": org.id}).scalar()


def _dora_rows():
    return {
        row.control_code: row for row in db.session.execute(
            db.text("SELECT c.id, c.control_code, c.title, c.category, c.official_reference FROM compliance_controls c "
                    "JOIN regulatory_frameworks f ON f.id = c.framework_id WHERE f.code = 'DORA'")).all()
    }


def _back_to_the_first_release():
    """Put the DORA catalogue back as the first release left it: fifteen rows, old values, no references."""
    framework_id = db.session.execute(db.text("SELECT id FROM regulatory_frameworks WHERE code = 'DORA'")).scalar()
    for article, (title, category) in FIRST_DORA.items():
        db.session.execute(db.text(
            "UPDATE compliance_controls SET title = :t, category = :g, official_reference = NULL, description = :d "
            "WHERE framework_id = :f AND control_code = :c"),
            {"t": title, "g": category, "d": FIRST_DESCRIPTIONS[article], "f": framework_id, "c": "DORA-Art." + article})
    extra = [
        row.id for row in db.session.execute(db.text(
            "SELECT id, control_code FROM compliance_controls WHERE framework_id = :f"), {"f": framework_id})
        if row.control_code.rsplit(".", 1)[1] not in FIRST_DORA
    ]
    db.session.execute(db.text("DELETE FROM application_compliance_controls WHERE control_id = ANY(:ids)"), {"ids": extra})
    db.session.execute(db.text("DELETE FROM compliance_controls WHERE id = ANY(:ids)"), {"ids": extra})
    db.session.flush()
    return framework_id


def test_top_up_corrects_untouched_first_release_rows_and_keeps_an_edited_one(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["dora"])
    _choose(client, login_as, two["two_owner"], [], ["dora"])
    framework_id = _back_to_the_first_release()
    stale = _dora_rows()
    assert stale["DORA-Art.15"].category == "Incident Reporting" and stale["DORA-Art.8"].official_reference is None
    db.session.execute(db.text("UPDATE compliance_controls SET title = :t WHERE id = :i"),
                       {"t": "Backups we run nightly", "i": stale["DORA-Art.12"].id})
    db.session.flush()
    second_before = _held(two["two"])

    g.pop("current_org_id", None)  # the job queue that runs this has no signed-in organisation
    onboarding.run_starter_data(two["one"].id)

    rows = _dora_rows()
    assert len(rows) == 41
    assert rows["DORA-Art.15"].category == "ICT Risk Management"
    assert rows["DORA-Art.8"].title == "Identification"
    assert all(row.official_reference == "Regulation (EU) 2022/2554, Article %s" % code.rsplit(".", 1)[1]
               for code, row in rows.items() if code != "DORA-Art.12")
    assert rows["DORA-Art.12"].title == "Backups we run nightly" and rows["DORA-Art.12"].official_reference is None
    assert _held(two["one"]) == 41
    # the second organisation's rows were not touched by the first one's top-up
    assert _held(two["two"]) == second_before == 15

    login_as(client, two["one_owner"])
    listed = client.get("/dashboard/api/compliance/frameworks/%d/controls" % framework_id).get_json()
    articles = [int(row["control_code"].rsplit(".", 1)[1]) for row in listed]
    assert articles == list(range(5, 46))
    assert all(row["official_reference"] for row in listed if row["control_code"] != "DORA-Art.12")


def test_the_order_is_by_article_number_for_requirements_to_link(client, login_as, two, tenant_ctx):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    _choose(client, login_as, two["one_owner"], [], ["dora", "nis2"])
    with tenant_ctx(two["one"].id):
        codes = [row["code"] for row in ApplicabilityService.requirements_to_link(two["one"].id)]
    dora = [int(c.rsplit(".", 1)[1]) for c in codes if c.startswith("DORA")]
    assert dora == list(range(5, 46))
    nis2 = [c for c in codes if c.startswith("NIS2")]
    assert nis2[0] == "NIS2-Art.21(2)(a)" and nis2[-1] == "NIS2-Art.23(4)(d)"


def _words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _published_text():
    """The published text by act and article number, and every line of it as a set of eight-word runs."""
    path = pathlib.Path(__file__).parent.joinpath("data", "official_regulation_sentences.txt")
    articles, act, article, runs = {}, None, None, set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Article "):
            article = line[len("## Article "):].strip()
            articles.setdefault(act, {}).setdefault(article, [])
            continue
        if line.startswith("## "):
            act = line[3:].strip()
            continue
        if line.startswith("#") or not line.strip():
            continue
        articles[act][article].append(line)
        words = _words(line)
        runs.update(tuple(words[i:i + 8]) for i in range(len(words) - 7))
    return articles, runs


def test_no_description_repeats_eight_words_of_the_regulation():
    articles, runs = _published_text()
    checked = 0
    for framework in RegulatoryFrameworkService.load_frameworks_from_json():
        if framework["code"] not in ("DORA", "NIS2"):
            continue
        for control in framework["controls"]:
            article = re.search(r"Art\.(\d+)", control["control_code"]).group(1)
            published = articles[framework["code"]].get(article)
            assert published, "%s has no published text to be held against" % control["control_code"]
            assert sum(len(_words(line)) for line in published) >= 50, control["control_code"]
            words = _words(control["description"])
            checked += 1
            assert len(words) >= 3
            for i in range(len(words) - 7):
                assert tuple(words[i:i + 8]) not in runs, "%s repeats the regulation: %s" % (
                    control["control_code"], " ".join(words[i:i + 8]))
    assert checked == 41 + 13


def test_the_application_page_and_the_compliance_api_store_the_same_link(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["dora"])
    first, second = _application(two["one"]), _application(two["one"], "Ledger")
    control_id = db.session.execute(db.text("SELECT id FROM compliance_controls WHERE control_code = 'DORA-Art.28'")).scalar()
    login_as(client, two["one_owner"])
    assert client.post("/applications/%d/requirements" % first.id, data={"control_id": control_id}).status_code == 302
    reply = client.post("/dashboard/api/applications/%d/compliance/map" % second.id, json={"control_id": control_id})
    assert reply.status_code == 200, reply.get_data(as_text=True)

    def stored(app_row):
        link = ApplicationComplianceControl.query.filter_by(application_id=app_row.id, control_id=control_id).one()
        return (link.organization_id, link.adoption_id, link.implementation_status, link.notes, link.evidence_url)

    assert stored(first) == stored(second)
    assert stored(first)[1] is not None
    # a requirement of a framework the organisation never adopted cannot be linked through either door
    other = db.session.execute(db.text("SELECT id FROM compliance_controls WHERE control_code NOT LIKE 'DORA%' LIMIT 1")).scalar()
    if other is not None:
        assert client.post("/dashboard/api/applications/%d/compliance/map" % first.id, json={"control_id": other}).status_code == 400


def test_only_one_place_writes_an_application_to_requirement_link():
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    writers = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for call in re.finditer(r"ApplicationComplianceControl\(([^)]*)\)", text):
            if "application_id=" in call.group(1):
                writers.append(str(path.relative_to(root)))
    assert writers == ["modules/compliance/services/applicability_service.py"]


def _links(org_id=None, application_id=None):
    sql = "SELECT count(*) FROM application_compliance_controls WHERE application_id IS NOT NULL"
    params = {}
    if org_id is not None:
        sql += " AND organization_id = :o"
        params["o"] = org_id
    if application_id is not None:
        sql += " AND application_id = :a"
        params["a"] = application_id
    return db.session.execute(db.text(sql), params).scalar()


def _requirement(code):
    return db.session.execute(db.text("SELECT id FROM compliance_controls WHERE control_code = :c"), {"c": code}).scalar()


def test_a_link_to_another_organisations_application_is_refused_and_writes_nothing(client, login_as, two):
    from app.modules.compliance.services.applicability_service import ApplicabilityService, NotFoundInOrganisation

    _choose(client, login_as, two["one_owner"], [], ["dora"])
    _choose(client, login_as, two["two_owner"], [], ["dora"])
    mine, theirs = _application(two["one"]), _application(two["two"])
    control_id = _requirement("DORA-Art.28")

    with pytest.raises(NotFoundInOrganisation):
        ApplicabilityService.link_requirement(two["one"].id, theirs.id, control_id)
    with pytest.raises(NotFoundInOrganisation):
        ApplicabilityService.link_requirement(two["one"].id, 2_000_000_000, control_id)
    assert _links(application_id=theirs.id) == 0

    login_as(client, two["one_owner"])
    other = client.post("/dashboard/api/applications/%d/compliance/map" % theirs.id, json={"control_id": control_id})
    missing = client.post("/dashboard/api/applications/2000000000/compliance/map", json={"control_id": control_id})
    assert other.status_code == missing.status_code == 404
    assert other.get_data() == missing.get_data()
    assert _links(application_id=theirs.id) == 0 and _links(org_id=two["two"].id) == 0

    # its own application still links, and only for the organisation that holds the requirement
    assert client.post("/dashboard/api/applications/%d/compliance/map" % mine.id, json={"control_id": control_id}).status_code == 200
    assert _links(application_id=mine.id) == 1 and _links(org_id=two["two"].id) == 0


def test_a_requirement_the_organisation_has_not_adopted_is_refused(client, login_as, two):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    _choose(client, login_as, two["one_owner"], [], ["dora"])
    _choose(client, login_as, two["two_owner"], [], ["nis2"])
    theirs_only = _application(two["two"])
    with pytest.raises(ValueError):
        ApplicabilityService.link_requirement(two["two"].id, theirs_only.id, _requirement("DORA-Art.28"))
    assert _links(application_id=theirs_only.id) == 0


def test_a_call_with_no_organisation_raises_and_reads_no_organisations_rows(client, login_as, two):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    _choose(client, login_as, two["one_owner"], [], ["dora"])
    mine = _application(two["one"])
    control_id = _requirement("DORA-Art.28")
    with pytest.raises(ValueError):
        ApplicabilityService.link_requirement(None, mine.id, control_id)
    with pytest.raises(ValueError):
        ApplicabilityService.requirements_to_link(None)
    with pytest.raises(ValueError):
        ApplicabilityService.requirements_of_application(None, mine.id)
    assert _links(application_id=mine.id) == 0


def test_an_applications_own_requirements_come_back_in_article_order(client, login_as, two):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    _choose(client, login_as, two["one_owner"], [], ["dora", "nis2"])
    app_row = _application(two["one"])
    # linked out of order on purpose
    for code in ("DORA-Art.28", "NIS2-Art.23(4)(b)", "DORA-Art.9", "NIS2-Art.21(2)(a)", "DORA-Art.10", "DORA-Art.45"):
        ApplicabilityService.link_requirement(two["one"].id, app_row.id, _requirement(code))
    codes = [row["code"] for row in ApplicabilityService.requirements_of_application(two["one"].id, app_row.id)]
    assert codes == ["DORA-Art.9", "DORA-Art.10", "DORA-Art.28", "DORA-Art.45", "NIS2-Art.21(2)(a)", "NIS2-Art.23(4)(b)"]
    assert ApplicabilityService.requirements_of_application(two["two"].id, app_row.id) == []
