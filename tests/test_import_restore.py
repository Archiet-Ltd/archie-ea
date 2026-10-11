"""Restore an organisation to how it stood before a model import.

Covers the snapshot taken by the one OEF import engine, the preview, the
restore itself (organisation-scoped, recorded in the audit trail), a chosen
later change applied again, and a 4,000-relationship import undone.
"""
import json
import os
import uuid
from datetime import UTC, datetime

import pytest

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - restore tests need PostgreSQL",
)
pytestmark = db_required

OEF_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-restore">
  <name xml:lang="en">{model}</name>
  <elements>
    <element identifier="A" xsi:type="Node"><name xml:lang="en">{prefix} Node A</name>
      <documentation>{desc}</documentation></element>
    <element identifier="B" xsi:type="Node"><name xml:lang="en">{prefix} Node B</name></element>
    <element identifier="C" xsi:type="Node"><name xml:lang="en">{prefix} Service C</name></element>
  </elements>
  <relationships>
    <relationship identifier="R1" source="C" target="A" xsi:type="Association"/>
    <relationship identifier="R2" source="A" target="B" xsi:type="Association"/>
  </relationships>
</model>
"""


def oef(prefix, model="Restore fixture", desc="from file"):
    return OEF_TEMPLATE.format(prefix=prefix, model=model, desc=desc).encode("utf-8")


def make_user(db_session, org, role="enterprise_architect", label="u"):
    from app.models import User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org.id, confirmed=True, enterprise_role=role,
    )
    user.password = "x"
    db_session.add(user)
    db_session.flush()
    return user


def post_import(client, login_as, user, xml, strategy="skip_duplicates"):
    import io

    login_as(client, user)
    resp = client.post(
        "/architecture/import/oef",
        data={"oef_file": (io.BytesIO(xml), "model.xml"), "strategy": strategy},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return resp.get_json()


def counts(org_id):
    from app import db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    import sqlalchemy as sa

    el = db.session.execute(sa.select(sa.func.count()).select_from(ArchiMateElement.__table__).where(
        ArchiMateElement.__table__.c.organization_id == org_id)).scalar()
    rel = db.session.execute(sa.select(sa.func.count()).select_from(ArchiMateRelationship.__table__).where(
        ArchiMateRelationship.__table__.c.organization_id == org_id)).scalar()
    return el, rel


def restore_point_id(org_id):
    from app.models.import_audit import ImportSessionLog

    row = (ImportSessionLog.query.filter(ImportSessionLog.organization_id == org_id)
           .order_by(ImportSessionLog.id.desc()).first())
    assert row is not None
    return row.id


def restore_json(client, login_as, user, log_id, reapply=()):
    login_as(client, user)
    return client.post(
        f"/architecture/import/oef/restore-points/{log_id}/restore",
        json={"reapply": list(reapply)},
    )


def test_import_takes_a_snapshot_on_the_import_session_log(client, db_session, login_as, make_org):
    from app.models.import_audit import ImportSessionLog

    org = make_org("snap")
    user = make_user(db_session, org)
    result = post_import(client, login_as, user, oef("Snap"))
    assert result["created"] == 3 and result["relationships_created"] == 2

    log = db_session.get(ImportSessionLog, restore_point_id(org.id))
    assert log.organization_id == org.id and log.user_id == user.id
    assert log.status == "completed" and log.is_rolled_back is False
    snap = log.snapshot_data
    assert sorted(snap["created_element_ids"]) == sorted(result["created_ids"])
    assert len(snap["created_relationship_ids"]) == 2
    assert snap["model_id"] == result["model_id"]


def test_preview_names_what_would_change_and_writes_nothing(client, db_session, login_as, make_org):
    org = make_org("prev")
    user = make_user(db_session, org)
    post_import(client, login_as, user, oef("Prev"))
    before = counts(org.id)
    log_id = restore_point_id(org.id)

    login_as(client, user)
    resp = client.get(f"/architecture/import/oef/restore-points/{log_id}", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["counts"]["elements_removed"] == 3
    assert body["counts"]["relationships_removed"] == 2
    assert sorted(e["name"] for e in body["elements_removed"]) == ["Prev Node A", "Prev Node B", "Prev Service C"]
    assert body["can_restore"] is True
    assert counts(org.id) == before

    login_as(client, user)
    page = client.get(f"/architecture/import/oef/restore-points/{log_id}", headers={"Accept": "text/html"})
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Prev Node A" in html and "btn-confirm-restore" in html

    login_as(client, user)
    listing = client.get("/architecture/import/oef/restore-points", headers={"Accept": "text/html"})
    assert listing.status_code == 200 and "btn-review-restore" in listing.get_data(as_text=True)


def test_restore_in_one_organisation_never_touches_another(client, db_session, login_as, make_org):
    from app.models.audit_log import AuditLog
    from app.models.import_audit import ImportSessionLog

    org_a, org_b = make_org("a"), make_org("b")
    user_a, user_b = make_user(db_session, org_a), make_user(db_session, org_b)
    post_import(client, login_as, user_a, oef("Same"))
    post_import(client, login_as, user_b, oef("Same"))
    log_a, log_b = restore_point_id(org_a.id), restore_point_id(org_b.id)
    assert counts(org_a.id) == (3, 2) and counts(org_b.id) == (3, 2)

    # A cannot even see B's restore point, let alone restore it.
    login_as(client, user_a)
    assert client.get(f"/architecture/import/oef/restore-points/{log_b}",
                      headers={"Accept": "application/json"}).status_code == 404
    assert restore_json(client, login_as, user_a, log_b).status_code == 404
    assert counts(org_b.id) == (3, 2)

    resp = restore_json(client, login_as, user_a, log_a)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["elements_removed"] == 3
    assert counts(org_a.id) == (0, 0)
    assert counts(org_b.id) == (3, 2), "B's rows survive A's restore untouched"
    assert db_session.get(ImportSessionLog, log_b).is_rolled_back is False
    assert db_session.get(ImportSessionLog, log_a).is_rolled_back is True

    # The restore is in A's audit trail, and only there.
    a_entries = AuditLog.query.filter(AuditLog.org_predicate(org_a.id), AuditLog.action == "restore").all()
    b_entries = AuditLog.query.filter(AuditLog.org_predicate(org_b.id), AuditLog.action == "restore").all()
    assert len(a_entries) == 1 and a_entries[0].record_id == log_a and a_entries[0].user_id == user_a.id
    assert b_entries == []
    assert a_entries[0].row_hash, "sealed into the organisation's audit chain"

    # Restoring twice is refused.
    assert restore_json(client, login_as, user_a, log_a).status_code == 409


def test_restore_is_refused_for_other_roles_and_anonymous(client, db_session, login_as, make_org):
    org = make_org("gate")
    ea = make_user(db_session, org)
    post_import(client, login_as, ea, oef("Gate"))
    log_id = restore_point_id(org.id)

    other = make_user(db_session, org, role="procurement", label="proc")
    login_as(client, other)
    assert client.get("/architecture/import/oef/restore-points").status_code == 403
    assert restore_json(client, login_as, other, log_id).status_code == 403
    assert counts(org.id) == (3, 2)

    anon = client.application.test_client()
    assert anon.get("/architecture/import/oef/restore-points").status_code in (302, 401)


def test_a_chosen_later_change_is_applied_again_and_an_unchosen_one_is_not(client, db_session, login_as, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.audit_log import AuditLog

    org = make_org("reapply")
    user = make_user(db_session, org)
    # A stored element the import will overwrite, and a second one it also overwrites.
    for name, desc in (("Re Node A", "original A"), ("Re Node B", "original B")):
        db_session.add(ArchiMateElement(name=name, type="Node", layer="Technology",
                                        description=desc, organization_id=org.id))
    db_session.flush()

    post_import(client, login_as, user, oef("Re", desc="imported A"), strategy="update_existing")
    node_a = ArchiMateElement.query.filter_by(organization_id=org.id, name="Re Node A").one()
    node_b = ArchiMateElement.query.filter_by(organization_id=org.id, name="Re Node B").one()
    assert node_a.description == "imported A"
    log_id = restore_point_id(org.id)

    # Two edits after the import, through the element screen (recorded in the audit trail).
    for element, text in ((node_a, "edited A"), (node_b, "edited B")):
        login_as(client, user)
        r = client.post(f"/architecture/technology/Node/{element.id}/edit",
                        json={"name": element.name, "description": text})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]

    login_as(client, user)
    preview = client.get(f"/architecture/import/oef/restore-points/{log_id}",
                         headers={"Accept": "application/json"}).get_json()
    assert preview["counts"]["elements_removed"] == 1  # only Service C was created
    changes = {c["fields"]["description"]: c for c in preview["changes_since"]}
    assert changes["edited A"]["reappliable"] and changes["edited B"]["reappliable"]
    assert {r["description_before"] for r in preview["elements_reverted"]} == {"original A", "original B"}

    resp = restore_json(client, login_as, user, log_id, reapply=[changes["edited A"]["audit_id"]])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["changes_reapplied"] == 1

    db_session.expire_all()
    assert db_session.get(ArchiMateElement, node_a.id).description == "edited A"      # chosen: re-applied
    assert db_session.get(ArchiMateElement, node_b.id).description == "original B"    # not chosen: pre-import state
    assert ArchiMateElement.query.filter_by(organization_id=org.id, name="Re Service C").count() == 0
    assert AuditLog.query.filter(AuditLog.org_predicate(org.id), AuditLog.action == "restore").count() == 1


def test_a_change_not_offered_in_the_preview_cannot_be_forced_in(client, db_session, login_as, make_org):
    org_a, org_b = make_org("force-a"), make_org("force-b")
    user_a, user_b = make_user(db_session, org_a), make_user(db_session, org_b)
    post_import(client, login_as, user_a, oef("Fa"))
    post_import(client, login_as, user_b, oef("Fb"))
    resp = restore_json(client, login_as, user_a, restore_point_id(org_a.id), reapply=[987654321])
    assert resp.status_code == 400
    assert counts(org_a.id) == (3, 2)


def test_relationships_added_after_the_import_are_removed_and_listed(client, db_session, login_as, make_org):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    org = make_org("later")
    user = make_user(db_session, org)
    result = post_import(client, login_as, user, oef("Later"))
    own = ArchiMateElement(name="Mine", type="Node", layer="Technology", organization_id=org.id)
    db_session.add(own)
    db_session.flush()
    db_session.add(ArchiMateRelationship(type="Flow", source_id=own.id, target_id=result["created_ids"][0],
                                         organization_id=org.id))
    db_session.flush()

    log_id = restore_point_id(org.id)
    login_as(client, user)
    preview = client.get(f"/architecture/import/oef/restore-points/{log_id}",
                         headers={"Accept": "application/json"}).get_json()
    assert preview["counts"]["later_relationships_removed"] == 1
    assert restore_json(client, login_as, user, log_id).status_code == 200
    assert counts(org.id) == (1, 0)  # "Mine" stays; the import and the link to it are gone


def test_domain_rows_the_import_created_are_removed_with_it(client, db_session, login_as, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("domain")
    user = make_user(db_session, org)
    xml = OEF_TEMPLATE.replace('xsi:type="Node"><name xml:lang="en">{prefix} Node A',
                               'xsi:type="ApplicationComponent"><name xml:lang="en">{prefix} Node A')
    post_import(client, login_as, user, xml.format(prefix="Dom", model="m", desc="d").encode())
    assert ApplicationComponent.query.filter_by(organization_id=org.id, name="Dom Node A").count() == 1
    resp = restore_json(client, login_as, user, restore_point_id(org.id))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["domain_rows_removed"] == 1
    assert ApplicationComponent.query.filter_by(organization_id=org.id, name="Dom Node A").count() == 0


def test_restore_is_blocked_when_a_domain_row_has_new_references(client, db_session, login_as, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.requirements import Requirement

    org = make_org("domain-block")
    user = make_user(db_session, org)
    xml = OEF_TEMPLATE.replace('xsi:type="Node"><name xml:lang="en">{prefix} Node A',
                               'xsi:type="ApplicationComponent"><name xml:lang="en">{prefix} Node A')
    post_import(client, login_as, user, xml.format(prefix="Block", model="m", desc="d").encode())
    imported = ApplicationComponent.query.filter_by(organization_id=org.id, name="Block Node A").one()
    db_session.add(Requirement(title="Needs the imported app", application_component_id=imported.id))
    db_session.flush()

    log_id = restore_point_id(org.id)
    login_as(client, user)
    preview = client.get(f"/architecture/import/oef/restore-points/{log_id}",
                         headers={"Accept": "application/json"}).get_json()
    assert preview["can_restore"] is False
    assert preview["blockers"] == [{"table": "requirements", "label": "requirements", "count": 1}]

    resp = restore_json(client, login_as, user, log_id)
    assert resp.status_code == 409
    assert "1 in requirements" in resp.get_data(as_text=True)
    assert ApplicationComponent.query.filter_by(organization_id=org.id, name="Block Node A").count() == 1


def test_preview_lists_one_blocker_entry_per_referencing_table(client, db_session, login_as, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.integration_metadata import SystemDependency
    from app.models.requirements import Requirement

    org = make_org("blocker-agg")
    user = make_user(db_session, org)
    result = post_import(client, login_as, user, oef("Agg"))
    imported_id = result["created_ids"][0]
    db_session.add(Requirement(
        title="Requirement with two links",
        archimate_element_id=imported_id,
        source_element_id=imported_id,
    ))
    db_session.add(SystemDependency(
        source_system_id=imported_id,
        target_system_id=imported_id,
        interface_id=imported_id,
        dependency_type="service",
    ))
    db_session.flush()

    log_id = restore_point_id(org.id)
    login_as(client, user)
    preview = client.get(f"/architecture/import/oef/restore-points/{log_id}",
                         headers={"Accept": "application/json"}).get_json()
    blockers = {item["table"]: item for item in preview["blockers"]}

    assert sorted(blockers) == ["requirements", "system_dependencies"]
    assert len(preview["blockers"]) == 2
    assert blockers["requirements"]["count"] == 2
    assert blockers["system_dependencies"]["count"] == 3
    assert db_session.get(ArchiMateElement, imported_id).organization_id == org.id


def test_a_faulty_import_of_4000_relationships_is_fully_undone(db_session, make_org, tenant_ctx):
    """Bulk inserts, not per-row commits: the fixture stands in for the import's writes."""
    import sqlalchemy as sa
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.import_audit import ImportSessionLog
    from app.models.audit_log import AuditLog
    from app.services import import_restore_service, import_snapshot_service as snaps

    el_t, rel_t = ArchiMateElement.__table__, ArchiMateRelationship.__table__
    orgs = {}
    for label in ("big-a", "big-b"):
        org = make_org(label)
        user = make_user(db_session, org)
        n_elements, n_rel = (200, 4000) if label == "big-a" else (20, 60)
        keep_id = db_session.execute(sa.insert(el_t).values(
            name=f"{label} kept", type="Node", layer="Technology", organization_id=org.id
        ).returning(el_t.c.id)).scalar()
        element_ids = [r[0] for r in db_session.execute(
            sa.insert(el_t).returning(el_t.c.id),
            [dict(name=f"{label} e{i}", type="Node", layer="Technology", organization_id=org.id)
             for i in range(n_elements)]).all()]
        rel_ids = [r[0] for r in db_session.execute(
            sa.insert(rel_t).returning(rel_t.c.id),
            [dict(type="Flow", source_id=element_ids[i % n_elements],
                  target_id=element_ids[(i * 7 + 1) % n_elements], organization_id=org.id)
             for i in range(n_rel)]).all()]
        log = ImportSessionLog(session_id=str(uuid.uuid4()), operation_type="import", user_id=user.id,
                               organization_id=org.id, import_source=snaps.IMPORT_SOURCE,
                               started_at=datetime.now(UTC), status="in_progress")
        db_session.add(log)
        db_session.flush()
        snapshot = snaps.ImportSnapshot(log, "create_all", label)
        for element_id in element_ids:
            snapshot.note_created_element(element_id)
        snapshot.note_created_relationships(rel_ids)
        snapshot.finish()
        orgs[label] = (org, user, log, keep_id)
    db_session.flush()

    org_a, user_a, log_a, keep_a = orgs["big-a"]
    org_b, _, log_b, _ = orgs["big-b"]
    assert counts(org_a.id) == (201, 4000) and counts(org_b.id) == (21, 60)

    with tenant_ctx(org_a.id):
        preview = import_restore_service.build_preview(org_a.id, log_a)
        assert preview["counts"]["relationships_removed"] == 4000
        assert preview["counts"]["elements_removed"] == 200 and preview["can_restore"]
        result = import_restore_service.restore(org_a.id, user_a.id, log_a.id)

    assert result["relationships_removed"] == 4000 and result["elements_removed"] == 200
    assert counts(org_a.id) == (1, 0)            # only the element that was there before
    assert db_session.get(ArchiMateElement, keep_a) is not None
    assert counts(org_b.id) == (21, 60)          # the other organisation is untouched
    assert db_session.get(ImportSessionLog, log_b.id).is_rolled_back is False
    entry = AuditLog.query.filter(AuditLog.org_predicate(org_a.id), AuditLog.action == "restore").one()
    assert entry.new_value["relationships_removed"] == 4000

    with tenant_ctx(org_b.id):  # A's restore point is not B's to restore
        with pytest.raises(import_restore_service.RestoreError) as err:
            import_restore_service.restore(org_b.id, user_a.id, log_a.id)
        assert err.value.status_code == 404


