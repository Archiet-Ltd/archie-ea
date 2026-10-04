"""Public content page loader.

Reads Markdown files with YAML front-matter from content/pages/ and returns
page objects with parsed metadata and rendered HTML body.

Directory layout maps to URL families:
  content/pages/vision/          → /vision
  content/pages/modules/         → /modules/<slug>
  content/pages/function-per-segment/ → /use-cases/<slug>
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

import json
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
    return PublicPage(
        family=family,
        slug=slug,
        url=url,
        title=title,
        body_html=body_html,
        front_matter=front_matter,
        source_path=file_path,
        canonical_url=canonical,
    )


def _slug_from_filename(filename: str) -> str:
    return filename.replace(".md", "")


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
            slug = _slug_from_filename(md_file.name)
            if family == "dogfood":
                url = FAMILY_URL_PREFIX[family]
            elif family == "vision":
                url = FAMILY_URL_PREFIX[family]
            else:
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

    file_path = family_dir / f"{slug}.md"
    if not file_path.is_file():
        return None

    url = f"{FAMILY_URL_PREFIX[family]}/{slug}"
    return _load_page(file_path, family, slug, url)


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