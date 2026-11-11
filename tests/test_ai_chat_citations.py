"""An answer about the portfolio must be checkable against the rows it came from.

Chat responses carried no provenance: the model could say "Salesforce is your CRM
system of record and is end-of-life in 2027" and the reader had no id, no link
and no way to tell a real row from a fluent invention. AIChatLinkService existed
with zero callers and would not have helped - it regex-matched the USER'S
QUESTION against a static map of dashboard URLs, producing generic page links
rather than record citations. AIHallucinationDetector likewise had zero callers.

Citations are derived from the tool results server-side rather than asked of the
model, because a citation the model writes is another claim, and the claim is
what is being verified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("FLASK_CONFIG", "testing")
    os.environ.setdefault("SECRET_KEY", "test-only-not-secret")
    from app import create_app

    return create_app("testing")


def runner():
    return AgentRunner(user_id=1)


def test_read_tool_rows_become_sources(app):
    sources = []
    with app.test_request_context("/"):
        runner()._collect_sources(
            "find_applications",
            {"success": True, "result": [{"id": 7, "name": "Salesforce"}]},
            sources,
        )
    assert len(sources) == 1
    assert sources[0]["type"] == "application"
    assert sources[0]["id"] == 7
    assert sources[0]["name"] == "Salesforce"


def test_the_same_record_is_not_cited_twice(app):
    """The same row commonly comes back from several tools in one turn."""
    sources = []
    row = {"success": True, "result": [{"id": 7, "name": "Salesforce"}]}
    with app.test_request_context("/"):
        runner()._collect_sources("find_applications", row, sources)
        runner()._collect_sources("find_applications_by_capability", row, sources)
    assert len(sources) == 1


def test_failed_and_write_tools_produce_no_citations(app):
    sources = []
    with app.test_request_context("/"):
        runner()._collect_sources(
            "find_applications", {"success": False, "error": "boom"}, sources
        )
        runner()._collect_sources(
            "create_solution", {"success": True, "result": [{"id": 1, "name": "X"}]}, sources
        )
    assert sources == [], "only successful READ tools describe existing records"


def test_rows_without_an_identity_are_skipped(app):
    sources = []
    with app.test_request_context("/"):
        runner()._collect_sources(
            "find_applications",
            {"success": True, "result": [
                {"id": None, "name": "no id"},
                {"id": 3},
                "not a dict",
                {"id": 4, "name": "keeps"},
            ]},
            sources,
        )
    assert [s["name"] for s in sources] == ["keeps"]


def test_citations_are_capped(app):
    sources = []
    with app.test_request_context("/"):
        runner()._collect_sources(
            "find_applications",
            {"success": True, "result": [{"id": i, "name": f"app{i}"} for i in range(200)]},
            sources,
        )
    assert len(sources) == AgentRunner.MAX_SOURCES, (
        "a broad query must not bury the answer under its own footnotes"
    )


def test_a_url_is_built_for_a_registered_record_type(app):
    with app.test_request_context("/"):
        url = AgentRunner._source_url("application", {"id": 7, "name": "Salesforce"})
    assert url and str(7) in url, url


def test_a_missing_url_degrades_to_no_link_not_an_error(app):
    """Blueprints register non-fatally, so an unresolvable endpoint must cost the
    link, not the citation."""
    with app.test_request_context("/"):
        assert AgentRunner._source_url("not_a_real_type", {"id": 1}) is None
        # archimate needs layer+type; without them there is nothing to build.
        assert AgentRunner._source_url("archimate_element", {"id": 1}) is None


def test_sources_reach_both_chat_responses():
    source = (ROOT / "app/modules/ai_chat/routes/chat_core.py").read_text(encoding="utf-8")
    assert '"sources": agent_result.get("sources", [])' in source, (
        "the non-streaming response dropped its citations"
    )


def test_context_used_is_no_longer_hardcoded_true():
    """It asserted grounding on every response regardless of whether any context
    was built - _build_system_prompt swallows failures into an empty string."""
    source = (ROOT / "app/modules/ai_chat/routes/chat_core.py").read_text(encoding="utf-8")
    assert '"context_used": True' not in source
    assert '"context_used": bool(agent_result.get("sources"))' in source


def test_the_ui_renders_citations_escaped():
    """Record names are database content rendered into innerHTML.

    Lives in app/static/js/ai_chat/render.js since the client rebuild moved the
    transcript rendering out of the template. The guarantee is unchanged: a
    source name or url reaching innerHTML must go through escapeForHtml first,
    because both are database content and one of them is a link target.
    """
    ui = (ROOT / "app/static/js/ai_chat/render.js").read_text(encoding="utf-8")
    assert "function renderSources(" in ui
    assert "escapeForHtml(s.name)" in ui
    assert "escapeForHtml(s.url)" in ui

    # Defined is not the same as called. The original assertion pinned
    # "${renderSources(metadata.sources)}" — that the result reaches the message
    # body, not merely that the function exists. Sources now reach it through
    # renderEvidence's disclosure (Plan 3 Task 4), so the route changed; the
    # guarantee must not.
    assert "renderSources(sources)" in ui, "renderEvidence does not render the sources"
    # Sources reach the message through renderAnswerFooter -> renderEvidence
    # (Plan 3 Task 6 folded receipts and next-artifact into the same footer, so
    # every answer path shares one call site). The route changed twice now; the
    # guarantee — sources reach the message, escaped — has not.
    assert "renderEvidence(m.trail" in ui, "the answer footer does not render the evidence strip"
    assert "renderAnswerFooter(metadata)" in ui, "the completed message drops its footer"
    assert "renderAnswerFooter(meta);" in ui, "the streamed message finalises without its footer"


def test_no_transcript_rendering_was_left_behind_in_the_template():
    """The template must not regrow a second, unescaped renderer.

    Two copies of this function is exactly how the original stored-XSS bug
    survived: the streaming path sanitised and the completion path did not.
    """
    tpl = (ROOT / "app/templates/ai_chat/index.html").read_text(encoding="utf-8")
    assert "function renderSources(" not in tpl
    assert "function appendMessage(" not in tpl


def test_the_model_is_told_the_grounding_contract():
    runner_src = (
        ROOT / "app/modules/ai_chat/services/agent_runner.py"
    ).read_text(encoding="utf-8")
    prefix = runner_src.split('_AGENT_PREFIX = """', 1)[1].split('"""', 1)[0]

    assert "system of record" in prefix
    assert "_omitted" in prefix, "the model must know withheld context is not empty"
    assert "showing" in prefix.lower() and "total" in prefix.lower(), (
        "the model must be told to report the true total, not the row count"
    )


def test_a_read_tool_returning_a_single_record_is_cited():
    """_collect_sources required a list, so a one-record tool cited nothing.

    get_solution_summary returns {"result": {"id": .., "name": ..}} — a dict, not
    a list — so the isinstance(rows, list) guard skipped it entirely and an
    answer built on a real solution looked ungrounded. _source_url has had a
    `solution` branch all along that no tool could reach.
    """
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    sources = []
    AgentRunner.__new__(AgentRunner)._collect_sources(
        "get_solution_summary",
        {"success": True, "result": {"id": 7, "name": "Order-to-Cash"}},
        sources,
    )
    assert sources, "a single-record read tool produced no citation"
    assert sources[0]["type"] == "solution"
    assert sources[0]["id"] == 7 and sources[0]["name"] == "Order-to-Cash"


def test_technical_capability_rows_are_cited():
    """find_technical_capabilities returns a list of {id, name} and was uncited."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    sources = []
    AgentRunner.__new__(AgentRunner)._collect_sources(
        "find_technical_capabilities",
        {"success": True, "result": [
            {"id": 3, "name": "Identity & Access Management"},
            {"id": 4, "name": "API Gateway"},
        ]},
        sources,
    )
    assert len(sources) == 2, "technical capability rows produced no citations"
    assert {s["type"] for s in sources} == {"technical_capability"}


