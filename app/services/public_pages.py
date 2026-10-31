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

import functools
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
    "/modules/integrations": "/connectors",
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
    # /docs has no front-matter state field of its own (it is a plain site
    # page, not a module/use-case), so is_held's state-based half cannot see
    # it -- this is its only hold mechanism.
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
IMG_ROLES_DIR = STATIC_ROOT / "img" / "roles"
VIDEO_USE_CASES_DIR = STATIC_ROOT / "video" / "use-cases"

# ── seeded demo personas (Lantern Quay Systems -- app/commands/seed_demo_company.py) ──
# scripts/capture_screenshots.py --modules logs in as these to capture the
# registries below; kept here (not duplicated in the capture script) so the
# fictional persona list has one source.
DEMO_PERSONA = "demo@lantern-quay.example.com"
ITOPS_ADMIN_PERSONA = "sage.itops@lantern-quay.example.com"
APP_MANAGER_PERSONA = "casey.inventory@lantern-quay.example.com"
PROCUREMENT_PERSONA = "taylor.procurement@lantern-quay.example.com"

# Role pages: each role's workspace in Ask, open on the impact question for the
# Calibration Ledger application with its first answer row shown
# (scripts/capture_proof_assets.py --roles). Same 5-tuple shape as MODULE_CAPTURES.
def _role_capture(slug: str, role: str) -> tuple[str, str, str, str, str]:
    article = "an" if role[0] in "AEIOUaeiou" or role.startswith("IT ") else "a"
    return (
        slug, "/ai-chat/", DEMO_PERSONA,
        f"The AI chat as {article} {role} sees it: the impact question put to Calibration Ledger, with the first row of the answer showing what depends on it.",
        f"Screenshot of the AI chat signed in as {article} {role}, with the impact question asked of Calibration Ledger and the first result row displayed.",
    )


# The screen each role page's hero shows, named in the browser frame (Ask unless listed).
ROLE_FRAME_LABELS = {
    "business-architects": "Capability maturity",
    "security-architects": "Risk register",
    "data-architects": "Data lineage",
    "chief-architects": "Capability health",
    "scale-up-cto-and-vp-engineering": "Import applications",
}

ROLE_CAPTURES: list[tuple[str, str, str, str, str]] = [
    _role_capture("enterprise-architects", "enterprise architect"),
    _role_capture("cto-and-cio", "CTO or CIO"),
    _role_capture("it-portfolio-managers", "IT portfolio manager"),
    _role_capture("solution-architects", "solution architect"),
    # Roles whose primary screen is not Ask: the path is the screen the page's own
    # text names first, and capture_role photographs the top of it.
    ("business-architects", "/capability-maturity/heatmap", DEMO_PERSONA,
     "The capability maturity heat map as a business architect sees it: current against target maturity for each capability, the gap and the date it was assessed.",
     "Screenshot of the capability maturity heat map signed in as a business architect, listing capabilities with current and target maturity, the gap and the assessment date."),
    ("chief-architects", "/strategic/capability-health", DEMO_PERSONA,
     "The capability health dashboard as a chief architect sees it: average portfolio health, critical capabilities, at-risk hotspots and the domain health index.",
     "Screenshot of the capability health dashboard signed in as a chief architect, with portfolio health, critical capabilities, at-risk hotspots and a domain health index with a status chart."),
    ("scale-up-cto-and-vp-engineering", "/applications/import", DEMO_PERSONA,
     "The application import as a scale-up CTO sees it: a spreadsheet chosen, its first rows previewed and each column matched to an application field.",
     "Screenshot of the Import Applications dialog with the Upload File tab open, a template download link, merge or duplicate handling and a preview of the first rows of an application spreadsheet."),
    ("security-architects", "/risks/", DEMO_PERSONA,
     "The risk register as a security architect sees it: the top risks ranked by how many elements each reaches, with the elements it touches.",
     "Screenshot of the risk register signed in as a security architect, with the top risks ranked by the number of elements each reaches."),
    ("data-architects", "/architecture/data-lineage", DEMO_PERSONA,
     "Data lineage as a data architect sees it: data objects, recorded lineage edges and the form for recording a flow between two modelled data objects.",
     "Screenshot of the data lineage screen signed in as a data architect, with counts of data objects and recorded lineage edges above the form for recording a lineage flow."),
]


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
    "build-your-model-from-your-tools": (
        "the connection step happens at sign-up in a new organisation, so there is "
        "no seeded screen to photograph yet; capture it from a fresh sign-up"
    ),
    "answer-sources": (
        "shown inside the AI chat answer, which the home page already photographs; "
        "a module-specific capture is owed"
    ),
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
    "digital-twin": (
        "no seeded demonstration organisation or browser available when the "
        "page was written to capture Ask with an impact answer and the "
        "provenance drawer open, the twin map, or model history with a "
        "seeded change -- needs a real capture run, not a placeholder"
    ),
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
    "role": "roles",
    "connector": "connectors",
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
    "role": "/roles",
    "connector": "/connectors",
}

