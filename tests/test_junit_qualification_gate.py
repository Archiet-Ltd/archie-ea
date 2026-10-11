"""Qualification must reject skipped or absent execution, not just exit zero."""
from pathlib import Path
import os
import subprocess
import sys

import pytest
import yaml

GATE = Path(__file__).resolve().parents[1] / 'scripts/ci/check_junit_qualification.py'


def test_independent_browser_stages_run_after_another_journey_fails():
    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    steps = (workflow['jobs']['smoke-other']['steps']
             + workflow['jobs']['smoke-adversarial']['steps'])
    stages = [step for step in steps if any(name in step.get('name', '')
              for name in ('Interaction-reality census', 'Adversarial regression probes',
                           'Enabled AI protocol', 'Providerless saved-guide'))]
    assert len(stages) == 4
    for step in stages:
        assert step.get('if') == "${{ !cancelled() && steps.smoke-schema.outcome == 'success' }}"
        assert not step.get('continue-on-error', False)


def test_provider_modes_and_history_report_are_explicit():
    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    adversarial = next(step for step in workflow['jobs']['smoke-adversarial']['steps']
                       if step.get('name', '').startswith('Adversarial regression'))
    assert adversarial['env']['SMOKE_AI_PROTOCOL_STUB'] == '1'
    history = next(step for step in workflow['jobs']['smoke-other']['steps']
                   if step.get('name') == 'Providerless saved-guide history journeys')
    assert history['env'] == {'AI_PAGE_GUIDE_ENABLED': 'true', 'SMOKE_AI_PROTOCOL_STUB': ''}
    required = next(step for step in workflow['jobs']['smoke']['steps']
                    if step.get('name') == 'Require all browser reports without skips')
    assert 'chromium-guide-history.xml' in required['run']


def test_compatibility_requires_ai_and_providerless_history_execution():
    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['browser-compatibility']['steps']
    for name, report, env in [
        ('Cross-browser AI submission and persistence', '-ai-protocol.xml', {'SMOKE_AI_PROTOCOL_STUB': '1'}),
        ('Cross-browser providerless saved history', '-guide-history.xml',
         {'AI_PAGE_GUIDE_ENABLED': 'true', 'SMOKE_AI_PROTOCOL_STUB': ''}),
    ]:
        stage = next(step for step in steps if step.get('name') == name)
        assert stage['env'] == env
        assert stage['if'] == "${{ !cancelled() && steps.compatibility-schema.outcome == 'success' }}"
        assert report in stage['run']
        required = next(step for step in steps if step.get('name') == 'Require executed compatibility tests without skips')
        assert report in required['run']


def test_import_browser_journeys_are_arguments_to_one_pytest_command():
    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    stage = next(step for step in workflow['jobs']['browser-compatibility']['steps']
                 if step.get('name') == 'Run cross-browser critical journeys')
    command = stage['run']
    # Extra indentation inside YAML >- preserves newlines and turns filenames
    # into separate shell commands instead of pytest arguments.
    assert len(command.splitlines()) == 1
    assert command.startswith('pytest ')
    for path in ('tests/smoke/test_manual_application_import.py',
                 'tests/smoke/test_application_import_history_journey.py'):
        assert command.split().count(path) == 1
    assert '--junitxml=' in command


@pytest.mark.parametrize('xml,ok', [
    ('<testsuites><testsuite tests="1"><testcase name="real"/></testsuite></testsuites>', True),
    ('<testsuite><testcase name="xfail"><skipped type="pytest.xfail"/></testcase></testsuite>', False),
    ('<testsuite><testcase name="skip"><skipped/></testcase></testsuite>', False),
    ('<testsuite><testcase name="bad"><failure/></testcase></testsuite>', False),
    ('<testsuite><testcase name="error"><error/></testcase></testsuite>', False),
    ('<testsuite tests="99"/>', False),
    ('broken xml', False),
])
def test_actual_testcases_required_without_nonpasses(tmp_path, xml, ok):
    assert GATE.is_file()
    report = tmp_path / 'report.xml'
    report.write_text(xml, encoding='utf-8')
    result = subprocess.run([sys.executable, str(GATE), str(report)], capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr


def test_missing_report_fails_even_when_another_passes(tmp_path):
    assert GATE.is_file()
    report = tmp_path / 'good.xml'
    report.write_text('<testsuite><testcase name="ok"/></testsuite>', encoding='utf-8')
    result = subprocess.run([sys.executable, str(GATE), str(report), str(tmp_path / 'absent.xml')],
                            capture_output=True, text=True)
    assert result.returncode != 0


@pytest.mark.parametrize('case', ['skip', 'xfail', 'xpass'])
def test_real_pytest_nonpass_report_is_rejected(tmp_path, case):
    test = tmp_path / 'test_example.py'
    body = {
        'skip': 'import pytest\ndef test_case(): pytest.skip("not exercised")\n',
        'xfail': 'import pytest\n@pytest.mark.xfail(reason="known issue")\ndef test_case(): assert False\n',
        'xpass': 'import pytest\n@pytest.mark.xfail(reason="stale expectation")\ndef test_case(): assert True\n',
    }[case]
    test.write_text(body, encoding='utf-8')
    report = tmp_path / 'actual.xml'
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='')
    run = subprocess.run([sys.executable, '-m', 'pytest', str(test), '-q',
                          '-o', 'xfail_strict=true', '--junitxml=' + str(report)],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == (1 if case == 'xpass' else 0), run.stdout + run.stderr
    result = subprocess.run([sys.executable, str(GATE), str(report)], capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
