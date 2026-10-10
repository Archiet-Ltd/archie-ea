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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bleach
import markdown
import yaml
from markupsafe import Markup

from app.services.billing_plans import CONTACT_SALES_URL, PLANS

CONTENT_ROOT = Path(__file__).resolve().parent.parent.parent / "content" / "pages"
STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
SITE_URL = "https://entelim.org"

# ── SEO/GEO audit verdicts (2026-10-06) -- single source of truth ───────────
# Which pages fold into a parent page (MERGE, 301 from the old URL) and which
# stay reachable but out of the sitemap, llms.txt/llms-full.txt and every
# nav/index listing (HOLD, noindex) -- see the audit bucket for the full
# page-by-page reasoning. One registry each, the same pattern
# MODULE_CAPTURE_PENDING above already uses, rather than a front-matter flag
# hand-edited onto 38 separate files.

MERGED_PAGES: dict[str, str] = {
    "/vision": "/about",
    "/modules/batch-import": "/modules/architecture-model",
    "/modules/duplicate-detection": "/modules/rationalization",
    "/modules/gap-analysis": "/modules/roadmaps",
    "/modules/investment-analysis": "/modules/business-case",
    "/modules/my-applications": "/use-cases/what-i-own",
    "/modules/projects": "/use-cases/programme-tracking",
    "/modules/solutions": "/modules/arb",
    "/modules/value-streams": "/use-cases/value-streams-at-risk",
    "/use-cases/revenue-stream-risk": "/use-cases/value-streams-at-risk",
    "/use-cases/show-investors-what-we-run": "/use-cases/architecture-map-for-due-diligence",
    "/use-cases/key-person-risk": "/modules/org-chart",
}

HELD_PAGE_URLS: frozenset[str] = frozenset({
    "/modules/capability-maturity",
    "/use-cases/capability-maturity-heatmap",
    "/modules/industry-apqc",
    "/modules/integrations",
    "/use-cases/canvas-dependencies",
    "/use-cases/lean-canvas",
    "/use-cases/reference-packs",
    "/use-cases/website-first-look",
    "/use-cases/website-full-profile",
    "/use-cases/systems-with-no-owner",
    "/use-cases/import-from-jira-and-github",
    "/use-cases/match-systems-across-jira-and-servicenow",
    "/use-cases/architecture-change-tracking",
    "/use-cases/pagerduty-and-datadog-incidents",
    "/use-cases/connector-candidates",
    "/use-cases/leanix-ardoq-import",
    "/use-cases/servicenow-cmdb-mapping",
    "/use-cases/ai-assistant-for-your-architecture",
    "/use-cases/derivation-yield",
    "/use-cases/workforce-planning",
    "/use-cases/adopt-reference-pack",
    "/use-cases/see-your-own-twin",
    "/use-cases/set-up-in-an-afternoon",
    "/use-cases/demo-on-a-company-like-mine",
    "/use-cases/what-we-can-and-cannot-tell",
    "/docs",
})

# A single, parameterised offer for every held page's "tell us you need
# this" enquiry -- see PublicPage.feature_interest_offer below. The
# product_inquiries.offer column is String(50); "feature:" (8 chars) plus
# the longest held slug today (41 chars) is 49, so this fits without a
# migration. A future held slug longer than 42 characters would not --
# flagged here rather than guessed around.
FEATURE_INTEREST_OFFER_PREFIX = "feature:"
FEATURE_INTEREST_CONSENT_TEXT = (
    "Used to tell you when this becomes available, and for nothing else."
)
IMG_MODULES_DIR = STATIC_ROOT / "img" / "modules"
IMG_USE_CASES_DIR = STATIC_ROOT / "img" / "use-cases"
VIDEO_USE_CASES_DIR = STATIC_ROOT / "video" / "use-cases"

# ── seeded demo personas (Lantern Quay Systems -- app/commands/seed_demo_company.py) ──
# scripts/capture_screenshots.py --modules logs in as these to capture the
# registries below; kept here (not duplicated in the capture script) so the
# fictional persona list has one source.
DEMO_PERSONA = "demo@lantern-quay.example.com"
ITOPS_ADMIN_PERSONA = "sage.itops@lantern-quay.example.com"
APP_MANAGER_PERSONA = "casey.inventory@lantern-quay.example.com"
PROCUREMENT_PERSONA = "taylor.procurement@lantern-quay.example.com"

