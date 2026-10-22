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

from app.services.site_registry import items_needing_own_page, raw_registry, site_registry

# group, label, line (menu item descriptions v2)
PRODUCT_ITEMS = [
    ("Ask anything", "AI chat", "Ask questions about your model in plain words and read the answer in chat."),
    ("Ask anything", "Twin map", "Pick a system and see the applications and services that depend on it."),
    ("Ask anything", "Connect your AI assistant", "Connect an AI assistant you already use to your organisation's model."),
    ("Map your business", "Business capability map", ""),
    ("Map your business", "Value streams", "Draw value streams with their stages and map each stage to capabilities."),
    ("Map your business", "Business model canvas", "Create a business model canvas and export it."),
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
    ("Approvals", "Architecture review board", "Submit a change for board review and track it from submission to decision."),
    ("Approvals", "AI approval queue", "Review changes an AI assistant proposes, then approve or reject each."),
]
PRODUCT_SOON = [
    ("Build your model from your tools", "Coming soon: start your model from the tools you already use."),
    ("Scenarios", "Coming soon: compare what-if plans side by side before you commit."),
    ("Every answer shows its sources", "Coming soon: see the records behind each figure in an answer."),
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
    ("Scenarios", "Coming soon: compare what-if plans side by side before you commit."),
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


def test_the_roles_menu_lists_46_roles_in_eight_families(app):
    with app.test_request_context("/"):
        reg = site_registry()
    assert reg["roles"]["count"] == 46
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
    roles by family: the footer links the families and the roles hub, which lists
    every role (checked in test_roles_hub_lists_every_role)."""
    nav = _links(home_html, "header")
    foot = _links(home_html, "footer")
    foot_labels = {text.removesuffix(" Coming soon") for _, text in foot}
    role_labels = set()
    with_registry = site_registry_labels_for_roles()
    role_labels |= with_registry
    top_level = {"Product", "Solutions", "Roles", "Compare", "Resources", "Pricing"}
    own = {"Sign in", "Talk to us", "Start free", "All features"}
    missing = []
    for href, text in nav:
        text = text.strip()
        if not text or text in top_level or text in own or text in role_labels:
            continue
        if text.startswith("+ "):  # "+ 3 more" opens the family on the roles hub
            continue
        if text not in foot_labels:
            missing.append(text)
    assert not missing, f"navbar items missing from the footer: {missing}"
    # and a footer label never differs from the navbar label for the same page
    nav_by_href = {}
    for href, text in nav:
        nav_by_href.setdefault(href, set()).add(text.strip())
    for href, text in foot:
        label = text.removesuffix(" Coming soon")
        if href in nav_by_href and label not in nav_by_href[href] and href not in ("/contact", "/pricing", "/features#coming-soon"):
            if not any(label.startswith(n) or n.startswith(label) for n in nav_by_href[href]):
                pytest.fail(f"{href}: footer says {label!r}, navbar says {sorted(nav_by_href[href])}")


def site_registry_labels_for_roles():
    raw = raw_registry()
    return {r["label"] for f in raw["roles"]["families"] for r in f["roles"]}


def test_roles_hub_lists_every_role_and_every_menu_role_link_lands_on_it(app):
    with app.test_client() as client:
        html = client.get("/roles").data.decode()
        raw = raw_registry()
        assert "All 46 roles" in html
        for family in raw["roles"]["families"]:
            assert f'id="{family["id"]}"' in html, f"roles hub has no place for {family['label']}"
            for role in family["roles"]:
                assert role["label"] in html, f"roles hub does not list {role['label']}"
        nav = _links(client.get("/").data.decode(), "header")
        anchors = [href for href, _ in nav if href.startswith("/roles#")]
        assert anchors, "the Roles menu links nowhere on the hub"
        for href in anchors:
            assert f'id="{href.split("#", 1)[1]}"' in html, f"{href} does not exist on the roles hub"


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
        pricing_main = re.sub(r"<footer.*?</footer>", "", _slice(client.get("/pricing").data.decode(), "main"), flags=re.DOTALL)
        for item in soon:
            assert item["label"] not in pricing_main


def test_the_pages_that_still_need_their_own_page_are_recorded(app):
    wanted = items_needing_own_page()
    labels = {w["label"] for w in wanted}
    assert {"Business capability map", "Value streams", "AI approval queue"} <= labels
    for w in wanted:
        assert w["own_page"].startswith("/")
        assert w["shown_on"].startswith("/")


def test_signed_in_visitor_sees_an_account_menu_not_sign_up(app, db_session, make_org, login_as):
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
    assert "Start free" not in header
    assert "Log out" in header


def test_no_page_in_the_registry_is_a_redirect_or_a_merged_page(app):
    from app.services.public_pages import MERGED_PAGES

    raw = raw_registry()
    hrefs = []
    for g in raw["product"]["groups"]:
        hrefs += [i["href"] for i in g["items"]]
    hrefs += [i["href"] for i in raw["solutions"]["items"]]
    for href in hrefs:
        assert href.split("#")[0] not in MERGED_PAGES, f"{href} is a 301"
