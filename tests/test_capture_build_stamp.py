"""The capture pipeline's build-stamp selector must match the sidebar's build
line, and nothing else in the sidebar footer.

scripts/capture_screenshots.py strips BUILD_STAMP_SELECTOR before every public
capture (screenshot and recording). This test renders a real authenticated
page the same way the capture pipeline does and pins the selector to the
template it targets, so a future markup change to the build line breaks this
test -- not the public images, silently, months later.
"""
from __future__ import annotations

import pathlib
import sys
import uuid

import pytest
from bs4 import BeautifulSoup

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from capture_screenshots import BUILD_STAMP_SELECTOR  # noqa: E402

pytestmark = pytest.mark.usefixtures("db_session")


def _signed_in_client(app, db_session, make_org, login_as):
    from app.models.user import User

    org = make_org("capture-build-stamp")
    user = User(
        email=f"build-stamp-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Capture",
        last_name="Stamp",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="solution_architect",
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


def _render_dashboard(app, db_session, make_org, login_as):
    client = _signed_in_client(app, db_session, make_org, login_as)
    response = client.get("/dashboard/overview")
    assert response.status_code == 200, response.get_data(as_text=True)[:2000]
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


def test_build_stamp_selector_matches_exactly_one_element_with_the_build_text(
    app, db_session, make_org, login_as,
):
    from app._bootstrap.build_info import get_build_id

    soup = _render_dashboard(app, db_session, make_org, login_as)

    matches = soup.select(BUILD_STAMP_SELECTOR)
    assert len(matches) == 1, (
        f"expected exactly one element to match {BUILD_STAMP_SELECTOR!r}, "
        f"found {len(matches)}"
    )
    assert matches[0].get_text(strip=True) == f"Build {get_build_id()}"


def test_build_stamp_selector_does_not_match_other_sidebar_footer_chrome(
    app, db_session, make_org, login_as,
):
    """The capture scripts cannot strip BUILD_STAMP_SELECTOR and accidentally
    strip the rest of the sidebar footer with it -- .sidebar-footer-text sits
    on six other elements this must stay disjoint from."""
    soup = _render_dashboard(app, db_session, make_org, login_as)
    matched_by_build_selector = soup.select(BUILD_STAMP_SELECTOR)

    footer_text_elements = soup.select(".sidebar-footer-text")
    assert len(footer_text_elements) > 1, (
        "expected .sidebar-footer-text on more than just the build line "
        "(user name/email, the log-out link, and, when present, the "
        "'All modules' label) -- this test needs that to tell the two apart"
    )
    for element in footer_text_elements:
        if element in matched_by_build_selector:
            continue
        assert element not in matched_by_build_selector

    user_name = soup.select_one(".sidebar-footer-text p.font-medium")
    assert user_name is not None, "expected the signed-in user's name block to render"
    assert user_name not in matched_by_build_selector

    logout_link = soup.select_one('a.sidebar-footer-text[aria-label="Log out"]')
    assert logout_link is not None, "expected the log-out link to render for a signed-in user"
    assert logout_link not in matched_by_build_selector

    for span in soup.select("span.sidebar-footer-text"):
        if span.get_text(strip=True) == "All modules":
            assert span not in matched_by_build_selector
