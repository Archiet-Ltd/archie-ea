"""Reading an organisation's own website into profile proposals.

A local fixture site stands in for the company site: static pages, one page that only
shows its content after JavaScript runs, and a robots.txt that excludes a path. The model
is a stand-in that answers only from the page text it is handed, so every fact in a
proposal can be traced to the page it was read from.
"""

from __future__ import annotations

import json
import re
import threading
import time
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

LINKEDIN = "https://www.linkedin.com/company/acme-robotics"

HOME = """<!doctype html><html><head><title>Acme Robotics | Warehouse robots</title>
<meta name="description" content="Warehouse robots that pick and pack.">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization",
"name":"Acme Robotics Ltd","description":"Warehouse robots that pick and pack","foundingDate":"2014",
"email":"info@acme-robotics.test","telephone":"+44 20 7946 0000","logo":"/static/logo.svg",
"sameAs":["%(linkedin)s","https://twitter.com/acmerobotics","https://github.com/acme-robotics"]}</script>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TEST"></script>
</head><body><header><nav>
<a href="/about">About</a> <a href="/products">Products</a> <a href="/contact">Contact</a>
<a href="/app">Customer portal</a> <a href="/private/plans">Plans</a>
<a href="%(other)s/elsewhere">Partner site</a>
</nav></header>
<main><h1>Acme Robotics</h1><p>We build warehouse robots that pick and pack orders for logistics
operators, delivered as a monthly subscription to businesses.</p></main></body></html>"""

ABOUT = """<html><head><title>About Acme Robotics</title></head><body><main>
<h1>About us</h1><p>Founded in 2014, we are a team of about 40 people.</p>
<ul><li>Dana Whitfield, Chief Executive Officer</li><li>Priya Raman, Chief Technology Officer</li></ul>
<address>12 Dock Road, Bristol, BS1 4XY, United Kingdom</address></main></body></html>"""

PRODUCTS = """<html><head><title>Products</title></head><body><main><h1>Products</h1>
<p>Product: PickBot picks and packs orders twice as fast as a manual line.</p>
<p>We are ISO 27001 certified.</p><p>Integrates with SAP and Shopify.</p>
<p>Case study: Norland Foods cut picking time by 30%.</p></main></body></html>"""

CONTACT = """<html><head><title>Contact</title></head><body><main><h1>Contact</h1>
<p><a href="mailto:sales@acme-robotics.test">sales@acme-robotics.test</a></p></main></body></html>"""

APP_SHELL = """<html><head><title>Portal</title></head><body><div id="root"></div>
<script>
document.getElementById('root').innerHTML = '<h1>Customer portal</h1>'
 + '<p>Case study: Harbor Freight cut order errors by 20% using Acme Robotics fleets.</p>'
 + '<p>Our customers run the portal to track every robot, every shift and every order, with live '
 + 'dashboards for warehouse managers and finance teams across several sites.</p>';
</script></body></html>"""

ROBOTS = "User-agent: *\nDisallow: /private/\n"


class _Site:
    """A throwaway web server. ``pages`` maps a path to (status, content type, body)."""

    def __init__(self, pages, delay=0.0):
        self.pages = pages
        self.hits = []
        self.delay = delay
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                site.hits.append(self.path)
                if site.delay:
                    time.sleep(site.delay)
                status, kind, body = site.pages.get(self.path, (404, "text/html", "<p>missing</p>"))
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _html(body, kind="text/html"):
    return (200, kind, body)


@pytest.fixture
def other_site():
    site = _Site({"/elsewhere": _html("<p>another company</p>")})
    yield site
    site.stop()


@pytest.fixture
def site(other_site):
    pages = {
        "/": _html(HOME % {"linkedin": LINKEDIN, "other": other_site.url}),
        "/about": _html(ABOUT),
        "/products": _html(PRODUCTS),
        "/contact": _html(CONTACT),
        "/app": _html(APP_SHELL),
        "/private/plans": _html("<p>Secret pricing: Case study: Hidden Corp.</p>"),
        "/robots.txt": (200, "text/plain", ROBOTS),
    }
    server = _Site(pages)
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def internal_hosts_allowed(app):
    """The fixture site is on loopback; the outbound guard allows that only where configured."""
    previous = app.config.get("ALLOW_INTERNAL_WEBHOOK_TARGETS")
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = True
    yield
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = previous


def _path(url):
    return re.sub(r"^https?://[^/]+", "", url or "") or "/"


