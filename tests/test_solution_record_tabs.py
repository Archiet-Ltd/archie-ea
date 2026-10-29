"""The solution record, slice 5b: Requirements, Options and decision, Risks and Delivery tabs,
typed notes, and the old addresses that land on them.

Served through the test client against a fresh organisation. What a person sees in the
browser is proved by tests/smoke/test_solution_record_journey.py.
"""
import json
import uuid
from datetime import date

import pytest

from app.modules.solutions_strategic.v2.services import solution_record_service as svc
from tests.test_solution_record import _login, _user


@pytest.fixture
def record(app, db_session, make_org):
    from app.models.solution_models import Solution
    org = make_org("record-tabs")
    author = _user(db_session, org, "solution_architect", "author")
    solution = Solution(
        name=f"Tabs Fixture {uuid.uuid4().hex[:6]}", organization_id=org.id, created_by_id=author.id,
        description="Field readings are keyed twice.", solution_owner="Ivo Reed", governance_status="draft",
    )
    db_session.add(solution)
    db_session.flush()
    client = app.test_client()
    _login(client, author.id)
    return client, org, author, solution, db_session


def _element(session, org, name, kind="ApplicationComponent", layer="application"):
    from app.models.archimate_core import ArchiMateElement
    element = ArchiMateElement(name=name, organization_id=org.id, type=kind, layer=layer)
    session.add(element)
    session.flush()
    return element


def test_requirements_sit_beside_the_element_that_realises_them(record):
    client, org, author, solution, session = record
    from app.models.solution_architect_models import RequirementType, SolutionRequirement
    ledger = _element(session, org, "Calibration Ledger")
    session.add_all([
        SolutionRequirement(name="Record the as-found reading", description="Every instrument, every visit.",
                            organization_id=org.id, solution_id=solution.id, requirement_type=RequirementType.FUNCTIONAL,
                            moscow_priority="MUST", status="approved", archimate_element_id=ledger.id),
        SolutionRequirement(name="Export certificates as signed documents", description="PDF with a signature.",
                            organization_id=org.id, solution_id=solution.id, requirement_type=RequirementType.QUALITY,
                            priority=2, status="draft"),
    ])
    session.flush()

    # a requirement the model holds, realised through a realization relationship
    from app.models.models import ArchiMateRelationship
    from app.models.solution_models import SolutionArchiMateElement
    immutable = _element(session, org, "Calibration records must be immutable", "Requirement", "motivation")
    driver = _element(session, org, "Accreditation audit", "Driver", "motivation")
    for el in (immutable, driver, ledger):
        session.add(SolutionArchiMateElement(solution_id=solution.id, element_id=el.id, element_table="archimate_elements",
                                             layer_type=el.layer, element_name=el.name))
    session.add(ArchiMateRelationship(type="Realization", source_id=ledger.id, target_id=immutable.id, organization_id=org.id))
    session.flush()

    data = svc.requirements_for(solution, svc.design_groups(solution))
    names = [r["name"] for r in data["rows"]]
    assert names[0] == "Calibration records must be immutable", names  # the model's first
    assert set(names[1:]) == {"Export certificates as signed documents", "Record the as-found reading"}
    model_row = data["rows"][0]
    assert model_row["source"] == "model" and model_row["realised_by"]["name"] == "Calibration Ledger"
    record_row = next(r for r in data["rows"] if r["name"] == "Record the as-found reading")
    assert record_row["realised_by"]["name"] == "Calibration Ledger"
    assert record_row["priority"] == "Must" and record_row["kind"] == "Functional"
    assert data["from_model"] == 1 and data["realised"] == 2 and data["unrealised"] == 1
    assert [e["name"] for e in data["motivation"]] == ["Accreditation audit"]

    html = client.get(f"/solutions/{solution.id}?tab=requirements").get_data(as_text=True)
    assert 'data-testid="requirements-table"' in html and html.count('data-testid="requirement-row"') == 3
    assert html.count('data-testid="requirement-element-link"') == 2 and 'data-testid="requirement-link"' in html
    assert 'data-testid="motivation-table"' in html and "Accreditation audit" in html
    assert 'data-testid="requirements-composer"' in html
    assert 'data-testid="requirement-unrealised"' in html  # the second one says it is not linked yet
    assert 'data-testid="requirements-unrealised"' in html  # and the tab says so once, with the fix
    assert f"/solutions/{solution.id}/traceability" in html


