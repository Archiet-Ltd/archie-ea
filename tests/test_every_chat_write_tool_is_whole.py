"""Every tool the chat can write with is whole, so no change is left "in review" for want of a part.

A whole write tool has a target the review reads from the organisation's own records, the permission pair the
role rule asks about, and the kind of record it writes. Tools that were written before this rule are listed
below; the list can only get shorter, and a tool that becomes whole must leave it.
"""

from app.modules.ai_chat.services.change_review import _TOOL_TARGETS
from app.modules.ai_chat.tools.executor import ToolExecutor
from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

# Written before the rule. The generated element tools (route "ArchiMateElement") share one target and are
# counted as one exception.
KNOWN_EXCEPTIONS = {
    "bulk_update_application_status", "create_adr", "create_archimate_element",
    "create_archimate_relationship", "create_constraint", "create_contract", "create_driver",
    "create_option", "create_programme", "create_requirement", "create_solution", "create_vendor",
    "extract_contract_from_document", "generate_blueprint_narrative", "link_application_to_capability",
    "link_application_to_solution", "link_capability_to_solution", "link_vendor_product",
    "mark_option_recommended", "merge_capabilities", "poll_infrastructure",
    "record_capability_maturity", "run_inference_engine", "score_rationalization",
    "submit_for_arb_review", "update_application_status", "update_solution_fields",
    "update_solution_phase", "upsert_license"
}
KNOWN_EXCEPTION_CEILING = 29
GENERATED_ELEMENT_TOOLS = "generated element tools"


def _chat_write_tools():
    return [s for s in TOOL_SCHEMAS if s.get("mutates") and "chat" in s.get("surfaces", ["chat"])]


def _is_whole(schema):
    return (schema["name"] in _TOOL_TARGETS and bool(schema.get("permission"))
            and bool(schema.get("record_kind")) and hasattr(ToolExecutor, "_tool_" + schema["name"]))


def _generated(schema):
    return schema.get("route") == "ArchiMateElement" and schema["name"] != "create_archimate_element"


def test_every_chat_write_tool_is_whole_or_a_known_exception():
    unlisted = sorted(s["name"] for s in _chat_write_tools()
                      if not _is_whole(s) and not _generated(s) and s["name"] not in KNOWN_EXCEPTIONS)
    assert unlisted == [], "These write tools have no target, permission pair or record kind: %s" % unlisted


def test_a_tool_that_became_whole_leaves_the_exception_list():
    whole = {s["name"] for s in _chat_write_tools() if _is_whole(s)}
    assert sorted(whole & KNOWN_EXCEPTIONS) == []


def test_the_exception_list_only_gets_shorter():
    assert len(KNOWN_EXCEPTIONS) <= KNOWN_EXCEPTION_CEILING
    registered = {s["name"] for s in TOOL_SCHEMAS}
    assert sorted(KNOWN_EXCEPTIONS - registered) == [], "a listed tool no longer exists; remove it"


def test_the_goal_and_risk_tools_are_whole():
    names = {s["name"] for s in _chat_write_tools() if _is_whole(s)}
    assert {"create_goal", "update_goal", "create_risk", "update_risk"} <= names


def test_a_whole_tool_returns_the_address_of_its_record():
    import inspect

    for name in ("create_goal", "update_goal", "create_risk", "update_risk"):
        assert '"record_url"' in inspect.getsource(getattr(ToolExecutor, "_tool_" + name)), name
