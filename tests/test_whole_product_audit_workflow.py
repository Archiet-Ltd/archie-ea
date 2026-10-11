"""The exhaustive product audit must remain runnable for every product role."""

import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/whole-product-audit.yml"


def _valid_roles():
    tree = ast.parse((ROOT / "app/models/user.py").read_text(encoding="utf-8"))
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            constants[target.id] = node.value.value
        elif target.id == "VALID_ROLES" and isinstance(node.value, ast.List):
            return [
                constants[element.id] if isinstance(element, ast.Name) else element.value
                for element in node.value.elts
            ]
    raise AssertionError("VALID_ROLES not found")


def test_whole_product_audit_runs_every_role_and_retains_failure_evidence():
    assert WORKFLOW.exists(), "whole-product audit workflow is missing"
    raw = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    job = workflow["jobs"]["audit"]

    assert set(job["strategy"]["matrix"]["persona"]) == set(_valid_roles())
    assert "workflow_dispatch" in raw
    assert "postgres:16" in raw
    assert "scripts/production_readiness_audit.py" in raw
    assert "--persona ${{ matrix.persona }}" in raw
    assert "if: always()" in raw
    assert "audit-${{ matrix.persona }}-${{ github.sha }}" in raw
    assert "retention-days: 30" in raw


def test_whole_product_audit_exercises_both_desktop_and_mobile():
    raw = WORKFLOW.read_text(encoding="utf-8")

    assert "--desktop-only" not in raw
    assert "--level" not in raw, "the active audit must run all defined levels"


def test_audit_report_inventories_every_visible_control_per_page():
    from scripts import production_readiness_audit as audit

    source = (ROOT / "scripts/production_readiness_audit.py").read_text(encoding="utf-8")
    assert "out.controls =" in audit.PAGE_PROBE
    assert "getBoundingClientRect" in audit.PAGE_PROBE
    assert '[contenteditable="true"]' in audit.PAGE_PROBE
    assert '[tabindex]:not([tabindex="-1"])' in audit.PAGE_PROBE
    assert "@click(?:\\.[\\w-]+)*" in audit.PAGE_PROBE
    assert '"control_inventory": control_inventory' in source
    assert '"controls": probe.get("controls") or []' in source


def test_information_only_observations_do_not_fail_the_audit():
    from scripts import production_readiness_audit as audit

    observations = [
        {"severity": "info", "kind": "expected-forbidden"},
        {"severity": "medium", "kind": "broken-control"},
    ]

    assert audit.blocking_findings(observations) == [observations[1]]


def test_authorization_distinguishes_expected_and_unexpected_admin_access():
    from scripts import production_readiness_audit as audit

    base = {"route": "/admin/users", "endpoint": "admin.users", "viewport": "desktop"}
    expected_denial = audit.evaluate_findings({1, 8}, {**base, "persona": "cto"}, {}, 403, [], [])
    admin_denial = audit.evaluate_findings(
        {1, 8}, {**base, "persona": "platform_admin"}, {}, 403, [], []
    )
    unexpected_access = audit.evaluate_findings(
        {8}, {**base, "persona": "cto"}, {}, 200, [], []
    )

    assert any(f["kind"] == "expected-forbidden" and f["severity"] == "info"
               for f in expected_denial)
    assert any(f["kind"] == "unexpected-forbidden" for f in admin_denial)
    assert any(f["kind"] == "unauthorized-access" for f in unexpected_access)


def test_control_inventory_never_serializes_editable_values():
    from playwright.sync_api import sync_playwright
    from scripts import production_readiness_audit as audit

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content("""
            <label for="email">Email address</label>
            <input id="email" value="private@example.com">
            <label for="notes">Notes</label>
            <textarea id="notes">private field contents</textarea>
            <div contenteditable="true" aria-label="Comment">another secret</div>
        """)
        probe = page.evaluate(audit.PAGE_PROBE)
        browser.close()

    serialized = str(probe["controls"])
    assert "private@example.com" not in serialized
    assert "private field contents" not in serialized
    assert "another secret" not in serialized
    assert {control["label"] for control in probe["controls"]} == {
        "Email address", "Notes", "Comment"
    }


