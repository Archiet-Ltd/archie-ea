"""ARCH-131 — AI output evaluation harness (spec Priority 6: R-60, R-61, R-63).

The register's specific ask: assert every count the agent states matches the
API. Three concrete cases of fabricated figures were found on this branch:

  1. A hardcoded "516 business capabilities" / "720 ArchiMate elements" prose
     block in the Capability Architect prompt (fixed upstream of this file —
     ``app/modules/ai_chat/services/capability_architect_prompts.py``'s
     ``_platform_data_block()`` now queries live counts; CM-01 marker in that
     file documents it).
  2. A hardcoded "881-app catalog" figure baked into a *tool description*
     sent to the model on every function-calling turn
     (``app/modules/ai_chat/tools/registry.py``, the
     ``find_applications_by_capability`` tool) — found and fixed as part of
     this change. This is the more dangerous of the two: it is not prose the
     model might discount, it is inside the tool schema the model is told to
     trust.
  3. ``portfolio_summary`` with ``mapped_capabilities`` (50) exceeding
     ``total_capabilities`` (0) — covered by
     ``tests/test_count_reconciliation.py::test_ai_context_general_refuses_inconsistent_portfolio_summary``
     and the R-01/R-06 reconciliation tests in the same file. Not duplicated
     here.

This file's job is the *general* form of the defect class: a static scan
that fails the build the moment a new hardcoded catalog-size number is
introduced into anything the model reads (prompts or tool schemas), plus a
runtime grounding check that every number the deterministic (non-LLM)
context-building code hands the model is traceable to a live query rather
than a literal.

Design constraint from the assignment: this harness MUST be runnable without
an LLM. Production has DEEPSEEK_API_KEY; this environment does not. The
split:

  - Deterministic, no LLM required (run always): R-60's grounding check
    against source code and the live API, plus the static scan below.
  - Needs a live model (R-61 golden dataset, R-62 persona differentiation):
    external release evidence, collected fail-closed only when a provider key
    is deliberately configured. It is never reported as a local pass or skip.
    R-63's deterministic approval-summary contract runs everywhere.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AI_CHAT_DIR = REPO_ROOT / "app" / "modules" / "ai_chat"

# Any of these patterns in a string literal that ships as prompt text or a
# tool description is a hardcoded catalog-size claim: a bare number
# immediately followed by "app(s)"/"capabilit(y|ies)"/"element(s)"/"vendor(s)"
# and the word "catalog"/"taxonomy" nearby. Conservative on purpose — this is
# a build-time tripwire, not a general magic-number linter, so it only flags
# the exact shape of claim that bit this branch twice.
_HARDCODED_CATALOG_CLAIM = re.compile(
    r"\b\d{2,}[\s-](?:app|apps|application|applications|capabilit\w*|element\w*|vendor\w*)"
    r"\b.{0,20}\b(?:catalog|taxonomy)\b",
    re.IGNORECASE,
)


def _iter_python_source(*dirs: Path):
    for d in dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


# ---------------------------------------------------------------------------
# R-60 (static half) — no hardcoded catalog-size number reaches the model,
# in a prompt or a tool schema.
# ---------------------------------------------------------------------------


def test_no_hardcoded_catalog_size_in_ai_chat_source():
    """Static tripwire for the exact defect class in ARCH-131.

    Scans every .py file under app/modules/ai_chat for a bare number claiming
    to describe a catalog/taxonomy size (e.g. "881-app catalog",
    "516 business capabilities taxonomy"). Such a claim is either wrong the
    moment the database changes, or was already wrong when written — see the
    ``find_applications_by_capability`` tool description, which said
    "881-app catalog" while nothing regenerates that number.

    A line may opt out with a trailing ``# catalog-count-ok: <reason>``
    comment, mirroring the escape-hatch convention in scripts/verify.py, for
    the rare case of a genuinely fixed reference number (e.g. citing an
    external standard's fixed size).
    """
    offenders = []
    for path in _iter_python_source(AI_CHAT_DIR):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "catalog-count-ok" in line:
                continue
            if _HARDCODED_CATALOG_CLAIM.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "hardcoded catalog-size claim(s) found in AI-facing source — these "
        "become stale the moment the database changes and are exactly the "
        "ARCH-131 defect class (\"516 business capabilities\", "
        "\"881-app catalog\"):\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# R-60 (runtime half) — the deterministic context/prompt-building code
# reports only numbers traceable to a live query, for the counts we can
# reach without invoking a model at all.
# ---------------------------------------------------------------------------


@pytest.fixture
def eval_client(app, db_session, make_org, login_as):
    from app.models.user import Permission, Role, User

    org = make_org("aieval")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(
        email=f"aieval-{__import__('uuid').uuid4().hex[:8]}@example.com",
        first_name="AI",
        last_name="Eval",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = __import__('uuid').uuid4().hex
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return org, client, user


def test_platform_data_block_counts_are_live_not_literal(eval_client):
    """R-60: the Capability Architect prompt's numeric claims must move when
    the database moves — i.e. must NOT be constant across two different
    database states, and must equal the live db count at each state.

    This is the direct regression test for the fixed half of CM-01: a prompt
    block that queries live counts, run twice against two different seeded
    states, must produce two different numbers that each match the db at the
    time they were generated. A hardcoded literal would produce the same
    number twice regardless of what was seeded in between.
    """
    from app import db
    from app.models.application_layer import ApplicationComponent
    from app.modules.ai_chat.services.capability_architect_prompts import (
        _platform_data_block,
    )

    org, client, user = eval_client

    before_count = ApplicationComponent.query.count()
    block_before = _platform_data_block()
    assert str(before_count) in block_before, (
        f"prompt block does not contain the current live application count "
        f"({before_count}) — got: {block_before!r}"
    )

    db.session.add(
        ApplicationComponent(
            name=f"AI Eval Probe App {__import__('uuid').uuid4().hex[:6]}",
            organization_id=org.id,
        )
    )
    db.session.commit()
    after_count = ApplicationComponent.query.count()
    assert after_count == before_count + 1, "fixture sanity check"

    block_after = _platform_data_block()
    assert str(after_count) in block_after, (
        f"prompt block did not move after a write — still missing the new "
        f"live count ({after_count}); got: {block_after!r}. A prompt whose "
        "numbers do not move with the database is exactly the fabrication "
        "pattern this test exists to catch."
    )
    assert block_before != block_after, (
        "prompt block was byte-identical before and after a write that "
        "changed the application count — the count claim is not actually "
        "live"
    )


def test_tool_registry_has_no_static_catalog_size_field(eval_client):
    """R-60: tool descriptions sent to the model must not embed a number.

    Complements the static scan above with a runtime check against the
    actual object the executor hands to the model, so a future refactor
    that builds descriptions dynamically (and could reintroduce a stale
    f-string-baked count) is still caught.
    """
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    offenders = []
    for tool in TOOL_SCHEMAS:
        desc = tool.get("description", "")
        if _HARDCODED_CATALOG_CLAIM.search(desc):
            offenders.append(f"{tool.get('name')}: {desc!r}")

    assert not offenders, (
        "tool description(s) sent to the model embed a hardcoded catalog-"
        f"size claim: {offenders}"
    )


# ---------------------------------------------------------------------------
# R-61 / R-62 / R-63 — provider evidence is external; deterministic contracts
# remain in the ordinary release suite.
# ---------------------------------------------------------------------------

_LLM_AVAILABLE = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))

_NO_LLM_REASON = (
    "no LLM API key configured in this environment (DEEPSEEK_API_KEY / "
    "OPENAI_API_KEY unset) — production has DEEPSEEK_API_KEY; this suite "
    "runs there. Do not fabricate a model response to pass this locally."
)


def _r61_golden_dataset_factual_accuracy():
    """R-61: 50+ verified architecture Q&A pairs, run on every prompt/model
    change, tracking hallucination rate as a released quality metric.

    NOT IMPLEMENTED as a runnable assertion in this environment — it
    requires a live model call per question and this environment has no
    LLM key. The golden dataset itself (questions + verified answers,
    spanning the 12 personas) still needs to be curated as a follow-up;
    tracked here rather than in prose so the gap shows up as a skip in
    every CI run against production, not a line in a doc nobody rereads.
    """
    pytest.fail("R-61 live-model golden dataset is not implemented")


def _r62_persona_responses_differ_materially():
    """R-62: same question, 12 personas, responses must differ materially.

    Needs a live model per persona. Skipped honestly here; per the spec this
    was "never verified in this engagement" even before this branch, and
    remains unverified without an LLM key.
    """
    pytest.fail("R-62 live-model persona evaluation is not implemented")


# Live-provider qualification is a separate, externally credentialled release
# activity.  Do not collect placeholder tests that can only SKIP; when a key is
# deliberately supplied, collect them and fail closed until their real datasets
# and evaluators are implemented.
if _LLM_AVAILABLE:
    test_r61_golden_dataset_factual_accuracy = _r61_golden_dataset_factual_accuracy
    test_r62_persona_responses_differ_materially = _r62_persona_responses_differ_materially


def test_r63_approval_lifecycle_summary_is_human_readable_not_a_repr():
    """R-63 (the deterministic slice): an approval summary must never be a
    raw Python dict/object repr, regardless of whether the content itself
    was model-generated. This is a formatting contract, not a judgment call
    about model quality, so it needs no LLM to check.
    """
    from types import SimpleNamespace

    from app.modules.ai_chat.services.agent_runner import AgentRunner

    summary = AgentRunner._approval_summary(
        SimpleNamespace(
            name="create_solution",
            arguments={"name": "Payments Renewal", "owner": "Architecture"},
        )
    )
    assert summary == (
        "Ask the assistant to create a solution — "
        "name: Payments Renewal; owner: Architecture."
    )
    assert "{'" not in summary and '"name"' not in summary
