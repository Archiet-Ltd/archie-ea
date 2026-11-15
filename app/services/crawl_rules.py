"""robots.txt and the sitemap, worked out from the routes the app serves.

Crawlers may read everything except what a signed-in person or a program uses. robots.txt therefore starts with
``Allow: /`` and lists ``Disallow`` only for private areas: a route is private when a signed-out visitor is sent to
sign-in or refused (401, 403), and every API, admin and API-documentation address is private whatever it answers.
A public route is allowed without being listed anywhere, so a public page added later is crawlable the day it
exists. The sitemap stays the list of pages we want indexed; it does not decide what is allowed.

Both files are built once per process and kept until what they are built from changes (a route, a content file, a switch).
"""

from __future__ import annotations

import functools
import re
import threading
from urllib.parse import urlsplit

from flask import current_app

from app.services.site_registry import as_signed_out_visitor

# Crawler files and assets that must always be readable.
CRAWLER_FILES = ("/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt", "/favicon.ico", "/static/css/site.css")
# Areas that are private whatever a signed-out request to them answers: programs, administrators and API documentation.
TOOLING_PREFIXES = ("/api/", "/admin/", "/apidocs", "/apispec", "/flasgger_static/")
# Addresses that record a click and then forward the visitor: a crawler following one would count as a visitor.
TRACKING_PREFIXES = ("/t/",)

# A route carries a secret token when one of its path segments is named like one (token, reset_token, share_secret,
# api_key ...). Read from the URL map, so a new emailed or shared link is covered the day its route exists.
_TOKEN_SEGMENT = re.compile(r"<(?:[^>:]+:)?(?:\w*_)?(?:token|secret|api_key|access_key|share_key)>")

_lock = threading.Lock()

# Wrappers a view can carry, told apart by where their code lives and what they are called (the name a wrapper
# reports is copied from the view it wraps, so it says nothing). Reading these never runs the view.
# A wrapper that refuses a signed-out visitor makes the route private.
_GUARDS = {
    ("flask_login/utils.py", "decorated_view"),
    ("app/_decorators_base.py", "decorated_function"),
    ("app/decorators/requires_role.py", "decorated_function"),
    ("app/decorators/adm_permissions.py", "wrapper"),
    ("app/core/auth/decorators.py", "decorated_function"),
    ("app/security/rbac.py", "wrapper"),
    ("app/security/import_decorators.py", "decorated_function"),
    ("app/modules/oauth_provider/scopes.py", "wrapped"),
    ("app/modules/transformation_room/http.py", "wrapped"),
    ("app/modules/account/mfa_flow.py", "wrapped"),
    ("app/modules/admin/connector_routes.py", "decorated"),
    ("app/modules/solutions_strategic/v2/routes/journey_v2_routes.py", "decorated"),
    ("app/modules/solutions_strategic/v2/routes/governance_api_routes.py", "decorated_function"),
    ("app/modules/solutions_strategic/v2/routes/strategic_risks_hardened.py", "decorated_function"),
    ("app/modules/architecture_assistant/routes/wizard_ai_routes.py", "decorated"),
    ("app/routes/sidebar_api_hardened.py", "decorated_function"),
    ("app/modules/tech_radar/routes.py", "wrapper"),
}
# Wrappers that only time, audit, limit, cache or document a view.
_NEUTRAL = {
    ("app/decorators/audit.py", "wrapper"),
    ("app/services/rate_limiter.py", "wrapped"),
    ("app/utils/deprecation.py", "decorated_function"),
    ("app/extensions/cache.py", "wrapper"),
    ("flask_restx/api.py", "wrapper"),
}
# Blueprint-level request hooks that do not decide who may read a page.
_NEUTRAL_HOOKS = (
    "app.core.compat.mark_blueprint_guardrailed",
    "app.api.v1.add_api_version_header",
    "app.application_mgmt.routes.before_request",
    "app.modules.architecture.routes.arb_routes._default_deny_unauthorized_arb_writes",
    "app.modules.applications.routes._default_deny_unauthorized_writes",
    "app.implementation_planning._check_feature_flag",
)


def _site(function) -> tuple[str, str] | None:
    code = getattr(function, "__code__", None)
    if code is None:
        return None
    path = code.co_filename.replace("\\", "/")
    for marker in ("/app/", "/site-packages/"):
        if marker in path:
            path = (marker[1:] if marker == "/app/" else "") + path.split(marker, 1)[1]
            break
    return path, code.co_name


def _wrappers(view) -> list:
    """Every wrapper around a view function, outermost first, including those on a class-based view's methods."""
    found, function = [], view
    while function is not None:
        if getattr(function, "__wrapped__", None) is not None:
            found.append(function)
        owner = getattr(function, "view_class", None)
        if owner is not None:  # a class-based view's decorators sit on its methods
            for method in ("get", "dispatch_request"):
                found.extend(_wrappers(getattr(owner, method, None)))
        function = getattr(function, "__wrapped__", None)
    return found


