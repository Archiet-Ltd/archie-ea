import logging
import os
import smtplib

from flask import after_this_request, current_app, has_request_context, render_template
from flask_mail import Connection, Message

from app import create_app, mail

_log = logging.getLogger(__name__)


def mail_sender(app=None):
    """The From address account mail goes out under, or None when unset."""
    app = app or current_app
    return app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME") or None


def mail_available(app=None):
    """True when this server has somewhere to send mail and someone to send it as.

    ``MAIL_SERVER`` carries a default, so a server is always named; what an
    unconfigured install lacks is a sender. Every account flow asks this before
    it promises the user a message, so "no mail server" is said out loud rather
    than discovered when nothing arrives.
    """
    app = app or current_app
    return bool(app.config.get("MAIL_SERVER")) and bool(mail_sender(app))


class _BoundedConnection(Connection):
    """Flask-Mail's connection with a socket timeout on the SMTP session."""

    def configure_host(self):
        timeout = current_app.config.get("MAIL_TIMEOUT") or 15
        if self.mail.use_ssl:
            host = smtplib.SMTP_SSL(self.mail.server, self.mail.port, timeout=timeout)
        else:
            host = smtplib.SMTP(self.mail.server, self.mail.port, timeout=timeout)
        host.set_debuglevel(int(self.mail.debug))
        if self.mail.use_tls:
            host.starttls()
        if self.mail.username and self.mail.password:
            host.login(self.mail.username, self.mail.password)
        return host


def _compose(app, recipient, subject, template, **kwargs):
    msg = Message(
        app.config.get("EMAIL_SUBJECT_PREFIX", "") + " " + subject,
        sender=mail_sender(app),
        recipients=[recipient],
    )
    msg.body = render_template(template + ".txt", **kwargs)
    msg.html = render_template(template + ".html", **kwargs)
    return msg


def _transmit(app, msg):
    """Hand one composed message to the mail server. Returns ``(delivered, error)``."""
    try:
        with _BoundedConnection(app.extensions["mail"]) as conn:
            conn.send(msg)
        return True, None
    except Exception as exc:
        _log.error("account mail %r to recipient failed: %s", msg.subject, exc, exc_info=True)
        return False, "The mail server did not accept the message ({}).".format(
            type(exc).__name__
        )


def deliver_email(recipient, subject, template, **kwargs):
    """Render and send one message now, through the configured Flask-Mail settings.

    Returns ``(delivered, error)``. ``error`` is a short reason when the
    message did not go out, so the caller can tell the user what happened
    instead of assuming it arrived. Under TESTING, Flask-Mail suppresses the
    SMTP session and records the message instead (``mail.record_messages``).
    """
    app = current_app._get_current_object()
    if not mail_available(app):
        return False, "E-mail is not available on this server."
    try:
        msg = _compose(app, recipient, subject, template, **kwargs)
    except Exception as exc:
        _log.error("account mail %r could not be rendered: %s", subject, exc, exc_info=True)
        return False, "The message could not be prepared ({}).".format(type(exc).__name__)
    return _transmit(app, msg)


def deliver_email_after_response(recipient, subject, template, on_result=None, **kwargs):
    """Render one message now and hand it to the mail server once the response is sent.

    For answers that must not depend on whether a message went out -- the
    password-reset request answers the same for every address, and a reply
    that waited on the mail server would say which addresses have accounts
    by how long it took. Outside a request the message is sent at once.
    ``on_result(delivered, error)`` is then called in an application context.
    """
    app = current_app._get_current_object()
    if not mail_available(app):
        return
    msg = _compose(app, recipient, subject, template, **kwargs)

    def transmit():
        with app.app_context():
            delivered, error = _transmit(app, msg)
            if on_result is not None:
                try:
                    on_result(delivered, error)
                except Exception:
                    _log.error("recording the outcome of account mail %r failed", subject, exc_info=True)

    if not has_request_context():
        transmit()
        return

    @after_this_request
    def _send_when_closed(response):
        response.call_on_close(transmit)
        return response


def send_email(recipient, subject, template, **kwargs):
    app = create_app(os.getenv("FLASK_CONFIG") or "default")
    with app.app_context():
        msg = Message(
            app.config["EMAIL_SUBJECT_PREFIX"] + " " + subject,
            sender=app.config["EMAIL_SENDER"],
            recipients=[recipient],
        )
        msg.body = render_template(template + ".txt", **kwargs)
        msg.html = render_template(template + ".html", **kwargs)
        mail.send(msg)
