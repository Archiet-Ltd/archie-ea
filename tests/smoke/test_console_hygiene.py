"""Console-error and failed-request hygiene, on the pages each archetype uses.

A page that renders 200 while its console logs a TypeError, or while one of its
panels' API calls 404s, is silently degraded: the user sees a dashboard that is
quietly missing data, which this codebase treats as worse than an error page
(CLAUDE.md, "never invent data"). The window.modalManager regression shipped
exactly this way — every page rendered, every modal button was dead, and no
gate saw it because none of them executed the JavaScript.

Gated as a ratchet against an accepted baseline, like the axe audit next to it:
new console errors or newly failing requests on an audited page fail the build;
the recorded debt may only decrease. Regenerate with:

    SMOKE_HYGIENE_UPDATE_BASELINE=1 pytest tests/smoke/test_console_hygiene.py
"""

import json
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BASELINE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "console_hygiene_baseline.json"
)

# Same audit surface as the accessibility gate: one page per archetype.
AUDIT = [
    ("procurement", "/procurement/contracts"),
    ("procurement", "/procurement/compliance"),
    ("application_manager", "/my-applications/"),
    ("portfolio_manager", "/applications/"),
    ("business_architect", "/capability-map/"),
    ("cto", "/dashboard/overview"),
    ("enterprise_architect", "/ai-chat"),
    ("enterprise_architect", "/adm-kanban/"),
    ("enterprise_architect", "/solutions/"),
    ("cto", "/dashboard/rationalization"),
]

# Noise that is not an application defect: browser quirks, extension chatter,
# and the favicon probe. Keep this list short and literal — every entry here is
# a hole in the gate.
IGNORE_CONSOLE = (
    "favicon",
    "Download the React DevTools",
)
IGNORE_URLS = ("favicon.ico",)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass


def _normalise_console(text):
    """Stable fingerprint: strip URLs' query strings, line/col numbers, ids."""
    text = text.split("\n")[0]
    # A CSP violation quotes the policy, and the policy carries a fresh nonce on
    # every request. Left in, the same error never matched the baseline twice.
    text = re.sub(r"'nonce-[^']*'", "'nonce-N'", text)
    text = re.sub(r"'sha(?:256|384|512)-[^']*'", "'sha-N'", text)
    text = text[:200]
    text = re.sub(r":\d+:\d+", ":L:C", text)
    text = re.sub(r"\b\d{3,}\b", "N", text)
    return text


def _normalise_url(url, base):
    path = url.replace(base, "").split("?")[0]
    return re.sub(r"/\d+", "/N", path)[:160]


def _load_baseline():
    if not os.path.exists(BASELINE):
        return {"accepted": {}}
    with open(BASELINE, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def audited(browser, live_server, seeded):
    """Visit every audited page once and collect its hygiene report."""
    results = {}
    for archetype, path in AUDIT:
        email = seeded["emails"].get(archetype)
        if not email:
            continue
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        console = []
        failed = []
        page.on(
            "console",
            lambda m: console.append(_normalise_console(m.text))
            if m.type == "error"
            and not any(s in m.text for s in IGNORE_CONSOLE)
            else None,
        )
        page.on(
            "response",
            lambda r: failed.append((r.status, _normalise_url(r.url, live_server)))
            if r.status >= 400
            and r.url.startswith(live_server)
            and not any(s in r.url for s in IGNORE_URLS)
            else None,
        )
        _login(page, live_server, email)
        console.clear()
        failed.clear()
        try:
            page.goto(live_server + path, wait_until="load", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(4000)  # async panels settle
        except Exception as exc:
            results[path] = {"navigation_error": str(exc)[:200]}
            ctx.close()
            continue
        results[path] = {
            "console_errors": sorted(set(console)),
            "failed_requests": sorted({"%d %s" % (s, u) for s, u in failed}),
        }
        ctx.close()

    if os.environ.get("SMOKE_HYGIENE_UPDATE_BASELINE") == "1":
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "_comment": (
                        "Accepted console errors / failed requests per page. Every "
                        "entry is a real defect not fixed yet, not a statement the "
                        "page is healthy. Regenerate with "
                        "SMOKE_HYGIENE_UPDATE_BASELINE=1."
                    ),
                    "accepted": results,
                },
                fh,
                indent=1,
            )
    return results


@pytest.mark.parametrize("archetype,path", AUDIT, ids=[p for _, p in AUDIT])
def test_page_hygiene_no_worse_than_baseline(audited, archetype, path):
    result = audited.get(path)
    if result is None:
        pytest.skip("archetype %s not seeded" % archetype)
    assert "navigation_error" not in result, (
        "page did not load: %s" % result.get("navigation_error")
    )

    accepted = _load_baseline()["accepted"].get(path, {})
    new_console = set(result["console_errors"]) - set(accepted.get("console_errors", []))
    new_failed = set(result["failed_requests"]) - set(accepted.get("failed_requests", []))

    problems = []
    if new_console:
        problems.append("new console errors:\n  " + "\n  ".join(sorted(new_console)))
    if new_failed:
        problems.append("new failed requests:\n  " + "\n  ".join(sorted(new_failed)))
    assert not problems, (
        "%s regressed beyond console_hygiene_baseline.json:\n%s\n"
        "If these are pre-existing and being recorded for the first time, "
        "regenerate the baseline and review the diff." % (path, "\n".join(problems))
    )


def test_console_fingerprint_ignores_per_request_nonce():
    """Two violations that differ only by the CSP nonce are the same error."""
    template = (
        "Executing inline event handler violates the following Content Security "
        "Policy directive 'script-src 'self' 'nonce-%s' 'strict-dynamic' "
        "https://esm.sh'. Either the 'unsafe-inline' keyword, a hash, or a nonce "
        "is required to enable inline execution."
    )
    first = _normalise_console(template % "4dHBsJL9RD1nryci8faIgiHOuxMlSIAxC6PcXRuJS_g")
    second = _normalise_console(template % "Zq81xkTt0ePmW3aVnb2yHcUoRfDgLs7iJ9KXAEwMNvQ")
    assert first == second
    assert "nonce-N" in first
    # a genuinely different error must still differ
    assert _normalise_console("TypeError: x is undefined") != first
