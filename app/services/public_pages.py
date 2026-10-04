"""Public content page loader.

Reads Markdown files with YAML front-matter from content/pages/ and returns
page objects with parsed metadata and rendered HTML body.

Directory layout maps to URL families:
  content/pages/vision/          → /vision
  content/pages/modules/         → /modules/<slug>
  content/pages/function-per-segment/ → /use-cases/<slug> (new: without coded prefix)
  content/pages/vs/              → /vs/<slug>
  content/pages/dogfood/         → /how-archiet-runs-on-entelim
  content/pages/site/            → /<slug> (about, security, privacy, terms,
                                     contact, features, pricing, docs — one
                                     fixed top-level page per file)
  content/pages/legal/           → /<slug> (legal pages held back until
                                     LEGAL_PAGES_ENABLED is on — see
                                     app/services/legal_pages.py)
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bleach
import markdown
import yaml
from markupsafe import Markup

CONTENT_ROOT = Path(__file__).resolve().parent.parent.parent / "content" / "pages"

FAMILY_DIR_MAP = {
    "vision": "vision",
    "module": "modules",
    "function-per-segment": "function-per-segment",
    "comparison": "vs",
    "dogfood": "dogfood",
    "site": "site",
    "legal": "legal",
}

FAMILY_URL_PREFIX = {
    "vision": "/vision",
    "module": "/modules",
    "function-per-segment": "/use-cases",
    "comparison": "/vs",
    "dogfood": "/how-archiet-runs-on-entelim",
    # No prefix: each file under content/pages/site/ is its own fixed
    # top-level page (content/pages/site/about.md -> /about).
    "site": "",
    # Same shape as "site", but only published while LEGAL_PAGES_ENABLED is on.
    "legal": "",
}

# Base URL for canonical and Open Graph URLs
SITE_URL = "https://entelim.org"

_md = markdown.Markdown(extensions=["extra"])

# Tags and attributes produced by standard Markdown (plus extra extension).
# Any HTML tag or attribute not in these lists is stripped by bleach.
_ALLOWED_TAGS = {
    "a", "abbr", "acronym", "b", "blockquote", "br", "code", "em",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li",
    "ol", "p", "pre", "strong", "table", "tbody", "td", "th",
    "thead", "tr", "ul",
}
_ALLOWED_ATTRS = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "th": ["align"],
    "td": ["align"],
}


def _extract_meta_description(front_matter: dict[str, Any], body_md: str) -> str:
    """Extract meta description for a page.

    Priority:
    1. Front-matter 'description' field if present
    2. First italic summary line (text wrapped in *...* or _..._)
    3. First paragraph of content

    Returns plain text, at most 160 characters, cut at a word boundary,
    HTML-escaped.
    """
    # 1. Check front-matter description
    if "description" in front_matter and front_matter["description"]:
        desc = str(front_matter["description"]).strip()
        if desc:
            return _truncate_at_word_boundary(html.escape(desc), 160)

    # 2. Look for first italic summary line in markdown body
    # Pattern: *text* or _text_ at the start of a line (after optional whitespace)
    # Must be a single line, not spanning multiple lines
    italic_match = re.search(r"(?m)^\s*[\*_]([^\*_\n]+)[\*_]\s*$", body_md.strip())
    if italic_match:
        desc = italic_match.group(1).strip()
        if desc:
            return _truncate_at_word_boundary(html.escape(desc), 160)

    # 3. Fall back to first paragraph (non-heading, non-empty line)
    lines = body_md.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("---"):
            # This is the first paragraph
            # Remove markdown formatting for plain text
            plain = re.sub(r"[\*_`\[\]()#]", "", line)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                return _truncate_at_word_boundary(html.escape(plain), 160)

    return ""


def _truncate_at_word_boundary(text: str, max_len: int) -> str:
    """Truncate text at a word boundary, not exceeding max_len."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # Find last space
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space]
    return truncated


def _transform_use_case_slug(slug: str) -> str:
    """Transform use-case slug by dropping the coded prefix.

    e.g., 'uc-s1-01-canvas-dependencies' -> 'canvas-dependencies'
    If the slug doesn't match the pattern, return as-is.
    """
    # Pattern: uc-s<digit>-<digits>-<rest>
    match = re.match(r"^uc-s\d+-\d+-(.+)$", slug)
    if match:
        return match.group(1)
    return slug


