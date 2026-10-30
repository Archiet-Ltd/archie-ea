"""What a page states outright, read without asking a model.

Contact details, the logo, social profiles and the technologies a page loads are on
the page itself, so they are read here with the confidence their source deserves. That
also keeps an email or phone number out of the text sent to a model provider, which
scrubs both before anything leaves.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

SOCIAL_HOSTS = {
    "linkedin.com": "LinkedIn", "twitter.com": "Twitter/X", "x.com": "Twitter/X", "facebook.com": "Facebook",
    "instagram.com": "Instagram", "github.com": "GitHub", "youtube.com": "YouTube",
}

# (substring of a script address or a generator tag, technology, kind)
TECHNOLOGY_SIGNATURES: Tuple[Tuple[str, str, str], ...] = (
    ("react", "React", "frontend"), ("vue", "Vue", "frontend"), ("angular", "Angular", "frontend"),
    ("svelte", "Svelte", "frontend"), ("jquery", "jQuery", "frontend"), ("bootstrap", "Bootstrap", "frontend"),
    ("/_next/", "Next.js", "frontend"), ("/_nuxt/", "Nuxt", "frontend"),
    ("googletagmanager", "Google Tag Manager", "analytics"), ("google-analytics", "Google Analytics", "analytics"),
    ("gtag/js", "Google Analytics", "analytics"), ("mixpanel", "Mixpanel", "analytics"),
    ("segment.com", "Segment", "analytics"), ("hotjar", "Hotjar", "analytics"), ("plausible", "Plausible", "analytics"),
    ("hubspot", "HubSpot", "marketing"), ("mailchimp", "Mailchimp", "marketing"), ("marketo", "Marketo", "marketing"),
    ("cloudflare", "Cloudflare", "infrastructure"), ("cloudfront", "Amazon CloudFront", "infrastructure"),
    ("fastly", "Fastly", "infrastructure"),
    ("wp-content", "WordPress", "cms"), ("cdn.shopify", "Shopify", "cms"), ("wixstatic", "Wix", "cms"),
    ("squarespace", "Squarespace", "cms"),
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BUSINESS_PATHS = ("about", "team", "product", "solution", "service", "contact", "pricing", "customer",
                   "case", "partner", "integrat", "security", "compliance", "company", "career")


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


_EXAMPLE_TAGS = ["code", "pre", "kbd", "samp"]


def visible_text(html: str, without=()) -> str:
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "template", *without]):
        tag.decompose()
    body = soup.body or soup
    return re.sub(r"\s+", " ", body.get_text(" ")).strip()


def trimmed_html(html: str, limit: int = 60000) -> str:
    """The page for a model: no inline script or style bodies, cut to a size a prompt can hold."""
    soup = soup_of(html)
    for tag in soup.find_all(["script", "style"]):
        if tag.name == "style" or not tag.get("src"):
            tag.string = ""
    text = str(soup)
    return text[:limit]


def _ld_objects(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            found.append(node)
            walk(node.get("@graph"))

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            walk(json.loads(tag.string or tag.get_text() or ""))
        except ValueError:
            continue
    return found


def _kind(node: Dict[str, Any]) -> List[str]:
    kind = node.get("@type")
    return [str(k) for k in (kind if isinstance(kind, list) else [kind])]


def _absolute(base: str, value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("url") or value.get("@id")
    if not isinstance(value, str) or not value.strip():
        return None
    return urljoin(base, value.strip())


def social_platform(url: str) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return SOCIAL_HOSTS.get(host)


_NOT_A_HANDLE = {
    "x.com": {"intent", "share", "home", "i", "search", "hashtag", "explore", "settings", "login", "signup", "tos",
              "privacy", "messages", "notifications", "compose", "status"},
    "github.com": {"features", "sponsors", "orgs", "topics", "about", "pricing", "login", "join", "marketplace",
                   "enterprise", "settings", "collections", "explore", "customer-stories", "readme", "security",
                   "apps", "users", "search", "trending", "events"},
    "instagram.com": {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct"},
    "facebook.com": {"sharer", "sharer.php", "share.php", "share", "dialog", "groups", "events", "watch", "login",
                     "plugins", "policies", "help", "marketplace", "pages", "profile.php", "story.php", "photo",
                     "photos", "hashtag", "people"},
}


def social_profile_root(url: str) -> Optional[Tuple[str, str, str]]:
    """(network, profile address, handle) when the address is a network's profile root, else None.

    A person's profile, a post, a share link, a video, a repository or any page inside one is not an account."""
    platform = social_platform(url)
    if not platform:
        return None
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    segments = [s for s in parts.path.split("/") if s]
    handle = None
    if host == "linkedin.com":
        if len(segments) == 2 and segments[0].lower() in ("company", "school"):
            handle = segments[1]
            segments = [segments[0].lower(), handle]
    elif host == "youtube.com":
        if len(segments) == 1 and segments[0].startswith("@") and len(segments[0]) > 1:
            handle = segments[0][1:]
        elif len(segments) == 2 and segments[0].lower() in ("c", "channel", "user"):
            handle = segments[1]
    else:
        key = "x.com" if host in ("x.com", "twitter.com") else host
        if len(segments) == 1 and segments[0].lower() not in _NOT_A_HANDLE.get(key, set()):
            handle = segments[0]
    if not handle:
        return None
    return platform, "https://%s/%s" % ((parts.hostname or "").lower(), "/".join(segments)), handle