def fake_model(prompt):
    """Answers only from the page handed over, like a careful reader would."""
    if "Business Model Inference" in prompt:
        return json.dumps({
            "business_model": {"revenue_model": "Subscription", "target_market": "B2B",
                               "market_segment": "Enterprise", "geographic_focus": "Global", "confidence": 0.8},
            "company_stage": {"stage": "Early Stage", "estimated_age_years": 12, "estimated_employees": 40,
                              "confidence": 0.7},
            "technology_stack": {"frontend": ["React"], "backend": ["Python"], "infrastructure": ["AWS"],
                                 "analytics": ["Google Analytics"], "marketing": ["HubSpot"]},
            "competitive_positioning": {"value_proposition": "Twice the picking speed of a manual line",
                                        "competitive_advantages": ["Fast deployment"],
                                        "market_position": "Challenger"},
            "data_quality": {"completeness_score": 0.82, "reliability_score": 0.7, "missing_critical_data": []},
        })
    if "Indicators for Full Chromium Rendering" in prompt:
        return json.dumps({"rendering_strategy": "static", "confidence": 0.9, "reasoning": "complete html"})
    if "HTML structure analyzer" in prompt:
        return json.dumps({"content_containers": [], "navigation_elements": [],
                           "extraction_strategy": {"recommended_selectors": [], "confidence": 0.5}})
    url = re.search(r"\*\*Page URL\*\*: (\S+)", prompt).group(1)
    html = prompt.split("```html", 1)[1].rsplit("```", 1)[0]
    out = {"company": {"name": "Acme Robotics Ltd" if "Acme Robotics" in html else "", "website": url},
           "field_confidence": {}}
    if "warehouse robots" in html:
        out.update(category="Transportation & Logistics", business_model="B2B Robotics-as-a-Service",
                   industry_sector="Logistics Tech", target_market="Warehouse operators")
        out["field_confidence"] = {"category": 0.9, "business_model": 0.8, "industry_sector": 0.8,
                                   "target_market": 0.7}
    people = re.findall(r"<li>([A-Z]\w+ [A-Z]\w+), ([A-Za-z ]+)</li>", html)
    if people:
        out["people"] = [{"name": n, "title": t, "confidence": 0.9} for n, t in people]
    if "Dock Road" in html:
        out["location"] = {"country": "United Kingdom", "city": "Bristol", "region": "England",
                           "address": "12 Dock Road, Bristol, BS1 4XY, United Kingdom"}
    if "about 40 people" in html:
        out["team_size"] = {"min": 30, "max": 50, "confidence": 0.6}
    out["products"] = re.findall(r"Product: (\w+)", html)
    out["value_propositions"] = re.findall(r"(picks and packs orders twice as fast as a manual line)", html)
    out["compliance"] = ["ISO 27001"] if "ISO 27001" in html else []
    out["integration_partners"] = re.findall(r"Integrates with (\w+) and (\w+)", html) and \
        list(re.findall(r"Integrates with (\w+) and (\w+)", html)[0]) or []
    out["case_studies"] = re.findall(r"Case study: ([^<]+?)(?:\.|<)", html)
    out["links"] = []
    return json.dumps(out)


def _org(make_org, website, consent=True):
    from app import db
    from app.modules.account.services import onboarding_service as onboarding

    org = make_org("crawl")
    db.session.flush()
    if website:
        onboarding.save_website(org, website, consent, None)
    # the reading runs in a tenant scope that detaches loaded rows, so tests hold the id, not the row
    return SimpleNamespace(id=org.id, settings=json.loads(json.dumps(org.settings or {})), name=org.name)


def _crawl(org, **kwargs):
    from app.modules.organization.website_crawl import crawl_organization_website

    kwargs.setdefault("llm", fake_model)
    return crawl_organization_website(org.id, **kwargs)


def _by_field(org_id):
    from app.modules.organization.website_crawl import pending_website_proposals

    found = {}
    for row in pending_website_proposals(org_id):
        found.setdefault(row["field"], []).append(row)
    return found


FLOOR = {
    # field: (value fragment, the page the value must be credited to)
    "company_name": ("Acme Robotics Ltd", "/"),
    "tagline": ("Warehouse robots that pick and pack", "/"),
    "founded_year": ("2014", "/"),
    "contact_email": ("info@acme-robotics.test", "/"),
    "contact_phone": ("+44 20 7946 0000", "/"),
    "logo": ("/static/logo.svg", "/"),
    "linkedin_url": (LINKEDIN, "/"),
    "category": ("Transportation & Logistics", "/"),
    "business_model": ("B2B Robotics-as-a-Service", "/"),
    "revenue_model": ("Subscription", None),
    "industry_sector": ("Logistics Tech", "/"),
    "target_market": ("Warehouse operators", "/"),
    "market_segment": ("Enterprise", None),
    "geographic_focus": ("Global", None),
    "hq_country": ("United Kingdom", "/about"),
    "hq_city": ("Bristol", "/about"),
    "hq_region": ("England", "/about"),
    "hq_address": ("12 Dock Road", "/about"),
    "team_size": ("30", "/about"),
    "company_stage": ("Early Stage", None),
    "completeness_score": ("0.82", None),
    "value_proposition": ("Twice the picking speed", None),
    "market_position": ("Challenger", None),
    "key_person": ("Dana Whitfield", "/about"),
    "social_profile": ("twitter.com/acmerobotics", "/"),
    "technology": ("React", "/"),
    "product": ("PickBot", "/products"),
    "compliance": ("ISO 27001", "/products"),
    "integration_partner": ("SAP", "/products"),
    "case_study": ("Norland Foods", "/products"),
}


