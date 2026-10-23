"""Tests for scripts/check_two_org_coverage.py.

These build a tiny synthetic tree (a small component-inventory.json plus a few
fake test files) under a tmp directory and point the check at it with --root, so
the test is fast and does not depend on the ~5MB real inventory. The real
inventory is exercised when the gate runs against the repository root.

The association a covered family rests on is FAMILY-TARGETED: a two-organisation
test covers a family only when the test source names one of that family's
distinctive route rules (a URL path prefix) or an endpoint of the family's
blueprint in a url_for-style string. A test that merely contains the family's
English word as incidental data -- a capability value, an unrelated URL, a
parameter name -- does NOT count. Two of the tests below pin exactly that
distinction: one positive (a real route reference covers the family) and one
negative (incidental data does not).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_two_org_coverage.py")


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _inventory(routes):
    return json.dumps({"routes": routes})


def _run(root: str, *args: str):
    result = subprocess.run(
        [sys.executable, SCRIPT, "--root", root, *args],
        capture_output=True,
        text=True,
    )
    return result


def _build_tree(tmp_path, routes, test_files):
    root = str(tmp_path)
    _write(os.path.join(root, "docs", "component-inventory.json"), _inventory(routes))
    # ensure the tests directory exists even when empty
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    for rel, text in test_files.items():
        _write(os.path.join(root, rel), text)
    return root


def test_family_with_matching_two_org_test_is_covered(tmp_path):
    # The two-org test names the family's route rule, so it is associated.
    routes = [
        {"blueprint": "widgets", "login_required": True, "rule": "/widgets/list"},
    ]
    files = {
        "tests/test_widgets_iso.py": (
            "def two_orgs():\n    pass\n\n"
            "def test_widgets_isolation(two_orgs):\n"
            "    client.get('/widgets/list')\n"
            "    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_two_org_test_without_family_route_does_not_cover(tmp_path):
    # The test is a genuine two-org test but never touches the family's routes,
    # so the family stays uncovered -- being a two-org test is not enough.
    routes = [
        {"blueprint": "widgets", "login_required": True, "rule": "/widgets/list"},
    ]
    files = {
        "tests/test_other_iso.py": (
            "def two_orgs():\n    pass\n\n"
            "def test_other_isolation(two_orgs):\n"
            "    client.get('/unrelated/path')\n"
            "    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root)
    assert result.returncode == 1, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("widgets" in line for line in lines[:-1])


def test_family_without_two_org_test_is_uncovered(tmp_path):
    routes = [
        {"blueprint": "lonely", "login_required": True, "rule": "/lonely/"},
    ]
    files = {
        "tests/test_unrelated.py": "def test_nothing():\n    assert True\n",
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root)
    # An uncovered non-startup family over baseline makes the gate fail (exit 1).
    assert result.returncode == 1, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("lonely" in line for line in lines[:-1])


def test_family_that_does_not_read_org_data_is_ignored(tmp_path):
    # No login_required route -> not a tenant-scoped surface -> not counted.
    routes = [
        {"blueprint": "public_bp", "login_required": False, "rule": "/public/"},
    ]
    root = _build_tree(tmp_path, routes, {})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_escape_hatch_excuses_a_family(tmp_path):
    routes = [
        {"blueprint": "excused_bp", "login_required": True, "rule": "/excused/"},
    ]
    files = {
        "tests/test_marker.py": (
            "# two-org-coverage-ok: excused_bp read-only status surface, T-qa-13\n"
            "def test_marker():\n    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_escape_hatch_reason_prose_does_not_excuse_other_families(tmp_path):
    """Only the family named before the free-text reason is excused.

    A marker ``two-org-coverage-ok: <family> is a public endpoint`` must excuse
    only ``<family>``; the reason words (``is``, ``a``, ``public``, ``endpoint``)
    must NOT be treated as family names. Here two families read org data: the
    named one is excused and stays covered, while a family whose name happens to
    be a reason word (``public``) is not excused and stays uncovered.
    """
    routes = [
        {"blueprint": "webhook", "login_required": True, "rule": "/webhook/"},
        {"blueprint": "public", "login_required": True, "rule": "/public/surface"},
    ]
    files = {
        "tests/test_marker.py": (
            "# two-org-coverage-ok: webhook is a public endpoint, T-qa-13\n"
            "def test_marker():\n    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root)
    assert result.returncode == 1, result.stderr
    lines = result.stdout.strip().splitlines()
    # 'public' leaked out of the reason prose would wrongly be excused; it must
    # remain uncovered, so exactly one family is reported.
    assert lines[-1] == "1"
    assert any("public" in line for line in lines[:-1])
    # 'webhook' is genuinely named and must not be reported as uncovered.
    assert not any("webhook" in line for line in lines[:-1])


def test_count_prints_trailing_integer(tmp_path):
    routes = [
        {"blueprint": "a_bp", "login_required": True, "rule": "/a/"},
        {"blueprint": "b_bp", "login_required": True, "rule": "/b/"},
    ]
    root = _build_tree(tmp_path, routes, {})
    result = _run(root, "--count")
    last = result.stdout.strip().splitlines()[-1]
    assert last.isdigit()
    assert last == "2"


def test_startup_families_all_covered_by_family_targeted_tests(tmp_path):
    """Each startup family is covered only by a two-organisation test that
    references one of its own route rules, not by a keyword in a comment.

    One route per startup family blueprint, all reading org data. A separate
    two-organisation test per family names that family's own route path (or a
    url_for-style endpoint string), which is what associates it.
    """
    startup_blueprints = {
        "account": "/account/manage",
        "m365_connector": "/admin/connectors",
        "intelligence_ui": "/intelligence/ask",
        "unified_applications": "/applications/list",
        "billing": "/admin/billing/",
        "team": "/admin/team",
        "dashboard": "/dashboard/overview",
    }
    routes = [
        {"blueprint": bp, "login_required": True, "rule": rule}
        for bp, rule in startup_blueprints.items()
    ]
    files = {}
    for bp, rule in startup_blueprints.items():
        files[f"tests/test_{bp}_two_org.py"] = (
            "def two_orgs():\n    pass\n\n"
            f"def test_{bp}_isolation(two_orgs):\n"
            f"    client.get('{rule}')\n"
            "    assert True\n"
        )
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_incidental_keyword_does_not_cover_startup_family(tmp_path):
    """A test that merely contains a family's English word as incidental data
    -- a capability value 'Billing', an unrelated '/intelligence/' URL, or a
    'read_only_account' parameter name -- does NOT cover that family.

    This is the regression the loose substring rule allowed: the keywords
    collide with ordinary test content that has nothing to do with the family's
    isolation boundary. Here the only two-organisation test touches an
    unrelated route, so billing, intelligence and account must stay uncovered
    even though their keywords appear in the source.
    """
    routes = [
        {"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"},
        {"blueprint": "intelligence_ui", "login_required": True, "rule": "/intelligence/ask"},
        {"blueprint": "account", "login_required": True, "rule": "/account/manage"},
    ]
    files = {
        "tests/test_incidental.py": (
            "def two_orgs():\n    pass\n\n"
            "def test_incidental_only(two_orgs):\n"
            "    # business_capability='Billing'\n"
            "    row = make_thing(business_capability='Billing')\n"
            "    url = '/intelligence/'  # a bare prefix used as unrelated data\n"
            "    read_only_account = object()\n"
            "    client.get('/unrelated/surface')\n"
            "    assert row and url and read_only_account\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    # All three startup families must be uncovered: no genuine route reference.
    counted = _run(root, "--startup-only", "--count")
    assert counted.returncode == 1, counted.stderr
    assert counted.stdout.strip().splitlines()[-1] == "3"
    # The named output reports the uncovered startup families by family name.
    named = _run(root, "--startup-only")
    joined = named.stdout
    assert "admin billing" in joined
    assert "intelligence" in joined
    assert "account" in joined


def test_named_suite_covers_only_when_it_builds_a_second_org(tmp_path):
    """A hard-coded named suite is a two-organisation test only while it still
    constructs a second organisation. Removing that usage drops its families.

    The authorisation matrix is recognised not by a bare file-name allow-list
    but because it defines an ``other_org`` fixture (a genuine second
    organisation). A copy that references the family's route but builds no
    second organisation does not qualify as a two-organisation test, so the
    family is uncovered.
    """
    routes = [
        {"blueprint": "team", "login_required": True, "rule": "/admin/team"},
    ]
    # No second-organisation construction -> not a two-org test at all.
    files = {
        "tests/smoke/test_authorisation_matrix.py": (
            "POLICY = {'/admin/team': set()}\n\n"
            "def test_matrix():\n"
            "    client.get('/admin/team')\n"
            "    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 1, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "1"

    # Add a genuine second-organisation fixture and the family is covered.
    files["tests/smoke/test_authorisation_matrix.py"] = (
        "POLICY = {'/admin/team': set()}\n\n"
        "def other_org_fixture():\n"
        "    return make_org('second')\n\n"
        "def test_matrix(other_org_fixture):\n"
        "    client.get('/admin/team')\n"
        "    assert True\n"
    )
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_dual_org_fixture_pair_qualifies_as_two_org_test(tmp_path):
    """A test that requests both org_a and org_b is a two-organisation test
    (the tenant-isolation convention), and it covers a family whose route it
    names."""
    routes = [
        {"blueprint": "m365_connector", "login_required": True, "rule": "/admin/connectors"},
    ]
    files = {
        "tests/test_connector_scope.py": (
            "def test_connector_isolation(org_a, org_b, client):\n"
            "    resp = client.get('/admin/connectors/m365')\n"
            "    assert resp is not None\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_two_org_signal_in_unrelated_function_does_not_cover_family(tmp_path):
    """HIGH-3: a two-organisation fixture consumed by ONE test must not vouch for
    a DIFFERENT, single-organisation test that merely references the family's
    route in the same file.

    This mirrors tests/smoke/test_authorisation_matrix.py exactly: an
    ``other_org`` fixture feeds an interface-register isolation test, while a
    separate single-organisation test references the billing family's route.
    The file builds a second organisation, but not for billing, so billing must
    stay uncovered -- the old file-level rule wrongly counted it.
    """
    routes = [
        {"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"},
        {"blueprint": "interface_register", "login_required": True, "rule": "/interface-register/"},
    ]
    files = {
        "tests/smoke/test_authorisation_matrix.py": (
            "def other_org_initiative():\n"
            "    return make_org('second')\n\n"
            "def test_interface_register_other_org_is_404(other_org_initiative):\n"
            "    client.get('/interface-register/?initiative_id=1')\n"
            "    assert True\n\n"
            "def test_billing_policy_matrix():\n"
            "    # single-org policy matrix test, references the family route only\n"
            "    client.get('/admin/billing/')\n"
            "    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    # Billing (startup) has a second-org fixture only for the interface-register
    # test; it must stay uncovered. interface_register (non-startup) is covered.
    startup = _run(root, "--startup-only", "--count")
    assert startup.returncode == 1, startup.stderr
    assert startup.stdout.strip().splitlines()[-1] == "1"
    named = _run(root, "--startup-only")
    assert "admin billing" in named.stdout


def test_same_unit_builds_two_orgs_and_drives_family_route_covers(tmp_path):
    """HIGH-3 positive: when the SAME test both builds a second organisation and
    drives the family's own route, the family is covered."""
    routes = [
        {"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"},
    ]
    files = {
        "tests/smoke/test_billing_iso.py": (
            "def test_billing_two_org_isolation(org_a, org_b, client):\n"
            "    client.get('/admin/billing/', org=org_a)\n"
            "    resp = client.get('/admin/billing/', org=org_b)\n"
            "    assert resp.status_code == 404\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_two_org_fixture_used_by_test_that_drives_family_route_covers(tmp_path):
    """HIGH-3 positive variant: a two-org fixture is bound to a test that itself
    drives the family route, so the test unit is associated through the fixture
    it requests."""
    routes = [
        {"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"},
    ]
    files = {
        "tests/smoke/test_billing_iso.py": (
            "def other_org():\n"
            "    return make_org('second')\n\n"
            "def test_billing_isolation(other_org, client):\n"
            "    client.get('/admin/billing/')\n"
            "    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_escape_hatch_cannot_excuse_startup_family(tmp_path):
    """A startup family named in an escape-hatch marker is never excused: the
    hatch cannot stand in for a real startup two-organisation test."""
    routes = [
        {"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"},
    ]
    files = {
        "tests/test_marker.py": (
            "# two-org-coverage-ok: billing has no tenant boundary, T-qa-13\n"
            "def test_marker():\n    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--startup-only", "--count")
    assert result.returncode == 1, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "1"
    named = _run(root, "--startup-only")
    assert "admin billing" in named.stdout


def test_escape_hatch_requires_reason_and_brief_id(tmp_path):
    """A marker that names a family but carries no reason-with-brief-id excuses
    nothing: the family stays uncovered."""
    routes = [
        {"blueprint": "status_bp", "login_required": True, "rule": "/status/panel"},
    ]
    # A bare family name, no reason and no brief id -> not a valid excuse.
    files = {
        "tests/test_marker.py": (
            "# two-org-coverage-ok: status_bp\n"
            "def test_marker():\n    assert True\n"
        ),
    }
    root = _build_tree(tmp_path, routes, files)
    result = _run(root)
    assert result.returncode == 1, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("status_bp" in line for line in lines[:-1])

    # A reason but still no brief id -> also not a valid excuse.
    files["tests/test_marker.py"] = (
        "# two-org-coverage-ok: status_bp read-only status surface\n"
        "def test_marker():\n    assert True\n"
    )
    root = _build_tree(tmp_path, routes, files)
    result = _run(root)
    assert result.returncode == 1, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "1"

    # A reason AND a brief id -> excused (non-startup family).
    files["tests/test_marker.py"] = (
        "# two-org-coverage-ok: status_bp read-only status surface, T-qa-13\n"
        "def test_marker():\n    assert True\n"
    )
    root = _build_tree(tmp_path, routes, files)
    result = _run(root, "--count")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"
