"""The public site registry: one list behind the navbar, its menus and the footer.

Every label and one-line description below is the one that passed product and
design review (menu item descriptions v2, the Product menu decision v2, the
Solutions menu v2). The registry must carry them word for word. The rendered
navbar and footer are read back from the real pages, so a template that held its
own link list, or a footer label that drifted from its navbar label, fails here.
"""
import re
from html.parser import HTMLParser

import pytest

from app.services.site_registry import items_needing_own_page, raw_registry, site_registry, wave_released

# group, label, line (menu item descriptions v2)
PRODUCT_ITEMS = [
    ("Ask anything", "AI chat", "Ask questions about your model in plain words and read the answer in chat."),
    ("Ask anything", "Twin map", "Pick a system and see the applications and services that depend on it."),
    ("Ask anything", "Every answer shows its sources", "Open the records behind each answer to check it before you rely on it."),
    ("Ask anything", "Connect your AI assistant", "Connect an AI assistant you already use to your organisation's model."),
    ("Map your business", "Business capability map", ""),
    ("Map your business", "Value streams", "Draw value streams with their stages and map each stage to capabilities."),
    ("Map your business", "Business model canvas", "Create a business model canvas and export it."),
    ("Know what you run", "Build your model from your tools", "Connect GitHub or Jira at sign-up and start your model from them."),
    ("Know what you run", "Application inventory", "Keep a list of your applications and record the details of each."),
    ("Know what you run", "Architecture model", "Import an ArchiMate file and browse the elements and links it brings in."),
    ("Know what you run", "Diagrams", "Draw architecture diagrams from the elements in your model."),
    ("Know what you run", "Org chart", "See your organisation's units and roles as a chart."),
    ("Know what you run", "Risk register", "Record risks to your systems and review them together."),
    ("What it costs", "Rationalisation scoring", "Score each application and rank the results."),
    ("What it costs", "Contracts and licences", "Record vendor contracts and software licences in registers."),
    ("What it costs", "Business case", "Work out an investment's net present value from its costs and benefits."),
    ("What you are changing", "Roadmaps", "Record programmes and place them on a roadmap."),
    ("What you are changing", "Portfolio", "Register initiatives and see them together in a portfolio."),
    ("What you are changing", "Scenarios", "Try a change on a copy of your model and compare the scenarios you save."),
    ("Approvals", "Architecture review board", "Submit a change for board review and track it from submission to decision."),
    ("Approvals", "AI approval queue", "Review changes an AI assistant proposes, then approve or reject each."),
]
PRODUCT_SOON = [
    ("Software and tool discovery", "Coming soon: discover the software and tools you actually pay for and use."),
]
SOLUTIONS = [
    ("Application rationalisation", "Find overlapping applications and decide which to keep, consolidate or retire."),
    ("IT investment business cases", "Build the case for each IT investment and keep contracts and licences on record."),
    ("Technology risk", "See what a failure would reach and record the risks tied to it."),
    ("Transformation planning", "Plan the path from today's estate to the target and track it as a portfolio."),
    ("Enterprise architecture modelling", "Model applications, architecture and organisation, and draw from them."),
    ("Architecture governance", "Review architecture changes, record the decision and its conditions."),
]
SOLUTIONS_SOON = [
    ("Software and tool discovery", "Coming soon: discover the software and tools you actually pay for and use."),
]


