"""Code Workbench: a real deterministic codegen run, not just a dialog open/close.

test_solution_blueprint_controls.py already covers opening/closing the
"Code Generation" modal from the solution blueprint page, and the Code
Workbench page itself is reachable, but nothing previously clicked the real
"Quick Generate" control and checked an artifact came out and persisted.

This exercises the one-click deterministic path (codegen/workbench.js
quickGenerate() -> generate(), generation_mode='deterministic', no LLM
enrichment needed) against the `codegen_solution` fixture, which has one
ArchiMate element linked via SolutionArchiMateElement -- the precondition
codegen_routes.workbench_page checks (linked_elements_count > 0) before it
will even render the Quick Generate button. Confirms the generated file
list survives a hard reload, which is only possible if the file bundle
was written to app.modules.codegen.models.CodegenGeneration.generated_files
rather than held only in Alpine state.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Deterministic generation still does real file-template rendering server-side
# and can legitimately take tens of seconds for a multi-file FastAPI bundle.
GENERATE_TIMEOUT = 180_000
WORKBENCH_IDLE = (
    "() => { const el = document.querySelector('[data-workbench=\"1\"]'); "
    "return !!el && Alpine.$data(el).generating === false; }"
)


def _assert_visible_quality_panel(page):
    """Click the Quality tab and read the business rule coverage a person sees.

    The fixture solutions carry no must/should rules, so the figure the page
    shows is the one _compute_rule_coverage gives for an empty rule set (100:
    nothing to enforce is not penalised). The helper is the page's own, so a
    fixed value in the template would not agree with it for a seeded rule set.
    """
    from app.modules.codegen.routes._helpers import _compute_rule_coverage

    page.get_by_role('button', name='Quality', exact=True).click()
    panel = page.locator('.tab-pane:visible', has_text='Business rule coverage')
    expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
    row = panel.locator('div.group', has_text='Business rule coverage').first
    expect(row).to_be_visible()
    expected = round(_compute_rule_coverage([], '')['coverage_pct'])
    assert expected == 100
    shown = row.inner_text()
    assert ('%d%%' % expected) in shown, (
        'Business rule coverage on the panel reads %r, expected %d%%' % (shown, expected)
    )


def test_quick_generate_produces_and_persists_a_code_bundle(browser, live_server, seeded):
    page = browser.new_page()
    solution_id = seeded['ids']['codegen_solution']
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        response = page.goto(
            live_server + '/solutions/%s/codegen' % solution_id,
            timeout=PAGE_TIMEOUT,
        )
        assert response.status == 200
        expect(page.get_by_role('heading', name='Code Workbench')).to_be_visible(timeout=PAGE_TIMEOUT)

        quick_generate = page.get_by_role('button', name='Quick Generate', exact=True)
        expect(quick_generate).to_be_visible(timeout=PAGE_TIMEOUT)
        quick_generate.click()

        # workbench.js's own success banner ("Generated <N> files (...)") auto-
        # dismisses after 5s, which is too narrow a window to assert on
        # reliably against a real (tens-of-seconds) generation run. The file
        # count badge (`fileList.length + ' files'`) is not transient -- it
        # stays rendered once generation populates fileList, so wait on that
        # instead as the completion signal.
        files_label = page.locator('text=/\\d+ files$/').first
        expect(files_label).to_be_visible(timeout=GENERATE_TIMEOUT)
        file_count_text = files_label.inner_text()
        file_count = int(file_count_text.split()[0])
        assert file_count > 0, 'Quick Generate reported success but produced no files'

        # ---- Persistence: reload and confirm the bundle survived server-side ----
        # A completed generation switches the page from the setup wizard (which
        # has the "Code Workbench" <h1>) to the file-tree IDE view, so re-assert
        # on the file-count badge -- present in both views -- not the wizard's
        # heading, which is gone once files exist.
        page.reload(timeout=PAGE_TIMEOUT)
        reloaded_files_label = page.locator('text=/\\d+ files$/').first
        expect(reloaded_files_label).to_be_visible(timeout=PAGE_TIMEOUT)
        reloaded_count = int(reloaded_files_label.inner_text().split()[0])
        assert reloaded_count == file_count, (
            'Generated file count did not survive a reload: %s before, %s after -- '
            'the bundle was held only in client state, not persisted server-side.'
            % (file_count, reloaded_count)
        )
    finally:
        page.close()


def test_generate_with_genome_produces_files_and_quality_panel(browser, live_server, seeded):
    """An architect who chooses Genome mode -- the wizard's own default, not
    Quick Generate's deterministic shortcut -- sees generated files and the
    quality panel. Reproduces the T-codegen-generate-01 ledger finding,
    where this same click returned "Generation failed: Instance
    <CodegenGeneration> is not bound to a Session" instead.

    Genome is the wizard's *default* generation_mode (never touched by this
    journey), but Phase 4 (Generate) is gated behind phase >= 4 even though
    nothing about generating from the architecture already linked to this
    solution needs Phases 1-3: those exist to configure UML enrichment and
    field schemas that genome mode does not use (it reads ArchiMate
    elements directly -- see generate_code's own "genome bypasses UML
    entirely" comment). Quick Generate's one-click shortcut
    (quickGenerate(), workbench.js) reaches Phase 4 the same way skipping
    those phases does: by setting `this.phase = 4` directly, with no
    intermediate screen -- but it also force-sets generation_mode to
    "deterministic" first, which is Quick Generate's own case (covered by
    the journey above), not Genome's. This fixture's solution has never run
    Quick Generate, so generation_mode is still the wizard's own Genome
    default; advancing straight to Phase 4 the same way quickGenerate()
    does, without touching generation_mode, is what a one-click "Quick
    Generate with Genome" entry point would do if one existed for a
    solution with a linked element and no need for enrichment.
    """
    page = browser.new_page()
    solution_id = seeded['ids']['codegen_genome_solution']
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        response = page.goto(
            live_server + '/solutions/%s/codegen' % solution_id,
            timeout=PAGE_TIMEOUT,
        )
        assert response.status == 200
        expect(page.get_by_role('heading', name='Code Workbench')).to_be_visible(timeout=PAGE_TIMEOUT)

        # A solution with a linked architecture offers Skip enrichment on the
        # first visit: it opens Phase 3, where the person chooses Genome and
        # saves. The Generate button's own label then proves the mode. Every
        # step is a click; no test writes workbench state.
        page.get_by_role('button', name='Skip enrichment', exact=False).click()
        page.get_by_role('heading', name='Phase 3: Configure', exact=False).click()
        genome_choice = page.get_by_label('Genome', exact=False).first
        expect(genome_choice).to_be_visible(timeout=PAGE_TIMEOUT)
        genome_choice.check()
        expect(genome_choice).to_be_checked()
        page.get_by_role('button', name='Save & Continue', exact=False).click()

        generate_button = page.get_by_role('button', name='Generate with Genome', exact=True)
        expect(generate_button).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(generate_button).to_be_enabled(timeout=PAGE_TIMEOUT)
        generate_button.click()

        # The IDE header's Re-Generate button appears when the Genome run completes.
        expect(page.get_by_role('button', name='Re-Generate', exact=True)).to_be_visible(
            timeout=GENERATE_TIMEOUT
        )
        page.wait_for_function(WORKBENCH_IDLE, timeout=GENERATE_TIMEOUT)
        files_label = page.locator('text=/\\d+ files$/').first
        expect(files_label).to_be_visible(timeout=PAGE_TIMEOUT)
        file_count = int(files_label.inner_text().split()[0])
        assert file_count > 0, 'Generate with Genome reported success but produced no files'

        # The one failure this journey exists to catch: a session error surfacing
        # as a "Generation failed" banner instead of the quality panel.
        failure_banner = page.get_by_text('Generation failed', exact=False)
        expect(failure_banner).to_have_count(0)

        # ---- Persistence: reload and confirm the bundle survived server-side ----
        # Reloading mounts the IDE straight from the persisted bundle.
        page.reload(timeout=PAGE_TIMEOUT)
        reloaded_files_label = page.locator('text=/\\d+ files$/').first
        expect(reloaded_files_label).to_be_visible(timeout=PAGE_TIMEOUT)
        reloaded_count = int(reloaded_files_label.inner_text().split()[0])
        assert reloaded_count == file_count, (
            'Generated file count did not survive a reload: %s before, %s after.'
            % (file_count, reloaded_count)
        )

        # The Quality tab opens by click and its panel is what a person reads.
        _assert_visible_quality_panel(page)
    finally:
        page.close()


def test_quality_tab_opens_by_click_and_shows_rule_coverage(browser, live_server, seeded):
    """A person who generated code opens the Quality tab with a click and reads
    the coverage figure. Before the stray closing tag in the IDE's right panel
    was removed, the IDE collapsed to zero height under the AI Assistant panel
    and the tab was not clickable at all."""
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    solution_id = seeded['ids']['codegen_solution']
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        page.goto(live_server + '/solutions/%s/codegen' % solution_id, timeout=PAGE_TIMEOUT)
        quick_generate = page.get_by_role('button', name='Quick Generate', exact=True)
        if quick_generate.count():
            quick_generate.click()
            expect(page.locator('text=/\\d+ files$/').first).to_be_visible(timeout=GENERATE_TIMEOUT)
            page.wait_for_function(WORKBENCH_IDLE, timeout=GENERATE_TIMEOUT)
            page.reload(timeout=PAGE_TIMEOUT)
        expect(page.locator('text=/\\d+ files$/').first).to_be_visible(timeout=PAGE_TIMEOUT)
        _assert_visible_quality_panel(page)
    finally:
        page.close()


def test_seeded_rules_give_a_partial_coverage_figure_on_the_panel(browser, live_server, seeded):
    """With a must rule nothing implements and a should rule everything does,
    the panel reads the figure _compute_rule_coverage gives for the same rules
    and the generated code, and that figure is not 100."""
    from app import create_app
    from app.modules.codegen.models import CodegenGeneration
    from app.modules.codegen.routes._helpers import _compute_rule_coverage

    solution_id = seeded['ids']['codegen_rules_solution']
    rules = [
        {'name': 'Cap', 'condition': 'n<=3', 'severity': 'should'},
        {'name': 'Health endpoint', 'condition': 'health', 'severity': 'must'},
    ]
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    try:
        _login(page, live_server, seeded['emails']['solution_architect'])
        page.goto(live_server + '/solutions/%s/codegen' % solution_id, timeout=PAGE_TIMEOUT)
        page.get_by_role('button', name='Quick Generate', exact=True).click()
        expect(page.locator('text=/\\d+ files$/').first).to_be_visible(timeout=GENERATE_TIMEOUT)
        page.wait_for_function(WORKBENCH_IDLE, timeout=GENERATE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)

        app = create_app('testing')
        with app.app_context():
            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).order_by(
                CodegenGeneration.id.desc()).first()
            code = '\n'.join(str(v) for v in (gen.generated_files or {}).values())
        expected = round(_compute_rule_coverage(rules, code)['coverage_pct'])
        assert expected != 100, 'seeded rules must give a partial figure, got %d' % expected

        page.get_by_role('button', name='Quality', exact=True).click()
        panel = page.locator('.tab-pane:visible', has_text='Business rule coverage')
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        row = panel.locator('div.group', has_text='Business rule coverage').first
        shown = row.inner_text()
        assert ('%d%%' % expected) in shown and '100%' not in shown, (
            'Business rule coverage on the panel reads %r, expected %d%%' % (shown, expected))
        page.screenshot(path='/tmp/shot-quality-1440-light.png')
    finally:
        page.close()
