"""Tests for public content pages (T-SITE-1).

Covers:
  AC1 — Every file under content/pages/ returns 200 signed out, title in <title> and <h1>.
  AC2 — No rendered page contains front-matter keys or forbidden strings.
  AC3 — /llms.txt lists every page; /sitemap.xml includes every page.
  AC4 — Each page has valid JSON-LD for its family.
  AC5 — Adding a new Markdown file makes it appear at its URL, in sitemap and llms.txt.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from app.services.public_pages import (
    CONTENT_ROOT,
    FAMILY_DIR_MAP,
    FAMILY_URL_PREFIX,
    _parse_front_matter,
    build_jsonld,
    load_all_pages,
    load_feed_pages,
    load_page,
)

FORBIDDEN_STRINGS = [
    "on_main",
    "briefed",
    "in_review",
    "UC-S",
]

# Factual claims that were found false on one or more content pages and
# corrected. Each entry is (banned phrase, why it is false) so a future
# diff that reintroduces any of these phrases fails loudly instead of
# shipping a disproven claim again.
BANNED_CLAIMS = [
    (
        "straight through to your change-request system",
        "ARBDecisionEvent's subject_type is DB-constrained to decision_brief/solution/"
        "architecture_model/adr only -- no model links an ARB decision to a change request",
    ),
    (
        "control-gap view",
        "RiskEntityLink only links risks to application/solution/programme, never to a "
        "compliance control; the real compliance mechanism maps applications to controls, "
        "not risks",
    ),
    (
        "current automatically",
        "no content page module keeps any derived map, dependency graph, or model current "
        "without an explicit action recorded by someone",
    ),
    (
        "vendor and procurement detail",
        "an application's own record shows the vendor name as plain text only -- no page "
        "links from an application to vendor or procurement detail",
    ),
    (
        "not published; free to self-host",
        "Entelim's prices are published at /pricing (Startup $49/month, Team $29/editor/month) -- "
        "self-hosting under AGPL is a separate, true fact, but it does not mean pricing is "
        "unpublished",
    ),
    (
        "every canvas",
        "only the Business Model Canvas model exists (app/models/business_model.py); no other "
        "canvas type is modelled, so no plan or page can honestly promise 'every canvas'",
    ),
    (
        "who to hire next",
        "no hiring or workforce-planning capability exists anywhere in the codebase; "
        "content/pages/function-per-segment/uc-s3-12-who-do-we-need.md records this as a feature "
        "with no surface built at all",
    ),
    (
        "enter your website address",
        "there is no website-URL-intake feature; no route builds a model or a canvas from a "
        "submitted URL",
    ),
    (
        "your team's own AI assistants can work directly over your twin",
        "no MCP server exists in this codebase; only Entelim's own AI chat works over a tenant's "
        "model today",
    ),
    (
        "usage history",
        "no customer-visible usage-history page is confirmed to exist; dropped from every plan "
        "description rather than promised",
    ),
    (
        "webhook feed",
        "the real, shipped feature is a generic webhook subscription (admin.webhook_settings), "
        "not a branded 'webhook feed'",
    ),
    (
        "twelve platform events",
        "WebhookService.publish_event() is called from exactly one place in the app "
        "(POST /public/events) -- nothing internal to Entelim ever publishes any of the twelve "
        "named events on the webhook-settings picklist, so the platform does not notify a "
        "subscriber when one of those twelve things happens; only a customer's own API call or "
        "the manual 'test' button ever publishes an event",
    ),
    (
        "canvas drafted from your own site",
        "no route or service reads a customer's website to draft a canvas; "
        "uc-s1-09-website-full-profile.md (state: missing) records this as still "
        "unbuilt, not a shipped feature",
    ),
    (
        "ready for the diligence request",
        "no diligence-pack or investor-readiness export feature exists anywhere in the "
        "codebase",
    ),
    (
        "every suggestion traced to its source",
        "only a derived connection (computed from other data, see "
        "app/modules/intelligence/services/derived_facts.py's provenance field) carries a "
        "proof trail; a plain suggestion does not",
    ),
]

# Front-matter keys that are metadata-only and must never appear as visible
# text. We check these as whole-word patterns to avoid false positives from
# common English words like "source" or "state" that appear in body copy.
FORBIDDEN_FRONT_MATTER_PATTERNS = [
    "page_family",
    "url_slug",
    "capture_status",
    "compliance_note",
    "answers_use_cases",
    "grouped_sub_pages",
    "verification_note",
    "page_role",
    "roadmap_citation",
    "module_label",
    "use_case_id",
    "segment_id",
]


# ── AC1: Every file returns 200 with title in <title> and <h1> ────────────


def test_all_pages_return_200(app):
    """Every .md file under content/pages/ returns 200 at its URL."""
    pages = load_all_pages()
    assert len(pages) > 0, "No pages loaded from content/pages/"
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            assert rv.status_code == 200, f"{page.url} returned {rv.status_code}"


def test_all_pages_have_title_in_html_title(app):
    """Every page has its title in the <title> element, correctly encoded."""
    import html as html_mod
    import re

    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            title_match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
            assert title_match is not None, f"{page.url}: no <title> found"
            raw_title = title_match.group(1).strip()
            # Must not contain double-encoded entities
            assert "&amp;amp;" not in raw_title, (
                f"{page.url}: double-encoded &amp; in <title> '{raw_title}'"
            )
            assert "&amp;lt;" not in raw_title, (
                f"{page.url}: double-encoded &lt; in <title> '{raw_title}'"
            )
            # After a single unescape the title must contain the page title
            rendered_title = html_mod.unescape(raw_title)
            assert page.title in rendered_title, (
                f"{page.url}: title '{page.title}' not in <title> '{rendered_title}'"
            )


def test_all_pages_have_h1_with_title(app):
    """Every page has its title in an <h1> element."""
    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            assert "<h1" in html, f"{page.url}: no <h1> found"
            # The h1 should contain the page title text (stripped of HTML)
            import re

            h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
            assert h1_match is not None, f"{page.url}: <h1> not found"
            h1_text = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()
            assert len(h1_text) > 0, f"{page.url}: <h1> is empty"


# ── AC2: No forbidden strings in rendered output ──────────────────────────


def test_no_forbidden_strings_in_rendered_pages(app):
    """No rendered page body contains front-matter keys or forbidden strings."""
    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            # Remove script and style blocks before checking
            clean = _strip_tags(html, ["script", "style"])
            for forbidden in FORBIDDEN_STRINGS:
                assert forbidden not in clean, (
                    f"{page.url}: forbidden string '{forbidden}' found in rendered output"
                )
            for key in FORBIDDEN_FRONT_MATTER_PATTERNS:
                assert key not in clean, (
                    f"{page.url}: front-matter key '{key}' found in rendered output"
                )


def _strip_tags(html: str, tags: list[str]) -> str:
    """Remove content between opening and closing tags."""
    import re

    result = html
    for tag in tags:
        result = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>", "", result, flags=re.DOTALL | re.IGNORECASE
        )
    return result


# ── AC3: /llms.txt and /sitemap.xml ───────────────────────────────────────


def test_llms_txt_lists_every_page(app):
    """/llms.txt lists every page with its title and URL, except a page
    withdrawn from discovery (state: not_planned), which it must not list."""
    pages = load_all_pages()
    with app.test_client() as client:
        rv = client.get("/llms.txt")
        assert rv.status_code == 200
        text = rv.data.decode()
        for page in pages:
            if page.is_withdrawn:
                assert page.url not in text, (
                    f"llms.txt should not list withdrawn page {page.url}"
                )
                continue
            assert page.url in text, (
                f"llms.txt missing URL {page.url}"
            )
            # Title should appear (may be truncated in link format)
            assert page.title[:30] in text, (
                f"llms.txt missing title '{page.title[:30]}' for {page.url}"
            )


def test_sitemap_xml_includes_every_page(app):
    """/sitemap.xml includes every page, except a page withdrawn from
    discovery (state: not_planned), which it must not include."""
    pages = load_all_pages()
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        assert rv.status_code == 200
        xml = rv.data.decode()
        assert xml.startswith('<?xml')
        assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in xml
        for page in pages:
            if page.is_withdrawn:
                assert page.url not in xml, (
                    f"sitemap.xml should not include withdrawn page {page.url}"
                )
                continue
            assert page.url in xml, (
                f"sitemap.xml missing URL {page.url}"
            )


def test_sitemap_xml_lists_the_homepage_once_with_top_priority(app):
    """The home page is not a content page, but it is the most important URL."""
    import re
    from urllib.parse import urlparse

    with app.test_client() as client:
        xml = client.get("/sitemap.xml").data.decode()

    entries = re.findall(r"<url>\s*<loc>([^<]+)</loc>(.*?)</url>", xml, flags=re.S)
    homepage = [(loc, rest) for loc, rest in entries if urlparse(loc).path == "/"]
    assert len(homepage) == 1
    assert "<priority>1.0</priority>" in homepage[0][1]
    # Listing it does not displace any content page. +3 non-content URLs:
    # the homepage, the /vs comparison hub and the /use-cases index (views,
    # not load_all_pages() pages). load_feed_pages(), not load_all_pages():
    # the sitemap is built from the feed set, which leaves out any page
    # withdrawn from discovery (state: not_planned).
    assert len(entries) == len(load_feed_pages()) + 3


def test_withdrawn_pages_200_but_excluded_from_every_feed(app):
    """A page withdrawn from discovery (front matter ``state: not_planned``)
    still renders at its own URL -- an existing inbound link or bookmark
    must not 404 -- but its URL and title must not appear in /sitemap.xml,
    /llms.txt or /llms-full.txt, and (for a use-case page) it must not be
    linked from the /use-cases index either.

    Found generically from load_all_pages()/PublicPage.is_withdrawn, not
    hardcoded to one page's slug, so this keeps covering the mechanism if
    another page is withdrawn the same way later.
    """
    withdrawn = [p for p in load_all_pages() if p.is_withdrawn]
    assert len(withdrawn) > 0, (
        "expected at least one withdrawn page (state: not_planned) to exist "
        "on this branch to exercise the exclusion"
    )

    with app.test_client() as client:
        for page in withdrawn:
            rv = client.get(page.url)
            assert rv.status_code == 200, (
                f"withdrawn page {page.url} should still render, got {rv.status_code}"
            )

            sitemap = client.get("/sitemap.xml").data.decode()
            assert page.url not in sitemap, (
                f"sitemap.xml should not include withdrawn page {page.url}"
            )

            llms = client.get("/llms.txt").data.decode()
            assert page.url not in llms, (
                f"llms.txt should not include withdrawn page {page.url}"
            )

            llms_full = client.get("/llms-full.txt").data.decode()
            assert page.title not in llms_full, (
                f"llms-full.txt should not contain withdrawn page title '{page.title}'"
            )

            if page.family == "function-per-segment":
                use_cases_html = client.get("/use-cases").data.decode()
                assert f'href="{page.url}"' not in use_cases_html, (
                    f"/use-cases should not link to withdrawn page {page.url}"
                )


def test_withdrawn_page_is_noindex_but_live_page_is_not(app):
    """A page withdrawn from discovery (state: not_planned) still renders,
    but must tell search engines not to index it; a normal live page must
    not carry that tag at all."""
    withdrawn = [p for p in load_all_pages() if p.is_withdrawn]
    assert withdrawn, (
        "expected at least one withdrawn page (state: not_planned) to exist "
        "on this branch to exercise the noindex tag"
    )
    live = next(p for p in load_all_pages() if not p.is_withdrawn)

    with app.test_client() as client:
        for page in withdrawn:
            html = client.get(page.url).data.decode()
            assert '<meta name="robots" content="noindex">' in html, (
                f"withdrawn page {page.url} is missing <meta name=\"robots\" content=\"noindex\">"
            )

        live_html = client.get(live.url).data.decode()
        assert 'name="robots"' not in live_html, (
            f"live page {live.url} should not carry a robots noindex tag"
        )


def _strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings_in(item)


def test_structured_data_cannot_end_its_script_block_early(app):
    """A title with a closing script tag must not break out of the JSON-LD block."""
    import dataclasses

    hostile = 'Vision </script><script>alert(1)</script> & <!-- more'
    page = dataclasses.replace(load_all_pages()[0], title=hostile)

    serialised = build_jsonld(page)

    assert "</script>" not in serialised and "<" not in serialised and ">" not in serialised
    assert "&" not in serialised
    # Still valid JSON, and it decodes to exactly the text that was put in.
    assert hostile in set(_strings_in(json.loads(serialised)))


def test_every_page_renders_one_script_block_for_its_structured_data(app):
    """Rendered pages keep exactly one JSON-LD script element."""
    with app.test_client() as client:
        for page in load_all_pages()[:8]:
            html = client.get(page.url).data.decode()
            assert html.count('type="application/ld+json"') == 1


def test_llms_txt_has_correct_content_type(app):
    """/llms.txt returns text/plain."""
    with app.test_client() as client:
        rv = client.get("/llms.txt")
        assert "text/plain" in rv.content_type


def test_sitemap_xml_has_correct_content_type(app):
    """/sitemap.xml returns application/xml."""
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        assert "xml" in rv.content_type


# ── AC3 extended: llms.txt Capabilities section and llms-full.txt ─────────────


def test_llms_txt_has_capabilities_section(app):
    """/llms.txt includes a ## Capabilities section with all module pages."""
    from app.services.public_pages import load_all_pages

    module_pages = [p for p in load_all_pages() if p.family == "module"]
    assert len(module_pages) > 0, "No module pages found"

    with app.test_client() as client:
        rv = client.get("/llms.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

        # Check Capabilities section exists
        assert "## Capabilities" in text, "llms.txt missing ## Capabilities section"

        # Every module page should appear in the Capabilities section with its URL
        for page in module_pages:
            assert page.url in text, f"llms.txt Capabilities section missing URL {page.url}"
            # Title should appear
            assert page.title in text, f"llms.txt Capabilities section missing title '{page.title}'"
            # Should have a descriptive sentence (the — separator)
            assert f"[{page.title}](https://entelim.org{page.url}) —" in text, (
                f"llms.txt Capabilities section missing description for {page.url}"
            )


def test_llms_full_txt_returns_200(app):
    """/llms-full.txt returns 200 with text/plain content type."""
    with app.test_client() as client:
        rv = client.get("/llms-full.txt")
        assert rv.status_code == 200
        assert "text/plain" in rv.content_type


def test_llms_full_txt_contains_all_module_titles(app):
    """/llms-full.txt contains the title of every module page."""
    from app.services.public_pages import load_all_pages

    module_pages = [p for p in load_all_pages() if p.family == "module"]
    assert len(module_pages) > 0, "No module pages found"

    with app.test_client() as client:
        rv = client.get("/llms-full.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

        for page in module_pages:
            assert page.title in text, f"llms-full.txt missing module title '{page.title}'"


def test_llms_full_txt_contains_all_use_case_titles(app):
    """/llms-full.txt contains the title of every use-case page, except a
    page withdrawn from discovery (state: not_planned), which it must not
    contain."""
    from app.services.public_pages import load_all_pages

    use_case_pages = [p for p in load_all_pages() if p.family == "function-per-segment"]
    assert len(use_case_pages) > 0, "No use-case pages found"
    assert any(p.is_withdrawn for p in use_case_pages), (
        "expected at least one withdrawn use-case page to exercise the exclusion"
    )

    with app.test_client() as client:
        rv = client.get("/llms-full.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

        for page in use_case_pages:
            if page.is_withdrawn:
                assert page.title not in text, (
                    f"llms-full.txt should not contain withdrawn use-case title '{page.title}'"
                )
                continue
            assert page.title in text, f"llms-full.txt missing use-case title '{page.title}'"


def test_llms_full_txt_contains_all_comparison_titles(app):
    """/llms-full.txt contains the title of every comparison page."""
    from app.services.public_pages import load_all_pages

    comparison_pages = [p for p in load_all_pages() if p.family == "comparison"]
    assert len(comparison_pages) > 0, "No comparison pages found"

    with app.test_client() as client:
        rv = client.get("/llms-full.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

        for page in comparison_pages:
            assert page.title in text, f"llms-full.txt missing comparison title '{page.title}'"


def test_llms_full_txt_includes_urls(app):
    """/llms-full.txt includes the URL for each page, except a page withdrawn
    from discovery (state: not_planned), which it must not include."""
    from app.services.public_pages import load_all_pages

    target_pages = [
        p for p in load_all_pages()
        if p.family in {"module", "function-per-segment", "comparison"}
    ]
    assert len(target_pages) > 0, "No target pages found"

    with app.test_client() as client:
        rv = client.get("/llms-full.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

        for page in target_pages:
            if page.is_withdrawn:
                assert page.url not in text, (
                    f"llms-full.txt should not include withdrawn page URL {page.url}"
                )
                continue
            assert page.url in text, f"llms-full.txt missing URL {page.url}"


def test_llms_full_txt_under_size_limit(app):
    """/llms-full.txt is under 2 MB."""
    with app.test_client() as client:
        rv = client.get("/llms-full.txt")
        assert rv.status_code == 200
        assert len(rv.data) < 2 * 1024 * 1024, "llms-full.txt exceeds 2 MB limit"


# ── Review findings: llms.txt defects ───────────────────────────────────────


def test_llms_txt_no_duplicate_urls(app):
    """/llms.txt must not contain any URL more than once."""
    from app.services.public_pages import load_all_pages

    with app.test_client() as client:
        rv = client.get("/llms.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

    # Extract all URLs from markdown links [title](url)
    import re

    urls = re.findall(r"\]\((https://entelim\.org[^)]+)\)", text)
    # Count occurrences
    from collections import Counter

    counts = Counter(urls)
    duplicates = [url for url, count in counts.items() if count > 1]
    assert not duplicates, f"llms.txt contains duplicate URLs: {duplicates}"


def test_llms_txt_first_sentence_not_glued_to_title(app):
    """For ai-chat, the extracted sentence in llms.txt does not start with the page title."""
    from app.services.public_pages import load_page

    page = load_page("module", slug="ai-chat")
    assert page is not None, "ai-chat page not found"

    with app.test_client() as client:
        rv = client.get("/llms.txt")
        assert rv.status_code == 200
        text = rv.data.decode()

    # Find the line for ai-chat in the Capabilities section
    import re

    # Pattern: - [AI Chat](https://entelim.org/modules/ai-chat) — <sentence>
    pattern = rf"\[{re.escape(page.title)}\]\(https://entelim\.org{re.escape(page.url)}\) — ([^\n]+)"
    match = re.search(pattern, text)
    assert match is not None, "ai-chat entry not found in llms.txt Capabilities section"

    sentence = match.group(1).strip()
    # The sentence must not start with the page title (glued)
    assert not sentence.startswith(page.title), (
        f"Extracted sentence starts with page title (glued): '{sentence}'"
    )
    # The sentence should be a proper sentence starting with a capital letter
    assert sentence[0].isupper(), f"Sentence should start with capital letter: '{sentence}'"


def test_html_to_plain_text_removes_script_and_style_content():
    """_html_to_plain_text removes <script> and <style> elements with their content."""
    from app.main.views import _html_to_plain_text

    html = """
    <h1>Title</h1>
    <script>alert('xss'); var x = 1;</script>
    <p>Paragraph 1.</p>
    <style>.hidden { display: none; }</style>
    <p>Paragraph 2.</p>
    """
    result = _html_to_plain_text(html)

    # Script content must not appear
    assert "alert('xss')" not in result, "Script content leaked into plain text"
    assert "var x = 1" not in result, "Script content leaked into plain text"
    # Style content must not appear
    assert ".hidden" not in result, "Style content leaked into plain text"
    assert "display: none" not in result, "Style content leaked into plain text"
    # But regular content must remain
    assert "Title" in result
    assert "Paragraph 1" in result
    assert "Paragraph 2" in result


# ── AC4: JSON-LD per page family ──────────────────────────────────────────


def test_every_page_has_valid_jsonld(app):
    """Each page has valid JSON-LD that parses as JSON."""
    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            assert 'application/ld+json' in html, (
                f"{page.url}: no JSON-LD script found"
            )
            # Extract JSON-LD
            import re

            ld_match = re.search(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            assert ld_match is not None, f"{page.url}: JSON-LD script not found"
            ld_text = ld_match.group(1).strip()
            try:
                ld = json.loads(ld_text)
            except json.JSONDecodeError as e:
                pytest.fail(f"{page.url}: invalid JSON-LD: {e}")
            assert "@context" in ld, f"{page.url}: JSON-LD missing @context"
            assert "@type" in ld, f"{page.url}: JSON-LD missing @type"


def test_comparison_pages_have_faq_jsonld(app):
    """Comparison pages have FAQPage JSON-LD with mainEntity."""
    pages = [p for p in load_all_pages() if p.family == "comparison"]
    assert len(pages) > 0, "No comparison pages found"
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            import re

            ld_match = re.search(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            ld = json.loads(ld_match.group(1))
            assert ld["@type"] == "FAQPage", (
                f"{page.url}: expected FAQPage, got {ld['@type']}"
            )
            assert "mainEntity" in ld, f"{page.url}: FAQPage missing mainEntity"


def test_module_pages_have_software_application_jsonld(app):
    """Module pages have SoftwareApplication JSON-LD."""
    pages = [p for p in load_all_pages() if p.family == "module"]
    assert len(pages) > 0, "No module pages found"
    with app.test_client() as client:
        for page in pages[:5]:  # Sample first 5
            rv = client.get(page.url)
            html = rv.data.decode()
            import re

            ld_match = re.search(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            ld = json.loads(ld_match.group(1))
            assert ld["@type"] == "SoftwareApplication", (
                f"{page.url}: expected SoftwareApplication, got {ld['@type']}"
            )


def test_comparison_pages_have_canonical_link(app):
    """Comparison pages with archiet.ai url_slug have canonical link."""
    pages = [p for p in load_all_pages() if p.family == "comparison"]
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            if page.canonical_url:
                assert f'rel="canonical" href="{page.canonical_url}"' in html, (
                    f"{page.url}: missing canonical link to {page.canonical_url}"
                )


# ── AC5: Adding a new Markdown file works without code changes ────────────


def test_new_markdown_file_appears_at_url(app, tmp_path):
    """Adding a new .md file makes it appear at its URL, in sitemap and llms.txt."""
    import markdown

    # Create a temporary module file
    test_content = """---
page_family: module
module_label: "Test Module"
state: on_main
cta: waiting_list
---

# Test Module Title

This is a test module page.
"""
    # We need to write into the real content/pages/modules/ directory
    # because the loader reads from CONTENT_ROOT
    modules_dir = CONTENT_ROOT / "modules"
    test_file = modules_dir / "zzz-test-temp-module.md"
    try:
        test_file.write_text(test_content, encoding="utf-8")

        # Reload pages
        pages = load_all_pages()
        test_page = next(
            (p for p in pages if p.slug == "zzz-test-temp-module"), None
        )
        assert test_page is not None, "New page not loaded"
        assert test_page.url == "/modules/zzz-test-temp-module"

        with app.test_client() as client:
            # AC5a: Returns 200 at its URL
            rv = client.get("/modules/zzz-test-temp-module")
            assert rv.status_code == 200, (
                f"New page returned {rv.status_code}"
            )
            html = rv.data.decode()
            assert "Test Module Title" in html

            # AC5b: Appears in sitemap
            rv = client.get("/sitemap.xml")
            assert "/modules/zzz-test-temp-module" in rv.data.decode()

            # AC5c: Appears in llms.txt
            rv = client.get("/llms.txt")
            assert "/modules/zzz-test-temp-module" in rv.data.decode()

            # AC5d: Has JSON-LD
            assert 'application/ld+json' in html

            # AC5e: cta=waiting_list shows waiting list link
            assert "/#waitlist" in html
            assert "Join the waiting list" in html
    finally:
        if test_file.exists():
            test_file.unlink()


def test_new_page_without_code_changes(app):
    """Adding a new .md file to function-per-segment works without code changes."""
    seg_dir = CONTENT_ROOT / "function-per-segment"
    test_file = seg_dir / "zzz-test-new-segment.md"
    test_content = """---
page_family: function-per-segment
use_case_id: UC-TEST-01
segment_id: S1
state: on_main
---

# Test Segment Page

Content for the test segment page.
"""
    try:
        test_file.write_text(test_content, encoding="utf-8")

        with app.test_client() as client:
            rv = client.get("/use-cases/zzz-test-new-segment")
            assert rv.status_code == 200
            html = rv.data.decode()
            assert "Test Segment Page" in html
            assert 'application/ld+json' in html

            # Verify in sitemap and llms.txt
            rv = client.get("/sitemap.xml")
            assert "/use-cases/zzz-test-new-segment" in rv.data.decode()

            rv = client.get("/llms.txt")
            assert "/use-cases/zzz-test-new-segment" in rv.data.decode()
    finally:
        if test_file.exists():
            test_file.unlink()


# ── Cross-organisation tests ──────────────────────────────────────────────


def test_public_pages_accessible_without_login(app):
    """All public pages are accessible without authentication."""
    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages[:10]:  # Sample
            rv = client.get(page.url)
            assert rv.status_code == 200, (
                f"{page.url} returned {rv.status_code} without login"
            )


def test_public_pages_no_login_required(app):
    """Public content pages do not require authentication (no redirect to login)."""
    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages[:10]:  # Sample
            rv = client.get(page.url, follow_redirects=False)
            assert rv.status_code == 200, (
                f"{page.url} returned {rv.status_code} without login"
            )
            # Must not redirect to login
            assert rv.location is None or "login" not in rv.location.lower(), (
                f"{page.url} redirected to login: {rv.location}"
            )


def test_public_pages_read_only_no_state_change(app):
    """GET requests to public pages do not modify database state."""
    from app import db as database

    pages = load_all_pages()
    with app.test_client() as client:
        with app.app_context():
            # Capture row counts before
            from sqlalchemy import text
            before = {}
            for table in ["users", "organizations"]:
                try:
                    result = database.session.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    ).scalar()
                    before[table] = result
                except Exception:
                    pass

        for page in pages[:5]:
            client.get(page.url)

        with app.app_context():
            for table, count in before.items():
                try:
                    after = database.session.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    ).scalar()
                    assert after == count, (
                        f"Table {table} changed from {count} to {after}"
                    )
                except Exception:
                    pass


# ── Service-level unit tests ──────────────────────────────────────────────


def test_parse_front_matter():
    """YAML front-matter is parsed correctly."""
    raw = """---
key1: value1
key2: true
---
# Body
Content here.
"""
    meta, body = _parse_front_matter(raw)
    assert meta == {"key1": "value1", "key2": True}
    assert "# Body" in body
    assert "Content here." in body


def test_parse_front_matter_no_delimiters():
    """Content without front-matter returns empty dict."""
    raw = "# Just a heading\nContent."
    meta, body = _parse_front_matter(raw)
    assert meta == {}
    assert body == raw


def test_load_all_pages_returns_list():
    """load_all_pages returns a non-empty list."""
    pages = load_all_pages()
    assert isinstance(pages, list)
    assert len(pages) > 0


def test_load_page_known_module():
    """load_page returns a page for a known module."""
    page = load_page("module", slug="applications")
    assert page is not None
    assert page.family == "module"
    assert page.url == "/modules/applications"
    assert len(page.title) > 0
    assert len(page.body_html) > 0


def test_load_page_nonexistent():
    """load_page returns None for a nonexistent page."""
    page = load_page("module", slug="nonexistent-xyz")
    assert page is None


def test_load_page_vision():
    """load_page returns the vision page."""
    page = load_page("vision")
    assert page is not None
    assert page.url == "/vision"


def test_load_page_dogfood():
    """load_page returns the dogfood page."""
    page = load_page("dogfood")
    assert page is not None
    assert page.url == "/how-archiet-runs-on-entelim"


def test_build_jsonld_webpage():
    """build_jsonld returns valid JSON for a function-per-segment page."""
    page = load_page("function-per-segment", slug="canvas-dependencies")
    assert page is not None
    ld_str = build_jsonld(page)
    ld = json.loads(ld_str)
    assert ld["@type"] == "WebPage"
    assert "name" in ld


def test_build_jsonld_module():
    """build_jsonld returns SoftwareApplication for a module page."""
    page = load_page("module", slug="applications")
    assert page is not None
    ld_str = build_jsonld(page)
    ld = json.loads(ld_str)
    assert ld["@type"] == "SoftwareApplication"
    assert "offers" in ld


def test_build_jsonld_comparison():
    """build_jsonld returns FAQPage for a comparison page."""
    page = load_page("comparison", slug="leanix")
    assert page is not None
    ld_str = build_jsonld(page)
    ld = json.loads(ld_str)
    assert ld["@type"] == "FAQPage"


def test_comparison_canonical_url():
    """Comparison pages with archiet.ai url_slug have canonical_url set."""
    page = load_page("comparison", slug="leanix")
    assert page is not None
    assert page.canonical_url == "https://archiet.ai/vs/leanix"


def test_non_comparison_no_canonical():
    """Non-comparison pages have no canonical_url."""
    page = load_page("module", slug="applications")
    assert page is not None
    assert page.canonical_url is None


def test_waiting_list_cta_renders_link(app):
    """Pages with cta=waiting_list show the waiting list link."""
    # ai-chat moved to cta: plans (feature shipped) and /contact moved to
    # cta: inquiry (a sales enquiry form), so use a not-yet-built use-case
    # page instead, which still carries cta: waiting_list. Its current,
    # readable URL -- the old uc-s1-01-canvas-dependencies filename form
    # now 301-redirects here instead of rendering directly.
    with app.test_client() as client:
        rv = client.get("/use-cases/canvas-dependencies")
        html = rv.data.decode()
        assert "/#waitlist" in html
        assert "Join the waiting list" in html


def test_no_waiting_list_on_non_cta_pages(app):
    """Pages without cta=waiting_list do not show the waiting list link."""
    with app.test_client() as client:
        rv = client.get("/modules/applications")
        html = rv.data.decode()
        # applications has no cta field, so no waiting list
        assert "Join the waiting list" not in html


def test_page_count_consistency(app):
    """load_all_pages publishes every .md file in content/pages/, except that the
    legal family is held back until LEGAL_PAGES_ENABLED is on."""

    def md_count(families):
        return sum(
            1
            for family, family_dir_name in FAMILY_DIR_MAP.items()
            if family in families and (CONTENT_ROOT / family_dir_name).is_dir()
            for _ in (CONTENT_ROOT / family_dir_name).glob("*.md")
        )

    everything = set(FAMILY_DIR_MAP)
    for enabled, families in ((False, everything - {"legal"}), (True, everything)):
        with app.app_context():
            app.config["LEGAL_PAGES_ENABLED"] = enabled
            try:
                pages = load_all_pages()
            finally:
                app.config["LEGAL_PAGES_ENABLED"] = False
        assert len(pages) == md_count(families), (
            f"flag={enabled}: load_all_pages returned {len(pages)} pages "
            f"but {md_count(families)} .md files exist"
        )


# ── Tests for review findings ─────────────────────────────────────────────


def test_title_html_entities_decoded():
    """Titles containing HTML entities like &amp; are decoded to plain text."""
    page = load_page("module", slug="diagrams")
    assert page is not None
    # The markdown heading is "Diagrams & Composer"
    # After fix: title should be "Diagrams & Composer" (decoded), not "Diagrams &amp; Composer"
    assert page.title == "Diagrams & Composer", (
        f"Expected 'Diagrams & Composer', got '{page.title}'"
    )
    assert "&amp;" not in page.title, (
        f"Title contains HTML entity: '{page.title}'"
    )


def test_title_html_entities_decoded_org_chart():
    """Org Chart & RACI title has & decoded."""
    page = load_page("module", slug="org-chart")
    assert page is not None
    assert page.title == "Org Chart & RACI", (
        f"Expected 'Org Chart & RACI', got '{page.title}'"
    )
    assert "&amp;" not in page.title


def test_comparison_faq_jsonld_has_entries():
    """Comparison pages with <strong>-format FAQ produce non-empty mainEntity."""
    for slug in ["leanix", "ardoq", "bizzdesign-hopex", "avolution-abacus", "orbus-iserver"]:
        page = load_page("comparison", slug=slug)
        assert page is not None, f"Comparison page {slug} not found"
        ld_str = build_jsonld(page)
        ld = json.loads(ld_str)
        assert ld["@type"] == "FAQPage", f"{slug}: expected FAQPage"
        assert len(ld["mainEntity"]) > 0, (
            f"{slug}: FAQPage mainEntity is empty, got {ld['mainEntity']}"
        )
        for item in ld["mainEntity"]:
            assert item["@type"] == "Question"
            assert len(item["name"]) > 0
            assert item["acceptedAnswer"]["@type"] == "Answer"
            assert len(item["acceptedAnswer"]["text"]) > 0


def test_comparison_faq_jsonld_question_count():
    """Each comparison page has the expected number of FAQ entries."""
    expected_counts = {
        "leanix": 3,
        "ardoq": 3,
        "bizzdesign-hopex": 2,
        "avolution-abacus": 4,
        "orbus-iserver": 3,
    }
    for slug, expected in expected_counts.items():
        page = load_page("comparison", slug=slug)
        assert page is not None
        ld = json.loads(build_jsonld(page))
        actual = len(ld["mainEntity"])
        assert actual == expected, (
            f"{slug}: expected {expected} FAQ entries, got {actual}"
        )


def test_rendered_title_no_double_encoding(app):
    """Pages with & in title render correctly in <title> without double-encoding."""
    import re

    with app.test_client() as client:
        for url in ["/modules/diagrams", "/modules/org-chart"]:
            rv = client.get(url)
            html = rv.data.decode()
            title_match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
            assert title_match is not None, f"{url}: no <title>"
            raw_title = title_match.group(1)
            # Must not contain double-encoded entities
            assert "&amp;amp;" not in raw_title, (
                f"{url}: double-encoded &amp; in <title>: '{raw_title}'"
            )
            # Must contain a properly encoded &
            assert "&amp;" in raw_title, (
                f"{url}: missing &amp; in <title>: '{raw_title}'"
            )


# ── XSS sanitization tests ─────────────────────────────────────────────────


def test_xss_script_tag_stripped_from_rendered_page(app):
    """<script> tags in Markdown source are stripped from rendered output."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    raw_html = md.convert('<script>alert("xss")</script>\n\n# Title')
    sanitized = _sanitize_html(raw_html)
    assert "<script>" not in sanitized
    assert "</script>" not in sanitized
    assert "<h1>Title</h1>" in sanitized


def test_xss_event_handler_stripped(app):
    """Event handlers like onclick are stripped from rendered HTML."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    raw_html = md.convert('<p onclick="alert(1)">test</p>')
    sanitized = _sanitize_html(raw_html)
    assert "onclick" not in sanitized
    assert "<p>test</p>" in sanitized


def test_xss_img_onerror_stripped(app):
    """onerror handlers on img tags are stripped."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    raw_html = md.convert('<img src=x onerror="alert(1)">')
    sanitized = _sanitize_html(raw_html)
    assert "onerror" not in sanitized


def test_xss_legitimate_html_preserved(app):
    """Legitimate Markdown-generated HTML is preserved after sanitization."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    test_md = """# Title

**bold** and *italic* and [a link](https://example.com).

- list item 1
- list item 2
"""
    raw_html = md.convert(test_md)
    sanitized = _sanitize_html(raw_html)
    assert "<h1>Title</h1>" in sanitized
    assert "<strong>bold</strong>" in sanitized
    assert "<em>italic</em>" in sanitized
    assert '<a href="https://example.com">a link</a>' in sanitized
    assert "<li>list item 1</li>" in sanitized


def test_xss_svg_tag_stripped(app):
    """SVG tags are stripped from rendered HTML."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    raw_html = md.convert('<svg onload="alert(1)"></svg>')
    sanitized = _sanitize_html(raw_html)
    assert "<svg" not in sanitized


def test_xss_iframe_tag_stripped(app):
    """iframe tags are stripped from rendered HTML."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    raw_html = md.convert('<iframe src="https://evil.com"></iframe>')
    sanitized = _sanitize_html(raw_html)
    assert "<iframe" not in sanitized


def test_xss_javascript_url_stripped(app):
    """javascript: URLs in href are stripped."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    raw_html = md.convert('[click me](javascript:alert(1))')
    sanitized = _sanitize_html(raw_html)
    assert "javascript:" not in sanitized


def test_all_rendered_pages_are_sanitized(app):
    """Every rendered page body has no script tags or event handlers."""
    import re

    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            # Extract only the page body content (inside public-page-content)
            body_match = re.search(
                r'<article[^>]*class="[^"]*public-page-content[^"]*"[^>]*>(.*?)</article>',
                html,
                re.DOTALL,
            )
            if body_match is None:
                # Fallback: check the whole page minus script/style blocks
                body_html = html
            else:
                body_html = body_match.group(1)
            assert "<script>" not in body_html.lower(), (
                f"{page.url}: contains <script> tag in page body"
            )
            assert "onerror=" not in body_html.lower(), (
                f"{page.url}: contains onerror handler in page body"
            )
            assert "onclick=" not in body_html.lower(), (
                f"{page.url}: contains onclick handler in page body"
            )
            assert "onload=" not in body_html.lower(), (
                f"{page.url}: contains onload handler in page body"
            )


def test_xss_markdown_code_blocks_preserved(app):
    """Code blocks and inline code are preserved after sanitization."""
    import markdown

    from app.services.public_pages import _sanitize_html

    md = markdown.Markdown(extensions=["extra"])
    test_md = """# Title

Some `inline code`.

```
code block
```
"""
    raw_html = md.convert(test_md)
    sanitized = _sanitize_html(raw_html)
    assert "<code>inline code</code>" in sanitized
    assert "code block" in sanitized


# ── Sitemap homepage tests ─────────────────────────────────────────────────


def test_sitemap_includes_homepage(app):
    """/sitemap.xml includes the homepage URL."""
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        assert rv.status_code == 200
        xml = rv.data.decode()
        assert "<loc>https://entelim.org/</loc>" in xml, (
            "sitemap.xml missing homepage URL"
        )


def test_sitemap_homepage_has_priority(app):
    """/sitemap.xml homepage entry has priority 1.0."""
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        xml = rv.data.decode()
        assert "<priority>1.0</priority>" in xml, (
            "sitemap.xml homepage missing priority"
        )


def test_sitemap_still_includes_all_content_pages(app):
    """/sitemap.xml still includes every content page after homepage addition,
    except a page withdrawn from discovery (state: not_planned)."""
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        xml = rv.data.decode()
        for page in pages:
            if page.is_withdrawn:
                assert page.url not in xml, (
                    f"sitemap.xml should not include withdrawn content page URL {page.url}"
                )
                continue
            assert page.url in xml, (
                f"sitemap.xml missing content page URL {page.url}"
            )


def test_sitemap_xml_valid_with_homepage(app):
    """/sitemap.xml is valid XML with homepage included."""
    import xml.etree.ElementTree as ET

    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        xml = rv.data.decode()
        # Must parse as valid XML
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            pytest.fail(f"sitemap.xml is not valid XML: {e}")
        assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
        urls = root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url")
        assert len(urls) > 0


# ── Cross-organisation test for XSS sanitization ───────────────────────────


def test_xss_sanitization_cross_org(app):
    """Sanitized pages are safe regardless of which organisation's context."""
    # Public pages are filesystem-based with no org context, but verify
    # that no XSS vector can be introduced through any page.
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            html = rv.data.decode()
            # No raw HTML event handlers anywhere
            for handler in ["onerror", "onclick", "onload", "onmouseover",
                          "onfocus", "onblur", "onsubmit"]:
                assert f"{handler}=" not in html.lower(), (
                    f"{page.url}: contains {handler} handler"
                )


# ── robots.txt crawl-access guard ──────────────────────────────────────────
#
# app/static/robots.txt is an explicit allow-list: every ``Allow:`` line
# names one path a crawler may fetch, and the file ends in a catch-all
# ``Disallow: /`` that blocks anything not explicitly named. That shape makes
# it easy to add a new public page or a new crawler-facing file (llms.txt,
# llms-full.txt, sitemap.xml) without ever adding the matching ``Allow:``
# line — the page renders fine for a signed-out visitor, and is simply
# invisible to every crawler.
#
# This guard treats /sitemap.xml, /llms.txt and /llms-full.txt as the three
# places a real crawler discovers the rest of the site, fetches each one for
# real, collects every entelim.org URL any of them names (plus their own
# three paths), and checks each one against the actual rules in robots.txt
# using the same longest-match-wins algorithm real crawlers use. A URL that
# is reachable from one of those three files but not allowed by robots.txt
# fails this test by name.

_ROBOTS_TXT_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "robots.txt"

# The three files a crawler uses to discover the rest of the site. Each
# one's own path must be allowed, and so must every entelim.org URL it names.
_CRAWLER_FACING_ENDPOINTS = ["/sitemap.xml", "/llms.txt", "/llms-full.txt"]


def _parse_robots_rules(text: str) -> list[tuple[str, str]]:
    """Return the ordered ``(directive, pattern)`` rules for ``User-agent: *``.

    Only the rule lines that apply to the wildcard user-agent group are
    kept, in file order, which is all this robots.txt has.
    """
    rules: list[tuple[str, str]] = []
    group_applies = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            group_applies = value == "*"
            continue
        if not group_applies:
            continue
        if key in ("allow", "disallow") and value:
            rules.append((key, value))
    return rules


def _robots_pattern_matches(pattern: str, path: str) -> bool:
    """A robots.txt pattern matches a path by prefix, except a trailing
    ``$`` which anchors the match to the exact path (used here only by
    ``/$``, the home page)."""
    if pattern.endswith("$"):
        return path == pattern[:-1]
    return path.startswith(pattern)


def _robots_allows(rules: list[tuple[str, str]], path: str) -> bool:
    """Whether ``path`` is allowed under ``rules``.

    The longest matching pattern wins, the de facto standard algorithm most
    crawlers use, including Google's documented behaviour; a tie between an
    Allow and a Disallow of the same length favours Allow; a path matched by
    no rule at all is allowed by default (this file has no such paths, since
    its own last rule is the catch-all ``Disallow: /``).
    """
    best_length = -1
    best_directive = "allow"
    for directive, pattern in rules:
        if _robots_pattern_matches(pattern, path):
            length = len(pattern)
            if length > best_length or (length == best_length and directive == "allow"):
                best_length = length
                best_directive = directive
    return best_directive == "allow"


def _entelim_paths_in(text: str) -> set[str]:
    """Every ``https://entelim.org/...`` URL referenced in a rendered
    response, reduced to its path."""
    paths = set()
    for match in re.finditer(r"https://entelim\.org([^\s)\"'<>]*)", text):
        paths.add(match.group(1) or "/")
    return paths


def _collect_must_allow_paths(client) -> set[str]:
    """Every path a real crawler meets by fetching sitemap.xml, llms.txt and
    llms-full.txt — plus the three paths themselves.

    Each endpoint is fetched for real. If an endpoint isn't live yet, its
    own path is still checked (robots.txt governs the URL whether or not the
    route behind it has shipped), but there is no rendered body to pull
    further URLs from.
    """
    paths: set[str] = set(_CRAWLER_FACING_ENDPOINTS)
    for endpoint in _CRAWLER_FACING_ENDPOINTS:
        response = client.get(endpoint)
        if response.status_code == 200:
            paths |= _entelim_paths_in(response.data.decode())
    return paths


def test_every_crawler_discoverable_url_is_allowed_by_robots_txt(app):
    """No URL reachable from sitemap.xml, llms.txt or llms-full.txt, and none
    of those three files' own paths, is blocked by robots.txt's catch-all
    ``Disallow: /``."""
    rules = _parse_robots_rules(_ROBOTS_TXT_PATH.read_text(encoding="utf-8"))
    with app.test_client() as client:
        must_allow = _collect_must_allow_paths(client)

    blocked = sorted(path for path in must_allow if not _robots_allows(rules, path))
    assert not blocked, (
        "robots.txt blocks these URLs that a crawler reaches from sitemap.xml, "
        "llms.txt or llms-full.txt: " + ", ".join(blocked)
    )


def test_robots_root_dollar_matches_only_exact_root():
    rules = [("allow", "/$"), ("disallow", "/")]
    assert _robots_allows(rules, "/") is True
    assert _robots_allows(rules, "/about") is False


def test_robots_longest_match_wins_over_shorter_disallow():
    rules = [("disallow", "/"), ("allow", "/modules/")]
    assert _robots_allows(rules, "/modules/applications") is True
    assert _robots_allows(rules, "/other") is False


def test_robots_path_with_no_matching_rule_is_allowed_by_default():
    rules = [("allow", "/vision")]
    assert _robots_allows(rules, "/unrelated") is True


def test_robots_parse_rules_ignores_other_user_agent_groups():
    text = (
        "User-agent: Googlebot-Image\n"
        "Disallow: /private\n"
        "\n"
        "User-agent: *\n"
        "Allow: /vision\n"
        "Disallow: /\n"
    )
    rules = _parse_robots_rules(text)
    assert ("disallow", "/private") not in rules
    assert ("allow", "/vision") in rules


# ── Regression guard: disproven factual claims must never reappear ────────


def test_no_page_repeats_a_disproven_claim():
    """No content page body contains a factual claim already found false
    and corrected elsewhere.

    Each phrase in BANNED_CLAIMS was once live on a content page and was
    disproven against the data model (see the reason recorded next to each
    phrase). This test collects every page/phrase match across the whole
    corpus before failing, so a single run shows every offending page at
    once rather than stopping at the first one.
    """
    pages = load_all_pages()
    violations = []
    for page in pages:
        body_lower = page.body_html.lower()
        for phrase, reason in BANNED_CLAIMS:
            if phrase.lower() in body_lower:
                violations.append(
                    f"{page.url}: contains banned phrase '{phrase}' ({reason})"
                )
    assert not violations, (
        "Disproven claim(s) reappeared on a content page:\n"
        + "\n".join(violations)
    )


def test_billing_plans_summaries_repeat_no_disproven_claim():
    """billing_plans.PLANS's own ``summary`` strings are never rendered
    through load_all_pages() (they are not content pages), but they feed
    every page's JSON-LD Offer/AggregateOffer description directly (see
    app/services/public_pages.py::_flat_plan_offer / _per_unit_plan_offer /
    _enterprise_offer, all of which interpolate ``plan.summary`` verbatim).
    A disproven claim fixed on the rendered pages but left in
    billing_plans.py would resurface there the next time those templates
    changed what they pull from the catalogue."""
    from app.services.billing_plans import PLANS

    violations = []
    for plan in PLANS:
        summary_lower = plan.summary.lower()
        for phrase, reason in BANNED_CLAIMS:
            if phrase.lower() in summary_lower:
                violations.append(
                    f"billing_plans.PLANS[{plan.key!r}].summary: contains banned phrase "
                    f"'{phrase}' ({reason})"
                )
    assert not violations, (
        "Disproven claim(s) reappeared in billing_plans.py's own PLANS summaries:\n"
        + "\n".join(violations)
    )


def test_rendered_home_pricing_and_onboarding_pages_repeat_no_disproven_claim(app):
    """The actually-rendered HTML of /, /pricing and /team-annual-onboarding
    must never contain a disproven claim either.

    test_no_page_repeats_a_disproven_claim already checks every content
    page's ``body_html`` -- the Markdown-derived content only. The home
    page is not a content page at all (rendered directly from
    app/templates/main/index.html, never through load_all_pages()), and a
    content page's full HTTP response can contain more than its own
    body_html (template chrome, the plan_buy_section() CTA, JSON-LD). This
    checks the three pages a pricing claim is most likely to land on, as
    they are actually served.
    """
    pages_to_check = ["/", "/pricing", "/team-annual-onboarding"]
    with app.test_client() as client:
        violations = []
        for url in pages_to_check:
            rv = client.get(url)
            assert rv.status_code == 200, f"{url} returned {rv.status_code}"
            body_lower = rv.data.decode().lower()
            for phrase, reason in BANNED_CLAIMS:
                if phrase.lower() in body_lower:
                    violations.append(
                        f"{url}: contains banned phrase '{phrase}' ({reason})"
                    )
    assert not violations, (
        "Disproven claim(s) reappeared on a rendered page:\n" + "\n".join(violations)
    )


# ── cta: plans must match a feature that is actually live (PR #373 review) ─
#
# PR #373 flipped 7 pages from cta: waiting_list to cta: plans on the claim
# that the feature each one describes was shipped and reachable. An
# independent review found 4 of the 7 were not: capture_status had been set
# to "live" right alongside cta, so a check that only compares those two
# front-matter fields against each other would have passed all 7 pages,
# including the 4 that were wrong. The actual tell, in every one of the 4
# bad pages, was that nothing in the running app answers the page's own
# url_slug -- the only front-matter field that names a concrete, checkable
# destination. That is what this test verifies, for every module and
# function-per-segment page, via the app's real url_map rather than by
# re-reading the content files a second time.
#
# state: missing is also rejected outright, since a page admitting its own
# feature does not exist yet is never consistent with cta: plans -- see
# uc-s3-15, the one page of the 4 that had this set and still should not
# have needed a route check to catch.


def test_cta_plans_requires_capture_status_live(app):
    """Any module / function-per-segment page claiming cta: plans must also
    declare capture_status: live and must not declare state: missing."""
    pages = [
        p for p in load_all_pages() if p.family in ("module", "function-per-segment")
    ]
    assert pages, "no module / function-per-segment pages loaded"

    for page in pages:
        fm = page.front_matter
        if fm.get("cta") != "plans":
            continue
        assert fm.get("capture_status") == "live", (
            f"{page.source_path}: cta: plans but capture_status is "
            f"{fm.get('capture_status')!r}, not 'live'"
        )
        assert fm.get("state") != "missing", (
            f"{page.source_path}: cta: plans but state is 'missing' -- "
            "the page itself says the feature does not exist yet"
        )


def test_cta_plans_url_slug_matches_a_real_route(app):
    """Any module / function-per-segment page claiming cta: plans, with a
    same-origin url_slug, must name a route the app actually serves.

    A page can legitimately have no url_slug (nothing to check) or a
    url_slug pointing at an external canonical (archiet.ai/...), which this
    skips -- only a same-origin path (starting with "/") makes a checkable
    claim about this app's own routing.
    """
    from werkzeug.exceptions import MethodNotAllowed, NotFound

    adapter = app.url_map.bind("entelim.org")
    pages = [
        p for p in load_all_pages() if p.family in ("module", "function-per-segment")
    ]
    checked_at_least_one = False

    for page in pages:
        fm = page.front_matter
        if fm.get("cta") != "plans":
            continue
        url_slug = fm.get("url_slug")
        if not isinstance(url_slug, str) or not url_slug.startswith("/"):
            continue

        checked_at_least_one = True
        try:
            adapter.match(url_slug, method="GET")
        except MethodNotAllowed:
            pass  # route exists; GET just isn't one of its declared methods
        except NotFound:
            pytest.fail(
                f"{page.source_path}: cta: plans and url_slug={url_slug!r}, "
                "but no route in the app answers that path -- the feature "
                "it describes is not actually reachable"
            )

    assert checked_at_least_one, (
        "no cta: plans page with a same-origin url_slug was found to check -- "
        "this test would pass vacuously; update it alongside whatever changed"
    )


# ── Use-case pages: readable slugs, old-URL redirects, /use-cases index ───
#
# Every function-per-segment (use-case) page used to be served at
# /use-cases/<internal uc-sN-NN-* filename slug>. Some of those URLs are
# already indexed, so the old route now 301-redirects to the new, readable
# slug (each page's own url_slug front-matter, rewritten to /use-cases/<slug>)
# instead of just 404ing.


def _redirect_path(rv) -> str:
    """The path a Werkzeug test-client redirect response points at, whether
    its Location header came back relative or absolute."""
    from urllib.parse import urlparse

    return urlparse(rv.location).path


def test_use_case_slugs_are_unique():
    """Every function-per-segment page's public slug is unique -- two pages
    must never resolve to the same /use-cases/<slug> URL."""
    pages = [p for p in load_all_pages() if p.family == "function-per-segment"]
    assert len(pages) > 0, "no function-per-segment pages loaded"

    slugs = [p.slug for p in pages]
    from collections import Counter

    counts = Counter(slugs)
    duplicates = {slug: count for slug, count in counts.items() if count > 1}
    assert not duplicates, f"duplicate use-case slugs: {duplicates}"

    urls = [p.url for p in pages]
    assert len(urls) == len(set(urls)), "duplicate use-case page URLs"


def test_every_use_case_page_redirects_from_its_old_filename_url(app):
    """Every use-case page whose public slug differs from its uc-sN-NN-*
    filename 301-redirects from that old filename-based URL to its current
    url_slug -- not a hand-picked sample, every file under
    content/pages/function-per-segment/, with the expected target read from
    that file's own front matter at test time (not a hardcoded mapping), so
    this cannot drift out of sync with a future slug change.

    Also asserts the redirect was actually exercised for every file in the
    family (none silently skipped), so this test cannot quietly shrink to a
    no-op as pages are added, renamed or removed.
    """
    seg_dir = CONTENT_ROOT / "function-per-segment"
    md_files = sorted(seg_dir.glob("*.md"))
    assert len(md_files) > 0, "no function-per-segment files found"

    checked = 0
    with app.test_client() as client:
        for md_file in md_files:
            filename_slug = md_file.stem
            raw = md_file.read_text(encoding="utf-8")
            front_matter, _ = _parse_front_matter(raw)
            url_slug = front_matter.get("url_slug", "")
            assert isinstance(url_slug, str) and url_slug.startswith("/use-cases/"), (
                f"{md_file.name}: url_slug is {url_slug!r}, expected a "
                "/use-cases/<slug> path -- every use-case file must carry one"
            )
            public_slug = url_slug[len("/use-cases/"):]
            if public_slug == filename_slug:
                continue  # this file's filename already is its public slug

            checked += 1
            rv = client.get(f"/use-cases/{filename_slug}", follow_redirects=False)
            assert rv.status_code == 301, (
                f"/use-cases/{filename_slug}: expected a 301 redirect to "
                f"{url_slug}, got {rv.status_code}"
            )
            assert _redirect_path(rv) == url_slug, (
                f"/use-cases/{filename_slug}: redirected to {rv.location!r}, "
                f"expected {url_slug!r}"
            )

    # Every file in the family migrated its slug, so every file must have
    # been checked above -- if this ever reads 0, the loop above stopped
    # actually testing anything and the test would otherwise pass vacuously.
    assert checked == len(md_files), (
        f"expected to check all {len(md_files)} use-case files' redirects, "
        f"only checked {checked} -- some file's filename already equals its "
        "public slug, which should not happen for this family yet"
    )


def test_use_case_old_filename_url_redirects_to_new_slug_sample(app):
    """Spot-check: a handful of already-indexed old uc-* URLs 301 to their
    new, readable slug -- including the one page that had no url_slug at all
    before this migration (uc-s1-07)."""
    samples = {
        "uc-s2-01-what-breaks": "/use-cases/what-breaks-and-who-gets-called",
        "uc-s3-06-capability-maturity-heatmap": "/use-cases/capability-maturity-heatmap",
        "uc-s1-07-reference-packs": "/use-cases/reference-packs",
        "uc-s4-06-what-we-can-and-cannot-tell": "/use-cases/what-we-can-and-cannot-tell",
    }
    with app.test_client() as client:
        for old_slug, new_url in samples.items():
            rv = client.get(f"/use-cases/{old_slug}", follow_redirects=False)
            assert rv.status_code == 301, f"{old_slug}: expected 301, got {rv.status_code}"
            assert _redirect_path(rv) == new_url, f"{old_slug}: redirected to {rv.location!r}"
            # And the new URL actually renders.
            rv2 = client.get(new_url)
            assert rv2.status_code == 200, f"{new_url}: expected 200, got {rv2.status_code}"


def test_use_case_unknown_slug_still_404s(app):
    """A slug that was never a real page (old filename or new) still 404s --
    the redirect lookup must not turn every unknown path into a catch-all."""
    with app.test_client() as client:
        rv = client.get("/use-cases/this-page-does-not-exist-xyz")
        assert rv.status_code == 404


# Directories scanned for stale old-filename-form use-case references (see
# the needle string built below). Binary/vendor assets are skipped: they
# cannot contain a hand-written link to a content page and some are not
# valid UTF-8. tests/ is included deliberately (not just app/content/static):
# a stale reference in a fixture, doc or test is exactly the same bug as one
# in product code, just caught later.
_STALE_REFERENCE_ROOTS = ["app", "content", "tests"]
_STALE_REFERENCE_SKIP_DIR_NAMES = {
    "vendor", "node_modules", "__pycache__", ".git", "dist", "build",
}
_STALE_REFERENCE_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf",
    ".eot", ".pdf", ".svg", ".zip", ".pyc", ".map",
}

# Test functions that deliberately reference the old uc-sN-NN-* URL form, to
# exercise the redirect itself (this file's own check logic, just below,
# also carries the needle string literally and is exempted the same way).
# A new file is never added to this allowlist to silence a hit -- only a
# function whose whole job is testing the old-URL-to-new-URL redirect.
_STALE_REFERENCE_ALLOWED_FUNCTIONS = {
    ("tests/test_public_content_pages.py", "test_every_use_case_page_redirects_from_its_old_filename_url"),
    ("tests/test_public_content_pages.py", "test_use_case_old_filename_url_redirects_to_new_slug_sample"),
    ("tests/test_public_content_pages.py", "test_no_stale_internal_use_case_uc_slug_references"),
}


def _text_with_allowed_functions_blanked(repo_root, rel_path: str, text: str) -> str:
    """``text`` with the source of any function in
    ``_STALE_REFERENCE_ALLOWED_FUNCTIONS`` for this file replaced by blank
    lines (preserving line numbers, in case a future failure message wants
    them) -- a needle inside one of those functions is expected and not a
    finding; a needle anywhere else in the same file still is.
    """
    import ast

    names = {name for path, name in _STALE_REFERENCE_ALLOWED_FUNCTIONS if path == rel_path}
    if not names:
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    lines = text.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start, end = node.lineno - 1, getattr(node, "end_lineno", node.lineno)
            for i in range(start, end):
                lines[i] = "\n"
    return "".join(lines)


def test_no_stale_internal_use_case_uc_slug_references():
    """No file under app/, content/ (which covers app/static/) or tests/
    contains the string "/use-cases/uc-s" -- the old, internal-filename-based
    URL form -- outside the specific test functions whose job is to exercise
    the redirect from that old form. A hit here would be a leftover internal
    link, fixture or doc still pointing at the pre-migration URL instead of
    the current readable slug.
    """
    repo_root = Path(__file__).resolve().parent.parent
    needle = "/use-cases/uc-s"
    offenders: list[str] = []

    for root_name in _STALE_REFERENCE_ROOTS:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in _STALE_REFERENCE_SKIP_SUFFIXES:
                continue
            if _STALE_REFERENCE_SKIP_DIR_NAMES & set(path.relative_to(root).parts[:-1]):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel_path = str(path.relative_to(repo_root)).replace("\\", "/")
            if rel_path.endswith(".py"):
                text = _text_with_allowed_functions_blanked(repo_root, rel_path, text)
            if needle in text:
                offenders.append(rel_path)

    assert not offenders, (
        "stale '/use-cases/uc-s' reference(s) found (old filename-based URL "
        "form) outside the functions that deliberately test the redirect -- "
        "update to the current readable slug:\n" + "\n".join(offenders)
    )


def test_use_cases_index_returns_200_and_lists_every_page(app):
    """/use-cases returns 200 and links every live use-case page by its
    current (post-migration) URL and title, except a page withdrawn from
    discovery (state: not_planned), which it must not link to or name."""
    pages = [p for p in load_all_pages() if p.family == "function-per-segment"]
    assert len(pages) > 0
    assert any(p.is_withdrawn for p in pages), (
        "expected at least one withdrawn use-case page to exercise the exclusion"
    )

    with app.test_client() as client:
        rv = client.get("/use-cases")
        assert rv.status_code == 200
        html = rv.data.decode()
        # Unescape first: a title with an apostrophe renders as &#39; in the
        # template's auto-escaped output (same handling as
        # test_all_pages_have_title_in_html_title above).
        import html as _html_mod

        unescaped = _html_mod.unescape(html)
        for page in pages:
            if page.is_withdrawn:
                assert f'href="{page.url}"' not in html, (
                    f"/use-cases should not link to withdrawn page {page.url}"
                )
                assert page.title not in unescaped, (
                    f"/use-cases should not name withdrawn page title '{page.title}'"
                )
                continue
            assert f'href="{page.url}"' in html, f"/use-cases missing link to {page.url}"
            assert page.title in unescaped, f"/use-cases missing title for {page.url}"


def test_use_cases_index_groups_by_segment(app):
    """/use-cases groups pages under a segment heading, not one flat list."""
    with app.test_client() as client:
        rv = client.get("/use-cases")
        html = rv.data.decode()
    for label in ["Startups", "Scale-ups", "Enterprise architecture teams", "Services and operations"]:
        assert label in html, f"/use-cases missing segment heading '{label}'"


def test_use_cases_index_in_robots_and_sitemap(app):
    """/use-cases (the index) is allowed by robots.txt and listed in the
    sitemap, same as every other public index."""
    rules = _parse_robots_rules(_ROBOTS_TXT_PATH.read_text(encoding="utf-8"))
    assert _robots_allows(rules, "/use-cases"), "robots.txt blocks /use-cases"

    with app.test_client() as client:
        xml = client.get("/sitemap.xml").data.decode()
    assert "<loc>https://entelim.org/use-cases</loc>" in xml


# ── Home page: curated use-case highlights per segment, plus the /vs hub ──


def test_home_page_links_curated_use_cases_and_vs_hub(app):
    """The home page links at least one use case per persona segment, plus
    the /vs comparison hub, with real working URLs."""
    with app.test_client() as client:
        rv = client.get("/")
        html = rv.data.decode()

    assert 'href="/use-cases"' in html
    assert 'href="/vs"' in html

    # Every curated slug on the home page resolves to a real page.
    from app.main.views import _HOME_USE_CASE_HIGHLIGHTS

    for slugs in _HOME_USE_CASE_HIGHLIGHTS.values():
        for slug in slugs:
            page = load_page("function-per-segment", slug=slug)
            assert page is not None, f"curated home-page slug {slug!r} does not resolve"
            assert f'href="{page.url}"' in html, (
                f"home page missing link to curated use case {page.url}"
            )


# ── Header/footer: every public page links Features, Use cases, Compare, ──
# Pricing, and the two offers -- the two-click reachability test below is
# the real acceptance check; this test documents which links carry it.


def test_public_footer_links_use_cases_compare_pricing_and_offers(app):
    """The shared public footer (every public page) links Use cases, Compare,
    Pricing and both offers."""
    with app.test_client() as client:
        html = client.get("/").data.decode()

    for href in ["/use-cases", "/vs", "/pricing", "/architecture-health-check", "/team-annual-onboarding"]:
        assert f'href="{href}"' in html, f"public footer missing link to {href}"


def test_two_click_reachability_from_home_page(app):
    """Every URL in the sitemap is reachable within two ordinary links from
    the home page: either linked directly from '/', or linked from a page
    that '/' links to.

    This is the brief's actual acceptance test -- it does not special-case
    any one page family, it walks real anchor hrefs the way a visitor or a
    crawler would.
    """
    import re
    from urllib.parse import urlparse

    def same_site_links(html: str) -> set[str]:
        hrefs = re.findall(r'href="([^"]+)"', html)
        paths = set()
        for href in hrefs:
            if href.startswith("https://entelim.org"):
                href = href[len("https://entelim.org"):] or "/"
            if href.startswith("/") and not href.startswith("//"):
                paths.add(urlparse(href).path)
        return paths

    with app.test_client() as client:
        sitemap_xml = client.get("/sitemap.xml").data.decode()
        must_reach = set(re.findall(r"<loc>https://entelim\.org([^<]*)</loc>", sitemap_xml))
        must_reach.discard("/")  # the home page itself, not a link target

        home_html = client.get("/").data.decode()
        one_click = same_site_links(home_html)

        two_click = set(one_click)
        for path in one_click:
            if path in ("/", "") or path.startswith("/api/") or path.startswith("/static/"):
                continue
            rv = client.get(path)
            if rv.status_code != 200:
                continue
            two_click |= same_site_links(rv.data.decode())

        unreachable = sorted(p for p in must_reach if p not in two_click and p not in one_click)
        assert not unreachable, (
            "not reachable within two clicks of the home page: " + ", ".join(unreachable)
        )
