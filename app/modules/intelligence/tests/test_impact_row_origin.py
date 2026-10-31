"""Every recorded impact row names its source record, who or what recorded it
and when, or says plainly that the origin is not recorded."""

from __future__ import annotations

import datetime as _dt
import json
import threading
import uuid


def _user(db_session, org_id, first="Ada", last="Lovelace"):
    from app.models.user import User

    user = User(
        email=f"pa2-{uuid.uuid4().hex[:10]}@example.com",
        first_name=first,
        last_name=last,
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org_id, source, target, **origin):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id, **origin
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _impact(app, org_id, element_id, **kw):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.cross_layer_impact(element_id, include_derived=False, **kw)


def _only_row(result):
    assert len(result["rows"]) == 1
    return result["rows"][0]["relation"]


def test_an_imported_relationship_names_the_file_the_time_and_recorded(app, db_session, make_org):
    org = make_org("pa2-import")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    when = _dt.datetime(2026, 10, 1, 9, 30, 0)
    rel = _link(db_session, org.id, a, b, source_kind="import", source_label="estate.xml", source_recorded_at=when)
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["relationship_id"] == rel.id
    assert relation["confidence"] == "recorded"
    assert relation["provenance"]["text"] == "Imported from estate.xml"
    assert relation["computed_at"] == when.isoformat()
    assert relation["provenance"]["recorded_at"] == when.isoformat()
    assert relation["chain"][-1] == rel.id


def test_a_hand_drawn_relationship_names_the_person_and_the_date(app, db_session, make_org):
    org = make_org("pa2-manual")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    when = _dt.datetime(2026, 10, 2, 14, 0, 0)
    _link(db_session, org.id, a, b, source_kind="manual", source_label="Ada Lovelace", source_recorded_at=when)
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["text"] == "Drawn by Ada Lovelace"
    assert relation["computed_at"] == when.isoformat()
    assert relation["confidence"] == "recorded"


def test_a_hand_drawn_relationship_with_only_a_creator_id_names_that_person(app, db_session, make_org):
    org = make_org("pa2-creator")
    person = _user(db_session, org.id, "Grace", "Hopper")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    rel = _link(db_session, org.id, a, b, source_kind="manual", created_by_id=person.id)
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["text"] == "Drawn by Grace Hopper"
    assert relation["provenance"]["relationship_id"] == rel.id
    assert relation["computed_at"] is not None


def test_a_relationship_from_before_origins_says_origin_not_recorded(app, db_session, make_org):
    org = make_org("pa2-legacy")
    person = _user(db_session, org.id)
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    _link(db_session, org.id, a, b, created_by_id=person.id)
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["text"] == "origin not recorded"
    assert "Ada" not in json.dumps(relation, default=str)
    assert relation["confidence"] == "recorded"


def test_the_cited_answer_carries_the_same_fields(app, db_session, make_org):
    from flask import g

    from app.modules.intelligence.services.answer_envelope import KIND_IMPACT, build_envelope, normalise_params
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("pa2-envelope")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    when = _dt.datetime(2026, 10, 3, 8, 0, 0)
    rel = _link(db_session, org.id, a, b, source_kind="import", source_label="estate.xml", source_recorded_at=when)
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(a.id, include_derived=False)
        envelope = build_envelope(KIND_IMPACT, normalise_params(KIND_IMPACT, {"element_id": a.id}), result)

    citation = next(c for c in envelope["citations"] if c["type"] == "relationship" and c["id"] == rel.id)
    assert citation["origin"] == "Imported from estate.xml"
    assert citation["recorded_at"] == when.isoformat()
    assert citation["confidence"] == "recorded"
    row_relation = envelope["rows"][0]["relation"]
    assert row_relation["provenance"]["text"] == "Imported from estate.xml"
    assert row_relation["confidence"] == "recorded"
    json.dumps(envelope, default=str)


def test_one_query_loads_the_people_for_a_page_of_rows(app, db_session, make_org):
    from flask import g
    from sqlalchemy import event

    from app.extensions import db
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("pa2-count")
    person = _user(db_session, org.id)
    root = _element(db_session, org.id, "Root")
    for i in range(6):
        _link(
            db_session,
            org.id,
            root,
            _element(db_session, org.id, f"Dep{i}"),
            source_kind="manual",
            created_by_id=person.id,
        )
    db_session.commit()

    statements = []
    me = threading.get_ident()

    def listener(conn, cursor, statement, params, context, executemany):
        if threading.get_ident() == me:
            statements.append(" ".join(statement.split()).lower())

    with app.test_request_context("/"):
        g.current_org_id = org.id
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            result = IntelligenceQueryService.cross_layer_impact(root.id, include_derived=False, page_size=50)
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)

    assert len(result["rows"]) == 6
    assert all(r["relation"]["provenance"]["text"] == "Drawn by Ada Lovelace" for r in result["rows"])
    user_selects = [s for s in statements if s.startswith("select") and " from users" in s]
    assert len(user_selects) == 1, user_selects


