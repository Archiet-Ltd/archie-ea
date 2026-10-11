"""
Natural-Language Query Router (A95-002)

Maps common discovery-style natural-language queries to structured backend API
calls so the chat can return tabular data without an LLM round-trip.

The router uses weighted keyword matching (similar to IntentClassifierService)
to detect known query intents.  When a match is found it returns a structured
result dict; when no match is found it returns ``None`` so the caller can
fall through to the regular LLM pipeline.

Usage::

    from app.modules.ai_chat.services.nl_query_router import NLQueryRouter

    router = NLQueryRouter()
    result = router.try_route(user_message)
    if result is not None:
        # result contains structured tabular data + metadata
        ...
    else:
        # fall through to LLM
        ...
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query pattern definitions
# ---------------------------------------------------------------------------
# Each pattern has:
#   keywords   – list of phrases; matched case-insensitively with word boundaries
#   api_path   – the backend endpoint to call (internal Flask route)
#   api_method – HTTP method
#   api_params – default query params / JSON body
#   label      – human-readable description of the query
#   domain     – chat domain this maps to
#   dashboard_url – optional deep-link for "View in dashboard" CTA
#   formatter  – name of a _format_* helper that post-processes raw API data
# ---------------------------------------------------------------------------

QUERY_PATTERNS: List[Dict[str, Any]] = [
    {
        "id": "apps_no_owner",
        "keywords": [
            "applications without owner",
            "applications without business owner",
            "apps without owner",
            "apps without business owner",
            "apps no owner",
            "applications missing owner",
            "apps missing business owner",
            "unowned applications",
        ],
        "api_path": "/api/applications/table-data",
        "api_method": "GET",
        "api_params": {"filter_owner": "none"},
        "label": "Applications without a business owner",
        "domain": "technology",
        "dashboard_url": "/application-management/applications?owner=empty",
        "formatter": "applications",
    },
    {
        "id": "expiring_vendors",
        "keywords": [
            "expiring vendors",
            "vendors expiring",
            "vendors are expiring",
            "vendor expiry",
            "vendors expiring in 90 days",
            "vendor contracts expiring",
            "vendors about to expire",
            "vendor lifecycle risks",
            "vendor end of life",
        ],
        "api_path": "/api/ai-gap-detection/vendor-lifecycle",
        "api_method": "GET",
        "api_params": {},
        "label": "Vendors with expiring contracts or lifecycle risks",
        "domain": "vendor_intelligence",
        "dashboard_url": "/dashboard/vendor-catalog",
        "formatter": "vendor_lifecycle",
    },
    {
        "id": "capability_gaps",
        "keywords": [
            "capability gaps",
            "unmapped capabilities",
            "uncovered capabilities",
            "capabilities without applications",
            "capability coverage gaps",
            "low coverage capabilities",
            "gaps in capabilities",
            "unmapped apqc",
        ],
        "api_path": "/api/ai-gap-detection/uncovered",
        "api_method": "GET",
        "api_params": {},
        "label": "Capabilities without application coverage",
        "domain": "gap_analysis",
        "dashboard_url": "/capabilities/",
        "formatter": "capability_gaps",
    },
    {
        "id": "vendor_risk",
        "keywords": [
            "vendor risk",
            "vendor risk scores",
            "risky vendors",
            "high risk vendors",
            "vendor risk assessment",
            "vendor risk analysis",
        ],
        "api_path": "/api/predictive-analytics/vendor-risk-scores",
        "api_method": "GET",
        "api_params": {},
        "label": "Vendor risk assessment scores",
        "domain": "vendor_intelligence",
        "dashboard_url": "/dashboard/vendor-catalog",
        "formatter": "vendor_risk",
    },
    {
        "id": "duplicate_applications",
        "keywords": [
            "duplicate applications",
            "duplicates",
            "duplicate apps",
            "similar applications",
            "application duplicates",
            "find duplicates",
            "redundant applications",
        ],
        "api_path": "/api/applications/duplicates",
        "api_method": "GET",
        "api_params": {},
        "label": "Potential duplicate applications",
        "domain": "technology",
        "dashboard_url": "/duplicate-detection/",
        "formatter": "duplicates",
    },
    {
        "id": "rationalization_candidates",
        "keywords": [
            "rationalization candidates",
            "rationalization opportunities",
            "apps to retire",
            "elimination candidates",
            "applications for retirement",
            "consolidation opportunities",
            "rationalize applications",
            "rationalization",
        ],
        "api_path": "/dashboard/api/rationalization/elimination-candidates",
        "api_method": "GET",
        "api_params": {},
        "label": "Application rationalization / elimination candidates",
        "domain": "technology",
        "dashboard_url": "/applications/rationalization",
        "formatter": "rationalization",
    },
    {
        "id": "low_maturity_capabilities",
        "keywords": [
            "low maturity capabilities",
            "capabilities with low maturity",
            "have low maturity",
            "low maturity",
            "immature capabilities",
            "maturity below",
            "lowest maturity",
        ],
        "api_path": "/api/ai-gap-detection/low-coverage",
        "api_method": "GET",
        "api_params": {},
        "label": "Capabilities with low maturity or coverage",
        "domain": "business_capability",
        "dashboard_url": "/capabilities/",
        "formatter": "capability_gaps",
    },
    {
        "id": "gap_summary",
        "keywords": [
            "gap summary",
            "gap analysis summary",
            "overall gaps",
            "gap overview",
            "architecture gaps",
        ],
        "api_path": "/api/ai-gap-detection/summary",
        "api_method": "GET",
        "api_params": {},
        "label": "Gap analysis summary across all domains",
        "domain": "gap_analysis",
        "dashboard_url": "/capabilities/",
        "formatter": "gap_summary",
    },
]


class NLQueryRouter:
    """Maps natural-language chat messages to structured backend API calls.

    The router pre-compiles regex patterns from ``QUERY_PATTERNS`` at init time
    for fast matching.  Each keyword is wrapped in word-boundary anchors.
    """

    def __init__(self) -> None:
        # Build compiled patterns: list of (compiled_re, pattern_dict)
        self._compiled: List[tuple] = []
        for pattern in QUERY_PATTERNS:
            for kw in pattern["keywords"]:
                regex = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
                self._compiled.append((regex, pattern))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(self, message: str) -> Optional[Dict[str, Any]]:
        """Return the best-matching pattern dict, or ``None`` if no match.

        Scoring: each keyword hit adds ``len(keyword)`` to the pattern's score
        (longer keywords = more specific = higher weight).  The pattern with the
        highest score wins, provided it exceeds a minimum threshold of 5 chars.
        """
        if not message or not isinstance(message, str):
            return None

        scores: Dict[str, int] = {}
        pattern_map: Dict[str, Dict] = {}

        for regex, pattern in self._compiled:
            if regex.search(message):
                pid = pattern["id"]
                # Use keyword length as weight — longer phrases are more specific
                kw_len = len(regex.pattern) - 4  # subtract \b...\b overhead approx
                scores[pid] = scores.get(pid, 0) + max(kw_len, 1)
                pattern_map[pid] = pattern

        if not scores:
            return None

        best_id = max(scores, key=scores.get)  # type: ignore[arg-type]
        if scores[best_id] < 5:
            return None

        return pattern_map[best_id]

    def try_route(self, message: str) -> Optional[Dict[str, Any]]:
        """Attempt to route a message to a structured API response.

        Returns a fully-formed chat response dict if a pattern matches and
        the backend API call succeeds, or ``None`` to fall through to LLM.
        """
        pattern = self.match(message)
        if pattern is None:
            return None

        logger.info(
            "A95-002: NL query matched pattern '%s' for message: %.80s",
            pattern["id"],
            message,
        )

        try:
            data = self._call_api(pattern)
        except Exception as exc:
            logger.warning(
                "A95-002: API call failed for pattern '%s': %s — falling through to LLM",
                pattern["id"],
                exc,
            )
            return None

        # Format result into a chat-friendly response
        formatted = self._format_response(pattern, data)
        return formatted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_api(self, pattern: Dict[str, Any]) -> Dict[str, Any]:
        """Call the internal Flask API endpoint and return its JSON payload."""
        from app.utils.internal_api import call_internal_api

        status, data = call_internal_api(
            method=pattern["api_method"],
            path=pattern["api_path"],
            query_string=pattern.get("api_params") if pattern["api_method"].upper() == "GET" else None,
            json_body=pattern.get("api_params") if pattern["api_method"].upper() != "GET" else None,
        )

        if status == 200:
            return data
        else:
            logger.warning(
                "A95-002: API %s returned status %s",
                pattern["api_path"],
                status,
            )
            return {}

    def _format_response(self, pattern: Dict[str, Any], api_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format raw API data into a structured chat response with table."""
        formatter_name = pattern.get("formatter", "generic")
        formatter = getattr(self, f"_format_{formatter_name}", self._format_generic)

        table_html, row_count = formatter(api_data)

        # Build dashboard link
        dashboard_link = ""
        if pattern.get("dashboard_url"):
            dashboard_link = (
                f'\n\n<a href="{pattern["dashboard_url"]}" '  # raw-html-ok: dashboard_url is a hardcoded internal route path from this file's own pattern dict, never user input
                f'class="inline-flex items-center gap-1 text-sm text-primary hover:underline">'
                f'View in dashboard &rarr;</a>'
            )

        if row_count == 0:
            response_text = (
                f"**{pattern['label']}**\n\n"
                f"No results found for this query. The data may not yet be populated "
                f"or all items pass the filter criteria."
                f"{dashboard_link}"
            )
        else:
            response_text = (
                f"**{pattern['label']}**\n\n"
                f"Found **{row_count}** result{'s' if row_count != 1 else ''}:\n\n"
                f"{table_html}"
                f"{dashboard_link}"
            )

        return {
            "success": True,
            "response": response_text,
            "domain": pattern.get("domain", "general"),
            "confidence": 1.0,
            "metadata": {
                "nl_query_routed": True,
                "pattern_id": pattern["id"],
                "api_path": pattern["api_path"],
                "result_count": row_count,
            },
            "context_used": False,
            "follow_up_questions": self._follow_ups(pattern),
            "processing_metadata": {
                "nl_query_routed": True,
                "pattern_id": pattern["id"],
            },
        }

    # ------------------------------------------------------------------
    # Formatters — each returns (html_table_string, row_count)
    # ------------------------------------------------------------------

    def _format_generic(self, data: Dict[str, Any]) -> tuple:
        """Fallback formatter: render first-level list as a simple table."""
        items = data.get("data") or data.get("items") or data.get("results") or []
        if isinstance(items, dict):
            items = [items]
        if not items:
            return ("", 0)

        # Auto-detect columns from first item keys
        if isinstance(items[0], dict):
            columns = list(items[0].keys())[:6]  # cap at 6 columns
            rows = []
            for item in items[:50]:  # cap at 50 rows
                row = [str(item.get(c, "")) for c in columns]
                rows.append(row)
            return (self._build_markdown_table(columns, rows), len(items))

        return ("", 0)

    def _format_applications(self, data: Dict[str, Any]) -> tuple:
        """Format application list data."""
        items = data.get("data") or data.get("applications") or data.get("items") or []
        if not items:
            return ("", 0)

        columns = ["Name", "Status", "Business Owner", "Department"]
        rows = []
        for app in items[:50]:
            if isinstance(app, dict):
                rows.append([
                    app.get("name", app.get("application_name", "Unknown")),
                    app.get("status", app.get("lifecycle_status", "-")),
                    app.get("business_owner", app.get("owner", "-")) or "-",
                    app.get("department", app.get("business_unit", "-")) or "-",
                ])
        return (self._build_markdown_table(columns, rows), len(items))

    def _format_vendor_lifecycle(self, data: Dict[str, Any]) -> tuple:
        """Format vendor lifecycle risk data."""
        items = data.get("risks") or data.get("vendors") or data.get("data") or []
        if not items:
            return ("", 0)

        columns = ["Vendor", "Product", "Risk Level", "Contract End"]
        rows = []
        for v in items[:50]:
            if isinstance(v, dict):
                rows.append([
                    v.get("vendor_name", v.get("vendor", "Unknown")),
                    v.get("product_name", v.get("product", "-")),
                    v.get("risk_level", v.get("risk", "-")),
                    v.get("contract_end", v.get("end_date", v.get("expiry_date", "-"))),
                ])
        return (self._build_markdown_table(columns, rows), len(items))

    def _format_capability_gaps(self, data: Dict[str, Any]) -> tuple:
        """Format capability gap / uncovered data."""
        items = data.get("capabilities") or data.get("gaps") or data.get("data") or []
        if not items:
            return ("", 0)

        columns = ["Capability", "Level", "Coverage", "Notes"]
        rows = []
        for cap in items[:50]:
            if isinstance(cap, dict):
                rows.append([
                    cap.get("name", cap.get("capability_name", "Unknown")),
                    str(cap.get("level", cap.get("maturity_level", "-"))),
                    str(cap.get("coverage", cap.get("app_count", "-"))),
                    cap.get("notes", cap.get("description", "-")) or "-",
                ])
        return (self._build_markdown_table(columns, rows), len(items))

    def _format_vendor_risk(self, data: Dict[str, Any]) -> tuple:
        """Format vendor risk score data."""
        items = data.get("vendors") or data.get("scores") or data.get("data") or []
        if not items:
            return ("", 0)

        columns = ["Vendor", "Risk Score", "Category", "Assessment"]
        rows = []
        for v in items[:50]:
            if isinstance(v, dict):
                rows.append([
                    v.get("vendor_name", v.get("name", "Unknown")),
                    str(v.get("risk_score", v.get("score", "-"))),
                    v.get("category", v.get("risk_category", "-")),
                    v.get("assessment", v.get("recommendation", "-")),
                ])
        return (self._build_markdown_table(columns, rows), len(items))

    def _format_duplicates(self, data: Dict[str, Any]) -> tuple:
        """Format duplicate application data."""
        items = data.get("duplicates") or data.get("groups") or data.get("data") or []
        if not items:
            return ("", 0)

        columns = ["Application 1", "Application 2", "Similarity", "Status"]
        rows = []
        for dup in items[:50]:
            if isinstance(dup, dict):
                rows.append([
                    dup.get("app1_name", dup.get("application_1", "Unknown")),
                    dup.get("app2_name", dup.get("application_2", "Unknown")),
                    str(dup.get("similarity_score", dup.get("similarity", "-"))),
                    dup.get("status", dup.get("resolution_status", "Pending")),
                ])
        return (self._build_markdown_table(columns, rows), len(items))

    def _format_rationalization(self, data: Dict[str, Any]) -> tuple:
        """Format rationalization candidate data."""
        items = data.get("candidates") or data.get("applications") or data.get("data") or []
        if not items:
            return ("", 0)

        columns = ["Application", "Score", "Recommendation", "Business Impact"]
        rows = []
        for app in items[:50]:
            if isinstance(app, dict):
                rows.append([
                    app.get("name", app.get("application_name", "Unknown")),
                    str(app.get("score", app.get("rationalization_score", "-"))),
                    app.get("recommendation", app.get("disposition", "-")),
                    app.get("business_impact", app.get("impact", "-")),
                ])
        return (self._build_markdown_table(columns, rows), len(items))

    def _format_gap_summary(self, data: Dict[str, Any]) -> tuple:
        """Format gap analysis summary data."""
        summary = data.get("summary") or data
        if not summary or not isinstance(summary, dict):
            return ("", 0)

        # Build a key-value summary table
        columns = ["Metric", "Value"]
        rows = []
        key_map = {
            "total_capabilities": "Total Capabilities",
            "covered": "Covered",
            "uncovered": "Uncovered",
            "coverage_pct": "Coverage %",
            "total_gaps": "Total Gaps",
            "critical_gaps": "Critical Gaps",
            "high_gaps": "High Gaps",
            "medium_gaps": "Medium Gaps",
            "low_gaps": "Low Gaps",
        }
        for key, label in key_map.items():
            val = summary.get(key)
            if val is not None:
                rows.append([label, str(val)])

        # Also check top-level keys
        if not rows:
            for k, v in summary.items():
                if not isinstance(v, (dict, list)):
                    rows.append([k.replace("_", " ").title(), str(v)])
                if len(rows) >= 10:
                    break

        return (self._build_markdown_table(columns, rows), max(len(rows), 1))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_markdown_table(columns: List[str], rows: List[List[str]]) -> str:
        """Build a markdown-formatted table string."""
        if not columns or not rows:
            return ""

        # Header
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"

        # Body
        body_lines = []
        for row in rows:
            # Escape pipe characters in cell values
            cells = [cell.replace("|", "\\|") for cell in row]
            body_lines.append("| " + " | ".join(cells) + " |")

        return header + "\n" + separator + "\n" + "\n".join(body_lines)

    @staticmethod
    def _follow_ups(pattern: Dict[str, Any]) -> List[str]:
        """Generate contextual follow-up questions based on the matched pattern."""
        follow_up_map = {
            "apps_no_owner": [
                "Which of these applications are business critical?",
                "Can you assign owners to these applications?",
                "What is the total cost of unowned applications?",
            ],
            "expiring_vendors": [
                "What are the renewal options for expiring vendors?",
                "Which expiring vendors have the highest risk?",
                "Show vendor alternatives for the top risks",
            ],
            "capability_gaps": [
                "Which gaps have the highest business impact?",
                "Are there vendor solutions to close these gaps?",
                "What is the investment needed to fill these gaps?",
            ],
            "vendor_risk": [
                "Which high-risk vendors need immediate attention?",
                "What mitigation strategies are recommended?",
                "Show vendor alternatives for high-risk vendors",
            ],
            "duplicate_applications": [
                "What is the estimated savings from consolidation?",
                "Which duplicates should be consolidated first?",
                "Show the dependency graph for these duplicates",
            ],
            "rationalization_candidates": [
                "What is the total cost of retirement candidates?",
                "Which candidates have the fewest dependencies?",
                "Show migration options for top candidates",
            ],
            "low_maturity_capabilities": [
                "Which capabilities need the most investment?",
                "How do these compare to industry benchmarks?",
                "What applications support these capabilities?",
            ],
            "gap_summary": [
                "Show me the detailed capability gaps",
                "Which domains have the most gaps?",
                "What is the remediation priority?",
            ],
        }
        return follow_up_map.get(pattern["id"], [
            "Can you provide more details?",
            "What actions should be taken?",
            "Show related data",
        ])