_md = markdown.Markdown(extensions=["extra"])

# Tags and attributes produced by standard Markdown (plus extra extension).
# Any HTML tag or attribute not in these lists is stripped by bleach.
_ALLOWED_TAGS = {
    "a", "abbr", "acronym", "b", "blockquote", "br", "code", "em",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li",
    "ol", "p", "pre", "strong", "table", "tbody", "td", "th",
    "thead", "tr", "ul",
    # The captured-screenshot figure a content page may carry inline: the same
    # figure/picture/img markup public/_partials/_page_media.html renders for a module page.
    "figure", "figcaption", "picture", "source",
}
_ALLOWED_ATTRS = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title", "width", "height", "loading", "decoding", "class"],
    "figure": ["class"],
    "figcaption": ["class"],
    "source": ["media", "srcset", "width", "height"],
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

        True for a HOLD-verdict page (``HELD_PAGE_URLS`` -- a hold with no
        front-matter mechanism of its own, e.g. a site page like /docs) and
        for any page whose own front matter sets an explicit ``state`` other
        than ``"on_main"`` -- ``missing``, ``briefed``, ``in_review``,
        ``held`` and ``not_planned`` are all the same "not ready to
        advertise" case from this property's point of view, so a page is
        trusted to say so of itself rather than needing a second,
        hand-maintained list entry kept in sync by hand. A page with no
        ``state`` field at all (most site/vs/legal pages) is not held by
        this half of the check -- only ``HELD_PAGE_URLS`` can hold those.

        This was two checks before (``HELD_PAGE_URLS`` plus a literal
        ``state == "not_planned"`` match): a page whose state had moved on
        to ``on_main`` could still sit in ``HELD_PAGE_URLS`` from before it
        was finished, with nothing forcing the two back into agreement
        (found live: /use-cases/import-from-jira-and-github and
        /modules/industry-apqc, both on_main, both still noindexed). One
        check, with the page's own state as the only thing that can say
        "ready", removes that drift instead of auditing it by hand again.

        A MERGE-verdict page (``MERGED_PAGES``) is a different case --
        its own URL 301s to a parent instead of rendering at all -- so it
        is not folded into this property; see load_feed_pages(), which
        checks both ``is_held`` and ``MERGED_PAGES`` separately.
        """
        state = self.front_matter.get("state")
        return self.url in HELD_PAGE_URLS or (state is not None and state != "on_main")

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
# The four role-page captures are 2x as well (capture_role in the same script).
TWO_X_ROLE_IMAGES = frozenset({
    "enterprise-architects", "cto-and-cio", "it-portfolio-managers", "solution-architects",
    "business-architects", "security-architects", "data-architects",
    "chief-architects", "scale-up-cto-and-vp-engineering",
})


# More real screens for each role page, from the captures already committed for the
# product's own pages (app/static/img/modules). Each role page shows its own hero screen
# plus these, so a visitor sees the role's job on more than one screen. Entry:
# (image file under static/img, density, frame label, caption, alt text).
ROLE_MORE_SCREENS: dict[str, list[tuple[str, int, str, str, str]]] = {
    "enterprise-architects": [
        ("modules/arb.webp", 2, "Architecture Review Board",
         "The review board dashboard: reviews, how many are pending, the approval rate and the average cycle time.",
         "Screenshot of the Architecture Review Board with total reviews, pending reviews, approval rate and cycle time above a chart of review status."),
        ("modules/architecture-model.webp", 1, "Architecture model",
         "The architecture model: every ArchiMate element in a layer, counted and filterable.",
         "Screenshot of the architecture model for one ArchiMate layer, with element counts, filters and a table of elements and their relationships."),
    ],
    "solution-architects": [
        ("modules/solutions.webp", 1, "Solutions",
         "The solutions list: each design with its phase, readiness and next action.",
         "Screenshot of the All Solutions screen with counts of solutions by readiness and a list of solutions with phase, maturity and next action."),
        ("modules/arb.webp", 2, "Architecture Review Board",
         "The review board dashboard a design goes to for a decision.",
         "Screenshot of the Architecture Review Board with total reviews, pending reviews, approval rate and cycle time above a chart of review status."),
    ],
    "cto-and-cio": [
        ("modules/portfolio.webp", 1, "Portfolio",
         "The portfolio: initiatives with approved budget, spend to date, health and sponsor.",
         "Screenshot of the portfolio screen with initiative, budget and spend totals above a table of initiatives with phase, budget, spend, variance and sponsor."),
        ("modules/business-case.webp", 1, "Business cases",
         "Business cases: options, costs and expected return for each investment.",
         "Screenshot of the Business Cases list with each case's status, total cost of ownership, return on investment and last update."),
    ],
    "it-portfolio-managers": [
        ("modules/duplicate-detection.webp", 1, "Duplicate detection",
         "Duplicate detection: groups of applications that do the same job, with the estimated saving.",
         "Screenshot of the Duplicate Detection screen with duplicate groups, estimated annual saving and priority counts above a filterable list of groups."),
        ("modules/applications.webp", 2, "Applications",
         "The application portfolio: lifecycle, owner and capability for every application.",
         "Screenshot of the Applications screen with active portfolio counts, a data quality notice and a table of applications with type, lifecycle status and capability."),
    ],
    "chief-architects": [
        ("modules/arb.webp", 2, "Architecture Review Board",
         "The review board dashboard: total reviews, pending reviews, approval rate and cycle time.",
         "Screenshot of the Architecture Review Board with total reviews, pending reviews, approval rate and cycle time above a chart of review status."),
    ],
    "scale-up-cto-and-vp-engineering": [
        ("modules/applications.webp", 2, "Applications",
         "The portfolio the import fills, with how many applications have a named owner and a vendor.",
         "Screenshot of the Applications screen with active portfolio counts, a data quality notice about owners and a table of applications with lifecycle status."),
    ],
    "business-architects": [
        ("modules/projects.webp", 1, "Work packages",
         "Work packages: the changes that close a gap, with status, priority and progress.",
         "Screenshot of the Work Packages screen with a table of packages showing status, priority, a progress bar and due date."),
    ],
    "security-architects": [
        ("modules/compliance-frameworks.webp", 1, "Regulatory compliance",
         "Regulatory compliance: the frameworks in scope and the controls mapped to each.",
         "Screenshot of the Regulatory Compliance screen with active frameworks and controls counts above a table of frameworks with jurisdiction and enforcement."),
    ],
    "data-architects": [
        ("modules/architecture-model.webp", 1, "Architecture model",
         "The architecture model: elements by layer, including the data objects lineage is recorded between.",
         "Screenshot of the architecture model for one ArchiMate layer, with element counts, filters and a table of elements and their relationships."),
    ],
}


def get_role_more_screens(page: "PublicPage") -> list[dict[str, Any]]:
    """The extra real screens a role page shows below its hero (ROLE_MORE_SCREENS).

    Only files that exist on disk are returned, so a missing capture can never ship a
    broken <img>."""
    if page.page_family != "role" or page.front_matter.get("capture_status") != "live":
        return []
    from PIL import Image

    out = []
    for rel, density, label, caption, alt in ROLE_MORE_SCREENS.get(page.slug, []):
        path = STATIC_ROOT / "img" / rel
        if not path.is_file():
            continue
        with Image.open(path) as im:
            width, height = im.size[0] // density, im.size[1] // density
        out.append({"url": f"/static/img/{rel}", "width": width, "height": height,
                    "frame_label": f"{label} \u00b7 Demonstration company", "caption": caption, "alt": alt})
    return out


def _screenshot_density(slug: str, page_family: str) -> int:
    if page_family == "module" and slug in TWO_X_MODULE_IMAGES:
        return 2
    if page_family == "role" and slug in TWO_X_ROLE_IMAGES:
        return 2
    return 1


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
    elif page.page_family == "role":
        registry = ROLE_CAPTURES
        static_dir = IMG_ROLES_DIR
        url_prefix = "/static/img/roles"
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
        "frame_label": f"{ROLE_FRAME_LABELS.get(page.slug, 'Ask')} \u00b7 Demonstration company"
        if page.page_family == "role" else None,
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


_LEGAL_BLOCK = re.compile(
    r"\[\[if legal-pages\]\](.*?)(?:\[\[else\]\](.*?))?\[\[/if\]\]", re.DOTALL
)


def _apply_legal_blocks(body_md: str) -> str:
    """Keep the first branch of ``[[if legal-pages]]a[[else]]b[[/if]]`` when
    the held legal pages are live, otherwise the ``else`` branch (or nothing),
    so a page never names a legal page that answers 404."""
    from app.services.legal_pages import legal_pages_enabled

    live = legal_pages_enabled()
    return _LEGAL_BLOCK.sub(
        lambda m: (m.group(1) if live else (m.group(2) or "")), body_md
    )


def _load_page(file_path: Path, family: str, slug: str, url: str) -> PublicPage:
    raw = file_path.read_text(encoding="utf-8")
    front_matter, body_md = _parse_front_matter(raw)
    body_md = _apply_legal_blocks(body_md)
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


def _content_fingerprint() -> tuple[tuple[str, int], ...]:
    """Mtime fingerprint of every content file, used to invalidate the
    cached URL-to-page map in ``_pages_by_url()`` below without re-running
    markdown + bleach over every content file on each call."""
    if not CONTENT_ROOT.is_dir():
        return ()
    from app.services.legal_pages import legal_pages_enabled

    # The flag is part of the key: a page's body can differ with it.
    return (("legal-pages", int(legal_pages_enabled())),) + tuple(sorted(
        (str(p), p.stat().st_mtime_ns) for p in CONTENT_ROOT.rglob("*.md")
    ))


@functools.lru_cache(maxsize=1)
def _pages_by_url_for_fingerprint(fingerprint: tuple[tuple[str, int], ...]) -> dict[str, PublicPage]:
    return {p.url: p for p in load_all_pages()}


def _pages_by_url() -> dict[str, PublicPage]:
    """URL-to-page map, cached per process and invalidated whenever a
    content file is added, removed or edited (the fingerprint argument is
    the cache key). ``build_landing()`` calls this instead of rebuilding
    ``load_all_pages()`` -- markdown and bleach over every content file --
    on every landing-page request; a cache miss costs one ``stat()`` per
    content file to compute the fingerprint, not a full reparse.
    """
    return _pages_by_url_for_fingerprint(_content_fingerprint())


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
    elif family == "module" and page.cta != "waiting_list":
        # A module still on the waiting list is a plain page: no
        # SoftwareApplication or offer is emitted for a capability that
        # cannot be used yet.
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
    "role": ("Roles", "/roles"),
    "connector": ("Connectors", "/connectors"),
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


# ── Hubs: /roles, /connectors, /features (feed-built lists) ─────────────────
# One ordered label table per family (front-matter key -> heading), and one
# grouping function every hub shares. A page with a missing or unknown key
# lands in a final "More" group.

ROLE_GROUPS: tuple[tuple[str, str], ...] = (
    ("technology-leadership", "Technology leadership"),
    ("architecture", "Architecture"),
    ("portfolio-procurement-finance", "Portfolio, procurement and finance"),
    ("security-risk-compliance", "Security, risk and compliance"),
    ("owners", "Owners"),
)

CONNECTOR_GROUPS: tuple[tuple[str, str], ...] = (
    ("ai-assistants", "AI assistants"),
    ("sign-in", "Sign-in"),
    ("service-management-and-delivery", "Service management and delivery"),
    ("collaboration", "Collaboration"),
)

# family -> (front-matter key, ordered label table)
_HUB_GROUP_TABLES: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "role": ("role_group", ROLE_GROUPS),
    "connector": ("connector_group", CONNECTOR_GROUPS),
}

# Non-page entries: (heading, target URLs). Link text is each target page's
# own title from load_feed_pages(); a target not in the feed is dropped.
ROLE_HUB_EXTRA_ENTRIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Architecture review board members", (
        "/modules/arb",
        "/use-cases/architecture-review-board",
    )),
    ("IT operations", (
        "/use-cases/what-breaks-and-who-gets-called",
        "/use-cases/what-happens-if-a-supplier-fails",
        "/use-cases/contract-renewals",
    )),
)

CONNECTOR_HUB_EXTRA_ENTRIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("File imports", (
        "/use-cases/import-archimate-model",
        "/modules/architecture-model",
    )),
)


def _title_key(page: PublicPage) -> str:
    return page.title.lower()


def family_hub_groups(
    family: str,
    *,
    group_key: str | None = None,
    labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The one grouping function behind every hub: the feed pages of *family*
    grouped by a front-matter key, pages sorted by title (case-insensitive),
    empty groups omitted.

    Roles and connectors use their own ordered tables, with anything whose
    key is missing or unknown in a final "More" group. /use-cases passes
    ``group_key="segment_id"`` and its segment labels (a mapping): groups in
    sorted key order, an unknown key labelled by itself ("More" when empty).
    """
    pages = [p for p in load_feed_pages() if p.family == family]
    by_key: dict[str, list[PublicPage]] = {}

    if group_key is not None:
        for page in pages:
            by_key.setdefault(str(page.front_matter.get(group_key) or ""), []).append(page)
        mapping = labels or {}
        return [
            {"label": mapping.get(key, key or "More"), "pages": sorted(by_key[key], key=_title_key)}
            for key in sorted(by_key)
        ]

    group_key, table = _HUB_GROUP_TABLES[family]
    known = {key for key, _ in table}
    extra: list[PublicPage] = []
    for page in pages:
        key = str(page.front_matter.get(group_key) or "")
        if key in known:
            by_key.setdefault(key, []).append(page)
        else:
            extra.append(page)
    groups = [
        {"label": label, "pages": sorted(by_key[key], key=_title_key)}
        for key, label in table
        if by_key.get(key)
    ]
    if extra:
        groups.append({"label": "More", "pages": sorted(extra, key=_title_key)})
    return groups