# ── module & use-case screenshot/recording registry ─────────────────────────
# Single source of truth for which live pages get a captured screen: the path
# scripts/capture_screenshots.py --modules visits, which seeded persona can
# reach it, and the caption/alt text get_page_screenshot()/get_page_recording()
# below attach to the image. The capture script imports this same list rather
# than keeping its own copy, so the capture tool and the renderer can never
# drift out of agreement about what a slug's image is of.
#
# Each entry: (slug, path, persona_email, caption, alt_text)
MODULE_CAPTURES: list[tuple[str, str, str, str, str]] = [
    ("ai-chat", "/ai-chat", DEMO_PERSONA,
     "The AI assistant answering a question from the organisation's own architecture model.",
     "Screenshot of the AI Chat module answering a question about Lantern Quay Systems' architecture."),
    ("applications", "/applications/", DEMO_PERSONA,
     "The application portfolio: lifecycle tiles, owner and vendor coverage, and each application's type, lifecycle stage and the capability it supports.",
     "Screenshot of the Applications module showing Lantern Quay Systems' portfolio tiles, data-quality coverage and application list with type, lifecycle stage and mapped capability."),
    ("arb", "/arb/", DEMO_PERSONA,
     "The Architecture Review Board hub: review volume, pending decisions, approval rate and cycle time.",
     "Screenshot of the Architecture Review Board hub showing total and pending reviews, approval rate, cycle time and the review status chart."),
    ("architecture-model", "/architecture/", DEMO_PERSONA,
     "The ArchiMate element browser, spanning the business, application, technology and motivation layers.",
     "Screenshot of the Architecture Model module's ArchiMate element browser."),
    ("business-case", "/business-case/", DEMO_PERSONA,
     "Business cases with their status, three-year TCO and return on investment.",
     "Screenshot of the Business Case module's list of business cases."),
    ("business-model-canvas", "/business-model/", DEMO_PERSONA,
     "The business model canvas library, with each canvas's operating-model archetype.",
     "Screenshot of the Business Model Canvas module's canvas library."),
    ("compliance-frameworks", "/dashboard/compliance", DEMO_PERSONA,
     "The compliance frameworks dashboard, tracking framework coverage across the estate.",
     "Screenshot of the Compliance Frameworks module's dashboard."),
    ("duplicate-detection", "/duplicate-detection/simple", DEMO_PERSONA,
     "The duplicate detection dashboard, flagging applications that may overlap in function.",
     "Screenshot of the Duplicate Detection module's dashboard."),
    ("my-applications", "/my-applications/", APP_MANAGER_PERSONA,
     "An application owner's personal dashboard of the applications they're responsible for.",
     "Screenshot of the My Applications module's owner dashboard."),
    ("portfolio", "/portfolio/", DEMO_PERSONA,
     "The portfolio dashboard, summarising active initiatives and programmes.",
     "Screenshot of the Portfolio module's dashboard."),
    ("procurement", "/procurement/renewals", PROCUREMENT_PERSONA,
     "The contract renewals dashboard, showing upcoming vendor renewal dates.",
     "Screenshot of the Procurement module's contract renewals dashboard."),
    ("projects", "/enterprise/implementation/work-packages", DEMO_PERSONA,
     "The work packages list, tracking delivery programmes in progress.",
     "Screenshot of the Projects module's work packages list."),
    ("risk-register", "/risks/", DEMO_PERSONA,
     "The risk register, with likelihood, impact and a mitigation plan recorded for each risk.",
     "Screenshot of the Risk Register module."),
    ("solutions", "/solutions/", DEMO_PERSONA,
     "The solutions list, tracking each solution's design progress and next action.",
     "Screenshot of the Solutions module's solution list."),
    ("vendors", "/applications/vendors", DEMO_PERSONA,
     "The vendor catalogue, with each vendor's type, products and contract status.",
     "Screenshot of the Vendors module's vendor catalogue."),
]

