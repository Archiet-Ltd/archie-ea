"""Tool risk classifier: declared risk class vs actual record types touched.

A tool whose test-run touches more record types than declared is blocked
from activation until reclassified and re-approved at the higher level.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture(autouse=True)
def _reset_classifier():
    """Reset the singleton classifier before each test for isolation."""
    from app.modules.ai_chat.services.tool_risk_classifier import reset_tool_risk_classifier

    reset_tool_risk_classifier()


# ------------------------------------------------------------------ #
# ClassificationResult and risk class ordering                        #
# ------------------------------------------------------------------ #


def test_risk_class_order_is_correct():
    """read < propose < write < external_action."""
    from app.modules.ai_chat.services.tool_risk_classifier import (
        RISK_CLASS_ORDER,
        _risk_class_rank,
    )

    assert _risk_class_rank("read") == 0
    assert _risk_class_rank("propose") == 1
    assert _risk_class_rank("write") == 2
    assert _risk_class_rank("external_action") == 3
    # Unknown risk class fails closed (highest rank)
    assert _risk_class_rank("unknown") >= len(RISK_CLASS_ORDER)


def test_classifier_unknown_tool_fails():
    """A tool not in the registry returns non-compliant."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    result = classifier.classify_tool("nonexistent_tool_xyz")
    assert result.is_compliant is False
    assert "not found" in result.mismatch_details


def test_classifier_write_tool_with_no_test_trace_fails_closed():
    """A write tool with no test trace cannot be verified — fail closed."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    # create_solution is a write tool with record_types_written=["Solution"]
    result = classifier.classify_tool("create_solution")
    # No test trace has been recorded, so it fails closed
    assert result.is_compliant is False
    assert "no test execution trace" in result.mismatch_details


def test_classifier_read_tool_with_no_test_trace_is_compliant():
    """A read tool with no test trace is still compliant (no writes to verify)."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    result = classifier.classify_tool("query_capability_gaps")
    # Read tools don't need test traces
    assert result.is_compliant is True


def test_classifier_tool_with_matching_trace_is_compliant():
    """A tool whose test trace matches declared record types is compliant."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    classifier.record_test_trace("create_solution", ["Solution"])
    result = classifier.classify_tool("create_solution")
    assert result.is_compliant is True
    assert result.mismatch_details is None


def test_classifier_tool_with_undeclared_touches_is_blocked():
    """A tool that touches record types it didn't declare is non-compliant."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    # Declared: ["Solution"], but test touches Solution AND ApplicationComponent
    classifier.record_test_trace("create_solution", ["Solution", "ApplicationComponent"])
    result = classifier.classify_tool("create_solution")
    assert result.is_compliant is False
    assert "undeclared record types" in result.mismatch_details
    assert "ApplicationComponent" in result.mismatch_details


def test_classifier_write_tool_declared_as_read_is_blocked():
    """A tool that writes but declares 'read' risk class is blocked."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    # Record a test trace showing writes, but the tool declares "read"
    classifier.record_test_trace("query_capability_gaps", ["Solution"])
    result = classifier.classify_tool("query_capability_gaps")
    # It declares "read" with no record_types_written but actually writes — blocked
    assert result.is_compliant is False
    # Either undeclared-touch or risk-class mismatch message
    assert (
        "undeclared record types" in result.mismatch_details
        or "Must be at least 'write'" in result.mismatch_details
    )


def test_can_activate_tool_returns_boolean_and_reason():
    """can_activate_tool returns (bool, reason_if_blocked)."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    classifier.record_test_trace("create_solution", ["Solution"])
    can, reason = classifier.can_activate_tool("create_solution")
    assert can is True
    assert reason is None

    # Unknown tool
    can, reason = classifier.can_activate_tool("nonexistent")
    assert can is False
    assert reason is not None


def test_get_all_classifications_returns_all_tools():
    """get_all_classifications returns a result for every registered tool."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

    classifier = get_tool_risk_classifier()
    results = classifier.get_all_classifications()
    assert len(results) == len(TOOL_SCHEMA_BY_NAME)
    tool_names = {r.tool_name for r in results}
    assert tool_names == set(TOOL_SCHEMA_BY_NAME.keys())


def test_get_non_compliant_tools_filters_correctly():
    """get_non_compliant_tools returns only failing tools."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    # Make one tool compliant
    classifier.record_test_trace("create_solution", ["Solution"])
    non_compliant = classifier.get_non_compliant_tools()
    # create_solution should now be compliant, so not in the list
    nc_names = {r.tool_name for r in non_compliant}
    assert "create_solution" not in nc_names


def test_export_catalogue_includes_risk_class_and_record_types():
    """Catalogue export includes risk_class, record_types_written, and compliance."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    classifier.record_test_trace("create_solution", ["Solution"])
    catalogue = classifier.export_catalogue(filter_write_only=False)

    assert len(catalogue) > 0
    # Find create_solution in the catalogue
    cs = next((c for c in catalogue if c["name"] == "create_solution"), None)
    assert cs is not None
    assert cs["risk_class"] == "write"
    assert "Solution" in cs["declared_record_types_written"]
    assert cs["mutates"] is True
    assert "classification_compliant" in cs


def test_export_catalogue_filter_write_only():
    """Filtering to write tools only excludes read tools."""
    from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier

    classifier = get_tool_risk_classifier()
    all_catalogue = classifier.export_catalogue(filter_write_only=False)
    write_catalogue = classifier.export_catalogue(filter_write_only=True)

    assert len(write_catalogue) < len(all_catalogue)
    # Every entry in write_catalogue should have mutates=True
    for entry in write_catalogue:
        assert entry["mutates"] is True
    # Read tools should not appear
    write_names = {c["name"] for c in write_catalogue}
    assert "query_capability_gaps" not in write_names


def test_record_types_written_field_present_on_all_schemas():
    """Every tool schema has a record_types_written field."""
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    for schema in TOOL_SCHEMAS:
        assert "record_types_written" in schema, (
            "Tool '%s' is missing record_types_written field" % schema["name"]
        )
        assert isinstance(schema["record_types_written"], list), (
            "Tool '%s' record_types_written is not a list" % schema["name"]
        )


def test_mutating_tools_have_non_empty_record_types():
    """Every mutating tool declares at least one record type written (or external_action)."""
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    for schema in TOOL_SCHEMAS:
        if schema.get("mutates") and schema.get("risk_class") not in ("external_action",):
            assert len(schema["record_types_written"]) > 0, (
                "Mutating tool '%s' declares no record_types_written" % schema["name"]
            )