def hub_extra_entries(
    entries: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[dict[str, Any]]:
    """Resolve non-page entries against the feed: only targets that are live
    feed pages are linked, titled with their own page title."""
    by_url = {p.url: p for p in load_feed_pages()}
    resolved = []
    for label, urls in entries:
        pages = [by_url[u] for u in urls if u in by_url]
        if pages:
            resolved.append({"label": label, "pages": pages})
    return resolved


def role_siblings(page: PublicPage) -> list[dict[str, Any]]:
    """The "Other roles" strip for a role page: every other role-family feed
    page (sorted by title) as one group, then the non-page role entries as
    further groups. Never the page itself."""
    others = sorted(
        (p for p in load_feed_pages() if p.family == "role" and p.url != page.url),
        key=_title_key,
    )
    groups = hub_extra_entries(ROLE_HUB_EXTRA_ENTRIES)
    if others:
        groups.insert(0, {"label": "", "pages": others})
    return groups


def build_hub_jsonld(
    name: str,
    path: str,
    description: str | None,
    pages: list[PublicPage],
    body_html: str | None = None,
) -> str:
    """Structured data for a hub: CollectionPage with an ItemList of every
    linked page, a BreadcrumbList (Home > hub) and an FAQPage only when the
    hub's own intro has a real FAQ section. One @graph, one script block."""
    site_url = SITE_URL
    collection: dict[str, Any] = {
        "@type": "CollectionPage",
        "url": f"{site_url}{path}",
        "name": name,
    }
    if description:
        collection["description"] = description
    collection["mainEntity"] = {
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": position,
                "url": f"{site_url}{page.url}",
                "name": page.title,
            }
            for position, page in enumerate(pages, start=1)
        ],
    }
    nodes: list[dict[str, Any]] = [
        collection,
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{site_url}/"},
                {"@type": "ListItem", "position": 2, "name": name, "item": f"{site_url}{path}"},
            ],
        },
    ]
    if body_html:
        faq_questions = _extract_faq_questions(body_html)
        if faq_questions:
            nodes.append({
                "@type": "FAQPage",
                "url": f"{site_url}{path}",
                "mainEntity": faq_questions,
            })
    ld = {"@context": "https://schema.org", "@graph": nodes}
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


