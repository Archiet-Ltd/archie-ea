"""Shared helper for extracting a public page's own content region.

A set of older tests each asserted "Join the waiting list" was absent from
a whole page. That sentence now lives by design in the public navbar's
Product menu, the shared footer, and (for the home page only) a labelled
Coming soon block. Each of those tests must evaluate its assertion against
the page's own main content, not the menu/footer chrome that wraps every
public page.

``page_body`` returns that region: the ``<main>`` element's inner HTML, since
every public page extends layouts/public_base.html's
``<main id="main-content">``. If a page has no ``<main>`` element, it falls
back to stripping the ``<header>`` and ``<footer>`` elements instead, per the
ruling's own fallback rule, so a page rendered without the standard layout
(a future bare page, or a test page) still gets a body estimate rather than a
crash.
"""

import re

_MAIN_RE = re.compile(r"<main\b[^>]*>(.*)</main>", re.DOTALL | re.IGNORECASE)
_HEADER_RE = re.compile(r"<header\b[^>]*>.*?</header>", re.DOTALL | re.IGNORECASE)
_FOOTER_RE = re.compile(r"<footer\b[^>]*>.*?</footer>", re.DOTALL | re.IGNORECASE)


def page_body(html: str) -> str:
    """Return the page's own content region: inside ``<main>`` when present,
    else the whole page with ``<header>`` and ``<footer>`` stripped out."""
    match = _MAIN_RE.search(html)
    if match:
        return match.group(1)
    return _FOOTER_RE.sub("", _HEADER_RE.sub("", html))


def page_body_excluding(html: str, *, exclude_testid: str) -> str:
    """``page_body(html)`` with one ``data-testid="..."`` block additionally
    removed -- used by the home page's own assertions to look at the page
    body without its own, by-design Coming soon block."""
    body = page_body(html)
    pattern = re.compile(
        r'<[^>]+data-testid="' + re.escape(exclude_testid) + r'"[^>]*>.*?</div>',
        re.DOTALL,
    )
    return pattern.sub("", body, count=1)