def _wrapper_guards(wrapper) -> bool | None:
    """True when this wrapper refuses a signed-out visitor, False when it does not, None when it cannot be read."""
    site = _site(wrapper)
    if site is None:
        return None
    if site[0].endswith("app/core/decorators.py") and site[1] == "wrapper":
        # guarded_route keeps its settings as closure values; reading them runs nothing
        settings = dict(zip(wrapper.__code__.co_freevars, (cell.cell_contents for cell in wrapper.__closure__ or ())))
        if "auth" in settings:
            return bool(settings.get("auth") or settings.get("roles") or settings.get("permission"))
        return False  # timed_route and validate_body do not check who is asking
    if any(site[0].endswith(path) and site[1] == name for path, name in _GUARDS):
        return True
    if any(site[0].endswith(path) and site[1] == name for path, name in _NEUTRAL):
        return False
    return None


# Old addresses whose view only redirects: the redirect target lives in the view body, which is never read, so each
# is declared here. An old address is private exactly when the page it forwards to is.
REDIRECT_SHIMS = {
    "main.vendors_redirect": "/applications/vendors",
    "main.vendor_templates_redirect": "/architecture/vendor-templates",
    "main.roadmaps_redirect": "/genome/roadmap/",
    "main.agentic_gaps_retired": "/dashboard/overview",
    "capabilities_redirect": "/capability-map",
    "_journey_v2_redirect": "/architecture-journey/",
}


# Steps of signing in: a crawler has no use for them, and each one only forwards a signed-out visitor to the sign-in page.
SIGN_IN_STEPS = {"main.login_redirect", "account.mfa_challenge"}


def refuses_visitors_when(check):
    """Mark a view that checks sign-in inside its own body, and only while ``check()`` is true.

    The mark changes nothing about the view. robots.txt reads it, so such a page is disallowed while it sends a
    signed-out visitor to sign-in, and allowed while it shows everyone a public page."""

    def mark(view):
        view.refuses_visitors_when = check
        return view

    return mark


def _refuses_by_condition(view) -> bool:
    function = view
    while function is not None:
        check = getattr(function, "refuses_visitors_when", None)
        if check is not None:
            return bool(check())
        function = getattr(function, "__wrapped__", None)
    return False


def _blueprint_hooks_unreadable(blueprint: str) -> bool:
    hooks = current_app.before_request_funcs.get(blueprint, [])
    return any(not f"{hook.__module__}.{hook.__qualname__}".startswith(_NEUTRAL_HOOKS) for hook in hooks)


def _is_private(rule) -> bool:
    """Whether a signed-out GET of this route is refused, read from the route's decorators and its blueprint's hooks.

    A guard that cannot be read marks the whole route private by its address: hiding a page from search for a
    release is recoverable, publishing a private one is not."""
    if rule.rule.startswith(TOOLING_PREFIXES + TRACKING_PREFIXES) or carries_secret_token(rule):
        return True
    if rule.endpoint in SIGN_IN_STEPS:
        return True
    if rule.endpoint in REDIRECT_SHIMS:
        try:
            target = current_app.url_map.bind("").match(REDIRECT_SHIMS[rule.endpoint], return_rule=True)[0]
        except Exception:
            return False
        return target.endpoint != rule.endpoint and _is_private(target)
    view = current_app.view_functions[rule.endpoint]
    if _refuses_by_condition(view):
        return True
    verdicts = [_wrapper_guards(wrapper) for wrapper in _wrappers(view)]
    if any(verdict is True for verdict in verdicts):
        return True
    if any(verdict is None for verdict in verdicts):
        return True
    blueprint = rule.endpoint.rpartition(".")[0]
    return bool(blueprint) and _blueprint_hooks_unreadable(blueprint)


def carries_secret_token(rule) -> bool:
    """Whether the route's address holds a secret token (a reset, confirm, invite or share link)."""
    return rule is not None and _TOKEN_SEGMENT.search(rule.rule) is not None


def never_indexed(rule) -> bool:
    """Whether a response from this route is sent with noindex: secret-token addresses and click trackers."""
    return carries_secret_token(rule) or (rule is not None and rule.rule.startswith(TRACKING_PREFIXES))


def published_paths() -> set[str]:
    """The pages we publish: the sitemap list, every link in the public header and footer, and the crawler files."""
    from app.services.public_pages import feed_page_paths
    from app.services.site_registry import registry_links

    published = set(CRAWLER_FILES) | set(feed_page_paths())
    for link in registry_links():
        if link["href"].startswith("/"):
            published.add(urlsplit(link["href"]).path)
    return published


def private_routes(published: set[str]) -> list:
    """Every GET route of the app that is private to signed-in people, programs and administrators.

    Read from the URL map and each view's decorators; no view is called, so building this writes nothing, reaches
    no service and gives the same answer whoever asks."""
    adapter = current_app.url_map.bind("")

    def rule_for(path: str):
        try:
            return adapter.match(path, return_rule=True)[0]
        except Exception:
            return None

    # a page we publish cannot be private, whatever a decorator on it says
    published_rules = {id(rule) for rule in map(rule_for, published) if rule is not None}
    return [
        rule
        for rule in current_app.url_map.iter_rules()
        if "GET" in rule.methods
        and id(rule) not in published_rules
        and not (rule.endpoint.endswith("static") and not rule.rule.startswith(TOOLING_PREFIXES))
        and _is_private(rule)
    ]


