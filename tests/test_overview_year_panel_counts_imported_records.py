"""The Overview's year panel counts every record in the model, not only those the change history saw.

An import, a connector or a bulk loader that writes rows with a direct insert leaves no history row. Each
such record is counted in the year it entered the model, or under a plain line when that is unknown.
Two real organisations: the second's own records never move the first's figures.
"""
from datetime import datetime

from sqlalchemy import insert, text

from app import db
from app.models import ArchiMateElement
from app.modules.dashboard.services.overview_figures import build_overview
from app.services.archimate_backbone import create_backbone_element
from app.services.change_source import change_source, dated_when_written
from tests.test_sample_company_working_data import _architect


def _imported(org, count, entered, before_versions_were_kept=False):
    """Elements as a direct insert writes them: the row alone, no history row.

    ``before_versions_were_kept`` writes them as a row written before each version was recorded: with
    the database's own triggers off for this transaction, so it carries no timestamp at all.
    """
    if before_versions_were_kept:
        db.session.execute(text("SET LOCAL session_replication_role = replica"))
    for n in range(count):
        db.session.execute(insert(ArchiMateElement.__table__).values(
            name="Imported %d" % n, type="BusinessActor", layer="Business",
            organization_id=org.id, source_kind="import", source_label="landscape.xlsx",
            source_recorded_at=entered,
        ))
    db.session.commit()


class _On:
    """Dates each record's first history row on one day, as a load spread over time does."""

    def __init__(self, day):
        self.day = day

    def date_new_record(self, connection, table_label, record):
        return self.day


def _by_hand(org, user, count, day=None):
    with change_source("screen", user_id=user.id), dated_when_written(_On(day) if day else None):
        for n in range(count):
            create_backbone_element(
                element_type="BusinessActor", layer="Business", name="Desk %d %s" % (n, day),
                organization_id=org.id,
            )
    db.session.commit()


def _activity(org, user):
    from flask import current_app

    with current_app.test_request_context("/"):
        return build_overview(org.id, user, now=datetime.utcnow())["activity"]


def test_the_year_panel_counts_imported_records_in_the_year_they_entered(make_org):
    now = datetime.utcnow()
    org_a, org_b = make_org(), make_org()
    user_a = _architect(org_a, "year-a@example.com")
    user_b = _architect(org_b, "year-b@example.com")
    _imported(org_b, 4, datetime(now.year, 1, 2))
    before_b = _activity(org_b, user_b)

    _imported(org_a, 10, datetime(now.year, 1, 2))
    _by_hand(org_a, user_a, 3)
    _by_hand(org_a, user_a, 2, day=datetime(now.year - 1, 6, 1))

    activity = _activity(org_a, user_a)
    # the figures the card shows: 10 imported and 3 by hand this year, 2 by hand last year
    assert (activity["this_year"], activity["last_year"]) == (13, 2)
    assert activity["this_year_count"] == 13
    assert activity["before_last_year"] == activity["no_entry_date"] == 0
    assert activity["this_year_count"] + activity["last_year"] == 15
    assert before_b["this_year_count"] == _activity(org_b, user_b)["this_year_count"] == 4


def test_a_record_with_no_entry_date_is_counted_under_a_plain_line(make_org):
    org_a, org_b = make_org(), make_org()
    user_a = _architect(org_a, "undated-a@example.com")
    user_b = _architect(org_b, "undated-b@example.com")
    _imported(org_a, 3, None, before_versions_were_kept=True)
    _imported(org_b, 1, datetime(datetime.utcnow().year, 1, 2))

    activity = _activity(org_a, user_a)
    assert activity["this_year"] == "No records added this year. 3 records with no recorded entry date"
    assert activity["no_entry_date"] == 3
    assert _activity(org_b, user_b)["this_year"] == "1 added this year"


def test_the_week_and_the_ranges_count_imported_records_like_the_year(make_org):
    now = datetime.utcnow()
    org_a, org_b = make_org(), make_org()
    user_a = _architect(org_a, "week-a@example.com")
    user_b = _architect(org_b, "week-b@example.com")
    _imported(org_b, 5, now)
    before_b = _activity(org_b, user_b)

    _imported(org_a, 21, now)
    _by_hand(org_a, user_a, 3)

    activity = _activity(org_a, user_a)
    assert activity["this_year_count"] == 24
    for key in ("7d", "30d", "90d"):
        assert activity["ranges"][key]["total"] == 24, key
        assert activity["ranges"][key]["connected"] == 21, key
        assert activity["ranges"][key]["manual"] == 3, key
    assert activity["week"]["added"] == 24
    assert activity["week"]["total"] == 24
    assert sum(row["changes"] for row in activity["week"]["rows"]) == 24
    assert before_b["week"]["added"] == _activity(org_b, user_b)["week"]["added"] == 5
