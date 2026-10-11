"""What the model is told about the customer's estate must be true.

Two defects, same theme as the tool-count one: the model was misled about what
it had been given, so it answered confidently from a fragment.

1. The context block was `json.dumps(ctx)[:6000]` - a raw slice of a JSON
   string. It cut mid-token, so the model received malformed JSON ending
   part-way through a value, and nothing said anything had been removed. A
   context truncated from 40 elements to 22 looked exactly like a portfolio
   containing 22.

2. The DEFAULT domain injected six integers and a static domain list - not one
   application, capability or vendor NAME. The assistant knew the shape of the
   portfolio and none of its contents.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner

ROOT = Path(__file__).resolve().parents[1]

SERVICE_SOURCE = (
    ROOT / "app/modules/ai_chat/services/multi_domain_chat_service.py"
).read_text(encoding="utf-8")


def _loader_body(name: str) -> str:
    return SERVICE_SOURCE.split(f"def {name}", 1)[1].split("\n    def ", 1)[0]


# --------------------------------------------------------------------------
# Context serialisation
# --------------------------------------------------------------------------

def test_empty_context_produces_no_block():
    assert AgentRunner._serialise_context({}) == ""
    assert AgentRunner._serialise_context(None) == ""


def test_context_within_budget_is_untouched_and_parseable():
    ctx = {"portfolio_summary": {"total_applications": 5000}, "domains": ["a", "b"]}
    assert json.loads(AgentRunner._serialise_context(ctx)) == ctx


def test_oversized_context_stays_valid_json():
    """The whole point. A mid-token slice is unparseable, and the model then
    guesses where the data stopped."""
    ctx = {
        "portfolio_summary": {"total_applications": 5000},
        "elements": [{"id": i, "name": "e" * 200} for i in range(200)],
    }
    out = AgentRunner._serialise_context(ctx)

    parsed = json.loads(out)  # would raise on a mid-token cut
    assert isinstance(parsed, dict)
    assert len(out) <= AgentRunner.MAX_CONTEXT_CHARS


def test_oversized_context_names_what_it_dropped():
    ctx = {
        "portfolio_summary": {"total_applications": 5000},
        "elements": [{"id": i, "name": "e" * 200} for i in range(200)],
    }
    parsed = json.loads(AgentRunner._serialise_context(ctx))

    assert "elements" in parsed["_omitted"]["keys"], (
        "the model must be told a key was withheld, or absent reads as empty"
    )
    assert "NOT empty" in parsed["_omitted"]["reason"]


def test_the_smallest_keys_survive_so_totals_are_not_lost():
    """Totals are the authority on portfolio size; a big list is replaceable by
    a tool call. Drop the list, keep the numbers."""
    ctx = {
        "portfolio_summary": {"total_applications": 5000},
        "elements": [{"id": i, "name": "e" * 200} for i in range(200)],
    }
    parsed = json.loads(AgentRunner._serialise_context(ctx))

    assert parsed["portfolio_summary"]["total_applications"] == 5000
    assert "elements" not in parsed


def test_a_single_oversized_key_is_flagged_rather_than_silently_cut():
    huge = {"only": [{"name": "x" * 500} for _ in range(100)]}
    out = AgentRunner._serialise_context(huge)

    assert "CONTEXT TRUNCATED MID-VALUE" in out, (
        "when a lone key cannot be dropped the cut must be announced"
    )


def test_serialiser_is_used_and_the_raw_slice_is_gone():
    source = (ROOT / "app/modules/ai_chat/services/agent_runner.py").read_text(encoding="utf-8")
    assert "self._serialise_context(raw_ctx)" in source
    # Match the ASSIGNMENT, not the mention: _serialise_context's docstring
    # quotes the old expression to explain what it replaced.
    assert "ctx_block = json.dumps(" not in source, "the raw mid-token slice is back"


# --------------------------------------------------------------------------
# Default-domain grounding
# --------------------------------------------------------------------------

@pytest.mark.parametrize("entity", ["applications", "capabilities", "vendors"])
def test_default_context_names_entities_not_only_counts(entity):
    """The default domain is what an ordinary chat turn gets. Counts alone let
    the model describe the portfolio's shape and nothing in it."""
    source = (
        ROOT / "app/modules/ai_chat/services/multi_domain_chat_service.py"
    ).read_text(encoding="utf-8")

    general = source.split("def _load_general_context", 1)[1].split("\n    def ", 1)[0]
    assert '"portfolio_sample"' in general, "the default domain injects no entity names"
    assert '"%s"' % entity in general, "no %s are named in the default context" % entity


