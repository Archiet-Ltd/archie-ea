"""Tests for captured screenshots and recordings on public module and
use-case pages.

Covers the "Screenshots on the public module pages" brief, widened
2026-10-06 to include use-case pages and short recordings for four
multi-step use cases, then revised 2026-10-06 after lead review of PR #402
rejected 11 of the 26 captured images and all 4 recordings (empty data, the
wrong screen, or a recording that never performed the use case it claimed):

  - Every live module page (content/pages/modules/*.md, capture_status: live)
    either has a captured image file under the 200 KB cap and renders it
    with descriptive alt text, explicit width/height and lazy loading, OR
    is named explicitly in MODULE_CAPTURE_PENDING with a reason -- there is
    no third option. A live module silently missing from both fails this
    suite, which is what keeps the pending list from becoming a place
    things quietly go to be forgotten.
  - get_page_screenshot() never returns an image for a page whose
    capture_status is not "live", and never returns one for a slug with no
    captured file on disk, even if the registry lists it (checked directly
    against the helper function, not only incidentally through content).
  - capture_status on every pending page stays "live" -- capture-pending
    and feature-pending are different things, and flipping the former to
    say the latter is exactly the mistake a prior round of this same brief
    made and had to revert.
  - The one live use-case page follows the identical pending-or-captured
    rule as modules.
  - The four recorded use-case pages, once captured, render a <video> with
    controls, muted, playsinline, preload="none" and a poster, never an
    autoplay attribute, and their VideoObject JSON-LD carries name/
    description/thumbnailUrl/uploadDate/duration/contentUrl. All four are
    capture-pending as of this revision (see USE_CASE_VIDEO_PENDING); the
    tests that assert those properties on a captured file skip cleanly,
    with a reason, rather than pass vacuously on an empty list.
  - Every captured recording, once restored, is under the 3 MB cap, 15-40
    seconds (read from the sidecar manifest scripts/capture_screenshots.py
    --modules writes -- no ffmpeg/ffprobe dependency at test time), and has
    a poster under the 200 KB image cap.
"""

from __future__ import annotations

import html
import json
import re

import pytest

from app.services.public_pages import (
    IMG_MODULES_DIR,
    IMG_USE_CASES_DIR,
    MODULE_CAPTURE_PENDING,
    MODULE_CAPTURES,
    USE_CASE_SCREENSHOT_CAPTURES,
    USE_CASE_SCREENSHOT_PENDING,
    USE_CASE_VIDEO_CAPTURES,
    USE_CASE_VIDEO_PENDING,
    VIDEO_USE_CASES_DIR,
    PublicPage,
    get_page_recording,
    get_page_screenshot,
    load_all_pages,
    load_page,
)

MAX_IMAGE_BYTES = 200 * 1024
MAX_VIDEO_BYTES = 3 * 1024 * 1024

_VIDEO_PENDING_REASON = (
    "all four use-case recordings are capture-pending as of the 2026-10-06 lead "
    "review -- see USE_CASE_VIDEO_PENDING in app/services/public_pages.py; round 2 "
    "moves entries back to USE_CASE_VIDEO_CAPTURES as each is properly recorded"
)
_USE_CASE_SCREENSHOT_PENDING_REASON = (
    "the one live use-case screenshot is capture-pending as of the 2026-10-06 lead "
    "review -- see USE_CASE_SCREENSHOT_PENDING in app/services/public_pages.py"
)


