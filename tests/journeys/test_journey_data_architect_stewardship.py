"""Journey: a data architect stewards and classifies a data entity.

The data_architect persona (see test_journey_security_and_data_architect.py
for why this role was promoted from charter-only) owns data architecture,
lineage and stewardship. This journey proves the persona can complete its two
writes end to end over real HTTP, not just reach its pages:

* assign a steward to a critical data entity with the picker, reload, and see
  the steward persisted and the entity gone from the no-steward list;
* propose a classification label for an entity that has a downstream lineage
  hop, have it accepted from the approval inbox, reload, and see the label on
  both the entity and the downstream entity.

A stewardship write is a change to organisation data, so the persona must be
able to make it and observe it. These tests fail on plain main, where the
steward routes, the no-steward list and the classification proposal do not
exist.
"""

import re
import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def _uid():
    return uuid.uuid4().hex[:8]


def _critical_entity(db, org_id, name):
    """A critical data entity: its domain criticality is 'mission_critical',
    the definition of "critical" used for the no-steward list."""
    from app.models.process_data import DataDomain, DataEntity

    domain = DataDomain(
        name=f"Critical Domain {_uid()}",
        criticality="mission_critical",
        organization_id=org_id,
    )
    db.session.add(domain)
    db.session.flush()
    entity = DataEntity(name=name, domain_id=domain.id, organization_id=org_id)
    db.session.add(entity)
    db.session.flush()
    return entity


def _lineage_hop(db, org_id, source_entity, target_entity):
    """A DataLineage edge source_entity -> target_entity."""
    from app.models.all_missing_models import DataLineage, data_lineage_entities

    lineage = DataLineage(name=f"Lineage {_uid()}", organization_id=org_id)
    db.session.add(lineage)
    db.session.flush()
    db.session.execute(
        data_lineage_entities.insert().values(
            data_lineage_id=lineage.id,
            data_entity_id=source_entity.id,
            participation_type="source",
        )
    )
    db.session.execute(
        data_lineage_entities.insert().values(
            data_lineage_id=lineage.id,
            data_entity_id=target_entity.id,
            participation_type="target",
        )
    )
    db.session.flush()
    return lineage


def test_data_architect_assigns_a_steward_and_the_entity_leaves_the_no_steward_list(app, client):
    """The persona's first write: steward assignment via the picker.

    A critical entity with no steward appears on the no-steward list; the
    data architect assigns a person from the picker; after a reload the
    steward is shown and the entity is gone from the list.
    """
    from app import db

    with app.app_context():
        org_id = make_org(db, "StewJourney")
        entity = _critical_entity(db, org_id, f"Customer Master {_uid()}")
        steward_id = make_user(
            db, org_id, "stew", enterprise_role="data_architect",
            role_name="Architect",
        )
        architect_id = make_user(
            db, org_id, "arch", enterprise_role="data_architect",
            role_name="Architect",
        )
        db.session.commit()
        entity_name = entity.name

    login(client, architect_id)

    # The critical entity with no steward appears on the no-steward list.
    no_steward = client.get("/data-governance/no-steward")
    assert no_steward.status_code == 200, no_steward.data[:300]
    assert entity_name.encode() in no_steward.data

    # The entity detail page exposes the steward picker.
    detail = client.get(f"/data-governance/entities/{entity.id}")
    assert detail.status_code == 200, detail.data[:300]
    assert b"No steward assigned yet" in detail.data
    assert b'"steward-picker"' in detail.data

    # Assign a steward with the picker.
    assign = client.post(
        f"/data-governance/entities/{entity.id}/steward",
        data={"user_id": steward_id},
        follow_redirects=True,
    )
    assert assign.status_code == 200
    assert b"Steward assigned" in assign.data

    # Reload: the steward is now shown.
    after = client.get(f"/data-governance/entities/{entity.id}")
    assert after.status_code == 200
    assert b"No steward assigned yet" not in after.data
    assert b"Remove" in after.data  # a steward row with its remove action is rendered

    # The entity is off the no-steward list.
    no_steward_after = client.get("/data-governance/no-steward")
    assert no_steward_after.status_code == 200
    assert entity_name.encode() not in no_steward_after.data


def test_data_architect_classification_propagates_to_a_downstream_lineage_hop(app, client):
    """The persona's second write: propose, then the approval inbox accepts.

    The data architect proposes a classification for an entity that has a
    downstream lineage hop. A second architect accepts it from the approval
    inbox (self-approval is refused). After a reload the label shows on the
    entity and on the downstream entity.
    """
    from app import db

    with app.app_context():
        org_id = make_org(db, "ClassJourney")

        domain = _critical_entity(db, org_id, f"Sales Source {_uid()}")
        downstream = _critical_entity(db, org_id, f"Sales Downstream {_uid()}")
        _lineage_hop(db, org_id, domain, downstream)
        db.session.commit()
        source_id = domain.id
        downstream_id = downstream.id

        proposer_id = make_user(
            db, org_id, "prop", enterprise_role="data_architect",
            role_name="Architect",
        )
        approver_id = make_user(
            db, org_id, "appr", enterprise_role="data_architect",
            role_name="Architect",
        )
        db.session.commit()

    login(client, proposer_id)

    # Propose a classification label. The proposal is an approval row.
    classify = client.post(
        f"/data-governance/entities/{source_id}/classify",
        data={"classification_label": "confidential"},
        follow_redirects=True,
    )
    assert classify.status_code == 200
    assert b"Classification proposal created" in classify.data

    # Pull the approval id out of the flashed message so the approver (a
    # different user) can accept it from the approval inbox.
    match = re.search(r"approval #(\d+)", classify.get_data(as_text=True))
    assert match, "the proposal flash does not name the approval id"
    approval_id = int(match.group(1))

    # The approval inbox lists the pending proposal for the approver.
    login(client, approver_id)
    inbox = client.get("/ai-chat/approvals/inbox")
    assert inbox.status_code == 200, inbox.data[:300]
    queue = client.get("/ai-chat/approvals/queue")
    assert queue.status_code == 200
    queued_ids = {a["id"] for a in queue.get_json()["approvals"]}
    assert approval_id in queued_ids

    # Accept it from the approval inbox.
    accept = client.post(f"/ai-chat/approvals/{approval_id}/approve")
    assert accept.status_code == 200, accept.data[:300]
    assert accept.get_json()["success"] is True

    # Reload: the label shows on the entity and on the downstream hop.
    source_detail = client.get(f"/data-governance/entities/{source_id}")
    assert source_detail.status_code == 200
    assert b"confidential" in source_detail.data

    downstream_detail = client.get(f"/data-governance/entities/{downstream_id}")
    assert downstream_detail.status_code == 200
    assert b"confidential" in downstream_detail.data
