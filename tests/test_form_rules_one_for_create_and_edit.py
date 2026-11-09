"""One set of rules for the Create and Edit forms: a gap is rated the same way created or edited, a choice
left unchosen is saved as the type's default or cleared, a value a record cannot hold is refused by its
field label with the limit, a record held only as an architecture element offers only what it can save,
and a record page shows the form's label and words."""

from __future__ import annotations

import json
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _editor(db_session, make_org, client, login_as, label):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    login_as(client, _user(db_session, org, label.replace("-", ""), role="enterprise_architect"))
    return org


def _opened(client, url):
    html = client.get(url).get_data(as_text=True)
    found = re.search(r"__APP_CONFIG__\.editingElement\s*=\s*(\{.*?\});", html, re.S)
    assert found, url
    return json.loads(found.group(1))


def _gap(rating):
    return {"name": "Gap at %s" % rating, "description": "d", "priority": rating, "resolution_status": "",
            "architecture_state": ""}


@pytest.mark.parametrize("rating", ["critical", "high", "medium", "low"])
def test_a_gap_created_at_a_rating_is_saved_and_shown_at_that_rating(db_session, make_org, client, login_as, rating):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import effective_severity

    _editor(db_session, make_org, client, login_as, "gap-create-%s" % rating)
    created = client.post("/architecture/implementation/Gap/new", json=_gap(rating))
    assert created.status_code == 200, created.get_json()
    gap = Gap.query.filter_by(id=created.get_json()["id"]).one()

    assert gap.priority == rating
    assert effective_severity(gap) == rating
    assert gap.severity == rating
    # A status left unchosen is the record type's documented default, and the record page says so.
    assert gap.resolution_status == "identified"
    page = client.get("/architecture/implementation/Gap/%d" % gap.id).get_data(as_text=True)
    assert "Rating (sets priority and severity)" in page and rating.capitalize() in page
    assert "Not set" not in page