def test_the_sample_is_labelled_as_a_sample():
    """An unlabelled list of 20 of 5,000 applications reads as the portfolio -
    which would swap one grounding failure for a worse one."""
    source = (
        ROOT / "app/modules/ai_chat/services/multi_domain_chat_service.py"
    ).read_text(encoding="utf-8")
    general = source.split("def _load_general_context", 1)[1].split("\n    def ", 1)[0]

    assert "NOT the" in general and "full portfolio" in general
    assert '"of_applications"' in general, (
        "the sample must carry the true total beside it"
    )


# --------------------------------------------------------------------------
# element_id deep links
#
# Six places in the product link into the chat with
# ?element_id=<id>&context_type=<type> — every "Ask AI about this
# application / vendor / solution" button. Those become `context_filter`.
# Three of the nine loaders took the parameter and never read it, so
# "Ask AI about this vendor" handed the model the first 50 vendors with
# nothing saying which one the user had open, from a link that looks like
# it works. See docs/known-issues/ai-chat-parameter-effects.md §2.
#
# The DB-backed tests below need PostgreSQL (TEST_DATABASE_URL); the
# source-level ones do not.
# --------------------------------------------------------------------------

def _service():
    from app.modules.ai_chat.services.multi_domain_chat_service import (
        MultiDomainChatService,
    )

    return MultiDomainChatService(user_id=None)


def test_vendor_deep_link_names_the_vendor_asked_about(db_session, make_org, tenant_ctx):
    """The bug, exactly: the id of the vendor whose page the user came from
    must reach the model, not a bag of 50 arbitrary vendors."""
    from app.models.vendor.vendor_organization import VendorOrganization

    org = make_org("vendor-focus")
    suffix = uuid.uuid4().hex[:8]

    # More decoys than the loader's own limit(50) / [:30] slice, so a loader
    # that ignores the filter cannot accidentally include the right vendor.
    # "Zz" prefix: VendorOrganization.name is unique and therefore indexed, so
    # an unordered LIMIT may come back in name order — sort the focus last
    # under either plan.
    for i in range(60):
        db_session.add(VendorOrganization(name=f"Decoy Vendor {suffix} {i:02d}"))
    focus = VendorOrganization(name=f"Zz Focus Vendor {suffix}")
    db_session.add(focus)
    db_session.flush()
    focus_id, focus_name = focus.id, focus.name

    with tenant_ctx(org.id):
        result = _service().get_domain_context(
            "vendor_intelligence",
            {"element_id": focus_id, "context_type": "vendor"},
        )

    assert result["success"], result
    context = result["context"]

    focus_block = context.get("context_focus")
    assert focus_block, (
        "the vendor loader discarded element_id — the model is not told which "
        "vendor the user asked about"
    )
    assert focus_block["resolved"] is True
    assert focus_block["name"] == focus_name
    assert focus_block["id"] == focus_id

    # And it must actually survive into the list the model is shown.
    shown = {v.get("name") for v in context["vendor_organizations"]}
    assert focus_name in shown, (
        "the focused vendor was cut by the 30-row slice, so the context names "
        "every vendor except the one that was asked about"
    )


