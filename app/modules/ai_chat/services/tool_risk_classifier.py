"""
Tool Risk Classifier

Compares a tool's declared risk_class against the record types its test runs
actually touch. A mismatch (declared low, touches more) blocks activation
until reclassified and re-approved at the higher level.

This implements the classification-vs-actual-touch check required by the brief.
"""

from dataclasses import dataclass
from typing import Optional

from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME


@dataclass
class ClassificationResult:
    """Result of a tool risk classification check."""
    tool_name: str
    declared_risk_class: str
    declared_record_types: list
    actual_record_types_touched: list
    is_compliant: bool
    mismatch_details: Optional[str] = None


# Risk class hierarchy (higher index = higher risk)
RISK_CLASS_ORDER = ["read", "propose", "write", "external_action"]


def _risk_class_rank(risk_class: str) -> int:
    """Return the rank of a risk class (higher = more risky)."""
    try:
        return RISK_CLASS_ORDER.index(risk_class)
    except ValueError:
        # Unknown risk class - treat as highest risk (fail closed)
        return len(RISK_CLASS_ORDER)


class ToolRiskClassifier:
    """
    Classifies tools by comparing declared risk against actual record types touched.

    The classifier uses test execution traces to determine what record types
    a tool actually writes. If a tool declares a lower risk class than what
    its actual touches warrant, it is blocked from activation.
    """

    def __init__(self):
        self._test_trace_cache = {}  # tool_name -> set of record types touched in tests

    def record_test_trace(self, tool_name: str, record_types_touched: list) -> None:
        """
        Record the record types touched during a test run of a tool.

        Called by test infrastructure after executing a tool in a test context.
        """
        if tool_name not in self._test_trace_cache:
            self._test_trace_cache[tool_name] = set()
        self._test_trace_cache[tool_name].update(record_types_touched)

    def get_test_trace(self, tool_name: str) -> set:
        """Get the recorded test trace for a tool."""
        return self._test_trace_cache.get(tool_name, set())

    def classify_tool(self, tool_name: str) -> ClassificationResult:
        """
        Classify a tool by comparing declared vs actual record types.

        Returns a ClassificationResult indicating whether the tool's declared
        risk class matches what it actually touches in tests.
        """
        schema = TOOL_SCHEMA_BY_NAME.get(tool_name)
        if not schema:
            return ClassificationResult(
                tool_name=tool_name,
                declared_risk_class="unknown",
                declared_record_types=[],
                actual_record_types_touched=[],
                is_compliant=False,
                mismatch_details=f"Tool '{tool_name}' not found in registry",
            )

        declared_risk_class = schema.get("risk_class", "write")
        declared_record_types = schema.get("record_types_written", [])
        actual_record_types = list(self.get_test_trace(tool_name))

        # If no test trace exists yet, we can't verify - fail closed for write tools
        if not actual_record_types and declared_risk_class in ("write", "external_action"):
            return ClassificationResult(
                tool_name=tool_name,
                declared_risk_class=declared_risk_class,
                declared_record_types=declared_record_types,
                actual_record_types_touched=actual_record_types,
                is_compliant=False,
                mismatch_details=(
                    f"Tool '{tool_name}' declares risk_class='{declared_risk_class}' "  # raw-html-ok: error message in JSON response, never rendered as HTML
                    f"but has no test execution trace. Cannot verify classification."
                ),
            )

        # Check if actual touches exceed declared
        undeclared_touches = set(actual_record_types) - set(declared_record_types)
        if undeclared_touches:
            # Tool touches record types it didn't declare
            return ClassificationResult(
                tool_name=tool_name,
                declared_risk_class=declared_risk_class,
                declared_record_types=declared_record_types,
                actual_record_types_touched=actual_record_types,
                is_compliant=False,
                mismatch_details=(
                    f"Tool '{tool_name}' touches undeclared record types: "
                    f"{', '.join(sorted(undeclared_touches))}. "
                    f"Declared: {', '.join(declared_record_types) or 'none'}"
                ),
            )

        # Check if declared risk class is appropriate for the record types touched
        # Write tools should declare at least "write" risk class
        if actual_record_types and declared_risk_class in ("read", "propose"):
            return ClassificationResult(
                tool_name=tool_name,
                declared_risk_class=declared_risk_class,
                declared_record_types=declared_record_types,
                actual_record_types_touched=actual_record_types,
                is_compliant=False,
                mismatch_details=(
                    f"Tool '{tool_name}' writes to record types "
                    f"{', '.join(actual_record_types)} but declares "
                    f"risk_class='{declared_risk_class}'. Must be at least 'write'."  # raw-html-ok: error message in JSON response, never rendered as HTML
                ),
            )

        # All checks passed
        return ClassificationResult(
            tool_name=tool_name,
            declared_risk_class=declared_risk_class,
            declared_record_types=declared_record_types,
            actual_record_types_touched=actual_record_types,
            is_compliant=True,
            mismatch_details=None,
        )

    def can_activate_tool(self, tool_name: str) -> tuple[bool, Optional[str]]:
        """
        Check if a tool can be activated based on its classification.

        Returns (can_activate, reason_if_blocked).
        """
        result = self.classify_tool(tool_name)
        if result.is_compliant:
            return True, None
        return False, result.mismatch_details

    def get_all_classifications(self) -> list[ClassificationResult]:
        """Get classification results for all registered tools."""
        results = []
        for tool_name in TOOL_SCHEMA_BY_NAME:
            results.append(self.classify_tool(tool_name))
        return results

    def get_non_compliant_tools(self) -> list[ClassificationResult]:
        """Get only the tools that fail classification."""
        return [r for r in self.get_all_classifications() if not r.is_compliant]

    def export_catalogue(self, filter_write_only: bool = False) -> list[dict]:
        """
        Export the complete tool catalogue with risk class and record types.

        Used by the Security Architect export feature.
        """
        catalogue = []
        for tool_name, schema in TOOL_SCHEMA_BY_NAME.items():
            if filter_write_only and not schema.get("mutates", False):
                continue

            result = self.classify_tool(tool_name)
            catalogue.append({
                "name": tool_name,
                "surfaces": schema.get("surfaces", []),
                "route": schema.get("route", ""),
                "risk_class": schema.get("risk_class", "unknown"),
                "mutates": schema.get("mutates", False),
                "tier": schema.get("tier", "auto"),
                "declared_record_types_written": schema.get("record_types_written", []),
                "actual_record_types_touched": result.actual_record_types_touched,
                "classification_compliant": result.is_compliant,
                "classification_details": result.mismatch_details,
                "description": schema.get("description", ""),
            })
        return catalogue


# Singleton instance
_tool_risk_classifier = None


def get_tool_risk_classifier() -> ToolRiskClassifier:
    """Get the singleton ToolRiskClassifier instance."""
    global _tool_risk_classifier
    if _tool_risk_classifier is None:
        _tool_risk_classifier = ToolRiskClassifier()
    return _tool_risk_classifier


def reset_tool_risk_classifier() -> None:
    """Reset the singleton (for test isolation)."""
    global _tool_risk_classifier
    _tool_risk_classifier = None