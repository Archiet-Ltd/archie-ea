"""T-ain-04: trust signals -- how complete and how fresh each area of the model is.

Pins: coverage is stated with its denominator ("2 of 4 owned"); an area with no
elements says "nothing recorded", never a percentage; freshness is the newest
and the median last change; the figures on the Catalogue index page, in the
answer envelope and through the read API are the same figures from the one
resolver; and none of it crosses an organisation.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/conftest.py.
"""

from __future__ import annotations

import datetime as _dt
import uuid


def _user(db_session, org_id, role="enterprise_architect"):
    from app.models.user import User

    user = User(
        email=f"trust-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Trust",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(
    db_session, org_id, name, type_="ApplicationComponent", layer="application", recorded_days_ago=None, undated=False
):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    if recorded_days_ago is not None or undated:
        _stamp(db_session, el.id, recorded_days_ago)
    return el


def _stamp(db_session, element_id, days_ago):
    """Set an element's change stamp. Written with triggers off for the one
    statement: where the history trigger is installed it would otherwise
    re-stamp the row with the time of the write."""
    from sqlalchemy import text

    stamp = None if days_ago is None else _dt.datetime.utcnow() - _dt.timedelta(days=days_ago)
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    db_session.execute(
        text("UPDATE archimate_elements SET recorded_at = :stamp WHERE id = :id"), {"stamp": stamp, "id": element_id}
    )
    db_session.execute(text("SET LOCAL session_replication_role = origin"))


def _link(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _component(db_session, org_id, element, *, lifecycle=None, cost=None):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(
        name=element.name, organization_id=org_id, archimate_element_id=element.id, lifecycle_status=lifecycle
    )
    if cost is not None:
        component.total_cost_of_ownership = cost
    db_session.add(component)
    db_session.flush()
    return component


def _owner(db_session, org_id, application_id, user):
    from app.models.application_owner import ApplicationOwner

    row = ApplicationOwner(
        application_id=application_id, user_id=user.id, organization_id=org_id, ownership_type="primary"
    )
    db_session.add(row)
    db_session.flush()
    return row


def _estate(db_session, org_id):
    """Four applications: two owned, three with a lifecycle, one with a cost,
    three connected. Plus one Goal (motivation) with nothing recorded about it."""
    owner = _user(db_session, org_id)
    apps = [
        _element(db_session, org_id, "App1", recorded_days_ago=3),
        _element(db_session, org_id, "App2", recorded_days_ago=10),
        _element(db_session, org_id, "App3", recorded_days_ago=20),
        _element(db_session, org_id, "App4", undated=True),
    ]
    comps = [
        _component(db_session, org_id, apps[0], lifecycle="operational", cost=1000),
        _component(db_session, org_id, apps[1], lifecycle="operational"),
        _component(db_session, org_id, apps[2], lifecycle="deprecated"),
        _component(db_session, org_id, apps[3]),
    ]
    _owner(db_session, org_id, comps[0].id, owner)
    _owner(db_session, org_id, comps[1].id, owner)
    _link(db_session, org_id, apps[0], apps[1])
    _link(db_session, org_id, apps[1], apps[2])
    goal = _element(db_session, org_id, "Goal1", type_="Goal", layer="motivation", recorded_days_ago=5)
    db_session.commit()
    return apps, goal, owner


def _area(trust, key):
    return next(a for a in trust["areas"] if a["area"] == key)


# --- the resolver ----------------------------------------------------------------


def test_coverage_states_every_figure_with_its_denominator(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-denominators")
    _estate(db_session, org.id)

    area = _area(model_trust(org.id), "application")

    assert area["state"] == "recorded"
    assert area["elements_total"] == 4
    assert area["coverage"]["owner"] == {"have": 2, "of": 4, "text": "2 of 4 owned"}
    assert area["coverage"]["lifecycle"] == {"have": 3, "of": 4, "text": "3 of 4 have a lifecycle"}
    assert area["coverage"]["cost"] == {"have": 1, "of": 4, "text": "1 of 4 have a cost"}
    assert area["coverage"]["relationship"] == {"have": 3, "of": 4, "text": "3 of 4 are connected"}


def test_a_measure_a_kind_of_element_does_not_carry_is_not_counted_against_it(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-not-carried")
    _estate(db_session, org.id)

    motivation = _area(model_trust(org.id), "motivation")

    assert motivation["elements_total"] == 1
    assert motivation["coverage"]["owner"] is None
    assert motivation["coverage"]["lifecycle"] is None
    assert motivation["coverage"]["cost"] is None
    assert motivation["coverage"]["relationship"] == {"have": 0, "of": 1, "text": "0 of 1 are connected"}


def test_an_area_with_no_elements_says_nothing_recorded_and_never_zero_percent(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-nothing")
    _estate(db_session, org.id)

    technology = _area(model_trust(org.id), "technology")

    assert technology["state"] == "nothing_recorded"
    assert technology["coverage"] is None and technology["freshness"] is None
    assert technology["line"] == "From the technology layer: nothing recorded"
    assert "%" not in technology["line"]


def test_every_layer_is_an_area_even_when_empty(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import LAYERS, model_trust

    org = make_org("trust-layers")
    trust = model_trust(org.id)

    keys = {a["area"] for a in trust["areas"] if a["kind"] == "layer"}
    assert keys == set(LAYERS)
    assert all(a["state"] == "nothing_recorded" for a in trust["areas"])


def test_every_element_type_present_is_an_area_of_its_own(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-types")
    _estate(db_session, org.id)

    trust = model_trust(org.id)
    types = {a["area"]: a for a in trust["areas"] if a["kind"] == "type"}

    assert set(types) == {"ApplicationComponent", "Goal"}
    assert types["ApplicationComponent"]["coverage"]["owner"]["text"] == "2 of 4 owned"


def test_freshness_is_the_newest_and_the_median_last_change(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-fresh")
    _estate(db_session, org.id)

    freshness = _area(model_trust(org.id), "application")["freshness"]

    # three of the four elements carry a change date: 3, 10 and 20 days ago
    assert freshness["dated"] == 3 and freshness["of"] == 4
    assert freshness["newest_days_ago"] == 3
    assert freshness["median_days_ago"] == 10
    assert freshness["text"] == "last change 3 days ago"


def test_the_event_log_counts_as_a_change_when_it_is_newer(app, db_session, make_org):
    from app.models.event_log import EventLogRecord
    from app.modules.intelligence.services.model_trust import model_trust
    from app.services.event_log_service import ensure_future_partitions

    org = make_org("trust-eventlog")
    apps, _goal, _owner_user = _estate(db_session, org.id)
    ensure_future_partitions()
    db_session.add(
        EventLogRecord(
            organization_id=org.id,
            event_type="archimate_element.updated",
            event_id=str(uuid.uuid4()),
            payload_json={"action": "updated", "id": apps[2].id},
            ordinal=1,
            entity_type="archimate_element",
            entity_id=apps[2].id,
            created_at=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1),
        )
    )
    db_session.commit()

    freshness = _area(model_trust(org.id), "application")["freshness"]

    assert freshness["newest_days_ago"] == 1
    assert freshness["text"] == "last change 1 day ago"


def test_the_line_reads_as_the_envelope_line(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-line")
    _estate(db_session, org.id)

    area = _area(model_trust(org.id), "application")

    assert area["line"] == "From the application layer: 2 of 4 owned, last change 3 days ago"


def test_no_figure_is_a_score_or_a_grade(app, db_session, make_org):
    import json

    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-noscore")
    _estate(db_session, org.id)

    text = json.dumps(model_trust(org.id), default=str).lower()

    for word in ("score", "grade", "health", "percent", "weight"):
        assert word not in text


def test_one_organisation_never_counts_another_organisations_model(app, db_session, make_org):
    from app.modules.intelligence.services.model_trust import model_trust

    org_a = make_org("trust-iso-a")
    org_b = make_org("trust-iso-b")
    _estate(db_session, org_a.id)
    other = _element(db_session, org_b.id, "B1", recorded_days_ago=1)
    _element(db_session, org_b.id, "B2", recorded_days_ago=2)
    _link(db_session, org_b.id, other, other)
    db_session.commit()

    a = _area(model_trust(org_a.id), "application")
    b = _area(model_trust(org_b.id), "application")

    assert a["elements_total"] == 4 and a["coverage"]["owner"]["have"] == 2
    assert b["elements_total"] == 2 and b["coverage"]["owner"]["have"] == 0
    assert _area(model_trust(org_b.id), "motivation")["state"] == "nothing_recorded"


def test_no_organisation_gives_nothing(app, db_session):
    from app.modules.intelligence.services.model_trust import model_trust

    assert model_trust(None)["areas"] == []


# --- the read API ----------------------------------------------------------------


def test_the_api_returns_the_resolver_figures_for_the_callers_organisation_only(
    app, db_session, make_org, client, login_as
):
    from app.modules.intelligence.services.model_trust import model_trust

    org_a = make_org("trust-api-a")
    org_b = make_org("trust-api-b")
    _estate(db_session, org_a.id)
    _element(db_session, org_b.id, "B1")
    db_session.commit()
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    db_session.commit()
    expected_line = _area(model_trust(org_a.id), "application")["line"]

    login_as(client, user_a)
    resp_a = client.get("/api/v1/intelligence/trust")
    login_as(client, user_b)
    resp_b = client.get("/api/v1/intelligence/trust")

    assert resp_a.status_code == 200 and resp_b.status_code == 200
    data_a = resp_a.get_json()["data"]
    data_b = resp_b.get_json()["data"]
    assert _area(data_a, "application")["line"] == expected_line
    assert _area(data_a, "application")["elements_total"] == 4
    assert _area(data_b, "application")["elements_total"] == 1
    assert _area(data_b, "application")["coverage"]["owner"]["have"] == 0


def test_the_api_serves_one_area_and_refuses_an_unknown_one(app, db_session, make_org, client, login_as):
    org = make_org("trust-api-area")
    _estate(db_session, org.id)
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    one = client.get("/api/v1/intelligence/trust?area=application")
    unknown = client.get("/api/v1/intelligence/trust?area=not-an-area")

    assert one.status_code == 200
    assert [a["area"] for a in one.get_json()["data"]["areas"]] == ["application"]
    assert unknown.status_code == 400


def test_the_api_needs_a_signed_in_user(app, client):
    assert client.get("/api/v1/intelligence/trust").status_code in (301, 302, 401)


# --- the assistant connection ----------------------------------------------------


def test_get_model_trust_is_a_read_only_tool_on_the_connection():
    from app.modules.ai_chat.tools.route_read_tools import connector_tools, resolve_connector_tool

    names = {t["name"] for t in connector_tools()}

    assert "get_model_trust" in names
    assert resolve_connector_tool("get_model_trust") == "get_model_trust"
    tool = next(t for t in connector_tools() if t["name"] == "get_model_trust")
    assert tool["annotations"] == {"readOnlyHint": True}
    assert tool["inputSchema"]["properties"]["area"]["type"] == "string"
    assert "required" not in tool["inputSchema"] or "area" not in tool["inputSchema"]["required"]


# --- the answer envelope ---------------------------------------------------------


def test_an_impact_answer_carries_the_trust_line_of_the_area_it_is_drawn_from(
    app, db_session, make_org, client, login_as
):
    from app.modules.intelligence.services.model_trust import model_trust

    org = make_org("trust-envelope")
    apps, _goal, _owner_user = _estate(db_session, org.id)
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    resp = client.post("/api/v1/intelligence/answers", json={"question": "impact", "element_id": apps[0].id})

    assert resp.status_code == 201
    data = resp.get_json()["data"]
    trust = data["freshness"]["model_trust"]
    assert [t["area"] for t in trust] == ["application"]
    assert trust[0]["line"] == _area(model_trust(org.id), "application")["line"]
    assert trust[0]["line"] == "From the application layer: 2 of 4 owned, last change 3 days ago"

    page = client.get(data["page_url"])
    assert page.status_code == 200
    assert trust[0]["line"] in page.get_data(as_text=True)


def test_a_set_answer_carries_one_line_per_area_of_its_elements(app, db_session, make_org, client, login_as):
    org = make_org("trust-envelope-set")
    apps, goal, _owner_user = _estate(db_session, org.id)
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    resp = client.post(
        "/api/v1/intelligence/answers", json={"question": "set_impact", "element_ids": [apps[0].id, goal.id]}
    )

    assert resp.status_code == 201
    trust = resp.get_json()["data"]["freshness"]["model_trust"]
    assert sorted(t["area"] for t in trust) == ["application", "motivation"]


def test_an_answer_with_no_rows_still_carries_the_area_trust(app, db_session, make_org, client, login_as):
    org = make_org("trust-envelope-empty")
    apps, _goal, _owner_user = _estate(db_session, org.id)
    lonely = _element(db_session, org.id, "Lonely", type_="Node", layer="technology")
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    resp = client.post("/api/v1/intelligence/answers", json={"question": "impact", "element_id": lonely.id})

    assert resp.status_code == 201
    trust = resp.get_json()["data"]["freshness"]["model_trust"]
    assert trust[0]["area"] == "technology" and trust[0]["state"] == "recorded"


# --- the Catalogue index ---------------------------------------------------------


def test_the_catalogue_index_shows_coverage_and_freshness_for_every_area(
    app, db_session, make_org, client, login_as
):
    from app.modules.intelligence.services.model_trust import LAYERS

    org = make_org("trust-index")
    _estate(db_session, org.id)
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    resp = client.get("/architecture/elements")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "From the application layer: 2 of 4 owned, last change 3 days ago" in html
    assert "From the technology layer: nothing recorded" in html
    for layer in LAYERS:
        assert f'data-trust-area="{layer}"' in html
    assert 'data-trust-area="ApplicationComponent"' in html
    assert "3 of 4 have a lifecycle" in html


# --- the three surfaces agree ----------------------------------------------------


def test_the_index_the_envelope_and_the_api_report_the_same_figures(
    app, db_session, make_org, client, login_as
):
    import re

    org = make_org("trust-agree")
    apps, _goal, _owner_user = _estate(db_session, org.id)
    user = _user(db_session, org.id)
    db_session.commit()
    login_as(client, user)

    api = _area(client.get("/api/v1/intelligence/trust").get_json()["data"], "application")
    envelope = client.post(
        "/api/v1/intelligence/answers", json={"question": "impact", "element_id": apps[0].id}
    ).get_json()["data"]["freshness"]["model_trust"][0]
    html = client.get("/architecture/elements").get_data(as_text=True)
    block = re.search(r'data-trust-area="application".*?</tr>|data-trust-area="application".*?</li>', html, re.S)

    assert envelope["line"] == api["line"]
    assert envelope["coverage"] == api["coverage"]
    assert envelope["freshness"] == api["freshness"]
    assert block is not None
    for measure in ("owner", "lifecycle", "cost", "relationship"):
        assert api["coverage"][measure]["text"] in block.group(0)
    assert api["line"] in html
