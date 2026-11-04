"""One plain-words treatment for the 403, 404 and 500 pages.

A signed-in person keeps the sidebar and sees what happened and what to do
next; a visitor who is not signed in (or a render that cannot build the full
shell) gets the standalone page in ``templates/errors``.
"""

from __future__ import annotations

from flask import jsonify, render_template
from flask_login import current_user
from markupsafe import Markup, escape

PLAIN = {
    403: (
        "lock",
        "You don't have access to this page",
        "Your role does not include this page. Ask an administrator of your organisation to "
        "give you access, or go back to the dashboard.",
    ),
    429: (
        "timer",
        "You have made too many requests to this page",
        "You have sent a lot of requests to this page in a short time, so it has been paused to keep "
        "the service responsive for everyone.",
    ),
    404: (
        "search-x",
        "We couldn't find that page",
        "It may have been moved or removed, or the link may be out of date. Use the menu to "
        "find what you were looking for.",
    ),
    500: (
        "triangle-alert",
        "Something went wrong on our side",
        "Check whether your changes were saved before trying again. If it keeps happening, "
        "tell your administrator.",
    ),
}


ICON_PATHS = {
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path>',
    "timer": '<line x1="10" x2="14" y1="2" y2="2"></line><line x1="12" x2="15" y1="14" y2="11"></line><circle cx="12" cy="14" r="8"></circle>',
    "search-x": '<path d="m13.5 8.5-5 5"></path><path d="m8.5 8.5 5 5"></path><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path>',
    "triangle-alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>',
}


def plain_json(status: int, error_type: str, **extra):
    """JSON body for an /api/ caller: the same sentence a person would read."""
    return jsonify({
        "success": False,
        "error": PLAIN[status][1] + ". " + PLAIN[status][2],
        "error_type": error_type,
        **extra,
    }), status


def render_plain_error(status: int, standalone_template: str | None = None, *, title: str | None = None,
                       message: str | None = None, offer_links: bool = True, standalone: bool = False,
                       next_step: tuple[str, str] | None = None):
    """Shell page for signed-in people, standalone page otherwise.

    ``standalone_template`` is accepted for old callers and ignored: there is
    one standalone page for every status.

    A refusal made before the person's session is read (a blocked address, a
    held allowance) passes ``standalone=True`` so the page does not draw a
    sidebar of links that would be refused too, and ``offer_links=False`` when
    no link on the page could land. ``next_step`` is a (label, address) pair that
    replaces the dashboard link, for a visitor who is not signed in.
    """
    icon, default_title, default_message = PLAIN[status]
    title = title or default_title
    message = message or default_message
    try:
        if not standalone and current_user.is_authenticated:
            return render_template(
                "errors/plain_error.html",
                error_status=status, error_icon=icon,
                error_title=title, error_message=message,
            ), status
    except Exception:
        # The full shell needs the database; a failing request may have broken it.
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
    try:
        return render_template(
            "errors/standalone_error.html",
            error_status=status, error_title=title, error_message=message,
            error_icon_paths=Markup(ICON_PATHS[icon]),  # fixed constants above, no user data offer_links=offer_links,
            next_label=next_step[0] if next_step else "Go to the dashboard",
            next_href=next_step[1] if next_step else "/dashboard/overview",
        ), status
    except Exception:
        return inline_error_page(status, offer_links=offer_links, next_step=next_step, title=title, message=message), status


def _way_back() -> str:
    """The same-origin page the person came from, else the dashboard.

    Only the path and query of a referrer on this host are used, so the link
    can never leave the site, and a referrer that is the failing page itself
    is skipped because it would just fail again.
    """
    fallback = "/dashboard/overview"
    try:
        from urllib.parse import urlsplit

        from flask import request

        ref = urlsplit(request.referrer or "")
        if ref.scheme in ("http", "https") and ref.netloc == request.host:
            target = ref.path + (f"?{ref.query}" if ref.query else "")
            safe = target.startswith("/") and not target.startswith(("//", "/\\")) and "\\" not in target
            if safe and ref.path != request.path:
                return target
    except Exception:
        pass
    return fallback


def _stylesheet_href() -> str:
    try:
        from flask import url_for

        return url_for("static", filename="css/shadcn_tokens.css")
    except Exception:
        return "/static/css/shadcn_tokens.css"


def inline_error_page(status: int, offer_links: bool = True, next_step: tuple[str, str] | None = None,
                      title: str | None = None, message: str | None = None) -> str:
    """A last-resort page that needs no template, database or session.

    Used when both error templates fail to render (a broken macro, a failing
    context processor), so the person still reads a sentence and has a way
    back instead of the bare server text.

    The content security policy allows no ``<style>`` block without the
    request's nonce, and the nonce is set during request setup, which may be
    exactly what failed. So the page links the app's own same-origin token
    stylesheet, which needs no request state, and lays itself out with style
    attributes, which the policy allows.
    """
    _icon, default_title, default_message = PLAIN.get(status, PLAIN[500])
    title = title or default_title
    message = message or default_message
    next_label, next_href = next_step if next_step else ("Go to the dashboard", "/dashboard/overview")
    link = 'display:inline-block;margin:.25rem;padding:.6rem 1.2rem;border-radius:.375rem;text-decoration:none;font-size:.9rem;'
    primary = link + "background:hsl(var(--primary));color:hsl(var(--primary-foreground));"
    secondary = link + "border:1px solid hsl(var(--border));color:hsl(var(--foreground));"
    links = (
        f'<a style="{escape(primary)}" href="{escape(_way_back())}">Go back</a>'
        f'<a style="{escape(secondary)}" href="{escape(next_href)}">{escape(next_label)}</a>'
    ) if offer_links else ""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title>"
        f'<link rel="stylesheet" href="{escape(_stylesheet_href())}">'
        "</head>"
        '<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;'
        "padding:1rem;font-family:system-ui,sans-serif;"
        'background:hsl(var(--background));color:hsl(var(--foreground))">'
        f'<main style="max-width:32rem;text-align:center" data-testid="plain-error" data-status="{escape(str(int(status)))}">'
        f"<h1>{escape(title)}</h1>"
        f'<p style="line-height:1.6;color:hsl(var(--muted-foreground))">{escape(message)}</p>'
        f"{links}"
        "</main></body></html>"
    )
