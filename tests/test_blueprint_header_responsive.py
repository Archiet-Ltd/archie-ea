"""Real blueprint header, shipped CSS and Alpine in a constrained document column."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LONG_TITLE = "Legacy CRM Retirement (Lotus Notes → Sales Cloud)"


def header_html():
    source = (ROOT / "app/templates/solutions/blueprint.html").read_text(encoding="utf-8")
    fragment = "{% macro _bp_meta() %}" + source.split("{% macro _bp_meta() %}", 1)[1].split("{# Completeness strip #}", 1)[0]
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True)
    env.globals["url_for"] = lambda endpoint, **kw: "/solutions/32/codegen" if endpoint == "codegen.workbench_page" else "/solutions/32"
    # The signed-in person may open the workbench: the header shows the link, not the reason it is closed.
    env.globals["can_open_code_workbench"] = lambda solution: True
    rendered = env.from_string("{% from 'macros/page_shell.html' import page_shell %}" + fragment).render(
        solution=SimpleNamespace(id=32, name=LONG_TITLE, status="draft", blueprint_version=1, solution_type="Transformation", updated_at=datetime(2026, 9, 5)),
        flask=SimpleNamespace(current_app=SimpleNamespace(view_functions={})),
    )
    return '<div style="margin-left:256px;margin-right:15.333px;padding:24px"><div class="flex gap-6 items-start"><nav class="hidden xl:block w-52 shrink-0">Outline</nav><main class="flex-1 min-w-0">' + rendered + '</main><aside class="hidden lg:block w-[280px] shrink-0 bg-card" style="height:600px;position:relative">Governance</aside></div></div>'


@pytest.mark.parametrize("width", [1280, 1100])
def test_long_title_header_actions_are_clickable_without_overlay(width):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 631})
            page.route("http://blueprint.test/solutions/32/codegen", lambda route: route.fulfill(body="<h1>Code Workbench</h1>", content_type="text/html"))
            page.route("http://blueprint.test/", lambda route: route.fulfill(body=header_html(), content_type="text/html"))
            page.goto("http://blueprint.test/")
            page.add_style_tag(path=str(ROOT / "app/static/css/tailwind-output.css"))
            page.route("**/inter-*.woff2", lambda route: route.fulfill(path=str(ROOT / "app/static/vendor" / route.request.url.rsplit("/", 1)[1])))
            page.add_style_tag(path=str(ROOT / "app/static/css/shadcn_tokens.css"))
            page.evaluate("document.fonts.ready")
            page.add_style_tag(content="[x-cloak]{display:none!important}")
            page.add_script_tag(path=str(ROOT / "app/static/vendor/alpine.min.js"))
            button = page.get_by_role("button", name="More actions", exact=True)
            expect(button).to_have_attribute("aria-expanded", "false")
            assert page.locator("main").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            assert button.evaluate("el => {const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}")
            button.click(timeout=3000)
            expect(page.get_by_role("menuitem", name="Code Workbench", exact=True)).to_be_visible()
            with page.expect_navigation():
                page.get_by_role("menuitem", name="Code Workbench", exact=True).click(timeout=3000)
            expect(page.get_by_role("heading", name="Code Workbench", exact=True)).to_be_visible()
        finally:
            browser.close()
