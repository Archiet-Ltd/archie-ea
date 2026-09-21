"""Imported assessments with real models, followed by separate durability cases.

The ordinary cases use the shared rollback fixture. Tests whose names start
with test_durable use only the existing committed concurrency fixture.
"""

import copy
import json
import multiprocessing
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from queue import Queue
from xml.sax.saxutils import escape

import pytest
from sqlalchemy import event, select, text

from test_concurrent_application_create import (
    concurrency_database_guard as concurrency_database_guard,
    independent_application_race as independent_application_race,
)


_OMITTED = object()
_RATIONALE = "Fictional unassessed input: the calibration service has no recorded assessment."


def _rationale(health):
    return {
        None: _RATIONALE,
        "healthy": "Fictional healthy assessment: calibration supports the required release process.",
        "at_risk": "Fictional at-risk assessment: single-site calibration needs continuity review.",
        "critical": "Fictional critical assessment: fragile release control needs urgent dependency review.",
    }[health]


def _json_scalar(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def _stored(model, organization_id, row_id=None):
    from app import db

    table = model.__table__
    query = select(*table.columns).where(table.c.organization_id == organization_id).order_by(table.c.id)
    if row_id is not None:
        query = query.where(table.c.id == row_id)
    rows = db.session.connection().execute(query).mappings().all()
    return json.loads(json.dumps([dict(row) for row in rows], default=_json_scalar, allow_nan=False))


def _count(model, organization_id):
    from app import db
    from sqlalchemy import func

    table = model.__table__
    return db.session.connection().execute(select(func.count()).select_from(table).where(
        table.c.organization_id == organization_id
    )).scalar_one()


def _settings(organization_id):
    from app import db
    from app.models.organization import Organization

    return copy.deepcopy(db.session.connection().execute(select(Organization.__table__.c.settings).where(
        Organization.__table__.c.id == organization_id
    )).scalar_one())


def _save_settings(organization_id, settings):
    from app import db
    from app.models.organization import Organization

    organization = db.session.execute(select(Organization).where(
        Organization.id == organization_id
    ).execution_options(populate_existing=True)).scalar_one()
    organization.settings = copy.deepcopy(settings)


def _normal_database():
    from app import db
    from app.models.application_layer import ApplicationComponent as Alias
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.models import ArchiMateElement as NormalElement

    assert os.environ.get("APP_FAST_INIT", "0") != "1"
    assert db.engine.dialect.name == "postgresql"
    assert db.session.connection().get_isolation_level() == "READ COMMITTED"
    assert "deleted_at" in ArchiMateElement.__table__.c
    assert "application_component_id" in ArchiMateElement.__table__.c
    assert Alias is ApplicationComponent
    assert ArchiMateElement is NormalElement


def _prepare(organization_id, name, *, count=1, health=None):
    """Tiny real OEF import plus a separately committed maintenance intent."""
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_import_service import ArchiMateImportService

    _normal_database()
    state = dict(org_id=organization_id, token=uuid.uuid4().hex, attempt=uuid.uuid4().hex,
                 digest=uuid.uuid4().hex + uuid.uuid4().hex, keys=[f"calibration-{i}" for i in range(count)])
    elements = []
    for index, key in enumerate(state["keys"]):
        display_name = name if count == 1 else f"{name} {index}"
        elements.append(f'''<element identifier="{key}" xsi:type="ApplicationComponent">
          <name>{escape(display_name)}</name><documentation>Fictional calibration service.</documentation>
          <properties>
            <property propertyDefinitionRef="scenario"><value>lantern-quay-v1</value></property>
            <property propertyDefinitionRef="reservation"><value>{state["token"]}</value></property>
            <property propertyDefinitionRef="key"><value>{key}</value></property>
          </properties></element>''')
    xml = f'''<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="assessment-model">
      <name>Fictional calibration assessment</name><propertyDefinitions>
      <propertyDefinition identifier="scenario" type="string"><name>a05:scenario</name></propertyDefinition>
      <propertyDefinition identifier="reservation" type="string"><name>a05:reservation</name></propertyDefinition>
      <propertyDefinition identifier="key" type="string"><name>a05:key</name></propertyDefinition>
      </propertyDefinitions><elements>{"".join(elements)}</elements></model>'''
    service = ArchiMateImportService()
    parsed = service.parse_oef_xml(xml)
    assert not parsed["errors"]
    imported = service.execute_import(parsed, strategy="update_existing")
    assert imported["created"] == count
    assert imported["failed"] == 0 and not imported["errors"]
    assert imported["relationships_created"] == 0 and not imported["relationships_failed"]
    snapshots = _stored(ArchiMateElement, organization_id)
    mapped = {}
    for key in state["keys"]:
        matches = [row for row in snapshots if (row["custom_properties"] or {}).get("a05:key") == key]
        assert len(matches) == 1
        row = matches[0]
        assert row["custom_properties"]["archie:imported_at"]
        assert row["application_component_id"] is None
        mapped[key] = {"id": row["id"], "snapshot": row}
    state["elements"] = mapped
    settings = _settings(organization_id) or {}
    settings["unrelated"] = {"retain": [None, "", False, 0]}
    settings["a05_fixture"] = {
        "schema_version": 1, "organization_id": organization_id,
        "scenario": "lantern-quay-v1", "reservation_token": state["token"],
        "authorized_nonproduction": True, "catalog_digest": state["digest"],
        "permitted_sizes": [300, 3000, 10000], "persona_accounts": [],
        "manifest": {"elements": mapped, "applications": {}, "health_checkpoint": None,
                     "pending": None, "unrelated_phase": {"retain": True}},
    }
    _save_settings(organization_id, settings)
    db.session.commit()
    _intend(state, health=health, description=_rationale(health))
    return state


def _intend(state, *, health=_OMITTED, description=_OMITTED):
    from app import db

    settings = _settings(state["org_id"])
    reservation = settings["a05_fixture"]
    manifest = reservation["manifest"]
    state["attempt"] = uuid.uuid4().hex
    operations = {}
    for field, value in (("health", health), ("description", description)):
        operations[field] = {"operation": "preserve"} if value is _OMITTED else {"operation": "set", "value": value}
    pending = {}
    for key in state["keys"]:
        accepted = manifest["applications"].get(key)
        pending[key] = {
            "element_id": manifest["elements"][key]["id"],
            "expected_application_id": None if accepted is None else accepted["id"],
            "expected_snapshot": None if accepted is None else accepted["snapshot"],
            "desired": copy.deepcopy(operations),
        }
    manifest["pending"] = {
        "attempt_id": state["attempt"], "catalog_digest": reservation["catalog_digest"],
        "requested_size": 300, "phase": "health", "health": pending,
    }
    _save_settings(state["org_id"], settings)
    db.session.commit()


def _arguments(state, key=None):
    key = key or state["keys"][0]
    intent = _settings(state["org_id"])["a05_fixture"]["manifest"]["pending"]["health"][key]
    args = dict(organization_id=state["org_id"], reservation_token=state["token"],
                fixture_key=key, attempt_id=state["attempt"], archimate_element_id=intent["element_id"],
                application_id=intent["expected_application_id"], expected_snapshot=intent["expected_snapshot"])
    for field, argument in (("health", "health_status"), ("description", "description")):
        if intent["desired"][field]["operation"] == "set":
            args[argument] = intent["desired"][field]["value"]
    return args


def _write(args):
    from app.modules.applications.services.application_factory import ApplicationFactory

    return ApplicationFactory.upsert_imported_application_assessment(**args)


def _checkpoint(state, receipts):
    from app.models.application_portfolio import ApplicationComponent

    settings = _settings(state["org_id"])
    manifest = settings["a05_fixture"]["manifest"]
    assert manifest["pending"]["phase"] == "health"
    assert manifest["pending"]["attempt_id"] == state["attempt"]
    assert manifest["pending"]["catalog_digest"] == state["digest"]
    assert {receipt["fixture_key"] for receipt in receipts} == set(manifest["pending"]["health"])
    for receipt in receipts:
        key = receipt["fixture_key"]
        intent = manifest["pending"]["health"][key]
        previous = manifest["applications"].get(key)
        assert intent["expected_application_id"] == (None if previous is None else previous["id"])
        assert intent["expected_snapshot"] == (None if previous is None else previous["snapshot"])
        assert receipt["organization_id"] == state["org_id"]
        assert receipt["archimate_element_id"] == intent["element_id"]
        assert _stored(ApplicationComponent, state["org_id"], receipt["application_id"]) == [receipt["snapshot"]]
        for field, column in (("health", "health_status"), ("description", "description")):
            desired = intent["desired"][field]
            expected_value = desired["value"] if desired["operation"] == "set" else previous["snapshot"][column]
            assert receipt["snapshot"][column] == expected_value
        manifest["applications"][receipt["fixture_key"]] = {
            "id": receipt["application_id"], "element_id": receipt["archimate_element_id"],
            "snapshot": receipt["snapshot"],
        }
    manifest["health_checkpoint"] = {"attempt_id": state["attempt"], "receipts": receipts}
    manifest["pending"] = None
    _save_settings(state["org_id"], settings)


def _accepted(state):
    from app import db

    receipts = [_write(_arguments(state, key)) for key in state["keys"]]
    _checkpoint(state, receipts)
    db.session.commit()
    return receipts


def _inventory(state):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    return (_stored(ArchiMateElement, state["org_id"]),
            _stored(ApplicationComponent, state["org_id"]), _settings(state["org_id"]))


@pytest.fixture
def assessment(app, db_session, make_org, tenant_ctx):
    org_id = make_org("import-assessment").id
    contrast_id = make_org("assessment-control").id
    db_session.commit()
    with tenant_ctx(contrast_id):
        contrast = _prepare(contrast_id, "Fictional Straße Calibration", health="critical")
        _accepted(contrast)
    with tenant_ctx(org_id):
        state = _prepare(org_id, "Fictional Straße Calibration")
        state["contrast"] = contrast
        yield state


def _refuses_without_change(state, args, code=None, exception=ValueError):
    before = _inventory(state)
    contrast = _inventory(state["contrast"]) if "contrast" in state else None
    with pytest.raises(exception) as error:
        _write(args)
    if code is not None:
        assert str(error.value) == code
        if code.endswith("_conflict") or code.endswith("_mismatch") or code in {"target_type", "target_unavailable"}:
            assert error.value.code == code
    assert _inventory(state) == before
    if contrast is not None:
        assert _inventory(state["contrast"]) == contrast


def test_old_create_then_link_leaves_an_extra_mirror_and_caller_owns_rollback(assessment):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.modules.applications.services.application_factory import ApplicationFactory

    org_id = assessment["org_id"]
    target_id = assessment["elements"][assessment["keys"][0]]["id"]
    baseline = _stored(ArchiMateElement, org_id)
    baseline_count = _count(ArchiMateElement, org_id)
    assert baseline_count == 1
    row = ApplicationComponent(name="Fictional legacy calibration", organization_id=org_id)
    db.session.add(row)
    db.session.flush()
    mirror_id = row.archimate_element_id
    assert mirror_id != target_id
    ApplicationFactory.update_application_with_archimate_link(row.id, target_id)
    db.session.flush()
    assert len(_stored(ArchiMateElement, org_id)) == len(baseline) + 1
    assert _count(ArchiMateElement, org_id) == baseline_count + 1
    assert _count(ApplicationComponent, org_id) == 1
    assert row.archimate_element_id == target_id
    db.session.rollback()
    assert _stored(ArchiMateElement, org_id) == baseline
    assert _stored(ApplicationComponent, org_id) == []
    assert _count(ApplicationComponent, org_id) == 0
    db.session.commit()
    repaired = _write(_arguments(assessment))
    assert repaired["archimate_element_id"] == target_id
    assert _stored(ArchiMateElement, org_id) == baseline
    assert _count(ArchiMateElement, org_id) == baseline_count
    assert _count(ApplicationComponent, org_id) == 1


@pytest.mark.parametrize("health", ["healthy", "at_risk", "critical", None])
@pytest.mark.parametrize("repeat_operation", ["preserve", "set"])
def test_health_roundtrip_and_committed_noop_preserve_every_column(assessment, health, repeat_operation):
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.my_applications.services import application_health_status

    _intend(assessment, health=health, description=_rationale(health))
    baseline_elements = _inventory(assessment)[0]
    contrast = _inventory(assessment["contrast"])
    first = _accepted(assessment)[0]
    assert first["outcome"] == "created"
    assert first["health_status"] == health
    assert first["source_identifier"] == f'a05:v1:{assessment["org_id"]}:{assessment["token"]}:{assessment["keys"][0]}'
    assert first["data_source"] == "a05_fixture"
    for field in ("external_id", "application_code", "application_owner", "business_owner",
                  "technical_owner", "technical_lead", "product_manager", "development_team", "support_team"):
        assert first["snapshot"][field] is None
    db.session.remove()
    with tenant_scope(assessment["org_id"]):
        assert _inventory(assessment)[0] == baseline_elements
        table = ApplicationComponent.__table__
        row = db.session.execute(select(ApplicationComponent).where(
            ApplicationComponent.id == first["application_id"]
        )).scalar_one()
        assert application_health_status(row) == (health or "unknown")
        assert db.session.connection().execute(select(table.c.health_status.is_(None)).where(
            table.c.organization_id == assessment["org_id"], table.c.id == first["application_id"]
        )).scalar_one() is (health is None)
        if repeat_operation == "preserve":
            _intend(assessment)
        else:
            _intend(assessment, health=health, description=_rationale(health))
        updates = []
        def track_update(mapper, connection, target):
            updates.append(target.id)
        event.listen(ApplicationComponent, "before_update", track_update)
        try:
            repeated = _accepted(assessment)[0]
        finally:
            event.remove(ApplicationComponent, "before_update", track_update)
        assert updates == []
        assert repeated["outcome"] == "unchanged"
        assert repeated["snapshot"] == first["snapshot"]
        assert repeated["application_id"] == first["application_id"]
        assert _inventory(assessment)[0] == baseline_elements
        assert len(_inventory(assessment)[1]) == 1
        assert _inventory(assessment["contrast"]) == contrast


def test_explicit_null_and_authorized_description_change_only_allowed_columns(assessment):
    from app import db

    _intend(assessment, health="critical", description="Fictional assessment: release control needs review.")
    first = _accepted(assessment)[0]
    _intend(assessment, health=None, description=_RATIONALE)
    second = _accepted(assessment)[0]
    assert second["outcome"] == "updated"
    assert second["snapshot"]["health_status"] is None
    assert second["snapshot"]["description"] == _RATIONALE
    assert second["snapshot"]["lock_version"] == first["snapshot"]["lock_version"] + 1
    assert second["snapshot"]["updated_at"] >= first["snapshot"]["updated_at"]
    for key in first["snapshot"]:
        if key not in {"health_status", "description", "updated_at", "lock_version"}:
            assert second["snapshot"][key] == first["snapshot"][key]
    _intend(assessment, description="Fictional unassessed input: awaiting an operational review.")
    third = _write(_arguments(assessment))
    assert third["health_status"] is None
    db.session.rollback()
    assert _inventory(assessment)[1] == [second["snapshot"]]


@pytest.mark.parametrize("health", ["unknown", "Healthy", "healthy ", " healthy", "", True, False, 0, 1, [], {}])
def test_invalid_health_refuses_without_mutation(assessment, health):
    args = _arguments(assessment)
    args["health_status"] = health
    _refuses_without_change(assessment, args, "invalid_health_status")


@pytest.mark.parametrize("field,value", [
    ("organization_id", None), ("organization_id", 0), ("organization_id", -1),
    ("organization_id", True), ("organization_id", "1"), ("archimate_element_id", False),
    ("application_id", 0), ("application_id", "1"), ("reservation_token", "BAD"),
    ("attempt_id", None), ("fixture_key", "has spaces"), ("fixture_key", "é"),
    ("fixture_key", "x" * 121), ("fixture_key", ""), ("description", None),
    ("description", ""), ("description", "  "), ("description", []),
])
def test_malformed_arguments_refuse(assessment, field, value):
    args = _arguments(assessment)
    args[field] = value
    _refuses_without_change(assessment, args)


@pytest.mark.parametrize("field", ["health_status", "description"])
def test_new_omission_is_not_null_or_empty(assessment, field):
    args = _arguments(assessment)
    del args[field]
    _refuses_without_change(assessment, args, "missing_assessment_input")


@pytest.mark.parametrize("context", [None, 0, -1, True, "1", "foreign"])
def test_matching_positive_context_is_required(assessment, context):
    from flask import g

    args = _arguments(assessment)
    previous = g.current_org_id
    try:
        g.current_org_id = previous + 1 if context == "foreign" else context
        _refuses_without_change(assessment, args, "invalid_tenant_context")
    finally:
        g.current_org_id = previous


def test_app_context_is_required_before_a_transaction(app):
    from flask import has_app_context

    assert not has_app_context()
    with pytest.raises(ValueError, match="invalid_tenant_context"):
        _write(dict(organization_id=1, reservation_token=uuid.uuid4().hex,
                    fixture_key="calibration", attempt_id=uuid.uuid4().hex,
                    archimate_element_id=1, health_status=None, description=_RATIONALE))


@pytest.mark.parametrize("path,value,code", [
    (("schema_version",), True, "reservation_mismatch"),
    (("organization_id",), False, "reservation_mismatch"),
    (("scenario",), "other", "reservation_mismatch"),
    (("reservation_token",), None, "reservation_mismatch"),
    (("authorized_nonproduction",), 1, "reservation_mismatch"),
    (("catalog_digest",), "bad", "reservation_mismatch"),
    (("permitted_sizes",), [300], "reservation_mismatch"),
    (("persona_accounts",), None, "reservation_mismatch"),
    (("manifest",), None, "reservation_mismatch"),
    (("manifest", "elements"), [], "reservation_mismatch"),
    (("manifest", "applications"), None, "reservation_mismatch"),
    (("manifest", "pending"), None, "intent_mismatch"),
    (("manifest", "pending", "phase"), "import", "intent_mismatch"),
    (("manifest", "pending", "attempt_id"), None, "intent_mismatch"),
    (("manifest", "pending", "catalog_digest"), None, "intent_mismatch"),
    (("manifest", "pending", "requested_size"), 1, "intent_mismatch"),
    (("manifest", "pending", "requested_size"), 300.0, "intent_mismatch"),
    (("manifest", "pending", "health"), {}, "intent_mismatch"),
])
def test_persisted_authority_cannot_be_guessed(assessment, path, value, code):
    from app import db

    args = _arguments(assessment)
    settings = _settings(assessment["org_id"])
    node = settings["a05_fixture"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    _save_settings(assessment["org_id"], settings)
    db.session.commit()
    _refuses_without_change(assessment, args, code)


@pytest.mark.parametrize("mode", ["missing", "disabled", "dirty", "new", "deleted", "unrelated_application", "unrelated_element", "unrelated_owner"])
def test_uncommitted_session_state_and_disabled_reservation_refuse(assessment, mode):
    from app import db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization

    args = _arguments(assessment)
    organization = db.session.get(Organization, assessment["org_id"])
    if mode == "missing":
        organization.settings = {}
        db.session.commit()
    elif mode == "disabled":
        organization.is_active = False
        db.session.commit()
    elif mode == "dirty":
        organization.settings = copy.deepcopy(organization.settings)
    elif mode == "new":
        db.session.add(Organization(name="Fictional pending reservation", slug=uuid.uuid4().hex))
    elif mode == "deleted":
        db.session.delete(organization)
    elif mode == "unrelated_application":
        db.session.add(ApplicationComponent(name="Fictional pending application"))
    elif mode == "unrelated_element":
        db.session.add(ArchiMateElement(name="Fictional pending element", type="ApplicationComponent"))
    else:
        db.session.add(ApplicationOwner(application_id=1, user_id=1, organization_id=assessment["org_id"]))
    with db.session.no_autoflush:
        _refuses_without_change(assessment, args, "reservation_mismatch" if mode in {"missing", "disabled"} else "snapshot_conflict")


@pytest.mark.parametrize("mode", ["missing", "foreign", "deleted", "type", "marker", "property", "snapshot_key", "reverse"])
def test_target_must_equal_the_actual_import_checkpoint(assessment, make_org, mode):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    args = _arguments(assessment)
    table = ArchiMateElement.__table__
    target_id = args["archimate_element_id"]
    values = {}
    code = "snapshot_conflict"
    if mode == "missing":
        db.session.execute(table.delete().where(table.c.organization_id == assessment["org_id"], table.c.id == target_id))
        code = "target_unavailable"
    elif mode == "foreign":
        values = {"organization_id": make_org("foreign-target").id}
        code = "target_unavailable"
    elif mode == "deleted":
        values = {"deleted_at": datetime.utcnow()}
        code = "target_unavailable"
    elif mode == "type":
        values = {"type": "applicationcomponent"}
        code = "target_type"
    elif mode in {"marker", "property"}:
        properties = copy.deepcopy(assessment["elements"][assessment["keys"][0]]["snapshot"]["custom_properties"])
        properties["a05:reservation" if mode == "marker" else "human:annotation"] = "changed"
        values = {"custom_properties": properties}
        code = "identity_conflict" if mode == "marker" else "snapshot_conflict"
    elif mode == "reverse":
        row = ApplicationComponent(name="Fictional reverse association", organization_id=assessment["org_id"], archimate_element_id=target_id)
        db.session.add(row)
        db.session.flush()
        values = {"application_component_id": row.id}
        code = "identity_conflict"
    else:
        settings = _settings(assessment["org_id"])
        del settings["a05_fixture"]["manifest"]["elements"][assessment["keys"][0]]["snapshot"]["custom_properties"]
        _save_settings(assessment["org_id"], settings)
    if values:
        db.session.execute(table.update().where(table.c.organization_id == assessment["org_id"], table.c.id == target_id).values(**values))
    db.session.commit()
    _refuses_without_change(assessment, args, code)


@pytest.mark.parametrize("mode", ["name", "deleted_name", "source", "link", "two_links", "natural_key", "deleted_natural_key"])
def test_all_name_source_and_link_candidates_are_audited(assessment, mode):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    args = _arguments(assessment)
    if mode in {"natural_key", "deleted_natural_key"}:
        row = ArchiMateElement(name=" FICTIONAL  STRASSE calibration ", type="ApplicationComponent",
                              organization_id=assessment["org_id"], layer="Application")
    else:
        link_id = args["archimate_element_id"]
        if mode in {"name", "deleted_name", "source"}:
            other_target = ArchiMateElement(name="Fictional collision target", type="ApplicationService",
                                            organization_id=assessment["org_id"], layer="Application")
            db.session.add(other_target)
            db.session.flush()
            link_id = other_target.id
        row = ApplicationComponent(
            name=" FICTIONAL  STRASSE calibration " if "name" in mode else "Fictional collision",
            organization_id=assessment["org_id"], archimate_element_id=link_id,
        )
        if mode == "source":
            row.source_identifier = f'a05:v1:{assessment["org_id"]}:{assessment["token"]}:{args["fixture_key"]}'
    if mode.startswith("deleted"):
        row.deleted_at = datetime.utcnow()
    db.session.add(row)
    if mode == "two_links":
        db.session.add(ApplicationComponent(name="Fictional second collision", organization_id=assessment["org_id"],
                                            archimate_element_id=args["archimate_element_id"]))
    db.session.commit()
    _refuses_without_change(assessment, args, "identity_conflict")


@pytest.mark.parametrize("field", ["application_owner", "business_owner", "technical_owner", "technical_lead", "product_manager", "development_team", "support_team"])
@pytest.mark.parametrize("value", ["Fictional custodian", ""])
def test_every_owner_text_field_blocks_even_an_unchanged_value(assessment, field, value):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    receipt = _accepted(assessment)[0]
    _intend(assessment)
    args = _arguments(assessment)
    table = ApplicationComponent.__table__
    db.session.execute(table.update().where(table.c.organization_id == assessment["org_id"], table.c.id == receipt["application_id"]).values(**{field: value}))
    db.session.commit()
    _refuses_without_change(assessment, args, "owner_conflict")


@pytest.mark.parametrize("foreign_owner", [False, True])
def test_any_owner_junction_blocks_including_malformed_foreign_ownership(assessment, make_org, foreign_owner):
    from app import db
    from app.models.application_owner import ApplicationOwner
    from app.models.user import User

    receipt = _accepted(assessment)[0]
    _intend(assessment)
    org_id = make_org("foreign-owner").id if foreign_owner else assessment["org_id"]
    user = User(email=f"{uuid.uuid4().hex}@example.invalid", organization_id=org_id,
                enterprise_role="enterprise_architect", is_platform_admin=False, is_org_admin=False)
    db.session.add(user)
    db.session.flush()
    db.session.add(ApplicationOwner(application_id=receipt["application_id"], user_id=user.id, organization_id=org_id))
    db.session.commit()
    _refuses_without_change(assessment, _arguments(assessment), "owner_conflict")


@pytest.mark.parametrize("field,value", [
    ("health_status", "critical"), ("health_status", "unknown"), ("description", "Human edited rationale"),
    ("name", "Renamed by a person"), ("notes", "Human notes"), ("data_source", "manual"),
    ("source_identifier", "changed"), ("lock_version", 29), ("tags", ["human"]),
    ("created_at", datetime(2020, 1, 1)), ("updated_at", datetime(2020, 1, 1)),
    ("deleted_at", datetime(2020, 1, 1)), ("archimate_element_id", None),
])
def test_full_snapshot_refuses_human_edits_before_comparing_desired_health(assessment, field, value):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    receipt = _accepted(assessment)[0]
    _intend(assessment, health="critical")
    args = _arguments(assessment)
    table = ApplicationComponent.__table__
    db.session.execute(table.update().where(table.c.organization_id == assessment["org_id"], table.c.id == receipt["application_id"]).values(**{field: value}))
    db.session.commit()
    _refuses_without_change(assessment, args)


@pytest.mark.parametrize("mode", ["caller_key", "caller_value", "bool_id", "manifest_key", "pending_key", "pending_absence", "lost_manifest", "orphan", "fake_id", "preserve_mismatch", "null_mismatch"])
def test_caller_prior_manifest_pending_and_live_state_must_all_agree(assessment, mode):
    from app import db

    _accepted(assessment)
    _intend(assessment)
    args = _arguments(assessment)
    settings = _settings(assessment["org_id"])
    manifest = settings["a05_fixture"]["manifest"]
    key = assessment["keys"][0]
    if mode == "caller_key":
        del args["expected_snapshot"]["notes"]
    elif mode == "caller_value":
        args["expected_snapshot"]["notes"] = "new expectation"
    elif mode == "bool_id":
        args["expected_snapshot"]["id"] = True
    elif mode == "manifest_key":
        del manifest["applications"][key]["snapshot"]["notes"]
    elif mode == "pending_key":
        del manifest["pending"]["health"][key]["expected_snapshot"]["notes"]
    elif mode == "pending_absence":
        manifest["pending"]["health"][key]["expected_snapshot"] = None
    elif mode in {"lost_manifest", "orphan"}:
        del manifest["applications"][key]
        if mode == "orphan":
            args.update(application_id=None, expected_snapshot=None, health_status=None, description=_RATIONALE)
            intent = manifest["pending"]["health"][key]
            intent.update(expected_application_id=None, expected_snapshot=None)
            intent["desired"] = {"health": {"operation": "set", "value": None}, "description": {"operation": "set", "value": _RATIONALE}}
    elif mode == "fake_id":
        args["application_id"] += 999999
    elif mode == "preserve_mismatch":
        args["health_status"] = None
    else:
        manifest["pending"]["health"][key]["desired"]["health"] = {"operation": "set", "value": None}
    _save_settings(assessment["org_id"], settings)
    db.session.commit()
    _refuses_without_change(assessment, args)


def test_omission_cannot_preserve_an_invalid_stored_assessment(assessment):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    receipt = _accepted(assessment)[0]
    table = ApplicationComponent.__table__
    db.session.execute(table.update().where(
        table.c.organization_id == assessment["org_id"], table.c.id == receipt["application_id"]
    ).values(health_status="unknown"))
    invalid_snapshot = _stored(ApplicationComponent, assessment["org_id"], receipt["application_id"])[0]
    settings = _settings(assessment["org_id"])
    settings["a05_fixture"]["manifest"]["applications"][assessment["keys"][0]]["snapshot"] = invalid_snapshot
    _save_settings(assessment["org_id"], settings)
    db.session.commit()
    _intend(assessment)
    _refuses_without_change(assessment, _arguments(assessment), "snapshot_conflict")


def test_same_names_and_keys_in_two_tenants_and_warm_foreign_identity(assessment, make_org, tenant_ctx):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    first = _accepted(assessment)[0]
    other_id = make_org("assessment-contrast").id
    db.session.commit()
    with tenant_ctx(other_id):
        other = _prepare(other_id, "Fictional Straße Calibration", health="critical")
        second = _accepted(other)[0]
        other_before = _inventory(other)
        warm = db.session.get(ApplicationComponent, second["application_id"])
        assert warm.health_status == "critical"
    with tenant_ctx(assessment["org_id"]):
        _intend(assessment, health="healthy")
        args = _arguments(assessment)
        args["application_id"] = second["application_id"]
        _refuses_without_change(assessment, args)
        changed = _accepted(assessment)[0]
        assert changed["application_id"] == first["application_id"]
        assert changed["health_status"] == "healthy"
        assert _inventory(other) == other_before
        assert warm.id == second["application_id"]


def test_foreign_link_integrity_audit_discloses_no_foreign_fields(assessment, make_org):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    args = _arguments(assessment)
    other_id = make_org("foreign-link").id
    row = ApplicationComponent(name="Private foreign application", organization_id=other_id,
                               archimate_element_id=args["archimate_element_id"])
    db.session.add(row)
    db.session.commit()
    before = _stored(ApplicationComponent, other_id)
    _refuses_without_change(assessment, args, "identity_conflict")
    assert _stored(ApplicationComponent, other_id) == before


def test_lock_failure_is_not_silently_treated_as_permission(assessment, monkeypatch):
    from app.utils import duplicate_guard

    monkeypatch.setattr(duplicate_guard, "lock_name_for_write", lambda *args, **kwargs: False)
    _refuses_without_change(assessment, _arguments(assessment), "lock_unavailable", RuntimeError)


def test_caller_rollback_removes_flushed_assessment_and_method_never_ends_transaction(assessment, monkeypatch):
    from app import db

    args = _arguments(assessment)
    before = _inventory(assessment)
    session = db.session()
    with monkeypatch.context() as patch:
        def forbidden():
            pytest.fail("Assessment writer ended the caller transaction")
        patch.setattr(session, "commit", forbidden)
        patch.setattr(session, "rollback", forbidden)
        receipt = _write(args)
        assert _inventory(assessment)[1] == [receipt["snapshot"]]
    db.session.rollback()
    assert _inventory(assessment) == before


@pytest.mark.parametrize("failure", ["later_flush", "readback", "checkpoint", "validation"])
@pytest.mark.parametrize("phase", ["create", "update"])
def test_whole_health_phase_rolls_back_and_preserves_import_and_prior_receipt(app, db_session, make_org, tenant_ctx, monkeypatch, failure, phase):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.modules.applications.services import application_factory as factory

    org_id = make_org("phase-rollback").id
    db.session.commit()
    with tenant_ctx(org_id):
        state = _prepare(org_id, "Fictional phase calibration", count=2, health="healthy")
        if phase == "update":
            _accepted(state)
        _intend(state, health=None, description=_RATIONALE)
        args = [_arguments(state, key) for key in state["keys"]]
        before = _inventory(state)
        # End the read transaction before the phase, retaining the committed setup.
        db.session.remove()
        listener = None
        model = None
        event_name = "before_insert" if phase == "create" else "before_update"
        try:
            if failure == "later_flush":
                model = ApplicationComponent
                def listener(mapper, connection, target):
                    if target.archimate_element_id == args[1]["archimate_element_id"]:
                        connection.execute(text("SELECT 1 / 0"))
                event.listen(model, event_name, listener)
            elif failure == "readback":
                original = factory._read_assessment_row
                def wrong_readback(connection, table, organization_id, row_id, *, lock=False):
                    row = original(connection, table, organization_id, row_id, lock=lock)
                    if (table is ApplicationComponent.__table__ and row is not None and not lock
                            and row["archimate_element_id"] == args[1]["archimate_element_id"]):
                        result = dict(row)
                        result["health_status"] = "critical"
                        return result
                    return row
                monkeypatch.setattr(factory, "_read_assessment_row", wrong_readback)
            elif failure == "checkpoint":
                model = Organization
                event_name = "before_update"
                def listener(mapper, connection, target):
                    if target.id == org_id:
                        connection.execute(text("SELECT 1 / 0"))
                event.listen(model, event_name, listener)
            else:
                args[1]["expected_snapshot"] = {"notes": "No longer the accepted snapshot"}
            from sqlalchemy.exc import DBAPIError
            error_type = {"readback": RuntimeError, "validation": ValueError}.get(failure, DBAPIError)
            receipts = []
            with pytest.raises(error_type) as raised:
                for argument in args:
                    receipts.append(_write(argument))
                _checkpoint(state, receipts)
                db.session.commit()
            assert len(receipts) == (2 if failure == "checkpoint" else 1)
            if isinstance(raised.value, DBAPIError):
                original_error = raised.value.orig
                assert (getattr(original_error, "sqlstate", None) or getattr(original_error, "pgcode", None)) == "22012"
        finally:
            if listener is not None:
                event.remove(model, event_name, listener)
            if failure == "readback":
                monkeypatch.setattr(factory, "_read_assessment_row", original)
            db.session.rollback()
            db.session.remove()
        assert _inventory(state) == before
        assert before[2]["unrelated"] == {"retain": [None, "", False, 0]}
        if phase == "update":
            assert before[2]["a05_fixture"]["manifest"]["health_checkpoint"] is not None
        receipts = _accepted(state)
        assert len(receipts) == 2 and all(receipt["health_status"] is None for receipt in receipts)
        assert _inventory(state)[0] == before[0]


def test_snapshot_encoding_retains_null_empty_decimal_datetime_and_json_types(assessment):
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.applications.services.application_factory import _snapshot_value

    receipt = _accepted(assessment)[0]
    assert set(receipt["snapshot"]) == {column.name for column in ApplicationComponent.__table__.columns}
    assert _snapshot_value({"null": None, "empty": "", "list": [], "bool": False, "int": 0,
                            "decimal": Decimal("1.2300"), "date": date(2020, 1, 1),
                            "time": datetime(2020, 1, 1, 1, 2, 3, 4)}) == {
        "null": None, "empty": "", "list": [], "bool": False, "int": 0,
        "decimal": "1.2300", "date": "2020-01-01", "time": "2020-01-01T01:02:03.000004",
    }
    for value in (float("nan"), float("inf"), object(), {1: "not a JSON key"}):
        with pytest.raises(ValueError, match="unsupported_snapshot_value"):
            _snapshot_value(value)
    db.session.remove()


# These cases use real committed rows and independent connections. They do not
# request db_session, make_org, or any fixture that pins the rollback connection.


def _durable_prepare(app, fixture):
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope

    with app.app_context(), tenant_scope(fixture["org_id"]):
        state = _prepare(fixture["org_id"], fixture["name"])
        db.session.remove()
    return state


def _deadline_assert(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    wake = threading.Event()
    while time.monotonic() < deadline:
        if predicate():
            return
        wake.wait(0.02)
    raise AssertionError("Synchronization deadline expired")


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_durable_unsupported_isolation_refuses_before_writes(app, independent_application_race, isolation):
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope

    state = _durable_prepare(app, independent_application_race)
    with app.app_context(), tenant_scope(state["org_id"]):
        args = _arguments(state)
        before = _inventory(state)
    with app.app_context(), tenant_scope(state["org_id"]):
        db.session.connection(execution_options={"isolation_level": isolation})
        with pytest.raises(RuntimeError, match="unsupported_assessment_transaction"):
            _write(args)
    with app.app_context(), tenant_scope(state["org_id"]):
        assert _inventory(state) == before


def test_durable_same_link_waits_through_commit_and_stale_absence_refuses(app, independent_application_race):
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.modules.applications.services.application_factory import ImportedApplicationAssessmentConflict

    fixture = independent_application_race
    state = _durable_prepare(app, fixture)
    with app.app_context(), tenant_scope(state["org_id"]):
        args = _arguments(state)
    release = threading.Event()
    flushed = threading.Event()
    connections = Queue()
    outcomes = Queue()

    def worker(first):
        try:
            with app.app_context(), tenant_scope(state["org_id"]):
                connection = db.session.connection()
                connection.execute(text("SET LOCAL lock_timeout = '15s'"))
                connection.execute(text("SET LOCAL statement_timeout = '20s'"))
                connections.put((first, connection.execute(text("SELECT pg_backend_pid()")).scalar_one()))
                try:
                    receipt = _write(copy.deepcopy(args))
                    if first:
                        flushed.set()
                        assert release.wait(12), "Commit was never released"
                        _checkpoint(state, [receipt])
                        db.session.commit()
                    outcomes.put((first, receipt))
                except ImportedApplicationAssessmentConflict as exc:
                    db.session.rollback()
                    outcomes.put((first, exc.code))
        except BaseException as exc:
            outcomes.put((first, exc))
            flushed.set()

    first = threading.Thread(target=worker, args=(True,), name="assessment-first")
    second = threading.Thread(target=worker, args=(False,), name="assessment-contender")
    fixture["threads"].extend([first, second])
    first.start()
    try:
        assert flushed.wait(15)
        second.start()
        pids = dict(connections.get(timeout=15) for _ in range(2))
        assert pids[True] != pids[False]
        with app.app_context(), db.engine.connect() as observer:
            def blocked():
                return pids[True] in observer.execute(text("SELECT pg_blocking_pids(:pid)"), {"pid": pids[False]}).scalar_one()
            _deadline_assert(blocked)
        # The first worker has finished the callable, but its caller still owns
        # the row lock. Only releasing its commit permits the contender to finish.
        assert outcomes.empty()
    finally:
        release.set()
        first.join(25)
        if second.ident is not None:
            second.join(25)
    assert not first.is_alive() and not second.is_alive()
    results = dict(outcomes.get(timeout=2) for _ in range(2))
    assert isinstance(results[True], dict), results
    assert results[False] == "intent_mismatch", results
    with app.app_context(), tenant_scope(state["org_id"]):
        _intend(state)
        repeated = _accepted(state)[0]
        assert repeated["outcome"] == "unchanged"
        assert repeated["snapshot"] == results[True]["snapshot"]
        inventory = _inventory(state)
        assert len(inventory[0]) == len(inventory[1]) == 1


def _memory_floor():
    if os.name == "nt":
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total_physical", "available_physical", "total_pagefile", "available_pagefile",
                    "total_virtual", "available_virtual", "available_extended",
                )
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        available = status.available_physical
    else:
        from pathlib import Path
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        available = int(values["MemAvailable"].split()[0]) * 1024
    assert available >= 3 * 1024 ** 3, "Child requires 3 GiB available RAM"


@contextmanager
def _owned_child(target, *args):
    _memory_floor()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(child, *args))
    try:
        process.start()
        child.close()
        yield process, parent
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join(5)
            assert not process.is_alive(), "Refusing fixture cleanup with an owned child alive"
        parent.close()
        child.close()


def _lock_probe(pipe, database_url, lock_name):
    """Lightweight independent process probes the actual advisory lock key."""
    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            pipe.send("ready")
            while pipe.poll(20):
                command = pipe.recv()
                if command == "stop":
                    return
                assert command == "probe"
                key = connection.execute(text("SELECT hashtext(:name)::bigint"), {"name": f"archie_job:{lock_name}"}).scalar_one()
                acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one()
                if acquired:
                    assert connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key}).scalar_one()
                pipe.send(acquired)
    finally:
        engine.dispose()
        pipe.close()


