"""Markup contract for the phone-sized vendor row and the composer template gallery.

The browser journeys in tests/smoke measure the rendered result; these read the two templates so the markup
that result depends on cannot be removed without a failure that names it."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
VENDORS = (ROOT / "app/templates/vendors/list.html").read_text(encoding="utf-8")
COMPOSER = (ROOT / "app/templates/archimate/composer.html").read_text(encoding="utf-8")


def test_the_vendor_evidence_footer_sits_behind_one_control_on_a_phone_only():
    assert "vendor-evidence-toggle" in VENDORS, "no control to show a vendor's evidence on a phone"
    toggle = re.search(r"ui\.button\([^%]*vendor-evidence-toggle[^%]*\)", VENDORS)
    assert toggle and "sm:hidden" in toggle.group(0), "the evidence control must exist on a phone only"
    assert re.search(r'id="vendor-evidence-\{\{ row\.id \}\}" class="hidden sm:block"', VENDORS), (
        "the footer must be folded below 640px and shown from 640px up")
    assert "sm:whitespace-nowrap" in VENDORS, "the vendor cell must keep its single-line evidence from 640px up"


def test_composer_template_chips_name_the_template_and_describe_it_separately():
    chip = re.search(r"call button\(variant='outline', size='xs', class=\"[^\"]*\", attrs_dict=\{\"@click\": \"loadBuiltinTemplate.*?\}\) %\}",
                     COMPOSER, re.S)
    assert chip, "the template chip is not where the gallery expects it"
    text = chip.group(0)
    assert '":aria-label": "tpl.name"' in text, "the chip's accessible name must be the template name"
    assert "aria-describedby" in text, "the description must be the chip's accessible description"
    assert '":title"' not in text, "a title would repeat the name"


def test_composer_gallery_uses_no_viewport_stepped_arbitrary_grid():
    assert "auto-fit,minmax" not in COMPOSER, "the arbitrary auto-fit grid is not emitted by the committed stylesheet"
    assert 'id="composer-template-grid"' in COMPOSER
    assert 'data-testid="composer-gallery-scroll-cue"' in COMPOSER, "the panel must show when there is more to scroll"
