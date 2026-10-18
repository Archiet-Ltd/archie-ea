"""An application's individual owner has one store: ApplicationOwner.

The owner text columns (application_owner, business_owner, technical_owner) were
read and written directly by dozens of screens, imports and APIs while the owner
screens, the ask drawer and the RACI read ApplicationOwner, so the same
application had an owner in one place and none in another. The three fields now
answer from, and assign into, the one ownership record; the text columns hold
only a name that matched no user.
"""

from __future__ import annotations

import json

import pytest

from tests.test_application_owner_writer import _make_app, _make_user
from tests.test_store_agreement_concepts import _live_findings, gate

CONCEPT = "application owners"


def _person(db_session, org, label):
    return _make_user(db_session, org, "application_manager", label)


def _full_name(user):
    return f"{user.first_name} {user.last_name}"


def _owners(application, org):
    from app.models.application_owner import ApplicationOwner

    return ApplicationOwner.get_display_rows_for_application(application.id, org.id)


# ── the legacy write paths now reach the ownership record ────────────────────


def test_assigning_an_owner_field_is_read_back_from_the_ownership_record(db_session, make_org):
    """Fails on main: the field was a text column and the record stayed empty."""
    org = make_org("one-store-write")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Ledger")

    application.business_owner = _full_name(jane)
    db_session.flush()

    rows = _owners(application, org)
    assert [(r["user_id"], r["ownership_type"]) for r in rows] == [(jane.id, "business")]
    assert application.business_owner == _full_name(jane)
    assert application.legacy_business_owner is None


def test_constructing_an_application_with_an_owner_records_it(db_session, make_org):
    org = make_org("one-store-new")
    jane = _person(db_session, org, "jane")
    application = _make_app(
        db_session, org, "New", application_owner=jane.email, technical_owner=_full_name(jane))

    kinds = sorted(r["ownership_type"] for r in _owners(application, org))
    assert kinds == ["primary", "technical"]


def test_reassigning_replaces_the_owner_of_that_type_only(db_session, make_org):
    org = make_org("one-store-replace")
    jane, joe = _person(db_session, org, "jane"), _person(db_session, org, "joe")
    application = _make_app(db_session, org, "Ledger")
    application.business_owner = _full_name(jane)
    application.technical_owner = _full_name(jane)
    db_session.flush()

    application.business_owner = _full_name(joe)
    db_session.flush()

    by_type = {r["ownership_type"]: r["user_id"] for r in _owners(application, org)}
    assert by_type == {"business": joe.id, "technical": jane.id}


def test_a_blank_assignment_never_blanks_an_owner_on_record(db_session, make_org):
    """The merge-import bug: a row with an empty owner cell wiped the owner."""
    org = make_org("one-store-blank")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Ledger")
    application.business_owner = _full_name(jane)
    db_session.flush()

    application.business_owner = None
    application.business_owner = "   "
    db_session.flush()

    assert [r["user_id"] for r in _owners(application, org)] == [jane.id]
    assert application.business_owner == _full_name(jane)


def test_a_name_matching_no_user_stays_visible_and_never_displaces_a_recorded_owner(
        db_session, make_org):
    org = make_org("one-store-unknown")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Ledger")

    application.technical_owner = "Someone External"
    db_session.flush()
    assert _owners(application, org) == []
    assert application.technical_owner == "Someone External"

    application.business_owner = _full_name(jane)
    db_session.flush()
    application.business_owner = "Someone External"
    db_session.flush()
    assert [r["user_id"] for r in _owners(application, org)] == [jane.id]
    assert application.business_owner == _full_name(jane)


def test_the_owner_fields_work_in_queries(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("one-store-query")
    jane = _person(db_session, org, "jane")
    owned = _make_app(db_session, org, "Owned")
    legacy = _make_app(db_session, org, "Legacy", legacy_business_owner="Outside Person")
    bare = _make_app(db_session, org, "Bare")
    owned.business_owner = _full_name(jane)
    db_session.flush()

    found = {a.id for a in ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id,
        ApplicationComponent.business_owner.isnot(None)).all()}
    assert found == {owned.id, legacy.id}
    by_name = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id,
        ApplicationComponent.business_owner.ilike("%jane%")).all()
    assert [a.id for a in by_name] == [owned.id]
    assert bare.id not in found


