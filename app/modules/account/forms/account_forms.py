"""
Account Forms (migrated).

Migrated from: app/account/forms.py
All 7 WTForms classes preserved exactly.
"""
from flask import url_for
from flask_wtf import FlaskForm
from wtforms import ValidationError
from wtforms.fields import BooleanField, EmailField, PasswordField, StringField, SubmitField
from wtforms.validators import Email, EqualTo, InputRequired, Length

from app.models import User


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[InputRequired(), Length(1, 64), Email()])
    password = PasswordField("Password", validators=[InputRequired()],
        render_kw={"autocomplete": "current-password"},
    )
    remember_me = BooleanField("Keep me logged in")
    submit = SubmitField("Log in")


class RegistrationForm(FlaskForm):
    first_name = StringField("First name", validators=[InputRequired(), Length(1, 64)])
    last_name = StringField("Last name", validators=[InputRequired(), Length(1, 64)])
    email = EmailField("Email", validators=[InputRequired(), Length(1, 64), Email()])
    password = PasswordField(
        "Password", validators=[InputRequired(), EqualTo("password2", "Passwords must match")]
    ,
        render_kw={"autocomplete": "new-password"},
    )
    password2 = PasswordField("Confirm password", validators=[InputRequired()],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Register")

    def validate_email(self, field):
        from app.modules.account.services.invitation_service import is_unactivated

        existing = User.find_by_email(field.data)
        # An account an invitation opened, and nobody has taken up, does not
        # own its address: registering takes it over (with its invitations).
        if existing is not None and not is_unactivated(existing):
            raise ValidationError(
                "Email already registered. (Did you mean to "
                '<a href="{}">log in</a> instead?)'.format(url_for("account.login"))
            )


class RequestResetPasswordForm(FlaskForm):
    email = EmailField("Email", validators=[InputRequired(), Length(1, 64), Email()])
    submit = SubmitField("Reset password")

    # We don't validate the email address so we don't confirm to attackers
    # that an account with the given email exists.


class ResetPasswordForm(FlaskForm):
    email = EmailField("Email", validators=[InputRequired(), Length(1, 64), Email()])
    new_password = PasswordField(
        "New password",
        validators=[InputRequired(), EqualTo("new_password2", "Passwords must match.")],
        render_kw={"autocomplete": "new-password"},
    )
    new_password2 = PasswordField("Confirm new password", validators=[InputRequired()],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Reset password")

    def validate_email(self, field):
        if User.find_by_email(field.data) is None:
            raise ValidationError("Unknown email address.")


class CreatePasswordForm(FlaskForm):
    password = PasswordField(
        "Password", validators=[InputRequired(), EqualTo("password2", "Passwords must match.")]
    ,
        render_kw={"autocomplete": "new-password"},
    )
    password2 = PasswordField("Confirm new password", validators=[InputRequired()],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Set password")


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField("Old password", validators=[InputRequired()],
        render_kw={"autocomplete": "current-password"},
    )
    new_password = PasswordField(
        "New password",
        validators=[InputRequired(), EqualTo("new_password2", "Passwords must match.")],
        render_kw={"autocomplete": "new-password"},
    )
    new_password2 = PasswordField("Confirm new password", validators=[InputRequired()],
        render_kw={"autocomplete": "new-password"},
    )
    submit = SubmitField("Update password")


class ChangeEmailForm(FlaskForm):
    email = EmailField("New email", validators=[InputRequired(), Length(1, 64), Email()])
    password = PasswordField("Password", validators=[InputRequired()],
        render_kw={"autocomplete": "current-password"},
    )
    submit = SubmitField("Update email")

    def validate_email(self, field):
        if User.find_by_email(field.data):
            raise ValidationError("Email already registered.")
