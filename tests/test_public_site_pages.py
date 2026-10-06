"""Tests for the fixed top-level marketing/legal pages (about, security, privacy,
terms, contact, features, pricing, docs) and the /signup and /register redirects.

Covers:
  Every listed path answers 200 (or 301 to its canonical page) on a fresh
  database, requested anonymously.
  /signup and /register redirect (301) to the real sign-up page rather than
  duplicating the form.
  Every new page is linked from the public navbar and the shared public
  footer, so nothing is a dead link.
"""

from __future__ import annotations

import pytest

from app.services.public_pages import load_page

SITE_PAGES = [
    "about",
    "security",
    "privacy",
    "terms",
    "contact",
    "features",
    "pricing",
    "docs",
]


def test_every_site_page_returns_200(app):
    """Every fixed top-level page answers 200, requested anonymously."""
    with app.test_client() as client:
        for slug in SITE_PAGES:
            rv = client.get(f"/{slug}")
            assert rv.status_code == 200, f"/{slug} returned {rv.status_code}"


def test_every_site_page_has_title_and_h1(app):
    """Every site page has its title in <title> and an <h1>."""
    with app.test_client() as client:
        for slug in SITE_PAGES:
            rv = client.get(f"/{slug}")
            html = rv.data.decode()
            assert "<title>" in html, f"/{slug}: no <title>"
            assert "<h1" in html, f"/{slug}: no <h1>"


def test_every_site_page_has_footer_with_all_links(app):
    """Every site page renders the shared footer linking to every other site page."""
    with app.test_client() as client:
        for slug in SITE_PAGES:
            rv = client.get(f"/{slug}")
            html = rv.data.decode()
            for other_slug in SITE_PAGES:
                assert f'href="/{other_slug}"' in html, (
                    f"/{slug}: footer is missing a link to /{other_slug}"
                )


def test_home_page_footer_links_every_site_page(app):
    """The home page's footer links to every new page (no dead links from the entry point)."""
    with app.test_client() as client:
        rv = client.get("/")
        html = rv.data.decode()
        for slug in SITE_PAGES:
            assert f'href="/{slug}"' in html, f"home page footer missing link to /{slug}"


def test_home_page_navbar_links_key_site_pages(app):
    """The public navbar surfaces the primary marketing pages, not just the footer."""
    with app.test_client() as client:
        rv = client.get("/")
        html = rv.data.decode()
        for slug in ("features", "pricing", "about", "docs"):
            assert f'href="/{slug}"' in html, f"navbar missing link to /{slug}"


def test_signup_redirects_to_real_register_form(app):
    """/signup is a 301 to the real sign-up page; no second form is built."""
    with app.test_client() as client:
        rv = client.get("/signup", follow_redirects=False)
        assert rv.status_code == 301
        assert rv.location.endswith("/account/register")


def test_register_redirects_to_real_register_form(app):
    """/register is a 301 to the real sign-up page; no second form is built."""
    with app.test_client() as client:
        rv = client.get("/register", follow_redirects=False)
        assert rv.status_code == 301
        assert rv.location.endswith("/account/register")


def test_signup_and_register_redirects_are_anonymous(app):
    """Following /signup and /register anonymously lands on the real sign-up form."""
    with app.test_client() as client:
        rv = client.get("/signup", follow_redirects=True)
        assert rv.status_code == 200
        assert b"Create an account" in rv.data

        rv = client.get("/register", follow_redirects=True)
        assert rv.status_code == 200
        assert b"Create an account" in rv.data


def test_pricing_page_matches_home_page_tiers(app):
    """The /pricing page carries the same tier names as the home page, not invented numbers."""
    with app.test_client() as client:
        home = client.get("/").data.decode()
        pricing = client.get("/pricing").data.decode()
        for tier in ("Community", "Startup", "Team", "Enterprise"):
            assert tier in home, f"home page missing tier '{tier}' (test assumption stale)"
            assert tier in pricing, f"/pricing missing tier '{tier}'"


def test_security_page_makes_no_certification_claim(app):
    """The security page must not claim a certification the codebase has no evidence for."""
    with app.test_client() as client:
        html = client.get("/security").data.decode()
        for claim in ("SOC 2 certified", "ISO 27001 certified", "HIPAA compliant", "PCI DSS compliant"):
            assert claim not in html, f"/security asserts an unearned claim: {claim}"


def test_contact_page_has_no_invented_email(app):
    """The contact page must not invent a support mailbox that doesn't exist in the codebase."""
    with app.test_client() as client:
        html = client.get("/contact").data.decode()
        assert "mailto:" not in html, "/contact invents an email address; no support mailbox exists"
        assert "support@example.com" not in html
        assert "https://reqarchitect.com" in html
        assert "https://archiet.com" in html


def test_contact_page_has_no_waiting_list_or_launch_framing(app):
    """/contact answers sales questions directly; no waiting-list or
    pre-launch framing (the product is live and priced)."""
    with app.test_client() as client:
        html = client.get("/contact").data.decode()
        assert "waiting list" not in html.lower()
        assert "when Entelim launches" not in html
        assert "/#waitlist" not in html


def test_contact_page_has_sales_enquiry_form(app):
    """/contact offers the same enquiry form as the paid offer pages, and
    keeps the AGPL/security-report routing and the sign-in link."""
    with app.test_client() as client:
        html = client.get("/contact").data.decode()
        assert 'data-testid="offer-inquiry-form"' in html
        assert "AGPL" in html
        assert "security issue" in html
        assert "/account/login" in html


def test_load_page_site_family_known_and_unknown_slug():
    """load_page('site', slug) resolves every listed page and returns None for the unknown."""
    for slug in SITE_PAGES:
        page = load_page("site", slug=slug)
        assert page is not None, f"load_page('site', '{slug}') returned None"
        assert page.url == f"/{slug}"
    assert load_page("site", slug="does-not-exist") is None


@pytest.mark.parametrize("slug", SITE_PAGES)
def test_site_pages_included_in_sitemap_and_llms_txt(app, slug):
    """Each new page is discoverable through the existing sitemap and llms.txt, unchanged."""
    with app.test_client() as client:
        sitemap = client.get("/sitemap.xml").data.decode()
        llms = client.get("/llms.txt").data.decode()
        assert f"/{slug}" in sitemap, f"/sitemap.xml missing /{slug}"
        assert f"/{slug}" in llms, f"/llms.txt missing /{slug}"


# ── No dead link to the not-yet-public repository ──────────────────────────


def test_no_page_links_to_the_private_repository(app):
    """Nothing on the home page or any site page links to the archiet-ltd/entelim
    repository: it is not public, so that link 404s for every visitor."""
    with app.test_client() as client:
        pages_to_check = ["/"] + [f"/{slug}" for slug in SITE_PAGES]
        for path in pages_to_check:
            html = client.get(path).data.decode()
            assert "archiet-ltd/entelim" not in html, (
                f"{path} links to the private archiet-ltd/entelim repository"
            )
            assert "github.com" not in html, (
                f"{path} links to github.com; the repository is not public"
            )
