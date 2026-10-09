"""Tests for "Start free, not a waiting list, on every live page".

Community sign-up is open to the public, so every page whose feature is
live must offer "Start free" (to registration) as its primary action, not
a waiting list, and the home page must not promise a feature ("enter your
website address") that does not exist.

Covers:
  1. The home page offers "Start free" to the registration route, and has
     no waiting-list form or copy.
  2. No live page in the content directory renders waiting-list copy or a
     waiting-list form -- checked across every page, not a hardcoded
     handful.
  3. No live surface (home page, live content files, the llms.txt /
     llms-full.txt product summary line) claims the website-address intake
     that does not exist.
  4. Pages the audit holds because the feature genuinely is not built yet
     (``capture_status: not_applicable_not_yet_built``) keep their original
     waiting-list framing untouched -- proving the removal above was not
     over-applied.
"""

from __future__ import annotations

import pytest

from app.services.public_pages import load_all_pages

# The one content-level signal shared by every function-per-segment page
# the SEO/GEO audit marks HOLD because the feature is not built yet. Pages
# carrying it correctly keep a waiting-list call to action; every other
# page must not have one.
NOT_YET_BUILT = "not_applicable_not_yet_built"

# The audit holds this one page for a different reason than the pattern
# above: it is a "site" page, not a function-per-segment use case, held
# because the documentation it describes is not published yet, not
# because a product feature is unbuilt -- so it carries no capture_status
# field at all, only its own inline waiting-list link. Held, not live.
SITE_PAGES_ALSO_HELD = {"docs"}


def _is_held(page) -> bool:
    if page.front_matter.get("capture_status") == NOT_YET_BUILT:
        return True
    return page.family == "site" and page.slug in SITE_PAGES_ALSO_HELD


# ---------------------------------------------------------------------------
# 1. Home page: Start free to registration, no waiting list
# ---------------------------------------------------------------------------


def test_home_page_offers_start_free_to_registration(app):
    with app.test_request_context():
        from flask import url_for

        register_url = url_for("account.register")

    with app.test_client() as client:
        rv = client.get("/")
        assert rv.status_code == 200
        html = rv.data.decode()

    assert "Start free" in html
    assert f'href="{register_url}"' in html


def test_home_page_has_no_waiting_list_form_or_copy(app):
    with app.test_client() as client:
        rv = client.get("/")
        html = rv.data.decode()

    assert "waiting list" not in html.lower()
    assert 'id="waitlist"' not in html
    # The removed form's own fields -- a stronger signal than the heading
    # text alone that the form itself is gone, not just relabelled.
    assert 'name="consent"' not in html
    assert 'name="email"' not in html


def test_home_page_does_not_claim_website_address_intake(app):
    with app.test_client() as client:
        rv = client.get("/")
        html = rv.data.decode()

    assert "website address" not in html.lower()


# ---------------------------------------------------------------------------
# 2. No live page renders waiting-list copy or a form
# ---------------------------------------------------------------------------


def test_no_live_page_renders_waiting_list_copy(app):
    """Iterates every page under content/pages/ -- not a hardcoded handful.

    A page is "live" here if its own front matter does not say the feature
    is not yet built, and it is not the one page held for a different
    reason (see ``_is_held``). Held pages are exercised separately in
    ``test_hold_pages_keep_their_waiting_list_framing``.

    Checks the structural signal (``cta: waiting_list``, which is what
    actually makes public/page.html render the waiting-list block) and the
    exact, case-sensitive call-to-action text it renders. A case-insensitive
    substring match on "waiting list" would also flag pages like /privacy,
    which factually describes what happens to data submitted through a
    waiting-list form elsewhere -- that is a disclosure, not a call to
    action, and is correctly left alone.
    """
    pages = load_all_pages()
    live_pages = [p for p in pages if not _is_held(p)]
    # Sanity check that this iterates the real content set, not an empty one.
    assert len(live_pages) >= 60

    with app.test_client() as client:
        for page in live_pages:
            assert page.cta != "waiting_list", (
                f"{page.url}: live page still carries cta: waiting_list"
            )
            rv = client.get(page.url)
            html = rv.data.decode()
            assert "Join the waiting list" not in html, (
                f"{page.url}: live page still renders the waiting-list call to action"
            )


# ---------------------------------------------------------------------------
# 3. No live page claims the website-address intake
# ---------------------------------------------------------------------------


def test_no_live_content_file_claims_website_address_intake():
    """Reads every content file directly, not just rendered HTML, so the
    check also catches a claim anywhere in the source Markdown."""
    pages = load_all_pages()
    checked = 0
    for page in pages:
        if _is_held(page) or page.source_path is None:
            continue
        text = page.source_path.read_text(encoding="utf-8")
        assert "website address" not in text.lower(), (
            f"{page.source_path}: still claims a website-address intake"
        )
        checked += 1
    assert checked >= 60


def test_llms_summary_line_does_not_claim_website_address_intake(app):
    """The one-line product summary at the top of llms.txt and
    llms-full.txt -- the literal claim rewritten here -- must not mention
    a website-address intake.

    Body text further down either file, drawn from held use cases whose
    feature genuinely is not built yet, is untouched by design; see
    ``test_hold_pages_keep_their_waiting_list_framing``.
    """
    with app.test_client() as client:
        for url in ("/llms.txt", "/llms-full.txt"):
            rv = client.get(url)
            assert rv.status_code == 200
            lines = rv.data.decode().splitlines()
            summary_line = lines[2]
            assert summary_line.startswith(">"), (
                f"{url}: expected the product summary on line 3, got {summary_line!r}"
            )
            assert "website address" not in summary_line.lower(), (
                f"{url}: summary line still claims a website-address intake"
            )


# ---------------------------------------------------------------------------
# 4. Held, not-yet-built pages keep their original framing untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "website-first-look",
        "website-full-profile",
        "see-your-own-twin",
        "set-up-in-an-afternoon",
    ],
)
def test_hold_pages_keep_their_waiting_list_framing(app, slug):
    """Spot check: pages held because the feature is not built yet still
    carry their original waiting-list call to action, proving the removal
    in tests 1-3 was not over-applied to them."""
    pages_by_slug = {p.slug: p for p in load_all_pages()}
    assert slug in pages_by_slug, f"{slug}: expected content page not found"
    page = pages_by_slug[slug]
    assert _is_held(page), f"{slug}: expected a held page"
    assert page.cta == "waiting_list"

    with app.test_client() as client:
        rv = client.get(page.url)
        assert rv.status_code == 200
        html = rv.data.decode()
    assert "join the waiting list" in html.lower()


def test_docs_page_held_for_unpublished_documentation_has_no_dead_waitlist_link(app):
    """Separate spot check: /docs is held for a different reason (the
    documentation it describes is not published yet) and so has no
    ``cta: waiting_list`` front matter.

    This page's own inline waiting-list link was dropped in a later,
    independent round of SEO/GEO truth fixes (the link pointed at the
    home page's own waiting-list section, already removed by "Start free,
    not a waiting list" before that round even started, so the link was
    dead regardless of how it got there) -- confirm the dead link stays
    gone rather than asserting it must remain, which an earlier version
    of this test did."""
    pages_by_slug = {p.slug: p for p in load_all_pages()}
    page = pages_by_slug["docs"]
    assert _is_held(page)

    with app.test_client() as client:
        rv = client.get(page.url)
        assert rv.status_code == 200
        html = rv.data.decode()
    assert "/#waitlist" not in html, "/docs still links to the removed home-page waitlist anchor"
    assert "join the waiting list" not in html.lower()
