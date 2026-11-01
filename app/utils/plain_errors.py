"""One plain-words treatment for the 403, 404 and 500 pages.

A signed-in person keeps the sidebar and sees what happened and what to do
next; a visitor who is not signed in (or a render that cannot build the full
shell) gets the standalone page in ``templates/errors``.
"""

from __future__ import annotations

from flask import jsonify, render_template
from flask_login import current_user

PLAIN = {
    403: (
        "lock",
        "You don't have access to this page",
        "Your role does not include this page. Ask an administrator of your organisation to "
        "give you access, or go back to the dashboard.",
    ),
    429: (
        "timer",
        "You have made too many requests",
        "You have sent a lot of requests in a short time, so this address has been paused to keep "
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
    return render_template(
        "errors/standalone_error.html",
        error_status=status, error_title=title, error_message=message,
        error_icon_paths=ICON_PATHS[icon], offer_links=offer_links,
        next_label=next_step[0] if next_step else "Go to the dashboard",
        next_href=next_step[1] if next_step else "/dashboard/overview",
    ), status
