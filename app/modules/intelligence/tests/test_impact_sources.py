"""Every relationship in a cascade says where it was recorded, of what type, as of
when, and links to the record; where none was recorded it says so (DG-034)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DRAWER = REPO_ROOT / "app" / "modules" / "intelligence" / "templates" / "intelligence" / "_provenance_drawer.html"
CORE = REPO_ROOT / "app" / "static" / "js" / "intelligence" / "core.js"

FIELDS = ("source_name", "kindLabel", "source_timestamp", "record_url", "record_name")


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"dg034-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Dg",
        last_name="Tester",
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


def _relationship(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _blank_relationship_dates(db_session, rel, created_at, updated_at):
    from sqlalchemy import text

    db_session.execute(
        text("UPDATE archimate_relationships SET created_at = :c, updated_at = :u, created_by_id = NULL WHERE id = :i"),
        {"c": created_at, "u": updated_at, "i": rel.id},
    )
    db_session.commit()


def _cascade_relation(client, login_as, user, element_id):
    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/impact/{element_id}")
    assert resp.status_code == 200
    rows = resp.get_json()["data"]["rows"]
    assert rows
    return rows[0]["relation"]


def test_cascade_row_with_a_recorded_source_carries_the_four_fields(app, db_session, make_org, client, login_as):
    org = make_org("dg034-known")
    user = _user(db_session, org.id)
    ledger = _element(db_session, org.id, "Calibration Ledger")
    lookup = _element(db_session, org.id, "Calibration Lookup")
    rel = _relationship(db_session, org.id, ledger, lookup)
    rel.created_by_id = user.id
    db_session.commit()

    relation = _cascade_relation(client, login_as, user, ledger.id)
    for field in FIELDS:
        assert field in relation, field
    # The relationship records who drew it and when: both are shown.
    assert relation["source_name"] == "Drawn by Dg Tester"
    assert relation["source_timestamp"]
    assert relation["source_timestamp"][:10] == (rel.updated_at or rel.created_at).date().isoformat()
    assert relation["record_name"] == "Calibration Ledger"
    assert relation["kindLabel"] == "serving"
    assert relation["derived"] is False
    assert "source_confidence" in relation
    assert relation["record_url"] and relation["record_url"].startswith("/")


def test_cascade_row_with_only_a_date_keeps_the_date_and_no_recorder(app, db_session, make_org, client, login_as):
    import datetime

    org = make_org("dg034-dateonly")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "Date Only A")
    b = _element(db_session, org.id, "Date Only B")
    rel = _relationship(db_session, org.id, a, b)
    _blank_relationship_dates(db_session, rel, datetime.datetime(2026, 3, 4, 9, 30), None)

    relation = _cascade_relation(client, login_as, user, a.id)
    assert relation["source_name"] is None
    assert relation["source_timestamp"] and relation["source_timestamp"].startswith("2026-03-04")
    assert relation["record_name"] == "Date Only A"


def test_cascade_row_with_neither_date_nor_recorder_carries_neither(app, db_session, make_org, client, login_as):
    org = make_org("dg034-neither")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "Neither A")
    b = _element(db_session, org.id, "Neither B")
    rel = _relationship(db_session, org.id, a, b)
    _blank_relationship_dates(db_session, rel, None, None)

    relation = _cascade_relation(client, login_as, user, a.id)
    assert relation["source_name"] is None
    assert relation["source_timestamp"] is None


def test_cascade_row_without_a_source_carries_a_null_source_record(app, db_session, make_org):
    from app.modules.intelligence.services.explanation import attach_relationship_sources

    org = make_org("dg034-unknown")
    rows = [{"element_id": 1, "relation": {"kind": "derived", "type": "Serving", "chain": [987654321],
                                            "computed_at": None}}]
    attach_relationship_sources(org.id, rows)
    relation = rows[0]["relation"]
    assert relation["source_name"] is None
    assert relation["record_url"] is None
    assert relation["kindLabel"] == "serving"
    assert relation["source_timestamp"] is None
    assert relation["derived"] is True


def test_another_organisations_relationship_is_never_named(app, db_session, make_org):
    from app.modules.intelligence.services.explanation import attach_relationship_sources

    mine = make_org("dg034-mine")
    theirs = make_org("dg034-theirs")
    a = _element(db_session, theirs.id, "Their A")
    b = _element(db_session, theirs.id, "Their B")
    rel = _relationship(db_session, theirs.id, a, b)
    db_session.commit()
    rows = [{"element_id": b.id, "relation": {"kind": "explicit", "type": "Serving", "chain": [rel.id]}}]
    attach_relationship_sources(mine.id, rows)
    assert rows[0]["relation"]["source_name"] is None
    assert rows[0]["relation"]["record_url"] is None


def test_the_why_dialog_shows_each_field_or_says_source_not_recorded():
    template = DRAWER.read_text(encoding="utf-8")
    assert "data-why-source" in template
    assert "Source not recorded" in template
    assert "Worked out from the model" in template
    # "Source not recorded" shows only when neither a recorder nor a date is recorded
    not_recorded = re.search(r'x-show="([^"]*)"[^>]*>Source not recorded', template)
    assert not_recorded, "Source not recorded line missing"
    assert "!drawer.detail.sourceName" in not_recorded.group(1)
    assert "!drawer.detail.asOf" in not_recorded.group(1)
    for hook in ("data-source-name", "data-source-type", "data-source-as-of", "data-source-link"):
        assert hook in template, hook
    # the four values are read from the row's detail, filled by core.js
    script = CORE.read_text(encoding="utf-8")
    for key in ("sourceName", "sourceUrl", "relationshipType", "asOf"):
        assert re.search(r"\b" + key + r"\b", script), key
    for field in FIELDS:
        assert field in script, field
