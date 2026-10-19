"""The legal pages a buyer can read before paying, and which of them are live.

Terms, Privacy, the data processing agreement, the sub-processor list and the
acceptable use page are always published. The cookie policy, refund policy and
commercial licence are written but held back
until ``LEGAL_PAGES_ENABLED`` is switched on, so their text can be approved
before anyone can read it as a binding promise.

This module is the one list of those pages. The footer, the checkout panel,
the page route and the sitemap all read it, so a page cannot be linked in one
place while it answers 404 in another.
"""

from __future__ import annotations

LEGAL_PAGES_FLAG = "LEGAL_PAGES_ENABLED"

# (slug, link label) — the slug is the top-level URL and the file name under
# content/pages/legal/ (the always-published pages live under content/pages/site/).
ALWAYS_PUBLISHED = (
    ("terms", "Terms"),
    ("privacy", "Privacy"),
    ("data-processing-agreement", "Data processing agreement"),
    ("sub-processors", "Sub-processors"),
    ("acceptable-use", "Acceptable use"),
)
AWAITING_APPROVAL = (
    ("cookie-policy", "Cookie policy"),
    ("refund-policy", "Refund policy"),
    ("commercial-licence", "Commercial licence"),
)


def legal_pages_enabled(app=None) -> bool:
    """True only when the flag is explicitly on for this app; off outside an app."""
    if app is None:
        from flask import current_app, has_app_context

        if not has_app_context():
            return False
        app = current_app
    return bool(app.config.get(LEGAL_PAGES_FLAG, False))


def legal_links(app=None) -> list[dict[str, str]]:
    """Every legal page a visitor can open right now, in reading order."""
    pages = list(ALWAYS_PUBLISHED)
    if legal_pages_enabled(app):
        pages.extend(AWAITING_APPROVAL)
    return [{"slug": slug, "label": label, "href": f"/{slug}"} for slug, label in pages]