def test_an_empty_requirements_tab_offers_the_first_one(record):
    client, org, author, solution, session = record
    html = client.get(f"/solutions/{solution.id}?tab=requirements").get_data(as_text=True)
    assert 'data-testid="requirements-empty"' in html and "Add a requirement" in html


def test_options_show_the_leader_and_the_decision(record):
    client, org, author, solution, session = record
    from app.models.architecture_decision import ArchitectureDecision
    options = [
        {"name": "Build on the ledger", "approach": "extend", "summary": "Extend the ledger the field app already uses.",
         "cost_band": "low", "timeline_months": 4, "risk": "medium", "recommended": True, "pros": ["Reuse"], "cons": []},
        {"name": "Buy a calibration suite", "approach": "buy", "summary": "A packaged suite with its own data model.",
         "cost_band": "high", "timeline_months": 9, "risk": "low", "recommended": False, "pros": [], "cons": ["Two stores"]},
    ]
    session.add(ArchitectureDecision(
        title="Extend the ledger rather than buy a suite", organization_id=org.id, solution_id=solution.id,
        enterprise_level=False, status="accepted", context="Two systems hold readings.",
        decision="Extend the Calibration Ledger.", rationale="It keeps one store of readings.",
        consequences="The field app changes; no new vendor.", alternatives=json.dumps(options),
        estimated_effort="Medium", business_value="High", created_by_id=author.id,
    ))
    session.flush()

    data = svc.options_for(solution)
    assert data["source"] == "decision"
    assert [o["name"] for o in data["options"]] == ["Build on the ledger", "Buy a calibration suite"]
    assert data["leader"]["name"] == "Build on the ledger"
    assert data["decision"]["status"] == "Accepted" and data["decision"]["effort"] == "Medium"

    html = client.get(f"/solutions/{solution.id}?tab=options").get_data(as_text=True)
    assert 'data-testid="options-table"' in html and html.count('data-testid="option-row"') == 2
    assert "Recommended" in html and "Build on the ledger." in html
    assert 'data-testid="options-decision"' in html and "It keeps one store of readings." in html
    assert f"/solutions/{solution.id}/options-advisor" in html


def test_an_empty_options_tab_offers_the_comparison(record):
    client, org, author, solution, session = record
    html = client.get(f"/solutions/{solution.id}?tab=options").get_data(as_text=True)
    assert 'data-testid="options-empty"' in html and "Compare options" in html


def test_risks_come_from_the_register_with_owners_and_a_grid(record):
    client, org, author, solution, session = record
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    ledger = _element(session, org, "Calibration Ledger")
    from app.models.solution_models import SolutionArchiMateElement
    session.add(SolutionArchiMateElement(solution_id=solution.id, element_id=ledger.id, element_table="archimate_elements",
                                         layer_type="application", element_name=ledger.name))
    direct = Risk(title="Engineers keep using paper job sheets", likelihood=5, impact=5, owner="Ivo Reed",
                  organization_id=org.id, solution_id=solution.id)
    linked = Risk(title="Readings are lost when a tester loses signal", likelihood=3, impact=5, owner="",
                  organization_id=org.id)
    on_element = Risk(title="The ledger load drops historic certificates", likelihood=2, impact=4,
                      owner="Compliance Officer", organization_id=org.id, archimate_element_id=ledger.id)
    session.add_all([direct, linked, on_element])
    session.flush()
    session.add(RiskEntityLink(risk_id=linked.id, entity_type="solution", entity_id=solution.id, organization_id=org.id))
    session.flush()

    data = svc.risks_for(solution, svc.design_groups(solution))
    titles = [r["title"] for r in data["rows"]]
    assert titles[0] == "Engineers keep using paper job sheets"  # highest score first
    assert set(titles) == {direct.title, linked.title, on_element.title}
    via = next(r for r in data["rows"] if r["title"] == on_element.title)
    assert via["via"] == "Calibration Ledger"
    assert data["without_owner"] == 1
    assert sum(c["count"] for row in data["grid"] for c in row) == 3
    top_right = data["grid"][0][4]
    assert (top_right["likelihood"], top_right["impact"], top_right["count"]) == (5, 5, 1)

    html = client.get(f"/solutions/{solution.id}?tab=risks").get_data(as_text=True)
    assert html.count('data-testid="risk-row"') == 3
    assert 'data-testid="risks-grid"' in html and "No owner yet" in html
    assert f"/risks/?solution_id={solution.id}" in html


