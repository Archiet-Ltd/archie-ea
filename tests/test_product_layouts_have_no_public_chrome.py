"""Pages inside the product never carry the public navbar or footer.

The public navbar and footer belong to entelim.org's public pages. A template
under a product layout that included them would show a visitor's menu inside the
product, so every layout and every product template is scanned.
"""

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
CHROME = ("components/public_navbar.html", "components/public_footer.html")
PUBLIC_PAGES = {
    "main/index.html",
    "public/page.html",
    "public/hub_index.html",
    "public/pricing.html",
    "public/vs_hub.html",
    # what a later release answers while the wave switch is on: public pages by design
    "public/early_access.html",
    "public/early_access_hub.html",
}


def _includes_public_chrome(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return any(name in text for name in CHROME)


def test_no_layout_includes_the_public_navbar_or_footer():
    layouts = sorted((TEMPLATES / "layouts").glob("*.html"))
    assert layouts, "no layouts found"
    offenders = [p.name for p in layouts if p.name != "public_base.html" and _includes_public_chrome(p)]
    assert offenders == []


def test_only_public_pages_include_the_public_navbar_or_footer():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative.startswith("components/public_") or relative in PUBLIC_PAGES:
            continue
        if _includes_public_chrome(path):
            offenders.append(relative)
    assert offenders == []
