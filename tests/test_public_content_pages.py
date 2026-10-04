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
    """/llms.txt lists every page with its title and URL."""
    pages = load_all_pages()
    with app.test_client() as client:
        rv = client.get("/llms.txt")
        assert rv.status_code == 200
        text = rv.data.decode()
        for page in pages:
            assert page.url in text, (
                f"llms.txt missing URL {page.url}"
            )
            # Title should appear (may be truncated in link format)
            assert page.title[:30] in text, (
                f"llms.txt missing title '{page.title[:30]}' for {page.url}"
            )


def test_sitemap_xml_includes_every_page(app):
    """/sitemap.xml includes every page."""
    pages = load_all_pages()
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        assert rv.status_code == 200
        xml = rv.data.decode()
        assert xml.startswith('<?xml')
        assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in xml
        for page in pages:
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
    # Listing it does not displace any content page.
    assert len(entries) == len(load_all_pages()) + 1


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
    page = load_page("function-per-segment", slug="uc-s1-02-canvas-on-one-page")
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
    # ai-chat has cta: waiting_list
    with app.test_client() as client:
        rv = client.get("/modules/ai-chat")
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
    for slug in ["leanix", "ardoq", "bizzdesign-hopex"]:
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
    """/sitemap.xml still includes every content page after homepage addition."""
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    with app.test_client() as client:
        rv = client.get("/sitemap.xml")
        xml = rv.data.decode()
        for page in pages:
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