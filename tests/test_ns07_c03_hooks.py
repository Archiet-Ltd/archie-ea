"""The remaining pages converted to the canonical macros keep their ids, bindings and hooks.

Group C-03 of the tier 1 conformance burn-down: risk register, vendors, traceability,
capability roadmap, login and the dashboard overview. The risk register is rendered;
for the other pages the template source is read, because they need a full request
context, and each handler, binding and hook the conversion touched must still be there
(through the button macro the attributes are written by the macro, so the check is on
the call arguments).
"""
from pathlib import Path

from bs4 import BeautifulSoup
from flask import render_template

ROOT = Path(__file__).resolve().parent.parent / "app" / "templates"


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _norm(html):
    return html.replace("&#39;", "'").replace("&#34;", '"').replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")


def _render_risk_register(app, db_session, make_org, tenant_ctx):
    org = make_org("ns07-c03")
    with tenant_ctx(org.id), app.test_request_context("/"):
        return _norm(render_template(
            "governance/risk_register.html", risks=[], risk_rows=[], grid=[], total=0,
            current_sort="id", current_dir="asc", raid_items=[], raid_kinds=[], programmes=[],
        ))


def test_risk_register_keeps_hooks(app, db_session, make_org, tenant_ctx):
    html = _render_risk_register(app, db_session, make_org, tenant_ctx)
    # kind filter (button macro inside the x-for)
    assert "@click=\"kindFilter = (kindFilter === k ? '' : k)\"" in html
    assert "x-text=\"k === 'dependency' ? 'Dependencies' : (k + 's')\"" in html
    assert ":class=\"kindFilter === k ?" in html
    # submit and entity buttons are named
    assert "@click=\"submit()\"" in html and ":disabled=\"saving\"" in html
    assert ":aria-label=\"saving ? 'Saving'" in html
    assert "@click=\"addLink(entity)\"" in html and ":aria-label=\"'Map to ' + entity.name\"" in html
    assert "@click=\"removeLink(link.id)\"" in html and 'aria-label="Unmap"' in html
    # edit and delete in the panel
    assert "@click=\"openEdit(selectedRisk)\"" in html
    assert "@click=\"confirmDelete(selectedRisk)\"" in html
    # link pickers are the canonical 36px
    assert 'aria-label="Entity type to link"' in html and 'aria-label="Search entities to link"' in html
    assert "h-8 rounded-md border" not in html


def test_risk_register_panel_is_a_trapped_modal_dialog(app, db_session, make_org, tenant_ctx):
    html = _render_risk_register(app, db_session, make_org, tenant_ctx)
    page = BeautifulSoup(html, "html.parser")
    panel = page.select_one("#risk-detail-panel")
    assert panel is not None
    assert panel.get("role") == "dialog" and panel.get("aria-modal") == "true"
    assert panel.get("aria-label") == "Risk detail"
    assert panel.get("x-show") == "panelOpen"
    assert panel.get("x-trap.inert") == "panelOpen"
    # close button inside the panel
    close = panel.select_one('button[aria-label="Close risk detail"]')
    assert close is not None and close.get("@click") == "panelOpen = false"
    # the scrim still closes on a click and is hidden from assistive technology
    scrim = [d for d in page.select('div[x-show="panelOpen"]') if d is not panel][0]
    assert scrim.get("@click.self") == "panelOpen = false"
    assert scrim.get("aria-hidden") == "true"
    assert scrim.get("@keydown.escape.window") == "panelOpen = false"


def test_vendors_keep_hooks_and_names():
    s = _src("vendors/list.html")
    for needle in (
        '@click="openEditModal(row)"', ":aria-label=\"'Edit ' + row.name\"",
        '@click="saveEdit()"', ':disabled="editSaving"', ":aria-label=\"editSaving ? 'Saving' : 'Save Changes'\"",
        '@click="executeBulkDelete()"', ":disabled=\"deleteConfirmText !== 'DELETE' || deleteInProgress\"",
        ":aria-label=\"deleteInProgress ? 'Deleting' : 'Delete'\"",
        'type="submit" :disabled="submitting"', ":aria-label=\"submitting ? 'Creating vendor' : 'Create Vendor'\"",
        'data-modal-close="edit-vendor-modal"', 'data-modal-close="bulk-delete-confirm-modal"',
        'x-model="deleteConfirmText"', "Platform.modal.close('create-vendor')",
    ):
        assert needle in s, needle


def test_traceability_keeps_hooks():
    s = _src("archimate_views/traceability.html")
    assert "{% from 'components/button.html' import button %}" in s
    assert '@click="gapOpen = !gapOpen"' in s
    assert ":aria-expanded=\"gapOpen.toString()\"" in s
    assert ":aria-label=\"gapOpen ? 'Hide gap panel' : 'Show gap panel'\"" in s
    assert "document.getElementById('sankey-view').style.display = ''" in s
    assert "document.getElementById('matrix-view').style.display = 'none'" in s
    assert "window.createTraceabilitySankey('#sankey-container'" in s
    assert "document.getElementById('matrix-view').style.display = '';" in s
    assert "view === 'sankey'" in s and "view === 'matrix'" in s
    assert 'x-data="{ view: \'matrix\' }"' in s


def test_traceability_toggle_renders_through_the_button_macro(app):
    from flask import render_template_string

    with app.test_request_context("/"):
        html = _norm(render_template_string(
            "{% from 'components/button.html' import button %}"
            "{% call button(variant='ghost', size='sm', class='gap-2', on_click=\"view = 'matrix';\","
            " attrs_dict={':class': \"view === 'matrix' ? '!bg-background' : ''\"}) %}Matrix{% endcall %}"
        ))
    assert "@click=\"view = 'matrix';\"" in html
    assert ":class=\"view === 'matrix' ? '!bg-background' : ''\"" in html
    assert "Matrix" in html and 'type="button"' in html


def test_capability_roadmap_keeps_hooks():
    s = _src("capability_roadmap/capability_roadmap.html")
    assert 'data-testid="wp-modal-gap-input"' in s and 'x-model="gapQuery"' in s
    assert "form.gap_id = null; gapName = ''; gapQuery = ''" in s
    assert "'x-show': 'form.gap_id'" in s and "'x-cloak': ''" in s
    assert "form.gap_id = g.id; gapName = g.name; gapQuery = ''; gapResults = []" in s
    assert "'x-text': 'g.name'" in s
    assert 'x-for="g in gapResults"' in s


def test_overview_and_login_keep_hooks():
    o = _src("dashboards/overview.html")
    assert "on_click='dismiss()'" in o and "aria_label='Dismiss welcome message'" in o
    assert 'data-lucide="x"' in o
    lg = _src("account/login.html")
    assert 'id="login-title"' in lg
    assert "rounded-xl border bg-card" not in lg
