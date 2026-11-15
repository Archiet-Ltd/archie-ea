"""Browser journey: an architect imports a motivation model and it arrives as modelled.

The file holds a Goal, an Outcome, a Principle, a Requirement and a Constraint. The
Outcome realizes the Goal, the Requirement realizes the Principle and the Constraint
influences the Goal. The architect imports it through the model-import screen, opens the
Goal in the element catalogue and must read the incoming relationships as realization and
influence, with no warning that the pair is outside the matrix.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
       identifier="id-model-{tag}" version="3.0">
  <name xml:lang="en">Motivation model {tag}</name>
  <elements>
    <element identifier="id-goal-{tag}" xsi:type="Goal"><name xml:lang="en">Goal {tag}</name></element>
    <element identifier="id-outcome-{tag}" xsi:type="Outcome"><name xml:lang="en">Outcome {tag}</name></element>
    <element identifier="id-principle-{tag}" xsi:type="Principle"><name xml:lang="en">Principle {tag}</name></element>
    <element identifier="id-req-{tag}" xsi:type="Requirement"><name xml:lang="en">Requirement {tag}</name></element>
    <element identifier="id-con-{tag}" xsi:type="Constraint"><name xml:lang="en">Constraint {tag}</name></element>
  </elements>
  <relationships>
    <relationship identifier="id-r1-{tag}" source="id-outcome-{tag}" target="id-goal-{tag}" xsi:type="Realization"/>
    <relationship identifier="id-r2-{tag}" source="id-req-{tag}" target="id-principle-{tag}" xsi:type="Realization"/>
    <relationship identifier="id-r3-{tag}" source="id-con-{tag}" target="id-goal-{tag}" xsi:type="Influence"/>
  </relationships>
</model>
"""

WARNING = "not in the ArchiMate 3.2 matrix"


def _shot(page, name):
    folder = os.environ.get("SMOKE_WALK_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name))


@pytest.mark.parametrize("width", [1440, 390])
def test_imported_motivation_relationships_keep_their_modelled_type(
    browser, live_server, seeded, tmp_path, width
):
    tag = uuid.uuid4().hex[:8]
    model = tmp_path / "motivation.xml"
    model.write_text(_MODEL.format(tag=tag), encoding="utf-8")

    context = browser.new_context(viewport={"width": width, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/import/oef", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.set_input_files("#oef_file", str(model))
        page.click("[data-testid='btn-preview-import']")
        preview = page.locator("[data-testid='import-preview']")
        expect(preview).to_be_visible(timeout=PAGE_TIMEOUT)
        assert WARNING not in preview.inner_text()
        page.click("[data-testid='btn-import-model']")
        result = page.locator("[data-testid='import-result']")
        expect(result).to_be_visible(timeout=PAGE_TIMEOUT)
        rel_panel = page.locator("[data-testid='relationship-import-result']")
        expect(rel_panel).to_be_visible(timeout=PAGE_TIMEOUT)
        assert WARNING not in result.inner_text(), result.inner_text()
        created = int(page.locator("[data-testid='result-relationships-created']").first.inner_text().strip())
        assert created == 3, rel_panel.inner_text()
        _shot(page, "motivation-import-result-%d.png" % width)

        page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("input[placeholder='Search by name...']").fill("Goal " + tag)
        row = page.locator("tr[data-testid='element-row']", has_text="Goal " + tag)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        row.click()

        outcome = page.locator("div.rounded-md", has_text="Outcome " + tag)
        constraint = page.locator("div.rounded-md", has_text="Constraint " + tag)
        expect(outcome.last).to_contain_text("realization", ignore_case=True, timeout=PAGE_TIMEOUT)
        expect(constraint.last).to_contain_text("influence", ignore_case=True, timeout=PAGE_TIMEOUT)
        panel = page.locator("div.space-y-2", has_text="Outcome " + tag).last
        assert "association" not in panel.inner_text().lower(), panel.inner_text()
        _shot(page, "motivation-goal-relationships-%d.png" % width)
    finally:
        context.close()
