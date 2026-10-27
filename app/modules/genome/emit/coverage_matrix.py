"""Deterministic Jinja emitter: coverage slice -> HTML heatmap fragment.

Zero LLM. Given a slice dict from
``app.modules.genome.services.coverage_slice.build_coverage_slice``, renders a
Capability x Application coverage heatmap:

  - rows    = capabilities (business_capability store)
  - columns = applications (application_components store)
  - a shaded cell wherever a mapping exists, shaded by coverage/support strength
  - every populated cell carries its provenance element ids as data-* attributes
    and an accessible tooltip (title), reusing genome_to_bundle's structural
    provenance-stamping pattern (the archimate element id travels with the datum).

The emitter is pure: same slice dict -> byte-identical HTML string. It does not
touch Flask, the DB, or the network. The route embeds this fragment into a
server-rendered page; the fragment is self-contained and re-testable in isolation.
"""
from __future__ import annotations

from markupsafe import escape

# Shade intensity per support level. Values are the alpha applied to the
# heatmap hue in an inline style attribute (CSP allows style-src-attr).
_SUPPORT_ALPHA = {
    "full": 0.85,
    "partial": 0.55,
    "minimal": 0.30,
    "none": 0.12,
    "gap": 0.12,
}
_DEFAULT_ALPHA = 0.40

# A single fixed hue for the heatmap so output is deterministic and theme-neutral.
# (Emitter output uses inline style, not Tailwind classes, so no CSS rebuild is
# required and the design-tokens gate — which scans templates — is unaffected.)
_HEAT_RGB = "37, 99, 235"  # a blue heat ramp; alpha encodes strength


def _cell_alpha(cell: dict) -> float:
    support = (cell.get("mapping") or {}).get("support_level")
    if support in _SUPPORT_ALPHA:
        return _SUPPORT_ALPHA[support]
    coverage = (cell.get("mapping") or {}).get("coverage_percentage")
    if isinstance(coverage, (int, float)) and coverage > 0:
        return max(0.12, min(0.85, coverage / 100.0))
    return _DEFAULT_ALPHA


