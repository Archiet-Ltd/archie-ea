"""Static-asset regression test for the landing-band alignment fast-follow.

Covers two layout fixes to the public landing-band sections (the full-width
bands used for module/use-case/comparison "landing pages"):

* Prose and card grids inside a ``.landing-band`` section are left-aligned
  instead of centered — ``public_site.css`` overrides the page-wide centered
  ``.public-page-content`` rule for elements inside ``.landing-band``, and the
  template drops the centering ``mx-auto`` utility from the card grid.
* The gap between a landing band's heading and its screenshot/recording media
  is tightened — the template wraps the media partial so the first ``<figure>``
  loses its default top margin, which Tailwind compiles to a dedicated rule in
  the hashed stylesheet the manifest points at.

This reads the compiled assets straight off disk. It needs no application
context, no database and no running server, so it can and does run as a
plain, fast ``pytest`` invocation.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SITE_CSS = REPO_ROOT / "app" / "static" / "css" / "public_site.css"
MANIFEST_PATH = REPO_ROOT / "app" / "static" / "manifest.json"
LANDING_TEMPLATE = REPO_ROOT / "app" / "templates" / "public" / "_partials" / "_landing.html"

# The rule that overrides the page-wide centered prose column for anything
# inside a landing band, so its text and card grid sit flush left instead of
# centered in the viewport.
LANDING_BAND_LEFT_ALIGN_RULE = (
    ".public-site .landing-band .public-page-content > * { margin-inline: 0; }"
)

# Tailwind's compiled form of the `[&>figure:first-child]:mt-0` wrapper class
# added around the media partial, which zeroes the first figure's top margin
# and tightens the gap between the heading above it and the screenshot/
# recording below it.
MEDIA_BAND_GAP_RULE = ".\\[\\&\\>figure\\:first-child\\]\\:mt-0>figure:first-child{margin-top:0}"


def _read(path: Path) -> str:
    assert path.is_file(), f"expected {path} to exist"
    return path.read_text(encoding="utf-8")


def test_public_site_css_left_aligns_landing_band_prose():
    css = _read(PUBLIC_SITE_CSS)
    assert LANDING_BAND_LEFT_ALIGN_RULE in css, (
        "expected public_site.css to override the centered .public-page-content "
        "margin for elements inside .landing-band, so landing-band prose is "
        "left-aligned rather than centered"
    )


def test_landing_band_card_grid_drops_centering_utility():
    html = _read(LANDING_TEMPLATE)
    assert 'class="mx-auto mt-8 grid max-w-5xl gap-6' not in html, (
        "the landing-band card grid should no longer carry mx-auto, which "
        "centered it instead of leaving it left-aligned with the band's prose"
    )
    assert 'class="mt-8 grid max-w-5xl gap-6 sm:grid-cols-2 lg:grid-cols-3"' in html, (
        "expected the card grid to keep its other layout utilities with "
        "mx-auto removed, not a rewritten or missing class list"
    )


def test_landing_band_media_wrapper_requests_the_tightened_gap():
    html = _read(LANDING_TEMPLATE)
    assert "[&>figure:first-child]:mt-0" in html, (
        "expected the landing-band media partial to be wrapped in a container "
        "that zeroes the first figure's top margin, tightening the gap below "
        "the heading"
    )


def test_manifest_points_at_a_compiled_stylesheet_containing_the_gap_rule():
    manifest = json.loads(_read(MANIFEST_PATH))
    hashed_name = manifest["css/tailwind-output.css"]
    hashed_path = REPO_ROOT / "app" / "static" / "css" / Path(hashed_name).name
    compiled_css = _read(hashed_path)
    assert MEDIA_BAND_GAP_RULE in compiled_css, (
        f"manifest resolves css/tailwind-output.css to {hashed_path.name}, but "
        "that file does not contain the compiled rule tightening the media-band "
        "gap — the manifest and the compiled asset have drifted apart"
    )
