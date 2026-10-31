"""Module, use-case and comparison pages render as landing pages.

Covers app/services/public_pages.py ``build_landing()`` (splitting a sanitized
body into hero lead, intro and sections; link lists to cards; icons) and the
landing branch of public/page.html (public/_partials/_landing.html): hero with
icon, breadcrumb, H1, lead and two buttons; section bands; card grids; the
closing action band. Every other page family keeps the document layout.

B-03 (use-case question/answer layout, MP-B2): a use-case page's "The
question" section renders as a callout instead of a plain prose
band, and its "What Entelim answers" section renders beside the page's own
screenshot/recording when one exists (text-only, unchanged from the default
band, when none does yet) -- see the ``variant``-specific tests below and
``USE_CASE_ICONS`` for the per-use-case-file icon front matter that gives
every use-case "Related" card its own, distinct icon the same way
``MODULE_ICONS`` already does for modules.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from markupsafe import Markup

from app.services.public_pages import (
    CONTENT_ROOT,
    LANDING_FAMILIES,
    MERGED_PAGES,
    PublicPage,
    _sanitize_html,
    build_landing,
    load_all_pages,
    page_icon,
)

MODULE_ICONS = {
    "ai-chat": "message-square",
    "answer-sources": "search",
    "build-your-model-from-your-tools": "unplug",
    "scenario-modelling": "git-compare",
    "software-and-tool-discovery": "search",
    "advisers-for-every-role": "users",
    "predictive-checks": "lightbulb",
    "applications": "app-window",
    "arb": "gavel",
    "architecture-model": "boxes",
    "batch-import": "upload",
    "business-case": "calculator",
    "business-model-canvas": "layout-grid",
    "capability-maturity": "gauge",
    "compliance-frameworks": "shield-check",
    "diagrams": "network",
    "digital-twin": "radar",
    "duplicate-detection": "copy",
    "gap-analysis": "git-compare",
    "industry-apqc": "library",
    "integrations": "plug",
    "investment-analysis": "trending-up",
    "my-applications": "user-check",
    "org-chart": "users",
    "portfolio": "layers",
    "procurement": "shopping-cart",
    "projects": "folder-kanban",
    "rationalization": "shrink",
    "risk-register": "shield-alert",
    "roadmaps": "milestone",
    "solutions": "puzzle",
    "value-streams": "workflow",
    "vendors": "truck",
}

# Every use-case (function-per-segment) content file's own icon, keyed by its
# filename stem (the internal uc-sN-NN-* slug, not the public url_slug) the
# same way MODULE_ICONS above is keyed by a module file's stem. Each value is
# distinct from every other entry here AND from every MODULE_ICONS value
# (test_every_use_case_page_declares_a_known_icon asserts both), so a
# "Related" grid mixing module and use-case cards can never repeat an icon.
USE_CASE_ICONS = {
    "uc-s1-01-canvas-dependencies": "git-branch",
    "uc-s1-02-canvas-on-one-page": "layout-template",
    "uc-s1-03-lean-canvas": "clipboard-list",
    "uc-s1-04-revenue-stream-risk": "banknote",
    "uc-s1-05-show-what-we-run": "presentation",
    "uc-s1-06-single-point-of-failure": "zap-off",
    "uc-s1-07-reference-packs": "package",
    "uc-s1-08-website-first-look": "eye",
    "uc-s1-09-website-full-profile": "book-user",
    "uc-s2-01-what-breaks": "phone-call",
    "uc-s2-02-no-owner": "user-x",
    "uc-s2-03-duplicate-spend": "receipt",
    "uc-s2-04-connect-your-tools": "github",
    "uc-s2-05-one-system-two-tools": "shuffle",
    "uc-s2-06-whats-changed": "history",
    "uc-s2-07-twin-map": "map",
    "uc-s2-08-risk-blast-radius": "flame",
    "uc-s2-09-incident-feeds": "siren",
    "uc-s2-10-what-i-own": "briefcase",
    "uc-s2-11-connector-candidates": "cable",
    "uc-s3-01-import-your-model": "file-up",
    "uc-s3-02-leanix-ardoq-import": "arrow-left-right",
    "uc-s3-03-servicenow-crosswalk": "table-2",
    "uc-s3-04-value-streams-at-risk": "alert-triangle",
    "uc-s3-05-business-case-for-the-cio": "piggy-bank",
    "uc-s3-06-capability-maturity-heatmap": "thermometer",
    "uc-s3-07-review-board": "users-round",
    "uc-s3-08-risk-and-controls": "shield-off",
    "uc-s3-09-programme-tracking": "list-checks",
    "uc-s3-10-ai-assistant": "sparkles",
    "uc-s3-11-derivation-yield": "percent",
    "uc-s3-12-who-do-we-need": "user-plus",
    "uc-s3-13-target-state-roadmap": "flag",
    "uc-s3-14-adopt-a-reference-pack": "download-cloud",
    "uc-s3-15-see-your-own-twin": "scan",
    "uc-s4-01-what-happens-if": "server-off",
    "uc-s4-02-set-up-in-an-afternoon": "clock",
    "uc-s4-03-see-it-on-a-company-like-mine": "building-2",
    "uc-s4-04-key-person-risk": "user-cog",
    "uc-s4-05-contract-renewals": "calendar",
    "uc-s4-06-what-we-can-and-cannot-tell": "eye-off",
}

# The vendored Lucide bundle only ever exports an icon's PascalCase name (e.g.
# "DownloadCloud"); it never embeds a kebab-case form. createIcons() resolves a
# data-lucide/icon-front-matter value back to that PascalCase name with its own
# toPascalCase conversion -- (\w)(\w*)(_|-|\s*), replacing each chunk with its
# upper-cased head plus lower-cased rest and dropping the separator -- before
# indexing the icons object. A name that was never valid (e.g. "cloud-download",
# which Lucide calls "DownloadCloud") renders nothing at runtime, and checking
# only against USE_CASE_ICONS/MODULE_ICONS can never catch that, because those
# dicts are exactly where the invalid name was copied from. Mirror the real
# conversion and check against the bundle's own export map instead.
_LUCIDE_BUNDLE = CONTENT_ROOT.parent.parent / "app" / "static" / "js" / "vendor" / "lucide.min.js"


def _lucide_pascal_names() -> set[str]:
    source = _LUCIDE_BUNDLE.read_text(encoding="utf-8")
    start = source.index("{__proto__:null,")
    end = source.index("});", start)
    block = source[start : end + 1]
    names = {
        match.group(1)
        for match in re.finditer(r"([A-Za-z][A-Za-z0-9]*):([A-Za-z0-9_$]+)", block)
        if match.group(1) != "__proto__"
    }
    assert len(names) > 1000, "Lucide export map parse looks broken -- too few names found"
    return names


def _lucide_pascal_case(name: str) -> str:
    """Lucide's own kebab/snake/space -> PascalCase conversion (toPascalCase)."""

    def repl(match: re.Match) -> str:
        head, rest = match.group(1), match.group(2)
        return head.upper() + rest.lower()

    return re.sub(r"(\w)(\w*)(?:_|-|\s*)", repl, name)