def _build_canonical_url(page_url: str) -> str:
    """Build canonical URL for a page."""
    return f"{SITE_URL}{page_url}"


def _build_og_data(page: "PublicPage") -> dict[str, str]:
    """Build Open Graph data for a page."""
    return {
        "og:title": page.title,
        "og:description": page.meta_description or page.title,
        "og:url": _build_canonical_url(page.url),
        "og:type": "website",
    }


def _sanitize_html(html: str) -> Markup:
    """Strip unsafe HTML tags and attributes from rendered Markdown.

    Returns ``Markup`` (a ``str`` subclass), not a plain string: this is the
    one place sanitization actually happens, so it is also the one place
    that gets to mark the result trusted -- the template then renders it
    with no bare ``|safe`` for test_template_escaping.py to flag.
    """
    return Markup(bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
    ))


@dataclass
class PublicPage:
    """A single public content page."""

    family: str
    slug: str
    url: str
    title: str
    body_html: str
    front_matter: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None
    canonical_url: str | None = None
    meta_description: str = ""
    og_data: dict[str, str] = field(default_factory=dict)

    @property
    def cta(self) -> str | None:
        return self.front_matter.get("cta")

    @property
    def page_family(self) -> str:
        return self.front_matter.get("page_family", self.family)


def _parse_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    """Split YAML front-matter from Markdown body.

    Front-matter is delimited by --- on its own line at the start of the file.
    """
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    body = parts[2].strip()
    return meta, body


def _extract_title(body_html: str, front_matter: dict[str, Any]) -> str:
    """Extract the page title from the first h1 in rendered HTML."""
    import html as _html
    import re

    match = re.search(r"<h1[^>]*>(.*?)</h1>", body_html, re.DOTALL)
    if match:
        return _html.unescape(re.sub(r"<[^>]+>", "", match.group(1)).strip())
    return front_matter.get("title", front_matter.get("module_label", "Untitled"))


def _build_canonical(front_matter: dict[str, Any]) -> str | None:
    """Build a canonical URL from front-matter if the page targets another domain."""
    url_slug = front_matter.get("url_slug", "")
    if isinstance(url_slug, str) and url_slug.startswith("archiet.ai/"):
        return "https://" + url_slug
    return None


def _load_page(file_path: Path, family: str, slug: str, url: str) -> PublicPage:
    raw = file_path.read_text(encoding="utf-8")
    front_matter, body_md = _parse_front_matter(raw)
    body_html = _sanitize_html(_md.reset().convert(body_md))
    title = _extract_title(body_html, front_matter)
    canonical = _build_canonical(front_matter)
    # For non-comparison pages, canonical is the entelim.org URL
    if canonical is None:
        canonical = _build_canonical_url(url)
    meta_description = _extract_meta_description(front_matter, body_md)
    page = PublicPage(
        family=family,
        slug=slug,
        url=url,
        title=title,
        body_html=body_html,
        front_matter=front_matter,
        source_path=file_path,
        canonical_url=canonical,
        meta_description=meta_description,
    )
    page.og_data = _build_og_data(page)
    return page


def _slug_from_filename(filename: str) -> str:
    return filename.replace(".md", "")


def _get_use_case_slugs(filename: str) -> tuple[str, str]:
    """Get both old and new slugs for a use-case file.

    Returns (old_slug, new_slug) where:
    - old_slug is the filename without .md (e.g., 'uc-s1-01-canvas-dependencies')
    - new_slug is the transformed slug without coded prefix (e.g., 'canvas-dependencies')
    """
    old_slug = _slug_from_filename(filename)
    new_slug = _transform_use_case_slug(old_slug)
    return old_slug, new_slug