def test_vendor_deep_link_says_so_when_the_record_cannot_be_read(
    db_session, make_org, tenant_ctx
):
    """A missing record must be reported, not quietly replaced by the generic
    list — otherwise the model answers confidently about the wrong vendor."""
    org = make_org("vendor-missing")

    with tenant_ctx(org.id):
        result = _service().get_domain_context(
            "vendor_intelligence",
            {"element_id": 987654321, "context_type": "vendor"},
        )

    assert result["success"], result
    focus_block = result["context"].get("context_focus")
    assert focus_block, "an unresolvable element_id must still be surfaced"
    assert focus_block["resolved"] is False
    assert "could not be" in focus_block["_note"]


def test_capability_deep_link_names_the_capability_asked_about(
    db_session, make_org, tenant_ctx
):
    from app.models.business_capabilities import BusinessCapability

    org = make_org("cap-focus")
    suffix = uuid.uuid4().hex[:8]

    # Create inside the tenant context. Creating a BusinessCapability fires
    # _sync_archimate_element(), which inserts a matching ArchiMateElement — and
    # that cascaded insert takes its organization_id from g.current_org_id via
    # the tenant before_flush. Flushing outside a request context leaves it NULL
    # and the insert fails the NOT NULL constraint.
    with tenant_ctx(org.id):
        for i in range(3):
            db_session.add(
                BusinessCapability(name=f"Other Capability {suffix} {i}", organization_id=org.id)
            )
        focus = BusinessCapability(name=f"Focus Capability {suffix}", organization_id=org.id)
        db_session.add(focus)
        db_session.flush()
        focus_id, focus_name = focus.id, focus.name

        result = _service().get_domain_context(
            "business_capability",
            {"element_id": focus_id, "context_type": "capability"},
        )

    assert result["success"], result
    focus_block = result["context"].get("context_focus")
    assert focus_block, "the capability loader discarded element_id"
    assert focus_block["resolved"] is True
    assert focus_block["name"] == focus_name


def test_general_domain_resolves_a_solution_deep_link(db_session, make_org, tenant_ctx):
    """archimate/composer.html deep-links with context_type=solution and NO
    domain, so the general loader — the fallback — receives it."""
    from app.models.solution_models import Solution

    org = make_org("general-focus")
    focus = Solution(name=f"Focus Solution {uuid.uuid4().hex[:8]}", organization_id=org.id)
    db_session.add(focus)
    db_session.flush()
    focus_id, focus_name = focus.id, focus.name

    with tenant_ctx(org.id):
        result = _service().get_domain_context(
            "general",
            {"element_id": focus_id, "context_type": "solution"},
        )

    assert result["success"], result
    focus_block = result["context"].get("context_focus")
    assert focus_block, "the general loader discarded element_id"
    assert focus_block["resolved"] is True
    assert focus_block["name"] == focus_name


@pytest.mark.parametrize(
    "loader",
    ["_load_vendor_context", "_load_capability_context", "_load_general_context"],
)
def test_every_loader_taking_context_filter_actually_reads_it(loader):
    """The measurement from the known-issue doc, as a regression guard: a
    loader whose body mentions `context_filter` exactly once has taken the
    parameter in its signature and ignored it."""
    body = _loader_body(loader)

    assert body.count("context_filter") > 1, (
        f"{loader} names context_filter only in its signature — the deep-link "
        f"parameter is being dropped"
    )
    assert "context_focus" in body, (
        f"{loader} does not label the record the user asked about"
    )


# --------------------------------------------------------------------------
# Architecture context ordering
#
# _load_architecture_context selected an organisation's own elements with no
# ORDER BY, then LIMIT(100) and (when there are relationships) a re-sort by
# relationship count. Neither step means anything without a deterministic
# starting order: which rows the LIMIT keeps when an organisation has more
# than 100, and which element wins a relationship-count tie, both depend on
# it. Confirmed live: an otherwise-identical capture of the same
# organisation's AI chat page (same organisation, same records, nothing else
# changed) rendered a different element in an early, visible slot depending
# only on how much unrelated data existed elsewhere in the archimate_elements
# table — the query was free to hand rows back in whatever order their
# physical storage happened to put them in.
# --------------------------------------------------------------------------

