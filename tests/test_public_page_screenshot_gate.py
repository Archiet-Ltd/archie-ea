"""The screenshot gate (scripts/check_public_page_screenshots.py) catches what it claims to."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_public_page_screenshots", ROOT / "scripts" / "check_public_page_screenshots.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def test_committed_baseline_matches_current_offenders_exactly():
    assert set(json.loads(gate.BASELINE_FILE.read_text())["urls"]) == set(gate.find_offenders())


def test_known_unshot_pages_are_caught():
    offenders = gate.find_offenders()
    assert "/modules/diagrams" in offenders


def test_held_pages_are_exempt():
    from app.services.public_pages import load_all_pages

    held = {p.url for p in load_all_pages() if p.is_held}
    assert held, "expected at least one held page"
    assert not held & set(gate.find_offenders())


def test_a_live_role_page_without_a_screenshot_is_caught(monkeypatch):
    from app.services import public_pages

    real = public_pages.load_feed_pages

    def with_live_roles():
        pages = list(real())
        for p in public_pages.load_all_pages():
            if p.page_family == "role":
                p.front_matter["state"] = "on_main"
                pages.append(p)
        return pages

    monkeypatch.setattr(public_pages, "load_feed_pages", with_live_roles)
    offenders = gate.find_offenders()
    assert "/roles/cto-and-cio" in offenders


def test_a_removed_image_is_a_new_offender(monkeypatch):
    from app.services import public_pages

    monkeypatch.setattr(public_pages, "IMG_MODULES_DIR", ROOT / "app" / "static" / "img" / "nowhere")
    assert "/modules/arb" in gate.find_offenders()


@pytest.mark.parametrize("size,ok", [(0, False), (10, False)])
def test_tiny_file_is_not_a_real_image(tmp_path, monkeypatch, size, ok):
    (tmp_path / "app" / "static").mkdir(parents=True)
    f = tmp_path / "app" / "static" / "x.webp"
    f.write_bytes(b"0" * size)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    assert (gate._image_problem({"url": "/static/x.webp"}) is None) is ok