def _page(body: str, family: str = "module", front_matter: dict | None = None) -> PublicPage:
    html = _sanitize_html(body)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", str(html), re.S)
    return PublicPage(
        family=family,
        slug="synthetic",
        url="/modules/synthetic",
        title=re.sub(r"<[^>]+>", "", title_match.group(1)) if title_match else "Untitled",
        body_html=html,
        front_matter=front_matter or {},
    )


def _landing(app, body: str, **kwargs):
    with app.test_request_context():
        return build_landing(_page(body, **kwargs))


def _fragments(landing):
    yield landing.lead_html
    yield landing.intro_html
    for section in landing.sections:
        yield section.heading_html
        yield section.body_html
        for card in section.cards or []:
            yield card.title_html


# ---------------------------------------------------------------------------
# build_landing(): splitting
# ---------------------------------------------------------------------------


def test_build_landing_splits_zero_one_and_several_h2(app):
    none = _landing(app, "<h1>Title</h1><p>Lead.</p><p>More intro.</p>")
    assert none.sections == []
    assert str(none.lead_html) == "Lead."
    assert str(none.intro_html) == "<p>More intro.</p>"

    one = _landing(app, "<h1>Title</h1><p>Lead.</p><h2>Only section</h2><p>Body.</p>")
    assert [s.id for s in one.sections] == ["only-section"]
    assert str(one.sections[0].heading_html) == "Only section"
    assert str(one.sections[0].body_html) == "<p>Body.</p>"
    assert one.intro_html is None

    four = _landing(
        app,
        "<h1>Title</h1><p>Lead.</p><p>Intro.</p>"
        "<h2>First</h2><p>a</p><h2>Second part</h2><p>b</p>"
        "<h2>Third, with punctuation!</h2><p>c</p><h2>Fourth</h2><ul><li>d</li></ul>",
    )
    assert [s.id for s in four.sections] == ["first", "second-part", "third-with-punctuation", "fourth"]
    assert [str(s.heading_html) for s in four.sections] == [
        "First", "Second part", "Third, with punctuation!", "Fourth",
    ]
    assert str(four.sections[3].body_html) == "<ul><li>d</li></ul>"
    for landing in (none, one, four):
        assert landing.title == "Title"
        for fragment in _fragments(landing):
            assert fragment is None or "<h1" not in str(fragment)


