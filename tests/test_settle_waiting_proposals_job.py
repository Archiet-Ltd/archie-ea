"""The one-off job settles what was waiting before proposals settled on the server.

The workspace is seeded to match the thirty-five blueprint proposals of one session: sixteen repeat an element
of the same name and type, nine repeat a name under another type, ten are close variants of a name (two of them
listed twice), all raised on 19 October 2026 and due the same day. Two real organisations hold proposals; the
job settles one at a time and never touches the other's.
"""

import uuid
from datetime import datetime

import pytest

from tests.test_proposals_settle_without_a_person import (
    Reviewer, _administrator, _count, _element, _propose, _row, _saved, _solution)

pytestmark = pytest.mark.usefixtures("db_session")

EXACT = ["Canonical model API", "Policy enforcer", "MarketingIQ", "Audit trail", "Billing hub", "Search index",
         "Identity broker", "Event bus", "Reporting store", "Pricing engine", "Quote desk", "Order intake",
         "Fraud screen", "Notification relay", "Document vault", "Consent register"]
OTHER_TYPE = ["Policy enforcer gateway", "Data lake", "Metadata catalogue", "Rules engine", "Scheduler",
              "Feature flags", "Config service", "Secrets broker", "Rate limiter"]
VARIANTS = {"ifiok code generation engine": "ifiok code-generation engine",
            "Policy enforcement engine": "Policy enforcer core",
            "Customer profile service": "Customer profile svc",
            "Invoice generator": "Invoice generation",
            "Ledger synchroniser": "Ledger synchronizer",
            "Tenant router": "Tenant routing",
            "Usage meter": "Usage metering",
            "Release notes writer": "Release note writer"}


def _seed(db_session, org, solution, suffix, due):
    """Elements the workspace already has, then the thirty-five proposals raised against them."""
    existing = {}
    for name in EXACT:
        existing[name] = _element(db_session, org.id, "%s %s" % (name, suffix))
    for name in OTHER_TYPE:
        _element(db_session, org.id, "%s %s" % (name, suffix), "TechnologyService", "technology")
    for _new, old in VARIANTS.items():
        _element(db_session, org.id, "%s %s" % (old, suffix))
    proposals = [_propose(db_session, org.id, solution, "%s %s" % (n, suffix)) for n in EXACT]
    proposals += [_propose(db_session, org.id, solution, "%s %s" % (n, suffix)) for n in OTHER_TYPE]
    names = list(VARIANTS)
    proposals += [_propose(db_session, org.id, solution, "%s %s" % (n, suffix)) for n in names]
    # two of the variants are listed a second time under a new id
    proposals += [_propose(db_session, org.id, solution, "%s %s" % (names[0], suffix)),
                  _propose(db_session, org.id, solution, "%s %s" % (names[1], suffix))]
    assert len(proposals) == 35
    for row in proposals:
        row.created_at = datetime(2026, 10, 19, 9, 0)
        row.expires_at = due
    db_session.commit()
    return proposals