def _message(pipe):
    assert pipe.poll(45), "Child synchronization deadline expired"
    return pipe.recv()


@pytest.mark.parametrize("exception_exit", [False, True])
@pytest.mark.parametrize("lock_kind", ["refresh", "recompute"])
def test_durable_job_lock_survives_scope_reset_commit_and_releases(app, independent_application_race, exception_exit, lock_kind):
    from app import db
    from app.jobs.tenant_safe_job import job_lock, tenant_scope
    from app.modules.intelligence.services.recompute_job import per_tenant_lock_name

    fixture = independent_application_race
    name = f'a05_fixture:org:{fixture["org_id"]}' if lock_kind == "refresh" else per_tenant_lock_name(fixture["org_id"])
    with app.app_context():
        database_url = db.engine.url.render_as_string(hide_password=False)
        with _owned_child(_lock_probe, database_url, name) as (process, pipe):
            assert _message(pipe) == "ready"
            class IntentionalExit(Exception):
                pass
            try:
                with job_lock(name, required=True) as acquired:
                    assert acquired
                    for _ in range(2):
                        with tenant_scope(fixture["org_id"]):
                            db.session.execute(text("SELECT 1"))
                            db.session.commit()
                        pipe.send("probe")
                        assert _message(pipe) is False
                    if exception_exit:
                        raise IntentionalExit()
            except IntentionalExit:
                assert exception_exit
            pipe.send("probe")
            assert _message(pipe) is True
            pipe.send("stop")
            process.join(10)
            assert process.exitcode == 0


