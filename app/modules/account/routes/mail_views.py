"""
Account views driven by e-mailed links: sign-up with address confirmation,
password reset, and joining an organisation from an invitation.

Shared by the account blueprint's two tiers (``routes/account_routes.py`` and
``v2/routes/account_routes.py``) so there is one implementation of each flow;
each tier's route is a one-line call into here.
"""
from flask import current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.flask_email import mail_available
from app.modules.account.forms.account_forms import (
    CreatePasswordForm,
    RegistrationForm,
    RequestResetPasswordForm,
)
from app.modules.account.services.account_service import AccountService
from app.services import buy_intent

MAIL_UNAVAILABLE_RESET = (
    "E-mail is not available on this server, so a reset link cannot be sent. "
    "Ask your organisation's administrator to set a new password for you."
)


def _link_expired(title, message, action_label, action_href):
    return render_template(
        "account/link_expired.html",
        title=title,
        message=message,
        action_label=action_label,
        action_href=action_href,
    ), 410


def _after_confirmation_url(user):
    """Where a newly confirmed person goes: an owner lands on inviting the team."""
    if "team.team" in current_app.view_functions and getattr(user, "is_org_admin", False):
        return url_for("team.team")
    return url_for("dashboard.overview")


def register_view():
    chosen = buy_intent.requested()
    if chosen is None and buy_intent.plan_given():
        # An unknown plan key: send the visitor back to choose one.
        return redirect("/pricing")
    if chosen is not None and current_user.is_authenticated:
        # Already has an account and is signed in: straight to that plan.
        return redirect(buy_intent.target_url(*chosen))
    form = RegistrationForm()
    if request.method == "GET":
        # A visitor reaching the sign-up form, not a retry after a failed
        # POST -- see app/services/public_analytics_service.py.
        from app.services.public_analytics_service import log_signup_started

        log_signup_started()
    if form.validate_on_submit():
        _user, confirmation = AccountService.sign_up(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            password=form.password.data,
        )
        from app.services.public_analytics_service import log_signup_completed

        log_signup_completed()
        if chosen is not None:
            # After sign_up: signing the new account in starts a fresh session.
            buy_intent.remember(chosen)
        if confirmation == "sent":
            return redirect(url_for("account.unconfirmed"))
        if confirmation == "failed":
            flash(
                "Your account is created, but the confirmation message could not be "
                "sent. Use the button below to try again.",
                "error",
            )
            return redirect(url_for("account.unconfirmed"))
        flash(
            "Account created. Welcome to {}! E-mail is not available on this "
            "server, so no confirmation message was sent.".format(current_app.config['APP_NAME']),
            "info",
        )
        if chosen is not None:
            # Usable at once: sign in and go straight to that plan's checkout.
            return redirect(url_for("account.login", plan=chosen[0], interval=chosen[1]))
        return redirect(url_for("main.index"))
    return render_template(
        "account/register.html", form=form,
        signin_args={"plan": chosen[0], "interval": chosen[1]} if chosen else {},
    )


def reset_request_view():
    if not current_user.is_anonymous:
        return redirect(url_for("main.index"))
    form = RequestResetPasswordForm()
    if not mail_available():
        # Said before anything is typed, and the same for every address.
        return render_template(
            "account/reset_password.html", form=form, state="mail_unavailable",
            message=MAIL_UNAVAILABLE_RESET,
        )
    if form.validate_on_submit():
        outcome = AccountService.request_password_reset(form.email.data)
        if outcome == "mail_unavailable":
            return render_template(
                "account/reset_password.html", form=form, state="mail_unavailable",
                message=MAIL_UNAVAILABLE_RESET,
            )
        return render_template(
            "account/reset_password.html", form=None, state="requested",
            email=form.email.data,
        )
    return render_template("account/reset_password.html", form=form, state="request")


def reset_view(token):
    if not current_user.is_anonymous:
        return redirect(url_for("main.index"))
    def expired():
        return _link_expired(
            "This reset link no longer works",
            "It has expired or has already been used. Reset links work once and "
            "for one hour. Ask for a new one below.",
            "Send a new reset link",
            url_for("account.reset_password_request"),
        )

    if not AccountService.reset_link_usable(token):
        return expired()
    form = CreatePasswordForm()
    form.submit.label.text = "Set new password"
    if form.validate_on_submit():
        success, message = AccountService.reset_password(token, form.password.data)
        if not success:
            return expired()
        flash(message, "form-success")
        return redirect(url_for("account.login"))
    return render_template("account/reset_password.html", form=form, state="set")


