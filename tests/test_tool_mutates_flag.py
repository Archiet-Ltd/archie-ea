"""Every tool declares whether it writes, and the declaration must be true.

`tier` already existed but conflates reads and writes: 34 of 37 tools are
tier=="auto", covering both find_applications and create_solution. Gating on it
would put every search behind an approval prompt, which is why
`toggle_auto_execute`'s own docstring (chat_core.py) asks for exactly this flag
before the auto-execute preference can be enforced.

The flag also decides which turns render a write receipt and which next-artifact
action is offered, so a wrong value is not cosmetic: it either hides a write from
the user or claims one that did not happen.

The classification was derived by reading each implementation for
db.session.add/commit/delete — including through the five tools that delegate to
a service — not from the tool's name. This file pins that derivation so a new
tool cannot join without a decision, and so a name that reads like a write
(propose_rationalization) cannot be silently reclassified.

CORRECTION: run_inference_engine was originally pinned here as read-only. It
is not. `_tool_run_inference_engine` (tools/executor.py) defaults
`dry_run = args.get("dry_run", False)` and calls
`engine.repair(link.element_id, dry_run=dry_run)` unconditionally — with the
default False, ArchiMateInferenceEngine.repair -> repair_chain ->
get_or_create_node/get_or_create_relationship (architecture_graph_facade.py)
call db.session.add + db.session.flush, committed by the chat turn's later
db.session.commit(). The tool's own schema description already said as much
("If true, show what would be created without writing to DB. Default
false."). This is exactly the case
test_the_declaration_matches_the_implementation's docstring calls out as
undetectable by its own regex — the write happens through a delegated
service call, not a literal `db.session.` token inside
`_tool_run_inference_engine` itself — which is why a second, independent
read of the call chain (not a regex) is what caught it.
"""

import re
from pathlib import Path

import pytest

from app.modules.ai_chat.tools.archimate_specs import ELEMENT_SPECS
from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]

# Derived from the implementations, then pinned here.
#
# HAND_WRITTEN is a literal on purpose: each of these was inspected and its
# write path confirmed, and a new one must be added here deliberately.
HAND_WRITTEN = {
    "create_adr",
    "create_archimate_element", "create_archimate_relationship", "create_constraint",
    "create_driver", "create_goal", "create_option", "create_requirement",
    "create_risk", "create_solution", "link_application_to_capability",
    "link_application_to_solution", "link_capability_to_solution", "link_vendor_product",
    "mark_option_recommended", "run_inference_engine", "submit_for_arb_review",
    "update_application_status", "update_solution_fields", "update_solution_phase",
    "record_capability_maturity", "score_rationalization",
    "create_vendor", "merge_capabilities",
    "bulk_update_application_status", "create_contract", "upsert_license",
    # create_programme (Phase 0 CI audit, fix/phase0-ci-and-audit): calls
    # ProgrammeSetupService.create_business_first_programme, the same entry
    # point the human /solutions/new-programme wizard POST handler calls, and
    # checks can_create_programme at the door first (audit F-04) before
    # building the write payload. Genuinely mutates; verified by reading
    # app/modules/ai_chat/tools/executor.py::_tool_create_programme directly,
    # not assumed from its name.
    "create_programme",
    # generate_blueprint_narrative overwrites existing section text through
    # the narrative generation service. Its own description says so, and it
    # is already tier='approve' so it always queues for confirmation.
    "generate_blueprint_narrative",
    # poll_infrastructure checks configured infrastructure endpoints for
    # reachability; it is classified as external_action and mutates the
    # infrastructure state (probes may trigger alerts or state changes).
    "poll_infrastructure",
    # extract_contract_from_document extracts structured contract terms from
    # text and persists them; it is classified as external_action and mutates
    # the contract registry.
    "extract_contract_from_document",
}

