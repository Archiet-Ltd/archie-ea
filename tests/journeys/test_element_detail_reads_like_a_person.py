"""Journey: a CTO opens an element and learns what it is and what depends on it.

The goal, in a person's words: "I clicked Calibration Ledger. What is it, and what breaks if it goes down?"

The organisation is seeded with linked data (an application on a node, delivering a capability that serves a
goal, a project in a roadmap stage), then each of six element types is opened as a signed-in architect:

* no raw program text anywhere on the page (a stage must not print as ``[<Plateau ... order=2>]``);
* one name for the type and the layer, every time it appears;
* the first screen says how many things this element depends on and how many use it, and those figures are the
  Impact analysis page's own figures for the same element;
* the connections table lists exactly those elements, each row is a link, and every link opens a page that is
  headed with that element's name (not another element that happens to share a number);
* the page has one loud action, Impact analysis, and Delete is quiet;
* nothing that was on the old page is lost: description, owner, source, dates and flags are still there.
"""

import re
import uuid

import pytest
from bs4 import BeautifulSoup

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

RAW_TEXT = [
    r"<Plateau", r"order=\d", r"\[<", r"\{'", r"&lt;", r"\bNone\b", r"\bnan\b", r"\bundefined\b",
    r"\[object Object\]",
]


def _seed(db):
    """Return {key: (layer, type, ae_id, name)} for six element types, linked to each other."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.implementation_migration import Plateau, WorkPackage

    org_id = make_org(db, "ElementDetail")
    architect_id = make_user(db, org_id, "eldetail", "enterprise_architect", role_name="Administrator")
    sfx = uuid.uuid4().hex[:5]

    def element(name, kind, layer, **extra):
        row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id, **extra)
        db.session.add(row)
        db.session.flush()
        return row

    app_ = element("Calibration Ledger %s" % sfx, "ApplicationComponent", "application",
                   description="Keeps every instrument calibration record.")
    node = element("Quay Compute Pool %s" % sfx, "Node", "technology")
    cap = element("Incident Response %s" % sfx, "Capability", "strategy")
    goal = element("Achieve ISO 17025 accreditation %s" % sfx, "Goal", "motivation")
    wp = element("Field Tester Rollout %s" % sfx, "WorkPackage", "implementation")
    stage = element("Transition State 1 %s" % sfx, "Plateau", "implementation", togaf_plateau="Transition")
    base = element("Baseline Architecture %s" % sfx, "Plateau", "implementation", togaf_plateau="Baseline")

    # Dedicated rows share the small id space with the model graph, as they do in a real organisation.
    base_row = Plateau(name=base.name, organization_id=org_id, sequence_order=1, archimate_element_id=base.id)
    db.session.add(base_row)
    db.session.flush()
    stage_row = Plateau(name=stage.name, organization_id=org_id, sequence_order=2, archimate_element_id=stage.id,
                        baseline_plateau_id=base_row.id)
    db.session.add(stage_row)
    db.session.add(ApplicationComponent(name=app_.name, organization_id=org_id, archimate_element_id=app_.id))
    db.session.flush()
    db.session.add(WorkPackage(name=wp.name, organization_id=org_id, archimate_element_id=wp.id,
                               plateau_id=stage_row.id))

    def link(kind, source, target):
        db.session.add(ArchiMateRelationship(type=kind, source_id=source.id, target_id=target.id,
                                             organization_id=org_id))

    link("Serving", node, app_)          # the node runs the application
    link("Realization", app_, cap)       # the application delivers the capability
    link("Serving", cap, goal)           # the capability serves the goal
    link("Realization", wp, app_)        # the project delivers the application
    link("Association", wp, stage)       # the project sits in the stage
    link("Association", stage, base)     # the stage sits next to the baseline
    link("Serving", app_, goal)          # a direct line, so two hops reach the goal twice
    db.session.commit()
    ids = {
        "ApplicationComponent": ("application", "ApplicationComponent", app_.id, app_.name),
        "Node": ("technology", "Node", node.id, node.name),
        "Capability": ("strategy", "Capability", cap.id, cap.name),
        "Goal": ("motivation", "Goal", goal.id, goal.name),
        "WorkPackage": ("implementation", "WorkPackage", wp.id, wp.name),
        "Plateau": ("implementation", "Plateau", stage.id, stage.name),
    }
    return architect_id, org_id, ids


def _kpi(soup, testid):
    """The figure on a first-screen card, as a number."""
    card = soup.find(attrs={"data-testid": testid})
    assert card is not None, "no %s card on the first screen" % testid
    return int(card.find(class_="tabular-nums").get_text(strip=True))


def _text(soup):
    main = soup.find("main") or soup
    for tag in main(["script", "style", "template"]):
        tag.decompose()
    return re.sub(r"\s+", " ", main.get_text(" ")).strip()


def _impact_figures(client, ae_id):
    """Direct and total figures printed on the Impact analysis page for this element."""
    resp = client.get("/architecture/impact/%d" % ae_id)
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    figures = {}
    for label in ("Total Impacted", "Direct (1 hop)"):
        node = soup.find(string=re.compile(re.escape(label)))
        assert node, "the Impact analysis page no longer shows %r" % label
        figures[label] = int(node.find_parent("div").parent.find("div").get_text(strip=True))
    return figures


@pytest.fixture
def seeded(app, client):
    from app import db

    with app.app_context():
        architect_id, org_id, ids = _seed(db)
    login(client, architect_id)
    return ids


def _open(client, ids, key):
    layer, kind, ae_id, name = ids[key]
    resp = client.get("/architecture/%s/%s/%d?ae=1" % (layer, kind, ae_id))
    assert resp.status_code == 200, (key, resp.status_code)
    return BeautifulSoup(resp.get_data(as_text=True), "html.parser")


@pytest.mark.parametrize("key", ["ApplicationComponent", "Node", "Capability", "Goal", "WorkPackage", "Plateau"])
def test_every_type_reads_like_a_person(app, client, seeded, key):
    soup = _open(client, seeded, key)
    name = seeded[key][3]
    text = _text(BeautifulSoup(str(soup), "html.parser"))

    for pattern in RAW_TEXT:
        assert not re.search(pattern, text), "%s page shows raw text matching %r: %s" % (
            key, pattern, re.search(r".{0,40}%s.{0,40}" % pattern, text).group(0))

    h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    assert h1 == [name], (key, h1)

    # one plain name for the type and one for the layer wherever each appears
    crumb = soup.find("nav", attrs={"aria-label": "Breadcrumb"})
    assert crumb is not None, "no breadcrumb on the %s page" % key
    badge_layer = soup.find(attrs={"data-testid": "element-layer"}).get_text(" ", strip=True)
    badge_type = soup.find(attrs={"data-testid": "element-type"}).get_text(" ", strip=True)
    crumb_text = crumb.get_text(" ", strip=True)
    assert badge_layer in crumb_text and badge_type in crumb_text, (badge_layer, badge_type, crumb_text)
    assert "Implementation & Migration Layer" not in text

    # no tab bar: the answer is on the first screen
    assert not soup.find(attrs={"role": "tablist"})
    # the loud action is Impact analysis; Delete is quiet
    delete = soup.find(attrs={"data-testid": "delete-element"})
    assert delete is not None
    assert "bg-destructive " not in delete.get("class", [""])[0] + " " and "bg-destructive" not in " ".join(
        delete.get("class", []))
    assert soup.find("a", attrs={"data-testid": "impact-analysis-link"})


@pytest.mark.parametrize("key", ["ApplicationComponent", "Node", "Capability", "Goal", "WorkPackage", "Plateau"])
def test_first_screen_figures_equal_the_impact_page(app, client, seeded, key):
    soup = _open(client, seeded, key)
    ae_id = seeded[key][2]
    depends = _kpi(soup, "kpi-depends-on")
    used_by = _kpi(soup, "kpi-used-by")
    impact = _impact_figures(client, ae_id)
    assert depends + used_by == impact["Direct (1 hop)"], (key, depends, used_by, impact)

    # the card that answers the question states the same total
    answer = soup.find(attrs={"data-testid": "answer-impact"}).get_text(" ", strip=True)
    assert re.search(r"\b%d element" % impact["Total Impacted"], answer), (key, answer, impact)

    # the connections table lists exactly the elements counted above
    rows = soup.select("[data-testid='connection-row']")
    assert len(rows) >= depends + used_by
    counted = [r for r in rows if r.get("data-group") in ("depends-on", "used-by")]
    assert len(counted) == depends + used_by
    # the Impact analysis link is for this element's own model-graph id
    impact_link = soup.find("a", attrs={"data-testid": "impact-analysis-link"})
    assert impact_link["href"].rstrip("/").endswith("/%d" % ae_id) or ("element_id=%d" % ae_id) in impact_link["href"]


def test_every_connection_opens_the_element_it_names(app, client, seeded):
    checked = 0
    for key in seeded:
        soup = _open(client, seeded, key)
        for row in soup.select("[data-testid='connection-row']"):
            link = row.find("a", href=True)
            assert link, "a connection row on the %s page names an element but does not link to it" % key
            target = client.get(link["href"])
            assert target.status_code == 200, (key, link["href"], target.status_code)
            page = BeautifulSoup(target.get_data(as_text=True), "html.parser")
            heading = [h.get_text(" ", strip=True) for h in page.find_all("h1")]
            assert heading == [link.get_text(" ", strip=True)], (key, link["href"], heading)
            assert len(_text(page)) > 200, "the page behind %s is empty" % link["href"]
            checked += 1
    assert checked >= 10


def test_the_stage_page_says_its_order_not_its_object(app, client, seeded):
    soup = _open(client, seeded, "Plateau")
    text = _text(BeautifulSoup(str(soup), "html.parser"))
    assert "Stage 2" in text
    assert "Transition" in text            # the state, in words
    follows = soup.find(attrs={"data-testid": "stage-follows"})
    assert follows is not None and "Baseline Architecture" in follows.get_text()


def test_the_project_page_names_its_stage_with_a_link(app, client, seeded):
    soup = _open(client, seeded, "WorkPackage")
    stage = soup.find(attrs={"data-testid": "element-plateau"})
    assert stage is not None
    link = stage.find("a", href=True)
    assert link and "Transition State 1" in link.get_text()
    assert client.get(link["href"]).status_code == 200


def test_nothing_the_old_page_showed_is_lost(app, client, seeded):
    soup = _open(client, seeded, "ApplicationComponent")
    text = _text(BeautifulSoup(str(soup), "html.parser"))
    assert "Keeps every instrument calibration record." in text
    for label in ("Owner", "Created", "External identifiers"):
        assert label.lower() in text.lower(), label
    # the three yes/no flags are still there, each with a sentence that says what it means
    node = _open(client, seeded, "Node")
    flags = node.find(attrs={"data-testid": "element-flags"})
    assert flags is not None, "the flags disappeared"
    flag_text = flags.get_text(" ", strip=True)
    for label in ("Critical path member", "Is customized", "Is baseline"):
        assert label in flag_text, label
    assert "has to keep running" in flag_text
    assert soup.find(attrs={"data-testid": "element-owner"}) is not None
    assert soup.find(attrs={"data-testid": "element-provenance-asof"}) is not None
    # the way to add a link is still here
    assert soup.find(attrs={"data-testid": "add-relationship"}) is not None


def test_an_element_with_no_connections_says_why_and_offers_the_next_step(app, client, seeded):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User

    with app.app_context():
        user = User.query.filter(User.email.like("eldetail-%")).order_by(User.id.desc()).first()
        lone = ArchiMateElement(name="Lone Service %s" % uuid.uuid4().hex[:5], type="ApplicationService",
                                layer="application", organization_id=user.organization_id)
        db.session.add(lone)
        db.session.commit()
        lone_id = lone.id
    resp = client.get("/architecture/application/ApplicationService/%d" % lone_id)
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    text = _text(BeautifulSoup(str(soup), "html.parser"))
    assert _kpi(soup, "kpi-depends-on") == 0
    assert "Nothing is connected to this yet" in text
    assert soup.find(attrs={"data-testid": "add-relationship"}) is not None
    assert not re.search(r"(?<![\w-])—(?![\w-])", soup.find(attrs={"data-testid": "kpi-band"}).get_text())
