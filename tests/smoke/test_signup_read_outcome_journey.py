"""A person who gives GitHub at sign-up is told the truth about the read on the review page.

Sign up, give GitHub on the sources step, let the background read run against a GitHub that cannot find
the account (HTTP 404) or that holds no repositories, then open the connector page and its review page the
way a person does. The review page says the read could not finish, with the reason, or that it found no
repositories, with what to do next; every link in those sentences lands on a page with content.

GitHub is never called: the server's own requests to api.github.com are answered by a small module placed
on its PYTHONPATH (the same way the connect-GitHub journey does it).
"""

import os
import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.test_connect_github_to_cited_answer_journey import TOKEN
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (fixtures and helpers)
    new_page,
    people,
    shoot,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_ANSWERS = '''
def _install():
    import json
    import requests

    original = requests.Session.request
    mode = %r

    def dispatch(self, method=None, url=None, *args, **kwargs):
        if not (isinstance(url, str) and url.startswith("https://api.github.com/")):
            return original(self, method, url, *args, **kwargs)
        answer = requests.Response()
        answer.url = url
        answer.headers["Content-Type"] = "application/json"
        if mode == "missing":
            answer.status_code = 404
            body = {"message": "Not Found"}
        else:
            answer.status_code = 200
            body = {"type": "Organization", "login": "acme"} if url.rstrip("/").endswith("/users/acme") else []
        answer._content = json.dumps(body).encode()
        return answer

    requests.Session.request = dispatch


_install()
'''


def _server(request, app, tmp_path_factory, mode):
    folder = tmp_path_factory.mktemp("github_" + mode)
    (folder / "sitecustomize.py").write_text(_ANSWERS % mode)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"}
    )


@pytest.fixture(scope="module")
def server_missing(request, app, tmp_path_factory):
    return _server(request, app, tmp_path_factory, "missing")


@pytest.fixture(scope="module")
def server_empty(request, app, tmp_path_factory):
    return _server(request, app, tmp_path_factory, "empty")


def _give_github_and_wait(page, base, people, expected):
    """Sign up, give GitHub, and wait on the sources step until the sign-up line reports the read's end."""
    sign_up(page, base, people)
    page.get_by_label("GitHub organisation").fill("acme")
    page.get_by_label("Read-only token").fill(TOKEN)
    page.get_by_role("button", name="Connect GitHub").click()
    status = page.get_by_test_id("signup-status-github")
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        page.goto(base + "/account/welcome", wait_until="domcontentloaded")
        if re.search(expected, status.inner_text()):
            return status.inner_text()
        page.wait_for_timeout(3000)
    raise AssertionError("the sign-up line never reported the read: %s" % status.inner_text())


def _open_review_from_the_connect_page(page, base):
    page.goto(base + "/admin/connectors/github", wait_until="domcontentloaded")
    page.get_by_role("link", name="Review what was found").click()
    page.wait_for_url("**/admin/connectors/github/review")
    return page.locator("main")


def _the_link_lands_on_content(page, name, heading):
    page.get_by_role("link", name=name).click()
    page.wait_for_url("**/admin/connectors/github")
    expect(page.get_by_role("heading", name=re.compile(heading)).first).to_be_visible()
    assert len(page.locator("main").inner_text().strip()) > 40


def test_a_read_that_failed_at_sign_up_is_called_failed_on_the_review_page(browser, server_missing, people):
    base = str(server_missing)
    context, page = new_page(browser)
    try:
        line = _give_github_and_wait(page, base, people, r"GitHub could not be read")
        assert "HTTP 404" in line, line
        review = _open_review_from_the_connect_page(page, base)
        expect(review).to_contain_text("could not finish: GitHub could not find the account acme")
        expect(review).to_contain_text("HTTP 404")
        expect(review).not_to_contain_text("GitHub is connected; read your repositories again")
        shoot(page, "review-after-failed-read")
        _the_link_lands_on_content(page, "Read again", "GitHub")
    finally:
        context.close()


def test_a_read_that_found_nothing_at_sign_up_is_called_empty_on_the_review_page(browser, server_empty, people):
    base = str(server_empty)
    context, page = new_page(browser)
    try:
        line = _give_github_and_wait(page, base, people, r"Read 0 repositories")
        assert "Read 0 repositories" in line, line
        review = _open_review_from_the_connect_page(page, base)
        expect(review).to_contain_text("The last read found no repositories")
        expect(review).to_contain_text("read again")
        expect(review).not_to_contain_text("GitHub is connected; read your repositories again")
        shoot(page, "review-after-empty-read")
        _the_link_lands_on_content(page, "Check the GitHub page", "GitHub")
    finally:
        context.close()
