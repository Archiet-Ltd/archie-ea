"""The one gate for an account that has not confirmed its address.

Both account blueprints (the live one and the older flat one) call it, so the rule is stated once.
"""
from flask import redirect, request, url_for
from flask_login import current_user

# What an account that has not confirmed its address may open: the prompt and its resend, the
# emailed link, signing out, the pages a signed-out visitor can already open, and the session
# timer's keepalive. Every other account page belongs to a workspace, which the account does not
# have until it confirms, so it is sent back to the prompt.
HELD_ACCOUNT_OPEN = frozenset(
    {
        "account.unconfirmed",
        "account.confirm_request",
        "account.confirm",
        "account.logout",
        "account.login",
        "account.register",
        "account.reset_password_request",
        "account.reset_password",
        "account.session_keepalive",
        "account.sso_login",
        "account.sso_callback",
        "account.join",
        "account.join_from_invite",
    }
)


def redirect_held_account():
    """A redirect to the confirm prompt for a signed-in, unconfirmed account; otherwise None."""
    if (
        current_user.is_authenticated
        and not current_user.confirmed
        and request.endpoint not in HELD_ACCOUNT_OPEN
        and request.endpoint != "static"
    ):
        return redirect(url_for("account.unconfirmed"))
    return None