class _Links(HTMLParser):
    """Anchors inside one element (header or footer), with their visible text."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._label = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attributes = dict(attrs)
            self._href = attributes.get("href")
            self._label = attributes.get("data-label")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            self.links.append((self._href, self._label or text))
            self._href = None


def _slice(html, tag):
    match = re.search(rf"<{tag}\b.*?</{tag}>", html, re.DOTALL)
    assert match, f"no <{tag}> rendered"
    return match.group(0)


def _links(html, tag):
    parser = _Links()
    parser.feed(_slice(html, tag))
    return parser.links


@pytest.fixture
def home_html(app):
    with app.test_client() as client:
        rv = client.get("/")
        assert rv.status_code == 200
        return rv.data.decode()


def test_product_menu_carries_the_reviewed_labels_and_lines_word_for_word(app):
    with app.test_request_context("/"):
        reg = site_registry()
    got = [(g["label"], i["label"], i["blurb"]) for g in reg["product"]["groups"] for i in g["items"]]
    assert got == PRODUCT_ITEMS
    assert [g["label"] for g in reg["product"]["groups"]] == [
        "Ask anything", "Map your business", "Know what you run", "What it costs", "What you are changing", "Approvals",
    ]
    soon = [(i["label"], i["blurb"]) for i in reg["product"]["coming_soon"]["items"]]
    assert soon == PRODUCT_SOON
    assert reg["product"]["coming_soon"]["all_link"]["label"] == "Everything coming soon"


def test_solutions_menu_carries_the_reviewed_labels_and_lines(app):
    with app.test_request_context("/"):
        reg = site_registry()
    assert [(i["label"], i["blurb"]) for i in reg["solutions"]["items"]] == SOLUTIONS
    assert [(i["label"], i["blurb"]) for i in reg["solutions"]["coming_soon"]["items"]] == SOLUTIONS_SOON


def test_top_level_menu_order(app):
    with app.test_request_context("/"):
        reg = site_registry()
    assert [e["label"] for e in reg["nav"]] == ["Product", "Solutions", "Roles", "Compare", "Pricing", "Resources"]
    assert [a["label"] for a in reg["actions"]] == ["Sign in", "Talk to us", "Start free"]


def test_every_blurb_is_one_short_line_and_every_icon_is_from_the_closed_list():
    import json
    from pathlib import Path

    patterns = json.loads((Path(__file__).resolve().parents[1] / "design" / "patterns.json").read_text(encoding="utf-8"))
    allowed = set(patterns["public_icons"])
    raw = raw_registry()
    items = [i for g in raw["product"]["groups"] for i in g["items"]]
    items += raw["product"]["coming_soon"]["items"] + raw["solutions"]["items"] + raw["solutions"]["coming_soon"]["items"]
    for item in items:
        assert len(item.get("blurb", "")) <= 80, f"{item['label']}: blurb over 80 characters"
        assert item["icon"] in allowed, f"{item['label']}: icon {item['icon']!r} is not in public_icons"


def test_the_roles_menu_lists_47_roles_in_eight_families(app):
    with app.test_request_context("/"):
        reg = site_registry()
    assert reg["roles"]["count"] == 47
    assert [f["label"] for f in reg["roles"]["families"]] == [
        "Executives", "Startups and scale-ups", "Architecture", "Portfolio, programmes and product",
        "IT operations", "Risk, compliance and data", "Finance, sourcing and people", "Consultancies",
    ]


def test_navbar_renders_every_menu_item_from_the_registry(home_html):
    nav = _links(home_html, "header")
    labels = {text for _, text in nav}
    for _, label, _ in PRODUCT_ITEMS:
        assert any(label == t or t.startswith(label) for t in labels), f"navbar is missing {label!r}"
    for label, _ in PRODUCT_SOON + SOLUTIONS + SOLUTIONS_SOON:
        assert any(t.startswith(label) for t in labels), f"navbar is missing {label!r}"


def test_navbar_and_footer_agree_on_every_label(home_html):
    """Every navbar item is in the footer, under the same label. The Roles menu lists
    role pages; the footer links the role families and the roles hub, which lists
    every role page (checked in test_roles_hub_lists_every_role_page)."""
    nav = _links(home_html, "header")
    foot = _links(home_html, "footer")
    foot_labels = {text for _, text in foot}
    role_labels = {r.get("card_title", r["label"]) for f in raw_registry()["roles"]["families"] for r in f["roles"]}
    top_level = {"Product", "Solutions", "Roles", "Compare", "Resources", "Pricing"}
    own = {"Sign in", "Talk to us", "Start free", "All features"}
    missing = []
    for href, text in nav:
        text = text.strip()
        if not text or text in top_level or text in own or text in role_labels:
            continue
        if text not in foot_labels:
            missing.append(text)
    assert not missing, f"navbar items missing from the footer: {missing}"
    nav_by_href = {}
    for href, text in nav:
        nav_by_href.setdefault(href, set()).add(text.strip())
    for href, text in foot:
        if href in nav_by_href and text not in nav_by_href[href] and href not in ("/contact", "/pricing", "/features#coming-soon"):
            if not any(text.startswith(n) or n.startswith(text) for n in nav_by_href[href]):
                pytest.fail(f"{href}: footer says {text!r}, navbar says {sorted(nav_by_href[href])}")


def test_roles_hub_lists_every_role_page_and_every_menu_role_link_lands_on_it(app):
    """Cards only for roles that have a page, startup roles first; the menu lists
    only those pages; no plain-text role name that goes nowhere."""
    with app.test_client() as client:
        html = client.get("/roles").data.decode()
        raw = raw_registry()
        cards = re.findall(r'<a href="([^"]+)"[^>]*data-role-card', html)
        paged = {r["href"] for f in raw["roles"]["families"] for r in f["roles"] if r.get("href")}
        assert set(cards) == paged
        assert cards[0] == "/roles/startup-founder"
        assert "<h1" in html and "Entelim for your role" in html
        without_page = [r["label"] for f in raw["roles"]["families"] for r in f["roles"] if not r.get("href")]
        for label in without_page:
            assert label not in html, f"{label} has no page and must not be listed"
        nav = _links(client.get("/").data.decode(), "header")
        role_links = [href for href, _ in nav if href.startswith("/roles/")]
        assert set(role_links) == paged
        families = [href for href, _ in nav if href.startswith("/roles#")]
        assert families
        for href in families:
            assert f'id="{href.split("#", 1)[1]}"' in html, f"{href} does not exist on the roles hub"
        for href in paged:
            assert client.get(href).status_code == 200


def test_the_footer_is_one_set_of_columns_with_coming_soon_in_one_group(home_html):
    foot = _slice(home_html, "footer")
    columns = re.findall(r'data-footer-column="([^"]+)"', foot)
    assert columns == ["product", "solutions", "roles", "compare", "resources", "company", "legal", "coming-soon"]
    assert "Ask anything" not in foot
    soon = foot[foot.index('data-footer-column="coming-soon"'):]
    # a released Scenarios is listed with the product, not under coming soon
    assert "Scenarios" not in soon and "Software and tool discovery" in soon
    assert "Scenarios" in foot[:foot.index('data-footer-column="coming-soon"')]
    for live in ("Build your model from your tools", "Every answer shows its sources"):
        assert live not in soon
        assert live in foot


def test_every_navbar_and_footer_link_answers_200_with_a_heading(app, home_html):
    """No redirect, no 404, no sign-in page; the anchor exists when the link has one."""
    seen = {}
    for tag in ("header", "footer"):
        for href, text in _links(home_html, tag):
            # Sign in and Start free lead to the account forms by design.
            if href.startswith(("mailto:", "#", "/account/")) or not href:
                continue
            seen.setdefault(href, text)
    assert len(seen) > 60
    with app.test_client() as client:
        for href, text in seen.items():
            path, _, fragment = href.partition("#")
            rv = client.get(path)
            assert rv.status_code == 200, f"{text!r} -> {href} answers {rv.status_code}"
            body = rv.data.decode()
            assert "<h1" in body, f"{text!r} -> {href} has no h1"
            assert "/account/login" not in rv.request.path
            if fragment:
                assert f'id="{fragment}"' in body, f"{text!r} -> {href}: no element with id {fragment!r}"


def test_coming_soon_items_point_at_a_waiting_list_and_are_never_in_a_plan(app):
    from app.services.billing_plans import PLANS

    with app.test_request_context("/"):
        reg = site_registry()
    soon = reg["product"]["coming_soon"]["items"] + reg["solutions"]["coming_soon"]["items"]
    plan_text = " ".join(f"{p.name} {p.summary}" for p in PLANS).lower()
    with app.test_client() as client:
        for item in soon:
            html = client.get(item["href"]).data.decode()
            screens = [e["signed_in"]["href"] for e in soon if e["href"] == item["href"] and e.get("signed_in")]
            if screens and wave_released({"href": screens[0]}):
                # its screen is released: the page says available and no longer offers a waiting list
                assert "Available now" in html and "Not built yet" not in html, item["label"]
                continue
            assert 'data-testid="feature-interest-form"' in html, f"{item['label']} has no waiting list form"
            # the page's own content: not the site footer, which sits inside <main> and carries the logo
            main = re.sub(r"<footer\b.*?</footer>", "", _slice(html, "main"), flags=re.DOTALL)
            for forbidden in ("<img", "<picture", "<figure", "<video"):
                assert forbidden not in main, f"{item['label']}: {forbidden} on a coming soon page"
            for link in ('href="/pricing"', "account/register"):
                assert link not in main, f"{item['label']}: {link} on a coming soon page"
            # structured data: a plain page, never the software or an offer for this capability
            import json

            graph = json.loads(re.search(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', main, re.DOTALL).group(1))
            nodes = graph.get("@graph", [graph])
            assert nodes[0]["@type"] == "WebPage", f"{item['label']}: typed {nodes[0]['@type']}"
            assert not any(n.get("@type") == "SoftwareApplication" and n.get("name") == item["label"] for n in nodes)
            assert item["label"].lower() not in plan_text
        # the pricing page's own content never lists them
        # The whole pricing page, including any dated band, never lists them.
        pricing_main = re.sub(r"<footer.*?</footer>", "", _slice(client.get("/pricing").data.decode(), "main"), flags=re.DOTALL)
        assert "pricing-grow" not in pricing_main
        for item in soon:
            assert item["label"] not in pricing_main


def test_the_pages_that_still_need_their_own_page_are_recorded(app):
    wanted = items_needing_own_page()
    labels = {w["label"] for w in wanted}
    assert {"Business capability map", "Value streams", "AI approval queue"} <= labels
    for w in wanted:
        assert w["own_page"].startswith("/")
        assert w["shown_on"].startswith("/")


def test_signed_in_visitor_sees_open_entelim_and_the_account_menu_goes_home(app, db_session, make_org, login_as):
    from app.models.user import User

    org = make_org("nav")
    user = User(email="nav-user@example.com", first_name="Nia", last_name="Navigator",
                organization_id=org.id, confirmed=True)
    db_session.add(user)
    db_session.flush()
    with app.test_client() as client:
        login_as(client, user)
        html = client.get("/pricing").data.decode()
    header = _slice(html, "header")
    assert 'aria-label="Account menu"' in header
    assert "Start free" not in header and "Sign in" not in header
    assert "Open Entelim" in header
    links = dict((text, href) for href, text in _links(html, "header"))
    assert links["Open Entelim"] == "/dashboard/overview"
    assert links["Dashboard"] == links["Open Entelim"]
    assert "/admin" not in links["Dashboard"]
    assert "Log out" in header


def test_the_features_page_opens_scenarios_on_the_working_screen_for_a_signed_in_visitor(
        app, db_session, make_org, login_as):
    """The footer and menus send a signed-in person to /simulations; the features page agrees with them."""
    from app.models.user import User

    org = make_org("feat")
    user = User(email="feat-user@example.com", first_name="Fay", last_name="Features",
                organization_id=org.id, confirmed=True)
    db_session.add(user)
    db_session.flush()

    def scenario_links(html):
        return {href for href, text in _links(html, "main") if text.strip() == "Scenarios"}

    with app.test_client() as client:
        assert scenario_links(client.get("/features").data.decode()) == {"/modules/scenario-modelling"}
        login_as(client, user)
        html = client.get("/features").data.decode()
        assert scenario_links(html) == {"/simulations"}
        assert "/modules/scenario-modelling" not in _slice(html, "main")
        available, _, waiting = html.partition('<h2 id="coming-soon">')
        assert 'href="/simulations">Scenarios</a>: compare' in available
        assert "Scenarios" not in waiting.split("</ul>")[0]
        assert "planned" not in available.split('id="simulation-and-scenario-planning"')[1].split("<h3")[0]
        landing = client.get("/simulations")
        assert landing.status_code == 200 and "Run simulation" in landing.data.decode()


def test_no_page_in_the_registry_is_a_redirect_or_a_merged_page(app):
    from app.services.public_pages import MERGED_PAGES

    raw = raw_registry()
    hrefs = []
    for g in raw["product"]["groups"]:
        hrefs += [i["href"] for i in g["items"]]
    hrefs += [i["href"] for i in raw["solutions"]["items"]]
    for href in hrefs:
        assert href.split("#")[0] not in MERGED_PAGES, f"{href} is a 301"


def test_a_signed_in_promotion_sits_only_where_the_registry_reads_it():
    """The signed-in key promotes an entry of the product menu's coming-soon list; anywhere else it is never read."""
    import pathlib
    import yaml

    raw = yaml.safe_load((pathlib.Path(__file__).resolve().parents[1] / "content/site/registry.yml").read_text())

    def holders(node, path=""):
        if isinstance(node, dict):
            if "signed_in" in node:
                yield path
            for key, value in node.items():
                yield from holders(value, path + "/" + str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from holders(value, path + "/" + str(index))

    assert all(p.startswith("/product/coming_soon/items/") for p in holders(raw)), list(holders(raw))
    assert list(holders(raw)), "the product entry that promotes Scenarios has lost its signed_in key"


@pytest.fixture
def wave(app, monkeypatch):
    def set_wave(number):
        monkeypatch.setitem(app.config, "RELEASE_WAVE", str(number))

    return set_wave


def _surfaces(client):
    """What a person reads about Scenarios on the home page (outside its footer) and the features page."""
    home_page = client.get("/")
    # a signed-in person is sent past the home page to the dashboard
    home = re.sub(r"<footer\b.*?</footer>", "", home_page.data.decode(), flags=re.DOTALL) if home_page.status_code == 200 else "<main></main>"
    features = client.get("/features").data.decode()
    return {
        "home": [text.strip() for href, text in _links(home, "main") if "Scenarios" in text],
        "features_waiting": features.partition('<h2 id="coming-soon">')[2].split("</ul>")[0] if '<h2 id="coming-soon">' in features else "",
        "features": features,
    }


@pytest.mark.parametrize("signed_in", [False, True], ids=["visitor", "signed-in"])
def test_a_released_scenarios_is_available_on_every_surface_and_offers_no_waiting_list(
        app, wave, db_session, make_org, login_as, signed_in):
    """Once the Scenarios screen is released, the menus, footer, home page, features page and module page agree."""
    from app.models.user import User

    wave(3)
    with app.test_client() as client:
        if signed_in:
            org = make_org("rel")
            user = User(email="rel-user@example.com", first_name="Rae", last_name="Released",
                        organization_id=org.id, confirmed=True)
            db_session.add(user)
            db_session.flush()
            login_as(client, user)
        target = "/simulations" if signed_in else "/modules/scenario-modelling"
        with app.test_request_context("/"):
            if signed_in:
                from flask_login import login_user

                login_user(user)
            reg = site_registry()
        menu = [i["href"] for g in reg["product"]["groups"] for i in g["items"] if i["label"] == "Scenarios"]
        assert menu == [target]
        soon = reg["product"]["coming_soon"]["items"] + reg["solutions"]["coming_soon"]["items"]
        assert "Scenarios" not in [i["label"] for i in soon]
        assert "Software and tool discovery" in [i["label"] for i in soon]
        footer_columns = {c["id"]: c["items"] for r in reg["footer"]["rows"] for c in r["columns"]}
        assert target in [i["href"] for i in footer_columns["product"] if i["label"] == "Scenarios"]
        assert "Scenarios" not in [i["label"] for i in footer_columns["coming-soon"]]

        seen = _surfaces(client)
        assert seen["home"] == ([] if signed_in else ["Scenarios"]), seen["home"]
        assert "Scenarios" not in seen["features_waiting"]
        assert 'id="available-now"' in seen["features"]
        section = seen["features"].split('id="simulation-and-scenario-planning"')[1].split("<h3")[0]
        assert "Early access" not in section and "waiting list" not in section
        assert ("Sign in to open it" in _slice(seen["features"], "main")) is (not signed_in)

        module = client.get("/modules/scenario-modelling")
        if signed_in:
            # a signed-in person is sent from the module page to the working screen
            assert module.status_code == 302 and module.headers["Location"].endswith("/simulations")
            return
        html = module.data.decode()
        assert "Available now" in html and "Not built yet" not in html
        assert html.count('data-testid="feature-interest-form"') == 0, "a released page offers a waiting list"


def test_before_its_screen_is_released_scenarios_is_coming_soon_everywhere_and_the_page_has_the_waiting_list(app, wave):
    wave(2)
    with app.test_request_context("/"):
        reg = site_registry()
    assert "Scenarios" in [i["label"] for i in reg["product"]["coming_soon"]["items"]]
    with app.test_client() as client:
        seen = _surfaces(client)
        assert seen["home"] == ["Scenarios (coming soon)"], seen["home"]
        assert "Scenarios" in seen["features_waiting"]
        assert client.get("/modules/scenario-modelling").data.decode().count('data-testid="feature-interest-form"') == 1
