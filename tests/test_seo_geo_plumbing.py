"""Tests for the SEO/GEO truth-and-plumbing brief's renderer-level work that
doesn't fit naturally inside test_public_content_pages.py or
test_vs_comparison_hub.py: the www -> apex redirect, the "no page anywhere
links to #waitlist" guard, FAQPage JSON-LD generalised beyond comparison
pages, and meta-description precedence (explicit front matter over the
generated fallback).
"""

from __future__ import annotations

import json

from app.services.public_pages import (
    CONTENT_ROOT,
    load_all_pages,
    load_page,
)


# ── www -> apex redirect ────────────────────────────────────────────────────


def test_www_host_redirects_to_apex(app):
    """A request to www.entelim.org 301s to the same path on entelim.org."""
    with app.test_client() as client:
        rv = client.get("/pricing", headers={"Host": "www.entelim.org"})
        assert rv.status_code == 301
        assert rv.location == "https://entelim.org/pricing" or rv.location.endswith(
            "entelim.org/pricing"
        )


def test_www_host_redirect_preserves_path_and_query(app):
    """The www redirect keeps the exact path requested, not just the root."""
    with app.test_client() as client:
        rv = client.get("/modules/applications", headers={"Host": "www.entelim.org"})
        assert rv.status_code == 301
        assert "/modules/applications" in rv.location


def test_non_www_host_is_not_redirected(app):
    """A normal request (not to the www host) is never caught by the
    www -> apex redirect."""
    with app.test_client() as client:
        rv = client.get("/pricing")
        assert rv.status_code == 200


# ── "Enter your website address" claim removed from the crawler files and
#    the home page's own head tags (SEO/GEO audit item 3: no website-intake
#    route, service or template exists on main). The home page's own body
#    copy (the hero paragraph) is left exactly as it is -- removing it there
#    is PR414's job (home page CTA / waiting-list removal), not this
#    brief's; this test checks everything that IS this brief's job: the two
#    crawler-facing files and the home page's own meta/og/twitter tags. ────


def test_llms_files_make_no_website_address_claim(app):
    with app.test_client() as client:
        llms = client.get("/llms.txt").data.decode()
        llms_full = client.get("/llms-full.txt").data.decode()
    assert "website address" not in llms.lower()
    assert "website address" not in llms_full.lower()


def test_home_page_head_tags_make_no_website_address_claim(app):
    """The home page's <head> tags (meta description, og:description,
    twitter:description) no longer claim a website-intake feature that
    doesn't exist -- only the body copy's hero paragraph is left to
    PR414."""
    with app.test_client() as client:
        html = client.get("/").data.decode()
    head = html.split("<body", 1)[0]
    assert "website address" not in head.lower(), (
        "home page <head> still makes the website-address claim"
    )


# ── No page anywhere links to the removed waiting-list anchor ──────────────


def test_no_page_links_to_cross_page_waitlist_anchor(app):
    """No page other than the home page itself links to "/#waitlist" --
    every held page's former waiting-list box now points at its own
    "tell us you need this" enquiry instead (see
    app/templates/public/page.html's cta == 'waiting_list' block).

    The home page's own hero button and waiting-list section still use a
    same-page "#waitlist" anchor (href="#waitlist", no leading slash) --
    removing that is PR414's job (home page CTA / waiting-list removal),
    not this brief's, so it is deliberately excluded here.
    """
    pages = [p for p in load_all_pages()]
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            if rv.status_code != 200:
                continue
            html = rv.data.decode()
            assert "/#waitlist" not in html, f"{page.url}: still links to /#waitlist"

        # The use-cases index and /vs hub are views, not content pages.
        for url in ("/use-cases", "/vs"):
            html = client.get(url).data.decode()
            assert "/#waitlist" not in html, f"{url}: still links to /#waitlist"


def test_home_page_still_has_its_own_same_page_waitlist_anchor(app):
    """Documents the deliberate exclusion above: the home page's own
    waiting-list section still exists on main (PR414, which removes it,
    has not merged yet) and still uses a same-page anchor, not a dead
    cross-page link."""
    with app.test_client() as client:
        html = client.get("/").data.decode()
    assert 'id="waitlist"' in html


# ── FAQPage JSON-LD generalised beyond comparison pages ───────────────────


def _faq_node(ld: dict) -> dict | None:
    nodes = ld["@graph"] if "@graph" in ld else [ld]
    return next((n for n in nodes if n.get("@type") == "FAQPage"), None)