def test_preloading_gives_the_same_answer_as_reading_one_by_one(db_session, make_org):
    from app.services.element_ownership_service import preload_application_owner_text

    org = make_org("one-store-preload")
    jane = _person(db_session, org, "jane")
    apps = [_make_app(db_session, org, f"A{i}") for i in range(3)]
    apps[0].business_owner = _full_name(jane)
    db_session.flush()
    fresh = [type(a).query.get(a.id) for a in apps]
    for a in fresh:
        a.__dict__.pop("_owner_text_cache", None)
    preload_application_owner_text(fresh)
    assert [a.business_owner for a in fresh] == [_full_name(jane), None, None]


def test_the_bulk_assign_route_records_the_owner(db_session, make_org, client, login_as):
    """A legacy route that only ever set the text column now reaches the record."""
    org = make_org("one-store-route")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Ledger")
    login_as(client, jane)

    resp = client.post(
        "/applications/api/bulk-assign-owner",
        data=json.dumps({"ids": [application.id], "owner_name": _full_name(jane)}),
        content_type="application/json")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert [(r["user_id"], r["ownership_type"]) for r in _owners(application, org)] == [
        (jane.id, "primary")]


# ── two organisations ────────────────────────────────────────────────────────


def test_an_owner_in_one_organisation_is_never_visible_from_the_other(db_session, make_org):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("one-store-a"), make_org("one-store-b")
    jane_a = _person(db_session, org_a, "janea")
    jane_b = _person(db_session, org_b, "janeb")
    app_a = _make_app(db_session, org_a, "A ledger")
    app_b = _make_app(db_session, org_b, "B ledger")

    app_a.business_owner = _full_name(jane_a)
    # A name that exists only in organisation B cannot own organisation A's app.
    app_a.technical_owner = _full_name(jane_b)
    app_b.business_owner = _full_name(jane_b)
    db_session.flush()

    assert [r["user_id"] for r in _owners(app_a, org_a)] == [jane_a.id]
    assert [r["user_id"] for r in _owners(app_b, org_b)] == [jane_b.id]
    # Read as organisation B, organisation A's owners do not exist.
    assert ApplicationOwner.get_display_rows_for_application(app_a.id, org_b.id) == []
    assert ApplicationOwner.query.filter_by(user_id=jane_b.id, application_id=app_a.id).count() == 0
    # The unmatched name stays text on A's application; it is not B's person.
    assert app_a.technical_owner == _full_name(jane_b)
    assert app_a.legacy_technical_owner == _full_name(jane_b)
    # Filtering by an owner's name never crosses the organisation line.
    hits = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org_a.id,
        ApplicationComponent.business_owner == _full_name(jane_b)).all()
    assert hits == []


def test_an_owner_cannot_be_recorded_across_organisations_through_the_service(db_session, make_org):
    from app.services.element_ownership_service import (
        CrossOrganisationElementOwner, set_element_owner)

    org_a, org_b = make_org("one-store-xa"), make_org("one-store-xb")
    jane_b = _person(db_session, org_b, "janeb")
    app_a = _make_app(db_session, org_a, "A ledger")
    with pytest.raises(CrossOrganisationElementOwner):
        set_element_owner(element_type="application", element_id=app_a.id,
                          user_id=jane_b.id, organization_id=org_a.id)
    with pytest.raises(CrossOrganisationElementOwner):
        set_element_owner(element_type="application", element_id=app_a.id,
                          user_id=jane_b.id, organization_id=org_b.id)


# ── the backfill ─────────────────────────────────────────────────────────────


def _state(org):
    from app.models.application_owner import ApplicationOwner

    return sorted(
        (o.application_id, o.user_id, o.ownership_type, o.source_table)
        for o in ApplicationOwner.query.filter_by(organization_id=org.id).all())