def test_a_tool_returning_no_records_still_cites_nothing():
    """The widening must not invent a citation where there is no record.

    A source that does not correspond to a returned row is fabrication, which is
    the failure the whole citation mechanism exists to prevent.
    """
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)
    for payload in (
        {"success": False, "result": [{"id": 1, "name": "x"}]},   # tool failed
        {"success": True, "result": {"score": 62}},               # no id/name
        {"success": True, "result": [{"id": None, "name": "x"}]}, # no real id
        {"success": True, "result": "a narrative string"},        # not records
    ):
        sources = []
        runner._collect_sources("get_solution_summary", payload, sources)
        runner._collect_sources("find_technical_capabilities", payload, sources)
        assert sources == [], f"invented a citation from {payload}"


# ── Evidence trail (Plan 3 Task 4) ──────────────────────────────────────────

def _render_js():
    return (ROOT / "app/static/js/ai_chat/render.js").read_text(encoding="utf-8")


def test_the_evidence_trail_has_three_states_not_a_binary():
    """A grounded/ungrounded binary keyed on sources would lie.

    Citations cover 7 of 37 tools, so an answer built by propose_rationalization
    or simulate_impact against real rows produces no sources. Rendering that as
    "not checked against your portfolio" is a false provenance claim in the
    direction the fabricated-data gate cannot see.
    """
    js = _render_js()
    assert "function renderEvidence(" in js
    for state in ("retrieved", "context", "unretrieved"):
        assert "'%s'" % state in js, f"the {state} state is missing"
    assert "contextUsed" in js, (
        "no way to distinguish 'no tool ran but the snapshot was in context' "
        "from 'nothing was read at all'"
    )


