"""Every module page has a whole-journey recording, or is named with the screen and what is wrong with it.

The recordings are made by ``scripts/capture_screenshots.py --modules`` from the demonstration organisation and
registered in ``MODULE_RECORDINGS`` (``app/services/public_pages.py``); a module whose job cannot be shown end to
end is named in ``MODULE_RECORDING_DEFECTS`` with the reason, which the seat report repeats. This file keeps the
two lists complete and exclusive, keeps every recording inside the standard (30 to 90 seconds, under 2 MB, a
poster under 200 KB, a first caption that states the result, the same closing card), and checks the page serves
the recording with a summary beside it and a VideoObject for search engines.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from app.services.public_pages import (
    IMG_MODULES_DIR,
    MODULE_RECORDING_DEFECTS,
    MODULE_RECORDINGS,
    MODULE_RECORDINGS_MANIFEST,
    build_jsonld,
    get_module_recording,
    load_page,
    module_recording_slugs,
)

MAX_VIDEO_BYTES = 2 * 1024 * 1024
MAX_POSTER_BYTES = 200 * 1024
SIGN_UP = "Start free at entelim.org"
NAMES_NOT_FOR_PEOPLE = re.compile(r"claude|codex|kilo|aider|orchestrator|agent|R1-B|TB-\d|PB-\d", re.I)


def _manifest():
    return json.loads((IMG_MODULES_DIR / MODULE_RECORDINGS_MANIFEST).read_text(encoding="utf-8"))


def test_there_are_thirty_one_module_pages():
    assert len(module_recording_slugs()) == 31


def test_every_module_has_a_recording_or_a_named_defect_and_never_both():
    slugs = set(module_recording_slugs())
    recorded, defects = set(MODULE_RECORDINGS), set(MODULE_RECORDING_DEFECTS)
    assert not recorded & defects, "recorded and also listed as a defect: %s" % sorted(recorded & defects)
    assert slugs - recorded - defects == set(), "module with neither: %s" % sorted(slugs - recorded - defects)
    assert (recorded | defects) - slugs == set(), "not a module page: %s" % sorted((recorded | defects) - slugs)


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDING_DEFECTS))
def test_a_defect_names_the_screen_and_what_is_wrong(slug):
    reason = MODULE_RECORDING_DEFECTS[slug]
    assert len(reason) > 40, "%s: the defect reason is too short to act on" % slug
    assert not NAMES_NOT_FOR_PEOPLE.search(reason)


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_a_recording_is_inside_the_standard(slug):
    entry = MODULE_RECORDINGS[slug]
    video = IMG_MODULES_DIR / f"{slug}.webm"
    poster = IMG_MODULES_DIR / f"{slug}.poster.webp"
    assert video.is_file() and poster.is_file(), "%s: recording or poster file is missing" % slug
    assert video.stat().st_size <= MAX_VIDEO_BYTES, "%s: over 2 MB" % slug
    assert poster.stat().st_size <= MAX_POSTER_BYTES, "%s: poster over 200 KB" % slug

    meta = _manifest()[slug]
    assert 30 <= meta["duration_seconds"] <= 90, "%s: %s seconds" % (slug, meta["duration_seconds"])
    assert (meta["width"], meta["height"]) == (1440, 900)
    assert meta["clip_sha256"] == hashlib.sha256(video.read_bytes()).hexdigest(), \
        "%s: the file is not the recording the manifest measured" % slug
    assert all(row["passed"] for row in meta["checks"]) and meta["checks"], "%s: a declared check failed" % slug

    # the result is stated first, each caption can be read, and the clip ends on the one closing card
    assert meta["captions"][0] == entry["title"]
    assert len(meta["captions"]) >= 3
    assert all(3 <= len(c.split()) <= 20 for c in meta["captions"]), "%s: a caption is too long or too short" % slug
    assert meta["closing"] == [entry["promise"], SIGN_UP]


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_the_words_with_a_recording_describe_it_and_name_no_tool(slug):
    entry = MODULE_RECORDINGS[slug]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", entry["summary"].strip()) if s]
    assert 2 <= len(sentences) <= 4, "%s: the summary is %d sentences" % (slug, len(sentences))
    assert entry["alt"].startswith("Recording of "), slug
    for field in ("title", "promise", "summary", "alt"):
        assert not NAMES_NOT_FOR_PEOPLE.search(entry[field]), "%s %s names a tool" % (slug, field)


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_the_module_page_serves_its_recording_with_its_summary(slug):
    page = load_page("module", slug=slug)
    recording = get_module_recording(page)
    assert recording is not None, "%s: the page does not offer its recording" % slug
    assert recording["video_url"] == "/static/img/modules/%s.webm" % slug
    assert recording["poster_url"] == "/static/img/modules/%s.poster.webp" % slug
    assert recording["caption"] == MODULE_RECORDINGS[slug]["summary"]
    graph = json.loads(build_jsonld(page))
    primary = graph["@graph"][0] if "@graph" in graph else graph
    video = primary["video"]
    for field in ("name", "description", "thumbnailUrl", "uploadDate", "duration", "contentUrl"):
        assert video.get(field), "%s: VideoObject has no %s" % (slug, field)


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDING_DEFECTS))
def test_a_module_without_a_recording_offers_none(slug):
    assert get_module_recording(load_page("module", slug=slug)) is None


def test_a_recorded_module_that_redirects_to_its_parent_is_known():
    """A recording on a page that redirects (MERGED_PAGES) is never seen at its own address: those are named here so
    the lead can decide where it is shown, and the list cannot grow unnoticed."""
    from app.services.public_pages import MERGED_PAGES

    redirected = sorted(slug for slug in MODULE_RECORDINGS if "/modules/%s" % slug in MERGED_PAGES)
    assert redirected == ["duplicate-detection", "gap-analysis", "projects"]
