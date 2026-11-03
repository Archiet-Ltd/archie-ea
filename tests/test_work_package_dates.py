"""A work package's dates: an end before the start is refused with a plain message, by the one writer."""

from datetime import datetime

import pytest

from app.services import work_package_service as svc


def test_create_refuses_an_end_before_the_start(db_session, make_org):
    org = make_org("wp-dates")
    with pytest.raises(svc.WorkPackageError) as refused:
        svc.create_work_package(organization_id=org.id, name="Backwards",
                                start_date="2026-12-10", end_date="2026-12-01")
    assert str(refused.value) == "End date must not be before the start date."
    assert refused.value.field == "end_date"


def test_update_refuses_an_end_before_the_start_and_keeps_the_saved_dates(db_session, make_org):
    org = make_org("wp-dates-update")
    wp = svc.create_work_package(organization_id=org.id, name="Forwards",
                                 start_date="2026-12-01", end_date="2026-12-20")
    db_session.commit()
    with pytest.raises(svc.WorkPackageError):
        svc.update_work_package(wp.id, organization_id=org.id, end_date="2026-11-30")
    db_session.rollback()
    db_session.expire_all()
    fresh = svc.get_work_package(wp.id, org.id)
    assert fresh.end_date == datetime(2026, 12, 20)


def test_the_same_day_and_a_later_end_are_accepted(db_session, make_org):
    org = make_org("wp-dates-ok")
    same = svc.create_work_package(organization_id=org.id, name="Same day",
                                   start_date="2026-12-10", end_date="2026-12-10")
    later = svc.create_work_package(organization_id=org.id, name="Later",
                                    start_date="2026-12-10", end_date="2026-12-15")
    assert same.duration_days == 0 and later.duration_days == 5
