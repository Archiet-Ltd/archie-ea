"""The reference library, the control-to-model mapping and the compliance answer (slice 1).

Pins: the library seed is platform-wide, idempotent and honest about how much of each
standard it holds; a control becomes one Requirement element (a Constraint when it is
a prohibition) through one function; controls reach applications, technology, processes
and capabilities through ordinary realisation relationships; the old application-only
rows stay readable and are folded, not deleted; one compliance answer serves the page,
the assistant and the connector; and one organisation never sees another's links.

Fixtures (app, db_session, make_org, client, login_as) come from app/modules/conftest.py.
"""

from __future__ import annotations

import uuid

import pytest
from flask import g

from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
from app.modules.intelligence.services import answer_envelope as envelope
from app.modules.intelligence.services.query_service import IntelligenceQueryService


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _control(db_session, code="AC-2", title="Account management", description=None, framework=None):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    if framework is None:
        framework = RegulatoryFramework(code=f"FW{uuid.uuid4().hex[:8]}", name="Test Framework", category="security")
        db_session.add(framework)
        db_session.flush()
    control = ComplianceControl(
        framework_id=framework.id, control_code=code, title=title, description=description,
    )
    db_session.add(control)
    db_session.flush()
    return control


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"fw-{uuid.uuid4().hex[:10]}@example.com", first_name="Fw", last_name="Tester",
        organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _in_org(app, org_id, fn):
    with app.test_request_context("/"):
        g.current_org_id = org_id
        return fn()


def _call_tool(app, user, org_id, arguments):
    with app.test_request_context("/"):
        g.current_org_id = org_id
        return ToolExecutor(user.id).execute(ToolCall(id="c1", name="answer", arguments=arguments))


@pytest.fixture
def seeded(app, db_session):
    from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    return RegulatoryFrameworkService


# --------------------------------------------------------------------------- #
# (b) The reference-library seed                                               #
# --------------------------------------------------------------------------- #