def test_delivery_lists_the_work_with_what_it_depends_on(record):
    client, org, author, solution, session = record
    from app.models.implementation_migration import WorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage
    first = UnifiedWorkPackage(name="Ledger migration", context="solution", context_id=solution.id, organization_id=org.id,
                               status="completed", progress_percentage=100, start_date=date(2026, 11, 2),
                               end_date=date(2026, 12, 4), owner_id=author.id)
    session.add(first)
    session.flush()
    second = UnifiedWorkPackage(name="Field app rollout", context="solution", context_id=solution.id, organization_id=org.id,
                                status="in_progress", progress_percentage=40, start_date=date(2026, 12, 7),
                                work_dependencies=[first.id])
    session.add(second)
    # a row still only in the older table is shown once too
    session.add(WorkPackage(name="Certificate signing service", context="solution", context_id=solution.id,
                            organization_id=org.id, status="planned", percent_complete=0, sequence_order=3))
    session.flush()

    data = svc.delivery_for(solution, [])
    names = [p["name"] for p in data["packages"]]
    assert names[:2] == ["Ledger migration", "Field app rollout"], names
    assert "Certificate signing service" in names and len(names) == 3, names
    assert data["packages"][1]["depends_on"] == ["Ledger migration"]
    assert data["packages"][0]["needed_by"] == ["Field app rollout"]
    assert data["done"] == 1 and data["started"] == 1
    assert data["packages"][0]["owner"]

    html = client.get(f"/solutions/{solution.id}?tab=delivery").get_data(as_text=True)
    assert html.count('data-testid="work-package-row"') == 3
    assert "1 done, 1 under way, 1 not started" in html
    assert f"/solutions/{solution.id}/migration-roadmap" in html


def test_an_empty_delivery_tab_offers_the_roadmap(record):
    client, org, author, solution, session = record
    html = client.get(f"/solutions/{solution.id}?tab=delivery").get_data(as_text=True)
    assert 'data-testid="delivery-empty"' in html and "Open the roadmap" in html


def test_a_note_is_posted_and_listed_newest_first(record):
    client, org, author, solution, session = record
    resp = client.post(f"/solutions/{solution.id}/notes", json={"kind": "meeting", "subject": "Kick-off with field service",
                                                               "summary": "Agreed the pilot region."})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    resp = client.post(f"/solutions/{solution.id}/notes", json={"kind": "decision", "subject": "Pilot in the north first"})
    assert resp.status_code == 201
    assert client.post(f"/solutions/{solution.id}/notes", json={"kind": "tweet", "subject": "x"}).status_code == 400
    assert client.post(f"/solutions/{solution.id}/notes", json={"kind": "email"}).status_code == 400

    notes = svc.notes_for(solution.id)
    assert [n["subject"] for n in notes] == ["Pilot in the north first", "Kick-off with field service"]
    assert notes[0]["kind_label"] == "Decision" and notes[1]["by"]

    html = client.get(f"/solutions/{solution.id}").get_data(as_text=True)
    assert 'data-testid="note-form"' in html and html.count('data-testid="note-item"') == 2
    assert "Agreed the pilot region." in html


def test_the_old_addresses_land_on_their_tab(record):
    client, org, author, solution, session = record
    for path, tab in (("/risks/heatmap", "risks"), ("/risks", "risks")):
        resp = client.get(f"/solutions/{solution.id}{path}", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["Location"].endswith(f"/solutions/{solution.id}?tab={tab}"), (path, resp.headers["Location"])
    resp = client.get(f"/solutions/{solution.id}/communications")
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/solutions/{solution.id}#record-notes")
    # scripts still get the risk list as data
    resp = client.get(f"/solutions/{solution.id}/risks", headers={"Accept": "application/json"})
    assert resp.status_code == 200 and resp.is_json and resp.get_json()["success"] is True
    assert svc.tab_for("heatmap") == "risks" and svc.tab_for("communications") == "overview"
    assert svc.tab_for("traceability") == "requirements" and svc.tab_for("migration-roadmap") == "delivery"


def test_another_organisation_never_posts_a_note(app, db_session, make_org, record):
    client, org, author, solution, session = record
    other = make_org("other-notes")
    outsider = _user(db_session, other, "solution_architect", "outsider")
    _login(client, outsider.id)
    resp = client.post(f"/solutions/{solution.id}/notes", json={"kind": "email", "subject": "Hello"})
    assert resp.status_code in (403, 404)
    assert svc.notes_for(solution.id) == []
