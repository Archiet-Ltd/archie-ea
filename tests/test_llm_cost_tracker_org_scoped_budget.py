"""``LLMCostTracker._get_organization_spending`` (app/modules/ai_chat/services/llm_cost_tracker.py)
summed every organisation's ``LLMInteraction`` rows with no scoping at all, so
``check_budget_before_call``'s organisation-level check compared the CALLER's budget against
every tenant's combined spend. One organisation spending heavily could exhaust the budget check
for every other organisation on the platform -- an availability bug (a wrongly-blocked LLM call),
not a data-disclosure one: no other organisation's prompts, responses or costs are returned to
the caller, only a pass/fail decision is affected.

The fix joins the sum through ``User.organization_id`` (``LLMInteraction`` carries no
organisation column of its own; ``user_id`` is the only link) and requires the caller to supply
an organisation, resolved from the signed-in user with a ``user_id`` fallback, rather than
defaulting to a global sum.

Tests fix the per-user and organisation-wide budgets via config overrides so only the
organisation-level branch of ``check_budget_before_call`` is exercised — the per-user threshold
uses the same ``DEFAULT_MONTHLY_BUDGET`` constant and would otherwise trip first.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models import LLMInteraction
from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker


@pytest.fixture
def isolate_org_budget_check(app):
    """Set a low, deterministic org budget and a high user budget, so only the
    organisation-level branch of check_budget_before_call can block a call."""
    original = {
        k: app.config.get(k) for k in ("LLM_USER_MONTHLY_BUDGET", "LLM_ORG_MONTHLY_BUDGET")
    }
    app.config["LLM_USER_MONTHLY_BUDGET"] = Decimal("100000")
    app.config["LLM_ORG_MONTHLY_BUDGET"] = Decimal("500")
    try:
        yield
    finally:
        for k, v in original.items():
            if v is None:
                app.config.pop(k, None)
            else:
                app.config[k] = v


def _user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"llm-{uuid.uuid4().hex[:6]}@example.test",
        first_name="LLM",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _spend(db_session, user, amount):
    interaction = LLMInteraction(
        user_id=user.id,
        model_name="gpt-4",
        provider="openai",
        token_count_input=10,
        token_count_output=10,
        cost=Decimal(amount),
    )
    db_session.add(interaction)
    db_session.commit()
    return interaction


def test_a_heavy_spending_organisation_does_not_block_another_organisations_budget_check(
    app, db_session, make_org, isolate_org_budget_check
):
    org_a = make_org("llm-heavy")
    org_b = make_org("llm-light")
    user_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b)

    # Org A alone is already at 98% of the £500 org budget -- comfortably over the 95% hard
    # limit -- while org B has spent nothing.
    _spend(db_session, user_a, "490.00")

    tracker = LLMCostTracker()
    with app.test_request_context("/"):
        allowed, message = tracker.check_budget_before_call(user_id=user_b.id)

    assert allowed is True, message


def test_an_organisations_own_heavy_spend_still_blocks_its_own_budget_check(
    app, db_session, make_org, isolate_org_budget_check
):
    org_a = make_org("llm-self-heavy")
    user_a = _user(db_session, org_a)

    _spend(db_session, user_a, "490.00")

    tracker = LLMCostTracker()
    with app.test_request_context("/"):
        allowed, message = tracker.check_budget_before_call(user_id=user_a.id)

    assert allowed is False
    assert "Organization monthly budget limit reached" in message


def test_get_organization_spending_only_counts_the_given_organisations_interactions(
    app, db_session, make_org
):
    from datetime import datetime, timedelta

    org_a = make_org("llm-scoped-a")
    org_b = make_org("llm-scoped-b")
    user_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b)

    _spend(db_session, user_a, "100.00")
    _spend(db_session, user_b, "7.00")

    tracker = LLMCostTracker()
    since = datetime.utcnow() - timedelta(days=1)
    with app.app_context():
        spending_a = tracker._get_organization_spending(since, org_a.id)
        spending_b = tracker._get_organization_spending(since, org_b.id)

    assert spending_a == Decimal("100.00")
    assert spending_b == Decimal("7.00")
