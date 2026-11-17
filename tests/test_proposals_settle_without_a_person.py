"""Every proposal settles on the server, whatever wrote it: nobody approves, rejects or waits.

A solution blueprint's proposals are checked against the elements the workspace already has, judged by a model
other than the drafter, and then applied, linked to what is there, declined with reasons, or retired when they
are past their time. The decisions are made as the workspace's own system actor.

The reviewer is stubbed at the router. Every isolation case uses two real organisations.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PASS_7 = {"criteria": [{"id": n, "passed": True, "reason": "Fine."} for n in range(1, 8)]}


def _unique(label):
    return "%s %s" % (label, uuid.uuid4().hex[:6])


def _administrator(db_session, org_id):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    assert role is not None
    user = User(email="admin-%s@example.com" % uuid.uuid4().hex[:10], first_name="Org", last_name="Admin",
                organization_id=org_id, role=role, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


def _solution(db_session, org_id):
    from app.models.solution_models import Solution

    solution = Solution(name=_unique("Blueprint"), organization_id=org_id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _element(db_session, org_id, name, kind="ApplicationComponent", layer="application"):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _propose(db_session, org_id, solution, name, kind="ApplicationComponent"):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.services.solution_blueprint_service import create_solution_blueprint_proposal

    proposal = create_solution_blueprint_proposal(
        solution_id=solution.id, organization_id=org_id, archimate_type=kind, name=name, source="llm",
        acm_domain="BUS",
    )
    db_session.commit()
    return AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, source_table="solution_blueprint_proposals", source_id=proposal.id).one()


def _row(approval_id):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    db.session.expire_all()
    return AIChatCRUDApproval.query.filter_by(id=approval_id).one()


def _count(org_id, name=None, kind=None):
    from app import db
    from app.models.archimate_core import ArchiMateElement

    db.session.expire_all()
    query = ArchiMateElement.query.filter(ArchiMateElement.organization_id == org_id)
    if name is not None:
        query = query.filter(ArchiMateElement.name == name)
    if kind is not None:
        query = query.filter(ArchiMateElement.type == kind)
    return query.count()


class Reviewer:
    """The model calls behind the router. ``answers`` maps a name in the change to the review it gets."""

    def __init__(self, monkeypatch, answers=None, default=PASS_7, revisions=()):
        from app.modules.ai_chat.services import llm_model_router as router

        self.prompts = []
        self.answers = answers or {}
        self.revisions = list(revisions)
        fake = self

        def select_reviewer(self, drafter_provider, drafter_model):
            return router.ReviewerChoice("deepseek", "deepseek-v4-pro", True)

        def call_model(self, choice, prompt, timeout=90.0):
            if "independent reviewer rejected it" in prompt:
                return json.dumps(fake.revisions.pop(0))
            fake.prompts.append(prompt)
            for name, review in fake.answers.items():
                if name in prompt:
                    return json.dumps(review)
            return json.dumps(default)

        monkeypatch.setattr(router.LLMModelRouter, "select_reviewer", select_reviewer)
        monkeypatch.setattr(router.LLMModelRouter, "call_model", call_model)
        monkeypatch.setattr("app.modules.ai_chat.services.change_review._month_has_room", lambda c, p: True)


def _saved(approval):
    return approval.review_reasons or {}


def _fail(criterion, reason):
    return {"criteria": [{"id": n, "passed": n != criterion, "reason": reason if n == criterion else "Fine."}
                         for n in range(1, 8)]}