def test_build_landing_ignores_nested_h2(app):
    landing = _landing(
        app,
        "<h1>Title</h1><p>Lead.</p><h2>Outer</h2><p>before</p>"
        "<blockquote><h2>Quoted heading</h2><p>quoted</p></blockquote><p>after</p>",
    )
    assert [s.id for s in landing.sections] == ["outer"]
    body = str(landing.sections[0].body_html)
    assert "<blockquote><h2>Quoted heading</h2>" in body
    assert body.endswith("<p>after</p>")


def test_build_landing_lead_unwraps_em_and_strong(app):
    em = _landing(app, "<h1>T</h1><p><em>An <a href=\"/x\">italic</a> lead.</em></p>")
    assert str(em.lead_html) == 'An <a href="/x">italic</a> lead.'
    strong = _landing(app, "<h1>T</h1><p><strong>A bold lead.</strong></p>")
    assert str(strong.lead_html) == "A bold lead."
    # Mixed content is not unwrapped: only a paragraph whose only child is one em/strong.
    mixed = _landing(app, "<h1>T</h1><p><em>Partly</em> italic.</p>")
    assert str(mixed.lead_html) == "<em>Partly</em> italic."


def test_build_landing_no_lead_when_first_block_is_not_p(app):
    landing = _landing(app, "<h1>T</h1><ul><li>one</li></ul><p>Then text.</p><h2>S</h2><p>x</p>")
    assert landing.lead_html is None
    assert str(landing.intro_html) == "<ul><li>one</li></ul><p>Then text.</p>"
    straight_to_h2 = _landing(app, "<h1>T</h1><h2>S</h2><p>x</p>")
    assert straight_to_h2.lead_html is None
    assert straight_to_h2.intro_html is None


def test_section_ids_are_unique_and_keep_existing_ids(app):
    landing = _landing(
        app,
        '<h1>T</h1><p>Lead.</p><h2 id="custom-anchor">Named</h2><p>a</p>'
        "<h2>Repeat</h2><p>b</p><h2>Repeat</h2><p>c</p><h2>Repeat</h2><p>d</p>",
    )
    assert [s.id for s in landing.sections] == ["custom-anchor", "repeat", "repeat-2", "repeat-3"]


# ---------------------------------------------------------------------------
# build_landing(): B-03 use-case section variants (callout / figure_band)
# ---------------------------------------------------------------------------


def test_use_case_question_and_answer_headings_get_their_own_variant(app):
    landing = _landing(
        app,
        "<h1>T</h1><p>Lead.</p>"
        "<h2>The question</h2><p>Q body.</p>"
        "<h2>What Entelim answers</h2><p>A body.</p>"
        "<h2>Related</h2><p>Not a list, stays prose.</p>",
        family="function-per-segment",
    )
    assert [s.variant for s in landing.sections] == ["callout", "figure_band", "default"]


def test_use_case_variant_match_is_exact_and_case_insensitive(app):
    """Only the two known headings, matched on their own plain text (not a
    substring), switch a use-case section's variant -- a page whose second
    heading reads "What Entelim is building" (not yet live) or anything else
    keeps the default prose band, and matching ignores case/whitespace."""
    landing = _landing(
        app,
        "<h1>T</h1><p>Lead.</p>"
        "<h2>  THE QUESTION  </h2><p>Q body.</p>"
        "<h2>What Entelim is building</h2><p>Not live yet.</p>"
        "<h2>The question, sort of</h2><p>Not an exact match.</p>",
        family="function-per-segment",
    )
    assert [s.variant for s in landing.sections] == ["callout", "default", "default"]


def test_section_variants_only_apply_to_use_case_pages(app):
    """The same two headings on a module or comparison page never switch
    variant -- build_landing() gates on page.family, not the heading text
    alone."""
    for family in ("module", "comparison"):
        landing = _landing(
            app,
            "<h1>T</h1><p>Lead.</p><h2>The question</h2><p>a</p>"
            "<h2>What Entelim answers</h2><p>b</p>",
            family=family,
        )
        assert [s.variant for s in landing.sections] == ["default", "default"]


# ---------------------------------------------------------------------------
# build_landing(): link lists as cards
# ---------------------------------------------------------------------------


def test_link_list_section_becomes_cards(app):
    landing = _landing(
        app,
        "<h1>T</h1><p>Lead.</p><h2>Related modules</h2>"
        '<ul>\n<li><a href="/modules/arb">Architecture <em>Review</em> Board</a></li>\n'
        '<li> <a href="/somewhere-not-content">Elsewhere</a> </li>\n</ul>',
    )
    cards = landing.sections[0].cards
    assert cards is not None and len(cards) == 2
    assert cards[0].url == "/modules/arb"
    assert str(cards[0].title_html) == "Architecture <em>Review</em> Board"
    assert cards[1].url == "/somewhere-not-content"
    assert cards[1].icon == "link"
    assert cards[1].description is None