def _jsonld_from(html: str) -> dict:
    match = re.search(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    assert match, "no JSON-LD script block found on the page"
    return json.loads(match.group(1))


# ── registry stays in sync with content, strictly ───────────────────────


def test_module_capture_registry_matches_every_live_module_content_file():
    """Every module page marked capture_status: live is in exactly one of
    MODULE_CAPTURES (captured) or MODULE_CAPTURE_PENDING (named, with a
    reason, as not yet captured) -- never neither, never both. A live
    module quietly missing from both fails here: the pending list is an
    explicit, reviewed roster, not a loophole that silently grows."""
    live_slugs = {
        p.slug
        for p in load_all_pages()
        if p.family == "module" and p.front_matter.get("capture_status") == "live"
    }
    registry_slugs = {entry[0] for entry in MODULE_CAPTURES}
    pending_slugs = set(MODULE_CAPTURE_PENDING)

    overlap = registry_slugs & pending_slugs
    assert not overlap, f"slugs in both MODULE_CAPTURES and MODULE_CAPTURE_PENDING: {overlap}"

    assert live_slugs == registry_slugs | pending_slugs, (
        f"live module(s) in neither MODULE_CAPTURES nor MODULE_CAPTURE_PENDING: "
        f"{live_slugs - registry_slugs - pending_slugs}; "
        f"MODULE_CAPTURES/MODULE_CAPTURE_PENDING name slug(s) content doesn't mark live: "
        f"{(registry_slugs | pending_slugs) - live_slugs}"
    )


def test_module_capture_pending_entries_all_have_a_reason():
    for slug, reason in MODULE_CAPTURE_PENDING.items():
        assert isinstance(reason, str) and len(reason) > 10, (
            f"{slug}: MODULE_CAPTURE_PENDING reason is missing or too short to be useful"
        )


def test_module_capture_pending_pages_stay_capture_status_live():
    """Capture-pending and feature-pending are different things. A page
    named in MODULE_CAPTURE_PENDING describes a real, shipped feature whose
    screenshot capture hasn't landed yet -- its own capture_status must
    stay "live", never flipped to awaiting_capture or anything else, and
    its cta must be untouched by this list's existence."""
    for slug in MODULE_CAPTURE_PENDING:
        page = load_page("module", slug=slug)
        assert page is not None, f"{slug}: no content page found"
        assert page.front_matter.get("capture_status") == "live", (
            f"{slug}: capture_status is {page.front_matter.get('capture_status')!r}, "
            f"not 'live' -- being capture-pending must never change this"
        )


# ── module screenshots: files on disk ───────────────────────────────────


def test_every_live_module_has_a_captured_image_file_under_the_size_cap():
    assert len(MODULE_CAPTURES) > 0
    for slug, _path, _persona, _caption, _alt in MODULE_CAPTURES:
        image_path = IMG_MODULES_DIR / f"{slug}.webp"
        assert image_path.is_file(), f"missing captured image for live module {slug}"
        size = image_path.stat().st_size
        assert size <= MAX_IMAGE_BYTES, (
            f"{slug}.webp is {size} bytes, over the {MAX_IMAGE_BYTES}-byte cap"
        )
        assert size > 0, f"{slug}.webp is a zero-byte file"


def test_no_stray_image_file_for_a_pending_module():
    """A rejected capture's file must actually be gone, not just dropped
    from the registry -- a stray file on disk would still fail the "never
    ship an empty/wrong screen" rule even though nothing links to it."""
    for slug in MODULE_CAPTURE_PENDING:
        image_path = IMG_MODULES_DIR / f"{slug}.webp"
        assert not image_path.is_file(), (
            f"{slug}.webp still exists on disk despite being capture-pending"
        )


# ── module pages render the image ───────────────────────────────────────


def test_every_live_module_page_renders_its_screenshot_with_alt_text(app):
    with app.test_client() as client:
        for slug, _path, _persona, caption, alt in MODULE_CAPTURES:
            rv = client.get(f"/modules/{slug}")
            assert rv.status_code == 200, f"/modules/{slug} returned {rv.status_code}"
            # Jinja autoescapes attribute values (an apostrophe becomes &#39;),
            # so compare against the unescaped text rather than raw HTML.
            page_html = html.unescape(rv.data.decode())
            assert 'data-testid="page-screenshot"' in page_html, f"{slug}: no screenshot block rendered"
            assert f'alt="{alt}"' in page_html, f"{slug}: alt text missing or does not match the registry"
            assert caption in page_html, f"{slug}: caption text missing"
            assert 'loading="lazy"' in page_html, f"{slug}: image is not lazy-loaded"
            # width/height both present as explicit attributes somewhere on the img tag
            assert re.search(r'width="\d+"', page_html)
            assert re.search(r'height="\d+"', page_html)


def test_capture_pending_module_pages_render_no_screenshot(app):
    """Every MODULE_CAPTURE_PENDING page renders with no media at all --
    capture_status stays live (previous test), but get_page_screenshot()'s
    file-exists gate means the removed file renders nothing rather than a
    broken <img> or a stale picture."""
    with app.test_client() as client:
        for slug in MODULE_CAPTURE_PENDING:
            page = load_page("module", slug=slug)
            assert page is not None
            assert get_page_screenshot(page) is None, f"{slug}: expected no screenshot"
            rv = client.get(f"/modules/{slug}")
            assert rv.status_code == 200
            assert 'data-testid="page-screenshot"' not in rv.data.decode()


# ── the screenshot gate itself, independent of real content files ──────


def test_get_page_screenshot_returns_none_for_a_non_live_capture_status():
    page = PublicPage(
        family="module",
        slug="applications",  # a slug that DOES have a captured file
        url="/modules/applications",
        title="Applications",
        body_html="",
        front_matter={"capture_status": "awaiting_capture"},
    )
    assert get_page_screenshot(page) is None


def test_get_page_screenshot_returns_none_when_no_file_exists_even_if_live():
    page = PublicPage(
        family="module",
        slug="this-module-does-not-exist",
        url="/modules/this-module-does-not-exist",
        title="Not Real",
        body_html="",
        front_matter={"capture_status": "live"},
    )
    assert get_page_screenshot(page) is None


def test_get_page_screenshot_returns_none_for_other_page_families():
    page = PublicPage(
        family="vision",
        slug="home",
        url="/vision",
        title="Vision",
        body_html="",
        front_matter={"capture_status": "live"},
    )
    assert get_page_screenshot(page) is None


# ── the one live use-case screenshot (capture-pending, see module above) ─


def test_use_case_screenshot_registry_matches_pending_list():
    """Same strict either/or rule as modules, applied to the one live
    use-case page."""
    live_slugs = {
        p.slug
        for p in load_all_pages()
        if p.family == "function-per-segment"
        and p.front_matter.get("capture_status") == "live"
    }
    registry_slugs = {entry[0] for entry in USE_CASE_SCREENSHOT_CAPTURES}
    pending_slugs = set(USE_CASE_SCREENSHOT_PENDING)
    assert not (registry_slugs & pending_slugs)
    assert live_slugs == registry_slugs | pending_slugs, (
        f"live use-case page(s) in neither list: {live_slugs - registry_slugs - pending_slugs}; "
        f"named but not actually live: {(registry_slugs | pending_slugs) - live_slugs}"
    )


def test_use_case_screenshot_pending_page_stays_capture_status_live_and_renders_nothing(app):
    with app.test_client() as client:
        for slug in USE_CASE_SCREENSHOT_PENDING:
            page = load_page("function-per-segment", slug=slug)
            assert page is not None
            assert page.front_matter.get("capture_status") == "live"
            assert get_page_screenshot(page) is None
            rv = client.get(f"/use-cases/{slug}")
            assert rv.status_code == 200
            assert 'data-testid="page-screenshot"' not in rv.data.decode()


@pytest.mark.skipif(not USE_CASE_SCREENSHOT_CAPTURES, reason=_USE_CASE_SCREENSHOT_PENDING_REASON)
def test_live_use_case_has_a_captured_image_under_the_size_cap():
    for slug, _path, _persona, _caption, _alt in USE_CASE_SCREENSHOT_CAPTURES:
        image_path = IMG_USE_CASES_DIR / f"{slug}.webp"
        assert image_path.is_file(), f"missing captured image for use case {slug}"
        assert image_path.stat().st_size <= MAX_IMAGE_BYTES


@pytest.mark.skipif(not USE_CASE_SCREENSHOT_CAPTURES, reason=_USE_CASE_SCREENSHOT_PENDING_REASON)
def test_live_use_case_page_renders_its_screenshot(app):
    with app.test_client() as client:
        for slug, _path, _persona, caption, alt in USE_CASE_SCREENSHOT_CAPTURES:
            rv = client.get(f"/use-cases/{slug}")
            assert rv.status_code == 200
            page_html = html.unescape(rv.data.decode())
            assert 'data-testid="page-screenshot"' in page_html
            assert f'alt="{alt}"' in page_html
            assert caption in page_html


def test_awaiting_capture_use_case_page_renders_no_screenshot(app):
    """A use-case page whose capture_status is awaiting_capture must never
    render a screenshot block, even though it has its own body copy and
    may (once round 2 lands) carry a recording instead."""
    live_slugs = {entry[0] for entry in USE_CASE_SCREENSHOT_CAPTURES} | set(
        USE_CASE_SCREENSHOT_PENDING
    )
    recorded_slugs = {entry[0] for entry in USE_CASE_VIDEO_CAPTURES}
    pages = [
        p
        for p in load_all_pages()
        if p.family == "function-per-segment"
        and p.front_matter.get("capture_status") == "awaiting_capture"
        and p.slug not in live_slugs
    ]
    assert len(pages) > 0, "expected at least one awaiting_capture use-case page to check"
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            assert rv.status_code == 200
            page_html = rv.data.decode()
            assert 'data-testid="page-screenshot"' not in page_html, (
                f"{page.url}: rendered a screenshot despite capture_status: awaiting_capture"
            )
            if page.slug not in recorded_slugs:
                assert 'data-testid="page-recording"' not in page_html


# ── the four recorded use cases (all capture-pending, see module docstring) ─


def test_use_case_video_pending_entries_all_have_a_reason():
    for slug, reason in USE_CASE_VIDEO_PENDING.items():
        assert isinstance(reason, str) and len(reason) > 10, (
            f"{slug}: USE_CASE_VIDEO_PENDING reason is missing or too short to be useful"
        )


def test_no_stray_recording_files_for_a_pending_use_case():
    for slug in USE_CASE_VIDEO_PENDING:
        for suffix, path in (
            (".webm", VIDEO_USE_CASES_DIR / f"{slug}.webm"),
            ("-poster.webp", VIDEO_USE_CASES_DIR / f"{slug}-poster.webp"),
            (".json", VIDEO_USE_CASES_DIR / f"{slug}.json"),
        ):
            assert not path.is_file(), f"{slug}{suffix} still exists on disk despite being capture-pending"


def test_use_case_video_pending_pages_render_no_recording(app):
    with app.test_client() as client:
        for slug in USE_CASE_VIDEO_PENDING:
            page = load_page("function-per-segment", slug=slug)
            assert page is not None, f"{slug}: no content page found"
            assert get_page_recording(page) is None
            rv = client.get(f"/use-cases/{slug}")
            assert rv.status_code == 200
            assert 'data-testid="page-recording"' not in rv.data.decode()


@pytest.mark.skipif(not USE_CASE_VIDEO_CAPTURES, reason=_VIDEO_PENDING_REASON)
def test_every_recorded_use_case_has_video_poster_and_sidecar_under_caps():
    for slug, _steps, _persona, _caption, _alt in USE_CASE_VIDEO_CAPTURES:
        video_path = VIDEO_USE_CASES_DIR / f"{slug}.webm"
        poster_path = VIDEO_USE_CASES_DIR / f"{slug}-poster.webp"
        meta_path = VIDEO_USE_CASES_DIR / f"{slug}.json"
        assert video_path.is_file(), f"missing recording for {slug}"
        assert poster_path.is_file(), f"missing poster for {slug}"
        assert meta_path.is_file(), f"missing metadata sidecar for {slug}"

        video_size = video_path.stat().st_size
        assert 0 < video_size <= MAX_VIDEO_BYTES, (
            f"{slug}.webm is {video_size} bytes, over the {MAX_VIDEO_BYTES}-byte cap"
        )
        poster_size = poster_path.stat().st_size
        assert 0 < poster_size <= MAX_IMAGE_BYTES, (
            f"{slug}-poster.webp is {poster_size} bytes, over the {MAX_IMAGE_BYTES}-byte cap"
        )

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        duration = meta["duration_seconds"]
        assert 15.0 <= duration <= 40.0, f"{slug}: recording is {duration}s, outside 15-40s"
        assert meta["width"] > 0 and meta["height"] > 0
        assert meta["captured_date"]


@pytest.mark.skipif(not USE_CASE_VIDEO_CAPTURES, reason=_VIDEO_PENDING_REASON)
def test_recorded_use_case_page_renders_video_with_required_attributes(app):
    with app.test_client() as client:
        for slug, _steps, _persona, caption, alt in USE_CASE_VIDEO_CAPTURES:
            rv = client.get(f"/use-cases/{slug}")
            assert rv.status_code == 200
            page_html = html.unescape(rv.data.decode())
            assert 'data-testid="page-recording"' in page_html, f"{slug}: no recording block rendered"

            video_tag_match = re.search(r"<video\b[^>]*>", page_html)
            assert video_tag_match, f"{slug}: no <video> element found"
            video_tag = video_tag_match.group(0)
            assert "controls" in video_tag
            assert "muted" in video_tag
            assert "playsinline" in video_tag
            assert 'preload="none"' in video_tag
            assert "autoplay" not in video_tag
            assert f'aria-label="{alt}"' in video_tag
            assert caption in page_html


@pytest.mark.skipif(not USE_CASE_VIDEO_CAPTURES, reason=_VIDEO_PENDING_REASON)
def test_recorded_use_case_video_object_jsonld_has_required_fields(app):
    with app.test_client() as client:
        for slug, _steps, _persona, caption, _alt in USE_CASE_VIDEO_CAPTURES:
            rv = client.get(f"/use-cases/{slug}")
            page_html = rv.data.decode()
            ld = _jsonld_from(page_html)
            video = ld.get("video")
            assert video is not None, f"{slug}: WebPage JSON-LD has no video property"
            assert video["@type"] == "VideoObject"
            for field_name in (
                "name", "description", "thumbnailUrl", "uploadDate", "duration", "contentUrl",
            ):
                assert video.get(field_name), f"{slug}: VideoObject missing {field_name}"
            assert video["description"] == caption
            assert re.fullmatch(r"PT\d+S", video["duration"]), (
                f"{slug}: duration {video['duration']!r} is not ISO 8601 (PTnS)"
            )
            assert video["contentUrl"].endswith(f"/static/video/use-cases/{slug}.webm")
            assert video["thumbnailUrl"].endswith(f"/static/video/use-cases/{slug}-poster.webp")
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", video["uploadDate"])


@pytest.mark.skipif(not USE_CASE_VIDEO_CAPTURES, reason=_VIDEO_PENDING_REASON)
def test_get_page_recording_ignores_capture_status():
    """A recorded use case is independent of capture_status -- three of the
    four recorded pages are still (rightly) marked awaiting_capture for the
    screenshot that field gates; get_page_recording() must not read it."""
    for slug, _steps, _persona, _caption, _alt in USE_CASE_VIDEO_CAPTURES:
        page = load_page("function-per-segment", slug=slug)
        assert page is not None, f"{slug}: no content page found"
        assert get_page_recording(page) is not None


def test_get_page_recording_returns_none_for_module_pages():
    page = PublicPage(
        family="module",
        slug="applications",
        url="/modules/applications",
        title="Applications",
        body_html="",
        front_matter={},
    )
    assert get_page_recording(page) is None


def test_get_page_recording_returns_none_for_an_unrecorded_use_case():
    page = PublicPage(
        family="function-per-segment",
        slug="capability-maturity-heatmap",  # not in USE_CASE_VIDEO_CAPTURES,
        # regardless of its own screenshot's pending status
        url="/use-cases/capability-maturity-heatmap",
        title="Capability maturity",
        body_html="",
        front_matter={"capture_status": "live"},
    )
    assert get_page_recording(page) is None
