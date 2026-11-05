"""An architect maps a capability to APQC processes and reads the codes in the order they are numbered.

The goal: open "Map to APQC Process Framework" on the capability map, in a seeded
organisation on a 260-row catalogue (en_US collation), and read 1.1, 1.2, 1.10, 2.1,
9.0, 10.0, 13.0 in that order, with the dialog adding no ordering of its own.
"""

import re
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WANTED = ["1.1", "1.2", "1.10", "2.1", "9.0", "10.0", "13.0"]


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
        org = Organization(name="Dialog %s" % suffix, slug="dialog-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        user = User(email="dialog.%s@example.com" % suffix, first_name="Dialog", last_name="Reader",
                    organization_id=org.id, enterprise_role="platform_admin", confirmed=True)
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        user.onboarding_completed_at = datetime.utcnow()
        db.session.add(user)
        # 260 codes, inserted highest first so id order and collation order are both wrong
        codes = ["%d.%d" % (g, h) for g in range(1, 14) for h in range(0, 20)]
        for extra in WANTED:
            if extra not in codes:
                codes.append(extra)
        rows = [APQCProcess(process_code=c, process_name="Dialog %s %s" % (c, suffix),
                            category_level_1="Dialog %s" % suffix) for c in reversed(codes)]
        db.session.add_all(rows)
        db.session.commit()
        ids = [r.id for r in rows]
        email = user.email
    yield {"email": email, "suffix": suffix, "codes": codes}
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


@pytest.mark.parametrize("size,scheme", [((1440, 900), "light"), ((390, 844), "dark")])
def test_capability_mapping_dialog_lists_codes_in_natural_order(browser, live_server, world, size, scheme):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        page.goto(live_server + "/capability-map/dashboard", wait_until="networkidle")
        page.evaluate("async () => { await openCapabilityAPQCMapping(999999, 'Order review') }")
        page.wait_for_function(
            "(s) => { const m = document.querySelector('#apqc-mapping-modal'); return m && m.textContent.includes(s) }",
            arg="Dialog 13.0 " + world["suffix"],
        )
        spans = page.evaluate(
            "() => Array.from(document.querySelectorAll('#apqc-mapping-modal span.font-mono')).map(s => s.textContent.trim())"
        )
        suffix_codes = [c for c in spans if re.match(r"^\d+(\.\d+)*[a-z]?$", c)]
        assert len(suffix_codes) >= 260, len(suffix_codes)
        # everything the person reads is in natural order, with no ordering added in the browser
        assert suffix_codes == sorted(suffix_codes, key=_key), suffix_codes[:30]
        wanted = [c for c in suffix_codes if c in WANTED]
        assert wanted == WANTED, wanted
        # a search keeps the order too
        page.evaluate(
            "() => { const d = Alpine.$data(document.querySelector('#apqc-mapping-modal')); d.searchQuery = '%s'; d.filterProcesses() }"
            % world["suffix"]
        )
        again = page.evaluate(
            "() => Array.from(document.querySelectorAll('#apqc-mapping-modal span.font-mono')).map(s => s.textContent.trim())"
        )
        assert [c for c in again if c in WANTED] == WANTED
    finally:
        ctx.close()