def test_backfill_moves_every_text_field_and_is_idempotent(db_session, make_org):
    from app.commands.backfill_application_owners import backfill_owner_data

    org = make_org("one-store-backfill")
    jane, joe = _person(db_session, org, "jane"), _person(db_session, org, "joe")
    application = _make_app(
        db_session, org, "Legacy",
        legacy_application_owner=_full_name(jane),
        legacy_business_owner=joe.email,
        legacy_technical_owner="Nobody Known")
    other = _make_app(db_session, org, "Other", legacy_business_owner=_full_name(jane))

    first = backfill_owner_data(organization_ids=[org.id])
    after_first = _state(org)
    second = backfill_owner_data(organization_ids=[org.id])

    assert first["text_owner_fields"] == 3
    assert first["unresolved_text_owners"] == 1
    assert [(a, u, t) for a, u, t, _ in after_first] == sorted([
        (application.id, jane.id, "primary"),
        (application.id, joe.id, "business"),
        (other.id, jane.id, "business"),
    ])
    assert {src for *_, src in after_first} == {"application_owner", "business_owner"}
    assert second["text_owner_fields"] == 0
    assert _state(org) == after_first
    # A name that resolved to a user is cleared once recorded; one that did not stays.
    assert application.legacy_technical_owner == "Nobody Known"
    assert application.legacy_business_owner is None


def test_a_removed_owner_does_not_bring_back_the_legacy_text_after_backfill(db_session, make_org):
    """B2: the backfill left the text in place, so removing the owner re-showed it."""
    from app.commands.backfill_application_owners import backfill_owner_data
    from app.models.application_owner import ApplicationOwner

    org = make_org("one-store-removed")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Legacy", legacy_business_owner=_full_name(jane))
    backfill_owner_data(organization_ids=[org.id])
    assert application.business_owner == _full_name(jane)

    for row in ApplicationOwner.query.filter_by(application_id=application.id, organization_id=org.id).all():
        db_session.delete(row)
    db_session.flush()
    application.__dict__.pop("_owner_text_cache", None)

    assert application.business_owner is None
    backfill_owner_data(organization_ids=[org.id])
    assert application.business_owner is None


def test_a_recorded_owner_without_a_name_reads_as_their_email_not_a_dash(db_session, make_org):
    """B1a: a dash read as "no owner" in the evidence snapshot."""
    org = make_org("one-store-nameless")
    nameless = _person(db_session, org, "pat")
    nameless.first_name = None
    nameless.last_name = None
    application = _make_app(db_session, org, "Ledger")
    application.application_owner = nameless.email
    db_session.flush()

    assert application.application_owner == nameless.email
    application.__dict__.pop("_owner_text_cache", None)
    assert application.application_owner == nameless.email


def test_reading_an_owner_joins_the_applications_own_transaction(db_session, make_org):
    """B1b: the read ran on the global session and began a transaction on it."""
    from sqlalchemy.orm import object_session

    org = make_org("one-store-session")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Ledger")
    application.business_owner = _full_name(jane)
    db_session.flush()
    application.__dict__.pop("_owner_text_cache", None)

    session = object_session(application)
    assert application.business_owner == _full_name(jane)
    assert session.in_transaction()
    # A caller that reads the owner and then begins its own nested work on the
    # application's session must not find a transaction begun behind its back.
    with session.begin_nested():
        assert application.business_owner == _full_name(jane)


def test_backfill_of_one_organisation_leaves_the_other_untouched(db_session, make_org):
    from app.commands.backfill_application_owners import backfill_owner_data

    org_a, org_b = make_org("one-store-bfa"), make_org("one-store-bfb")
    jane_a, jane_b = _person(db_session, org_a, "janea"), _person(db_session, org_b, "janeb")
    _make_app(db_session, org_a, "A", legacy_business_owner=_full_name(jane_a))
    _make_app(db_session, org_b, "B", legacy_business_owner=_full_name(jane_b))

    backfill_owner_data(organization_ids=[org_a.id])

    assert [u for _, u, _, _ in _state(org_a)] == [jane_a.id]
    assert _state(org_b) == []


# ── the store-agreement gate ─────────────────────────────────────────────────