@pytest.mark.parametrize(
    "items",
    [
        '<li><a href="/modules/arb">Architecture Review Board</a>, the review hub.</li>',
        '<li><a href="https://example.com/">External</a></li>',
        '<li><a href="//example.com/">Protocol-relative</a></li>',
        '<li><a href="/modules/arb">One</a> <a href="/modules/portfolio">Two</a></li>',
        "<li>Plain text item</li>",
    ],
)
def test_mixed_list_stays_prose(app, items):
    body = (
        "<h1>T</h1><p>Lead.</p><h2>Related</h2><ul>"
        '<li><a href="/modules/portfolio">Portfolio</a></li>' + items + "</ul>"
    )
    section = _landing(app, body).sections[0]
    assert section.cards is None
    assert "<ul>" in str(section.body_html)


def test_list_with_text_beside_it_stays_prose(app):
    section = _landing(
        app,
        '<h1>T</h1><p>Lead.</p><h2>Related</h2><p>See also:</p><ul><li><a href="/modules/arb">ARB</a></li></ul>',
    ).sections[0]
    assert section.cards is None


def test_cards_use_target_description_and_icon(app):
    modules_dir = CONTENT_ROOT / "modules"
    temp_file = modules_dir / "zzz-test-temp-landing-target.md"
    try:
        temp_file.write_text(
            "---\npage_family: module\nstate: on_main\nicon: radar\n"
            "description: A temporary target page used by the landing card test.\n---\n\n"
            "# Temporary Target\n\nBody text.\n",
            encoding="utf-8",
        )
        landing = _landing(
            app,
            "<h1>T</h1><p>Lead.</p><h2>Related modules</h2><ul>"
            '<li><a href="/modules/zzz-test-temp-landing-target">Temporary Target</a></li>'
            '<li><a href="/modules/arb">Architecture Review Board</a></li>'
            "</ul>",
        )
        cards = landing.sections[0].cards
        assert cards is not None
        assert cards[0].description == "A temporary target page used by the landing card test."
        assert cards[0].icon == "radar"
        arb = next(p for p in load_all_pages() if p.url == "/modules/arb")
        assert cards[1].description == arb.description
        assert cards[1].icon == "gavel"
    finally:
        if temp_file.exists():
            temp_file.unlink()


def test_page_icon_rule():
    assert page_icon(_page("<h1>T</h1>", front_matter={"icon": "gavel"})) == "gavel"
    # Not a plain lowercase name: the family default instead.
    assert page_icon(_page("<h1>T</h1>", front_matter={"icon": "Gavel\" onclick=x"})) == "layout-grid"
    assert page_icon(_page("<h1>T</h1>", front_matter={"icon": ""})) == "layout-grid"
    assert page_icon(_page("<h1>T</h1>", family="function-per-segment")) == "help-circle"
    assert page_icon(_page("<h1>T</h1>", family="comparison")) == "scale"
    assert page_icon(_page("<h1>T</h1>", family="site")) == "link"


def test_fragments_are_sanitized_markup(app):
    landing = _landing(
        app,
        "<h1>T</h1><p onclick=\"steal()\"><em>Lead<script>alert(1)</script></em></p>"
        "<p>Intro <span onclick=\"x()\">text</span></p><script>alert(2)</script>"
        "<h2 onclick=\"y()\">Heading<script>alert(3)</script></h2><p onclick=\"z()\">Body</p>"
        "<h2>Cards</h2><ul><li><a href=\"/modules/arb\" onclick=\"w()\">ARB<script>alert(4)</script></a></li></ul>",
    )
    fragments = [f for f in _fragments(landing) if f is not None]
    assert len(fragments) >= 6
    for fragment in fragments:
        assert isinstance(fragment, Markup)
        assert "<script" not in str(fragment)
        assert "onclick" not in str(fragment)
    assert landing.sections[1].cards is not None


