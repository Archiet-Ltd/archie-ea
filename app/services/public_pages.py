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

from app.services.billing_plans import CONTACT_SALES_URL, PLANS

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


def _use_case_slug_and_url(front_matter: dict[str, Any], filename_slug: str) -> tuple[str, str]:
    """A use-case (function-per-segment) page's real, crawlable address is its
    own ``url_slug`` front-matter -- ``/use-cases/<slug>`` for every page in
    this family -- not its internal ``uc-sN-NN-*`` filename, which was never
    meant to be public. A page with no ``url_slug`` yet (should not happen
    once every file carries one, but kept as a safety fallback so a brand new
    file is still reachable immediately) falls back to its filename slug.
    """
    prefix = FAMILY_URL_PREFIX["function-per-segment"] + "/"
    url_slug = front_matter.get("url_slug")
    if isinstance(url_slug, str) and url_slug.startswith(prefix):
        return url_slug[len(prefix):], url_slug
    return filename_slug, f"{FAMILY_URL_PREFIX['function-per-segment']}/{filename_slug}"


def use_case_redirect_target(old_filename_slug: str) -> str | None:
    """The new ``/use-cases/<slug>`` URL for a use-case page previously
    served at its internal ``uc-sN-NN-*`` filename slug, or ``None`` if
    ``old_filename_slug`` is not a known filename in this family, or is one
    whose public slug was never different (nothing to redirect).

    Lets the ``/use-cases/<slug>`` route 301 an already-indexed old URL to
    its new one instead of just 404ing it.
    """
    family_dir = CONTENT_ROOT / FAMILY_DIR_MAP["function-per-segment"]
    if not family_dir.is_dir():
        return None
    file_path = family_dir / f"{old_filename_slug}.md"
    if not file_path.is_file():
        return None
    front_matter, _ = _parse_front_matter(file_path.read_text(encoding="utf-8"))
    public_slug, public_url = _use_case_slug_and_url(front_matter, old_filename_slug)
    if public_slug == old_filename_slug:
        return None
    return public_url


def _load_page(file_path: Path, family: str, slug: str, url: str) -> PublicPage:
    raw = file_path.read_text(encoding="utf-8")
    front_matter, body_md = _parse_front_matter(raw)
    if family == "function-per-segment":
        slug, url = _use_case_slug_and_url(front_matter, slug)
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

    if family == "function-per-segment":
        # The public slug is this family's own url_slug front-matter, not
        # the internal uc-sN-NN-* filename -- find the file whose public
        # slug (see _use_case_slug_and_url) matches the one requested.
        for md_file in sorted(family_dir.glob("*.md")):
            filename_slug = _slug_from_filename(md_file.name)
            front_matter, _ = _parse_front_matter(md_file.read_text(encoding="utf-8"))
            public_slug, public_url = _use_case_slug_and_url(front_matter, filename_slug)
            if public_slug == slug:
                return _load_page(md_file, family, filename_slug, public_url)
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


def _self_hosted_offer() -> dict[str, Any]:
    """The self-hosted AGPL edition: unlimited editors, $0, forever.

    Kept distinct from the hosted "Community" tier below, which is also $0
    but is a different thing -- hosted by Entelim, capped at three people --
    so a reader (human or crawler) cannot read one price as describing both.
    """
    return {
        "@type": "Offer",
        "name": "Self-Hosted Edition",
        "price": "0",
        "priceCurrency": "USD",
        "description": (
            "Free to self-host under AGPL-3.0, at any size, for as long as "
            "you want -- unlimited editors, no hosted-tier cap."
        ),
    }


def _flat_plan_offer(plan, interval: str, amount: int) -> dict[str, Any]:
    """An Offer for a flat (non per-unit) hosted plan price at one interval."""
    suffix = "month" if interval == "month" else "year"
    price_text = "Free" if amount == 0 else f"${amount}/{suffix}"
    return {
        "@type": "Offer",
        "name": f"{plan.name} (hosted)",
        "price": str(amount),
        "priceCurrency": plan.display_currency,
        "description": f"{plan.summary} {price_text}, hosted by Entelim.",
    }


def _per_unit_plan_offer(plan, interval: str, amount: int) -> dict[str, Any]:
    """An Offer for a per-seat hosted plan price at one interval.

    Carries a UnitPriceSpecification with a referenceQuantity rather than a
    flat Offer.price, since the real charge is quantity (seats) x this
    per-unit amount, not this amount alone.
    """
    unit = plan.display_price_unit or "unit"
    billing_duration = "P1M" if interval == "month" else "P1Y"
    suffix = "month" if interval == "month" else "year"
    return {
        "@type": "Offer",
        "name": f"{plan.name} (hosted, per {unit})",
        "price": str(amount),
        "priceCurrency": plan.display_currency,
        "description": (
            f"{plan.summary} ${amount}/{unit}/{suffix}, hosted by Entelim."
        ),
        "priceSpecification": {
            "@type": "UnitPriceSpecification",
            "price": str(amount),
            "priceCurrency": plan.display_currency,
            "unitText": unit,
            "billingDuration": billing_duration,
            "referenceQuantity": {
                "@type": "QuantitativeValue",
                "value": 1,
                "unitText": unit,
            },
        },
    }


def _enterprise_offer(plan, site_url: str) -> dict[str, Any]:
    """Enterprise's contract floor: a minimum, not a fixed, purchasable price.

    Typed AggregateOffer (schema.org's type for a price that starts at a
    floor rather than naming one fixed amount), carrying lowPrice rather
    than price, and pointing at contact sales rather than a checkout flow,
    since Enterprise is sold by contract and is not purchasable online.
    """
    floor = plan.display_price_floor_annual
    return {
        "@type": "AggregateOffer",
        "name": plan.name,
        "lowPrice": str(floor),
        "priceCurrency": plan.display_currency,
        "url": f"{site_url}{CONTACT_SALES_URL}",
        "description": (
            f"{plan.summary} Sold by contract, from ${floor:,}/year -- contact sales."
        ),
    }


def _hosted_plan_offers(site_url: str) -> list[dict[str, Any]]:
    """The real hosted tiers (Community, Startup, Team, Enterprise), read
    from billing_plans.PLANS's display-price fields -- the one place those
    dollar figures live, so this list can never silently drift from the
    pricing page or the home page again.
    """
    offers: list[dict[str, Any]] = []
    for plan in PLANS:
        if plan.display_price_floor_annual is not None:
            offers.append(_enterprise_offer(plan, site_url))
            continue
        offer_fn = _per_unit_plan_offer if plan.display_price_per_unit else _flat_plan_offer
        if plan.display_price_monthly is not None:
            offers.append(offer_fn(plan, "month", plan.display_price_monthly))
        if plan.display_price_annual is not None:
            offers.append(offer_fn(plan, "year", plan.display_price_annual))
    return offers


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
            "offers": [_self_hosted_offer(), *_hosted_plan_offers(site_url)],
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
        "offers": [_self_hosted_offer(), *_hosted_plan_offers(site_url)],
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