"""Tests for captured screenshots and recordings on public module and
use-case pages.

Covers the "Screenshots on the public module pages" brief, widened
2026-10-06 to include use-case pages and short recordings for four
multi-step use cases:

  - Every live module page (content/pages/modules/*.md, capture_status: live)
    has a captured image file under the 200 KB cap, and the /modules/<slug>
    page renders it with descriptive alt text, explicit width/height and
    lazy loading.
  - get_page_screenshot() never returns an image for a page whose
    capture_status is not "live", and never returns one for a slug with no
    captured file on disk, even if the registry lists it (checked directly
    against the helper function, not only incidentally through content).
  - The one live use-case page gets its screenshot the same way.
  - The four recorded use-case pages render a <video> with controls, muted,
    playsinline, preload="none" and a poster, never an autoplay attribute,
    and their VideoObject JSON-LD carries name/description/thumbnailUrl/
    uploadDate/duration/contentUrl.
  - Every captured recording is under the 3 MB cap, 15-40 seconds (read from
    the sidecar manifest scripts/capture_screenshots.py --modules writes --
    no ffmpeg/ffprobe dependency at test time), and has a poster under the
    200 KB image cap.
"""

from __future__ import annotations

import html
import json
import re

from app.services.public_pages import (
    IMG_MODULES_DIR,
    IMG_USE_CASES_DIR,
    MODULE_CAPTURES,
    USE_CASE_SCREENSHOT_CAPTURES,
    USE_CASE_VIDEO_CAPTURES,
    VIDEO_USE_CASES_DIR,
    PublicPage,
    get_page_recording,
    get_page_screenshot,
    load_all_pages,
    load_page,
)

MAX_IMAGE_BYTES = 200 * 1024
MAX_VIDEO_BYTES = 3 * 1024 * 1024


def _jsonld_from(html: str) -> dict:
    match = re.search(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    assert match, "no JSON-LD script block found on the page"
    return json.loads(match.group(1))


# ── registry stays in sync with content ─────────────────────────────────


def test_module_capture_registry_matches_every_live_module_content_file():
    """Every module page marked capture_status: live has a MODULE_CAPTURES
    entry, and vice versa -- the registry can't silently fall behind
    content/pages/modules/ (or claim a page that isn't actually live) --
    except the one documented case in public_pages.py's MODULE_CAPTURES
    comment: "integrations" stays capture_status: live (it is a real,
    sellable feature) but has no captured image, because the one screen
    its content maps to 500s on any data due to a pre-existing bug in
    app/routes/connector_routes.py, unrelated to this brief."""
    live_slugs = {
        p.slug
        for p in load_all_pages()
        if p.family == "module" and p.front_matter.get("capture_status") == "live"
    }
    registry_slugs = {entry[0] for entry in MODULE_CAPTURES}
    documented_exceptions = {"integrations"}
    assert live_slugs - documented_exceptions == registry_slugs, (
        f"content says live but missing from MODULE_CAPTURES (and not a documented "
        f"exception): {live_slugs - registry_slugs - documented_exceptions}; "
        f"MODULE_CAPTURES has slugs content doesn't mark live: {registry_slugs - live_slugs}"
    )
    assert documented_exceptions <= live_slugs, (
        "the integrations exception assumes content/pages/modules/integrations.md "
        "is still capture_status: live -- update this test if that ever changes"
    )


def test_live_module_with_no_capture_entry_is_the_documented_integrations_exception():
    """No OTHER live module is silently missing from MODULE_CAPTURES --
    "integrations" is the only name this test (and the registry's own
    comment) allows through."""
    live_slugs = {
        p.slug
        for p in load_all_pages()
        if p.family == "module" and p.front_matter.get("capture_status") == "live"
    }
    registry_slugs = {entry[0] for entry in MODULE_CAPTURES}
    assert live_slugs - registry_slugs == {"integrations"}


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


def test_live_module_with_no_captured_file_renders_no_screenshot(app):
    """content/pages/modules/integrations.md is capture_status: live (it is
    a real, sellable feature -- see the MODULE_CAPTURES comment in
    app/services/public_pages.py) but deliberately has no entry in
    MODULE_CAPTURES and so no captured file: its one in-product screen 500s
    on any data, a pre-existing bug unrelated to this brief. The file-exists
    half of get_page_screenshot()'s gate must still hold for a page whose
    capture_status alone would otherwise pass."""
    page = load_page("module", slug="integrations")
    assert page is not None
    assert page.front_matter.get("capture_status") == "live"
    assert get_page_screenshot(page) is None
    with app.test_client() as client:
        rv = client.get("/modules/integrations")
        assert rv.status_code == 200
        assert 'data-testid="page-screenshot"' not in rv.data.decode()


# ── the one live use-case screenshot ────────────────────────────────────


def test_live_use_case_has_a_captured_image_under_the_size_cap():
    assert len(USE_CASE_SCREENSHOT_CAPTURES) > 0
    for slug, _path, _persona, _caption, _alt in USE_CASE_SCREENSHOT_CAPTURES:
        image_path = IMG_USE_CASES_DIR / f"{slug}.webp"
        assert image_path.is_file(), f"missing captured image for use case {slug}"
        assert image_path.stat().st_size <= MAX_IMAGE_BYTES


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
    """A use-case page whose capture_status is awaiting_capture (every
    function-per-segment page except the one live one) must never render a
    screenshot block, even though it has its own body copy and may carry a
    recording instead."""
    live_slugs = {entry[0] for entry in USE_CASE_SCREENSHOT_CAPTURES}
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


# ── the four recorded use cases ─────────────────────────────────────────


def test_every_recorded_use_case_has_video_poster_and_sidecar_under_caps():
    assert len(USE_CASE_VIDEO_CAPTURES) == 4
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
        slug="uc-s3-06-capability-maturity-heatmap",  # live screenshot, but no recording
        url="/use-cases/uc-s3-06-capability-maturity-heatmap",
        title="Capability maturity",
        body_html="",
        front_matter={"capture_status": "live"},
    )
    assert get_page_recording(page) is None
