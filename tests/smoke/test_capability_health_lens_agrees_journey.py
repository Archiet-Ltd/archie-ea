"""An enterprise architect asks "how healthy are our capabilities?" on two screens and
gets one answer.

The capability map's health lens must list the same capabilities as Capability Health
counts, and show every capability with the figure Capability Health gives it: the same
number of capabilities, the same number measured, the same split into healthy, warning
and critical, and the same figure on each capability Capability Health names as at risk.
A capability with nothing recorded is "Not assessed" on the lens and is left out of the
measured count on Capability Health. A second organisation, with a different portfolio,
must see only its own.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# (label, level, current maturity, target maturity, mapped application coverage or None)
PORTFOLIO = [
    ("Calibrate", 1, 1, 4, None),     # three levels short, no application: critical
    ("Dispatch", 1, 3, 3, 90),        # on target and covered: healthy
    ("Forecast", 1, 2, 3, 90),        # one level short and covered: healthy or warning
    ("Inspect", 1, 1, 3, None),       # two levels short, no application: critical
    ("Recertify", 1, None, None, None),  # nothing recorded: not assessed
    ("Archive", 2, None, None, None),    # a sub-level capability with no parent, nothing recorded
]
EMPTY_PORTFOLIO = [
    ("Idle", 1, None, None, None),
    ("Spare", 1, None, None, None),
]


def _seed(org_id, noun, portfolio):
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        for label, level, current, target, coverage in portfolio:
            capability = BusinessCapability(
                name="%s %s" % (noun, label), level=level, organization_id=org_id,
                current_maturity_level=current, target_maturity_level=target)
            db.session.add(capability)
            db.session.commit()
            if coverage:
                application = ApplicationComponent(name="%s %s App" % (noun, label), organization_id=org_id)
                db.session.add(application)
                db.session.commit()
                db.session.add(ApplicationCapabilityMapping(
                    application_component_id=application.id, business_capability_id=capability.id,
                    organization_id=org_id, coverage_percentage=coverage))
                db.session.commit()
        db.session.remove()


def _band(figure):
    return "healthy" if figure >= 80 else "warning" if figure >= 60 else "critical"


def _lens_figures(page, base, noun):
    """{capability name: figure or None} from the lens's list view."""
    page.goto(base + "/capability-map/?lens=health&view=list", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    rows = page.locator("tbody tr", has_text=noun)
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    table = rows.first.locator("xpath=ancestor::table")
    headers = [t.strip() for t in table.locator("thead th").all_text_contents()]
    column = headers.index("Health")
    figures = {}
    for index in range(rows.count()):
        row = rows.nth(index)
        name = re.search(r"%s \w+" % noun, row.inner_text()).group(0)
        cell = row.locator("td").nth(column).inner_text().strip()
        figures[name] = None if cell == "Not assessed" else int(cell.rstrip("%"))
        assert cell == "Not assessed" or re.fullmatch(r"\d+%", cell), (name, cell)
    return figures


def _health_page(page, base):
    page.goto(base + "/strategic/capability-health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Avg. Portfolio Health")).to_be_visible(timeout=PAGE_TIMEOUT)
    text = page.locator("body").inner_text()
    counts = {
        key: int(page.locator("#%s-count" % key).inner_text())
        for key in ("healthy", "warning", "critical")
    }
    at_risk = {}
    for row in page.locator("table.dense-table tbody tr").all():
        cells = [c.strip() for c in row.locator("td").all_inner_texts()]
        if len(cells) >= 5 and re.search(r"\w+ \w+", cells[0]):
            at_risk[cells[0].splitlines()[0].strip()] = cells[4]
    return text, counts, at_risk


def _walk(browser, base, email, noun, other_noun):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, base, email)
    lens = _lens_figures(page, base, noun)
    text, counts, at_risk = _health_page(page, base)
    assert other_noun not in page.locator("body").inner_text()
    context.close()
    return lens, text, counts, at_risk


def test_health_lens_and_capability_health_agree(live_server, browser):
    noun = "Lens%s" % uuid.uuid4().hex[:6]
    rival = "Rival%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun, PORTFOLIO)
    # A second organisation with a different portfolio: nothing recorded on any capability.
    second = create_fresh_org("enterprise_architect")
    _seed(second["org_id"], rival, EMPTY_PORTFOLIO)

    lens, text, counts, at_risk = _walk(
        browser, live_server, org["emails"]["enterprise_architect"], noun, rival)

    # The lens lists every capability the organisation holds, including the sub-level one.
    assert len(lens) == len(PORTFOLIO), lens
    measured = {name: figure for name, figure in lens.items() if figure is not None}
    assert len(measured) == 4, lens
    assert lens["%s Recertify" % noun] is None and lens["%s Archive" % noun] is None, lens

    # Capability Health counts the same capabilities and the same measured ones.
    assert "Across %d of %d capabilities" % (len(measured), len(PORTFOLIO)) in text, text[:800]
    # It splits them into the same bands.
    for band in ("healthy", "warning", "critical"):
        assert counts[band] == sum(1 for f in measured.values() if _band(f) == band), (band, counts, lens)
    assert sum(counts.values()) == len(measured)
    # And every capability it names as at risk carries the figure the lens shows for it.
    assert at_risk, "no at-risk capability on Capability Health"
    for name, figure in at_risk.items():
        assert name in lens, (name, lens)
        assert figure == "%d%%" % lens[name], (name, figure, lens[name])
    assert any(figure < 70 for figure in measured.values())
    assert len(at_risk) == sum(1 for figure in measured.values() if figure < 70), (at_risk, lens)

    # The second organisation sees its own two capabilities, none measured, on both screens.
    lens2, text2, counts2, at_risk2 = _walk(
        browser, live_server, second["emails"]["enterprise_architect"], rival, noun)
    assert len(lens2) == len(EMPTY_PORTFOLIO) and set(lens2.values()) == {None}, lens2
    assert counts2 == {"healthy": 0, "warning": 0, "critical": 0}
    assert "No capability has recorded maturity" in text2
    assert not at_risk2