# ── capture-pending: named explicitly, by design (lead review 2026-10-06) ──
# Every slug below is genuinely capture_status: live in its own content file
# -- the FEATURE is real and shipped, cta: plans stays untouched, and
# nothing here ever flips that front-matter. What's pending is only the
# capture: each one's first screenshot/recording was reviewed and rejected
# (empty data, the wrong screen, or a recording that never performs the use
# case it claims), the file was removed, and round 2 reseeds what each
# screen actually needs and recaptures it properly.
#
# This dict (not just an absence from MODULE_CAPTURES) is what keeps the
# registry-completeness tests strict: test_module_screenshots.py asserts
# every live module/use-case is in MODULE_CAPTURES **or** named here, so a
# module that quietly loses its capture without being added to this list
# still fails the test, exactly as it would have before any pending list
# existed. Round 2 deletes a name from here the same moment it adds the
# slug back to the matching *_CAPTURES list above -- the two are meant to
# be mutually exclusive, never both.
MODULE_CAPTURE_PENDING: dict[str, str] = {
    "integrations": (
        "connector health dashboard throws \"An internal error occurred\" on any "
        "data: app/routes/connector_routes.py api_list_connectors() calls .value "
        "on connector_type/status/sync_mode as though they were Enum columns, but "
        "app/models/connector_config.py declares all three as plain strings -- "
        "pre-existing bug, unrelated file, out of scope to fix here"
    ),
    "capability-maturity": (
        "heat map showed \"No capabilities yet\" for an organisation that has 24 "
        "capabilities elsewhere (investment-analysis) -- this screen reads a "
        "different capability store than the one seeded; a reuse-register-shaped "
        "bug, separate brief owed"
    ),
    "batch-import": (
        "completed jobs rendered at 0% progress and 0 elements generated -- "
        "reads broken, not done; needs a real completed run with actual elements"
    ),
    "org-chart": (
        "captured screen was the module's hub page (three link cards), not the "
        "organisation chart itself -- needs actors/hierarchy seeded and the "
        "/organization/chart route captured instead"
    ),
    "diagrams": (
        "captured screen was a list of diagram names, not a rendered diagram -- "
        "needs a diagram actually open in the Composer"
    ),
    "industry-apqc": "0 processes shown on every seeded framework",
    "investment-analysis": "domain Unknown and 0 apps coverage on every capability row",
    "gap-analysis": "type None on every row",
    "rationalization": (
        "captured screen was the \"Get started\" onboarding panel, not the "
        "rationalization view itself -- needs scores past onboarding"
    ),
    "roadmaps": "0 gaps detected; plateaus with no description and 0 gaps",
    "value-streams": "0 stages and 0 capabilities on every value stream",
}

# The one live use-case page: same screen as the capability-maturity module
# (its own url_slug front-matter field points at the identical route). Empty
# for the same reason as capability-maturity above -- see
# USE_CASE_SCREENSHOT_PENDING.
USE_CASE_SCREENSHOT_CAPTURES: list[tuple[str, str, str, str, str]] = []

USE_CASE_SCREENSHOT_PENDING: dict[str, str] = {
    "capability-maturity-heatmap": (
        "same capability-maturity heat map issue as the module above -- "
        "\"No capabilities yet\""
    ),
}

# Four multi-step use cases keyed by the readable /use-cases/<slug> form --
# the pending URL rewrite from /use-cases/uc-* to this readable form has
# since landed, so these are keyed the same way USE_CASE_SCREENSHOT_PENDING
# above is: a rename, not a recapture. Each entry: (slug, steps,
# persona_email, caption, alt_text) -- steps themselves only matter to the
# capture script. Empty for round 1 -- see USE_CASE_VIDEO_PENDING below;
# round 2 restores these once each recording actually performs the use case
# it claims rather than touring past it.
USE_CASE_VIDEO_CAPTURES: list[tuple[str, list, str, str, str]] = []

USE_CASE_VIDEO_PENDING: dict[str, str] = {
    "import-archimate-model": "recording never selects or uploads a file",
    "what-breaks-and-who-gets-called": (
        "recording ends on the Twin map's empty \"pick a system\" prompt"
    ),
    "architecture-review-board": "recording never submits or decides a change",
    "business-case-for-the-cio": "recording never opens an actual business case",
}