def _rows(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    return (
        db_session.query(RegulatoryFramework).count(),
        db_session.query(ComplianceControl).count(),
    )


def test_the_seed_is_idempotent_and_leaves_adoptions_alone(app, db_session, make_org, seeded):
    from app.models.regulatory_framework import FrameworkAdoption

    make_org("seed-a"), make_org("seed-b")
    first = _rows(db_session)
    seeded.seed_manufacturing_frameworks()
    seeded.seed_manufacturing_frameworks()
    assert _rows(db_session) == first
    assert db_session.query(FrameworkAdoption).count() == 0, "the seed never adopts anything for an organisation"


def test_nist_csf_and_gdpr_are_loaded_in_full_and_the_counts_say_so(db_session, seeded):
    counts = {row["code"]: row for row in seeded.library_counts()}
    nist, gdpr = counts["NIST-CSF"], counts["GDPR"]
    assert (nist["loaded"], nist["total"], nist["content_level"]) == (106, 106, "full")
    assert (gdpr["loaded"], gdpr["total"]) == (99, 99)


def test_gdpr_says_titles_for_all_and_summaries_for_some_not_full_text(db_session, seeded):
    counts = {row["code"]: row for row in seeded.library_counts()}
    gdpr = counts["GDPR"]
    assert gdpr["content_level"] == "titles"
    assert gdpr["with_summary"] == 46, "articles 5 to 50 carry a summary; the rest carry a title only"
    said = seeded.describe_count(gdpr)
    assert "99 of 99" in said and "titles" in said and "46 summaries" in said
    assert "full" not in said.lower()
    assert "106 of 106" in seeded.describe_count(counts["NIST-CSF"])


def test_no_library_title_is_cut_short(seeded):
    for pack in seeded.load_library_packs():
        for control in pack["controls"]:
            assert not control["title"].rstrip().endswith(("...", "\u2026")), (pack["code"], control["control_code"])
    nist = {c["control_code"]: c for pack in seeded.load_library_packs() if pack["code"] == "NIST-CSF" for c in pack["controls"]}
    assert nist["GV.OC-02"]["title"] == nist["GV.OC-02"]["description"]


def test_licensed_standards_are_identifiers_titles_and_a_link_only(db_session, seeded):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    counts = {row["code"]: row for row in seeded.library_counts()}
    for code, total in (("ISO-27001", 93), ("SOC-2", 61), ("PCI-DSS", 12)):
        assert (counts[code]["loaded"], counts[code]["total"]) == (total, total)
        assert counts[code]["content_level"] == "identifiers"
        framework = db_session.query(RegulatoryFramework).filter_by(code=code).one()
        assert framework.official_url.startswith("https://")
        texts = db_session.query(ComplianceControl.description).filter_by(framework_id=framework.id).all()
        assert all(text is None for (text,) in texts), f"{code} carries no licensed wording"


def test_a_sample_framework_does_not_claim_a_total(db_session, seeded):
    sample = [row for row in seeded.library_counts() if row["content_level"] == "sample"]
    assert sample, "the frameworks defined before the library stay as library rows"
    assert all(row["total"] is None and row["loaded"] >= 0 for row in sample)


def test_the_seed_does_not_duplicate_a_framework_already_seeded_under_another_spelling(db_session):
    from app.models.compliance_models import RegulatoryFramework
    from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

    db_session.add(RegulatoryFramework(code="ISO27001", name="ISO/IEC 27001:2022", category="security"))
    db_session.flush()
    RegulatoryFrameworkService.seed_manufacturing_frameworks()
    iso = db_session.query(RegulatoryFramework).filter(RegulatoryFramework.code.in_(["ISO27001", "ISO-27001", "ISO - 27001"]))
    assert iso.count() == 1


def test_the_seed_runs_on_deploy():
    import pathlib

    script = pathlib.Path(__file__).resolve().parents[4] / "scripts" / "database" / "deploy-schema.sh"
    assert "seed-framework-catalogue" in script.read_text()


# --------------------------------------------------------------------------- #
# (a) One mapping function                                                     #
# --------------------------------------------------------------------------- #


def test_a_control_becomes_one_requirement_element_with_its_id_as_a_property(app, db_session, make_org):
    from app.modules.compliance.services.control_model_service import control_to_element

    org = make_org("map")
    control = _control(db_session)

    first = control_to_element(control, org.id)
    second = control_to_element(control, org.id)

    assert first.id == second.id
    assert first.type == "Requirement" and first.layer.lower() == "motivation"
    assert first.organization_id == org.id
    assert first.custom_properties["compliance_control_id"] == control.id
    assert first.custom_properties["control_code"] == "AC-2"


def test_a_prohibition_becomes_a_constraint(db_session, make_org):
    from app.modules.compliance.services.control_model_service import control_to_element

    org = make_org("prohibit")
    prohibition = _control(db_session, "Art. 9", "Special categories",
                           "Processing special categories of personal data is prohibited unless an exception applies.")
    duty = _control(db_session, "Art. 32", "Security of processing",
                    "Controllers implement security measures appropriate to the risk.")

    assert control_to_element(prohibition, org.id).type == "Constraint"
    assert control_to_element(duty, org.id).type == "Requirement"


def test_two_organisations_get_their_own_element_for_the_same_control(db_session, make_org):
    from app.modules.compliance.services.control_model_service import control_to_element

    org_a, org_b = make_org("own-a"), make_org("own-b")
    control = _control(db_session)

    a, b = control_to_element(control, org_a.id), control_to_element(control, org_b.id)

    assert a.id != b.id
    assert (a.organization_id, b.organization_id) == (org_a.id, org_b.id)


# --------------------------------------------------------------------------- #
# (c) Links through ordinary realisation relationships                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "type_,layer",
    [("ApplicationComponent", "application"), ("Node", "technology"),
     ("BusinessProcess", "business"), ("Capability", "strategy")],
)
def test_a_control_links_to_any_core_element_by_realisation(db_session, make_org, type_, layer):
    from app.models import ArchiMateRelationship
    from app.modules.compliance.services.control_model_service import link_control_to_element

    org = make_org("link")
    target = _element(db_session, org.id, f"Target {type_}", type_, layer)
    control = _control(db_session)

    rel = link_control_to_element(control.id, target.id, org.id)
    again = link_control_to_element(control.id, target.id, org.id)

    assert rel.id == again.id
    assert rel.type == "realization" and rel.source_id == target.id
    assert rel.organization_id == org.id
    assert rel.connection_spec["origin"] == "compliance_link"
    requirement = db_session.get(type(target), rel.target_id)
    assert requirement.type == "Requirement"
    assert db_session.query(ArchiMateRelationship).filter_by(source_id=target.id).count() == 1


