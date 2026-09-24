"""Capture all after-*.png screenshots for T-UI-2 round 2.

Uses the new menuitemcheckbox to toggle dark mode (not only localStorage).
Outputs to ~/verify/seats/T-UI-2-r2/shots/.
"""

import os
import pathlib
import subprocess
import sys
import time
import socket

# ── Config ────────────────────────────────────────────────────────────────
PORT = 5602
BASE = f"http://127.0.0.1:{PORT}"
SHOT_DIR = pathlib.Path(os.path.expanduser("~/verify/seats/T-UI-2-r2/shots"))
SHOT_DIR.mkdir(parents=True, exist_ok=True)

PASSWORD = "SmokeJourney!2026"
ENTERPRISE_ARCHITECT_EMAIL = None  # will be discovered from seeded data

# ── Helpers ───────────────────────────────────────────────────────────────

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    env = dict(os.environ)
    env.setdefault("SECRET_KEY", "smoke-only-not-secret-" + "x" * 16)
    env.setdefault("FLASK_CONFIG", "testing")
    env["FLASK_DEBUG"] = "0"
    log_path = f"/tmp/smoke-server-{port}.log"
    log_handle = open(log_path, "w+b")
    cmd = [
        sys.executable, "-m", "gunicorn", "manage:app",
        "--bind", f"127.0.0.1:{port}",
        "--workers", "2", "--threads", "8",
        "--timeout", "120", "--graceful-timeout", "20",
        "--error-logfile", "-",
    ]
    proc = subprocess.Popen(cmd, env=env, stdout=log_handle, stderr=subprocess.STDOUT)
    # Wait for server to be ready
    import urllib.request
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Server exited during boot")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5):
                break
        except urllib.error.HTTPError:
            break
        except Exception:
            pass
        time.sleep(3)
    else:
        proc.kill()
        raise RuntimeError(f"Server did not start within 180s")
    # Warm up
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/account/login", timeout=180).read()
    except Exception:
        pass
    print(f"[capture] Server ready on port {port} (pid={proc.pid})")
    return proc, log_path


def login(page, email):
    page.goto(f"{BASE}/account/login", wait_until="domcontentloaded", timeout=60000)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, f"Login failed for {email}"


def toggle_dark_via_menu(page):
    """Toggle dark theme through the user-menu menuitemcheckbox."""
    # Open user menu
    page.locator("#user-menu-btn").click()
    page.wait_for_timeout(400)
    # Click the dark theme menuitemcheckbox
    dark_item = page.locator('button[role="menuitemcheckbox"]')
    dark_item.click()
    page.wait_for_timeout(500)


def close_search_modal(page):
    """Close the search modal if it's open — use Escape key which is the most reliable."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def ensure_dark(page):
    """Ensure dark mode is on, using the menu item."""
    close_search_modal(page)
    is_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
    if not is_dark:
        toggle_dark_via_menu(page)
    # Wait for the dark class
    page.wait_for_function(
        "() => document.documentElement.classList.contains('dark')",
        timeout=5000,
    )
    page.wait_for_timeout(500)


def ensure_light(page):
    """Ensure dark mode is off."""
    close_search_modal(page)
    is_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
    if is_dark:
        toggle_dark_via_menu(page)
    page.wait_for_function(
        "() => !document.documentElement.classList.contains('dark')",
        timeout=5000,
    )
    page.wait_for_timeout(500)


def shot(page, name, full_page=True):
    path = SHOT_DIR / name
    page.screenshot(path=str(path), full_page=full_page)
    size = path.stat().st_size
    print(f"  [shot] {name} ({size} bytes)")
    return path


def get_enterprise_architect_email():
    """Discover the seeded enterprise architect email."""
    import importlib
    sys.path.insert(0, os.getcwd())
    from app import create_app, db
    app = create_app("testing")
    with app.app_context():
        from app.models.user import User
        user = User.query.filter_by(enterprise_role="enterprise_architect").first()
        if user:
            return user.email
    return None


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    from playwright.sync_api import sync_playwright

    # Discover email
    email = get_enterprise_architect_email()
    if not email:
        print("[capture] No enterprise_architect found — using seeded fixture approach")
        # Fall back to creating a user
        from app import create_app, db
        import uuid
        app = create_app("testing")
        with app.app_context():
            from app.models.user import User
            from app.models.organization import Organization
            orgs = Organization.query.limit(1).all()
            if not orgs:
                org = Organization(name="Smoke Test Org")
                db.session.add(org)
                db.session.flush()
            else:
                org = orgs[0]
            suffix = uuid.uuid4().hex[:8]
            user = User(
                email=f"capture-{suffix}@example.com",
                first_name="Capture",
                last_name="Tester",
                organization_id=org.id,
                confirmed=True,
                enterprise_role="enterprise_architect",
            )
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            email = user.email
            print(f"[capture] Created user: {email}")

    # Start server
    proc, log_path = start_server(PORT)
    pid = proc.pid
    print(f"[capture] Server pid={pid}")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()

            # Sign in
            login(page, email)

            # ── Solutions page ──────────────────────────────────────────
            print("[capture] Solutions page...")
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)

            # Light mode
            ensure_light(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "after-solutions-1280x800-light.png")

            # Dark mode
            ensure_dark(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "after-solutions-1280x800-dark.png")

            # Mobile viewports
            page.set_viewport_size({"width": 390, "height": 844})
            ensure_light(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "after-solutions-390x844-light.png")

            ensure_dark(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "after-solutions-390x844-dark.png")

            # ── Solutions search open ────────────────────────────────────
            page.set_viewport_size({"width": 1280, "height": 900})
            print("[capture] Solutions search open...")

            ensure_light(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            page.locator("#search-modal-trigger").click()
            page.wait_for_timeout(600)
            shot(page, "after-solutions-search-open-1280x800-light.png")
            close_search_modal(page)

            ensure_dark(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            page.locator("#search-modal-trigger").click()
            page.wait_for_timeout(600)
            shot(page, "after-solutions-search-open-1280x800-dark.png")
            close_search_modal(page)

            page.set_viewport_size({"width": 390, "height": 844})
            ensure_light(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "after-solutions-search-open-390x844-light.png")

            ensure_dark(page)
            page.goto(f"{BASE}/solutions/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            shot(page, "after-solutions-search-open-390x844-dark.png")

            # ── Composer ─────────────────────────────────────────────────
            page.set_viewport_size({"width": 1280, "height": 900})
            print("[capture] Composer page...")
            close_search_modal(page)
            ensure_dark(page)
            page.goto(f"{BASE}/archimate/composer", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            shot(page, "after-composer-1280x800-dark.png")

            # ── Architecture Journey ─────────────────────────────────────
            page.set_viewport_size({"width": 1280, "height": 900})
            print("[capture] Architecture Journey page...")
            close_search_modal(page)
            ensure_dark(page)
            page.goto(f"{BASE}/architecture-journey/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # Check if dark actually applied
            is_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
            print(f"  [capture] Architecture Journey dark class present: {is_dark}")
            shot(page, "after-architecture-journey-1280x800-dark.png")

            if not is_dark:
                print("  [capture] WARNING: dark class not present on architecture-journey page!")

            browser.close()
    finally:
        print(f"[capture] Stopping server (pid={pid})...")
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        print(f"[capture] Server stopped.")

    print(f"\n[capture] Done. {len(list(SHOT_DIR.glob('*.png')))} screenshots in {SHOT_DIR}")


if __name__ == "__main__":
    main()