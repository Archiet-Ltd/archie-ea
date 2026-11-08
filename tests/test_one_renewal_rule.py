"""One rule says when a contract renews, how many days away that is and how urgent it is.

The renewals page, the Contracts register, a contract's own page, the renewal brief and the contract analysis
all read the rule in the vendor register. A contract that renews in 14 days and ends in 197 reads 14 days on
each of them, and no module outside the vendor register works out renewal days or bands for itself.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]

# arithmetic on a contract's own dates, and a hand-written threshold ladder beside a renewal word
DATE_ARITHMETIC = re.compile(r"\b(?:contract|c)\.(?:renewal_date|end_date)\s*-(?!\s*timedelta)")
BAND_LADDER = re.compile(r"(?:days?\w*|remaining|expiry)\s*(?:<=?|>=?)\s*(?:30|90|180)\b")
RENEWAL_WORDS = re.compile(r"renew|contract|expir|urgency", re.I)
OWNER = Path("app/services/vendor_register.py")


def _source_files():
    for path in sorted((ROOT / "app").rglob("*")):
        if path.suffix in (".py", ".html") and "static" not in path.parts and path.relative_to(ROOT) != OWNER:
            yield path


def test_no_module_outside_the_vendor_register_works_out_renewal_days_or_bands():
    found = []
    for path in _source_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            code = line.split("#", 1)[0] if path.suffix == ".py" else line
            if DATE_ARITHMETIC.search(code) or (BAND_LADDER.search(code) and RENEWAL_WORDS.search(code)
                                                and "framework" not in str(path)):
                found.append("%s:%d: %s" % (path.relative_to(ROOT), number, line.strip()))
    assert not found, "work out renewal days or bands with app.services.vendor_register:\n" + "\n".join(found)


def test_the_private_copies_of_the_rule_are_gone():
    services = (ROOT / "app/modules/procurement/services.py").read_text()
    assert "def get_days_until_renewal" not in services and "def get_renewal_urgency" not in services
    routes = (ROOT / "app/modules/procurement/routes.py").read_text()
    assert 'urgency = "' not in routes and "elif days <=" not in routes


def _contract(db_session, org, name, renews_in, ends_in, status="active"):
    from app.models.application_portfolio import VendorContract

    contract = VendorContract(
        contract_name=name, organization_id=org.id, annual_cost=1000.0, status=status,
        start_date=date.today() - timedelta(days=90),
        renewal_date=date.today() + timedelta(days=renews_in) if renews_in is not None else None,
        end_date=date.today() + timedelta(days=ends_in))
    db_session.add(contract)
    db_session.flush()
    return contract


def test_the_register_the_renewals_page_the_contract_and_the_brief_say_the_same_days_and_band(
        app, db_session, make_org):
    from app.modules.procurement.procurement_ai_service import _build_renewal_brief_context

    mine, theirs = make_org("orr-a"), make_org("orr-b")
    tag = uuid.uuid4().hex[:6]
    soon = _contract(db_session, mine, "Soon %s" % tag, 14, 197)
    renewed = _contract(db_session, mine, "Renewed %s" % tag, -20, 160)
    _contract(db_session, theirs, "Theirs %s" % tag, 3, 300)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()

    register = _answer(client.get("/procurement/contracts").get_data(as_text=True))
    renewals = _answer(client.get("/procurement/renewals?days=365").get_data(as_text=True))
    for name in ("Soon %s" % tag, "Renewed %s" % tag):
        assert name in register and name in renewals
    assert "Theirs" not in register and "Theirs" not in renewals
    # 14 days is the renewal date, not the 197 days to the end date
    assert re.search(r"Soon %s.*?In 14 days" % tag, register), register
    assert re.search(r"Soon %s.*?In 14 days" % tag, renewals), renewals
    assert "In 197 days" not in register
    # a renewal date already past on a running contract is not shown: its next date is its end date
    assert re.search(r"Renewed %s.*?In 160 days" % tag, register), register

    detail = _answer(client.get("/procurement/contracts/%d" % soon.id).get_data(as_text=True))
    assert "14 days until renewal" in detail
    assert "Renewal Date " + (date.today() + timedelta(days=14)).strftime("%d %b %Y") in detail
    past = _answer(client.get("/procurement/contracts/%d" % renewed.id).get_data(as_text=True))
    assert "Renewal Date " + (date.today() + timedelta(days=160)).strftime("%d %b %Y") in past
    assert (date.today() - timedelta(days=20)).strftime("%d %b %Y") not in past

    brief = _build_renewal_brief_context(soon)
    assert brief["days_to_expiry"] == 14 and brief["renewal_urgency"] == "critical"
    assert brief["renewal_date"] == (date.today() + timedelta(days=14)).isoformat()
    again = _build_renewal_brief_context(renewed)
    assert again["renewal_date"] == (date.today() + timedelta(days=160)).isoformat()
    assert again["days_to_expiry"] == 160


def test_the_contract_analysis_bands_its_dates_by_the_same_rule(db_session, make_org):
    from app.services.clause_extraction_service import ClauseExtractionService

    mine = make_org("orr-d")
    tag = uuid.uuid4().hex[:6]
    soon = _contract(db_session, mine, "Analysed %s" % tag, 14, 197)
    timeline = ClauseExtractionService()._build_obligations_timeline([soon])
    by_kind = {e["event_type"]: e for e in timeline}
    assert by_kind["renewal"]["days_remaining"] == 14 and by_kind["renewal"]["urgency"] == "critical"
    assert by_kind["end"]["days_remaining"] == 197 and by_kind["end"]["urgency"] == "info"


def test_a_deadline_exactly_30_days_away_is_critical_in_the_analysis_as_on_the_renewals_page(db_session, make_org):
    from app.services.clause_extraction_service import ClauseExtractionService
    from app.services.vendor_register import renewal_band

    mine = make_org("orr-e")
    edge = _contract(db_session, mine, "Edge %s" % uuid.uuid4().hex[:6], 30, 213)
    events = {e["event_type"]: e for e in ClauseExtractionService()._build_obligations_timeline([edge])}
    assert renewal_band(30) == "critical"
    assert events["renewal"]["days_remaining"] == 30 and events["renewal"]["urgency"] == "critical"
