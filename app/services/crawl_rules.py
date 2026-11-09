"""robots.txt, worked out from the routes and the public-site registry.

Crawlers may read everything except what a signed-in person or a program uses. The public pages are the ones the
site registry (header, footer), the sitemap list and the early-access pages name; every other route the app serves is
signed-in or private, and robots.txt disallows it. A public page added to the registry, to the content folders or to
the early-access list is therefore crawlable the day it exists, and a new signed-in route is blocked without anyone
having to remember a line for it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import current_app
from werkzeug.exceptions import HTTPException
from werkzeug.routing import RequestRedirect

# Crawler files and assets that are not pages in the registry but must always be readable.
CRAWLER_FILES = ("/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt", "/favicon.ico", "/static/css/site.css")
SITE_ORIGIN = "https://entelim.org"


def public_paths() -> list[str]:
    """Every public page path: the home page, the sitemap list, old addresses that redirect to a page, and each header and footer link."""
    from app.services.public_pages import MERGED_PAGES, feed_page_paths
    from app.services.site_registry import registry_links

    # an old address that redirects to a page stays readable, so a crawler can follow it to the page
    paths = ["/"] + feed_page_paths() + list(MERGED_PAGES) + list(CRAWLER_FILES)
    for link in registry_links():
        href = link["href"]
        if href.startswith("/"):
            paths.append(urlsplit(href).path)
    return list(dict.fromkeys(paths))


def _matched_rule(adapter, path: str):
    for _ in range(3):
        try:
            return adapter.match(path, return_rule=True)[0]
        except RequestRedirect as redirect:
            path = urlsplit(redirect.new_url).path
        except HTTPException:
            return None
    return None


def _static_prefix(rule) -> tuple[str, bool]:
    """The literal start of a rule, and whether the whole rule is literal (no variable part)."""
    text = rule.rule
    cut = text.find("<")
    return (text, True) if cut < 0 else (text[:cut], False)


def disallowed_patterns() -> list[str]:
    """The robots.txt patterns for every route that is not a public page, shortest and fewest that cover them."""
    adapter = current_app.url_map.bind("")
    public_paths_list = public_paths()
    public_rules = {id(rule) for rule in (_matched_rule(adapter, p) for p in public_paths_list) if rule is not None}
    patterns: set[str] = set()
    for rule in current_app.url_map.iter_rules():
        if id(rule) in public_rules or rule.endpoint.endswith("static"):
            continue
        prefix, literal = _static_prefix(rule)
        if len(prefix) < 2 or prefix == "/":
            continue
        if literal:
            pattern = prefix.rstrip("/") + "$" if prefix.rstrip("/") else prefix
            options = [pattern, prefix, prefix + "$"] if prefix.endswith("/") else [pattern]
        else:
            if not prefix.endswith("/"):
                prefix = prefix.rsplit("/", 1)[0] + "/"
            if len(prefix) < 2:
                continue
            options = [prefix]
        for option in options:
            patterns.add(option)
    # a section with no public page in it (admin, api, applications ...) is blocked whole, on one pair of lines
    public_sections = {path.split("/")[1] for path in public_paths_list if path != "/"}
    sections = {p.split("/")[1].rstrip("$") for p in patterns}
    for section in sections - public_sections:
        patterns = {p for p in patterns if p.split("/")[1].rstrip("$") != section}
        patterns |= {f"/{section}/", f"/{section}$"}
    # a route's pattern must never block a public page: drop it when it would
    safe = {p for p in patterns if not any(_covers(p, path) for path in public_paths_list)}
    # a literal route that a shorter pattern already blocks needs no line of its own
    prefixes = [p for p in safe if not p.endswith("$")]
    kept = [
        p for p in safe
        if not any(q != p and _covers(q, p.rstrip("$")) for q in prefixes)
    ]
    return sorted(kept)


def _covers(pattern: str, path: str) -> bool:
    if pattern.endswith("$"):
        return path == pattern[:-1]
    return path.startswith(pattern)


def robots_text() -> str:
    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {pattern}" for pattern in disallowed_patterns()]
    lines += ["", f"Sitemap: {SITE_ORIGIN}/sitemap.xml", ""]
    return "\n".join(lines)