# The per-ArchiMate-type element tools are GENERATED from ELEMENT_SPECS rather
# than written out once each, so pinning them as a 54-name literal would create
# a list nobody maintains -- and a stale pin is worse than none, because it
# reads as a reviewed decision. The invariant worth asserting is the one that
# actually protects the approval boundary: every generated element tool CREATES
# an element, so every one of them mutates. If a generated tool ever stopped
# being a write, the equality below would fail and someone would have to say so.
GENERATED_ELEMENT_TOOLS = {
    ("create_archimate_%s" % element_type if "create_%s" % element_type in HAND_WRITTEN
     else "create_%s" % element_type)
    for element_type in ELEMENT_SPECS
}

MUTATING = HAND_WRITTEN | GENERATED_ELEMENT_TOOLS


def test_every_tool_declares_whether_it_mutates():
    """No default. A new tool must make the call explicitly."""
    missing = [t["name"] for t in TOOL_SCHEMAS if "mutates" not in t]
    assert not missing, (
        "these tools do not declare `mutates`, so receipts and approval gating "
        "cannot classify them: %s" % missing
    )


def test_the_declaration_matches_the_implementation():
    """A tool that touches db.session must say so, and vice versa.

    This is the assertion that stops the flag drifting from reality: it re-derives
    the answer from executor.py rather than trusting the registry.
    """
    ex = (ROOT / "app/modules/ai_chat/tools/executor.py").read_text(encoding="utf-8")
    writes_re = re.compile(r"db\.session\.(add|commit|delete)")

    wrong = []
    for tool in TOOL_SCHEMAS:
        name = tool["name"]
        # Bounded at the next def at ANY indentation, not just the next
        # _tool_ method. Under the old lookahead the LAST tool method
        # captured all the way to end-of-file and absorbed every
        # module-level helper below it -- so search_archimate_elements, a
        # pure query, reported as a writer the moment a helper containing
        # db.session.add was appended to the module.
        m = re.search(
            r"def _tool_%s\(self.*?(?=\n    def |\ndef |\nclass |\Z)" % name,
            ex, re.S)
        if not m:
            continue  # no implementation to compare against
        implementation_writes = bool(writes_re.search(m.group(0)))
        if implementation_writes and not tool["mutates"]:
            wrong.append(f"{name}: writes to the database but declares mutates=False")
        # The reverse is not asserted: a tool may legitimately declare mutates=True
        # while delegating its write to a service, which this regex cannot see.
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("name", sorted(MUTATING))
def test_known_writers_are_flagged(name):
    tool = next(t for t in TOOL_SCHEMAS if t["name"] == name)
    assert tool["mutates"] is True, f"{name} writes to the repository but is not flagged"


def test_a_name_that_sounds_like_a_write_is_not_assumed_to_be_one():
    """propose_rationalization reads only despite its action-shaped name.

    A startswith("create_")-style heuristic would have missed
    mark_option_recommended and submit_for_arb_review while wrongly flagging
    this one — which is why the classification came from the implementations.

    run_inference_engine is deliberately NOT in this list any more: it reads
    like a write and, on inspection, is one — see the module docstring's
    CORRECTION. A name that sounds neutral ("run", "diagnose"-adjacent) is
    just as unreliable a signal as one that sounds like a write; both were
    only ever a hint, never the source of truth.
    """
    by_name = {t["name"]: t for t in TOOL_SCHEMAS}
    for read_only in ("propose_rationalization",
                      "simulate_impact",
                      "validate_sap_clean_core"):
        assert by_name[read_only]["mutates"] is False, (
            f"{read_only} does not write; flagging it would put a read behind an "
            f"approval prompt and claim a receipt for a turn that changed nothing"
        )


def test_run_inference_engine_is_flagged_mutating():
    """Regression pin for the misclassification caught in adversarial review:
    dry_run defaults False, so the un-dry-run path writes via
    ArchiMateInferenceEngine.repair -> get_or_create_node/relationship."""
    by_name = {t["name"]: t for t in TOOL_SCHEMAS}
    assert by_name["run_inference_engine"]["mutates"] is True


def test_the_helper_returns_the_same_set():
    from app.modules.ai_chat.tools.registry import mutating_tool_names

    assert mutating_tool_names() == MUTATING
