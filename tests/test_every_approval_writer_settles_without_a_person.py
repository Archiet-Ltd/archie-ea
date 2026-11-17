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

from tests.test_connector_approval_flow import (  # noqa: F401  (fixtures the connector journey below uses)
    _connector_hosts_need_no_dns,
    admin_a,
    client,
    org_a,
)
from tests.test_proposals_settle_without_a_person import Reviewer, _administrator

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parent.parent

# (file, function that calls create_approval_record) -> what it writes:
#   "source"  a proposal nobody asked for, from a source the review settles (PROPOSAL_SOURCES);
#   "loop"    a change a person asked for that enters the review when it is written (the assistant's own);
#   "not yet" a person's request whose own approval flow has not been moved into the review; the reason is
#             in the report. This group may only shrink: moving a writer out of it needs its flow's tests changed.
WRITERS = {
    ("app/commands/backfill_review_queue_approvals.py", "run_backfill"): "source",
    ("app/services/solution_blueprint_service.py", "create_solution_blueprint_proposal"): "source",
    ("app/services/confidence_review_service.py", "add_to_review_queue"): "source",
    ("app/services/vendor_detection.py", "propose"): "source",
    ("app/services/vendor_detection.py", "_propose_architecture"): "source",
    ("app/services/connector_framework.py", "propose_connector_change"): "source",
    ("app/modules/ai_chat/services/ai_chat_approval_service.py", "create_pending_approval"): "loop",
    ("app/connectors/github.py", "_propose_owner"): "source",
    ("app/connectors/github.py", "propose_relationships"): "source",
    ("app/connectors/github.py", "propose_technologies"): "source",
    ("app/connectors/github.py", "unmatched"): "source",
    ("app/modules/ai_chat/services/agent_runner.py", "_queue_approval"): "loop",
    ("app/modules/ai_chat/services/agent_registry_service.py", "request_charter_change"): "not yet",
    ("app/modules/enterprise_genome/services/ai_system_classification.py", "propose_classification"): "not yet",
    ("app/modules/enterprise_genome/services/ai_system_classification.py", "propose_assessment"): "not yet",
    ("app/modules/enterprise_genome/services/ai_system_grounding.py", "propose_grounding_source"): "not yet",
    ("app/modules/intelligence/services/deadline_alert_service.py", "scan_organization_for_eol_alerts"): "source",
    ("app/modules/architecture/services/data_stewardship_service.py", "propose_classification"): "not yet",
    ("app/modules/organization/website_crawl/service.py", "_store"): "source",
    ("app/modules/intelligence/services/matcher_service.py", "propose_match"): "source",
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


def test_the_writers_not_yet_in_the_review_are_exactly_these_five():
    """Named, so a sixth cannot join them unseen and one that moves in is removed from the list."""
    not_yet = sorted(key for key, kind in WRITERS.items() if kind == "not yet")
    assert not_yet == sorted([
        ("app/modules/architecture/services/data_stewardship_service.py", "propose_classification"),
        ("app/modules/ai_chat/services/agent_registry_service.py", "request_charter_change"),
        ("app/modules/enterprise_genome/services/ai_system_classification.py", "propose_assessment"),
        ("app/modules/enterprise_genome/services/ai_system_classification.py", "propose_classification"),
        ("app/modules/enterprise_genome/services/ai_system_grounding.py", "propose_grounding_source"),
    ])


def test_a_change_the_assistant_asks_for_is_settled_in_its_own_turn(db_session, make_org, tenant_ctx, monkeypatch):
    """The chat writers settle in the turn that wrote them (``settle=True``): the record is checked, reviewed
    and applied or declined before the writer returns, with the person who asked."""
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    org = make_org("chat-a")
    admin = _administrator(db_session, org.id)
    Reviewer(monkeypatch)
    with tenant_ctx(org.id):
        approval = create_approval_record(
            organization_id=org.id, operation_type="create", entity_type="capability", summary="Create capability X",
            operation_payload={"name": "X %s" % uuid.uuid4().hex[:5]}, user_id=admin.id, settle=True)
        assert change_review.in_scope(approval)
        assert _status(approval.id) != ApprovalStatus.PENDING


def test_the_functions_that_write_records_are_named_after_the_code():
    """The listed function names are real: a rename must update the list, not escape it."""
    missing = []
    for (file, function), _kind in WRITERS.items():
        tree = ast.parse((ROOT / file).read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if function not in names:
            missing.append((file, function))
    assert not missing, missing


def _verdict_of(approval_id):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.ai_chat.services import change_review

    db.session.expire_all()
    row = AIChatCRUDApproval.query.filter_by(id=approval_id).one()
    return row, change_review.record_row(row)


@pytest.mark.parametrize("case", ["new", "near"])
def test_the_matchers_real_proposal_settles_on_its_merits_and_the_other_organisations_is_left(
    case, db_session, make_org, tenant_ctx, monkeypatch
):
    """Drive the matcher itself: an incoming element is written by the matcher's own code, queued for settling
    at once, and ends applied (a new element) or linked (one the workspace has) with a reason about that
    element, not a generic item."""
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.archimate_core import ArchiMateElement
    from app.modules.ai_chat.services import change_review
    from app.modules.intelligence.services.matcher_service import MatcherService
    from tests.test_proposals_settle_without_a_person import _element

    org, other = make_org("matcher-a"), make_org("matcher-b")
    _administrator(db_session, org.id)
    _administrator(db_session, other.id)
    Reviewer(monkeypatch)
    label = uuid.uuid4().hex[:6]
    existing = "Billing Platform %s" % label
    incoming = "Ledger Gateway %s" % label if case == "new" else "Billing Platform %s Service" % label
    queued = []
    real_queue = change_review.queue_settling
    monkeypatch.setattr(change_review, "queue_settling", lambda org_id: (queued.append(org_id), real_queue(org_id))[1])
    with tenant_ctx(other.id):
        _element(db_session, other.id, existing)
        theirs = MatcherService.propose_match(name=incoming, type_name="ApplicationComponent", org_id=other.id)
    with tenant_ctx(org.id):
        _element(db_session, org.id, existing)
        result = MatcherService.propose_match(name=incoming, type_name="ApplicationComponent", org_id=org.id)
        assert result.approval_id, "the matcher proposed it for review"
        assert (result.matched_element_id is not None) == (case == "near"), result
        db_session.commit()
        assert org.id in queued, "a settling job is queued at once, not left to a sweep"
        assert _status(result.approval_id) == ApprovalStatus.PENDING

        change_review.settle_proposals(org.id)

        row, shown = _verdict_of(result.approval_id)
        assert row.status != ApprovalStatus.PENDING, "the matcher's proposal waits for a person"
        assert shown["label"] == ("Applied" if case == "new" else "Linked"), shown
        assert incoming in (row.summary or ""), "the record is about the incoming element"
        assert "Test change" not in (row.summary or "")
        added = ArchiMateElement.query.filter_by(organization_id=org.id, name=incoming).count()
        assert added == (1 if case == "new" else 0), "a new element is added once; a match adds nothing"
    with tenant_ctx(other.id):
        assert _status(theirs.approval_id) == ApprovalStatus.PENDING, "the other organisation's was not touched"
        assert ArchiMateElement.query.filter_by(organization_id=other.id, name=incoming).count() == 0


def test_a_connection_saved_through_the_connector_writer_settles_on_its_merits(
    db_session, org_a, admin_a, client, login_as, tenant_ctx, monkeypatch
):
    """Drive the connector writer with its real payload (a ServiceNow connection saved as a person saves it):
    it is queued for settling at once and ends applied with its checks and reasons, and the connection exists."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.connector_config import ConnectorConfig
    from app.models.job import Job
    from app.modules.ai_chat.services import change_review

    Reviewer(monkeypatch)
    login_as(client, admin_a)
    response = client.post("/admin/connectors", data={
        "connector_type": "servicenow", "name": "Writer test ServiceNow",
        "config_json": '{"instance_url": "https://org-a.service-now.com"}', "credential": "a-secret-value",
        "enabled": "1"})
    assert response.status_code == 302
    mine = AIChatCRUDApproval.query.filter_by(organization_id=org_a.id, entity_type="connectors").one()
    assert mine.status == ApprovalStatus.PENDING and mine.source_table == "connector_config"
    assert Job.query.filter(Job.task == "settle_proposals").count() >= 1, "a settling job was queued at once"
    assert ConnectorConfig.query.filter_by(organization_id=org_a.id, connector_type="servicenow").first() is None

    db_session.commit()
    with tenant_ctx(org_a.id):
        change_review.settle_proposals(org_a.id)

    row, shown = _verdict_of(mine.id)
    assert row.status == ApprovalStatus.APPROVED, shown
    assert shown["label"] == "Applied" and "Writer test ServiceNow" in shown["summary"], shown
    assert ConnectorConfig.query.filter_by(organization_id=org_a.id, name="Writer test ServiceNow").count() == 1