def test_another_organisations_origins_never_appear_for_this_one(app, db_session, make_org):
    org_a, org_b = make_org("pa2-org-a"), make_org("pa2-org-b")
    stranger = _user(db_session, org_b.id, "Mallory", "Outsider")
    a1, a2 = _element(db_session, org_a.id, "ARoot"), _element(db_session, org_a.id, "ADep")
    b1, b2 = _element(db_session, org_b.id, "BRoot"), _element(db_session, org_b.id, "BDep")
    _link(db_session, org_b.id, b1, b2, source_kind="import", source_label="secret-b.xml")
    # A's own relationship points at a creator who belongs to B.
    _link(db_session, org_a.id, a1, a2, source_kind="manual", created_by_id=stranger.id)
    db_session.commit()

    mine = _impact(app, org_a.id, a1.id)
    text = json.dumps(mine, default=str)
    assert "Mallory" not in text and "secret-b.xml" not in text
    assert _only_row(mine)["provenance"]["text"] == "origin not recorded"

    foreign = _impact(app, org_a.id, b1.id)
    assert foreign["rows"] == []
    assert "secret-b.xml" not in json.dumps(foreign, default=str)

    theirs = _impact(app, org_b.id, b1.id)
    assert _only_row(theirs)["provenance"]["text"] == "Imported from secret-b.xml"


def test_a_row_that_stored_only_the_drawers_id_in_source_ref_names_that_person(app, db_session, make_org):
    org = make_org("pa2-source-ref")
    person = _user(db_session, org.id, "Grace", "Hopper")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    _link(db_session, org.id, a, b, source_kind="manual", source_ref=str(person.id))
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["text"] == "Drawn by Grace Hopper"


def test_a_source_ref_person_from_another_organisation_is_not_named(app, db_session, make_org):
    org_a, org_b = make_org("pa2-sr-a"), make_org("pa2-sr-b")
    stranger = _user(db_session, org_b.id, "Mallory", "Outsider")
    a, b = _element(db_session, org_a.id, "Root"), _element(db_session, org_a.id, "Dep")
    created = _dt.datetime(2026, 9, 1, 10, 0, 0)
    _link(
        db_session, org_a.id, a, b,
        source_kind="manual", source_ref=str(stranger.id),
        source_recorded_at=_dt.datetime(2026, 10, 5, 10, 0, 0), created_at=created,
    )
    db_session.commit()

    result = _impact(app, org_a.id, a.id)
    relation = _only_row(result)

    assert "Mallory" not in json.dumps(result, default=str)
    assert relation["provenance"]["text"] == "origin not recorded"
    # With no origin, only the creation date is shown.
    assert relation["computed_at"] == created.isoformat()


def test_a_last_edit_date_is_not_a_recorded_date(app, db_session, make_org):
    org = make_org("pa2-no-updated-at")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    rel = _link(db_session, org.id, a, b)
    from sqlalchemy import text

    db_session.execute(
        text("update archimate_relationships set created_at = null, updated_at = now() where id = :i and organization_id = :o"),
        {"i": rel.id, "o": org.id},
    )
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["text"] == "origin not recorded"
    assert relation["computed_at"] is None
    assert relation["provenance"]["recorded_at"] is None


def test_a_relationship_drawn_through_the_drawing_service_names_the_signed_in_person(
    app, db_session, make_org, tenant_ctx, client, login_as
):
    from app.models import ArchitectureModel
    from app.modules.architecture.services.archimate_relationship_service import (
        ArchiMateRelationshipService,
    )

    org = make_org("pa2-real-service")
    with tenant_ctx(org.id):
        ada = _user(db_session, org.id, "Ada", "Lovelace")
        model = ArchitectureModel(name="PA2 model", organization_id=org.id)
        db_session.add(model)
        db_session.flush()
        source, target = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
        source.architecture_id = target.architecture_id = model.id
        db_session.flush()
        login_as(client, ada)
        with client:
            client.get("/")
            rel = ArchiMateRelationshipService.create_relationship(
                source_element=source, target_element=target, relationship_type="serving",
                architecture_id=model.id, organization_id=org.id,
            )
            assert rel is not None
        db_session.commit()
        source_id = source.id

    relation = _only_row(_impact(app, org.id, source_id))
    assert relation["provenance"]["text"] == "Drawn by Ada Lovelace"
    assert relation["computed_at"] is not None


def test_a_source_label_describing_a_kind_of_source_is_not_drawn_by(app, db_session, make_org):
    org = make_org("pa2-kind-label")
    a, b = _element(db_session, org.id, "Root"), _element(db_session, org.id, "Dep")
    _link(db_session, org.id, a, b, source_kind="manual", source_label="Entered by the team")
    db_session.commit()

    relation = _only_row(_impact(app, org.id, a.id))

    assert relation["provenance"]["text"] == "Entered by the team"
    assert "Drawn by" not in relation["provenance"]["text"]


def test_the_origin_words_for_a_name_and_for_a_kind_of_source():
    from app.services.relationship_origin import manual_origin_text

    assert manual_origin_text("Ada Lovelace") == "Drawn by Ada Lovelace"
    assert manual_origin_text("Entered by the team") == "Entered by the team"
    assert manual_origin_text("Imported from GitHub") == "Imported from GitHub"
