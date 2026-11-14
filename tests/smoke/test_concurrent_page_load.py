"""Under many requests at once a page renders whole or fails with a server error.

The person: an enterprise architect who opens the workspace and whose browser asks for every
sidebar page at the same moment. Whatever the load, each answer is either the whole page or a
5xx that the browser and the uptime check can see; a status 200 page that says "Something went
wrong", or prints a lone None where a value belongs, hides a failure.

Marked ``load`` and left out of the normal run; run it with ``pytest -m load``. Set
LOAD_REQUESTS to change the number of simultaneous requests (95 by default).
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

import pytest
import requests

from tests.smoke.conftest import PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.load]

REQUESTS_AT_ONCE = int(os.environ.get("LOAD_REQUESTS", "95"))
LONE_NONE = re.compile(r">\s*None\s*<")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href", "").startswith("/"):
            self.hrefs.append(attrs["href"].split("#")[0])


def _sign_in(base, email):
    session = requests.Session()
    login = session.get(base + "/account/login", timeout=300)
    token = re.search(r'<meta[^>]*name="csrf-token"[^>]*content="([^"]+)"', login.text) or re.search(
        r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', login.text
    )
    assert token, "login page carries no CSRF token"
    answer = session.post(
        base + "/account/login",
        data={"email": email, "password": PASSWORD, "csrf_token": token.group(1)},
        timeout=300,
        allow_redirects=True,
    )
    assert "/account/login" not in answer.url, "could not sign in as %s" % email
    return session


def _sidebar_pages(session, base, wanted):
    """The pages the signed-in sidebar links to, repeated to reach ``wanted`` requests."""
    page = session.get(base + "/dashboard", timeout=300)
    parser = _Links()
    parser.feed(page.text)
    seen = []
    for href in parser.hrefs:
        if href not in seen and not href.startswith(("/static", "/account/logout", "/logout")):
            seen.append(href)
    assert len(seen) >= 20, "the sidebar offered only %d pages" % len(seen)
    return [seen[i % len(seen)] for i in range(wanted)]


def _marks(body):
    """How many times a page says it went wrong or prints a lone None."""
    return body.count("Something went wrong") + len(LONE_NONE.findall(body))


@pytest.fixture(scope="module")
def pooled_server(request, ai_protocol_stub, app, seeded):
    """The app as it is deployed: a connection pool, not one connection per use."""
    return boot_live_server(
        request,
        ai_protocol_stub,
        app,
        extra_env={
            "FLASK_CONFIG": "production",
            "TRANSFORMATION_COMMAND_CAPABILITY_SECRET": "load-test-only-secret-" + "x" * 16,
            "SESSION_COOKIE_SECURE": "false",
            "REMEMBER_COOKIE_SECURE": "false",
            "SQLALCHEMY_POOL_SIZE": os.environ.get("LOAD_POOL_SIZE", "5"),
            "SQLALCHEMY_MAX_OVERFLOW": os.environ.get("LOAD_POOL_OVERFLOW", "5"),
        },
    )


def test_every_answer_under_load_is_a_whole_page_or_a_server_error(seeded, pooled_server):
    live_server = pooled_server
    base = str(live_server)
    session = _sign_in(base, seeded["emails"]["enterprise_architect"])
    pages = _sidebar_pages(session, base, REQUESTS_AT_ONCE)

    # Some pages carry the words in fixed text (a hidden "try again" panel, a "None" column
    # heading), so each page is first read on its own and the count there is its normal count.
    quiet = {}
    for path in dict.fromkeys(pages):
        reply = session.get(base + path, timeout=300)
        quiet[path] = _marks(reply.text) if reply.status_code == 200 else 0

    def fetch(path):
        reply = session.get(base + path, timeout=300, allow_redirects=True)
        return path, reply.status_code, reply.text, reply.elapsed.total_seconds()

    with ThreadPoolExecutor(max_workers=REQUESTS_AT_ONCE) as pool:
        answers = list(pool.map(fetch, pages))

    timings = sorted(a[3] for a in answers)
    statuses = {}
    for _, status, _, _ in answers:
        statuses[status] = statuses.get(status, 0) + 1
    print("requests=%d fastest=%.2fs slowest=%.2fs statuses=%s" % (len(answers), timings[0], timings[-1], statuses))

    degraded = [
        (path, _marks(body), quiet[path])
        for path, status, body, _ in answers
        if status == 200 and _marks(body) > quiet[path]
    ]
    print("degraded_200=%d" % len(degraded))
    assert not degraded, "pages answered 200 while degraded (path, now, alone): %s\nserver log:\n%s" % (
        degraded[:10],
        live_server.tail(30),
    )