# uc-s4-02-set-up-in-an-afternoon.md ("set it up from our spreadsheet in an
# afternoon") is deliberately in neither USE_CASE_VIDEO_CAPTURES nor
# USE_CASE_VIDEO_PENDING: its own content says plainly "What Entelim is
# building ... Coming soon. Join the waiting list" (capture_status:
# not_applicable_not_yet_built, state: briefed, not on_main). There is no
# built screen behind that page to record, which is a different thing from
# a capture being merely pending.

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
    # id is not an XSS vector; allowed so a page's own headings can carry a
    # deep-link anchor (e.g. /features#strategy-management) for other pages
    # to link into, without needing the markdown "toc" extension.
    "h1": ["id"], "h2": ["id"], "h3": ["id"], "h4": ["id"], "h5": ["id"], "h6": ["id"],
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
    # The archiet.ai address this page's content also lives at, for
    # cross-linking (the /vs hub) -- NOT a canonical-tag assertion. See
    # self_canonical_url below for the tag that actually goes in <head>.
    external_url: str | None = None

    @property
    def cta(self) -> str | None:
        return self.front_matter.get("cta")

    @property
    def page_family(self) -> str:
        return self.front_matter.get("page_family", self.family)

    @property
    def self_canonical_url(self) -> str:
        """Every public page is canonical to its own entelim.org address --
        no exceptions, including comparison pages that also have an
        ``external_url`` on archiet.ai (section 2 of the SEO/GEO audit:
        the two are different products' content now, not the same page)."""
        return f"{SITE_URL}{self.url}"

    @property
    def is_held(self) -> bool:
        """Out of the sitemap, llms.txt/llms-full.txt and every nav/index
        listing, but still reachable at its own URL with a noindex tag.

        True for a HOLD-verdict page (``HELD_PAGE_URLS`` -- not built yet)
        and for a page withdrawn from discovery via front matter
        ``state: not_planned`` (a feature that shipped as a page, then had
        its release item pulled, with nothing left to build towards) --
        both are the same "still reachable, just not advertised" case to
        every caller, so one property covers both reasons.

        A MERGE-verdict page (``MERGED_PAGES``) is a different case --
        its own URL 301s to a parent instead of rendering at all -- so it
        is not folded into this property; see load_feed_pages(), which
        checks both ``is_held`` and ``MERGED_PAGES`` separately.
        """
        return (
            self.url in HELD_PAGE_URLS
            or self.front_matter.get("state") == "not_planned"
        )

    @property
    def description(self) -> str | None:
        """Meta description: this page's own front-matter ``description``,
        falling back to its first rendered paragraph trimmed to 155
        characters when no explicit one is set. Computed, not stored --
        the six pages a content-writer is rewriting on another branch get
        this fallback too without anyone editing their front matter."""
        explicit = self.front_matter.get("description")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        return _first_paragraph_summary(self.body_html)

    @property
    def feature_interest_offer(self) -> str:
        """The ``offer`` value for this page's "tell us you need this"
        enquiry -- unique per page (the product_inquiries table has a
        UNIQUE(email, offer) constraint), so asking about two different
        held features from the same address stores two rows, not one."""
        return (FEATURE_INTEREST_OFFER_PREFIX + self.slug)[:50]


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


def _build_external_url(front_matter: dict[str, Any]) -> str | None:
    """The archiet.ai address this page's content also lives at, if any --
    used only for cross-linking (the /vs hub), never as this page's own
    <link rel="canonical">, which is always self (PublicPage.self_canonical_url)."""
    url_slug = front_matter.get("url_slug", "")
    if isinstance(url_slug, str) and url_slug.startswith("archiet.ai/"):
        return "https://" + url_slug
    return None


def _first_paragraph_summary(body_html: str, limit: int = 155) -> str | None:
    """The page's first rendered paragraph, tags and entities stripped,
    trimmed to ``limit`` characters at a word boundary -- the fallback
    meta description for a page with no explicit front-matter one."""
    import html as _html
    import re

    match = re.search(r"<p[^>]*>(.*?)</p>", body_html, re.DOTALL)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", "", match.group(1))
    text = _html.unescape(text)
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    truncated = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return truncated + "…"


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