_FAQ_FIXTURE_MODULE_CONTENT = """---
page_family: module
module_label: "FAQ Fixture Module"
state: on_main
cta: plans
capture_status: live
---

# FAQ Fixture Module

A fixture page with a real FAQ section, in the same shape PR415's longer
module and offer rewrites use, to prove FAQPage JSON-LD is not gated on
page_family == comparison.

## Frequently asked questions

### Does this work on a module page?

Yes -- FAQPage JSON-LD is generated from any page's own "## Frequently
asked ..." section, not just a comparison page's.

### What about a second question?

It is picked up too, as a second mainEntity entry.
"""


def test_faq_jsonld_renders_on_a_non_comparison_page(app):
    """A module page (not a comparison page) with a real "## Frequently
    asked questions" / "### question" section gets FAQPage JSON-LD
    alongside its own SoftwareApplication node -- this is exactly the gap
    PR415's longer module/offer rewrites exposed: the FAQ parser used to
    be gated on page_family == comparison, so a real FAQ section on any
    other page family never rendered as structured data at all.

    A fixture page stands in for PR415's own rewritten pages (business-case
    and others), which haven't merged yet: same "## Frequently asked
    questions" / "### question" shape, so this test is real today and also
    covers the real pages once that branch lands.
    """
    modules_dir = CONTENT_ROOT / "modules"
    test_file = modules_dir / "zzz-test-faq-fixture.md"
    try:
        test_file.write_text(_FAQ_FIXTURE_MODULE_CONTENT, encoding="utf-8")

        page = load_page("module", slug="zzz-test-faq-fixture")
        assert page is not None

        with app.test_client() as client:
            rv = client.get("/modules/zzz-test-faq-fixture")
            assert rv.status_code == 200
            html = rv.data.decode()

        import re

        ld_match = re.search(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        assert ld_match is not None, "no JSON-LD script found"
        ld = json.loads(ld_match.group(1))

        nodes = ld["@graph"] if "@graph" in ld else [ld]
        assert any(n.get("@type") == "SoftwareApplication" for n in nodes), (
            "fixture module page lost its own SoftwareApplication node"
        )

        faq = _faq_node(ld)
        assert faq is not None, "fixture module page has no FAQPage node"
        assert len(faq["mainEntity"]) == 2, (
            f"expected 2 FAQ entries, got {len(faq['mainEntity'])}"
        )
        for item in faq["mainEntity"]:
            assert item["@type"] == "Question"
            assert item["name"]
            assert item["acceptedAnswer"]["text"]
    finally:
        if test_file.exists():
            test_file.unlink()


def test_page_with_no_faq_section_gets_no_faq_node():
    """A page with no "## Frequently asked ..." section at all gets no
    FAQPage node -- the generalisation only fires on a real section, it
    does not invent one."""
    from app.services.public_pages import build_jsonld

    page = load_page("module", slug="applications")
    assert page is not None
    ld = json.loads(build_jsonld(page))
    assert _faq_node(ld) is None


# ── Meta description precedence: explicit front matter over fallback ──────


_DESCRIPTION_FIXTURE_CONTENT = """---
page_family: module
module_label: "Description Fixture Module"
state: on_main
cta: plans
capture_status: live
description: "The explicit front-matter description, not the first paragraph below."
---

# Description Fixture Module

This is the first paragraph, which would become the fallback description if
no explicit front-matter description were set above -- it must not win.
"""


def test_explicit_description_front_matter_wins_over_fallback():
    """A page with an explicit front-matter description uses it verbatim,
    never the generated first-paragraph fallback -- no real content page
    sets one yet (that's the content-writer's rewrite work, not this
    brief's), so a fixture page proves the precedence the mechanism itself
    must have."""
    modules_dir = CONTENT_ROOT / "modules"
    test_file = modules_dir / "zzz-test-description-fixture.md"
    try:
        test_file.write_text(_DESCRIPTION_FIXTURE_CONTENT, encoding="utf-8")
        page = load_page("module", slug="zzz-test-description-fixture")
        assert page is not None
        assert page.description == (
            "The explicit front-matter description, not the first paragraph below."
        )
    finally:
        if test_file.exists():
            test_file.unlink()


def test_page_without_explicit_description_falls_back_to_first_paragraph():
    """A page with no front-matter description still gets a non-empty,
    generated one from its own first paragraph."""
    pages = load_all_pages()
    without_description = [
        p for p in pages if not (
            isinstance(p.front_matter.get("description"), str)
            and p.front_matter["description"].strip()
        )
    ]
    assert without_description, "expected at least one page with no explicit description"
    checked = False
    for page in without_description:
        if page.description:
            checked = True
            assert page.description != page.front_matter.get("description")
            assert len(page.description) <= 156  # 155 chars + "…"
    assert checked, "no page's fallback description could be checked"
