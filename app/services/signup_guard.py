"""Keeps scripts out of the sign-up form without getting in a person's way.

Four checks, each refusing with a plain message and creating nothing:

* a hidden field a person never sees, so never fills (``nickname``);
* a minimum time between the form being drawn and being sent, carried in a signed field
  (``form_issued``) so a script cannot claim it waited, and tied to the session that drew the form so
  a stamp taken from one session is refused in another;
* a user agent that names itself a bot or crawler (never any other user agent);
* an address on a disposable-mailbox domain, from the list in ``app/data/disposable_email_domains.txt``.

An optional Cloudflare Turnstile check is off until ``SIGNUP_TURNSTILE_ENABLED`` is set with a site key
and a secret. The sign-up count per address is limited separately, on the route.
"""

import hashlib
import hmac
import os
import re
import secrets
from functools import lru_cache

from flask import current_app, request
from itsdangerous import BadData, URLSafeTimedSerializer

HONEYPOT_FIELD = "nickname"
TOKEN_FIELD = "form_issued"
SESSION_KEY = "signup_form_binding"
TURNSTILE_FIELD = "cf-turnstile-response"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# A form left open longer than this is drawn again rather than trusted.
MAX_FORM_AGE_SECONDS = 6 * 3600

_DOMAINS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "disposable_email_domains.txt")
# A bot names itself as a word ("bot", "Spider") or as a name joined to the word and closed by a
# separator ("Googlebot/2.1", "bingbot;", "Baiduspider+"). A phone model such as "Cubot X20 Pro"
# ends in the same letters, so "cubot" is left out by name; a digit may start a bot name ("MJ12bot").
_BOT_AGENT = re.compile(
    r"\b(?:bot|crawler|spider|scraper)\b|[a-z0-9](?:(?<!cu)bot|crawler|spider|scraper)(?=[/;)+-]|$)|scrapy",
    re.IGNORECASE,
)

MESSAGES = {
    "honeypot": "We could not accept this sign-up. If you are a person, reload the page and try again.",
    "too_fast": "That was quicker than a person can fill in the form. Wait a few seconds and send it again.",
    "expired": "This form has expired. Reload the page and fill it in again.",
    "bot_agent": "Automated sign-ups are not accepted. If you are a person, use an ordinary web browser.",
    "turnstile": "We could not confirm you are a person. Try the check again.",
}
DISPOSABLE_MESSAGE = (
    "Please use your work or personal email address. Temporary mailbox addresses are not accepted."
)


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="signup-form")


def _session_binding():
    """A digest of a secret held in this browser session, so a stamp is good only in the session that drew it."""
    from flask import session

    if SESSION_KEY not in session:
        session[SESSION_KEY] = secrets.token_hex(16)
    return hashlib.sha256(session[SESSION_KEY].encode("utf-8")).hexdigest()


def issue_form_token():
    """A signed stamp of the moment the form was drawn, tied to the session that drew it."""
    return _serializer().dumps(_session_binding())


def checks_required():
    return bool(current_app.config.get("SIGNUP_FORM_TOKEN_REQUIRED", True))


def turnstile_enabled():
    config = current_app.config
    return bool(
        config.get("SIGNUP_TURNSTILE_ENABLED") and config.get("TURNSTILE_SITE_KEY") and config.get("TURNSTILE_SECRET_KEY")
    )


def _form_age(token):
    """Seconds since the form was drawn, or None when the stamp is missing, forged or too old."""
    if not token:
        return None
    try:
        payload, issued = _serializer().loads(token, max_age=MAX_FORM_AGE_SECONDS, return_timestamp=True)
    except BadData:
        return None
    if not hmac.compare_digest(str(payload), _session_binding()):
        return None
    from datetime import datetime, timezone

    return (datetime.now(timezone.utc) - issued.astimezone(timezone.utc)).total_seconds()


def _turnstile_passes(response_token):
    if not response_token:
        return False
    try:
        import requests

        answer = requests.post(
            TURNSTILE_VERIFY_URL,
            data={"secret": current_app.config["TURNSTILE_SECRET_KEY"], "response": response_token},
            timeout=5,
        )
        return bool(answer.json().get("success"))
    except Exception:  # an unreachable check refuses, as a failed one does
        return False


def refusal(form=None):
    """The reason this request is not a person signing up, or None when it may go on."""
    if _BOT_AGENT.search(request.headers.get("User-Agent") or ""):
        return "bot_agent"
    form = form if form is not None else request.form
    if (form.get(HONEYPOT_FIELD) or "").strip():
        return "honeypot"
    if checks_required():
        age = _form_age(form.get(TOKEN_FIELD))
        if age is None:
            return "expired"
        if age < float(current_app.config.get("SIGNUP_MIN_SECONDS", 3)):
            return "too_fast"
    if turnstile_enabled() and not _turnstile_passes(form.get(TURNSTILE_FIELD)):
        return "turnstile"
    return None


@lru_cache(maxsize=1)
def disposable_domains():
    with open(_DOMAINS_FILE, encoding="utf-8") as handle:
        return frozenset(
            line.strip().lower() for line in handle if line.strip() and not line.lstrip().startswith("#")
        )


def is_disposable(address):
    domain = (address or "").rsplit("@", 1)[-1].strip().lower()
    domains = disposable_domains()
    # a subdomain of a listed domain is the same mailbox service
    return any(domain == listed or domain.endswith("." + listed) for listed in domains)