def test_every_profile_field_is_proposed_with_its_page_date_and_confidence(make_org, site):
    org = _org(make_org, site.url)
    summary = _crawl(org)
    found = _by_field(org.id)

    assert summary["proposals"] > 0
    for field, (fragment, page) in FLOOR.items():
        assert field in found, "no proposal for %s" % field
        rows = [r for r in found[field] if fragment in json.dumps(r["value"])]
        assert rows, "%s never carried %r: %s" % (field, fragment, [r["value"] for r in found[field]])
        row = rows[0]
        assert row["source_url"].startswith(site.url), field
        if page is not None:
            assert _path(row["source_url"]) == page, "%s credited to %s" % (field, row["source_url"])
        assert re.match(r"\d{4}-\d{2}-\d{2}", row["fetched_at"]), field
        assert 0.0 <= row["confidence"] <= 1.0, field


def test_a_javascript_page_is_rendered_in_a_browser_and_static_pages_are_not(make_org, site):
    org = _org(make_org, site.url)
    summary = _crawl(org)
    found = _by_field(org.id)

    assert [_path(u) for u in summary["rendered"]] == ["/app"]
    harbor = [r for r in found["case_study"] if "Harbor Freight" in json.dumps(r["value"])]
    assert harbor, "the case study that only exists after JavaScript runs was not read"
    assert _path(harbor[0]["source_url"]) == "/app"
    assert not any("Hidden Corp" in json.dumps(r["value"]) for r in found["case_study"])


def test_robots_txt_exclusion_is_never_requested(make_org, site, other_site):
    org = _org(make_org, site.url)
    summary = _crawl(org)

    assert "/robots.txt" in site.hits
    assert not [h for h in site.hits if h.startswith("/private")]
    assert [_path(u) for u in summary["skipped_by_robots"]] == ["/private/plans"]
    assert other_site.hits == [], "a link to another site was followed"


def test_no_more_than_thirty_pages_are_read(make_org, other_site):
    links = " ".join('<a href="/p%d">page %d</a>' % (i, i) for i in range(45))
    pages = {"/": _html("<html><head><title>Big</title></head><body>%s</body></html>" % links),
             "/robots.txt": (404, "text/plain", "")}
    for i in range(45):
        pages["/p%d" % i] = _html("<html><body><p>page %d with warehouse robots text</p></body></html>" % i)
    big = _Site(pages)
    try:
        org = _org(make_org, big.url)
        summary = _crawl(org)
        fetched = [h for h in big.hits if h != "/robots.txt"]
        assert summary["pages_read"] == 30
        assert len(fetched) == 30
    finally:
        big.stop()


def test_the_time_limit_stops_the_crawl_and_says_so(make_org):
    links = " ".join('<a href="/p%d">page %d</a>' % (i, i) for i in range(10))
    pages = {"/": _html("<html><body>%s</body></html>" % links), "/robots.txt": (404, "text/plain", "")}
    for i in range(10):
        pages["/p%d" % i] = _html("<html><body><p>page %d</p></body></html>" % i)
    slow = _Site(pages, delay=0.4)
    try:
        org = _org(make_org, slow.url)
        summary = _crawl(org, time_limit_seconds=1.5)
        assert summary["stopped"] == "time_limit"
        assert 1 <= summary["pages_read"] < 10
    finally:
        slow.stop()


def test_nothing_is_read_without_consent(make_org, site):
    from app.modules.organization.website_crawl import CrawlRefused

    no_site = _org(make_org, None)
    with pytest.raises(CrawlRefused):
        _crawl(no_site)

    declined = make_org("declined")
    declined.settings = {"onboarding": {"website": {"url": site.url}}}
    with pytest.raises(CrawlRefused):
        _crawl(declined)
    assert site.hits == []


def test_a_private_address_is_refused_outside_development(app, make_org, site):
    from app.modules.organization.website_crawl import CrawlRefused

    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = False
    org = _org(make_org, site.url)
    with pytest.raises(CrawlRefused):
        _crawl(org)
    assert site.hits == []


