"""Saved scenarios, their export and a run's result card follow the one cost rule the Overview follows.

A person the rule withholds cost from reads no running cost on the list, the export, the saved reply or the
run card, and is told in a plain sentence why; the saved data is unchanged, so a person the rule admits reads
the same figures the run worked out. Another organisation's costs reach nobody.
"""

from __future__ import annotations

import csv
import io
import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

URL = "/strategic/api/scenarios"
RUN = "/strategic/api/simulations"
COST = 1234567.0
OTHER_COST = 7654321.0


def _user(db_session, org, enterprise_role):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(email="cost-%s@example.com" % uuid.uuid4().hex[:10], first_name="Cost", last_name="Tester",
                confirmed=True, organization_id=org.id, enterprise_role=enterprise_role,
                password_hash=generate_password_hash("x"))
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org, name, cost):
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    db_session.add(ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id,
                                        total_cost_of_ownership=cost, lifecycle_status="3. Production"))
    db_session.flush()
    return element


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("cost-a"), make_org("cost-b")
    return {
        "app": _application(db_session, org_a, "Ledger %s" % uuid.uuid4().hex[:6], COST),
        "other_app": _application(db_session, org_b, "Ledger %s" % uuid.uuid4().hex[:6], OTHER_COST),
        "architect": _user(db_session, org_a, "solution_architect"),
        "viewer": _user(db_session, org_a, "business_user"),
        "cto": _user(db_session, org_a, "cto"),
        "other_cto": _user(db_session, org_b, "cto"),
        "other_viewer": _user(db_session, org_b, "business_user"),
    }


def _body(element, name="Retire it"):
    return {"name": name, "change_type": "RETIRE", "element": {"id": element.id},
            "analysis_result": {"severity": "LOW"}}


def _save(client, login_as, world, name="Retire it"):
    login_as(client, world["architect"])
    response = client.post(URL, json=_body(world["app"], name))
    assert response.status_code == 201, response.get_data(as_text=True)[:300]
    return response


def _numbers(text):
    return [str(int(COST)), str(int(OTHER_COST)), "1,234,567", "7,654,321"]


def _holds_no_cost(text):
    return not any(n in text for n in _numbers(text))


def _cost_figure(payload):
    return {f["label"]: f for f in payload["figures"]}["Yearly running cost of all applications"]


def test_the_architect_whom_the_rule_withholds_from_reads_no_cost_in_the_save_reply(client, login_as, db_session, world):
    reply = _save(client, login_as, world)
    text = reply.get_data(as_text=True)
    assert _holds_no_cost(text), text[:400]
    result = reply.get_json()["analysis_result"]
    assert result["estimated_risk_cost"] is None
    assert "withheld for your role" in result["financial_risk_reason"]
    figure = _cost_figure(result)
    assert (figure["before"], figure["after"], figure["change"]) == (None, None, None)
    assert "withheld for your role" in figure["note"]


def test_the_list_withholds_cost_from_a_viewer_and_shows_it_to_the_cto_unchanged(client, login_as, db_session, world):
    _save(client, login_as, world)
    login_as(client, world["viewer"])
    listed = client.get(URL)
    assert listed.status_code == 200
    assert _holds_no_cost(listed.get_data(as_text=True))
    assert listed.get_json()[0]["analysis_result"]["costs_withheld"] is True
    login_as(client, world["cto"])
    result = client.get(URL).get_json()[0]["analysis_result"]
    assert result["estimated_risk_cost"] == COST
    figure = _cost_figure(result)
    assert (figure["before"], figure["after"]) == (COST, 0.0)


def test_the_cto_reads_the_figure_the_run_worked_out(client, login_as, db_session, world):
    _save(client, login_as, world)
    login_as(client, world["cto"])
    saved = _cost_figure(client.get(URL).get_json()[0]["analysis_result"])
    live = _cost_figure(client.post(RUN, json={"element_id": world["app"].id, "change_type": "RETIRE"}).get_json()["simulation"])
    assert saved["before"] == live["before"] == COST