def emit_coverage_matrix_html(slice_dict: dict, planned_work: dict | None = None) -> str:
    """Render the coverage slice to a deterministic HTML heatmap fragment.

    Args:
        slice_dict: output of build_coverage_slice (must contain capabilities,
            applications, cells, capability_source, spec_hash).
        planned_work: optional ``{capability id: [{"name", "href"}]}``, the work packages that change each
            capability, read by the caller through the one slice registry. When given, each row ends with
            what is planned for it (or says none is), so a capability no application supports shows what, if
            anything, will close the gap.

    Returns:
        An HTML string (table with provenance-stamped cells). Deterministic:
        identical slice -> identical bytes.
    """
    capabilities = slice_dict.get("capabilities", [])
    applications = slice_dict.get("applications", [])
    cells = slice_dict.get("cells", [])
    source = slice_dict.get("capability_source", "unknown")
    spec_hash = slice_dict.get("spec_hash", "")

    # Index cells by (capability_id, application_id) for O(1) lookup.
    cell_index = {(c["capability_id"], c["application_id"]): c for c in cells}

    populated_caps = {c["capability_id"] for c in cells}
    populated_apps = {c["application_id"] for c in cells}

    parts = []
    parts.append(
        f'<div class="genome-coverage-matrix" '
        f'data-spec-hash="{escape(spec_hash)}" '
        f'data-capability-source="{escape(source)}" '
        f'data-capability-count="{len(capabilities)}" '  # raw-html-ok: len(...) is always an int
        f'data-application-count="{len(applications)}" '  # raw-html-ok: len(...) is always an int
        f'data-cell-count="{len(cells)}">'  # raw-html-ok: len(...) is always an int
    )

    # Legend / provenance banner.
    parts.append(
        '<div class="mb-4 text-sm text-muted-foreground">'
        f'{len(capabilities)} {"capability" if len(capabilities) == 1 else "capabilities"} '
        f'and {len(applications)} {"application" if len(applications) == 1 else "applications"}, '
        f'with {len(cells)} {"link" if len(cells) == 1 else "links"} between them.'
        '</div>'
    )

    if not capabilities or not applications:
        parts.append(
            '<div class="p-6 text-center text-muted-foreground border border-border '
            'rounded-lg">No business-layer capability or application data for this '
            'organization.</div>'
        )
        parts.append('</div>')
        return "".join(parts)

    parts.append(
        '<div class="max-w-full overflow-auto border border-border rounded-lg" data-coverage-scroll '
        'tabindex="0" role="region" aria-label="Capability coverage matrix">'
    )
    parts.append('<table class="min-w-max border-collapse text-xs">')

    # Header row: application names as columns.
    parts.append('<thead><tr>')
    parts.append(
        '<th class="bg-card p-2 text-left font-medium min-w-32 '
        'text-foreground border-b border-r border-border">Capability \\ Application</th>'
    )
    for app in applications:
        app_populated = app["id"] in populated_apps
        cls = "text-foreground" if app_populated else "text-muted-foreground"
        parts.append(
            '<th class="p-2 border-b border-border align-bottom" '
            f'data-application-id="{app["id"]}" '  # raw-html-ok: app["id"] is a db.Integer PK
            f'data-archimate-element-id="{escape(str(app.get("archimate_element_id")))}">'
            f'<div class="{cls}" style="writing-mode: vertical-rl; transform: rotate(180deg); '  # raw-html-ok: cls is one of two fixed CSS-class literals from a ternary above
            'white-space: nowrap; max-height: 12rem;">'
            f'{escape(app["name"])}</div></th>'
        )
    if planned_work is not None:
        parts.append(
            '<th class="min-w-32 p-2 border-b border-l border-border text-left font-medium text-foreground" '
            'scope="col">Planned work</th>'
        )
    parts.append('</tr></thead>')

    # Body: one row per capability.
    parts.append('<tbody>')
    for cap in capabilities:
        cap_populated = cap["id"] in populated_caps
        name_cls = "text-foreground" if cap_populated else "text-muted-foreground"
        parts.append('<tr>')
        parts.append(
            '<th class="bg-card p-2 text-left font-normal min-w-32 whitespace-normal '
            f'border-r border-b border-border {name_cls}" '
            f'id="capability-{cap["id"]}" '  # raw-html-ok: cap["id"] is a db.Integer PK
            f'data-capability-id="{cap["id"]}" '  # raw-html-ok: cap["id"] is a db.Integer PK
            f'data-archimate-element-id="{escape(str(cap.get("archimate_element_id")))}" '
            'scope="row">'
            f'{escape(cap["name"])}</th>'
        )
        for app in applications:
            cell = cell_index.get((cap["id"], app["id"]))
            if cell is None:
                parts.append(
                    '<td class="border-b border-border w-6 h-6" '
                    'aria-label="no mapping"></td>'
                )
                continue
            prov = cell["provenance"]
            m = cell["mapping"]
            alpha = _cell_alpha(cell)
            title = (
                f'{cap["name"]} -> {app["name"]} | '
                f'support={m.get("support_level")}, coverage={m.get("coverage_percentage")}% | '
                f'cap element #{prov["capability_archimate_element_id"]}, '
                f'app element #{prov["application_archimate_element_id"]}'
            )
            parts.append(
                '<td class="border-b border-border w-6 h-6 genome-cov-cell" '
                f'style="background-color: rgba({_HEAT_RGB}, {alpha:.2f});" '
                f'data-capability-id="{cap["id"]}" '  # raw-html-ok: cap["id"] is a db.Integer PK
                f'data-application-id="{app["id"]}" '  # raw-html-ok: app["id"] is a db.Integer PK
                f'data-cap-element-id="{prov["capability_archimate_element_id"]}" '  # raw-html-ok: capability_archimate_element_id is a db.Integer FK
                f'data-app-element-id="{prov["application_archimate_element_id"]}" '  # raw-html-ok: application_archimate_element_id is a db.Integer FK
                f'data-support-level="{escape(str(m.get("support_level")))}" '
                f'data-coverage="{escape(str(m.get("coverage_percentage")))}" '
                f'title="{escape(title)}" '
                f'aria-label="{escape(title)}"></td>'
            )
        if planned_work is not None:
            work = planned_work.get(cap["id"], [])
            parts.append(
                '<td class="min-w-32 p-2 border-b border-l border-border align-top text-xs" '
                f'data-planned-work-for="{cap["id"]}">'  # raw-html-ok: cap["id"] is a db.Integer PK
            )
            if work:
                parts.append("<br>".join(
                    f'<a href="{escape(w["href"])}" class="text-primary hover:underline">{escape(w["name"])}</a>'
                    for w in work if w.get("href")
                ))
            else:
                parts.append('<span class="text-muted-foreground">None planned</span>')
            parts.append('</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    parts.append('</div>')
    return "".join(parts)