def _options(rule_text: str) -> list[list[str]]:
    """Disallow lines for one route, widest first: its literal start (a query string is covered too), then its exact form."""
    cut = rule_text.find("<")
    if cut >= 0:
        prefix = rule_text[:cut]
        prefix = prefix if prefix.endswith("/") else prefix.rsplit("/", 1)[0] + "/"
        return [[prefix], [re.sub(r"<[^>]+>", "*", rule_text)]]
    if rule_text.endswith("/"):
        bare = rule_text.rstrip("/")
        return [[rule_text], [rule_text + "$", rule_text + "?", bare + "$", bare + "?"]]
    return [[rule_text + "$", rule_text + "?"]]


@functools.lru_cache(maxsize=None)
def _matcher(pattern: str):
    regex = re.compile(re.escape(pattern.rstrip("$")).replace(r"\*", ".*"))
    return regex.fullmatch if pattern.endswith("$") else regex.match


def _covers(pattern: str, path: str) -> bool:
    return _matcher(pattern)(path) is not None


def disallowed_patterns() -> list[str]:
    """The robots.txt Disallow values: the fewest lines that cover every private route and no published page."""
    published = published_paths()
    private = private_routes(published)
    private_ids = {id(rule) for rule in private}
    public_literals = set(published)
    public_sections: set[str] = set()
    for rule in current_app.url_map.iter_rules():
        if "GET" not in rule.methods or id(rule) in private_ids or rule.rule == "/":
            continue
        public_sections.add(rule.rule.split("/")[1])
        if "<" not in rule.rule:
            public_literals.add(rule.rule)

    def safe(lines: list[str]) -> bool:
        return not any(_covers(line, path) for line in lines for path in public_literals)

    patterns: set[str] = set()
    for rule in private:
        if len(rule.rule) < 2 or rule.rule.startswith("/<"):
            continue
        for lines in _options(rule.rule):
            if safe(lines):
                patterns.update(lines)
                break
    # a section with no public route in it (admin, api, applications ...) is blocked whole, on one set of lines
    sections = {p.split("/")[1].rstrip("$?") for p in patterns if not p.startswith("/*") and "*" not in p.split("/")[1]}
    for section in sections - public_sections:
        patterns = {p for p in patterns if p.split("/")[1].rstrip("$?") != section}
        patterns |= {f"/{section}/", f"/{section}$", f"/{section}?"}
    prefixes = [p for p in patterns if not p.endswith(("$", "?"))]
    kept = [p for p in patterns if not any(q != p and _covers(q, p.rstrip("$?")) for q in prefixes)]
    return sorted(kept)


def _sitemap_url(path: str) -> str:
    from html import escape

    from app.services.origin import canonical_origin

    base = canonical_origin()
    priority = "<priority>1.0</priority>" if path == "/" else ""
    return f"  <url><loc>{base}{escape(path)}</loc>{priority}</url>"


def _build_robots() -> str:
    from app.services.origin import canonical_origin

    base = canonical_origin()
    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {pattern}" for pattern in disallowed_patterns()]
    lines += ["", f"Sitemap: {base}/sitemap.xml", ""]
    return "\n".join(lines)


def _build_sitemap() -> str:
    from app.services.public_pages import feed_page_paths

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(_sitemap_url(path) for path in feed_page_paths())
        + "\n</urlset>"
    )


def _conditional_refusals() -> tuple[str, ...]:
    """The routes that refuse a visitor only while a switch is on, as they stand now: part of what the files are built from."""
    return tuple(sorted(
        rule.rule for rule in current_app.url_map.iter_rules()
        if _refuses_by_condition(current_app.view_functions[rule.endpoint])
    ))


def _cached(name: str, build) -> str:
    """A file built once per process and rebuilt only when what it is built from changes: a route, a content file,
    the legal-pages switch or an early-access page."""
    from app.release_waves.routes import early_access_paths
    from app.services.legal_pages import legal_pages_enabled
    from app.services.public_pages import _content_fingerprint

    key = (len(list(current_app.url_map.iter_rules())), _content_fingerprint(), legal_pages_enabled(),
           tuple(early_access_paths()), _conditional_refusals())
    with _lock:
        store = current_app.extensions.setdefault("crawl_rules", {})
        held = store.get(name)
        if held is None or held[0] != key:
            with as_signed_out_visitor():
                held = store[name] = (key, build())
        return held[1]


def robots_text() -> str:
    return _cached("robots", _build_robots)


def sitemap_text() -> str:
    return _cached("sitemap", _build_sitemap)


def warm(application) -> None:
    """Build both files once, before the first request: a server calls this when it is ready, so no visitor or
    crawler waits for the content index to be read."""
    with application.test_request_context("/robots.txt"):
        robots_text()
        sitemap_text()