# ── Landing layout (module, use-case, comparison and role pages) ──────────────────
# These four families render as a landing page (hero, alternating section
# bands, link lists as cards, a closing action band) instead of one prose
# column; every other family keeps the document layout. Keyed on
# ``page.family`` (the content directory), never ``page.page_family``, so
# front matter cannot switch a legal or site page into it.

LANDING_FAMILIES: frozenset[str] = frozenset({"module", "function-per-segment", "comparison", "role"})

# The one segment-label table: the /use-cases index groups by it and a
# use-case page's hero names its segment from it.
USE_CASE_SEGMENT_LABELS: dict[str, str] = {
    "S1": "Startups",
    "S2": "Scale-ups",
    "S3": "Enterprise architecture teams",
    "S4": "Services and operations",
}

# The one icon table: a page's own ``icon:`` front matter wins; a family with
# no per-page icon falls back here. Lucide names, checked against the vendored
# v0.344.0 bundle (static/vendor/lucide.min.js).
_FAMILY_DEFAULT_ICON: dict[str, str] = {
    "module": "layout-grid",
    "function-per-segment": "help-circle",
    "comparison": "scale",
}

_ICON_NAME_RE = re.compile(r"[a-z0-9-]+")
_VOID_ELEMENTS = frozenset({"br", "hr", "img"})


