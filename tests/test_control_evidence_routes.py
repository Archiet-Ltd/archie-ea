"""Recording control tests and evidence, and accepting connector evidence.

Signed-in clients of two real organisations drive the routes; the connector
items come from a recorded fixture and reach the model only through the one
approval queue.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests import _control_evidence_world as w

pytestmark = pytest.mark.usefixtures("db_session")

FIXTURE = Path(__file__).parent / "fixtures" / "control_evidence" / "connector_evidence_items.json"
BASE = "/dashboard/api/compliance/mappings"


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("rt-a"), make_org("rt-b")
    fw = w.framework(db_session)
    comp_a, _ = w.application(db_session, org_a.id, "A Ledger")
    comp_b, _ = w.application(db_session, org_b.id, "B Ledger")
    row_a = w.adopted(db_session, org_a.id, w.control(db_session, fw, "RT-A"), comp_a)
    row_b = w.adopted(db_session, org_b.id, w.control(db_session, fw, "RT-B"), comp_b)
    user_a = w.user(db_session, org_a.id, "Alice", first_name="Alice", last_name="Adams")
    user_a2 = w.user(db_session, org_a.id, "Amir", first_name="Amir", last_name="Ahmed")
    user_b = w.user(db_session, org_b.id, "Bob", first_name="Bob", last_name="Brown")
    db_session.commit()
    return type("World", (), dict(
        org_a=org_a, org_b=org_b, fw=fw, comp_a=comp_a, comp_b=comp_b,
        row_a=row_a, row_b=row_b, user_a=user_a, user_a2=user_a2, user_b=user_b,
    ))


def _post_test(client, login_as, user, row_id, **body):
    login_as(client, user)
    payload = {"test_kind": "design", "result": "effective"}
    payload.update(body)
    return client.post(f"{BASE}/{row_id}/tests", json=payload)


# --------------------------------------------------------------------- #
# Control tests                                                          #
# --------------------------------------------------------------------- #


def test_a_compliance_user_records_a_test_and_it_is_listed_after_a_reload(
    db_session, client, login_as, world
):
    first = _post_test(client, login_as, world.user_a, world.row_a.id, test_kind="design")
    second = _post_test(
        client, login_as, world.user_a, world.row_a.id, test_kind="operating", result="effective"
    )
    assert first.status_code == 201 and second.status_code == 201

    login_as(client, world.user_a)
    listing = client.get(f"{BASE}/{world.row_a.id}/tests")
    body = listing.get_json()
    assert listing.status_code == 200
    assert body["effectiveness"] == "effective"
    assert {t["test_kind"] for t in body["tests"]} == {"design", "operating"}
    assert all(t["tested_by"] == "Alice Adams" for t in body["tests"])

    later = _post_test(
        client, login_as, world.user_a, world.row_a.id, test_kind="operating",
        result="ineffective", notes="Sample of 25 failed",
    )
    assert later.status_code == 201
    login_as(client, world.user_a)
    again = client.get(f"{BASE}/{world.row_a.id}/tests").get_json()
    assert again["effectiveness"] == "ineffective"


def test_a_test_with_an_unknown_kind_or_result_is_refused_and_writes_nothing(
    db_session, client, login_as, world
):
    from app.models.control_test import ControlTest

    bad_kind = _post_test(client, login_as, world.user_a, world.row_a.id, test_kind="annual")
    bad_result = _post_test(client, login_as, world.user_a, world.row_a.id, result="passed")

    assert bad_kind.status_code == 400 and bad_result.status_code == 400
    assert ControlTest.query.filter_by(organization_id=world.org_a.id).count() == 0


def test_another_organisations_row_is_a_404_for_a_test_and_for_the_list(
    db_session, client, login_as, world
):
    from app.models.control_test import ControlTest

    foreign = _post_test(client, login_as, world.user_a, world.row_b.id)
    assert foreign.status_code == 404

    login_as(client, world.user_a)
    foreign_list = client.get(f"{BASE}/{world.row_b.id}/tests")
    assert foreign_list.status_code == 404

    assert ControlTest.query.filter_by(organization_id=world.org_b.id).count() == 0
    assert ControlTest.query.filter_by(application_control_id=world.row_b.id).count() == 0


def test_a_foreign_row_whose_id_equals_one_of_our_application_ids_is_still_a_404(
    db_session, client, login_as, world
):
    import random

    from app.models.application_portfolio import ApplicationComponent
    from app.models.control_test import ControlTest

    shared_id = random.randint(1_000_000_000, 2_000_000_000)
    ours = ApplicationComponent(
        id=shared_id, name="Decoy app", organization_id=world.org_a.id,
        archimate_element_id=w.element(db_session, world.org_a.id, "Decoy app").id,
    )
    db_session.add(ours)
    db_session.flush()
    foreign = w.adopted(
        db_session, world.org_b.id, w.control(db_session, world.fw, "RT-DECOY"), world.comp_b,
        id=shared_id,
    )
    db_session.commit()
    assert foreign.id == ours.id and foreign.organization_id == world.org_b.id

    refused = _post_test(client, login_as, world.user_a, shared_id)
    evidence = client.put(f"{BASE}/{shared_id}", json={"evidence_url": "https://e.example/x"})

    assert refused.status_code == 404 and evidence.status_code == 404
    assert ControlTest.query.filter_by(application_control_id=shared_id).count() == 0
    db_session.expire_all()
    assert foreign.evidence_url is None


def test_a_foreign_row_cannot_take_evidence_or_an_owner(db_session, client, login_as, world):
    login_as(client, world.user_a)
    refused = client.put(
        f"{BASE}/{world.row_b.id}",
        json={"evidence_url": "https://e.example/x", "evidence_owner_id": world.user_a.id},
    )
    assert refused.status_code == 404
    db_session.expire_all()
    assert world.row_b.evidence_url is None and world.row_b.evidence_owner_id is None


def test_with_no_active_organisation_the_writes_refuse_and_the_list_is_empty(
    app, db_session, client, login_as, world, monkeypatch
):
    from app.models.control_test import ControlTest
    import app.application_mgmt.compliance_routes as routes

    monkeypatch.setattr(routes, "current_org_id", lambda: None)
    login_as(client, world.user_a)

    created = client.post(f"{BASE}/{world.row_a.id}/tests", json={"test_kind": "design", "result": "effective"})
    evidence = client.put(f"{BASE}/{world.row_a.id}", json={"evidence_url": "https://e.example/x"})
    listing = client.get(f"{BASE}/{world.row_a.id}/tests")

    assert created.status_code in (403, 404)
    assert evidence.status_code in (403, 404)
    assert listing.status_code in (403, 404)
    assert ControlTest.query.count() == 0
    db_session.expire_all()
    assert world.row_a.evidence_url is None


def test_a_persona_without_a_write_role_cannot_record_a_test(db_session, client, login_as, world):
    from app.models.control_test import ControlTest

    reader = w.user(db_session, world.org_a.id, "Reader", enterprise_role="finance", role_name="Viewer")
    db_session.commit()

    refused = _post_test(client, login_as, reader, world.row_a.id)

    assert refused.status_code == 403
    assert ControlTest.query.filter_by(organization_id=world.org_a.id).count() == 0


# --------------------------------------------------------------------- #
# Evidence written by a person                                           #
# --------------------------------------------------------------------- #


def test_evidence_validity_and_owner_are_saved_through_the_existing_writer(
    db_session, client, login_as, world
):
    login_as(client, world.user_a)
    saved = client.put(
        f"{BASE}/{world.row_a.id}",
        json={
            "evidence_url": "https://e.example/policy",
            "evidence_valid_until": "2027-06-30",
            "evidence_owner_id": world.user_a2.id,
        },
    )
    assert saved.status_code == 200, saved.get_data(as_text=True)
    db_session.expire_all()
    assert world.row_a.evidence_url == "https://e.example/policy"
    assert world.row_a.evidence_valid_until == datetime(2027, 6, 30, 23, 59, 59), "a bare date holds through that day"
    assert world.row_a.evidence_source == "person"
    assert world.row_a.evidence_collected_at is not None
    assert world.row_a.evidence_owner_id == world.user_a2.id


def test_assigning_an_owner_alone_leaves_the_evidence_as_it_was(db_session, client, login_as, world):
    world.row_a.evidence_url = "https://e.example/keep"
    world.row_a.evidence_source = "connector"
    db_session.commit()

    login_as(client, world.user_a)
    saved = client.put(f"{BASE}/{world.row_a.id}", json={"evidence_owner_id": world.user_a2.id})

    assert saved.status_code == 200
    db_session.expire_all()
    assert world.row_a.evidence_url == "https://e.example/keep"
    assert world.row_a.evidence_source == "connector"
    assert world.row_a.evidence_owner_id == world.user_a2.id


def test_an_owner_from_another_organisation_or_a_bad_date_is_refused(db_session, client, login_as, world):
    login_as(client, world.user_a)
    foreign_owner = client.put(f"{BASE}/{world.row_a.id}", json={"evidence_owner_id": world.user_b.id})
    bad_date = client.put(f"{BASE}/{world.row_a.id}", json={"evidence_valid_until": "next month"})
    bad_link = client.put(f"{BASE}/{world.row_a.id}", json={"evidence_url": "javascript:alert(1)"})

    assert foreign_owner.status_code == 400
    assert bad_date.status_code == 400
    assert bad_link.status_code == 400
    db_session.expire_all()
    assert world.row_a.evidence_owner_id is None and world.row_a.evidence_url is None


# --------------------------------------------------------------------- #
# Connector evidence through the approval queue                          #
# --------------------------------------------------------------------- #


def _fixture_items():
    data = json.loads(FIXTURE.read_text())
    return data["source_system"], data["items"]


def _propose_all(db_session, world, proposer):
    from app.modules.compliance.services.evidence_gap_service import propose_control_evidence

    source_system, items = _fixture_items()
    fw = world.fw
    rows = {}
    approvals = []
    for item in items:
        row = w.adopted(
            db_session, world.org_a.id, w.control(db_session, fw, item["control_code"]), world.comp_a
        )
        rows[item["control_code"]] = row
        approvals.append(propose_control_evidence(
            world.org_a.id, row.id,
            evidence_url=item["evidence_url"],
            collected_at=datetime.fromisoformat(item["collected_at"]),
            valid_until=datetime.fromisoformat(item["valid_until"]),
            source_system=source_system,
            summary=item["summary"],
            proposed_by_user_id=proposer.id,
        ))
    db_session.commit()
    return rows, approvals


def test_three_connector_items_become_three_pending_proposals_and_nothing_is_written(
    db_session, world
):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    rows, approvals = _propose_all(db_session, world, world.user_a)

    pending = AIChatCRUDApproval.query.filter_by(
        organization_id=world.org_a.id, entity_type="control_evidence", status=ApprovalStatus.PENDING,
    ).all()
    assert len(pending) == 3 and len(approvals) == 3
    assert {p.entity_id for p in pending} == {r.id for r in rows.values()}
    assert all(p.operation_type == "update" for p in pending)
    db_session.expire_all()
    for row in rows.values():
        assert row.evidence_url is None and row.evidence_source is None


def test_approving_one_writes_its_evidence_with_the_connector_as_source(db_session, world):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    rows, approvals = _propose_all(db_session, world, world.user_a)
    source_system, items = _fixture_items()

    result = AIChatApprovalService(user_id=world.user_a2.id).approve_and_execute(approvals[0].id)

    assert result.get("success") is True, result
    db_session.expire_all()
    first = rows[items[0]["control_code"]]
    assert first.evidence_url == items[0]["evidence_url"]
    assert first.evidence_source == "connector"
    assert first.evidence_collected_at == datetime.fromisoformat(items[0]["collected_at"])
    assert first.evidence_valid_until == datetime.fromisoformat(items[0]["valid_until"])
    second = rows[items[1]["control_code"]]
    assert second.evidence_url is None, "an unapproved proposal writes nothing"


def test_the_proposer_cannot_approve_their_own_proposal(db_session, world):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    rows, approvals = _propose_all(db_session, world, world.user_a)

    result = AIChatApprovalService(user_id=world.user_a.id).approve_and_execute(approvals[0].id)

    assert result["success"] is False
    db_session.expire_all()
    assert all(r.evidence_url is None for r in rows.values())


def test_a_refused_proposal_can_never_be_accepted_afterwards(db_session, world):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.modules.compliance.services.evidence_gap_service import accept_control_evidence

    rows, approvals = _propose_all(db_session, world, world.user_a)
    refused = AIChatApprovalService(user_id=world.user_a2.id).reject_approval(approvals[0].id, "not ours")
    assert refused.get("success") is True, refused

    through_queue = AIChatApprovalService(user_id=world.user_a2.id).approve_and_execute(approvals[0].id)
    direct = accept_control_evidence(approvals[0].id, world.org_a.id, world.user_a2.id)

    assert through_queue["success"] is False
    assert direct["success"] is False
    db_session.expire_all()
    assert all(r.evidence_url is None for r in rows.values())


def test_a_proposal_whose_control_row_belongs_to_another_organisation_is_refused(db_session, world):
    from app.modules.ai_chat.services.ai_chat_approval_service import (
        AIChatApprovalService,
        create_approval_record,
    )
    from app.modules.compliance.services.evidence_gap_service import (
        accept_control_evidence,
        propose_control_evidence,
    )

    # Proposing against B's row as A is refused at the door.
    with pytest.raises(ValueError):
        propose_control_evidence(
            world.org_a.id, world.row_b.id, evidence_url="https://e.example/x",
            collected_at=datetime(2026, 10, 1), valid_until=datetime(2027, 1, 1),
            source_system="Scanner", summary="x", proposed_by_user_id=world.user_a.id,
        )

    # A hand-built proposal in A's queue naming B's row is refused at execution.
    decoy = create_approval_record(
        organization_id=world.org_a.id, operation_type="update", entity_type="control_evidence",
        entity_id=world.row_b.id, summary="decoy", user_id=world.user_a.id,
        operation_payload={
            "evidence_url": "https://e.example/decoy", "collected_at": "2026-10-01T00:00:00",
            "valid_until": "2027-01-01T00:00:00", "source_system": "Scanner", "summary": "decoy",
        },
    )
    db_session.commit()

    direct = accept_control_evidence(decoy.id, world.org_a.id, world.user_a2.id)
    queued = AIChatApprovalService(user_id=world.user_a2.id).approve_and_execute(decoy.id)

    assert direct["success"] is False and queued["success"] is False
    db_session.expire_all()
    assert world.row_b.evidence_url is None


def test_accepting_directly_without_the_queue_writes_nothing(db_session, world):
    from app.modules.compliance.services.evidence_gap_service import accept_control_evidence

    rows, approvals = _propose_all(db_session, world, world.user_a)

    direct = accept_control_evidence(approvals[0].id, world.org_a.id, world.user_a2.id)

    assert direct["success"] is False
    db_session.expire_all()
    assert all(r.evidence_url is None for r in rows.values())


def test_a_user_of_another_organisation_cannot_approve_this_organisations_proposal(db_session, world):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    rows, approvals = _propose_all(db_session, world, world.user_a)

    result = AIChatApprovalService(user_id=world.user_b.id).approve_and_execute(approvals[0].id)

    assert result["success"] is False
    db_session.expire_all()
    assert all(r.evidence_url is None for r in rows.values())


def test_a_proposal_with_a_non_web_link_is_refused_at_proposal_time(db_session, world):
    from app.modules.compliance.services.evidence_gap_service import propose_control_evidence

    with pytest.raises(ValueError):
        propose_control_evidence(
            world.org_a.id, world.row_a.id, evidence_url="file:///etc/passwd",
            collected_at=datetime(2026, 10, 1), valid_until=None, source_system="Scanner", summary="x",
        )