def test_architecture_context_elements_are_name_ordered_and_stable_across_calls(
    db_session, make_org, tenant_ctx
):
    """Insert an organisation's elements in the reverse of the order the fix
    must produce, so an unordered query — which hands rows back in roughly
    insertion order absent an ORDER BY — and the correct, name-ordered one
    disagree about what comes first. Without the fix this fails on the first
    assertion below, not just on a mismatch between the two calls."""
    from app.models.archimate_core import ArchiMateElement

    org = make_org("architecture-order")
    suffix = uuid.uuid4().hex[:8]

    names = [f"Zz Element {suffix}", f"Mm Element {suffix}", f"Aa Element {suffix}"]
    for name in names:
        db_session.add(ArchiMateElement(
            name=name, type="ApplicationComponent", layer="application",
            organization_id=org.id,
        ))
    db_session.flush()
    expected_order = sorted(names)

    with tenant_ctx(org.id):
        first = _service().get_domain_context("architecture", {})
        second = _service().get_domain_context("architecture", {})

    assert first["success"], first
    assert second["success"], second

    first_names = [e["name"] for e in first["context"]["architecture_elements"]]
    second_names = [e["name"] for e in second["context"]["architecture_elements"]]

    assert first_names == expected_order, (
        "the architecture context's own elements are not name-ordered — "
        "which rows a LIMIT keeps, and which element wins a relationship-"
        "count tie, both depend on this"
    )
    assert second_names == first_names, (
        "the same organisation's architecture context changed order between "
        "two calls with nothing in between that should have changed it"
    )


def test_architecture_context_same_named_elements_are_id_ordered_and_stable_across_calls(
    db_session, make_org, tenant_ctx
):
    """Two elements sharing one name are exactly the case the comment above
    the fix describes: name alone does not distinguish them, so without an id
    tie-breaker either one can come first and the two can swap between calls.

    The row inserted first is given the higher id (an explicit id override),
    so insertion order and id order disagree -- an id-ordered result and a
    name-only one give different answers for which element comes first."""
    from app.extensions import db as _db
    from app.models.archimate_core import ArchiMateElement

    org = make_org("architecture-tie")
    suffix = uuid.uuid4().hex[:8]
    shared_name = f"Tied Element {suffix}"

    max_id = _db.session.query(_db.func.max(ArchiMateElement.id)).scalar() or 0
    higher_id = max_id + 10001

    first_inserted = ArchiMateElement(
        id=higher_id, name=shared_name, type="ApplicationComponent",
        layer="application", organization_id=org.id,
    )
    db_session.add(first_inserted)
    db_session.flush()

    second_inserted = ArchiMateElement(
        name=shared_name, type="ApplicationComponent",
        layer="application", organization_id=org.id,
    )
    db_session.add(second_inserted)
    db_session.flush()

    assert second_inserted.id < first_inserted.id, (
        "test setup requires the second-inserted row to hold the lower id -- "
        "got %r then %r" % (first_inserted.id, second_inserted.id)
    )
    expected_ids = [second_inserted.id, first_inserted.id]

    with tenant_ctx(org.id):
        first = _service().get_domain_context("architecture", {})
        second = _service().get_domain_context("architecture", {})

    assert first["success"], first
    assert second["success"], second

    def _tied_ids(result):
        return [
            e["id"] for e in result["context"]["architecture_elements"]
            if e["name"] == shared_name
        ]

    first_ids = _tied_ids(first)
    second_ids = _tied_ids(second)

    assert first_ids == expected_ids, (
        "two elements with the same name must be ordered by id, not left to "
        "whatever order the database happens to return ties in"
    )
    assert second_ids == first_ids, (
        "the tied pair changed order between two calls with nothing in "
        "between that should have changed it"
    )