def test_editing_an_element_is_recorded_in_the_audit_trail(client, db_session, login_as, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.audit_log import AuditLog

    org = make_org("edit-audit")
    user = make_user(db_session, org)
    el = ArchiMateElement(name="Edit me", type="Node", layer="Technology", description="one", organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    login_as(client, user)
    assert client.post(
        f"/architecture/technology/Node/{el.id}/edit",
        json={"name": "Edit me", "description": "two"},
        headers={"User-Agent": "restore-test-agent"},
        environ_overrides={"REMOTE_ADDR": "198.51.100.8"},
    ).status_code == 200
    entry = AuditLog.query.filter(AuditLog.org_predicate(org.id), AuditLog.table_name == "archimate_elements",
                                  AuditLog.record_id == el.id).one()
    assert entry.action == "update"
    assert entry.old_value == {"description": "one"} and entry.new_value == {"description": "two"}
    assert entry.ip_address == "198.51.100.8"
    assert entry.user_agent == "restore-test-agent"


def test_a_chosen_later_custom_property_change_is_applied_again(client, db_session, login_as, make_org, app):
    from flask import g

    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture.routes.archimate_crud.routes import (
        _archimate_element_state,
        _record_element_update,
    )

    org = make_org("props")
    user = make_user(db_session, org)
    db_session.add(ArchiMateElement(
        name="Props Node A",
        type="Node",
        layer="Technology",
        description="before",
        custom_properties={"classification": "initial"},
        organization_id=org.id,
    ))
    db_session.flush()

    post_import(client, login_as, user, oef("Props", desc="imported"), strategy="update_existing")
    node = ArchiMateElement.query.filter_by(organization_id=org.id, name="Props Node A").one()
    log_id = restore_point_id(org.id)

    with app.test_request_context("/"):
        g.current_org_id = org.id
        before = _archimate_element_state(node, True)
        node.custom_properties = {"classification": "restricted", "owner": "EA"}
        after = _archimate_element_state(node, True)
        _record_element_update(before, after)
        db_session.flush()

    login_as(client, user)
    preview = client.get(f"/architecture/import/oef/restore-points/{log_id}",
                         headers={"Accept": "application/json"}).get_json()
    change = next(c for c in preview["changes_since"] if c["fields"].get("custom_properties"))
    assert change["reappliable"] is True
    assert change["fields"]["custom_properties"] == {"classification": "restricted", "owner": "EA"}

    restore = restore_json(client, login_as, user, log_id, reapply=[change["audit_id"]])
    assert restore.status_code == 200, restore.get_data(as_text=True)

    db_session.expire_all()
    restored = db_session.get(ArchiMateElement, node.id)
    assert restored.custom_properties == {"classification": "restricted", "owner": "EA"}
    assert restored.description == "before"


def test_a_member_on_the_default_title_cannot_restore(client, db_session, login_as, make_org):
    """The platform_admin title is the default for every member; it is not authority."""
    org = make_org("default-title")
    ea = make_user(db_session, org)
    post_import(client, login_as, ea, oef("DefaultTitle"))
    log_id = restore_point_id(org.id)
    before = counts(org.id)

    member = make_user(db_session, org, role="platform_admin", label="member")
    login_as(client, member)
    assert client.get("/architecture/import/oef/restore-points").status_code == 403
    assert restore_json(client, login_as, member, log_id).status_code == 403
    assert counts(org.id) == before
