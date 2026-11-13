"""A person the one cost rule withholds from receives no money from any page or JSON route.

Written from one goal: an architect without spend authority opens every page and reads every feed the
product offers, in an organisation that holds cost data, and finds no amount of money anywhere. The sweep
requests every GET route that takes no argument, and every route whose arguments are the person's own
records, as that architect and as the chief technology officer (the control: the same sweep finds the money
when the rule allows it, so a silent detector cannot pass). A second organisation holds other money, so a
figure from it would also show.

A new page or feed that prints an amount to the withheld person fails here without anyone adding it to a list.
"""

from __future__ import annotations

import html
import json
import re
import signal
import sys
import time
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# the figures seeded in the organisation under test, and in the other organisation, as a page would print them
OWN_FIGURES = (482917, 731519, 268431, 905163, 374821, 619247)
OTHER_FIGURES = (558833, 884422)
MONEY_KEY = re.compile(r"cost|tco|budget|spend|saving|invest|estimate|price|licen[cs]e|capex|opex|revenue", re.I)
# a key that names money but holds a count, a flag, a date, a name or a reference, never an amount
NOT_AN_AMOUNT_KEY = re.compile(
    r"(count|number|num|total_items|rank|score|percent|pct|ratio|_id$|^id$|_ids$|year|month|days|date|"
    r"confidence|level|index|order|limit|offset|page|size|length|duration|rating|weight|priority|version|"
    r"has_|is_|can_|use_|show_|enabled|available|supported|tokens|requests|quota)", re.I)
AMOUNT_TEXT = re.compile(r"(£|&pound;|&#163;|\bGBP\b)\s?\d")
SECONDS_PER_ROUTE = 20
# the routes are requested in this many parts, so each part ends well inside a test's time limit
PARTS = 8
MINIMUM_ROUTES = 1000

# Routes that print the platform's own price list or a plan's price, which is the product's price to every
# visitor and no figure of the organisation's. Each is named with its reason.
PLATFORM_PRICE_PAGES = {
    "/billing/plans": "the plan price list the platform sells at",
    "/pricing": "the plan price list the platform sells at",
}

# Routes the sweep does not request, with the reason. Everything else with no argument is requested.
NOT_REQUESTED = {
    "/account/logout": "ends the session the sweep is using",
    "/logout": "ends the session the sweep is using",
}
NOT_REQUESTED_PARTS = ("logout", "/stream", "/events", "/sse", "/websocket", "/socket")


def _user(db_session, org, enterprise_role, label):
    from werkzeug.security import generate_password_hash

    from app.models.user import User

    user = User(email="sweep-%s-%s@example.com" % (label, uuid.uuid4().hex[:10]), first_name="Sweep", last_name=label,
                confirmed=True, organization_id=org.id, enterprise_role=enterprise_role,
                password_hash=generate_password_hash("x"))
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org, figures, label):
    """One of each record that carries money, in ``org``; returns the ids a route argument is filled from."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_case import BusinessCase
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.services.application_cost_accessor import set_annual_cost

    app_cost, case_tco, budget, spent, estimate, annual = (list(figures) * 3)[:6]
    application = ApplicationComponent(name="Sweep app %s" % label, organization_id=org.id)
    db_session.add(application)
    db_session.flush()
    set_annual_cost(application, app_cost)
    case = BusinessCase(title="Sweep case %s" % label, organization_id=org.id, tco_3yr=case_tco, capex=spent,
                        opex_annual=estimate, financial_benefit_annual=annual)
    strategic = StrategicInitiative(name="Sweep bet %s" % label, organization_id=org.id, status="in_progress",
                                    budget_allocated=budget, budget_spent=spent)
    initiative = EnterpriseInitiative(name="Sweep move %s" % label, organization_id=org.id, approved_budget=budget,
                                      spent_to_date=spent, status="active")
    capability = UnifiedCapability(name="Sweep capability %s" % label, organization_id=org.id, level=1,
                                   annual_cost=annual, investment_required=budget)
    package = UnifiedWorkPackage(name="Sweep package %s" % label, organization_id=org.id, estimated_cost=estimate,
                                 start_date=datetime(2026, 3, 1), end_date=datetime(2026, 9, 1), status="planned")
    solution = Solution(name="Sweep solution %s" % label, organization_id=org.id)
    element = ArchiMateElement(name="Sweep system %s" % label, type="ApplicationComponent", layer="application",
                               organization_id=org.id)
    db_session.add_all([case, strategic, initiative, capability, package, solution, element])
    db_session.flush()
    return {"application_id": application.id, "app_id": application.id, "business_case_id": case.id,
            "initiative_id": strategic.id, "strategic_initiative_id": strategic.id,
            "capability_id": capability.id, "work_package_id": package.id, "wp_id": package.id,
            "solution_id": solution.id, "element_id": element.id, "id": application.id}


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("sweep-a"), make_org("sweep-b")
    own = _seed(db_session, org_a, OWN_FIGURES, "own")
    _seed(db_session, org_b, OTHER_FIGURES, "other")
    return {"own": own, "architect": _user(db_session, org_a, "solution_architect", "architect"),
            "cto": _user(db_session, org_a, "cto", "cto"),
            "other_architect": _user(db_session, org_b, "solution_architect", "other")}


def _fill(rule, own):
    """The path for ``rule`` with every argument taken from the person's own records, or None when one cannot be."""
    values = {}
    for argument in rule.arguments:
        if argument not in own:
            return None
        values[argument] = own[argument]
    from werkzeug.routing import BuildError

    try:
        return rule.build(values, append_unknown=False)[1]
    except BuildError:
        return None


