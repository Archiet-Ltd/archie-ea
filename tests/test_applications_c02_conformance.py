"""Hook preservation for the applications pages after the conformance conversion (C-02)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app" / "templates" / "applications"
LIST = (ROOT / "list_simple.html").read_text()
DASH = (ROOT / "dashboard.html").read_text()


def test_list_hooks_survive():
    for hook in ("btn-match-vendors", "btn-ai-map", "th-sort-id", "th-sort-name", "th-sort-type",
                 "th-sort-lifecycle-status", "btn-delete-{{ app.id }}", "btn-delete-mobile-{{ app.id }}"):
        assert 'data-testid="%s"' % hook in LIST, hook
    for handler in ("openAiMapModal()", "sortBy('id')", "sortBy('name')", "clearSelection()",
                    "openMappingForApp(el.dataset.appMapId, el.dataset.appMapName, 'archimate')", "uselect(u)"):
        assert handler in LIST, handler
    for field in ("ai-map-max", "ca-type", "ca-criticality", "ca-status"):
        assert re.search(r'id="%s" aria-label="[^"]+"' % field, LIST), field
    assert "rounded-xl" not in LIST


def test_dashboard_has_no_mermaid_and_has_the_answer_card():
    assert "mermaid" not in DASH.lower()
    assert 'data-testid="app-impact-answer"' in DASH
    assert 'data-testid="app-impact-open-composer"' in DASH
    assert "answer_cascade" in DASH
    assert DASH.count('aria-label="Remove ') >= 3
