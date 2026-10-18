"""Every internal link on a live public content page leads to a page that exists.

The links are read from the rendered body of each live page (a held page is not
live, so its links are not visible to anyone) and each target is requested as
an anonymous visitor. A missing page (404) or a server error fails here with
the pages that carry the link, so a page promised in the text but not built is
caught before a visitor clicks it.
"""

from __future__ import annotations

import re

HREF = re.compile(r'href="(/[^"#?\s]*)')


def test_every_internal_link_on_a_live_content_page_resolves(app):
    from app.services.public_pages import load_all_pages

    carried_by = {}
    for page in load_all_pages():
        for target in HREF.findall(page.body_html):
            if not target.startswith("//"):
                carried_by.setdefault(target, set()).add(page.url)

    broken = {}
    with app.test_client() as client:
        for target, pages in sorted(carried_by.items()):
            status = client.get(target, follow_redirects=False).status_code
            if status >= 400 and status not in (401, 403):
                broken[target] = (status, sorted(pages))
    assert not broken, "links that do not resolve: %s" % broken