def test_the_export_withholds_cost_and_says_why(client, login_as, db_session, world):
    scenario = _save(client, login_as, world).get_json()
    login_as(client, world["viewer"])
    response = client.get("%s/export.csv?ids=%s" % (URL, scenario["id"]))
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert _holds_no_cost(text), text
    rows = {r[0]: r[1] for r in csv.reader(io.StringIO(text))}
    assert "withheld for your role" in rows["Financial Risk (organisation currency)"]
    assert "withheld for your role" in rows["Yearly running cost of all applications"]
    login_as(client, world["cto"])
    admitted = {r[0]: r[1] for r in csv.reader(io.StringIO(client.get("%s/export.csv?ids=%s" % (URL, scenario["id"])).get_data(as_text=True)))}
    assert str(int(COST)) in admitted["Yearly running cost of all applications"]


def test_the_run_card_withholds_cost_from_a_viewer_and_shows_it_to_the_cto(client, login_as, db_session, world):
    login_as(client, world["viewer"])
    reply = client.post(RUN, json={"element_id": world["app"].id, "change_type": "RETIRE"})
    assert reply.status_code == 200
    text = reply.get_data(as_text=True)
    assert _holds_no_cost(text), text[:400]
    body = reply.get_json()
    assert body["estimated_financial_risk"] is None
    assert "withheld for your role" in body["financial_risk_reason"]
    assert _cost_figure(body["simulation"])["before"] is None
    login_as(client, world["cto"])
    admitted = client.post(RUN, json={"element_id": world["app"].id, "change_type": "RETIRE"}).get_json()
    assert admitted["estimated_financial_risk"] == COST
    assert _cost_figure(admitted["simulation"])["before"] == COST


def test_another_organisation_reads_none_of_the_costs(client, login_as, db_session, world):
    _save(client, login_as, world)
    for who in ("other_viewer", "other_cto"):
        login_as(client, world[who])
        listed = client.get(URL)
        assert listed.get_json() == []
        run = client.post(RUN, json={"element_id": world["app"].id, "change_type": "RETIRE"})
        assert run.status_code == 404
        assert str(int(COST)) not in listed.get_data(as_text=True) + run.get_data(as_text=True)


def test_an_element_name_with_a_space_before_the_equals_sign_is_neutralised_in_the_export(client, login_as, db_session, world):
    from app.models import ArchiMateElement

    element = ArchiMateElement(name=" =1+1 walk", type="ApplicationComponent", layer="application",
                               organization_id=world["architect"].organization_id)
    db_session.add(element)
    db_session.flush()
    login_as(client, world["architect"])
    saved = client.post(URL, json=_body(element, "Retire the spaced one"))
    assert saved.status_code == 201, saved.get_data(as_text=True)[:300]
    login_as(client, world["cto"])
    text = client.get("%s/export.csv?ids=%s" % (URL, saved.get_json()["id"])).get_data(as_text=True)
    rows = {r[0]: r[1] for r in csv.reader(io.StringIO(text))}
    assert rows["Element"] == "' =1+1 walk"


@pytest.mark.parametrize("raw", ["scenario-²", "scenario-٣", "²"])
def test_an_id_with_a_superscript_digit_is_refused_in_words_not_with_a_server_error(client, login_as, db_session, world, raw):
    _save(client, login_as, world)
    exported = client.get(URL + "/export.csv", query_string={"ids": raw})
    assert exported.status_code == 400
    assert "Choose at least one saved scenario" in exported.get_json()["error"]
    renamed = client.patch("%s/%s" % (URL, raw), json={"name": "x"})
    assert renamed.status_code == 404
    assert "not saved for your organisation" in renamed.get_json()["error"]


def test_the_result_card_names_a_risk_without_a_link_while_the_risk_register_is_closed(client, login_as, db_session, world, monkeypatch):
    from app.models.risk import Risk, RiskStatus

    risk = Risk(title="Ledger outage", likelihood=3, impact=3, status=RiskStatus.OPEN,
                organization_id=world["viewer"].organization_id, archimate_element_id=world["app"].id)
    db_session.add(risk)
    db_session.flush()
    login_as(client, world["viewer"])
    body = {"element_id": world["app"].id, "change_type": "RETIRE"}
    listed = client.post(RUN, json=body).get_json()["simulation"]["risks"]
    assert listed and listed[0]["href"] == "/risks/%d" % risk.id
    monkeypatch.setattr("app.release_waves.is_released_endpoint", lambda endpoint: endpoint != "risk.risk_page")
    closed = client.post(RUN, json=body).get_json()["simulation"]["risks"]
    assert [r["title"] for r in closed] == ["Ledger outage"]
    assert closed[0]["href"] is None
    assert "Ledger outage" in json.dumps(closed)
