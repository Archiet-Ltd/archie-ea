"""Fetching the consented site: its robots.txt, its pages, and a real browser for the few that need one.

Every address is checked by the outbound-URL guard before it is requested, redirects are
followed one hop at a time and stay on the same site, and a path robots.txt excludes is
never requested.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import urllib3

from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

from .extraction import same_site, visible_text

USER_AGENT = "EntelimProfileReader/1.0 (+reads only the website its owner consented to)"
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 5
MAX_CRAWL_DELAY = 5.0


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    text: str


class FetchRefused(Exception):
    """The address was not requested (private, off-site, or not a web address)."""


def _checked(url: str, site: str) -> str:
    try:
        validate_user_supplied_url(url)
    except BlockedOutboundURL as exc:
        raise FetchRefused(str(exc)) from exc
    if not same_site(url, site):
        raise FetchRefused("not on the consented site")
    return url


class Fetcher:
    def __init__(self, site: str, timeout: float = 15.0):
        self.site = site
        self.timeout = timeout
        self._robots: Optional[RobotFileParser] = None
        self._blocked_everything = False
        self.robots_unanswered = False  # robots.txt could not be fetched: the site did not answer, which is not a refusal
        self.crawl_delay = 0.0

    def get(self, url: str, timeout: Optional[float] = None) -> Page:
        """One page, following redirects within the site. Raises FetchRefused or requests errors."""
        timeout = min(timeout or self.timeout, self.timeout)
        current = _checked(url, self.site)
        for _ in range(MAX_REDIRECTS + 1):
            response = requests.get(
                current, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
                timeout=timeout, allow_redirects=False, stream=True,
            )
            try:
                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                    current = _checked(urljoin(current, response.headers["Location"]), self.site)
                    continue
                try:
                    body = response.raw.read(MAX_BYTES + 1, decode_content=True)
                except urllib3.exceptions.HTTPError as exc:  # a body that stops arriving is a failed page, not a crash
                    raise requests.exceptions.ConnectionError("the page body did not arrive: %s" % exc) from exc
                encoding = response.encoding or "utf-8"
                return Page(current, response.status_code, response.headers.get("Content-Type", ""),
                            body[:MAX_BYTES].decode(encoding, errors="replace"))
            finally:
                response.close()
        raise FetchRefused("too many redirects")

    def load_robots(self) -> None:
        """Read robots.txt once. Missing means everything is allowed; unreachable means nothing is."""
        parser = RobotFileParser()
        root = "%s://%s" % (urlparse(self.site).scheme, urlparse(self.site).netloc)
        try:
            page = self.get(root + "/robots.txt")
        except (FetchRefused, requests.RequestException):
            self._blocked_everything = self.robots_unanswered = True
            self._robots = parser
            return
        if page.status >= 500:
            self._blocked_everything = self.robots_unanswered = True
        elif page.status == 200:
            parser.parse(page.text.splitlines())
            delay = parser.crawl_delay(USER_AGENT)
            self.crawl_delay = min(float(delay), MAX_CRAWL_DELAY) if delay else 0.0
        else:
            parser.parse([])
        self._robots = parser

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            self.load_robots()
        if self._blocked_everything:
            return False
        return self._robots.can_fetch(USER_AGENT, url)


class Browser:
    """One headless Chromium for the whole crawl, started the first time a page needs it."""

    def __init__(self, site: str):
        from flask import current_app

        self.site = site
        self._app = current_app._get_current_object()  # the route handler below runs outside the request's context
        self._playwright = None
        self._browser = None

    def _start(self):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def render(self, url: str, timeout: float) -> Page:
        """The page's HTML after its scripts have run. Requests to addresses the guard refuses are dropped."""
        if self._browser is None:
            self._start()
        context = self._browser.new_context(user_agent=USER_AGENT)

        def screen(route):
            try:
                with self._app.app_context():
                    validate_user_supplied_url(route.request.url)
            except BlockedOutboundURL:
                route.abort()
                return
            route.continue_()

        context.route("**/*", screen)
        try:
            page = context.new_page()
            page.set_default_timeout(timeout * 1000)
            response = page.goto(url, wait_until="load")
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout, 5) * 1000)
            except Exception:  # a page that never goes idle is read as it stands
                pass
            deadline = time.monotonic() + min(timeout, 6)
            while time.monotonic() < deadline and len(visible_text(page.content())) < 200:
                page.wait_for_timeout(250)
            final = page.url
            _checked(final, self.site)
            return Page(final, response.status if response else 200, "text/html", page.content())
        finally:
            context.close()

    def close(self) -> None:
        for closer in (getattr(self._browser, "close", None), getattr(self._playwright, "stop", None)):
            if closer:
                try:
                    closer()
                except Exception:
                    pass
        self._browser = self._playwright = None