_OLD_USE_CASE_FILENAME_RE = re.compile(r"uc-s\d-\d{2}-[a-z0-9-]+")


def use_case_redirect_target(old_filename_slug: str) -> str | None:
    """The new ``/use-cases/<slug>`` URL for a use-case page previously
    served at its internal ``uc-sN-NN-*`` filename slug, or ``None`` if
    ``old_filename_slug`` doesn't even look like one of those filenames, is
    not a known filename in this family, or is one whose public slug was
    never different (nothing to redirect).

    Lets the ``/use-cases/<slug>`` route 301 an already-indexed old URL to
    its new one instead of just 404ing it. The filename-shape check runs
    first and fails closed: without it, any slug-shaped string reaching this
    function would open whatever file matches it verbatim under
    content/pages/function-per-segment/ and 301 to that file's own url_slug,
    which is not a claim this function should make about arbitrary input.
    """
    if not _OLD_USE_CASE_FILENAME_RE.fullmatch(old_filename_slug):
        return None
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


# Module images captured at 2x device pixel ratio by scripts/capture_proof_assets.py: their file
# pixels are twice their CSS size, so the <img> width/height attributes halve them and the
# image displays 1:1. Every other module image is a 1x capture and keeps its file size.
TWO_X_MODULE_IMAGES = frozenset({"applications", "arb"})


def _screenshot_density(slug: str, page_family: str) -> int:
    return 2 if page_family == "module" and slug in TWO_X_MODULE_IMAGES else 1


def get_page_screenshot(page: "PublicPage") -> dict[str, Any] | None:
    """Screenshot metadata for a module or use-case page, if one exists.

    Gated on capture_status: live -- a page flipped back to awaiting_capture
    stops rendering its image with no code change, since this check runs
    every request -- and on the file actually existing on disk, which is what
    lets MODULE_CAPTURES/USE_CASE_SCREENSHOT_CAPTURES list a page before its
    image has been captured without a broken <img> shipping in the meantime.
    """
    if page.front_matter.get("capture_status") != "live":
        return None

    if page.page_family == "module":
        registry = MODULE_CAPTURES
        static_dir = IMG_MODULES_DIR
        url_prefix = "/static/img/modules"
    elif page.page_family == "function-per-segment":
        registry = USE_CASE_SCREENSHOT_CAPTURES
        static_dir = IMG_USE_CASES_DIR
        url_prefix = "/static/img/use-cases"
    else:
        return None

    entry = next((e for e in registry if e[0] == page.slug), None)
    if entry is None:
        return None
    _, _, _, caption, alt = entry

    image_path = static_dir / f"{page.slug}.webp"
    if not image_path.is_file():
        return None

    from PIL import Image

    density = _screenshot_density(page.slug, page.page_family)
    with Image.open(image_path) as im:
        width, height = im.size[0] // density, im.size[1] // density

    # Optional phone-width capture of the same screen (scripts/capture_proof_assets.py),
    # shown below 1100px instead of shrinking the desktop crop under 1:1.
    mobile = None
    mobile_path = static_dir / f"{page.slug}-mobile.webp"
    if mobile_path.is_file():
        with Image.open(mobile_path) as im:
            mobile = {
                "url": f"{url_prefix}/{page.slug}-mobile.webp",
                "width": im.size[0] // density,
                "height": im.size[1] // density,
            }

    return {
        "url": f"{url_prefix}/{page.slug}.webp",
        "width": width,
        "height": height,
        "mobile": mobile,
        "alt": alt,
        "caption": caption,
    }


