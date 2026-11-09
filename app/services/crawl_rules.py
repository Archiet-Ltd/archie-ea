"""robots.txt and the sitemap, worked out from the routes the app serves.

Crawlers may read everything except what a signed-in person or a program uses. robots.txt therefore starts with
``Allow: /`` and lists ``Disallow`` only for private areas: a route is private when a signed-out visitor is sent to
sign-in or refused (401, 403), and every API, admin and API-documentation address is private whatever it answers.
A public route is allowed without being listed anywhere, so a public page added later is crawlable the day it
exists. The sitemap stays the list of pages we want indexed; it does not decide what is allowed.

Both files are built once per process and kept until what they are built from changes (a route, a content file, a switch).
"""

from __future__ import annotations

import re
import threading
from urllib.parse import urlsplit

from flask import current_app, url_for
from werkzeug.exceptions import HTTPException

# Crawler files and assets that must always be readable.
CRAWLER_FILES = ("/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt", "/favicon.ico", "/static/css/site.css")
# Areas that are private whatever a signed-out request to them answers: programs, administrators and API documentation.
TOOLING_PREFIXES = ("/api/", "/admin/", "/apidocs", "/apispec", "/flasgger_static/")

_lock = threading.Lock()


def _login_path() -> str | None:
    view = getattr(getattr(current_app, "login_manager", None), "login_view", None)
    return urlsplit(url_for(view)).path if view else None


def _signed_out_get(rule) -> tuple[str, str | None]:
    """What a signed-out GET of this route does: ``("refused", None)`` for 401/403, ``("redirect", path)``, else ``("open", None)``."""
    path = re.sub(r"<[^>]+>", "1", rule.rule)
    try:
        with current_app.test_request_context(path):
            endpoint, args = current_app.url_map.bind("").match(path)
            response = current_app.make_response(current_app.view_functions[endpoint](**args))
    except HTTPException as error:
        return ("refused", None) if error.code in (401, 403) else ("open", None)
    except Exception:  # a view that cannot answer a made-up address says nothing about who may read it
        return "open", None
    if response.status_code in (401, 403):
        return "refused", None
    if response.status_code in (301, 302, 303, 307, 308):
        return "redirect", urlsplit(response.headers.get("Location", "")).path
    return "open", None


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

    A route is private when a signed-out visitor is refused or sent to sign-in, or when it only redirects to a private route.
    """
    adapter = current_app.url_map.bind("")

    def rule_for(path: str):
        try:
            return adapter.match(path, return_rule=True)[0]
        except Exception:
            return None

    # a page we publish cannot be private: skip probing it (the home page and the content hubs are slow to render)
    published_rules = {id(rule) for rule in map(rule_for, published) if rule is not None}
    login_path = _login_path()
    answers = {}
    for rule in current_app.url_map.iter_rules():
        if "GET" not in rule.methods or (rule.endpoint.endswith("static") and not rule.rule.startswith(TOOLING_PREFIXES)):
            continue
        if id(rule) in published_rules:
            continue
        answers[id(rule)] = (rule, *_signed_out_get(rule))

    def private(entry, depth=0) -> bool:
        rule, kind, target = entry
        if rule.rule.startswith(TOOLING_PREFIXES) or kind == "refused":
            return True
        if kind != "redirect" or not target:
            return False
        if target == login_path:
            return True
        destination = rule_for(target)
        return depth < 3 and destination is not None and id(destination) in answers and private(answers[id(destination)], depth + 1)

    return [entry[0] for entry in answers.values() if private(entry)]


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


def _covers(pattern: str, path: str) -> bool:
    regex = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
    return re.fullmatch(regex, path) is not None if pattern.endswith("$") else re.match(regex, path) is not None


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

    from app.services.public_pages import SITE_URL

    priority = "<priority>1.0</priority>" if path == "/" else ""
    return f"  <url><loc>{SITE_URL}{escape(path)}</loc>{priority}</url>"


def _build_robots() -> str:
    from app.services.public_pages import SITE_URL

    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {pattern}" for pattern in disallowed_patterns()]
    lines += ["", f"Sitemap: {SITE_URL}/sitemap.xml", ""]
    return "\n".join(lines)


def _build_sitemap() -> str:
    from app.services.public_pages import feed_page_paths

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(_sitemap_url(path) for path in feed_page_paths())
        + "\n</urlset>"
    )


def _cached(name: str, build) -> str:
    """A file built once per process and rebuilt only when what it is built from changes: a route, a content file,
    the legal-pages switch or an early-access page."""
    from app.release_waves.routes import early_access_paths
    from app.services.legal_pages import legal_pages_enabled
    from app.services.public_pages import _content_fingerprint

    key = (len(list(current_app.url_map.iter_rules())), _content_fingerprint(), legal_pages_enabled(),
           tuple(early_access_paths()))
    with _lock:
        store = current_app.extensions.setdefault("crawl_rules", {})
        held = store.get(name)
        if held is None or held[0] != key:
            held = store[name] = (key, build())
        return held[1]


def robots_text() -> str:
    return _cached("robots", _build_robots)


def sitemap_text() -> str:
    return _cached("sitemap", _build_sitemap)