def test_the_job_retires_all_thirty_five_because_each_is_past_its_time_and_adds_nothing(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.commands.settle_waiting_proposals import plan_organisation
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services import change_review

    org, other = make_org("job-a"), make_org("job-b")
    _administrator(db_session, org.id)
    _administrator(db_session, other.id)
    suffix = uuid.uuid4().hex[:5]
    reviewer = Reviewer(monkeypatch)
    with tenant_ctx(other.id):
        theirs = _propose(db_session, other.id, _solution(db_session, other.id), "Theirs %s" % suffix)
        theirs.expires_at = datetime(2026, 10, 19, 9, 0)
        db_session.commit()
        their_id = theirs.id
    with tenant_ctx(org.id):
        proposals = _seed(db_session, org, _solution(db_session, org.id), suffix, datetime(2026, 10, 19, 9, 0))
        ids = [p.id for p in proposals]
        before = _count(org.id)

        plan = plan_organisation(org.id)
        print("DRY RUN (as they are on production, due 19 October 2026):", plan)
        assert plan["total"] == 35 and plan["retire"] == 35 and plan["link"] == plan["apply"] == plan["decline"] == 0
        assert _count(org.id) == before and all(_row(i).status == ApprovalStatus.PENDING for i in ids), "a dry run writes nothing"

        counts = change_review.settle_proposals(org.id)

        assert counts["retired"] == 35 and counts["applied"] == counts["linked"] == counts["declined"] == 0, counts
        assert all(_row(i).status == ApprovalStatus.REJECTED for i in ids)
        assert all(_saved(_row(i))["outcome"] == "retired" for i in ids)
        assert all("Expired on 19 October 2026; not applied." in _row(i).rejected_reason for i in ids)
        assert _count(org.id) == before, "nothing was added to the model"
        assert reviewer.prompts == [], "no reviewer was asked about an item past its time"
    with tenant_ctx(other.id):
        assert _row(their_id).status == ApprovalStatus.PENDING, "the other organisation's proposal was not touched"


def test_the_job_links_the_repeats_and_hands_only_the_near_matches_to_the_reviewer(
    db_session, make_org, tenant_ctx, monkeypatch
):
    """The same thirty-five with their time extended, to show what the duplicate handling decides."""
    from app.commands.settle_waiting_proposals import plan_organisation
    from app.models.archimate_core import ArchiMateElement
    from app.modules.ai_chat.services import change_review

    org = make_org("job-c")
    _administrator(db_session, org.id)
    suffix = uuid.uuid4().hex[:5]
    with tenant_ctx(org.id):
        proposals = _seed(db_session, org, _solution(db_session, org.id), suffix, datetime(2099, 1, 1))
        ids = [p.id for p in proposals]
        elements = {e.name: e.id for e in ArchiMateElement.query.filter_by(organization_id=org.id)}
        # The reviewer says a close variant is the same thing as the element it resembles, and a name that
        # only repeats another type's is a different thing.
        answers = {}
        for new, old in VARIANTS.items():
            answers["%s %s" % (new, suffix)] = {"criteria": [
                {"id": n, "passed": True, "reason": "Fine."} for n in range(1, 7)] + [
                {"id": 7, "passed": False, "reason": "It is the same thing.", "link_to": elements["%s %s" % (old, suffix)]}]}
        reviewer = Reviewer(monkeypatch, answers=answers)
        before = _count(org.id)

        plan = plan_organisation(org.id)
        print("DRY RUN (time extended):", plan)
        assert plan["total"] == 35 and plan["retire"] == 0 and plan["decline"] == 0
        assert plan["link"] >= 16 + 2  # the sixteen repeats, and the two listed twice
        assert plan["link"] + plan["apply"] == 35

        counts = change_review.settle_proposals(org.id)
        print("SETTLED (time extended):", counts, "reviewer calls:", len(reviewer.prompts))

        assert counts["in_review"] == 0 and counts["declined"] == 0 and counts["retired"] == 0, counts
        outcomes = [_saved(_row(i))["outcome"] for i in ids]
        assert outcomes[:16] == ["linked"] * 16, "the sixteen exact repeats link to what is there"
        assert counts["linked"] == outcomes.count("linked") and counts["applied"] == outcomes.count("applied")
        # Nothing of the exact repeats or the variants became a second element of the same name and type.
        for name in EXACT + [v for v in VARIANTS.values()]:
            assert _count(org.id, "%s %s" % (name, suffix), "ApplicationComponent") == 1, name
        # The reviewer saw only the proposals with a near match: never the sixteen exact repeats.
        assert not any("Canonical model API %s" % suffix in p and "NEAR_MATCHES" in p for p in reviewer.prompts)
        assert len(reviewer.prompts) < 35
        assert _count(org.id) >= before
