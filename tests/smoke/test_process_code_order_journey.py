"""An architect reads process codes in the order they are numbered.

The goal: open the process tree and the vendor analysis process picker and read the
codes 1.1, 1.2, 2.1, 10.0, 10.2a (and the level-one categories 1.0, 2.0, 9.0, 10.0, 13.0)
in that order, not in the database's order (10.0 between 1.1 and 1.2), in a seeded
organisation, on a database created with the en_US collation.
"""

import re
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

NATURAL = ["1.1", "1.2", "2.1", "10.0", "10.2a"]
LEVEL_ONE = ["1.0", "2.0", "9.0", "10.0", "13.0"]


@pytest.fixture
def world(app, live_server):
    from app import db
    from app.models.apqc_process import APQCProcess
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        collate = db.session.execute(
            db.text("select datcollate from pg_database where datname = current_database()")
        ).scalar()
        assert "en_US" in (collate or ""), "this journey needs an en_US database, this one is %r" % collate
        Role.insert_roles()
        org = Organization(name="Order %s" % suffix, slug="order-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        user = User(email="order.%s@example.com" % suffix, first_name="Order", last_name="Reader",
                    organization_id=org.id, enterprise_role="platform_admin", confirmed=True)
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        user.onboarding_completed_at = datetime.utcnow()
        db.session.add(user)
        rows = []
        for code in reversed(NATURAL):
            rows.append(APQCProcess(process_code=code, process_name="Tree %s %s" % (code, suffix)))
        for code in reversed(LEVEL_ONE):
            rows.append(APQCProcess(process_code=code, process_name="Category %s %s" % (code, suffix),
                                    category_level_1="Level one %s" % suffix))
        db.session.add_all(rows)
        db.session.commit()
        ids = [r.id for r in rows]
        email = user.email
    yield {"email": email, "suffix": suffix}
    with app.app_context():
        APQCProcess.query.filter(APQCProcess.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _key(code):
    from app.models.apqc_process import process_code_sort_key

    return process_code_sort_key(code)


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)])
def test_process_tree_lists_codes_in_the_order_they_are_numbered(browser, live_server, world, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        response = page.goto(live_server + "/capability-map/tree/apqc", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        page.wait_for_function(
            "(s) => document.querySelector('#tree-container') && document.querySelector('#tree-container').textContent.includes(s)",
            arg="Tree 1.1 " + world["suffix"],
        )
        text = page.locator("#tree-container").text_content()
        found = re.findall(r"Tree (\S+) %s" % world["suffix"], text)
        first_seen = list(dict.fromkeys(found))  # the tree draws each name twice
        assert first_seen == NATURAL, found
    finally:
        ctx.close()


def test_vendor_analysis_process_picker_lists_level_one_categories_in_order(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        page.goto(live_server + "/dashboard/vendor-analysis/new", wait_until="domcontentloaded")
        page.get_by_role("heading", name="New Vendor Options Analysis", level=1).wait_for()
        page.get_by_role("tab", name=re.compile("process", re.I)).first.click()
        box = page.get_by_role("listbox", name="L1 APQC processes")
        box.get_by_text("Category 1.0 %s" % world["suffix"]).wait_for()
        labels = [t.strip() for t in box.locator("label").all_inner_texts()]
        codes = [l.split()[0] for l in labels if l.split() and re.match(r"^\d+(\.\d+)*[a-z]?$", l.split()[0])]
        assert codes == sorted(codes, key=_key), codes
        mine = [c for l in labels for c in [l.split()[0]] if world["suffix"] in l]
        assert mine == LEVEL_ONE, labels
    finally:
        ctx.close()