def test_creating_and_editing_a_gap_rate_it_by_the_same_helper(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    _editor(db_session, make_org, client, login_as, "gap-same-rule")
    created = client.post("/architecture/implementation/Gap/new", json=_gap("low"))
    gap_id = created.get_json()["id"]
    assert client.post("/architecture/implementation/Gap/%d/edit" % gap_id,
                       json={"name": "Gap at low", "priority": "high"}).status_code == 200
    db_session.expire_all()
    gap = Gap.query.filter_by(id=gap_id).one()
    assert (gap.priority, gap.severity) == ("high", "high")


def test_a_gap_rating_must_be_one_the_form_offers(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "gap-bad-rating")
    reply = client.post("/architecture/implementation/Gap/new", json=_gap("urgent"))
    assert reply.status_code == 400
    assert reply.get_json()["error"].startswith("Rating (sets priority and severity) must be one of")


def test_a_record_held_only_as_an_architecture_element_offers_only_what_it_can_save(db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateElement

    org = _editor(db_session, make_org, client, login_as, "ae-only")
    element = ArchiMateElement(organization_id=org.id, name="Held as element", type="Node", layer="Technology")
    db_session.add(element)
    db_session.flush()
    url = "/architecture/technology/Node/%d/edit" % element.id

    assert _opened(client, url)["saveable_fields"] == ["architecture_state"]
    reply = client.post(url, json={"name": "Held as element", "description": "", "datacenter": "DC1"})
    assert reply.status_code == 400
    assert reply.get_json()["error"] == "Location / Data Centre cannot be saved on this record"
    assert "datacenter" not in reply.get_json()["error"]


def test_every_select_has_one_empty_choice_and_a_field_that_cannot_be_empty_offers_none():
    import sqlalchemy as sa

    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY
    from app.modules.architecture.routes.archimate_crud.services.field_configs import ELEMENT_TYPE_CONFIGS

    for element_type, config in ELEMENT_TYPE_CONFIGS.items():
        for spec in config.fields:
            assert all(o.value != "" for o in spec.options), (element_type, spec.name)
            column = sa.inspect(MODEL_REGISTRY[element_type]).columns.get(spec.name) if element_type in MODEL_REGISTRY else None
            if spec.field_type == "select" and spec.allow_empty and column is not None:
                assert column.nullable, "%s.%s cannot be empty yet offers Not set" % (element_type, spec.name)
    state = [f for f in ELEMENT_TYPE_CONFIGS["Node"].to_dict()["fields"] if f["name"] == "architecture_state"][0]
    assert [o["label"] for o in state["options"]] == ["As-Is (baseline)", "To-Be (target)", "Transition"]
    assert state["allow_empty"] is True


def test_choosing_not_set_clears_a_state_and_a_choice_and_reopens_on_not_set(db_session, make_org, client, login_as):
    from app.models.business_layer import BusinessActor

    _editor(db_session, make_org, client, login_as, "not-set")
    created = client.post("/architecture/business/BusinessActor/new",
                          json={"name": "Actor", "actor_type": "Team", "architecture_state": "Target"})
    actor_id = created.get_json()["id"]
    url = "/architecture/business/BusinessActor/%d/edit" % actor_id
    assert _opened(client, url)["architecture_state"] == "Target"

    assert client.post(url, json={"name": "Actor", "actor_type": "", "architecture_state": ""}).status_code == 200
    db_session.expire_all()
    opened = _opened(client, url)
    assert opened["architecture_state"] == ""
    assert opened.get("actor_type") in (None, "")
    assert BusinessActor.query.filter_by(id=actor_id).one().actor_type is None
    # A save that does not send the state leaves it alone.
    client.post(url, json={"name": "Actor", "architecture_state": "Baseline"})
    client.post(url, json={"name": "Actor renamed"})
    db_session.expire_all()
    assert _opened(client, url)["architecture_state"] == "Baseline"


def test_a_gap_status_chosen_blank_on_edit_is_left_as_it_was(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    _editor(db_session, make_org, client, login_as, "gap-blank-edit")
    gap_id = client.post("/architecture/implementation/Gap/new", json=_gap("high")).get_json()["id"]
    assert client.post("/architecture/implementation/Gap/%d/edit" % gap_id,
                       json={"name": "Gap", "priority": "", "resolution_status": ""}).status_code == 200
    db_session.expire_all()
    gap = Gap.query.filter_by(id=gap_id).one()
    assert (gap.priority, gap.resolution_status) == ("high", "identified")


def test_a_principle_is_created_with_its_statement_and_refused_without(db_session, make_org, client, login_as):
    from app.models.models import Principle

    _editor(db_session, make_org, client, login_as, "principle-create")
    refused = client.post("/architecture/motivation/Principle/new", json={"name": "Reuse first", "statement": ""})
    assert refused.status_code == 400 and refused.get_json()["error"] == "Statement is required"
    created = client.post("/architecture/motivation/Principle/new",
                          json={"name": "Reuse first", "statement": "Reuse before buying", "category": ""})
    assert created.status_code == 200, created.get_json()
    row = Principle.query.filter_by(id=created.get_json()["id"]).one()
    assert (row.name, row.statement) == ("Reuse first", "Reuse before buying")
    config = client.get("/architecture/api/field-configs").get_json()["Principle"]["fields"]
    assert [f["required"] for f in config if f["name"] == "statement"] == [True]


def test_a_business_function_is_created_under_a_capability_of_the_organisation(db_session, make_org, client, login_as):
    from app.models.business_capabilities import BusinessFunction
    from app.models.unified_capability import UnifiedCapability

    org = _editor(db_session, make_org, client, login_as, "function-create")
    other = make_org("function-other")
    mine = UnifiedCapability(organization_id=org.id, name="Mine %s" % uuid.uuid4().hex[:6], scope="tenant", level=1)
    theirs = UnifiedCapability(organization_id=other.id, name="Theirs %s" % uuid.uuid4().hex[:6], scope="tenant", level=1)
    db_session.add_all([mine, theirs])
    db_session.flush()

    config = client.get("/architecture/api/field-configs").get_json()["BusinessFunction"]["fields"]
    assert [(f["label"], f["required"]) for f in config if f["name"] == "capability_id"] == [("Capability", True)]
    assert client.post("/architecture/business/BusinessFunction/new", json={"name": "Fn"}).get_json()["error"] == "Capability is required"
    foreign = client.post("/architecture/business/BusinessFunction/new", json={"name": "Fn", "capability_id": str(theirs.id)})
    assert foreign.status_code == 400 and foreign.get_json()["error"] == "Capability is required"
    created = client.post("/architecture/business/BusinessFunction/new", json={"name": "Fn", "capability_id": str(mine.id)})
    assert created.status_code == 200, created.get_json()
    assert BusinessFunction.query.filter_by(id=created.get_json()["id"]).one().capability_id == mine.id

    choices = client.get("/architecture/api/capability-choices?q=" + mine.name.split()[0]).get_json()
    names = [c["label"] for c in choices]
    assert mine.name in names and theirs.name not in names


def test_the_new_gap_page_opens_the_gap_form_on_the_implementation_tab(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "new-gap-page")
    html = client.get("/architecture/implementation/Gap/new").get_data(as_text=True)
    assert '__APP_CONFIG__.createElementType = "Gap"' in html
    assert '__APP_CONFIG__.initialLayer = "implementation"' in html


def test_a_value_the_record_cannot_hold_is_refused_by_label_with_the_limit(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "limits")
    long_domain = client.post("/architecture/business/ValueStream/new", json={"name": "Stream", "industry_domain": "x" * 100})
    assert long_domain.status_code == 400
    assert long_domain.get_json()["error"] == "Domain is at most 50 characters"
    for bad in ("99999999999", "NaN", "Infinity", "-inf", "ten"):
        reply = client.post("/architecture/technology/CommunicationNetwork/new",
                            json={"name": "Network", "bandwidth_gbps": bad})
        assert reply.status_code == 400, bad
        error = reply.get_json()["error"]
        assert error.startswith("Bandwidth (Gbps) must be a number"), (bad, error)
    assert client.post("/architecture/technology/CommunicationNetwork/new",
                       json={"name": "Network", "bandwidth_gbps": "99999999999"}).get_json()["error"] == \
        "Bandwidth (Gbps) must be a number up to 99,999,999.99"


def test_a_record_page_shows_the_forms_label_and_the_forms_words_for_a_choice(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "record-wording")
    created = client.post("/architecture/strategy/Capability/new",
                          json={"name": "Cap", "current_maturity_level": "3"})
    assert created.status_code == 200, created.get_json()
    page = client.get("/architecture/strategy/Capability/%d" % created.get_json()["id"]).get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", page)
    text = re.sub(r"\s+", " ", text)
    assert "Maturity Level 3 – Defined" in text
    assert "Current maturity level" not in text


def test_a_listed_record_is_opened_by_its_own_id_and_its_page_shows_what_the_form_saved(
        db_session, make_org, client, login_as):
    """The list addresses a record kept in its own table by that record's id, not its architecture element's."""
    from app.models.models import Principle

    _editor(db_session, make_org, client, login_as, "listed-record")
    created = client.post("/architecture/motivation/Principle/new",
                          json={"name": "Listed principle", "statement": "Reuse before buying"})
    record_id = created.get_json()["id"]
    assert Principle.query.filter_by(id=record_id).one().archimate_element_id is not None

    listed = client.get("/architecture/api/layer/motivation/elements?element_type=Principle&per_page=100").get_json()
    entry = [e for e in listed["elements"] if e["name"] == "Listed principle"]
    assert len(entry) == 1
    assert entry[0]["record_id"] == record_id and entry[0]["id"] != record_id
    assert "saveable_fields" not in entry[0]

    page = client.get("/architecture/motivation/Principle/%d" % record_id).get_data(as_text=True)
    assert "Reuse before buying" in page


def test_a_gap_record_page_shows_one_rating_row_and_it_is_the_effective_rating(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    org = _editor(db_session, make_org, client, login_as, "gap-one-rating-row")
    gap = Gap(organization_id=org.id, name="Medium priority critical severity", gap_type="coverage",
              priority="medium", severity="critical")
    db_session.add(gap)
    db_session.flush()

    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get("/architecture/implementation/Gap/%d" % gap.id)
                                       .get_data(as_text=True)))
    assert text.count("Rating (sets priority and severity)") == 1
    assert "Rating (sets priority and severity) Critical" in text