def routes_to_request(app, own):
    """(path, rule) for every GET route that can be requested, and the routes that cannot be filled."""
    requests, unfilled, skipped = [], [], []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        if rule.rule in NOT_REQUESTED or any(part in rule.rule for part in NOT_REQUESTED_PARTS):
            skipped.append(rule.rule)
            continue
        path = _fill(rule, own) if rule.arguments else rule.rule
        if path is None:
            unfilled.append(rule.rule)
        else:
            requests.append((path, rule))
    return requests, unfilled, skipped


def _money_in_json(node, found, trail=""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = "%s.%s" % (trail, key)
            if (isinstance(value, (int, float)) and not isinstance(value, bool) and MONEY_KEY.search(str(key))
                    and not NOT_AN_AMOUNT_KEY.search(str(key))):
                found.append("%s = %s" % (here, value))
            else:
                _money_in_json(value, found, here)
    elif isinstance(node, list):
        for item in node[:200]:
            _money_in_json(item, found, trail + "[]")


def _figure_in(text, figures):
    for figure in figures:
        for shown in (str(figure), "{:,}".format(figure)):
            if re.search(r"(?<![\d,.])%s(?![\d])" % re.escape(shown), text):
                return shown
    return None


class _Slow(Exception):
    pass


def _alarm(signum, frame):
    raise _Slow()


def find_money(reply, own_figures=OWN_FIGURES, other_figures=OTHER_FIGURES):
    """What in one reply is money: a seeded figure, a pound amount, or a money key holding a number."""
    body = reply.get_data(as_text=True) if reply.mimetype in ("text/html", "application/json", "text/plain", "text/csv") else ""
    found = []
    shown = _figure_in(body, own_figures + other_figures)
    if shown:
        found.append("the figure %s" % shown)
    amount = AMOUNT_TEXT.search(html.unescape(body) if "&" in body else body)
    if amount:
        found.append("an amount near %r" % body[max(amount.start() - 20, 0):amount.end() + 20])
    if reply.mimetype == "application/json":
        try:
            _money_in_json(json.loads(body), found)
        except ValueError:
            pass
    return found[:4]


def sweep(app, client, login_as, user, own, part=None):
    """Request the routes as ``user`` (every PARTS-th route from ``part``, or all when no part is named).

    Returns (leaks, requested, unfilled, skipped, failed).
    """
    requests, unfilled, skipped = routes_to_request(app, own)
    if part is not None:
        requests = requests[part::PARTS]
    leaks, failed = {}, []
    previous = signal.signal(signal.SIGALRM, _alarm)
    try:
        started = time.time()
        for number, (path, rule) in enumerate(requests, 1):
            if number % 50 == 0:
                print("  %d of %d routes in %ds" % (number, len(requests), time.time() - started), file=sys.stderr, flush=True)
            login_as(client, user)
            signal.alarm(SECONDS_PER_ROUTE)
            try:
                reply = client.get(path, follow_redirects=True)
            except _Slow:
                failed.append("%s (no answer in %ss)" % (path, SECONDS_PER_ROUTE))
                continue
            except Exception as exc:  # a route that raises is not a route that leaks
                failed.append("%s (%s)" % (path, type(exc).__name__))
                from app import db

                db.session.rollback()
                continue
            finally:
                signal.alarm(0)
            if rule.rule in PLATFORM_PRICE_PAGES:
                continue
            money = find_money(reply)
            if money:
                leaks[path] = money
    finally:
        signal.signal(signal.SIGALRM, previous)
    return leaks, len(requests), unfilled, skipped, failed


def test_the_routes_are_counted_and_the_ones_not_requested_are_named(app, world, capsys):
    requests, unfilled, skipped = routes_to_request(app, world["own"])
    with capsys.disabled():
        print("\nsweep routes: %d requested, %d not requested because an argument is not one of the person's own "
              "records, %d skipped" % (len(requests), len(unfilled), len(skipped)))
        print("skipped: %s" % ", ".join(skipped))
        print("not requested:\n  " + "\n  ".join(unfilled))
    assert len(requests) > MINIMUM_ROUTES
    assert sum(len(requests[part::PARTS]) for part in range(PARTS)) == len(requests)


@pytest.mark.parametrize("part", range(PARTS))
def test_the_withheld_architect_receives_no_money_from_any_route(app, client, login_as, world, part, capsys):
    leaks, requested, _unfilled, _skipped, failed = sweep(app, client, login_as, world["architect"], world["own"], part)
    with capsys.disabled():
        print("\npart %d of %d as the withheld architect: %d routes requested, %d raised or did not answer"
              % (part + 1, PARTS, requested, len(failed)))
    assert requested > 100
    assert not leaks, "%d routes handed money to a person the cost rule withholds from:\n%s" % (
        len(leaks), "\n".join("  %s -> %s" % (path, "; ".join(found)) for path, found in sorted(leaks.items())))


def test_the_same_check_finds_the_money_for_the_chief_technology_officer(app, client, login_as, world):
    login_as(client, world["cto"])
    for path in ("/business-case/api/%d" % world["own"]["business_case_id"],
                 "/enterprise/api/work-packages/gantt", "/capability-map/api/applications",
                 "/capability-map/api/capabilities/hierarchy"):
        reply = client.get(path)
        assert reply.status_code == 200, path
        assert find_money(reply), "the check did not find the money the allowed person receives at %s" % path


def test_the_other_organisations_architect_is_withheld_from_too(app, client, login_as, world):
    login_as(client, world["other_architect"])
    for path in ("/enterprise/api/work-packages/gantt", "/capability-map/api/applications",
                 "/capability-map/api/capabilities/hierarchy"):
        reply = client.get(path)
        assert reply.status_code == 200, path
        assert not find_money(reply), "%s handed money to the other organisation's architect" % path