def load_all_pages() -> list[PublicPage]:
    """Load every Markdown page under content/pages/."""
    pages: list[PublicPage] = []
    if not CONTENT_ROOT.is_dir():
        return pages

    from app.services.legal_pages import legal_pages_enabled

    for family, dir_name in FAMILY_DIR_MAP.items():
        if family == "legal" and not legal_pages_enabled():
            continue
        family_dir = CONTENT_ROOT / dir_name
        if not family_dir.is_dir():
            continue
        for md_file in sorted(family_dir.glob("*.md")):
            if family == "function-per-segment":
                old_slug, new_slug = _get_use_case_slugs(md_file.name)
                slug = new_slug  # Use new slug for the page
                url = f"{FAMILY_URL_PREFIX[family]}/{new_slug}"
            elif family == "dogfood":
                slug = _slug_from_filename(md_file.name)
                url = FAMILY_URL_PREFIX[family]
            elif family == "vision":
                slug = _slug_from_filename(md_file.name)
                url = FAMILY_URL_PREFIX[family]
            else:
                slug = _slug_from_filename(md_file.name)
                url = f"{FAMILY_URL_PREFIX[family]}/{slug}"
            pages.append(_load_page(md_file, family, slug, url))

    return pages


def load_page(family: str, slug: str | None = None) -> PublicPage | None:
    """Load a single page by family and optional slug."""
    if family not in FAMILY_DIR_MAP:
        return None
    dir_name = FAMILY_DIR_MAP[family]
    family_dir = CONTENT_ROOT / dir_name
    if not family_dir.is_dir():
        return None

    if family in ("vision", "dogfood"):
        # These families have a single known file
        if family == "vision":
            target = "home.md"
        else:
            target = "how-archiet-runs-on-entelim.md"
        file_path = family_dir / target
        if not file_path.is_file():
            return None
        slug_val = _slug_from_filename(target)
        url = FAMILY_URL_PREFIX[family]
        return _load_page(file_path, family, slug_val, url)

    if slug is None:
        return None

    # For use-case pages, try both old and new slug patterns
    if family == "function-per-segment":
        # First try the slug as-is (could be new slug)
        file_path = family_dir / f"{slug}.md"
        if file_path.is_file():
            url = f"{FAMILY_URL_PREFIX[family]}/{slug}"
            return _load_page(file_path, family, slug, url)
        # Then try to find a file whose new slug matches
        for md_file in family_dir.glob("*.md"):
            old_slug, new_slug = _get_use_case_slugs(md_file.name)
            if new_slug == slug:
                url = f"{FAMILY_URL_PREFIX[family]}/{new_slug}"
                return _load_page(md_file, family, new_slug, url)
        return None

    file_path = family_dir / f"{slug}.md"
    if not file_path.is_file():
        return None

    url = f"{FAMILY_URL_PREFIX[family]}/{slug}"
    return _load_page(file_path, family, slug, url)


def get_old_use_case_url(new_slug: str) -> str | None:
    """Get the old URL for a use-case page given its new slug.

    Returns the old URL (with coded prefix) if found, else None.
    """
    family_dir = CONTENT_ROOT / "function-per-segment"
    if not family_dir.is_dir():
        return None
    for md_file in family_dir.glob("*.md"):
        old_slug, file_new_slug = _get_use_case_slugs(md_file.name)
        if file_new_slug == new_slug:
            return f"{FAMILY_URL_PREFIX['function-per-segment']}/{old_slug}"
    return None


def get_new_use_case_url(old_slug: str) -> str | None:
    """Get the new URL for a use-case page given its old slug.

    Returns the new URL (without coded prefix) if found, else None.
    """
    family_dir = CONTENT_ROOT / "function-per-segment"
    if not family_dir.is_dir():
        return None
    for md_file in family_dir.glob("*.md"):
        file_old_slug, new_slug = _get_use_case_slugs(md_file.name)
        if file_old_slug == old_slug:
            return f"{FAMILY_URL_PREFIX['function-per-segment']}/{new_slug}"
    return None


def build_jsonld(page: PublicPage) -> str:
    """Build JSON-LD structured data for a page based on its family.

    Returns ``Markup`` (a ``str`` subclass -- every existing caller treating
    it as plain text, including ``json.loads()``, is unaffected): the value
    is already escaped for a <script> block by the time it leaves this
    function, so the template renders it with no bare ``|safe`` for
    test_template_escaping.py to flag.
    """
    family = page.page_family
    site_url = "https://entelim.org"

    if family == "comparison":
        ld = _jsonld_faq(page, site_url)
    elif family == "vision":
        ld = _jsonld_software_app(page, site_url)
    elif family == "module":
        ld = _jsonld_software_app(page, site_url)
    elif family == "function-per-segment":
        ld = _jsonld_webpage(page, site_url)
    elif family == "dogfood":
        ld = _jsonld_webpage(page, site_url)
    else:
        ld = _jsonld_webpage(page, site_url)

    return Markup(_escape_for_script_block(json.dumps(ld, indent=2, ensure_ascii=False)))


