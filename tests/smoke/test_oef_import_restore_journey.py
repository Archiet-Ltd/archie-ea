"""Browser journey: restore an organisation to before a model import.

As an enterprise architect: import a model, make a change, open the restore
point, read the preview, choose the change to keep, confirm, reload, and read
what persisted -- the elements the import added are gone, the element it
overwrote is back, and the chosen change is applied again.
"""
import os
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OEF = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-journey">
  <name xml:lang="en">Restore journey {tag}</name>
  <elements>
    <element identifier="A" xsi:type="Node"><name xml:lang="en">Keep {tag}</name>
      <documentation>{desc}</documentation></element>
    {extra}
  </elements>
</model>
"""


def _write(tag, desc, extra=""):
    handle = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8")
    handle.write(OEF.format(tag=tag, desc=desc, extra=extra))
    handle.close()
    return handle.name


def _import(page, live_server, path, strategy):
    page.goto(live_server + "/architecture/import/oef", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.set_input_files("#oef_file", path)
    page.check("input[name='strategy'][value='%s']" % strategy)
    page.click("[data-testid='btn-import-model']")
    expect(page.locator("[data-testid='import-result']")).to_be_visible(timeout=PAGE_TIMEOUT)


def _find(page, live_server, name):
    resp = page.request.get(live_server + "/archimate/api/elements/search?q=" + name.replace(" ", "%20"))
    assert resp.ok, resp.status
    data = resp.json()
    matches = data.get("data") or data.get("elements") or data.get("results") or (
        data if isinstance(data, list) else [])
    return [m for m in matches if m.get("name") == name]


def test_restore_before_an_import_keeps_the_chosen_change(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:8]
    keep, added = "Keep " + tag, "Added " + tag
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    files = []
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])

        # Earlier state, then the faulty import that overwrites it and adds an element.
        files.append(_write(tag, "original"))
        _import(page, live_server, files[-1], "skip_duplicates")
        extra = '<element identifier="B" xsi:type="Node"><name xml:lang="en">%s</name></element>' % added
        files.append(_write(tag, "faulty overwrite", extra))
        _import(page, live_server, files[-1], "update_existing")
        kept = _find(page, live_server, keep)
        assert len(kept) == 1 and len(_find(page, live_server, added)) == 1
        keep_id = kept[0]["id"]

        # A change after the import, made the way the edit form saves it.
        csrf = page.locator("input[name='csrf_token']").first.get_attribute("value")
        edit = page.request.post(
            live_server + "/architecture/technology/Node/%s/edit" % keep_id,
            data={"name": keep, "description": "edited after the import"},
            headers={"X-CSRFToken": csrf, "Accept": "application/json", "Content-Type": "application/json"},
        )
        assert edit.ok, edit.status

        # Choose the restore point, read the preview, choose the change to keep, confirm.
        page.click("[data-testid='link-restore-points']")
        page.wait_for_url("**/restore-points", timeout=PAGE_TIMEOUT)
        page.locator("[data-testid='restore-point-row']").first.locator("[data-testid='btn-review-restore']").click()
        expect(page.locator("[data-testid='restore-summary']")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid='restore-removed-elements']")).to_contain_text(added)
        expect(page.locator("[data-testid='restore-reverts']")).to_contain_text(keep)
        page.locator("[data-testid='reapply-choice']").first.check()
        page.click("[data-testid='btn-confirm-restore']")
        expect(page.locator("[data-testid='restore-flash']")).to_contain_text("Restored", timeout=PAGE_TIMEOUT)

        # Reload and read what persisted.
        page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _find(page, live_server, added) == []
        detail = page.request.get(live_server + "/archimate/api/elements/%s/detail" % keep_id)
        assert detail.ok
        assert detail.json()["description"] == "edited after the import"
    finally:
        for path in files:
            try:
                os.unlink(path)
            except OSError:
                pass
        context.close()