def test_nothing_reaches_the_model_until_a_person_confirms(make_org, site):
    from app import db
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization

    org = _org(make_org, site.url)
    before_settings = json.dumps(org.settings, sort_keys=True)
    before_name = org.name
    counts = (ArchiMateElement.query.count(), ApplicationComponent.query.count())

    _crawl(org)

    db.session.expire_all()
    fresh = db.session.get(Organization, org.id)
    assert json.dumps(fresh.settings, sort_keys=True) == before_settings
    assert fresh.name == before_name
    assert (ArchiMateElement.query.count(), ApplicationComponent.query.count()) == counts


def test_proposals_belong_to_one_organisation(app, make_org, site, tenant_ctx):
    from app.modules.organization.website_crawl import pending_website_proposals

    first = _org(make_org, site.url)
    second = _org(make_org, site.url)
    _crawl(first)

    assert pending_website_proposals(first.id)
    assert pending_website_proposals(second.id) == []
    with tenant_ctx(second.id):
        assert pending_website_proposals(first.id) == []
    with tenant_ctx(first.id):
        assert pending_website_proposals(first.id)

    _crawl(second)
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    for mine, theirs in ((first, second), (second, first)):
        with tenant_ctx(mine.id):
            rows = AIChatCRUDApproval.query.filter_by(source_table="website_crawler").all()
            assert rows and {r.organization_id for r in rows} == {mine.id}
            assert {p["id"] for p in pending_website_proposals(mine.id)} == {r.id for r in rows}
            assert pending_website_proposals(theirs.id) == []


def test_reading_again_replaces_the_pending_proposals_instead_of_doubling_them(make_org, site):
    org = _org(make_org, site.url)
    _crawl(org)
    first_total = sum(len(v) for v in _by_field(org.id).values())
    _crawl(org)
    assert sum(len(v) for v in _by_field(org.id).values()) == first_total


def test_the_job_queue_reads_one_organisation_and_reports_counts(app, make_org, site, monkeypatch):
    from app.modules.organization import website_crawl
    from app.services.job_queue_service import get_job_queue_service

    org = _org(make_org, site.url)
    monkeypatch.setattr(website_crawl, "default_model", lambda: fake_model)
    job = website_crawl.queue_website_crawl(org.id)
    assert job.task == website_crawl.WEBSITE_TASK
    assert job.payload["organization_id"] == org.id

    result = get_job_queue_service()._execute_task(job.task, job.payload, job.id)
    assert result["pages_read"] >= 5
    assert result["proposals"] == len(website_crawl.pending_website_proposals(org.id))
    assert "html" not in json.dumps(result).lower()


def test_each_prompt_names_where_it_came_from_and_renders_without_leftovers():
    from app.modules.organization.website_crawl import prompts

    assert set(prompts.PROMPT_NAMES) == {
        "website_analysis", "website_inference", "page_rendering_strategy", "html_structure_discovery"}
    for name in prompts.PROMPT_NAMES:
        text = prompts.load(name)
        assert "operateiq-backend/src/prompts/%s.hbs" % name in text.splitlines()[0]
        assert prompts.SOURCE_COMMIT in text.splitlines()[0]
    rendered = prompts.render("website_analysis", {"url": "https://x.test/a", "title": "T",
                                                     "html_content": "<p>hi</p>"})
    assert "https://x.test/a" in rendered and "<p>hi</p>" in rendered
    assert "{{" not in rendered and "Load Time" not in rendered
    inferred = prompts.render("website_inference", {
        "company": {"name": "Acme", "website": "https://x.test"}, "technologies": [{"name": "React", "type": "frontend"}],
        "location": None})
    assert "**Name**: Acme" in inferred and "- React (frontend)" in inferred and "### Location" not in inferred


@pytest.mark.parametrize("html,expected", [
    ("<html><body><div id='root'></div><script src='/a.js'></script></body></html>", True),
    ("<html><body>%s</body></html>" % ("<p>plenty of real words here</p>" * 40), False),
], ids=["empty-app-shell", "full-static-page"])
def test_a_browser_is_used_only_when_the_static_page_misses_its_content(html, expected):
    from app.modules.organization.website_crawl.rendering import needs_browser

    assert needs_browser("https://x.test/", html, 200, llm=lambda p: pytest.fail("clear case asked the model")) is expected


@pytest.mark.parametrize("answer,expected", [
    ({"rendering_strategy": "chromium", "confidence": 0.8}, True),
    ({"rendering_strategy": "static", "confidence": 0.9}, False),
    ({"rendering_strategy": "static", "confidence": 0.3}, True),
], ids=["sure-it-needs-one", "sure-it-does-not", "unsure"])
def test_an_unclear_page_follows_the_rendering_strategy_answer(answer, expected):
    from app.modules.organization.website_crawl.rendering import needs_browser

    words = "<p>%s</p>" % ("some words " * 40)
    html = "<html><body>%s%s</body></html>" % (words, "<script src='/x.js'></script>" * 6)
    assert needs_browser("https://x.test/", html, 200, llm=lambda p: json.dumps(answer)) is expected