def test_other_families_get_no_landing(app):
    with app.test_request_context():
        for family in ("site", "legal", "vision", "dogfood"):
            assert build_landing(_page("<h1>T</h1><p>x</p><h2>S</h2>", family=family)) is None
        # Front matter cannot switch a page into the landing layout.
        page = _page("<h1>T</h1><p>x</p>", family="site", front_matter={"page_family": "module"})
        assert build_landing(page) is None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class _Outline(HTMLParser):
    """Records where the hero, h1s, primary buttons and lists sit."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, dict]] = []
        self.h1_count = 0
        self.hero_primary = 0
        self.card_links: list[str] = []
        self.list_links_in_bands: list[str] = []

    def _inside(self, predicate) -> bool:
        return any(predicate(tag, attrs) for tag, attrs in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "h1":
            self.h1_count += 1
        if tag == "a" and "public-btn" in classes and "public-btn--primary" in classes:
            if self._inside(lambda t, a: a.get("data-testid") == "landing-hero"):
                self.hero_primary += 1
        if tag == "a" and self._inside(lambda t, a: t == "ul" and a.get("role") == "list"):
            self.card_links.append(attrs.get("href", ""))
        if tag not in self.VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _landing_pages():
    return [
        p for p in load_all_pages()
        if p.family in LANDING_FAMILIES and p.url not in MERGED_PAGES
    ]


def test_every_landing_page_renders_hero_bands_and_closing(app):
    pages = _landing_pages()
    assert len(pages) >= 60
    with app.test_client() as client:
        for page in pages:
            rv = client.get(page.url)
            assert rv.status_code == 200, page.url
            html = rv.data.decode()
            main = re.search(r"<main\b.*?</main>", html, re.S)
            assert main, page.url
            outline = _Outline()
            outline.feed(main.group(0))
            assert outline.h1_count == 1, f"{page.url}: {outline.h1_count} <h1> elements"
            assert 'data-testid="landing-hero"' in html, page.url
            assert 'data-testid="landing-closing"' in html, page.url
            assert 'class="landing-bands"' in html, page.url
            assert outline.hero_primary >= 1, f"{page.url}: no primary button in the hero"
            with app.test_request_context():
                landing = build_landing(page)
            for section in landing.sections:
                if section.cards:
                    # Rendered as a card grid, not left as a bullet list of links.
                    assert str(section.body_html) not in main.group(0), (page.url, section.id)
                    for card in section.cards:
                        assert f'href="{card.url}" class="group flex' in main.group(0), (page.url, card.url)


def test_card_icons_follow_their_target_module(app):
    with app.test_client() as client:
        html = client.get("/modules/arb").data.decode()
    # Hero icon is the page's own, and a card to /modules/risk-register carries that module's icon.
    hero = html[html.index('data-testid="landing-hero"'):html.index('class="landing-bands"')]
    assert 'data-lucide="gavel"' in hero
    card = html[html.index('href="/modules/risk-register" class="group flex'):]
    card = card[:card.index("</a>")]
    assert 'data-lucide="shield-alert"' in card
    assert "Risk Register" in card


def test_use_case_card_icon_follows_its_own_front_matter(app):
    with app.test_client() as client:
        html = client.get("/use-cases/duplicate-software-spend").data.decode()
    hero = html[html.index('data-testid="landing-hero"'):html.index('class="landing-bands"')]
    assert 'data-lucide="receipt"' in hero
    card = html[html.index('href="/use-cases/what-breaks-and-who-gets-called" class="group flex'):]
    card = card[:card.index("</a>")]
    assert 'data-lucide="phone-call"' in card


def test_no_two_cards_in_the_same_grid_share_an_icon(app):
    """Acceptance criterion: no two cards in the same grid, on any module or
    use-case page, show the same icon -- checked against every live card
    grid on every module and use-case landing page, not only the two pages
    named in the brief."""
    with app.test_request_context():
        for page in _landing_pages():
            if page.family not in ("module", "function-per-segment"):
                continue
            landing = build_landing(page)
            for section in landing.sections:
                if not section.cards:
                    continue
                icons = [card.icon for card in section.cards]
                assert len(icons) == len(set(icons)), (page.url, section.id, icons)


@pytest.mark.parametrize(
    "url, list_tag, heading_text",
    [
        ("/vs/leanix", "<ul>", "What Entelim already does"),
        ("/modules/industry-apqc", "<ol>", "How it works in Entelim"),
    ],
)
def test_landing_prose_lists_keep_their_marker_class(app, url, list_tag, heading_text):
    """A section whose body stays prose (not a card grid) must render its
    `<ul>`/`<ol>` inside a wrapper that still carries `prose max-w-none` --
    that class is what supplies the Tailwind typography plugin's
    `list-style-type: disc`/`decimal`. Dropping it, as the landing wrappers
    once did, renders every bullet and numbered step with no marker even
    though the `<ul>`/`<ol>` markup itself is unchanged.
    """
    with app.test_client() as client:
        html = client.get(url).data.decode()
    assert heading_text in html, url
    list_index = html.index(list_tag, html.index(heading_text))
    wrapper_start = html.rfind('<div class="public-page-content', 0, list_index)
    assert wrapper_start != -1, (url, "no public-page-content wrapper before the list")
    wrapper_tag_end = html.index(">", wrapper_start)
    wrapper_open_tag = html[wrapper_start:wrapper_tag_end]
    classes = re.search(r'class="([^"]*)"', wrapper_open_tag).group(1).split()
    assert "prose" in classes, (url, wrapper_open_tag)
    assert "max-w-none" in classes, (url, wrapper_open_tag)


def test_landing_bands_all_carry_prose_on_their_content_wrapper(app):
    """Every `.public-page-content` wrapper inside a page's `landing-bands`
    (intro band and every section band) carries `prose max-w-none`, not
    only the ones a specific regression happened to be caught on.
    """
    with app.test_client() as client:
        html = client.get("/modules/industry-apqc").data.decode()
    bands = html[html.index('class="landing-bands"'):html.index('data-testid="landing-closing"')]
    wrapper_classes = re.findall(r'<div class="(public-page-content[^"]*)"', bands)
    assert len(wrapper_classes) >= 5, "expected an intro band plus several section bands"
    for class_attr in wrapper_classes:
        classes = class_attr.split()
        assert "prose" in classes, class_attr
        assert "max-w-none" in classes, class_attr


def test_pages_by_url_is_cached_across_calls(app, monkeypatch):
    """build_landing() must not re-run load_all_pages() -- markdown and
    bleach over every content file -- on every call. The cached
    `_pages_by_url()` map is keyed on a content-file mtime fingerprint, so
    two calls with no content change must reuse the same map and must not
    call load_all_pages() again.
    """
    from app.services import public_pages

    public_pages._pages_by_url_for_fingerprint.cache_clear()
    calls: list[int] = []
    real_load_all_pages = public_pages.load_all_pages

    def _counting_load_all_pages():
        calls.append(1)
        return real_load_all_pages()

    monkeypatch.setattr(public_pages, "load_all_pages", _counting_load_all_pages)
    with app.test_request_context():
        first = public_pages._pages_by_url()
        second = public_pages._pages_by_url()
    assert len(calls) == 1, "load_all_pages() ran again on an unchanged content tree"
    assert first is second


def test_landing_route_reuses_cached_pages_by_url_across_requests(app, monkeypatch):
    """The actual regression: every request to a module/use-case/comparison
    page whose sections contain a link-list pays for a full content reload.
    Three requests across two pages must load all content files at most
    once, not once per request.
    """
    from app.services import public_pages

    public_pages._pages_by_url_for_fingerprint.cache_clear()
    calls: list[int] = []
    real_load_all_pages = public_pages.load_all_pages

    def _counting_load_all_pages():
        calls.append(1)
        return real_load_all_pages()

    monkeypatch.setattr(public_pages, "load_all_pages", _counting_load_all_pages)
    with app.test_client() as client:
        assert client.get("/modules/industry-apqc").status_code == 200
        assert client.get("/modules/industry-apqc").status_code == 200
        assert client.get("/vs/leanix").status_code == 200
    assert len(calls) <= 1, f"load_all_pages() ran {len(calls)} times across 3 requests, expected at most 1"


def test_waiting_list_page_hero_points_at_the_form(app):
    page = next(p for p in _landing_pages() if p.cta == "waiting_list")
    with app.test_client() as client:
        html = client.get(page.url).data.decode()
    hero = html[html.index('data-testid="landing-hero"'):html.index('class="landing-bands"')]
    assert re.search(r'<a href="#feature-interest" class="public-btn public-btn--primary">Tell us you need this</a>', hero)
    assert 'id="feature-interest"' in html
    assert 'data-testid="feature-interest-form"' in html
    assert "Start free" not in hero


def test_live_page_hero_offers_start_free(app):
    with app.test_client() as client:
        html = client.get("/modules/arb").data.decode()
    hero = html[html.index('data-testid="landing-hero"'):html.index('class="landing-bands"')]
    assert re.search(r'<a href="/account/register" class="public-btn public-btn--primary">Start free</a>', hero)
    assert re.search(r'class="public-btn public-btn--outline">See pricing</a>', hero)
    nav = hero[hero.index('aria-label="Breadcrumb"'):hero.index("</nav>")]
    assert re.search(r'<a href="/features" class="[^"]*rounded-full[^"]*border-border[^"]*px-4 py-1.5[^"]*">', nav)
    assert hero.index("</nav>") < hero.index("<h1")
    closing = html[html.index('data-testid="landing-closing"'):]
    assert "Import your model and get a free, cited answer to what breaks if a system fails, in minutes." in closing
    assert 'href="/contact"' in closing
    assert re.search(r'class="public-btn public-btn--outline">Get in touch</a>', closing)
    assert "text-center" in html[html.index('data-testid="landing-closing"') - 10:][:140]
    assert "bg-primary" not in html[html.index('data-testid="landing-closing"') - 10:][:140]


def test_use_case_hero_names_its_segment(app):
    page = next(
        p for p in _landing_pages()
        if p.family == "function-per-segment" and p.front_matter.get("segment_id") == "S2"
    )
    with app.test_client() as client:
        html = client.get(page.url).data.decode()
    hero = html[html.index('data-testid="landing-hero"'):html.index('class="landing-bands"')]
    assert 'href="/use-cases"' in hero
    assert "/ Scale-ups" in hero


# ---------------------------------------------------------------------------
# B-03 rendering: "The question" callout and the "What Entelim answers" band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, question_text",
    [
        (
            "/use-cases/duplicate-software-spend",
            "A SaaS spend review finds three tools doing the same job",
        ),
        (
            "/use-cases/what-breaks-and-who-gets-called",
            "The first ten minutes are spent figuring out who owns the failing component",
        ),
    ],
)
def test_use_case_question_renders_as_a_callout_card(app, url, question_text):
    with app.test_client() as client:
        html = client.get(url).data.decode()
    assert 'data-testid="usecase-question-callout"' in html
    marker = html.index('data-testid="usecase-question-callout"')
    open_tag_start = html.rindex("<div", 0, marker)
    open_tag = html[open_tag_start:html.index(">", open_tag_start) + 1]
    assert 'class="public-callout"' in open_tag
    assert "border-l-4" not in open_tag
    callout = html[open_tag_start:html.index("</div>", marker)]
    assert question_text in callout
    # The callout sits inside the "The question" section band, after its own heading.
    assert "<h2" in html[html.index('aria-labelledby="the-question"'):open_tag_start]


def test_use_case_answer_band_is_text_only_when_no_figure_exists_yet(app):
    """Neither target page has a captured screenshot or recording yet (both
    USE_CASE_SCREENSHOT_CAPTURES and USE_CASE_VIDEO_CAPTURES are empty as of
    this change), so "What Entelim answers" must render its real text with no
    figure band and no broken or placeholder image."""
    with app.test_client() as client:
        html = client.get("/use-cases/duplicate-software-spend").data.decode()
    assert 'data-testid="usecase-answer-figure-band"' not in html
    assert 'data-testid="page-screenshot"' not in html
    assert 'data-testid="page-recording"' not in html
    assert "Find what you&#39;re paying for twice" in html or "Find what you're paying for twice" in html


def test_use_case_answer_band_shows_the_figure_beside_the_text_when_one_exists(app, monkeypatch):
    """Once a use-case page does have a captured screenshot, "What Entelim
    answers" renders it in a side-by-side (5/7) band -- the B-02 figure-slot
    shape -- instead of the page-top media slot module pages use, and the
    figure is not duplicated in both places."""
    from app.services import public_pages

    fake_screenshot = {
        "url": "/static/img/use-cases/duplicate-software-spend.webp",
        "width": 1200,
        "height": 800,
        "mobile": None,
        "alt": "Synthetic screenshot for this test only.",
        "caption": "Synthetic caption for this test only.",
    }

    def _fake_get_page_screenshot(page):
        return fake_screenshot if page.url == "/use-cases/duplicate-software-spend" else None

    monkeypatch.setattr(public_pages, "get_page_screenshot", _fake_get_page_screenshot)
    with app.test_client() as client:
        html = client.get("/use-cases/duplicate-software-spend").data.decode()

    assert 'data-testid="usecase-answer-figure-band"' in html
    band_start = html.index('data-testid="usecase-answer-figure-band"')
    band = html[band_start:html.index("data-testid=\"landing-closing\"")]
    assert 'data-testid="page-screenshot"' in band
    assert "lg:col-span-5" in band
    assert "lg:col-span-7" in band
    assert "Synthetic caption for this test only." in band
    # Not duplicated at the top of the page, before the section loop.
    assert 'data-testid="page-screenshot"' not in html[:band_start]


def test_use_case_answer_figure_still_renders_without_a_figure_band_section(app, monkeypatch):
    """D-3 regression: a use-case page whose second heading is not "What
    Entelim answers" (this one reads "What Entelim is building" instead) has
    no figure_band section to show a screenshot in. The page-top media slot
    must not be unconditionally suppressed for every function-per-segment
    page -- only for the ones that actually have a figure_band to show the
    figure in instead -- or a captured screenshot would render nowhere at
    all."""
    from app.services import public_pages

    url = "/use-cases/capability-maturity-heatmap"
    fake_screenshot = {
        "url": "/static/img/use-cases/capability-maturity-heatmap.webp",
        "width": 1200,
        "height": 800,
        "mobile": None,
        "alt": "Synthetic screenshot for this test only.",
        "caption": "Synthetic caption for this test only.",
    }

    def _fake_get_page_screenshot(page):
        return fake_screenshot if page.url == url else None

    monkeypatch.setattr(public_pages, "get_page_screenshot", _fake_get_page_screenshot)
    with app.test_client() as client:
        html = client.get(url).data.decode()

    # This page has no "What Entelim answers" heading, so no figure_band section exists.
    assert 'data-testid="usecase-answer-figure-band"' not in html
    # The figure must still render -- in the page-top slot, since there is no band for it.
    assert 'data-testid="page-screenshot"' in html
    assert "Synthetic caption for this test only." in html


def test_every_module_page_declares_a_known_icon():
    module_dir = CONTENT_ROOT / "modules"
    files = sorted(module_dir.glob("*.md"))
    assert len(files) == 33
    known = set(MODULE_ICONS.values())
    for md_file in files:
        front = md_file.read_text(encoding="utf-8").split("---", 2)[1]
        icons = re.findall(r"^icon:\s*(\S+)\s*$", front, re.M)
        assert len(icons) == 1, md_file.name
        assert icons[0] in known, (md_file.name, icons[0])
        assert icons[0] == MODULE_ICONS[md_file.stem], md_file.name


def test_every_use_case_page_declares_a_known_icon():
    use_case_dir = CONTENT_ROOT / "function-per-segment"
    files = sorted(use_case_dir.glob("*.md"))
    assert len(files) == 41
    assert {f.stem for f in files} == set(USE_CASE_ICONS)
    known = set(USE_CASE_ICONS.values())
    assert len(known) == len(USE_CASE_ICONS), "USE_CASE_ICONS has a repeated icon value"
    assert known.isdisjoint(set(MODULE_ICONS.values())), (
        "a use-case icon repeats a module icon -- a Related grid mixing the two would collide"
    )
    valid_lucide_names = _lucide_pascal_names()
    for md_file in files:
        front = md_file.read_text(encoding="utf-8").split("---", 2)[1]
        icons = re.findall(r"^icon:\s*(\S+)\s*$", front, re.M)
        assert len(icons) == 1, md_file.name
        assert icons[0] in known, (md_file.name, icons[0])
        assert icons[0] == USE_CASE_ICONS[md_file.stem], md_file.name
        assert _lucide_pascal_case(icons[0]) in valid_lucide_names, (
            md_file.name, icons[0], "not a real Lucide 0.344 icon name -- it would render blank"
        )
        assert page_icon(PublicPage(
            family="function-per-segment", slug=md_file.stem, url="/use-cases/x",
            title="x", body_html=Markup(""), front_matter={"icon": icons[0]},
        )) == icons[0], md_file.name


@pytest.mark.parametrize("url", ["/terms", "/privacy", "/about", "/pricing", "/features"])
def test_document_layout_unchanged_for_other_families(app, url):
    with app.test_client() as client:
        rv = client.get(url)
    assert rv.status_code == 200, url
    main = re.search(r"<main\b.*?</main>", rv.data.decode(), re.S).group(0)
    assert "landing-hero" not in main
    assert "landing-closing" not in main
    assert "landing-bands" not in main
    if url == "/pricing":
        # /pricing renders its own template (public/pricing.html), which has
        # never carried the document <article>; it must simply stay untouched.
        assert 'data-testid="pricing-plans"' in main
    else:
        assert '<article class="public-page-content' in main


ROLE_SLUGS = ("enterprise-architects", "cto-and-cio", "it-portfolio-managers", "solution-architects")


def test_home_and_role_pages_open_with_the_one_shared_hero(app):
    """The home page and every role page render their opening through
    public/_partials/_hero.html: the same grid, frame and figure markup, the role's own
    captured screen in the frame, and exactly one primary action."""
    with app.test_client() as client:
        home = client.get("/").data.decode()
        assert 'data-testid="home-hero"' in home and "home-frame__img" in home
        for slug in ROLE_SLUGS:
            html = client.get(f"/roles/{slug}").data.decode()
            hero = html[html.index('data-testid="landing-hero"'): html.index('data-testid="landing-hero-figure"')]
            assert "home-display" in html and "home-frame" in html, slug
            assert hero.count("public-btn--primary") == 1, slug
            figure = html[html.index('data-testid="landing-hero-figure"'):]
            figure = figure[: figure.index("</figure>")]
            assert f"/static/img/roles/{slug}.webp" in figure, slug
            assert f"/static/img/roles/{slug}-mobile.webp" in figure, slug
            assert 'data-testid="page-screenshot"' not in html, f"{slug}: screenshot repeated below the hero"
            assert html.count("<h1") == 1, slug
