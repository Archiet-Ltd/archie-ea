"""Every writer of an approval record is brought into the one review: none of its records waits for a person.

The callers of ``create_approval_record`` are enumerated from the code, so a writer added later fails here until
it is listed below and shown to settle. Each source a writer uses is then written for a real organisation and
settled by the server: applied, linked or declined, never left waiting. A second organisation's record of the
same kind is left alone.
"""

import ast
import json
import uuid
from pathlib import Path

import pytest

from tests.test_proposals_settle_without_a_person import Reviewer, _administrator

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parent.parent

# (file, function that calls create_approval_record) -> what it writes: a source the review settles as a
# proposal, or "person" for a record a named person asked for, which the review settles as that person.
WRITERS = {
    ("app/commands/backfill_review_queue_approvals.py", "run_backfill"): "source",
    ("app/services/solution_blueprint_service.py", "create_solution_blueprint_proposal"): "source",
    ("app/services/confidence_review_service.py", "add_to_review_queue"): "source",
    ("app/services/vendor_detection.py", "propose"): "source",
    ("app/services/vendor_detection.py", "_propose_architecture"): "source",
    ("app/services/connector_framework.py", "propose_connector_change"): "source",
    ("app/modules/ai_chat/services/ai_chat_approval_service.py", "create_pending_approval"): "person",
    ("app/connectors/github.py", "_propose_owner"): "source",
    ("app/connectors/github.py", "propose_relationships"): "source",
    ("app/connectors/github.py", "propose_technologies"): "source",
    ("app/connectors/github.py", "unmatched"): "source",
    ("app/modules/ai_chat/services/agent_runner.py", "_queue_approval"): "person",
    ("app/modules/ai_chat/services/agent_registry_service.py", "request_charter_change"): "person",
    ("app/modules/enterprise_genome/services/ai_system_classification.py", "propose_classification"): "person",
    ("app/modules/enterprise_genome/services/ai_system_classification.py", "propose_assessment"): "person",
    ("app/modules/enterprise_genome/services/ai_system_grounding.py", "propose_grounding_source"): "person",
    ("app/modules/intelligence/services/deadline_alert_service.py", "scan_organization_for_eol_alerts"): "source",
    ("app/modules/architecture/services/data_stewardship_service.py", "propose_classification"): "person",
    ("app/modules/organization/website_crawl/service.py", "_store"): "source",
}


def _callers():
    found = set()
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "create_approval_record(" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call) and getattr(inner.func, "id", getattr(inner.func, "attr", None))
                            == "create_approval_record") and node is _innermost(tree, inner):
                        found.add((path.relative_to(ROOT).as_posix(), node.name))
    return found


def _innermost(tree, call):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(inner is call for inner in ast.walk(node)):
                if best is None or any(inner is node for inner in ast.walk(best)):
                    best = node
    return best


def test_every_caller_of_create_approval_record_is_listed_here():
    found = _callers()
    unlisted = sorted(found - set(WRITERS))
    gone = sorted(set(WRITERS) - found)
    assert not unlisted, "a writer of approval records is not listed, so nothing shows it settles: %s" % unlisted
    assert not gone, "a listed writer no longer calls create_approval_record: %s" % gone


# What each distinct writer writes, as the records it makes look.
SOURCES = [
    "solution_blueprint_proposals", "github_connector", "connector_config", "review_queue_items",
    "relationship_suggestions", "matcher_service", "vendor_detection", "vendor_products", "website_crawler",
    "application_ownership", "arb_review_items",
]
PERSON_RECORDS = [
    ("update", "ai_system_classification"), ("update", "ai_system_assessment"),
    ("update", "ai_system_grounding_source"), ("update", "data_entity_classification"),
    ("agent_charter_change", "agent_registration"),
]


def _write(org_id, user_id=None, source=None, operation="review", entity="record"):
    from app import db
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    approval = create_approval_record(
        organization_id=org_id, operation_type=operation, entity_type=entity,
        summary="Test change %s" % uuid.uuid4().hex[:6], operation_payload={"note": "test"},
        user_id=user_id, source_table=source, source_id=None,
    )
    db.session.commit()
    return approval.id


def _status(approval_id):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    db.session.expire_all()
    return AIChatCRUDApproval.query.filter_by(id=approval_id).one().status


@pytest.mark.parametrize("source", SOURCES)
def test_a_record_from_each_source_is_settled_by_the_server_and_another_organisations_is_left(
    source, db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review

    org, other = make_org("writers-a"), make_org("writers-b")
    _administrator(db_session, org.id)
    _administrator(db_session, other.id)
    Reviewer(monkeypatch)
    with tenant_ctx(other.id):
        theirs = _write(other.id, source=source)
    with tenant_ctx(org.id):
        mine = _write(org.id, source=source)
        assert _status(mine) == ApprovalStatus.PENDING
        assert change_review.is_proposal(change_review.AIChatCRUDApproval.query.filter_by(id=mine).one())

        change_review.settle_proposals(org.id)

        assert _status(mine) != ApprovalStatus.PENDING, "%s waits for a person" % source
    with tenant_ctx(other.id):
        assert _status(theirs) == ApprovalStatus.PENDING, "the other organisation's record was not touched"


@pytest.mark.parametrize("operation,entity", PERSON_RECORDS)
def test_a_record_a_person_asked_for_is_settled_by_the_server_too(
    operation, entity, db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review

    org, other = make_org("people-a"), make_org("people-b")
    admin = _administrator(db_session, org.id)
    other_admin = _administrator(db_session, other.id)
    Reviewer(monkeypatch)
    with tenant_ctx(other.id):
        theirs = _write(other.id, user_id=other_admin.id, operation=operation, entity=entity)
    with tenant_ctx(org.id):
        mine = _write(org.id, user_id=admin.id, operation=operation, entity=entity)

        change_review.settle_proposals(org.id)

        assert _status(mine) != ApprovalStatus.PENDING, "%s %s waits for a person" % (operation, entity)
    with tenant_ctx(other.id):
        assert _status(theirs) == ApprovalStatus.PENDING


def test_the_functions_that_write_records_are_named_after_the_code():
    """The listed function names are real: a rename must update the list, not escape it."""
    missing = []
    for (file, function), _kind in WRITERS.items():
        tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if function not in names:
            missing.append((file, function))
    assert not missing, missing