def _escape_for_script_block(serialised: str) -> str:
    """Make serialised JSON safe to place inside a <script> element.

    ``json.dumps`` does not escape ``<``, ``>`` or ``&``, so a title or answer
    containing ``</script>`` would end the block early and let the rest run as
    markup. The escaped forms are still valid JSON and decode to the same text.
    Returned as a plain ``str``: the caller (``build_jsonld``) is the one that
    marks the final value ``Markup``-trusted, since this helper's own output
    still needs JSON-encoding (by ``json.dumps`` above) before that's true.
    """
    return (
        serialised.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def _jsonld_webpage(page: PublicPage, site_url: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": page.title,
        "url": f"{site_url}{page.url}",
        "about": {
            "@type": "SoftwareApplication",
            "name": "Entelim",
            "applicationCategory": "Enterprise Architecture",
            "operatingSystem": "Web",
            "offers": {
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "USD",
                "description": "Free to self-host under AGPL",
            },
        },
    }


def _jsonld_software_app(page: PublicPage, site_url: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Entelim",
        "url": f"{site_url}{page.url}",
        "applicationCategory": "Enterprise Architecture",
        "operatingSystem": "Web",
        "description": page.title,
        "offers": [
            {
                "@type": "Offer",
                "name": "Self-Hosted",
                "price": "0",
                "priceCurrency": "USD",
                "description": "Free to self-host under AGPL",
            },
            {
                "@type": "Offer",
                "name": "Commercial Licence",
                "price": "0",
                "priceCurrency": "USD",
                "description": "Available for organisations that need different terms",
            },
        ],
    }


def _jsonld_faq(page: PublicPage, site_url: str) -> dict[str, Any]:
    import html as _html
    import re

    questions: list[dict[str, str]] = []
    # Extract FAQ entries from rendered HTML: h2 "Frequently asked" followed by
    # either <h3>Question</h3><p>Answer</p> or <p><strong>Question</strong>Answer</p>
    faq_section = re.search(
        r"<h2[^>]*>Frequently asked.*?</h2>(.*?)(?=<h2|$)",
        page.body_html,
        re.DOTALL | re.IGNORECASE,
    )
    if faq_section:
        section_html = faq_section.group(1)
        # Format A: <h3>Question</h3><p>Answer</p>
        qa_pairs = re.findall(
            r"<h3[^>]*>(.*?)</h3>\s*<p[^>]*>(.*?)</p>",
            section_html,
            re.DOTALL,
        )
        for q_html, a_html in qa_pairs:
            q_text = _html.unescape(re.sub(r"<[^>]+>", "", q_html).strip())
            a_text = _html.unescape(re.sub(r"<[^>]+>", "", a_html).strip())
            if q_text and a_text:
                questions.append(
                    {
                        "@type": "Question",
                        "name": q_text,
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": a_text,
                        },
                    }
                )
        # Format B: <p><strong>Question</strong>Answer text</p>
        if not questions:
            bold_pairs = re.findall(
                r"<p[^>]*>\s*<strong[^>]*>(.*?)</strong>\s*(.*?)</p>",
                section_html,
                re.DOTALL,
            )
            for q_html, a_html in bold_pairs:
                q_text = _html.unescape(re.sub(r"<[^>]+>", "", q_html).strip())
                a_text = _html.unescape(re.sub(r"<[^>]+>", "", a_html).strip())
                if q_text and a_text:
                    questions.append(
                        {
                            "@type": "Question",
                            "name": q_text,
                            "acceptedAnswer": {
                                "@type": "Answer",
                                "text": a_text,
                            },
                        }
                    )

    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "url": f"{site_url}{page.url}",
        "mainEntity": questions,
    }