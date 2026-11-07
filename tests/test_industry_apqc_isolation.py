"""Industry process reads: shared rows plus the caller's own, and codes with a letter.

Organisation A and B each own rows in one framework next to a shared row. Every reader of the process table gives a
signed-in organisation the shared row and its own rows and never the other's; a code such as ``10.2a`` does not stop
the framework screen.
"""
import uuid

import pytest
import sqlalchemy as sa


def _framework(db_session, rows):
    from app.models.industry_apqc import IndustryAPQCFramework, IndustryAPQCProcess

    code = "ISO" + uuid.uuid4().hex[:6].upper()
    fw = IndustryAPQCFramework(industry_code=code, industry_name="Isolation Industry", is_active=True)
    db_session.add(fw)
    db_session.flush()
    for number, owner, label, unique in rows:
        db_session.add(IndustryAPQCProcess(
            industry_framework_id=fw.id, industry_process_code=number, industry_process_name=f"{label} {number}",
            is_active=True, organization_id=owner, is_industry_unique=unique,
            regulatory_requirements=["SOX"] if unique else sa.null(),
        ))
    db_session.commit()
    return code


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"iso-{uuid.uuid4().hex[:8]}@example.test", first_name="I", last_name="S",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.commit()
    return user.id


@pytest.fixture()
def two_orgs(db_session, make_org):
    a, b = make_org("iso07-a"), make_org("iso07-b")
    rows = [("1.0", None, "Shared", False), ("1.1", a.id, "Alpha", False), ("1.2", a.id, "Alpha", True),
            ("1.3", b.id, "Bravo", False), ("1.4", b.id, "Bravo", True)]
    code = _framework(db_session, rows)
    return code, a, b, _user(db_session, a), _user(db_session, b)


def _names(payload):
    return sorted(p["industry_process_name"] for p in payload)


EXPECT_A = ["Alpha 1.1", "Alpha 1.2", "Shared 1.0"]
EXPECT_B = ["Bravo 1.3", "Bravo 1.4", "Shared 1.0"]


def test_process_api_gives_shared_plus_own_for_each_organisation(client, login_as, two_orgs):
    from app.models.user import User
    from app import db

    code, _a, _b, uid_a, uid_b = two_orgs
    for uid, expected in ((uid_a, EXPECT_A), (uid_b, EXPECT_B)):
        login_as(client, db.session.get(User, uid))
        resp = client.get(f"/industry-apqc/api/frameworks/{code}/processes")
        assert resp.status_code == 200
        body = resp.get_json()
        assert _names(body["processes"]) == expected
        assert body["total"] == 3


def test_framework_page_shows_shared_plus_own_for_each_organisation(client, login_as, two_orgs):
    from app.models.user import User
    from app import db

    code, _a, _b, uid_a, uid_b = two_orgs
    for uid, mine, theirs in ((uid_a, "Alpha 1.1", "Bravo"), (uid_b, "Bravo 1.3", "Alpha")):
        login_as(client, db.session.get(User, uid))
        resp = client.get(f"/industry-apqc/framework/{code}")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Shared 1.0" in html and mine in html
        assert theirs not in html


def test_other_readers_use_the_same_fence(app, db_session, two_orgs):
    """The service helpers and model classmethods are fenced too (organisation set as a request would set it)."""
    from flask import g
    from app.services.industry_apqc_service import IndustryAPQCService
    from app.models.industry_apqc import IndustryAPQCProcess

    code, a, b, _ua, _ub = two_orgs
    svc = IndustryAPQCService()
    with app.test_request_context("/"):
        for org, expected_unique in ((a, ["Alpha 1.2"]), (b, ["Bravo 1.4"])):
            g.current_org_id = org.id
            assert not any(p.organization_id not in (None, org.id) for p in svc.get_industry_processes(code))
            unique = _names([p.to_dict() for p in IndustryAPQCProcess.get_industry_unique_processes(code)])
            assert unique == expected_unique
            reg = _names([p.to_dict() for p in IndustryAPQCProcess.get_with_regulatory_requirements(code)])
            assert reg == expected_unique
            tree_codes = []

            def walk(nodes):
                for n in nodes:
                    tree_codes.append(n["name"])
                    walk(n["children"])

            walk(svc.build_hierarchy(code))
            assert not any(("Bravo" if org is a else "Alpha") in n for n in tree_codes)


def test_adopting_another_organisations_process_is_refused(app, db_session, two_orgs):
    from flask import g
    from app.models.industry_apqc import IndustryAPQCProcess
    from app.services.industry_apqc_service import IndustryAPQCService

    code, a, b, _ua, _ub = two_orgs
    theirs = db_session.query(IndustryAPQCProcess).filter_by(organization_id=b.id).first().id
    with app.test_request_context("/"):
        g.current_org_id = a.id
        assert IndustryAPQCService().adopt_process(code, theirs, a.id) == (None, False)


def test_a_code_with_a_letter_does_not_break_the_framework_page(client, login_as, db_session, make_org):
    from app.models.user import User

    org = make_org("iso07-letter")
    code = _framework(db_session, [("1.0", None, "First", False), ("10.2a", None, "Lettered", False),
                                   ("10.0", None, "Tenth", False), ("2.0", None, "Second", False)])
    uid = _user(db_session, org)
    login_as(client, db_session.get(User, uid))
    resp = client.get(f"/industry-apqc/framework/{code}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    order = [html.find(f'data-pcf-node="{n}"') for n in ("1.0", "2.0", "10.0")]
    assert all(i >= 0 for i in order) and order == sorted(order), order
    assert 'data-pcf-node="10.2a"' in html


def test_tree_orders_codes_naturally(app, db_session):
    from app.services.industry_apqc_service import IndustryAPQCService

    code = _framework(db_session, [("10.0", None, "T", False), ("2.0", None, "S", False), ("1.0", None, "F", False),
                                   ("10.2a", None, "L", False), ("10.2", None, "M", False)])
    with app.test_request_context("/"):
        tops = [n["code"] for n in IndustryAPQCService().build_hierarchy(code)]
    assert tops == ["1.0", "2.0", "10.0"], tops