def _crash_worker(pipe, state, commit):
    # No second suite or fixture initialization: boot only the existing normal
    # test application, using the explicit database already guarded by the parent.
    from app import create_app, db
    from app.jobs.tenant_safe_job import job_lock, tenant_scope
    from app.modules.intelligence.services.recompute_job import per_tenant_lock_name
    from sqlalchemy.engine import make_url

    application = create_app("testing")
    with application.app_context():
        _normal_database()
        assert db.engine.url == make_url(os.environ["TEST_DATABASE_URL"])
        with job_lock(f'a05_fixture:org:{state["org_id"]}', required=True):
            with job_lock(per_tenant_lock_name(state["org_id"]), required=True):
                with tenant_scope(state["org_id"]):
                    receipt = _write(_arguments(state))
                    _checkpoint(state, [receipt])
                    db.session.flush()
                    if commit:
                        db.session.commit()
                        # Exit before reporting any receipt; recovery must use SQL.
                        os._exit(0)
                    pipe.send("flushed")
                    assert pipe.poll(45), "Parent did not end the owned crash worker"
                    raise AssertionError("The before-commit worker must be terminated")


@pytest.mark.parametrize("commit_before_exit", [False, True])
def test_durable_process_interruption_recovers_only_atomic_rows_and_checkpoint(app, independent_application_race, commit_before_exit):
    from app import db
    from app.jobs.tenant_safe_job import job_lock, tenant_scope
    from app.modules.intelligence.services.recompute_job import per_tenant_lock_name

    state = _durable_prepare(app, independent_application_race)
    with app.app_context(), tenant_scope(state["org_id"]):
        before = _inventory(state)
    with _owned_child(_crash_worker, state, commit_before_exit) as (process, pipe):
        if commit_before_exit:
            process.join(90)
            assert process.exitcode == 0
        else:
            assert _message(pipe) == "flushed"
            process.terminate()
            process.join(15)
            assert not process.is_alive()
    with app.app_context():
        def locks_released():
            with job_lock(f'a05_fixture:org:{state["org_id"]}', required=False) as refresh:
                if not refresh:
                    return False
                with job_lock(per_tenant_lock_name(state["org_id"]), required=False) as recompute:
                    return recompute
        _deadline_assert(locks_released)
        with job_lock(f'a05_fixture:org:{state["org_id"]}', required=True):
            with job_lock(per_tenant_lock_name(state["org_id"]), required=True):
                with tenant_scope(state["org_id"]):
                    after = _inventory(state)
                    assert after[0] == before[0]
                    assert after[2]["unrelated"] == before[2]["unrelated"]
                    if commit_before_exit:
                        assert len(after[1]) == 1
                        manifest = after[2]["a05_fixture"]["manifest"]
                        accepted = manifest["applications"][state["keys"][0]]
                        assert after[1] == [accepted["snapshot"]]
                        assert manifest["pending"] is None
                        assert manifest["health_checkpoint"]["receipts"][0]["application_id"] == accepted["id"]
                        _intend(state)
                        recovered = _accepted(state)[0]
                        assert recovered["outcome"] == "unchanged"
                        assert recovered["snapshot"] == accepted["snapshot"]
                    else:
                        assert after == before
                        recovered = _accepted(state)[0]
                        assert recovered["outcome"] == "created"
                    db.session.remove()
                with tenant_scope(state["org_id"]):
                    final = _inventory(state)
                    assert len(final[0]) == len(final[1]) == 1
                    assert final[1] == [recovered["snapshot"]]
                    assert final[2]["a05_fixture"]["manifest"]["pending"] is None