def test_the_gate_catches_an_owner_held_only_as_legacy_text(
        app, db_session, make_org, tenant_ctx):
    from app.commands.backfill_application_owners import backfill_owner_data

    org = make_org("one-store-gate")
    user = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Legacy", legacy_business_owner=_full_name(user))

    findings, notes, _ = _live_findings(app, tenant_ctx, org.id, user, [CONCEPT], http=False)
    assert len(findings) == 1, (findings, notes)
    assert "[unmigrated]" in findings[0]
    assert "ids %s" % application.id in findings[0]

    backfill_owner_data(organization_ids=[org.id])
    findings, notes, _ = _live_findings(app, tenant_ctx, org.id, user, [CONCEPT], http=False)
    assert findings == [], (findings, notes)


def test_the_gate_catches_legacy_text_naming_someone_else(app, db_session, make_org, tenant_ctx):
    org = make_org("one-store-gate-diff")
    jane, joe = _person(db_session, org, "jane"), _person(db_session, org, "joe")
    application = _make_app(db_session, org, "Ledger", legacy_business_owner=_full_name(jane))
    from app.services.element_ownership_service import set_element_owner

    set_element_owner(element_type="application", element_id=application.id, user_id=joe.id,
                      organization_id=org.id, ownership_type="business")

    findings, notes, _ = _live_findings(app, tenant_ctx, org.id, joe, [CONCEPT], http=False)
    assert len(findings) == 1, (findings, notes)
    assert "[value-disagreement]" in findings[0]


def test_the_gate_is_green_when_an_owner_is_assigned_through_the_field(
        app, db_session, make_org, tenant_ctx):
    org = make_org("one-store-gate-ok")
    jane = _person(db_session, org, "jane")
    application = _make_app(db_session, org, "Ledger")
    application.business_owner = _full_name(jane)
    db_session.flush()

    findings, notes, _ = _live_findings(app, tenant_ctx, org.id, jane, [CONCEPT], http=False)
    assert findings == [], (findings, notes)


def test_the_gate_judges_each_organisation_by_its_own_owners(app, db_session, make_org, tenant_ctx):
    org_a, org_b = make_org("one-store-gate-a"), make_org("one-store-gate-b")
    jane_a, jane_b = _person(db_session, org_a, "janea"), _person(db_session, org_b, "janeb")
    app_a = _make_app(db_session, org_a, "A", legacy_business_owner=_full_name(jane_a))
    _make_app(db_session, org_b, "B")

    in_b, _, _ = _live_findings(app, tenant_ctx, org_b.id, jane_b, [CONCEPT], http=False)
    in_a, _, _ = _live_findings(app, tenant_ctx, org_a.id, jane_a, [CONCEPT], http=False)
    assert in_b == []
    assert len(in_a) == 1 and "ids %s" % app_a.id in in_a[0]


def test_the_gate_pairs_cover_exactly_the_fields_the_service_records():
    from app.services.element_ownership_service import APPLICATION_OWNER_FIELDS

    assert gate._APPLICATION_OWNER_FIELDS == APPLICATION_OWNER_FIELDS
    assert len(gate.VALUE_PAIRS[CONCEPT]) == len(APPLICATION_OWNER_FIELDS)


def test_the_organisation_unit_axis_is_not_a_surface_of_application_owners():
    named = {s.target for s in gate.CONCEPTS[CONCEPT]}
    assert "app.models.enterprise_intelligence.ApplicationOwnership" not in named


def test_no_site_assigns_the_legacy_columns_outside_the_owner_service():
    """The retired columns are written only by the service and the backfill."""
    import os
    import re

    root = os.path.join(os.path.dirname(__file__), "..", "app")
    pattern = re.compile(r"\.legacy_(application|business|technical)_owner\s*=[^=]|\blegacy_(application|business|technical)_owner\s*=")
    allowed = {
        os.path.join("services", "element_ownership_service.py"),
        os.path.join("commands", "backfill_application_owners.py"),
        os.path.join("models", "application_portfolio.py"),
    }
    offenders = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root)
            if not name.endswith(".py") or relative in allowed:
                continue
            with open(path, encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    if pattern.search(line):
                        offenders.append(f"{relative}:{number}")
    assert offenders == []