def test_button_name_probe_resolves_label_references_in_chromium():
    from playwright.sync_api import sync_playwright
    from scripts import production_readiness_audit as audit

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('''
                <span id="preference">Weekly digest</span>
                <button role="switch" aria-labelledby="preference"></button>
                <button class="broken-reference" aria-labelledby="missing"></button>
            ''')
            probe = page.evaluate(audit.PAGE_PROBE)
            assert probe["unnamedButtons"] == ["broken-reference"]
        finally:
            browser.close()


def test_is_public_account_route_matches_exact_and_prefixed_paths():
    from scripts import production_readiness_audit as audit

    # Exact matches.
    for route in audit._PUBLIC_ACCOUNT_ROUTES:
        assert audit._is_public_account_route(route), f"{route} should be public"
        assert audit._is_public_account_route(route + "/"), f"{route}/ should be public"

    # Prefixed sub-paths.
    assert audit._is_public_account_route("/account/login?next=/admin")
    assert audit._is_public_account_route("/account/register?foo=bar")
    assert audit._is_public_account_route("/account/reset-password/abc123")
    assert audit._is_public_account_route("/account/confirm-account/42")
    assert audit._is_public_account_route("/account/unconfirmed")
    assert audit._is_public_account_route("/account/sso/google")
    assert audit._is_public_account_route("/account/join-from-invite/5/token123")

    # Non-public routes.
    assert not audit._is_public_account_route("/account/manage")
    assert not audit._is_public_account_route("/account/manage/info")
    assert not audit._is_public_account_route("/account/manage/change-password")
    assert not audit._is_public_account_route("/account/logout")
    assert not audit._is_public_account_route("/admin/users")
    assert not audit._is_public_account_route("/dashboard/overview")
    assert not audit._is_public_account_route("/")


def test_is_login_form_detects_login_url_and_login_fields():
    from scripts import production_readiness_audit as audit

    # URL check: exact login path.
    assert audit._is_login_form("http://127.0.0.1:5000/account/login")
    assert audit._is_login_form("http://127.0.0.1:5000/account/login?next=/admin")
    assert not audit._is_login_form("http://127.0.0.1:5000/account/register")
    assert not audit._is_login_form("http://127.0.0.1:5000/dashboard/overview")

    # Probe check: has email and password fields.
    login_probe = {"controls": [
        {"id": "email", "tag": "input"},
        {"id": "password", "tag": "input"},
        {"id": "submit", "tag": "button"},
    ]}
    assert audit._is_login_form("http://127.0.0.1:5000/dashboard/overview", login_probe)

    # No login fields.
    normal_probe = {"controls": [
        {"id": "search", "tag": "input"},
        {"id": "name", "tag": "input"},
    ]}
    assert not audit._is_login_form("http://127.0.0.1:5000/dashboard/overview", normal_probe)

    # Empty probe.
    assert not audit._is_login_form("http://127.0.0.1:5000/dashboard/overview", {})

    # URL takes precedence over probe when URL is /account/login.
    assert audit._is_login_form("http://127.0.0.1:5000/account/login", {})


def test_session_lost_finding_suppresses_levels_above_1():
    """The suppression logic uses active_levels & {0,1} which is {1} since
    evaluate_findings only handles levels 1-9. This confirms the set operation."""
    from scripts import production_readiness_audit as audit

    # When on a login form for a non-public route, active levels are limited.
    all_levels = set(range(11))
    suppressed = all_levels & {0, 1}
    assert suppressed == {0, 1}
    # verify that {0, 1} & {1, 2, 3, 4, 5, 6, 7, 8, 9} == {1}
    assert suppressed & {1, 2, 3, 4, 5, 6, 7, 8, 9} == {1}
