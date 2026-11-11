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

from tests._published_text_rules import BUILD_PROCESS_WORDS, PLACEHOLDER_PERSON_NAME
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
    assert not BUILD_PROCESS_WORDS.search(reason)


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
    assert 2 <= len(meta["captions"]) <= 4, "%s: %d captions" % (slug, len(meta["captions"]))
    assert all(3 <= len(c.split()) <= 20 for c in meta["captions"]), "%s: a caption is too long or too short" % slug
    assert meta["closing"] == [entry["promise"], SIGN_UP]


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_the_words_with_a_recording_describe_it_and_say_nothing_of_how_the_product_was_built(slug):
    entry = MODULE_RECORDINGS[slug]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", entry["summary"].strip()) if s]
    assert 2 <= len(sentences) <= 4, "%s: the summary is %d sentences" % (slug, len(sentences))
    assert entry["alt"].startswith("Recording of "), slug
    for field in ("title", "promise", "summary", "alt"):
        assert not BUILD_PROCESS_WORDS.search(entry[field]), "%s %s describes how the product was built" % (slug, field)
        assert not PLACEHOLDER_PERSON_NAME.search(entry[field]), "%s %s names a person like a placeholder" % (slug, field)


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


def test_no_recording_is_registered_for_a_page_that_redirects():
    """A recording on a page that redirects (MERGED_PAGES) is never seen at its own address, so none is registered:
    a module whose page redirects is named as a defect with the address it redirects to."""
    from app.services.public_pages import MERGED_PAGES

    redirected = sorted(slug for slug in MODULE_RECORDINGS if "/modules/%s" % slug in MERGED_PAGES)
    assert redirected == []
    for slug in sorted(MODULE_RECORDING_DEFECTS):
        target = MERGED_PAGES.get("/modules/%s" % slug)
        if target:
            assert "redirects to %s" % target in MODULE_RECORDING_DEFECTS[slug], \
                "%s redirects to %s and its defect line does not say so" % (slug, target)


def test_every_recording_file_has_the_size_its_manifest_states():
    """The size a commit message or report quotes is the manifest's, so the manifest must be the file's."""
    for slug in MODULE_RECORDINGS:
        assert _manifest()[slug]["bytes"] == (IMG_MODULES_DIR / f"{slug}.webm").stat().st_size, slug


def test_the_manifest_names_only_registered_recordings():
    assert set(_manifest()) == set(MODULE_RECORDINGS)
    on_disk = {p.name[: -len(".webm")] for p in IMG_MODULES_DIR.glob("*.webm")}
    assert on_disk == set(MODULE_RECORDINGS), "a recording file with no registry entry: %s" % sorted(on_disk - set(MODULE_RECORDINGS))


# ── the scan that keeps a bad clip out ──────────────────────────────────────────────────────────────

SCREENS_THAT_BREAK_THE_STANDARD = [
    "Overview\nAnnual run cost\nAppears here once a contract states its annual cost",
    "Business purpose\nNot set",
    "Not reviewed yet",
    "No licence cost recorded",
    "No DR recorded",
    "No owner recorded",
    "1 of 20 have no owner \u00b7 1 of 20 have no capability \u00b7 1 of 20 have no vendor",
    "Missing to reach a complete record",
    "Not assessed\n0\nshown as",
    "0\nResolved\nNo gap has been closed yet",
    "Plateaus\n5\nWork Packages\n0\nAI Generated\n0",
    "Baseline\n0\ngaps\n\u00b7\n0\nwork packages",
    "No decision is recorded against this element yet.",
    "Count\n\u2014",
]

SCREENS_THAT_ARE_FINE = [
    "20 shown of 21\nCalibration Ledger\nMorgan Hale",
    "Evidence worked out 07 Nov 2026\n10 depend on it \u00b7 it depends on 8",
    "Total cost of ownership\n\u00a30.00 saved\n100%\n2.6 against 3.8",
    "Control A.5.11\nEvent Relay\tverified",
]


@pytest.mark.parametrize("text", SCREENS_THAT_BREAK_THE_STANDARD)
def test_the_scan_refuses_a_screen_with_an_empty_state_a_dash_or_a_zero(text):
    from scripts.capture.module_clips import frame_problems

    assert frame_problems([text]), "the scan let this screen through: %r" % text


@pytest.mark.parametrize("text", SCREENS_THAT_ARE_FINE)
def test_the_scan_lets_a_full_screen_through(text):
    from scripts.capture.module_clips import frame_problems

    assert frame_problems([text]) == []


def test_the_scan_has_no_per_clip_exception():
    import scripts.capture.module_clips as module_clips

    assert not hasattr(module_clips, "ALLOWED_IN_FRAME")


def test_a_caption_said_over_a_screen_that_does_not_show_its_claim_is_refused():
    from scripts.capture.clip_kit import ClipRefused
    from scripts.capture.module_clips import said

    class Page:
        def evaluate(self, *_):
            return None

        def wait_for_timeout(self, _):
            return None

    class See:
        frames = []

        def __call__(self, page):
            self.frames.append("Applications\nOwner\nLifecycle")

    said(See(), Page(), "Each row has an owner", hold=0, needs=("Owner",))
    with pytest.raises(ClipRefused):
        said(See(), Page(), "Nothing is overdue", hold=0, needs=("Overdue",))


def test_the_clip_starts_on_the_first_caption_not_on_the_page_it_signed_in_on():
    from scripts.capture.clip_kit import start_trim

    assert start_trim({"begin": 6.4}, steps_started=2.0) == pytest.approx(6.25)
    assert start_trim({}, steps_started=2.0) == pytest.approx(1.85)
    assert start_trim({"begin": 0.1}, steps_started=0.0) == 0.0


def test_no_story_opens_a_record_by_a_fixed_row_number_and_none_states_a_literal_it_did_not_read():
    import pathlib
    import scripts.capture.module_clips as module_clips

    source = pathlib.Path(module_clips.__file__).read_text(encoding="utf-8")
    assert not re.search(r"/[a-z-]+/\d+['\"]", source), "a story opens a record by its database row number"
    for stale in ("Nothing is overdue", "Six industry frameworks", "Select any entry", "Widen the view",
                  "Run Detection"):
        assert stale not in source, stale
    assert "Calibration Ledger" not in "".join(
        line for line in source.splitlines() if "say(" in line and "%s" not in line and "needs" not in line)


def test_every_story_in_the_capture_is_a_registered_recording():
    from scripts.capture.module_clips import STORIES

    assert set(STORIES) == set(MODULE_RECORDINGS)
