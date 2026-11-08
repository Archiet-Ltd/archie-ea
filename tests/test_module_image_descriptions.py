"""A module picture's description is worked out from the screen it was taken of, and says nothing the screen did not.

The descriptions used to be typed into the page registry with figures that drifted from the pictures (a board of
fifteen reviews described as nineteen, one active framework described where three were shown).
"""
from __future__ import annotations

import json
import re

import pytest

from app.services.public_pages import (
    IMG_MODULES_DIR, MODULE_CAPTURES, MODULE_PHONE_ALTS, STANDARD_MODULE_IMAGES, module_caption, module_image_alt)
from scripts.capture.image_descriptions import CannotDescribe, describe, number_words, window_words

WORDS = {number_words(n) for n in range(0, 100)} | {window_words(180)}


class Frame:
    """What a cropped frame offers a description: its text and the count of elements matching a selector."""

    def __init__(self, text, counts=None):
        self.text, self.counts = text, counts or {}

    def locator(self, selector):
        counts = self.counts

        class Found:
            def count(self):
                return counts.get(selector, 0)

            def evaluate_all(self, _script):
                return [str(c) for c in counts.get(selector, [])]

            @property
            def first(self):
                return self

            def inner_text(self):
                return counts.get(selector, "")
        return Found()


def _figures(text):
    return set(re.findall(r"\d[\d,.]*|\b(?:%s)\b" % "|".join(sorted(WORDS, key=len, reverse=True)), text))


def test_a_board_description_follows_the_figures_on_its_screen():
    def board(total, pending, rate, outside):
        text = ("Total reviews %d Pending %d Approval rate %s%% Cycle time 9.6 days %d of the %d pending reviews are "
                "not yet in a review cycle, so they are not listed below." % (total, pending, rate, outside, pending))
        return describe("arb", "desktop", Frame(text, {"tbody tr": 4}), text)

    alt, facts = board(15, 10, "80.0", 6)
    assert "fifteen reviews, ten of them pending" in alt and "80 per cent" in alt and "six of the ten" in alt
    other, facts = board(19, 12, "71.4", 8)
    assert "nineteen reviews, twelve of them pending" in other and "71.4 per cent" in other and "eight of the twelve" in other
    assert facts["total"] == 19 and facts["outside_queue"] == 8


def test_a_description_refuses_a_figure_its_screen_does_not_show():
    with pytest.raises(CannotDescribe):
        describe("compliance-frameworks", "phone", Frame("Active frameworks 3 Controls 97"), "Active frameworks 3 Controls 97")
    with pytest.raises(CannotDescribe):
        text = "Initiatives 7 1 red 2 amber 4 green"
        describe("portfolio", "desktop", Frame(text, {"tbody tr": 6}), text)


# Words that name the layout of a screen rather than a figure on it.
STRUCTURE = ("five-by-five", "three-year", "four figures", "one card each", "one above another", "nine-block")


def _stray(text, facts):
    for phrase in STRUCTURE:
        text = text.replace(phrase, "")
    title = facts.get("title", "")
    text = text.replace(title[:1].lower() + title[1:], "")
    allowed = set()
    for number in re.findall(r"\d+(?:\.\d+)?", json.dumps(facts)):
        value = float(number)
        allowed.add(number)
        if value == int(value):
            allowed |= {"%d" % value, number_words(int(value)), window_words(int(value))}
    return {f.rstrip(".,") for f in _figures(text)} - allowed


def test_every_standard_picture_carries_a_description_built_only_from_its_recorded_figures():
    manifest = json.loads((IMG_MODULES_DIR / "captures.json").read_text(encoding="utf-8"))
    assert set(manifest) >= STANDARD_MODULE_IMAGES
    for slug in sorted(STANDARD_MODULE_IMAGES):
        for variant in ("desktop", "phone"):
            entry = manifest[slug][variant]
            assert entry.get("alt") and "facts" in entry, (slug, variant)
            assert not _stray(entry["alt"], entry["facts"]), (slug, variant, entry["alt"])


def test_the_page_registry_holds_no_figure_for_a_standard_picture():
    for slug, _path, _persona, _caption, alt in MODULE_CAPTURES:
        if slug in STANDARD_MODULE_IMAGES:
            assert not _stray(alt, {}), (slug, alt)
    for slug, alt in MODULE_PHONE_ALTS.items():
        assert not _stray(alt, {}), (slug, alt)


def test_no_page_caption_holds_a_figure_written_by_hand():
    for slug, _path, _persona, caption, _alt in MODULE_CAPTURES:
        assert not _stray(caption, {}), (slug, caption)


def test_the_portfolio_caption_takes_its_count_from_the_figures_captured_with_the_picture(tmp_path, monkeypatch):
    from app.services import public_pages

    caption = next(c for slug, _p, _q, c, _a in MODULE_CAPTURES if slug == "portfolio")
    shown = json.loads((IMG_MODULES_DIR / "captures.json").read_text(encoding="utf-8"))["portfolio"]["desktop"]["facts"]
    assert "%d initiatives" % shown["initiatives"] in module_caption("portfolio", caption)

    for count, noun in ((9, "9 initiatives"), (1, "1 initiative")):
        (tmp_path / "captures.json").write_text(
            json.dumps({"portfolio": {"desktop": {"facts": {"initiatives": count}}}}), encoding="utf-8")
        monkeypatch.setattr(public_pages, "IMG_MODULES_DIR", tmp_path)
        assert noun in module_caption("portfolio", caption)
    (tmp_path / "captures.json").write_text("{}", encoding="utf-8")
    assert not re.search(r"\d", module_caption("portfolio", caption))


def test_the_page_serves_the_description_kept_with_the_picture():
    for slug in STANDARD_MODULE_IMAGES:
        assert module_image_alt(slug) and module_image_alt(slug, "phone")