def test_coverage_is_taken_from_the_result_not_inferred_from_row_count():
    """'47 matched, showing 15' must come from the tool, never be guessed.

    The number of rows shown is exactly what must not be presented as the number
    that exists — _AGENT_PREFIX rule 9 asks the model to report N of M, and this
    is the structural version that does not depend on it complying.
    """
    js = _render_js()
    assert "total_matched" in js, "coverage does not read the tool's own total"
    assert "matched, showing" in js


def test_both_render_paths_go_through_the_evidence_strip():
    """The completed path and the streamed path must not drift.

    Two renderers is how the original stored-XSS bug survived: one sanitised and
    one did not.
    """
    js = _render_js()
    assert js.count("function renderAnswerFooter(") == 1, (
        "more than one footer renderer means the paths can drift"
    )
    assert "renderEvidence(m.trail" in js, "the footer does not render the evidence strip"
    assert "renderAnswerFooter(metadata)" in js, "the completed message skips the footer"
    assert "renderAnswerFooter(meta);" in js, "the streamed message skips the footer"


def test_the_disclosure_is_reachable_without_a_mouse():
    js = _render_js()
    assert "aria-expanded" in js and "aria-controls" in js
    assert "aria-label=" in js, (
        "the accessible name must carry the whole sentence — the context-only "
        "and unretrieved states have to reach a screen reader with the same "
        "prominence as retrieved"
    )
    assert "js-evidence-toggle" in js and "addEventListener('click'" in js, (
        "the toggle needs a delegated listener; an inline handler would be "
        "refused by the CSP"
    )


def test_source_escaping_survives_the_new_nesting():
    """renderSources now renders inside the disclosure, not beside it."""
    js = _render_js()
    assert "renderSources(sources)" in js, "renderEvidence does not render the sources"
    assert "escapeForHtml(s.name)" in js and "escapeForHtml(s.url)" in js


# ── Receipts and next artifact (Plan 3 Task 6) ──────────────────────────────

def test_receipts_are_driven_by_the_mutates_flag_not_by_prose():
    """A receipt for a write that did not happen is the same defect as a
    citation for a record that was not returned."""
    js = _render_js()
    assert "function renderReceipts(" in js
    assert "a.mutates" in js, (
        "receipts do not key on the registry's mutates flag, so they are "
        "guessing which turns wrote"
    )


def test_the_server_marks_which_actions_wrote():
    """One source of truth. The client must not carry a second copy of the split."""
    runner = (ROOT / "app/modules/ai_chat/services/agent_runner.py").read_text(encoding="utf-8")
    assert '"mutates": tc.name in _MUTATING_TOOLS' in runner
    assert "mutating_tool_names" in runner, (
        "agent_runner should read the registry flag rather than re-deriving it"
    )


def test_an_unmapped_turn_offers_no_next_step():
    """A wrong suggestion costs more than none."""
    js = _render_js()
    assert "function renderNextArtifact(" in js
    assert "if (offers.length === 0) return '';" in js, (
        "the next-artifact block must render nothing when no tool in the turn "
        "maps to a known next step"
    )
    assert "NEXT_ARTIFACT" in js and "create_option" in js


def test_pending_approvals_card_states_whose_queue_it_is_not_a_dead_control():
    """The inline pending-approvals card must NOT offer a modal-open button.

    Segregation of duties: AIChatApprovalService.get_approver_queue() excludes
    the actor's own requests, so the requester — the only person who ever sees
    this card — opening the approvals modal always gets "No approvals waiting
    for your review". A button that always opens an empty modal for its only
    viewer reads as broken, so the card instead names whose queue it is and who
    can act. This locks in that deliberate decision (see render.js), replacing
    an earlier assertion that demanded the dead modal-open button.
    """
    js = _render_js()
    assert "function renderPendingApprovals(" in js
    assert 'data-modal-open="ai-chat-approvals-modal"' not in js, (
        "the requester's own card must not open an approvals modal that is "
        "always empty for them (segregation of duties)"
    )
    assert "Approvals for review" in js, (
        "the card must point to where another reviewer acts on it"
    )


def test_every_answer_path_renders_the_same_footer():
    """Streamed, completed and non-streaming fallback must not diverge.

    The fallback previously carried sources only, so an answer that arrived
    through it showed no receipts at all — the user would see a write happen in
    prose with no record of it.
    """
    js = _render_js()
    assert js.count("function renderAnswerFooter(") == 1
    assert "renderAnswerFooter(metadata)" in js, "the completed path skips the footer"
    assert "renderAnswerFooter(meta);" in js, "the streamed path skips the footer"

    app_js = (ROOT / "app/static/js/ai_chat/app.js").read_text(encoding="utf-8")
    assert "actions: data.actions_taken || []" in app_js, (
        "the non-streaming fallback drops receipts"
    )


def test_a_url_is_built_on_a_background_thread_with_only_an_application_context(app):
    """A streamed turn has no request, yet its sources must still link to the record."""
    with app.app_context():
        url = AgentRunner._source_url("application", {"id": 7, "name": "Salesforce"})
    assert url == "/applications/7", url