def same_site(url: str, site: str) -> bool:
    """The same host (www. aside) on the same port."""
    def origin(u):
        parts = urlparse(u)
        host = (parts.hostname or "").lower()
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            return None
        return (host[4:] if host.startswith("www.") else host, port)

    mine = origin(url)
    return bool(mine and mine[0]) and mine == origin(site)


def page_links(html: str, base: str, site: str) -> List[str]:
    """Addresses of the site's own pages this page links to, business pages first."""
    seen: Dict[str, int] = {}
    for anchor in soup_of(html).find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urldefrag(urljoin(base, href))[0]
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not same_site(url, site):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|pdf|zip|css|js|ico|xml|mp4)$", parts.path, re.I):
            continue
        rank = 0 if any(word in (parts.path or "").lower() for word in _BUSINESS_PATHS) else 1
        seen.setdefault(url, rank)
    return [u for u, _ in sorted(seen.items(), key=lambda item: item[1])]


def detect_technologies(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    sources = [tag["src"].lower() for tag in soup.find_all("script", src=True)]
    sources += [tag["href"].lower() for tag in soup.find_all("link", href=True)]
    found: Dict[str, Dict[str, Any]] = {}
    for needle, name, kind in TECHNOLOGY_SIGNATURES:
        if any(needle in source for source in sources):
            found.setdefault(name, {"name": name, "type": kind, "confidence": 0.9})
    for meta in soup.find_all("meta", attrs={"name": "generator"}):
        generator = (meta.get("content") or "").strip()
        if generator:
            found.setdefault(generator.split(" ")[0], {"name": generator, "type": "cms", "confidence": 1.0})
    return list(found.values())


def extract_static(url: str, html: str, site: str) -> Dict[str, Any]:
    """Everything the page states itself. Each value is {"value", "confidence"} or a list of them."""
    soup = soup_of(html)
    ld = _ld_objects(soup)
    organisation = next((n for n in ld if {"Organization", "Corporation", "LocalBusiness"} & set(_kind(n))), {})
    meta = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name")
        if key and tag.get("content"):
            meta.setdefault(key.lower(), tag["content"].strip())

    out: Dict[str, Any] = {"title": (soup.title.string or "").strip() if soup.title and soup.title.string else ""}

    def put(key, value, confidence):
        if value not in (None, "", []):
            out.setdefault(key, {"value": value, "confidence": confidence})

    out["site_name"] = meta.get("og:site_name") or ""
    put("company_name", organisation.get("name"), 1.0)
    put("company_name", meta.get("og:site_name"), 0.8)
    put("tagline", organisation.get("description"), 1.0)
    put("tagline", meta.get("og:description") or meta.get("description"), 0.8)
    founded = re.search(r"(1[89]\d\d|20\d\d)", str(organisation.get("foundingDate") or ""))
    put("founded_year", int(founded.group(1)) if founded else None, 1.0)
    put("contact_email", organisation.get("email"), 1.0)
    put("contact_phone", organisation.get("telephone"), 1.0)
    put("logo", _absolute(url, organisation.get("logo")), 1.0)

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if anchor.find_parent(_EXAMPLE_TAGS) is not None or anchor.find(_EXAMPLE_TAGS) is not None:
            continue  # an address shown as code is an example
        if href.lower().startswith("mailto:"):
            put("contact_email", href[7:].split("?")[0].strip(), 1.0)
        elif href.lower().startswith("tel:"):
            put("contact_phone", href[4:].strip(), 1.0)
    if "logo" not in out:
        for img in soup.find_all("img", src=True):
            marks = " ".join([" ".join(img.get("class") or []), img.get("id") or "", img.get("alt") or "", img["src"]])
            if "logo" in marks.lower():
                put("logo", urljoin(url, img["src"]), 0.8)
                break

    text = visible_text(html, without=_EXAMPLE_TAGS)
    email = _EMAIL.search(text)
    put("contact_email", email.group(0) if email else None, 0.7)

    # every candidate carries where it was linked from: the site's own header and footer, or its structured data,
    # speak for the company; a link in body text or a post may be anyone's
    candidates: List[Tuple[Any, bool]] = [(c, True) for c in organisation.get("sameAs") or []] \
        if isinstance(organisation.get("sameAs"), list) else []
    for anchor in soup.find_all("a", href=True):
        chrome = anchor.find_parent(["header", "footer", "nav"]) is not None
        candidates.append((anchor["href"], chrome))
    social: List[Dict[str, Any]] = []
    for candidate, chrome in candidates:
        root = social_profile_root(urljoin(url, str(candidate).strip()))
        if root:
            social.append({"platform": root[0], "url": root[1], "handle": root[2], "chrome": chrome,
                           "confidence": 1.0})
    out["social"] = social
    for item in sorted(social, key=lambda i: not i["chrome"]):
        if item["platform"] == "LinkedIn":
            put("linkedin_url", item["url"], 1.0)
    out["technologies"] = detect_technologies(soup)
    out["links"] = page_links(html, url, site)
    out["text_length"] = len(text)
    return out
