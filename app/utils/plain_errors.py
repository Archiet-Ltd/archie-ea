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


def plain_json(status: int, error_type: str, **extra):
    """JSON body for an /api/ caller: the same sentence a person would read."""
    return jsonify({
        "success": False,
        "error": PLAIN[status][1] + ". " + PLAIN[status][2],
        "error_type": error_type,
        **extra,
    }), status


def render_plain_error(status: int, standalone_template: str):
    """Shell page for signed-in people, standalone page otherwise."""
    icon, title, message = PLAIN[status]
    try:
        if current_user.is_authenticated:
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
    return render_template(standalone_template), status