def get_page_recording(page: "PublicPage") -> dict[str, Any] | None:
    """Recording metadata for a use-case page with a captured video, if any.

    Independent of capture_status: these use cases get a recording precisely
    because their answer is a sequence a single screenshot cannot show, and
    most are (rightly) still marked awaiting_capture for the screenshot that
    field was designed around. The three files existing together -- video,
    poster, and the sidecar with the measured duration -- is this function's
    own, separate signal; it does not read capture_status at all.
    """
    if page.page_family != "function-per-segment":
        return None

    entry = next((e for e in USE_CASE_VIDEO_CAPTURES if e[0] == page.slug), None)
    if entry is None:
        return None
    _, _, _, caption, alt = entry

    video_path = VIDEO_USE_CASES_DIR / f"{page.slug}.webm"
    poster_path = VIDEO_USE_CASES_DIR / f"{page.slug}-poster.webp"
    meta_path = VIDEO_USE_CASES_DIR / f"{page.slug}.json"
    if not (video_path.is_file() and poster_path.is_file() and meta_path.is_file()):
        return None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    return {
        "video_url": f"/static/video/use-cases/{page.slug}.webm",
        "poster_url": f"/static/video/use-cases/{page.slug}-poster.webp",
        "width": meta["width"],
        "height": meta["height"],
        "duration_seconds": meta["duration_seconds"],
        "captured_date": meta["captured_date"],
        "caption": caption,
        "alt": alt,
        "name": f"{page.title} — recorded walkthrough",
    }