def test_a_link_to_another_organisations_element_is_refused(db_session, make_org):
    from app.modules.compliance.services.control_model_service import link_control_to_element

    org_a, org_b = make_org("refuse-a"), make_org("refuse-b")
    foreign = _element(db_session, org_b.id, "Foreign")
    control = _control(db_session)

    with pytest.raises(ValueError):
        link_control_to_element(control.id, foreign.id, org_a.id)


def test_the_map_route_uses_the_realisation_path_not_the_application_only_table(
    app, db_session, make_org, client, login_as
):
    from app.models import ArchiMateRelationship
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("route")
    user = _user(db_session, org.id)
    user.is_org_admin = True
    element = _element(db_session, org.id, "Billing")
    component = ApplicationComponent(name="Billing", organization_id=org.id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    control = _control(db_session)
    db_session.commit()

    login_as(client, user)
    response = client.post(f"/dashboard/api/applications/{component.id}/compliance/map", json={"control_id": control.id})

    assert response.status_code in (200, 201), response.get_data(as_text=True)
    assert db_session.query(ApplicationComplianceControl).count() == 0
    assert db_session.query(ArchiMateRelationship).filter_by(source_id=element.id, type="realization").count() == 1


def test_old_rows_stay_readable_and_are_folded_not_deleted(app, db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.compliance.services.control_model_service import fold_legacy_mappings

    org = make_org("fold")
    element = _element(db_session, org.id, "Ledger")
    component = ApplicationComponent(name="Ledger", organization_id=org.id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    control = _control(db_session)
    legacy = ApplicationComplianceControl(
        organization_id=org.id, application_id=component.id, control_id=control.id,
        implementation_status="implemented", evidence_url="https://evidence.example/ledger",
    )
    db_session.add(legacy)
    db_session.commit()
    legacy_id = legacy.id

    assert fold_legacy_mappings(org.id) == 1
    assert fold_legacy_mappings(org.id) == 0, "folding twice records nothing new"

    assert db_session.get(ApplicationComplianceControl, legacy_id) is not None
    rel = db_session.query(ArchiMateRelationship).filter_by(source_id=element.id, type="realization").one()
    assert rel.connection_spec["origin"] == "folded_application_compliance_control"
    assert rel.connection_spec["legacy_mapping_id"] == legacy_id

    result = _in_org(app, org.id, lambda: IntelligenceQueryService.compliance_for_element(element.id))
    [row] = result["controls"]
    assert row["control_id"] == control.id and row["implementation_status"] == "implemented"
    assert row["no_evidence"] is False, "the old row's evidence still counts after the fold"


# --------------------------------------------------------------------------- #
# (d) The compliance answer                                                    #
# --------------------------------------------------------------------------- #


def _linked_estate(app, db_session, org, type_="Node", layer="technology"):
    from app.modules.compliance.services.control_model_service import link_control_to_element

    node = _element(db_session, org.id, "Primary database host", type_, layer)
    control = _control(db_session, "PR.DS-01", "Data-at-rest protected")
    link_control_to_element(control.id, node.id, org.id)
    db_session.commit()
    return node, control


def test_compliance_for_element_answers_for_a_non_application_element(app, db_session, make_org):
    org = make_org("answer-node")
    node, control = _linked_estate(app, db_session, org)

    result = _in_org(app, org.id, lambda: IntelligenceQueryService.compliance_for_element(node.id))

    [row] = result["controls"]
    assert row["control_id"] == control.id and row["code"] == "PR.DS-01"
    assert row["no_evidence"] is True, "a linked control with no evidence recorded is unmet"
    assert "no_control_evidence" in result["reasons"]
    assert "no_application_component" not in result["reasons"]


def test_the_compliance_question_goes_through_the_answer_envelope(app, db_session, make_org):
    org = make_org("envelope")
    node, control = _linked_estate(app, db_session, org)

    record = _in_org(app, org.id, lambda: envelope.ask_and_store("compliance", {"element_id": node.id}))

    assert record.question_kind == "compliance"
    assert record.truth_class == "drawn"
    [row] = record.rows
    assert row["control_id"] == control.id and row["no_evidence"] is True
    assert {"type": "compliance_control", "id": control.id} in record.citations
    assert any(c["type"] == "relationship" for c in record.citations)
    assert record.summary["total"] == 1 and record.summary["unmet"] == 1
    assert "no_control_evidence" in record.reasons
    opened = _in_org(app, org.id, lambda: envelope.open_answer(record.address))
    assert opened.rows == record.rows


def test_a_malformed_compliance_question_is_refused(app, db_session, make_org):
    org = make_org("malformed")
    node, _control = _linked_estate(app, db_session, org)
    assert _in_org(app, org.id, lambda: envelope.ask_and_store("compliance", {"element_id": node.id})).rows
    for params in ({}, {"element_id": "x"}, {"element_id": -1}):
        with pytest.raises(envelope.AnswerParamError):
            _in_org(app, org.id, lambda p=params: envelope.ask_and_store("compliance", p))


def test_the_assistant_gives_the_same_rows_as_the_envelope(app, db_session, make_org):
    org = make_org("assistant")
    user = _user(db_session, org.id)
    node, control = _linked_estate(app, db_session, org)

    result = _call_tool(app, user, org.id, {"question": "compliance", "element_id": node.id})

    assert result["success"] is True, result
    [row] = result["result"]["rows"]
    assert row["control_id"] == control.id and row["no_evidence"] is True
    assert result["result"]["summary"]["unmet"] == 1
    assert {"type": "compliance_control", "id": control.id} in result["result"]["citations"]


def test_the_rest_route_and_the_envelope_agree(app, db_session, make_org, client, login_as):
    org = make_org("rest")
    user = _user(db_session, org.id)
    node, control = _linked_estate(app, db_session, org)

    login_as(client, user)
    body = client.post("/api/v1/intelligence/answers", json={"question": "compliance", "element_id": node.id})
    assert body.status_code == 201, body.get_data(as_text=True)
    data = body.get_json()["data"]
    assert [r["control_id"] for r in data["rows"]] == [control.id]

    login_as(client, user)
    page = client.get(data["page_url"])
    assert page.status_code == 200
    assert "PR.DS-01" in page.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# (e) The gate: two organisations                                              #
# --------------------------------------------------------------------------- #


def test_two_organisations_never_see_each_others_controls(app, db_session, make_org):
    from app.modules.compliance.services.control_model_service import link_control_to_element

    org_a, org_b = make_org("gate-a"), make_org("gate-b")
    user_b = _user(db_session, org_b.id)
    a_node = _element(db_session, org_a.id, "A node", "Node", "technology")
    b_node = _element(db_session, org_b.id, "B node", "Node", "technology")
    control = _control(db_session, "PR.DS-01", "Data-at-rest protected")
    link_control_to_element(control.id, a_node.id, org_a.id)
    db_session.commit()

    # B asking about A's element reads exactly as an element that does not exist.
    with pytest.raises(envelope.AnswerNotAskable) as refused:
        _in_org(app, org_b.id, lambda: envelope.ask_and_store("compliance", {"element_id": a_node.id}))
    assert refused.value.reason == "element_not_found"
    via_tool = _call_tool(app, user_b, org_b.id, {"question": "compliance", "element_id": a_node.id})
    assert via_tool["success"] is False and via_tool["reason"] == "element_not_found"

    # B's own element, linked to the same global control by A's organisation only, has nothing.
    own = _in_org(app, org_b.id, lambda: IntelligenceQueryService.compliance_for_element(b_node.id))
    assert own["controls"] == []

    # B linking the same control makes B's own element, not A's.
    link_control_to_element(control.id, b_node.id, org_b.id)
    db_session.commit()
    a_view = _in_org(app, org_a.id, lambda: IntelligenceQueryService.compliance_for_element(a_node.id))
    b_view = _in_org(app, org_b.id, lambda: IntelligenceQueryService.compliance_for_element(b_node.id))
    assert [c["control_id"] for c in a_view["controls"]] == [control.id]
    assert [c["control_id"] for c in b_view["controls"]] == [control.id]


def test_one_organisations_requirement_element_is_not_read_as_anothers(app, db_session, make_org):
    from app.modules.compliance.services.control_model_service import control_to_element, link_control_to_element

    org_a, org_b = make_org("elem-a"), make_org("elem-b")
    a_node = _element(db_session, org_a.id, "A node", "Node", "technology")
    control = _control(db_session)
    link_control_to_element(control.id, a_node.id, org_a.id)
    b_requirement = control_to_element(control, org_b.id)
    db_session.commit()

    result = _in_org(app, org_a.id, lambda: IntelligenceQueryService.compliance_for_element(a_node.id))
    assert [c["control_id"] for c in result["controls"]] == [control.id]
    assert b_requirement.organization_id == org_b.id


# --------------------------------------------------------------------------- #
# The defect: the security persona's controls figure                           #
# --------------------------------------------------------------------------- #


def test_the_security_persona_controls_figure_reads_the_organisations_mappings(app, db_session, make_org):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services.architect_persona_charters import _security_architect_context

    org = make_org("persona")
    element = _element(db_session, org.id, "CRM")
    component = ApplicationComponent(name="CRM", organization_id=org.id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    control = _control(db_session)
    db_session.add(ApplicationComplianceControl(
        organization_id=org.id, application_id=component.id, control_id=control.id,
        implementation_status="implemented",
    ))
    db_session.commit()

    text = _in_org(app, org.id, _security_architect_context)

    assert "controls: unavailable" not in text
    assert "1/1 mapped controls implemented" in text


# --------------------------------------------------------------------------- #
# Wording, and the fold loop                                                   #
# --------------------------------------------------------------------------- #


def test_nothing_a_person_reads_calls_a_control_unmet(app, db_session, make_org, client, login_as):
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    schema = TOOL_SCHEMA_BY_NAME["answer"]
    assert "unmet" not in schema["description"].lower()
    assert "unmet" not in str(schema["parameters"]).lower()

    org = make_org("wording")
    user = _user(db_session, org.id)
    node, _control = _linked_estate(app, db_session, org)
    login_as(client, user)
    data = client.post("/api/v1/intelligence/answers", json={"question": "compliance", "element_id": node.id}).get_json()["data"]
    login_as(client, user)
    page = client.get(data["page_url"]).get_data(as_text=True)
    assert "no evidence recorded" in page.lower()
    assert "unmet" not in page.lower()
    login_as(client, user)
    assert "unmet" not in client.get("/intelligence/answers").get_data(as_text=True).lower()


def _legacy(db_session, org_id, element, control, name):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    row = ApplicationComplianceControl(
        organization_id=org_id, application_id=component.id, control_id=control.id, implementation_status="planned",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_a_bad_row_is_skipped_and_reported_and_the_rest_are_folded(db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.modules.compliance.services.control_model_service import fold_legacy_mappings

    org, other = make_org("fold-bad"), make_org("fold-other")
    foreign = _element(db_session, other.id, "Foreign element")
    good_element = _element(db_session, org.id, "Good element")
    control = _control(db_session)
    bad = _legacy(db_session, org.id, foreign, control, "Points at a foreign element")
    good = _legacy(db_session, org.id, good_element, control, "Good app")
    db_session.commit()

    skipped = []
    assert fold_legacy_mappings(org.id, skipped=skipped) == 1
    assert [row["mapping_id"] for row in skipped] == [bad.id]
    assert skipped[0]["reason"]
    rel = db_session.query(ArchiMateRelationship).filter_by(source_id=good_element.id, type="realization").one()
    assert rel.connection_spec["legacy_mapping_id"] == good.id
    assert db_session.query(ArchiMateRelationship).filter_by(source_id=foreign.id).count() == 0


def test_two_old_rows_for_one_link_keep_the_first_legacy_id(db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.modules.compliance.services.control_model_service import fold_legacy_mappings

    org = make_org("fold-twice")
    element = _element(db_session, org.id, "Shared element")
    control = _control(db_session)
    first = _legacy(db_session, org.id, element, control, "App one")
    _legacy(db_session, org.id, element, control, "App two")
    db_session.commit()

    fold_legacy_mappings(org.id)
    fold_legacy_mappings(org.id)

    rel = db_session.query(ArchiMateRelationship).filter_by(source_id=element.id, type="realization").one()
    assert rel.connection_spec["legacy_mapping_id"] == first.id
