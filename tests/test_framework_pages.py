"""One public page for every framework and canvas Entelim loads, with a hub, navbar and footer links.

Written from what a visitor and a search engine need: a page for each choice in the sign-up list, a
title and description of the right length, a FAQ whose markup matches what is shown, a real screenshot
where one was captured, honest wording about certification, and a place in the sitemap and llms.txt.
"""
import json
import re
from html import unescape

import pytest

from app.modules.account.services import onboarding_service
from app.modules.business_model_canvas import model_service
from app.services.public_pages import load_feed_pages, load_page
from app.services.site_registry import registry_links, site_registry

# The nine frameworks and thirteen canvases Entelim loads at sign-up, by their sign-up keys.
FRAMEWORK_KEYS = {"iso27001", "soc2", "gdpr", "togaf", "archimate", "safe", "itil4", "cobit2019", "nistcsf"}
CANVAS_KEYS = {
    "business_model_canvas", "lean_canvas", "empathy_map", "swot_analysis", "pestle_analysis",
    "porters_five_forces", "ansoff_matrix", "mckinsey_7s", "blue_ocean", "wardley_mapping",
    "balanced_scorecard", "okr", "product_vision_board",
}


def _pages():
    return [p for p in load_feed_pages() if p.family == "framework"]


def _text(html):
    return unescape(re.sub(r"<[^>]+>", " ", html))


def test_every_framework_and_canvas_has_a_page():
    pages = _pages()
    assert {p.front_matter.get("signup_framework") for p in pages if p.front_matter.get("kind") == "framework"} == FRAMEWORK_KEYS
    assert {p.front_matter.get("canvas_key") for p in pages if p.front_matter.get("kind") == "canvas"} == CANVAS_KEYS
    assert len(pages) == 22


def test_a_new_sign_up_choice_without_a_page_fails_here():
    """Every framework the sign-up offers and every canvas type the canvas module creates is covered."""
    covered_frameworks = {p.front_matter.get("signup_framework") for p in _pages()}
    offered = {key for key, _label, _note in onboarding_service.FRAMEWORKS}
    assert offered <= covered_frameworks, f"sign-up offers frameworks with no page: {sorted(offered - covered_frameworks)}"
    covered_canvases = {p.front_matter.get("canvas_key") for p in _pages()}
    offered_canvases = {key for key, _name in model_service.canvas_types()}
    assert offered_canvases <= covered_canvases, f"canvases with no page: {sorted(offered_canvases - covered_canvases)}"


def test_each_page_has_a_unique_short_title_and_description_and_sensible_length(app):
    titles, descriptions = set(), set()
    for page in _pages():
        assert len(page.title) < 60, f"{page.url}: title is {len(page.title)} characters"
        assert 60 <= len(page.description) < 155, f"{page.url}: description is {len(page.description)} characters"
        assert page.title not in titles and page.description not in descriptions, f"{page.url}: not unique"
        titles.add(page.title)
        descriptions.add(page.description)
        words = len(_text(page.body_html).split()) + sum(len((i["q"] + " " + i["a"]).split()) for i in page.faq)
        assert 600 <= words <= 1000, f"{page.url}: {words} words"
        assert 4 <= len(page.faq) <= 6, f"{page.url}: {len(page.faq)} questions"


def test_each_page_answers_first_and_has_the_sections(app):
    for page in _pages():
        first = re.search(r"</h1>\s*<p>(.*?)</p>", page.body_html, re.DOTALL)
        assert first and len(first.group(1).split()) >= 40, f"{page.url}: no opening paragraph that says what it is"
        headings = re.findall(r"<h2[^>]*>(.*?)</h2>", page.body_html)
        for needed in ("at sign-up", "What you get", "How to use it", "Why Entelim is different"):
            assert any(needed in h for h in headings), f"{page.url}: no section '{needed}'"
        assert len(re.findall(r'href="/frameworks/[a-z0-9-]+"', page.body_html)) >= 3, f"{page.url}: too few related links"


def test_no_page_claims_certification_or_compliance(app):
    claim = re.compile(r"(become|becomes|makes? (you|us|your \w+|a customer)|will make|gets? you|get you) (\w+ ){0,2}(certified|compliant)", re.I)
    for page in _pages():
        text = _text(page.body_html) + " " + " ".join(i["a"] for i in page.faq)
        assert not claim.search(text), f"{page.url}: claims certification or compliance"
        assert "certif" not in text.lower() or re.search(r"\bnot\b|\bno\b|does not|cannot|never", text, re.I)


def test_a_page_renders_title_description_faq_and_matching_markup(app):
    client = app.test_client()
    page = load_page("framework", slug="safe")
    response = client.get("/frameworks/safe")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f"<title>{page.title}" in html.replace("&amp;", "&")
    assert f'content="{page.description}"' in html
    shown = re.findall(r"<h3>(.*?)</h3>\s*<p>(.*?)</p>", html.split('data-testid="page-faq"')[1], re.DOTALL)
    assert len(shown) == len(page.faq) >= 4
    blocks = [json.loads(b) for b in re.findall(r'<script[^>]*type="application/ld\+json">(.*?)</script>', html, re.DOTALL)]
    graph = [n for b in blocks for n in b.get("@graph", [b])]
    faq = [n for n in graph if n.get("@type") == "FAQPage"]
    assert len(faq) == 1
    marked = [(q["name"], q["acceptedAnswer"]["text"]) for q in faq[0]["mainEntity"]]
    assert marked == [(i["q"], i["a"]) for i in page.faq]
    assert [unescape(q) for q, _ in shown] == [q for q, _ in marked]
    assert any(n.get("@type") == "BreadcrumbList" for n in graph)


