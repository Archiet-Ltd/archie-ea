"""Deterministic Jinja-free emitter: drift report dict -> HTML dashboard fragment.

Zero LLM. Given a report dict from
``app.modules.genome.services.drift_detector.detect_model_drift``, renders a
model-health / drift dashboard:

  * a header banner with the spec_hash and per-severity counts;
  * findings grouped by type, each carrying its provenance element id(s) as
    ``data-element-id`` (and ``data-archimate-element-id``) so the UI can link
    straight to the element(s) the finding is about;
  * an explicit "not computed" panel naming signals the schema cannot support
    (e.g. model-age staleness), so the omission is visible, never silent.

Pure: same report dict -> byte-identical HTML string. No Flask, DB or network.
CSP-safe (no inline event handlers; only ``style`` attributes, which
style-src-attr permits — matching the coverage_matrix emitter's approach).
"""
from __future__ import annotations

from typing import Any, Dict

from markupsafe import escape

# Fixed severity ordering + a theme-neutral accent alpha (inline style only, so
# no Tailwind rebuild and the design-tokens gate is unaffected).
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_ALPHA = {"high": 0.85, "medium": 0.5, "low": 0.28}
_ACCENT_RGB = "220, 38, 38"  # a red drift accent; alpha encodes severity

# Emitted verbatim where the CSRF hidden input belongs. The route replaces it
# with a real token after emit, keeping the pure emitter deterministic/testable.
DRIFT_CSRF_PLACEHOLDER = "<!--DRIFT_CSRF-->"

_TYPE_LABELS = {
    "orphaned_element": "Orphaned elements",
    "capability_without_application_support": "Capabilities with no application support",
    "decommissioned_application_still_mapped": "Retiring applications still mapped as support",
    "motivation_without_realization": "Motivation elements with no realization",
    "near_duplicate_elements": "Near-duplicate element clusters",
}


def emit_drift_report_html(report: Dict[str, Any]) -> str:
    """Render the drift report to a deterministic HTML dashboard fragment.

    Args:
        report: output of detect_model_drift (must contain findings, summary,
            spec_hash, signals_scanned, uncomputable_signals).

    Returns:
        An HTML string. Deterministic: identical report -> identical bytes.
    """
    findings = report.get("findings", [])
    summary = report.get("summary", {})
    spec_hash = report.get("spec_hash", "")
    by_severity = summary.get("by_severity", {})
    total = summary.get("total", 0)

    parts = []
    parts.append(
        f'<div class="genome-drift-report" '
        f'data-spec-hash="{escape(spec_hash)}" '
        f'data-finding-count="{total}">'  # raw-html-ok: total is summary.get('total', 0), always an int
    )

    # --- Header banner -----------------------------------------------------
    sev_bits = " &middot; ".join(
        f'<span class="font-medium text-foreground">{by_severity.get(s, 0)}</span> {s}'  # raw-html-ok: by_severity.get(s, 0) is always an int; s is one of a fixed 3-item literal tuple
        for s in ("high", "medium", "low")
    )
    parts.append(
        '<div class="mb-4 text-sm text-muted-foreground">'
        f'<span class="font-medium text-foreground">{total}</span> drift findings '  # raw-html-ok: total is summary.get('total', 0), always an int
        f'&middot; {sev_bits} '
        f'&middot; <span class="font-mono text-xs">{escape(spec_hash)}</span>'
        '</div>'
    )

    if total == 0:
        parts.append(
            '<div class="p-6 text-center text-muted-foreground border border-border '
            'rounded-lg">No drift detected against the computable signals for this '
            'organization. The model matches reality on every signal scanned.</div>'
        )

    # --- Findings grouped by type -----------------------------------------
    grouped: Dict[str, list] = {}
    for f in findings:
        grouped.setdefault(f["type"], []).append(f)

    # Preserve the detector's signal order via the summary's declared order.
    for ftype in report.get("signals_scanned", list(grouped.keys())):
        group = grouped.get(ftype)
        if not group:
            continue
        label = _TYPE_LABELS.get(ftype, ftype)
        parts.append(
            f'<section class="mb-6 genome-drift-group" data-finding-type="{escape(ftype)}">'
        )
        parts.append(
            f'<h3 class="text-sm font-semibold text-foreground mb-2">'
            f'{escape(label)} '
            f'<span class="text-muted-foreground font-normal">({len(group)})</span></h3>'
        )
        parts.append('<ul class="space-y-2">')
        for f in group:
            parts.append(_render_finding(f))
        parts.append('</ul>')
        parts.append('</section>')

    # --- Not-computed panel (honest omissions) -----------------------------
    uncomputable = report.get("uncomputable_signals", {})
    if uncomputable:
        parts.append(
            '<section class="mt-6 pt-4 border-t border-border genome-drift-uncomputable">'
        )
        parts.append(
            '<h3 class="text-xs font-semibold uppercase tracking-wide '
            'text-muted-foreground mb-2">Not computed (no data source)</h3>'
        )
        parts.append('<ul class="space-y-1 text-xs text-muted-foreground">')
        for key in sorted(uncomputable):
            parts.append(
                f'<li data-uncomputable-signal="{escape(key)}">'
                f'<span class="font-mono">{escape(key)}</span> — {escape(uncomputable[key])}</li>'
            )
        parts.append('</ul></section>')

    parts.append('</div>')
    return "".join(parts)


