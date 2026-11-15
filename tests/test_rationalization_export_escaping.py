"""RationalizationExportService.generate_pdf interpolated app.name (a
user-entered application name) into a hand-built HTML report with zero
escaping. Found 11 Sep 2026 while triaging the raw-html-escaping gate.

Uses a monkeypatched get_scored_apps rather than a full DB fixture, since
the escaping bug is entirely in string formatting downstream of that call.
"""
from unittest.mock import MagicMock

from app.services.rationalization_export_service import RationalizationExportService

MALICIOUS = '<img src=x onerror=alert(1)>'


def test_generate_pdf_escapes_application_name(monkeypatch):
    app = MagicMock()
    app.name = MALICIOUS

    score = MagicMock()
    score.overall_health_score = 42
    score.disposition_action = "TOLERATE"
    score.estimated_annual_savings = 1000
    score.rationalization_action = "TOLERATE"

    monkeypatch.setattr(
        RationalizationExportService, "get_scored_apps",
        staticmethod(lambda scope=None: [(score, app)]),
    )

    html = RationalizationExportService.generate_pdf(show_costs=False).decode("utf-8")

    assert MALICIOUS not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
