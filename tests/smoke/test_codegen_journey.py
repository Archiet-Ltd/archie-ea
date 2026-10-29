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
    """An architect who regenerates with Genome mode (not Quick Generate's
    deterministic shortcut) sees generated files and the quality panel --
    reproduces the T-codegen-generate-01 ledger finding, where this same
    click sequence returned "Generation failed: Instance <CodegenGeneration>
    is not bound to a Session" instead.

    Genome is the wizard's *default* generation_mode, but on a seeded
    solution with a linked ArchiMate element and no prior UML, Phase 1
    offers only "Quick Generate" (which force-sets generation_mode to
    "deterministic") or AI Enrich (needs an LLM key, unavailable here) --
    there is no one-click path straight to Genome, and generate-stream
    (what the UI's Generate button actually calls) 400s with "No ArchiMate
    elements found" for a solution with neither a linked element nor UML,
    so Skip Enrichment's "no architecture yet" shortcut is not a route to
    it either. An architect who wants Genome instead runs Quick Generate
    first (which unlocks Phase 3), reopens Phase 3 (Configure), switches
    the mode radio from the deterministic value Quick Generate left behind
    back to "Genome", saves, and regenerates. That second, genome-mode
    generate-stream call is the one the ledger finding and this journey are
    about; the first (deterministic) call is the same call the Quick
    Generate journey above already covers and is not itself under test
    here.
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

        quick_generate = page.get_by_role('button', name='Quick Generate', exact=True)
        expect(quick_generate).to_be_visible(timeout=PAGE_TIMEOUT)
        quick_generate.click()

        first_files_label = page.locator('text=/\\d+ files$/').first
        expect(first_files_label).to_be_visible(timeout=GENERATE_TIMEOUT)

        # A completed generation replaces the wizard with the file-tree IDE view
        # (workbench.html's ideShowSetup flag), and the SSE connection this run
        # just finished can still be settling on the client past that point --
        # clicking "Setup" to flip ideShowSetup back can lose the race against
        # generate()'s own `finally` handler for as long as that takes. Reloading
        # sidesteps the race entirely: the browser drops the in-flight fetch on
        # navigation, and the bundle already persisted server-side (the same
        # persistence the Quick Generate journey above asserts on), so the
        # reloaded page starts from a clean, non-generating state.
        page.reload(timeout=PAGE_TIMEOUT)
        expect(first_files_label).to_be_visible(timeout=PAGE_TIMEOUT)
        page.screenshot(path='/tmp/claude-0/-home-user/7509dd19-0375-513c-b89f-6495c6787196/scratchpad/shots/after_reload.png', full_page=True)

        setup_button = page.get_by_role('button', name='Setup', exact=True)
        expect(setup_button).to_be_visible(timeout=PAGE_TIMEOUT)
        setup_button.click()
        page.wait_for_timeout(500)
        page.screenshot(path='/tmp/claude-0/-home-user/7509dd19-0375-513c-b89f-6495c6787196/scratchpad/shots/after_setup_click.png', full_page=True)

        # Reopen Phase 3 (Configure) -- Quick Generate's deterministic run already
        # unlocked it -- and switch the mode back to Genome.
        phase3_heading = page.get_by_text('Phase 3: Configure', exact=False)
        expect(phase3_heading).to_be_visible(timeout=PAGE_TIMEOUT)
        phase3_heading.click()
        genome_radio = page.locator('input[type="radio"][value="genome"]')
        expect(genome_radio).to_be_visible(timeout=PAGE_TIMEOUT)
        genome_radio.check()
        expect(genome_radio).to_be_checked()

        save_button = page.get_by_role('button', name='Save & Continue', exact=True)
        expect(save_button).to_be_enabled(timeout=PAGE_TIMEOUT)
        save_button.click()
        expect(save_button).to_be_hidden(timeout=PAGE_TIMEOUT)

        generate_button = page.get_by_role('button', name='Generate with Genome', exact=True)
        expect(generate_button).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(generate_button).to_be_enabled(timeout=PAGE_TIMEOUT)
        generate_button.click()

        # workbench.js's success banner auto-dismisses; the file-count badge does
        # not, so wait on it (same completion signal the Quick Generate journey
        # above uses) rather than racing a transient toast.
        files_label = page.locator('text=/\\d+ files$/').first
        expect(files_label).to_be_visible(timeout=GENERATE_TIMEOUT)
        file_count = int(files_label.inner_text().split()[0])
        assert file_count > 0, 'Generate with Genome reported success but produced no files'

        # The one failure this journey exists to catch: a session error surfacing
        # as a "Generation failed" banner instead of the quality panel.
        failure_banner = page.get_by_text('Generation failed', exact=False)
        expect(failure_banner).to_have_count(0)

        # The quality panel (including the business rule coverage bar from the
        # brief's acceptance criteria) must actually render, not just the file list.
        expect(page.get_by_text('Business rule coverage', exact=False)).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Persistence: reload and confirm the bundle survived server-side ----
        page.reload(timeout=PAGE_TIMEOUT)
        reloaded_files_label = page.locator('text=/\\d+ files$/').first
        expect(reloaded_files_label).to_be_visible(timeout=PAGE_TIMEOUT)
        reloaded_count = int(reloaded_files_label.inner_text().split()[0])
        assert reloaded_count == file_count, (
            'Generated file count did not survive a reload: %s before, %s after.'
            % (file_count, reloaded_count)
        )
    finally:
        page.close()