def _render_finding(f: Dict[str, Any]) -> str:
    """Render one finding as a provenance-stamped list item."""
    severity = f.get("severity", "low")
    alpha = _SEVERITY_ALPHA.get(severity, 0.28)
    elements = f.get("elements", [])
    why = f.get("why", "")
    remediation = f.get("remediation", {}) or {}

    primary_id = None
    if elements:
        primary_id = elements[0].get("archimate_element_id")

    # data-element-id on the item carries the primary provenance element.
    item = [
        '<li class="p-3 border border-border rounded-lg bg-card genome-drift-finding" '
        f'data-finding-type="{escape(f.get("type", ""))}" '
        f'data-severity="{escape(severity)}" '
        f'data-element-id="{escape(str(primary_id))}">'
    ]
    # Severity dot (inline style; CSP style-src-attr).
    item.append(
        '<div class="flex items-start gap-2">'
        f'<span class="mt-1 inline-block w-2 h-2 rounded-full shrink-0" '
        f'style="background-color: rgba({_ACCENT_RGB}, {alpha:.2f});" '
        f'role="img" aria-label="{escape(severity)} severity"></span>'
        '<div class="min-w-0">'
    )
    item.append(f'<p class="text-sm text-foreground">{escape(why)}</p>')

    # Provenance chips — each names its element id for click-through.
    if elements:
        item.append('<div class="mt-1 flex flex-wrap gap-1">')
        for el in elements:
            eid = el.get("archimate_element_id")
            name = el.get("name", "?")
            etype = el.get("archimate_type") or "element"
            item.append(
                '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded '
                'bg-muted text-xs text-muted-foreground genome-drift-provenance" '
                f'data-archimate-element-id="{escape(str(eid))}" '
                f'data-element-role="{escape(str(el.get("role", "")))}" '
                f'title="ArchiMate element #{escape(str(eid))}">'
                f'<span class="font-mono">#{escape(str(eid))}</span> '
                f'{escape(etype)}: {escape(str(name))}</span>'
            )
        item.append('</div>')

    # Governed-remediation control, only when a single-element patch can fix it.
    if remediation.get("available") and remediation.get("target_element_id") is not None:
        # The action is a stable relative path (deterministic — no url_for in the
        # pure emitter). CSRF cannot be deterministic, so a placeholder is emitted
        # and the route substitutes the real hidden input via one str.replace.
        item.append(
            '<form method="post" class="mt-2 genome-drift-remediate" '
            'action="/genome/model-health/remediate">'
            + DRIFT_CSRF_PLACEHOLDER
            + f'<input type="hidden" name="finding_type" value="{escape(f.get("type", ""))}">'
            f'<input type="hidden" name="element_id" '
            f'value="{escape(str(remediation.get("target_element_id")))}">'
            '<button type="submit" '
            'class="inline-flex items-center gap-1 px-3 py-1 text-xs rounded-md '
            'bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">'
            'Propose governed fix</button>'
            '<span class="ml-2 text-xs text-muted-foreground">'
            'Queues for human approval — nothing is applied.</span>'
            '</form>'
        )
    elif remediation.get("hint"):
        item.append(
            f'<p class="mt-1 text-xs text-muted-foreground">'
            f'{escape(remediation.get("hint"))}</p>'
        )

    item.append('</div></div>')
    item.append('</li>')
    return "".join(item)


__all__ = ["emit_drift_report_html", "DRIFT_CSRF_PLACEHOLDER"]