def unconfirmed_view():
    if current_user.is_anonymous or current_user.confirmed:
        return redirect(url_for("main.index"))
    return render_template(
        "account/unconfirmed.html", mail_is_available=mail_available()
    )


def confirm_request_view():
    """POST: mail a fresh confirmation link to the signed-in, unconfirmed user."""
    if current_user.confirmed:
        return redirect(url_for("main.index"))
    if request.method != "POST":
        return redirect(url_for("account.unconfirmed"))
    delivered, error = AccountService.send_confirmation_email(current_user)
    if delivered:
        flash("A new confirmation link has been sent to {}.".format(current_user.email), "success")
    else:
        flash("No message was sent: {}".format(error), "error")
    return redirect(url_for("account.unconfirmed"))


def confirm_view(token):
    user, message = AccountService.confirm_account(token)
    if user is None:
        if current_user.is_authenticated and current_user.confirmed:
            return redirect(url_for("main.index"))
        if current_user.is_authenticated:
            return _link_expired(
                "This confirmation link no longer works",
                "It has expired or has already been used. Send yourself a new one.",
                "Back to confirmation",
                url_for("account.unconfirmed"),
            )
        return _link_expired(
            "This confirmation link no longer works",
            "It has expired or has already been used. Sign in to send yourself a new one.",
            "Sign in",
            url_for("account.login"),
        )
    if current_user.is_authenticated and current_user.id == user.id:
        flash(message, "success")
        held = buy_intent.remembered_url()
        if held:
            session.pop(buy_intent.SESSION_KEY, None)
            return redirect(held)
        return redirect(_after_confirmation_url(user))
    flash(message + " Sign in to continue.", "form-success")
    # The chosen plan stays in the session; signing in lands on its checkout.
    return redirect(url_for("account.login"))


def _invitation_gone():
    return _link_expired(
        "This invitation no longer works",
        "It has expired, has already been used, or was withdrawn. Ask the "
        "person who invited you to send a new one.",
        "Go to sign in",
        url_for("account.login"),
    )


def join_view(token):
    """Take up an invitation.

    Someone new sets a password and becomes a member; someone who already has
    an account signs in as it and accepts or declines.
    """
    from app.modules.account.services import invitation_service

    invitation = invitation_service.find_joinable(token)
    if invitation is None:
        return _invitation_gone()
    organisation_name = invitation_service.organisation_name(invitation.organization_id)
    if not invitation_service.is_unactivated(invitation.user):
        return _join_existing_view(token, invitation, organisation_name)

    if current_user.is_authenticated:
        return _link_expired(
            "Sign out to accept this invitation",
            "You are signed in as {}. An invitation opens a new account, so sign "
            "out first and open the link again.".format(current_user.email),
            "Sign out",
            url_for("account.logout"),
        )
    form = CreatePasswordForm()
    form.submit.label.text = "Join {}".format(organisation_name)
    if form.validate_on_submit():
        user = invitation_service.accept_new(token, form.password.data)
        if user is None:
            return _invitation_gone()
        flash(
            "You have joined {}. Sign in with {} and the password you just set.".format(
                organisation_name, user.email
            ),
            "form-success",
        )
        return redirect(url_for("account.login"))
    return render_template(
        "account/join_invite.html",
        form=form,
        organisation_name=organisation_name,
        email=invitation.user.email,
        inviter=invitation.inviter,
    )


def _join_existing_view(token, invitation, organisation_name):
    """An invitation for an account that already exists: its holder answers it."""
    from app.modules.account.services import invitation_service

    if current_user.is_anonymous:
        flash("Sign in as {} to answer the invitation to {}.".format(
            invitation.user.email, organisation_name), "info")
        return redirect(url_for("account.login", next=request.path))
    if current_user.id != invitation.user_id:
        return _link_expired(
            "This invitation is for another account",
            "You are signed in as {}. Sign out, then sign in with the address the "
            "invitation was sent to and open the link again.".format(current_user.email),
            "Sign out",
            url_for("account.logout"),
        )
    if request.method == "POST":
        # CSRF is checked for every POST by the application-wide CSRFProtect.
        accept = request.form.get("decision") == "accept"
        org_id = invitation_service.answer_existing(token, current_user, accept)
        if org_id is None:
            return _invitation_gone()
        if accept:
            flash("You are now a member of {}.".format(organisation_name), "success")
        else:
            flash("You declined the invitation to {}.".format(organisation_name), "info")
        return redirect(url_for("dashboard.overview"))
    return render_template(
        "account/join_invite.html",
        form=None,
        existing=True,
        organisation_name=organisation_name,
        email=invitation.user.email,
        inviter=invitation.inviter,
        role=invitation.role,
        join_url=url_for("account.join", token=token),
    )
