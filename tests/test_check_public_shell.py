"""The public-shell gate: each rule fires on exactly the thing it names, and the
real navbar, menus and footer pass with no findings."""
from pathlib import Path

import pytest

from scripts import check_public_shell as gate

ICONS = {"arrow-right", "chevron-down", "menu", "x"}


def _scan(tmp_path, source, name="sample.html"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return gate.scan(path, ICONS)


def _checks(findings):
    return sorted({check for _, check, _ in findings})


def test_a_compliant_fragment_has_no_findings(tmp_path):
    source = """
    <a href="{{ url_for('main.index') }}" class="flex gap-3 rounded-md p-3 hover:bg-accent text-muted-foreground">
      <i data-lucide="arrow-right" class="h-4 w-4"></i> Label
    </a>
    <script src="{{ url_for('static', filename='js/public/site_nav.js') }}" defer></script>
    <a href="#main-content">Skip</a>
    """
    assert _scan(tmp_path, source) == []


@pytest.mark.parametrize("source,check", [
    ('<div class="text-[#ff0000]">x</div>', "PUBLIC_COLOUR_LITERAL"),
    ('<div class="bg-white p-3">x</div>', "PUBLIC_COLOUR_LITERAL"),
    ('<div class="text-slate-500">x</div>', "PUBLIC_COLOUR_LITERAL"),
    ('<div class="w-[640px]">x</div>', "PUBLIC_ARBITRARY_VALUE"),
    ('<div style="color: red">x</div>', "PUBLIC_INLINE_CODE"),
    ('<style>.a{color:red}</style>', "PUBLIC_INLINE_CODE"),
    ('<script>var a = 1;</script>', "PUBLIC_INLINE_CODE"),
    ('<svg viewBox="0 0 1 1"></svg>', "PUBLIC_ICON_SET"),
    ('<i data-lucide="rocket"></i>', "PUBLIC_ICON_SET"),
    ('<span>\U0001F680 Launch</span>', "PUBLIC_ICON_SET"),
    ('<a class="public-btn public-btn--primary">x</a>', "PUBLIC_PARALLEL_SYSTEM"),
    ('<body class="theme-marketing">', "PUBLIC_PARALLEL_SYSTEM"),
    ('<span class="rounded-full border px-4 py-1">pill</span>', "PUBLIC_PARALLEL_SYSTEM"),
    ('<a href="/pricing">Pricing</a>', "PUBLIC_LITERAL_LINK"),
    ('<a href="https://example.com">x</a>', "PUBLIC_LITERAL_LINK"),
    ('<div class="dark:bg-slate-900">x</div>', "PUBLIC_DARK_VARIANT"),
])
def test_each_rule_fires_on_what_it_names(tmp_path, source, check):
    assert check in _checks(_scan(tmp_path, source))


def test_dark_hidden_and_dark_block_are_allowed(tmp_path):
    assert _scan(tmp_path, '<img class="dark:hidden"><img class="hidden dark:block">') == []


def test_json_ld_script_is_allowed(tmp_path):
    assert _scan(tmp_path, '<script type="application/ld+json">{}</script>') == []


def test_nav_contract_catches_a_role_menu_and_a_trigger_without_a_panel(tmp_path):
    navbar = tmp_path / "public_navbar.html"
    navbar.write_text(
        '<header data-site-nav><nav aria-label="Primary"></nav>'
        '<button type="button" aria-expanded="false" aria-controls="menu-x" aria-label="Menu"></button>'
        '<div role="menu"></div></header>',
        encoding="utf-8",
    )
    problems = [detail for _, check, detail in gate.scan(navbar, ICONS) if check == "PUBLIC_NAV_CONTRACT"]
    assert any("role=\"menu\"" in p for p in problems)
    assert any("matches no panel id" in p for p in problems)
    assert any("account button" in p for p in problems)


def test_footer_contract_catches_a_link_styled_span(tmp_path):
    footer = tmp_path / "public_footer.html"
    footer.write_text(
        '<footer><nav aria-label="Site map">{% for row in reg.footer.rows %}{% endfor %}</nav>'
        '<span class="text-sm underline">AGPL-3.0</span></footer>',
        encoding="utf-8",
    )
    problems = [detail for _, check, detail in gate.scan(footer, ICONS) if check == "PUBLIC_FOOTER_CONTRACT"]
    assert any("link-styled span" in p for p in problems)


def test_the_real_public_shell_passes():
    files, icons = gate.scope()
    assert files, "public_scope is empty"
    findings = []
    for path in files:
        assert path.exists(), f"{path} is named in public_scope but missing"
        findings += [(path.name, *f) for f in gate.scan(path, icons)]
    assert findings == []


def test_the_gate_is_registered_in_verify_as_a_static_zero_gate():
    verify = (Path(__file__).resolve().parents[1] / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert 'Gate("public-shell"' in verify
