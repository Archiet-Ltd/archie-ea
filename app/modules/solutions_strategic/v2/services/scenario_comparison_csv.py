"""The comparison of saved scenarios as a CSV file, written by the one CSV writer the server exports use."""

import csv
import io
from typing import Any, Dict, List, Optional

# A cell that starts with one of these is read as a formula by a spreadsheet.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

NOT_RECORDED = "Not recorded"
NOT_SAVED = "Not saved with this scenario"


def safe_text(value: Any) -> str:
    """Text a person typed, kept as text: a leading formula character is neutralised with a single quote."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _count(items: Any) -> str:
    return str(len(items)) if isinstance(items, list) else "Not scored"


def _number(value: Any, money: bool) -> str:
    if value is None:
        return "Not worked out"
    return "%.2f" % value if money else str(value)


def figure_text(figure: Dict[str, Any]) -> str:
    """One figure of a run as the dialog draws it: the figure now, after the change, and the change worded as the screen words it."""
    money = bool(figure.get("money"))
    text = "%s to %s" % (_number(figure.get("before"), money), _number(figure.get("after"), money))
    change = figure.get("change")
    if change is None:
        return text
    return text + (" (No change)" if change == 0 else " (%s %s)" % ("Up" if change > 0 else "Down", _number(abs(change), money)))


def figure_labels(scenarios: List[Dict[str, Any]]) -> List[str]:
    """Every figure label any chosen scenario carries, in the order they first appear."""
    labels: List[str] = []
    for scenario in scenarios:
        for figure in (scenario.get("analysis_result") or {}).get("figures") or []:
            if figure.get("label") not in labels:
                labels.append(figure.get("label"))
    return labels


def comparison_csv(scenarios: List[Dict[str, Any]]) -> str:
    """Rows are the metrics the dialog shows; every cell is quoted and no name reaches a spreadsheet as a formula."""
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")

    def results(s):
        return s.get("analysis_result") or {}

    def cost(s) -> Optional[float]:
        value = results(s).get("estimated_risk_cost")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    writer.writerow(["Metric"] + [safe_text(s["name"]) for s in scenarios])
    writer.writerow(["Element"] + [safe_text((s.get("element") or {}).get("name")) for s in scenarios])
    writer.writerow(["Change Type"] + [safe_text(s.get("change_label") or s.get("change_type")) for s in scenarios])
    writer.writerow(["Impact Severity"] + [safe_text(results(s).get("severity")) for s in scenarios])
    writer.writerow(["Affected Capabilities"] + [_count(results(s).get("impacted_capabilities")) for s in scenarios])
    writer.writerow(["Affected Processes"] + [_count(results(s).get("impacted_processes")) for s in scenarios])
    writer.writerow(["Financial Risk (organisation currency)"] + [
        NOT_RECORDED if cost(s) is None else _number(cost(s), True) for s in scenarios])
    writer.writerow(["Direct Dependencies"] + [_count(results(s).get("dependencies")) for s in scenarios])
    for label in figure_labels(scenarios):
        cells = []
        for s in scenarios:
            match = next((f for f in results(s).get("figures") or [] if f.get("label") == label), None)
            cells.append(NOT_SAVED if match is None else figure_text(match))
        writer.writerow([safe_text(label)] + cells)
    return out.getvalue()
