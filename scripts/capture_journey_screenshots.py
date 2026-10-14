"""Capture Architecture Journey screenshots without pytest in the way.

The smoke suite cannot start Playwright's sync API here: some plugin in the test
environment already owns an asyncio loop, and `sync_playwright()` refuses to run
inside one. Disabling pytest-asyncio and anyio did not clear it. Rather than keep
guessing at plugin flags, this drives the browser directly -- no pytest, no
plugins, no loop.

It boots a real server against a real database and photographs what a real user
sees. If the page does not render, it says so and exits non-zero rather than
saving a picture of an error page and calling it evidence.

    python scripts/capture_journey_screenshots.py [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from capture_screenshots import _dismiss_onboarding  # noqa: E402

PASSWORD = "ScreenshotJourney!2026"
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed():
    """Create the tenant, the user and one journey the screenshots need.

    Real rows through the real models -- nothing is stubbed for the camera. The
    journey carries links so the home's count panels show measured numbers rather
    than a screenshot of an empty state pretending to be a populated one.
    """
    sys.path.insert(0, str(REPO))
    from app import create_app, db
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.architecture_journey_link import ArchitectureJourneyLink
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        Role.insert_roles()

        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"Screenshot Org {suffix}", slug=f"shot-{suffix}")
        db.session.add(org)
        db.session.flush()

        # @example.com, not @example.test. The login form applies WTForms' Email()
        # validator, which rejects a domain that does not resolve -- so a .test
        # address is accepted by the ORM, passes verify_password perfectly, and
        # still fails at the browser form with a re-rendered 200 that looks
        # exactly like a wrong password. The smoke harness uses example.com for
        # the same reason.
        email = f"shots-{suffix}@example.com"
        user = User(
            email=email,
            first_name="Ada",
            last_name="Architect",
            confirmed=True,
            organization_id=org.id,
            enterprise_role="business_architect",
        )
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()

        journey = ArchitectureJourney(
            owner_id=user.id,
            organization_id=org.id,
            title="Regional operating-model redesign",
            intent="operating_model",
            selected_layers=["motivation", "business", "application", "governance"],
            selected_deliverables=[],
            current_stage="shape",
        )
        db.session.add(journey)
        db.session.flush()

        for entity_type, entity_id, relation in (
            ("decision", 101, "produces"),
            ("risk", 202, "impacts"),
            ("document", 303, "informs"),
            ("arb_review", 404, "governs"),
        ):
            db.session.add(
                ArchitectureJourneyLink(
                    journey_id=journey.id,
                    organization_id=org.id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    relation=relation,
                    created_by_id=user.id,
                )
            )

        db.session.commit()
        return email, journey.id


def _boot(port, log_path):
    env = dict(os.environ)
    env.setdefault("FLASK_CONFIG", "testing")
    database_url = env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")
    if database_url:
        env["DATABASE_URL"] = database_url
        env["TEST_DATABASE_URL"] = database_url

    handle = open(log_path, "w", encoding="utf-8")
    # Output to a file, never a pipe: a pipe deadlocks the child at 64KB, which is
    # the trap the smoke harness records having hit.
    process = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "manage", "run",
         "--no-reload", "--port", str(port)],
        cwd=str(REPO), env=env, stdout=handle, stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"server exited early; see {log_path}")
        try:
            urllib.request.urlopen(base + "/health", timeout=3)
            return process, base
        except urllib.error.HTTPError:
            return process, base          # any HTTP answer means it is serving
        except Exception:
            time.sleep(2)
    process.terminate()
    raise SystemExit(f"server did not boot within 180s; see {log_path}")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=60000)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")

    # Wait for the URL to actually change rather than sleeping a fixed interval.
    # A flat 2.5s was the bug on the first attempt: this machine takes longer than
    # that to complete the POST, so the check fired while the browser was still on
    # the login page and reported a credential failure that had not happened. The
    # smoke harness waits up to 90s here for the same reason.
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=90000)
    except Exception:
        pass
    page.wait_for_timeout(1000)
    if "/account/login" in page.url:
        raise SystemExit(
            "could not sign in; the page said: "
            + " ".join(page.inner_text("body").split())[:200]
        )


def _capture(page, target, width, height):
    """Photograph the whole screen, not just the first viewport of it.

    `full_page=True` is not enough here: the admin shell pins its own height and
    scrolls an inner `overflow-auto` container, so the document never grows and
    Playwright returns exactly one viewport. The result looks like a complete page
    and silently omits everything below the fold -- on the journey home, that is
    every count panel.

    So measure the tallest scrollable element, grow the viewport to it, capture,
    and put the viewport back.
    """
    tallest = page.evaluate(
        """() => {
            let max = document.documentElement.scrollHeight;
            document.querySelectorAll('*').forEach((el) => {
                if (el.scrollHeight > max) { max = el.scrollHeight; }
            });
            return max;
        }"""
    )
    grown = min(int(tallest) + 80, 12000)
    if grown > height:
        page.set_viewport_size({"width": width, "height": grown})
        page.wait_for_timeout(600)
    page.screenshot(path=str(target), full_page=True)
    page.set_viewport_size({"width": width, "height": height})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO / "screenshots"))
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    email, journey_id = _seed()
    port = _free_port()
    log_path = out / "server.log"
    process, base = _boot(port, log_path)

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for name, (width, height) in VIEWPORTS.items():
                    context = browser.new_context(
                        viewport={"width": width, "height": height}
                    )
                    page = context.new_page()
                    _login(page, base, email)

                    for label, path in (
                        ("hub", "/architecture-journey/"),
                        ("home", f"/architecture-journey/work/{journey_id}"),
                    ):
                        response = page.goto(
                            base + path, wait_until="domcontentloaded", timeout=60000
                        )
                        page.wait_for_timeout(1500)
                        if response is None or response.status >= 400:
                            status = "no response" if response is None else response.status
                            raise SystemExit(
                                f"{path} returned {status}; refusing to save a "
                                f"screenshot of an error page as evidence"
                            )
                        headings = page.locator("h1").count()
                        if headings != 1:
                            raise SystemExit(f"{path} has {headings} <h1> elements, expected 1")

                        _dismiss_onboarding(page)
                        target = out / f"journey-{label}-{name}.png"
                        _capture(page, target, width, height)
                        print(f"saved {target} ({target.stat().st_size} bytes)")

                    context.close()
            finally:
                browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()

    print("done")


if __name__ == "__main__":
    main()
