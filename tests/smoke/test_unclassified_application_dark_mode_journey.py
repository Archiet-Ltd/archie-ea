"""A portfolio manager reads the applications list in dark mode and opens an
unclassified application to classify it.

The applications list is the surface where the dark-mode token work and the
carried main changes meet on one page: the newer list tells a person which
applications are not yet classified and, when they may edit, offers the way
in; the dark-mode tokens must keep that text legible while dark mode is on.
The journey clicks the real theme toggle, reads the classification prompt in
dark mode and follows it to the edit page, and checks the screen stays dark
there.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login, _visit, page  # noqa: F401
from .test_header_search_and_theme import _contrast_ratio, _parse_rgb, _relative_luminance

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _contrast_against_surface(page, locator):
    """WCAG contrast of an element's text colour against the surface behind
    it (the nearest opaque ancestor), exactly as the browser renders it."""
    text = locator.evaluate("el => window.getComputedStyle(el).color")
    background = locator.evaluate(
        """el => {
            let node = el;
            while (node) {
                const c = window.getComputedStyle(node).backgroundColor;
                if (c !== 'transparent' && c !== 'rgba(0, 0, 0, 0)') {
                    return c;
                }
                node = node.parentElement;
            }
            return 'rgb(0, 0, 0)';
        }"""
    )
    return _contrast_ratio(
        _relative_luminance(*_parse_rgb(text)),
        _relative_luminance(*_parse_rgb(background)),
    )


def test_unclassified_application_is_legible_and_reachable_in_dark_mode(page, live_server, seeded):
    """The seeded application has no component type, so the list offers the
    person who may edit a route in: "Not set: open to classify".  With dark
    mode on that prompt must still read at 4.5:1, and clicking it must land
    on the edit page with content, dark mode intact."""
    _login(page, live_server, seeded["emails"]["application_manager"])
    app_id = seeded["ids"]["application"]

    _visit(page, live_server, "/applications/")

    # The merged list renders the new, plain-language column headers and the
    # classification affordance for an unclassified application.
    page.locator("th", has_text="ArchiMate").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)

    classify = page.locator('[data-testid="app-type-%d"]' % app_id)
    classify.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    label = classify.inner_text().strip()
    assert "Not set" in label and "classify" in label, (
        "the unclassified application did not render the classification prompt; got %r" % label
    )

    row = page.locator("tr", has=classify)
    stated = row.locator("span", has_text="Linked").count() + row.locator(
        "span", has_text="Not linked"
    ).count()
    assert stated >= 1, (
        "the row did not state the ArchiMate linkage in plain words (Linked or Not linked)"
    )

    # Dark mode goes on through the theme toggle, the way a person does it.
    page.locator("#user-menu-btn").click()
    page.wait_for_timeout(400)
    dark_item = page.locator('button[role="menuitemcheckbox"]')
    dark_item.click()
    page.wait_for_timeout(400)
    assert page.evaluate("() => document.documentElement.classList.contains('dark')"), (
        "the dark class did not apply after clicking the Dark theme toggle"
    )

    # The classification prompt stays legible on the dark surface.
    ratio = _contrast_against_surface(page, classify)
    assert ratio >= 4.5, (
        "the classification prompt text measured %.2f:1 on the dark page; needs 4.5:1" % ratio
    )

    # Following the prompt lands on the application edit page, still dark.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        classify.click()
    page.wait_for_timeout(800)
    assert page.url.endswith("/applications/%d/edit" % app_id), (
        "the classification prompt went to %r instead of the edit page" % page.url
    )
    assert "Edit Application" in page.inner_text("body"), (
        "the edit page rendered without its heading"
    )
    assert page.locator("#name").count() == 1, "the edit form has no name field"
    assert page.evaluate("() => document.documentElement.classList.contains('dark')"), (
        "dark mode was lost when the person followed the classification prompt"
    )