def _load_page(file_path: Path, family: str, slug: str, url: str) -> PublicPage:
    raw = file_path.read_text(encoding="utf-8")
    front_matter, body_md = _parse_front_matter(raw)
    if family == "function-per-segment":
        slug, url = _use_case_slug_and_url(front_matter, slug)
    body_html = _sanitize_html(_md.reset().convert(body_md))
    title = _extract_title(body_html, front_matter)
    external_url = _build_external_url(front_matter)
    return PublicPage(
        family=family,
        slug=slug,
        url=url,
        title=title,
        body_html=body_html,
        front_matter=front_matter,
        source_path=file_path,
        external_url=external_url,
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


def load_feed_pages() -> list[PublicPage]:
    """Every public page that belongs in a "lists every page" surface: the
    sitemap, /llms.txt, /llms-full.txt, the /vs hub, and any per-family
    "see every one of these" index (e.g. the /use-cases index).

    The single, combined feed set, built on both verdicts the SEO/GEO audit
    produces -- excludes a HOLD-verdict page or a page withdrawn from
    discovery via front matter ``state: not_planned`` (see
    PublicPage.is_held, which covers both) and a MERGE-verdict page
    (MERGED_PAGES -- its own URL 301s to a parent instead of rendering, so
    it is not a second entry for content that now lives at the target).
    Every excluded page still renders at its own URL via load_page() /
    load_all_pages(), which this does not change; it is simply not
    advertised as current. Every caller that used to build its own feed
    list (a prior round's ``_indexable_pages()`` in app/main/views.py among
    them) should call this instead of load_all_pages() directly, so a newly
    held, withdrawn or merged page is left out everywhere at once rather
    than one surface at a time.
    """
    return [
        page for page in load_all_pages()
        if not page.is_held and page.url not in MERGED_PAGES
    ]


def feed_page_paths() -> list[str]:
    """Every path that belongs in a "submit/list every page" surface: the
    homepage, the /vs and /use-cases hub views (not PublicPage content, so
    load_feed_pages() alone does not carry them) and every path from
    load_feed_pages() itself.

    The sitemap (app/main/views.py::sitemap_xml) and the IndexNow CLI
    (app/commands/indexnow_commands.py::ping_indexnow_command) both build
    their URL set from this one list, so the two cannot drift apart again
    the way they did when each built its own (see
    tests/test_public_content_pages.py::test_indexnow_submission_matches_sitemap_urls).
    """
    return ["/", "/vs", "/use-cases"] + [page.url for page in load_feed_pages()]


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

    Extended (not duplicated) to also carry BreadcrumbList for module,
    use-case, comparison and offer pages, and FAQPage for ANY page family
    whose own body has a real "## Frequently asked ..." section -- not just
    comparison pages, which is all the FAQ extractor originally gated on.
    Every applicable node shares one ``@graph`` in a single script block,
    the standard way to combine more than one schema.org type in one
    place, rather than a second builder function or a second <script> tag.

    Returns ``Markup`` (a ``str`` subclass -- every existing caller treating
    it as plain text, including ``json.loads()``, is unaffected): the value
    is already escaped for a <script> block by the time it leaves this
    function, so the template renders it with no bare ``|safe`` for
    test_template_escaping.py to flag.
    """
    family = page.page_family
    site_url = SITE_URL

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

    extra_nodes: list[dict[str, Any]] = []

    breadcrumbs = _breadcrumb_list_items(page, site_url)
    if breadcrumbs:
        extra_nodes.append({"@type": "BreadcrumbList", "itemListElement": breadcrumbs})

    # Comparison pages already ARE a FAQPage above -- every other family
    # gets one added alongside its own primary type when its body actually
    # has a "## Frequently asked ..." section with real Q&A pairs under it
    # (PR415's longer module/offer rewrites, going forward).
    if family != "comparison":
        faq_questions = _extract_faq_questions(page.body_html)
        if faq_questions:
            extra_nodes.append({
                "@type": "FAQPage",
                "url": f"{site_url}{page.url}",
                "mainEntity": faq_questions,
            })

    if extra_nodes:
        primary = {k: v for k, v in ld.items() if k != "@context"}
        ld = {
            "@context": "https://schema.org",
            "@graph": [primary, *extra_nodes],
        }

    return Markup(_escape_for_script_block(json.dumps(ld, indent=2, ensure_ascii=False)))


# ── BreadcrumbList (SEO/GEO audit item 3) ───────────────────────────────────
# Module, use-case, comparison and offer pages get a breadcrumb trail; the
# fixed site pages (about, pricing, contact, features, docs...) that aren't
# offers, plus vision and dogfood, have no natural parent index and are left
# without one, same as before this change.

_BREADCRUMB_PARENTS: dict[str, tuple[str, str]] = {
    "module": ("Features", "/features"),
    "function-per-segment": ("Use cases", "/use-cases"),
    "comparison": ("Compare", "/vs"),
}


def _breadcrumb_list_items(page: PublicPage, site_url: str) -> list[dict[str, Any]] | None:
    family = page.page_family
    parent = _BREADCRUMB_PARENTS.get(family)
    if parent is None and family == "site" and page.front_matter.get("offer"):
        parent = ("Pricing", "/pricing")
    if parent is None:
        return None

    trail = [("Home", "/"), parent, (page.title, page.url)]
    return [
        {
            "@type": "ListItem",
            "position": position,
            "name": name,
            "item": f"{site_url}{path}",
        }
        for position, (name, path) in enumerate(trail, start=1)
    ]


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
    ld: dict[str, Any] = {
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
    recording = get_page_recording(page)
    if recording is not None:
        ld["video"] = _jsonld_video_object(recording, site_url)
    return ld


def _jsonld_video_object(recording: dict[str, Any], site_url: str) -> dict[str, Any]:
    """VideoObject for a use-case page's recording, so search engines and AI
    answers can cite the clip directly rather than just the page around it.

    Required fields per the brief: name, description, thumbnailUrl,
    uploadDate, duration, contentUrl.
    """
    duration_seconds = int(round(recording["duration_seconds"]))
    return {
        "@type": "VideoObject",
        "name": recording["name"],
        "description": recording["caption"],
        "thumbnailUrl": f"{site_url}{recording['poster_url']}",
        "uploadDate": f"{recording['captured_date']}T00:00:00Z",
        "duration": f"PT{duration_seconds}S",
        "contentUrl": f"{site_url}{recording['video_url']}",
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


def _extract_faq_questions(body_html: str) -> list[dict[str, Any]]:
    """Every Question/Answer pair under this page's own "## Frequently
    asked ..." section, in FAQPage ``mainEntity`` shape -- shared by every
    page family, not just comparison pages. Two heading shapes are
    supported: <h3>Question</h3><p>Answer</p> (the shape PR415's longer
    module/offer rewrites use) and <p><strong>Question</strong>Answer</p>
    (the shape the original comparison pages used).
    """
    import html as _html
    import re

    questions: list[dict[str, str]] = []
    faq_section = re.search(
        r"<h2[^>]*>Frequently asked.*?</h2>(.*?)(?=<h2|$)",
        body_html,
        re.DOTALL | re.IGNORECASE,
    )
    if not faq_section:
        return questions

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
    return questions


def _jsonld_faq(page: PublicPage, site_url: str) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "url": f"{site_url}{page.url}",
        "mainEntity": _extract_faq_questions(page.body_html),
    }