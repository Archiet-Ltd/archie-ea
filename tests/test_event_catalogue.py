"""The event catalogue: completeness, producer refusal and the published endpoints (R1-B28)."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from app.services import event_catalogue
from tests._webhook_helpers import install_guards, make_org_user

REPO = pathlib.Path(__file__).resolve().parent.parent
EVENT_MODEL = pathlib.Path(
    r"C:\sdlc-orchestrator\docs\buckets"
    r"\entelim-enterprise-intelligence-management-platform-design-the-f\event-model-v1.yaml"
)

PRODUCT_TYPES = {
    "archimate_element.created",
    "archimate_element.updated",
    "archimate_element.deleted",
    "archimate_relationship.created",
    "archimate_relationship.updated",
    "archimate_relationship.deleted",
    "webhook.test",
}


@pytest.fixture(autouse=True)
def _guards(app, _schema):
    install_guards(app)


# --------------------------------------------------------------------------- #
# The data file
# --------------------------------------------------------------------------- #


def test_one_entry_per_event_model_message():
    catalogue = event_catalogue.get_catalogue()
    assert catalogue["event_model_count"] == 1352
    assert len(catalogue["events"]) == catalogue["event_model_count"]
    types = [e["type"] for e in catalogue["events"]]
    assert len(set(types)) == len(types), "an event type appears twice"
    assert types == sorted(types)


def test_every_entry_has_type_version_id_and_a_resolvable_schema():
    catalogue = event_catalogue.get_catalogue()
    for entry in catalogue["events"]:
        assert entry["type"] and entry["version"] == "1.0.0"
        assert entry["id"].startswith("EVT-")
        assert entry["schema"] in catalogue["schemas"], entry["type"]
        resolved = event_catalogue.get_event(entry["type"])
        assert isinstance(resolved["schema"], dict)
    assert catalogue["envelope"]["type"] == "object"


def test_every_product_event_type_is_present():
    for event_type in PRODUCT_TYPES:
        assert event_catalogue.is_known(event_type), event_type
    for entry in event_catalogue.get_catalogue()["product_events"]:
        assert "maps_to" in entry and entry["note"]


@pytest.mark.skipif(not EVENT_MODEL.exists(), reason="the event model is not on this machine")
def test_the_committed_file_is_what_the_generator_writes(tmp_path):
    out = tmp_path / "catalogue.json"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "gen_event_catalogue.py"),
            str(EVENT_MODEL),
            str(out),
        ],
        check=True,
        capture_output=True,
        cwd=str(REPO),
    )
    committed = REPO / "app" / "seed_data" / "event_catalogue" / "catalogue-v1.json"
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(
        committed.read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------- #
# validate() and matches()
# --------------------------------------------------------------------------- #


def test_validate_accepts_a_valid_product_payload():
    event_catalogue.validate(
        "archimate_element.created", {"action": "created", "id": 7, "name": "x"}
    )
    event_catalogue.validate("archimate_element.deleted", {"action": "deleted", "id": None})


def test_unknown_type_raises_a_named_error():
    with pytest.raises(event_catalogue.UnknownEventType) as caught:
        event_catalogue.validate("no.such.event", {})
    assert caught.value.event_type == "no.such.event"
    assert isinstance(caught.value, event_catalogue.EventCatalogueError)
    assert caught.value.code == "event_type_unknown"


def test_missing_required_field_raises_an_error_naming_type_and_field():
    with pytest.raises(event_catalogue.EventSchemaError) as caught:
        event_catalogue.validate("archimate_element.created", {"action": "created"})
    assert caught.value.event_type == "archimate_element.created"
    assert "id" in caught.value.detail
    assert caught.value.code == "event_schema_invalid"


def test_wrong_type_reports_the_path():
    with pytest.raises(event_catalogue.EventSchemaError) as caught:
        event_catalogue.validate("archimate_element.created", {"action": 5, "id": 1})
    assert caught.value.path == "$.action"


def test_an_event_model_type_is_validated_against_its_schema():
    catalogue = event_catalogue.get_catalogue()
    for entry in catalogue["events"]:
        schema = event_catalogue.get_event(entry["type"])["schema"]
        if schema.get("required"):
            with pytest.raises(event_catalogue.EventSchemaError) as caught:
                event_catalogue.validate(entry["type"], {})
            assert caught.value.event_type == entry["type"]
            return
    pytest.fail("no event-model schema has a required field to test with")


@pytest.mark.parametrize(
    "pattern,event_type,expected",
    [
        ("*", "anything.at.all", True),
        ("archimate_element.created", "archimate_element.created", True),
        ("archimate_element.created", "archimate_element.updated", False),
        ("archimate_element.*", "archimate_element.deleted", True),
        ("archimate_element.*", "archimate_elements.deleted", False),
        ("archimate_element.*", "archimate_relationship.created", False),
        ("", "archimate_element.created", False),
    ],
)
def test_matches(pattern, event_type, expected):
    assert event_catalogue.matches(pattern, event_type) is expected


# --------------------------------------------------------------------------- #
# Producers: emit_event
# --------------------------------------------------------------------------- #


def _outbox_count(org):
    """Outbox rows of one organisation (the filter is explicit: other tests commit rows of their own)."""
    from app.models.transformation_execution import OperationOutboxEvent

    return OperationOutboxEvent.query.filter_by(organization_id=org.id).count()


def test_emit_event_refuses_an_unknown_type_and_writes_no_row(db_session, make_org):
    from app.services.outbox import emit_event

    org = make_org("cat")
    before = _outbox_count(org)
    with pytest.raises(event_catalogue.UnknownEventType):
        emit_event(organization_id=org.id, event_type="not.catalogued", payload={})
    db_session.flush()
    assert _outbox_count(org) == before


def test_emit_event_refuses_a_bad_payload_and_writes_no_row(db_session, make_org):
    from app.services.outbox import emit_event

    org = make_org("cat")
    before = _outbox_count(org)
    with pytest.raises(event_catalogue.EventSchemaError) as caught:
        emit_event(
            organization_id=org.id, event_type="archimate_element.created", payload={"id": 1}
        )
    assert caught.value.event_type == "archimate_element.created"
    assert "action" in caught.value.detail
    db_session.flush()
    assert _outbox_count(org) == before


def test_saving_an_element_still_succeeds_when_validation_would_fail(
    db_session, make_org, monkeypatch, caplog
):
    from app.models.archimate_core import ArchiMateElement

    def refuse(event_type, payload):
        raise event_catalogue.EventSchemaError(event_type, "$", "refused for the test")

    monkeypatch.setattr(event_catalogue, "validate", refuse)
    org = make_org("cat")
    before = _outbox_count(org)
    element = ArchiMateElement(
        name="Saved anyway",
        type="ApplicationComponent",
        layer="Application",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.commit()
    assert element.id is not None
    assert ArchiMateElement.query.filter_by(id=element.id).count() == 1
    assert _outbox_count(org) == before


def test_saving_an_element_still_emits_its_event(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.transformation_execution import OperationOutboxEvent

    org = make_org("cat")
    element = ArchiMateElement(
        name="Emits", type="ApplicationComponent", layer="Application", organization_id=org.id
    )
    db_session.add(element)
    db_session.commit()
    rows = OperationOutboxEvent.query.filter_by(
        organization_id=org.id, event_type="archimate_element.created"
    ).all()
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# The published endpoints and the publish route
# --------------------------------------------------------------------------- #


def test_catalogue_endpoints_need_a_signed_in_user(client):
    assert client.get("/api/webhooks/catalogue").status_code in (302, 401)
    assert client.get("/api/webhooks/catalogue/archimate_element.created").status_code in (302, 401)


def test_catalogue_endpoint_lists_every_event(app, db_session, make_org, client, login_as):
    user = make_org_user(db_session, make_org("cat"))
    login_as(client, user)
    response = client.get("/api/webhooks/catalogue")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["catalogue_version"] == "1.0.0"
    assert len(data["events"]) == 1352 + len(PRODUCT_TYPES)
    assert all("schema" in e and "version" in e for e in data["events"])


def test_catalogue_entry_endpoint(app, db_session, make_org, client, login_as):
    user = make_org_user(db_session, make_org("cat"))
    login_as(client, user)
    found = client.get("/api/webhooks/catalogue/archimate_element.created")
    assert found.status_code == 200
    assert found.get_json()["data"]["type"] == "archimate_element.created"
    login_as(client, user)
    assert client.get("/api/webhooks/catalogue/no.such.event").status_code == 404


def _publish(client, login_as, user, body):
    login_as(client, user)
    return client.post("/api/webhooks/public/events", json=body)


def test_publish_with_a_bad_payload_answers_400_and_creates_nothing(
    app, db_session, make_org, client, login_as
):
    from app.models.webhook import WebhookEvent

    org = make_org("cat")
    user = make_org_user(db_session, org)
    before = _outbox_count(org)
    response = _publish(
        client, login_as, user, {"event_type": "archimate_element.created", "payload": {"id": 1}}
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "event_schema_invalid"
    assert body["event_type"] == "archimate_element.created"
    assert "action" in body["detail"]
    assert _outbox_count(org) == before
    assert WebhookEvent.query.count() == 0


def test_publish_with_an_unknown_type_answers_400(app, db_session, make_org, client, login_as):
    org = make_org("cat")
    user = make_org_user(db_session, org)
    before = _outbox_count(org)
    response = _publish(client, login_as, user, {"event_type": "made.up.type", "payload": {}})
    assert response.status_code == 400
    assert response.get_json()["error"] == "event_type_unknown"
    assert _outbox_count(org) == before


def test_publish_with_a_good_payload_creates_one_outbox_row_for_the_callers_organisation(
    app, db_session, make_org, client, login_as
):
    from app.models.transformation_execution import OperationOutboxEvent
    from app.models.webhook import WebhookEvent

    org = make_org("cat")
    other = make_org("other")
    user = make_org_user(db_session, org)
    response = _publish(
        client,
        login_as,
        user,
        {"event_type": "archimate_element.updated", "payload": {"action": "updated", "id": 3}},
    )
    assert response.status_code == 201
    rows = OperationOutboxEvent.query.filter_by(event_type="archimate_element.updated").all()
    assert [r.organization_id for r in rows] == [org.id]
    assert OperationOutboxEvent.query.filter_by(organization_id=other.id).count() == 0
    assert WebhookEvent.query.count() == 0