@dataclass
class LandingCard:
    url: str
    title_html: Markup
    description: str | None
    icon: str


@dataclass
class LandingSection:
    id: str
    heading_html: Markup
    body_html: Markup
    cards: list[LandingCard] | None = None
    # "callout" (a use-case page's own "The question" -- rendered as a
    # left-ruled attention box, B-03) or "figure_band" (its "What Entelim
    # answers" -- rendered beside the page's screenshot/recording when one
    # exists, text-only otherwise, B-02's band shape). "default" for every
    # other section on every page family, which keeps the plain prose/card
    # band unchanged. See _SECTION_VARIANT_HEADINGS below.
    variant: str = "default"


# Use-case pages only (checked against page.family, never page.page_family,
# same guard build_landing() uses for LANDING_FAMILIES): the exact, lowercased
# plain-text heading that turns a section into the callout or figure-band
# variant. A page whose second heading reads "What Entelim is building" (not
# yet live) or something else entirely (e.g. the one longer FAQ-style use
# case with no "The question" heading at all) simply keeps the default prose
# band -- this table does not require every use-case file to share the same
# shape, only recognises the two headings that do.
_SECTION_VARIANT_HEADINGS: dict[str, str] = {
    "the question": "callout",
    "what entelim answers": "figure_band",
}


