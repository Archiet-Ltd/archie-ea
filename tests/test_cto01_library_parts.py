"""The fifteen CTO screens are built from the library's parts.

Scores each page template with the repository's own shadcn scanner and holds every one at zero raw
controls, raw colours, shadows, inline styles and missing macro imports. Also pins two behaviours the
rebuild added: a value stream row can be opened, and the place-on-radar form no longer carries the
`data-id` attribute that a base rule turns monospace.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "entelim_shadcn_compliance", ROOT / "scripts" / "qa" / "entelim_shadcn_compliance.py")
scanner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scanner)

ROOM = sorted((ROOT / "app/templates/solutions/transformation_room").glob("*.html"))
OTHERS = [
    ROOT / "app/modules/intelligence/templates/intelligence/value_streams_at_risk.html",
    ROOT / "app/templates/architecture/cto_scorecard.html",
    ROOT / "app/templates/tech_radar/classify.html",
]


@pytest.mark.parametrize("path", ROOM + OTHERS, ids=lambda p: p.name)
def test_template_scores_zero(path):
    score = scanner.score(path.read_text(encoding="utf-8"))
    assert score["total"] == 0, "%s: %r" % (path.name, score)


def test_room_has_twelve_templates():
    assert len(ROOM) == 12


def test_value_stream_row_links_to_its_stream():
    table = (ROOT / "app/modules/intelligence/templates/intelligence/_value_streams_at_risk_table.html").read_text(
        encoding="utf-8")
    assert "data-vsr-open" in table and "/value-streams/" in table


def test_radar_form_avoids_the_monospace_data_id_attribute():
    source = (ROOT / "app/templates/tech_radar/classify.html").read_text(encoding="utf-8")
    assert "data-id=" not in source and ":data-id'" not in source