def test_sign_up_link_names_the_framework(app):
    html = app.test_client().get("/frameworks/safe").get_data(as_text=True)
    assert 'href="/account/register?framework=safe"' in html
    canvas = app.test_client().get("/frameworks/lean-canvas").get_data(as_text=True)
    assert 'href="/account/register"' in canvas


def test_screenshots_are_real_captures_or_absent(app):
    client = app.test_client()
    with_image = client.get("/frameworks/iso-27001").get_data(as_text=True)
    assert 'data-testid="page-screenshot"' in with_image and "/static/img/modules/compliance-frameworks.webp" in with_image
    from pathlib import Path
    assert (Path(app.root_path) / "static/img/modules/compliance-frameworks.webp").is_file()
    for page in _pages():
        capture = page.front_matter.get("capture")
        if capture:
            assert (Path(app.root_path) / f"static/img/modules/{capture}.webp").is_file(), page.url


def test_hub_lists_every_page_and_the_navbar_and_footer_link_it(app):
    client = app.test_client()
    hub = client.get("/frameworks")
    assert hub.status_code == 200
    html = hub.get_data(as_text=True)
    for page in _pages():
        assert f'href="{page.url}"' in html, f"hub is missing {page.url}"
    with app.test_request_context("/"):
        links = registry_links(site_registry())
    for page in _pages():
        for where in ("navbar", "footer"):
            assert any(link["href"] == page.url and link["where"] == where for link in links), f"{page.url} not in the {where}"
    assert any(link["href"] == "/frameworks" and link["where"] == "navbar" for link in links)
    assert any(link["href"] == "/frameworks" and link["where"] == "footer" for link in links)


def test_sitemap_and_llms_list_every_page(app):
    client = app.test_client()
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    llms = client.get("/llms.txt").get_data(as_text=True)
    full = client.get("/llms-full.txt").get_data(as_text=True)
    for url in ["/frameworks"] + [p.url for p in _pages()]:
        assert f"https://entelim.org{url}</loc>" in sitemap, f"sitemap is missing {url}"
        assert f"https://entelim.org{url})" in llms, f"llms.txt is missing {url}"
    for page in _pages():
        assert page.title in full, f"llms-full.txt is missing {page.title}"
        assert f"Q: {page.faq[0]['q']}" in full


def test_every_internal_link_on_every_page_lands_on_a_page_with_content(app):
    client = app.test_client()
    seen = set()
    for page in _pages():
        html = client.get(page.url).get_data(as_text=True)
        for href in re.findall(r'href="(/frameworks[^"#?]*)"', html):
            if href in seen:
                continue
            seen.add(href)
            response = client.get(href)
            assert response.status_code == 200, f"{page.url} links to {href}: {response.status_code}"
            assert "<h1" in response.get_data(as_text=True)
    assert "/frameworks" in seen


def test_unknown_page_is_not_found(app):
    assert app.test_client().get("/frameworks/not-a-framework").status_code == 404


def _seo_geo_score(app, page, sitemap, llms):
    """Ten checks a search engine or an answer engine reads, one point each."""
    html = app.test_client().get(page.url).get_data(as_text=True)
    blocks = " ".join(re.findall(r'<script[^>]*type="application/ld\+json">(.*?)</script>', html, re.DOTALL))
    checks = {
        "title under 60": len(page.title) < 60,
        "description under 155": 60 <= len(page.description) < 155,
        "one h1": html.count("<h1") == 1,
        "canonical to its own address": f'rel="canonical" href="https://entelim.org{page.url}"' in html,
        "answers first": len(_text(page.body_html.split("</h1>", 1)[1].split("</p>", 1)[0]).split()) >= 40,
        "FAQPage markup": '"FAQPage"' in blocks and '"BreadcrumbList"' in blocks,
        "a captured screen with alt text": (len(re.findall(r'alt="[^"]{20,}"', html.split('data-testid="page-screenshot"')[-1][:1500])) > 0) if 'data-testid="page-screenshot"' in html else False,
        "three or more related links": len(re.findall(r'href="/frameworks/[a-z0-9-]+"', page.body_html)) >= 3,
        "in the sitemap and llms.txt": f"entelim.org{page.url}</loc>" in sitemap and f"entelim.org{page.url})" in llms,
        "a sign-up link": "/account/register" in html,
    }
    return sum(checks.values()), [name for name, ok in checks.items() if not ok]


def test_every_page_scores_at_least_eight_of_ten_on_the_seo_and_geo_checklist(app):
    client = app.test_client()
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    llms = client.get("/llms.txt").get_data(as_text=True)
    low = {}
    for page in _pages():
        score, missed = _seo_geo_score(app, page, sitemap, llms)
        if score < 8:
            low[page.url] = (score, missed)
    assert low == {}