@dataclass
class Landing:
    title: str
    icon: str
    lead_html: Markup | None
    intro_html: Markup | None
    sections: list[LandingSection]
    breadcrumb: tuple[str, str] | None
    segment_label: str | None
    primary_action: tuple[str, str]
    secondary_action: tuple[str, str] = ("See pricing", "/pricing")


@dataclass
class _Node:
    """One depth-0 node of an HTML fragment: an element (``tag`` set) or a
    run of text (``tag`` None). Offsets index into the parsed string."""

    tag: str | None
    attrs: dict[str, str | None]
    start: int
    end: int
    inner_start: int
    inner_end: int


def _top_level_nodes(fragment: str) -> list[_Node]:
    """Split ``fragment`` into its depth-0 nodes, tracking element depth with
    ``html.parser.HTMLParser``. Void elements (br, hr, img) do not change
    depth, so an ``<h2>`` inside a ``<blockquote>`` or a list is never a
    depth-0 node. Input is bleach output, so tags are balanced."""
    from html.parser import HTMLParser

    line_starts = [0]
    for index, char in enumerate(fragment):
        if char == "\n":
            line_starts.append(index + 1)

    nodes: list[_Node] = []

    class _Splitter(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.depth = 0
            self.open: _Node | None = None
            self.text_start: int | None = None

        def _offset(self) -> int:
            line, col = self.getpos()
            return line_starts[line - 1] + col

        def _close_text(self, at: int) -> None:
            if self.text_start is not None:
                nodes.append(_Node(None, {}, self.text_start, at, self.text_start, at))
                self.text_start = None

        def _text(self) -> None:
            if self.depth == 0 and self.text_start is None:
                self.text_start = self._offset()

        def handle_data(self, data: str) -> None:
            self._text()

        def handle_entityref(self, name: str) -> None:
            self._text()

        def handle_charref(self, name: str) -> None:
            self._text()

        def handle_starttag(self, tag: str, attrs: list) -> None:
            start = self._offset()
            starttag = self.get_starttag_text() or ""
            if self.depth == 0:
                self._close_text(start)
                if tag in _VOID_ELEMENTS:
                    end = start + len(starttag)
                    nodes.append(_Node(tag, dict(attrs), start, end, end, end))
                    return
                self.open = _Node(tag, dict(attrs), start, -1, start + len(starttag), -1)
            if tag not in _VOID_ELEMENTS:
                self.depth += 1

        def handle_startendtag(self, tag: str, attrs: list) -> None:
            start = self._offset()
            if self.depth == 0:
                self._close_text(start)
                end = start + len(self.get_starttag_text() or "")
                nodes.append(_Node(tag, dict(attrs), start, end, end, end))

        def handle_endtag(self, tag: str) -> None:
            if tag in _VOID_ELEMENTS:
                return
            start = self._offset()
            self.depth = max(self.depth - 1, 0)
            if self.depth == 0 and self.open is not None:
                node = self.open
                node.inner_end = start
                node.end = fragment.index(">", start) + 1
                nodes.append(node)
                self.open = None

    splitter = _Splitter()
    splitter.feed(fragment)
    splitter.close()
    splitter._close_text(len(fragment))
    if splitter.open is not None:  # unbalanced input: keep the rest whole
        node = splitter.open
        node.end = node.inner_end = len(fragment)
        nodes.append(node)
    return nodes


def _is_blank(fragment: str, node: _Node) -> bool:
    return node.tag is None and not fragment[node.start:node.end].strip()


def _element_nodes(fragment: str, nodes: list[_Node]) -> list[_Node] | None:
    """The element nodes of ``nodes``, or ``None`` when any depth-0 text
    beside them is more than whitespace."""
    elements = []
    for node in nodes:
        if node.tag is None:
            if not _is_blank(fragment, node):
                return None
            continue
        elements.append(node)
    return elements


def _fragment(html: str) -> Markup | None:
    """Sanitize a slice of a page body again (so it is balanced and marked
    trusted in the one place that does that), or ``None`` when empty."""
    html = html.strip()
    if not html:
        return None
    return _sanitize_html(html)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _plain_text(html: str) -> str:
    import html as _html

    return " ".join(_html.unescape(re.sub(r"<[^>]+>", "", html)).split())


def page_icon(page: PublicPage) -> str:
    """The Lucide icon name for ``page``: its own ``icon`` front matter when
    that is a non-empty string of lowercase letters, digits and hyphens, else
    its family's default, else ``"link"``."""
    own = page.front_matter.get("icon")
    if isinstance(own, str) and _ICON_NAME_RE.fullmatch(own):
        return own
    return _FAMILY_DEFAULT_ICON.get(page.family, "link")


def _link_list_cards(body: str, pages_by_url: dict[str, PublicPage]) -> list[LandingCard] | None:
    """Cards for a section whose body is exactly one ``<ul>`` of items that
    each hold one internal link and nothing else; ``None`` otherwise."""
    elements = _element_nodes(body, _top_level_nodes(body))
    if not elements or len(elements) != 1 or elements[0].tag != "ul":
        return None
    ul = elements[0]
    ul_inner = body[ul.inner_start:ul.inner_end]
    items = _element_nodes(ul_inner, _top_level_nodes(ul_inner))
    if not items or any(item.tag != "li" for item in items):
        return None

    cards: list[LandingCard] = []
    for item in items:
        li_inner = ul_inner[item.inner_start:item.inner_end]
        links = _element_nodes(li_inner, _top_level_nodes(li_inner))
        if not links or len(links) != 1 or links[0].tag != "a":
            return None
        link = links[0]
        href = link.attrs.get("href") or ""
        # Internal means a site path: "//host" is protocol-relative, external.
        if not href.startswith("/") or href.startswith("//"):
            return None
        title_html = _fragment(li_inner[link.inner_start:link.inner_end])
        if title_html is None:
            return None
        target = pages_by_url.get(href.split("#", 1)[0].split("?", 1)[0])
        cards.append(LandingCard(
            url=href,
            title_html=title_html,
            description=target.description if target is not None else None,
            icon=page_icon(target) if target is not None else "link",
        ))
    return cards


def build_landing(page: PublicPage) -> Landing | None:
    """The landing-page view of ``page`` for public/_partials/_landing.html,
    or ``None`` when ``page.family`` keeps the document layout.

    Splits the sanitized body at its depth-0 ``<h1>`` and ``<h2>`` elements:
    the first paragraph after the ``<h1>`` is the hero lead, anything else
    before the first ``<h2>`` is the intro, and each ``<h2>`` starts a
    section. A section whose body is only a list of internal links becomes a
    card grid, each card described and iconed from its target page. Every
    fragment passes through ``_sanitize_html()`` again before it is stored.
    """
    if page.family not in LANDING_FAMILIES:
        return None

    from flask import url_for

    body = str(page.body_html)
    nodes = _top_level_nodes(body)

    h1_index = next((i for i, n in enumerate(nodes) if n.tag == "h1"), None)
    before_h1 = ""
    if h1_index is not None:
        before_h1 = body[:nodes[h1_index].start]
        rest = nodes[h1_index + 1:]
        cursor = nodes[h1_index].end
    else:
        rest = nodes
        cursor = 0

    lead_html = None
    first_element = next((n for n in rest if not _is_blank(body, n)), None)
    if first_element is not None and first_element.tag == "p":
        inner = body[first_element.inner_start:first_element.inner_end]
        inner_elements = _element_nodes(inner, _top_level_nodes(inner))
        if inner_elements and len(inner_elements) == 1 and inner_elements[0].tag in ("em", "strong"):
            only = inner_elements[0]
            inner = inner[only.inner_start:only.inner_end]
        lead_html = _fragment(inner)
        cursor = first_element.end

    h2_nodes = [n for n in rest if n.tag == "h2"]
    intro_end = h2_nodes[0].start if h2_nodes else len(body)
    intro_html = _fragment(before_h1 + body[cursor:intro_end])

    pages_by_url: dict[str, PublicPage] | None = None
    sections: list[LandingSection] = []
    used_ids: set[str] = set()
    for index, h2 in enumerate(h2_nodes):
        heading = body[h2.inner_start:h2.inner_end]
        heading_text = _plain_text(heading)
        base_id = h2.attrs.get("id") or _slugify(heading_text) or "section"
        section_id, suffix = base_id, 2
        while section_id in used_ids:
            section_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(section_id)

        section_end = h2_nodes[index + 1].start if index + 1 < len(h2_nodes) else len(body)
        section_body = body[h2.end:section_end]
        cards = None
        if "<ul" in section_body:
            if pages_by_url is None:
                pages_by_url = _pages_by_url()
            cards = _link_list_cards(section_body.strip(), pages_by_url)
        variant = "default"
        if page.family == "function-per-segment":
            variant = _SECTION_VARIANT_HEADINGS.get(heading_text.strip().lower(), "default")
        sections.append(LandingSection(
            id=section_id,
            heading_html=_fragment(heading) or Markup(""),
            body_html=_fragment(section_body) or Markup(""),
            cards=cards,
            variant=variant,
        ))

    parent = _BREADCRUMB_PARENTS.get(page.family)
    segment_label = None
    if page.family == "function-per-segment":
        segment_label = USE_CASE_SEGMENT_LABELS.get(page.front_matter.get("segment_id", ""))

    if page.cta == "waiting_list":
        primary_action = ("Tell us you need this", "#feature-interest")
        # A page for something not built yet never points at pricing: its
        # second action is a conversation, not a plan.
        secondary_action = ("Talk to us", "/contact")
    else:
        primary_action = ("Start free", url_for("account.register"))
        secondary_action = ("See pricing", "/pricing")

    return Landing(
        title=page.title,
        icon=page_icon(page),
        lead_html=lead_html,
        intro_html=intro_html,
        sections=sections,
        breadcrumb=parent,
        segment_label=segment_label,
        primary_action=primary_action,
        secondary_action=secondary_action,
    )