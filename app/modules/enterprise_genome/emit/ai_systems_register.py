"""0-LLM emitter: the AI Systems Register matrix (PILLAR 6).

Turns a slice from ``build_ai_systems_slice`` into a deterministic, CSP-safe
HTML table fragment. No LLM, no randomness, no clock — the same slice emits the
same bytes. Every cell is produced from recorded data or the honest literal
``—`` (em dash) for ``"unknown"``; nothing is invented.

Each row carries ``data-element-id`` (structural provenance back to the
ArchiMateElement) and ``data-currency`` / ``data-flagged`` so the row's risk
state is machine-readable. Risk rows (retired model, ungoverned high autonomy,
regulated data without human review) are marked ``data-flagged="1"`` and list
their flags.

CSP-safe: emits plain markup only — no inline ``<script>``, no ``on*=`` handler,
no ``style=`` attribute. All text is HTML-escaped via ``markupsafe.escape``.
"""

from __future__ import annotations

from markupsafe import Markup, escape

from app.modules.enterprise_genome.services.ai_system_classification import (
    REFUSAL_MISSING_ASSESSMENT,
)

EM_DASH = "—"

# Human labels for the machine enum values. A value the profile could not
# resolve is "unknown" -> em dash, never a made-up label.
_CURRENCY_LABEL = {
    "current": "Current",
    "stale": "Stale",
    "retired": "Retired",
    "unknown": EM_DASH,
}
_FLAG_LABEL = {
    "retired-model": "Retired model",
    "ungoverned-high-autonomy": "Ungoverned high autonomy",
    "regulated-no-human-review": "Regulated data, no human review",
}


def _cell(value) -> str:
    """Escape a scalar for a table cell; 'unknown'/empty -> em dash."""
    if value is None:
        return EM_DASH
    s = str(value).strip()
    if not s or s == "unknown":
        return EM_DASH
    return str(escape(s))


def _tri_cell(value) -> str:
    """Render a tri-state governance flag: Yes / No / em dash (unrecorded)."""
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return EM_DASH


def _flags_cell(flags: list[str]) -> str:
    if not flags:
        return EM_DASH
    labels = [str(escape(_FLAG_LABEL.get(f, f))) for f in flags]
    return " · ".join(labels)


def _owner_cell(owner, element_id=None, can_write=False) -> str:
    """Render an owner dict as a display name, or em dash if unset, with a
    debounced user picker to assign (or change) the owner. The picker renders
    only for a viewer the owner route would admit (``can_write`` — the route
    needs general permission) and only inside a request, where the page
    script that drives it is loaded."""
    name = EM_DASH if owner is None else (owner.get("user_name") or EM_DASH)
    parts = [str(escape(name))]
    if element_id is not None and can_write:
        from flask import has_request_context, url_for

        if has_request_context():
            url = url_for("ai_systems_genome.ai_system_set_owner", element_id=element_id)
            # raw-html-ok: the owners URL is escaped; every other fragment is a fixed literal
            parts.append(
                '<div class="relative mt-1" data-owner-picker data-owners-url="'
                + str(escape(url)) + '">'
                '<input type="text" data-owner-search autocomplete="off" '
                'aria-label="Search for an owner" placeholder="Assign owner…" '
                'class="h-7 w-40 rounded border border-border bg-card px-2 text-xs">'
                '<button type="button" data-owner-save disabled '
                'class="ml-1 h-7 rounded-md border border-border bg-card px-2 text-xs '
                'font-medium text-foreground hover:bg-accent disabled:opacity-50">Set</button>'
                '<div data-owner-results role="listbox" '
                'class="hidden absolute z-10 mt-1 w-64 rounded-md border border-border '
                'bg-card shadow-lg max-h-48 overflow-y-auto"></div></div>'
            )
    return "".join(parts)


def _action_form(element_id: int, endpoint: str, label: str, enabled: bool = True) -> str:
    """A small CSP-safe POST form: hidden csrf token + labelled button.

    Empty string outside a request context (no CSRF token to mint), so the
    deterministic emitter stays byte-stable in unit tests that never render a
    live page; a real register render carries the live forms. Also empty when
    *enabled* is false: a control whose route would refuse the viewer is not
    drawn at all.
    """
    if not enabled:
        return ""
    from flask import has_request_context, url_for
    from flask_wtf.csrf import generate_csrf

    if not has_request_context():
        return ""
    action = url_for(endpoint, element_id=element_id)
    token = generate_csrf()
    # raw-html-ok: action and token are escaped below; label is a fixed
    # literal from this module, never user text.
    return (
        '<form method="POST" action="' + str(escape(action)) + '" class="inline-block">'
        '<input type="hidden" name="csrf_token" value="' + str(escape(token)) + '">'
        '<button type="submit" '
        'class="h-7 rounded-md border border-border bg-card px-3 text-xs font-medium '
        'text-foreground hover:bg-accent">' + str(escape(label)) + "</button></form>"
    )


def _release_cell(system: dict, can_release: bool = False, can_write: bool = False) -> str:
    """The Release state cell: the state, the gate refusal reason when release
    is blocked, and the admin actions (Release / Attach assessment / Classify).

    Each control renders only for a viewer whose role the underlying route
    admits: Release for an administrator (``can_release``), Attach assessment
    for general permission (``can_write``). A viewer who would be refused 403
    is not shown the button.

    CSP-safe: plain markup only, all text escaped, no inline handlers. The
    action forms render only inside a request (where a CSRF token exists);
    outside one, only the text is produced.
    """
    state = system.get("release_state")
    reason = system.get("release_blocked_reason")
    element_id = system.get("archimate_element_id")

    parts = [_cell(state)]
    if reason:
        parts.append(
            f'<p class="text-xs text-destructive mt-1">{_cell(reason)}</p>'  # raw-html-ok: _cell() escapes internally
        )
        if element_id is not None and reason == REFUSAL_MISSING_ASSESSMENT:
            attach = _action_form(
                element_id,
                "ai_systems_genome.ai_system_attach_assessment",
                "Attach assessment",
                enabled=can_write,
            )
            if attach:
                parts.append(f'<div class="mt-1">{attach}</div>')  # raw-html-ok: _action_form() escapes its action URL, csrf token and label
    elif state != "released" and element_id is not None:
        release = _action_form(
            element_id,
            "ai_systems_genome.ai_system_release",
            "Release",
            enabled=can_release,
        )
        if release:
            parts.append(f'<div class="mt-1">{release}</div>')  # raw-html-ok: _action_form() escapes its action URL, csrf token and label

    return "".join(parts)


def _classify_cell(system: dict, can_write: bool = False) -> str:
    """The Risk class cell: the current class and a classify form to propose
    a new (or first) classification. The form renders only inside a request
    context and only for a viewer the classify route would admit
    (``can_write`` — general permission)."""
    risk_class = system.get("risk_class")
    element_id = system.get("archimate_element_id")

    parts = [_cell(risk_class)]
    if element_id is not None and can_write:
        from flask import has_request_context, url_for
        from flask_wtf.csrf import generate_csrf

        if has_request_context():
            action = url_for("ai_systems_genome.ai_system_classify", element_id=element_id)
            token = generate_csrf()
            parts.append(
                '<form method="POST" action="' + str(escape(action)) + '" class="inline-block mt-1">'
                '<input type="hidden" name="csrf_token" value="' + str(escape(token)) + '">'
                '<select name="risk_class" aria-label="Risk class" '
                'class="h-7 rounded border border-border bg-card px-2 text-xs">'
                '<option value="high">High</option>'
                '<option value="limited">Limited</option>'
                '<option value="minimal">Minimal</option>'
                '<option value="unacceptable">Unacceptable</option>'
                '</select>'
                '<button type="submit" class="h-7 rounded-md border border-border bg-card px-2 '
                'text-xs font-medium text-foreground hover:bg-accent ml-1">Classify</button>'
                '</form>'
            )

    return "".join(parts)


def _lineage_panel(system: dict) -> str:
    """Render a lineage panel for one system: providers, data entities, and
    systems using the same provider with owner and data.

    Unrecorded facts render as 'not recorded' with the reason the service
    gives. CSP-safe: all text escaped.
    """
    providers = system.get("providers") or []
    data_entities = system.get("data_entities") or []
    provider_systems = system.get("provider_systems") or {}

    parts = ['<div class="text-xs text-muted-foreground space-y-1" data-testid="lineage-panel">']

    # Providers
    if providers:
        prov_items = "".join(
            '<span class="font-medium">' + str(escape(p.get("name", "not recorded"))) + "</span>"
            + (
                ""
                if p.get("registered", True)
                else ' <span class="text-muted-foreground">(not in the provider register)</span>'
            )
            + (
                ' <span class="text-destructive">(blocked by the provider register)</span>'
                if p.get("allowed") is False
                else ""
            )
            for p in providers
        )
        parts.append(
            '<div><span class="font-semibold">Providers:</span> '
            + prov_items
            + "</div>"
        )
    else:
        parts.append(
            '<div><span class="font-semibold">Providers:</span> '
            '<span class="text-muted-foreground">not recorded — no provider recorded on the system profile</span></div>'
        )

    # Data entities
    if data_entities:
        de_items = "".join(
            '<span class="font-medium">' + str(escape(d.get("name", "not recorded"))) + "</span>"
            for d in data_entities
        )
        parts.append(
            '<div><span class="font-semibold">Data entities:</span> '
            + de_items
            + "</div>"
        )
    else:
        parts.append(
            '<div><span class="font-semibold">Data entities:</span> '
            '<span class="text-muted-foreground">not recorded — no data entities linked</span></div>'
        )

    # Systems using the same provider
    if provider_systems:
        for systems_list in provider_systems.values():
            if not systems_list:
                continue
            sys_items = []
            for sys_entry in systems_list:
                owner = sys_entry.get("owner")
                owner_name = "not recorded — no owner assigned"
                if owner and owner.get("user_name"):
                    owner_name = owner["user_name"]
                data_names = [d.get("name", "not recorded") for d in sys_entry.get("data_entities", [])]
                data_str = (
                    ", ".join(data_names)
                    if data_names
                    else "not recorded — no data entities linked"
                )
                sys_items.append(
                    str(escape(sys_entry.get("name", "not recorded")))
                    + " (owner: " + str(escape(owner_name))
                    + ", data: " + str(escape(data_str)) + ")"
                )
            if sys_items:
                parts.append(
                    '<div><span class="font-semibold">Systems using provider:</span> '
                    + "; ".join(sys_items)
                    + "</div>"
                )

    parts.append("</div>")
    return "".join(parts)


def _obligations_panel(system: dict) -> str:
    """Render an obligations panel for one system: title and evidence status.

    Unrecorded facts render as 'not recorded' with the reason the service
    gives. CSP-safe: all text escaped.
    """
    obligations = system.get("obligations") or []

    parts = ['<div class="text-xs text-muted-foreground space-y-1" data-testid="obligations-panel">']

    if obligations:
        parts.append('<div class="font-semibold">Obligations:</div>')
        for ob in obligations:
            title = ob.get("title", "not recorded")
            evidence_status = ob.get("evidence_status", "not recorded")
            reason = ob.get("evidence_status_reason")
            badge_text = evidence_status + (f" — {reason}" if reason else "")
            location = ob.get("evidence_location") or "not recorded — no evidence location"
            verified = ob.get("last_verified_at") or "not recorded — never verified"
            parts.append(
                '<div class="flex items-center gap-2">'
                '<span class="font-medium">' + str(escape(title)) + "</span>"
                '<span class="text-xs px-2 py-0.5 rounded '
                + _status_badge_class(evidence_status)
                + '">' + str(escape(badge_text)) + "</span>"
                '<span>Evidence: ' + str(escape(location)) + "</span>"
                '<span>Last verified: ' + str(escape(verified)) + "</span>"
                "</div>"
            )
    else:
        parts.append(
            '<div><span class="font-semibold">Obligations:</span> '
            '<span class="text-muted-foreground">not recorded — no compliance requirements attached</span></div>'
        )

    parts.append("</div>")
    return "".join(parts)


def _status_badge_class(status: str) -> str:
    """CSS class for an evidence-status badge."""
    mapping = {
        "not_started": "bg-yellow-100 text-yellow-800",
        "in_progress": "bg-blue-100 text-blue-800",
        "completed": "bg-green-100 text-green-800",
        "verified": "bg-green-200 text-green-900",
        "not recorded": "bg-gray-100 text-gray-500",
    }
    return mapping.get(status, "bg-gray-100 text-gray-500")


def emit_ai_systems_register(
    slice_data: dict,
    *,
    can_release: bool = False,
    can_write: bool = False,
) -> Markup:
    """Emit the register table for a slice.

    ``can_release`` (administrator of the organisation) decides whether the
    Release form renders; ``can_write`` (general permission) decides whether
    the Classify, Attach-assessment and owner-assignment controls render —
    exactly the roles the corresponding routes admit. Both default to false so
    a caller that does not pass the viewer's permissions emits the read-only
    register. Returns a ``Markup`` fragment.
    """
    systems = slice_data.get("systems") or []
    counts = slice_data.get("counts") or {}
    spec_hash = slice_data.get("spec_hash") or ""

    rows: list[str] = []
    for s in systems:
        flags = s.get("risk_flags") or []
        flagged = "1" if flags else "0"
        currency = s.get("model_currency", "unknown")
        currency_label = _CURRENCY_LABEL.get(currency, EM_DASH)
        gov = s.get("governance") or {}

        rows.append(
            "<tr class=\"border-b border-border\" "
            f'data-element-id="{int(s["archimate_element_id"])}" '  # raw-html-ok: int(...) is always an int
            f'data-currency="{escape(currency)}" '
            f'data-flagged="{flagged}">'  # raw-html-ok: flagged is a literal '1'/'0' string, never free text
            f'<td class="px-3 py-2 text-sm font-medium">{_cell(s.get("name"))}</td>'  # raw-html-ok: _cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_cell(s.get("provider"))}</td>'  # raw-html-ok: _cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm font-mono">{_cell(s.get("model_id"))}</td>'  # raw-html-ok: _cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm" data-currency-cell="{escape(currency)}">{escape(currency_label)}</td>'
            f'<td class="px-3 py-2 text-sm">{_cell(s.get("autonomy_level"))}</td>'  # raw-html-ok: _cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_tri_cell(gov.get("approval_gate"))}</td>'  # raw-html-ok: _tri_cell() (defined above) returns only 'Yes'/'No'/em-dash literals
            f'<td class="px-3 py-2 text-sm">{_tri_cell(gov.get("human_review"))}</td>'  # raw-html-ok: _tri_cell() (defined above) returns only 'Yes'/'No'/em-dash literals
            f'<td class="px-3 py-2 text-sm">{_cell(s.get("data_sensitivity"))}</td>'  # raw-html-ok: _cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_classify_cell(s, can_write=can_write)}</td>'  # raw-html-ok: _classify_cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_release_cell(s, can_release=can_release, can_write=can_write)}</td>'  # raw-html-ok: _release_cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_owner_cell(s.get("owner"), s.get("archimate_element_id"), can_write=can_write)}</td>'  # raw-html-ok: _owner_cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm text-destructive">{_flags_cell(flags)}</td>'  # raw-html-ok: _flags_cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_lineage_panel(s)}</td>'  # raw-html-ok: _lineage_panel() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_obligations_panel(s)}</td>'  # raw-html-ok: _obligations_panel() (defined above) escapes internally
            "</tr>"
        )

    if rows:
        body = "".join(rows)
    else:
        body = (
            '<tr><td colspan="14" class="px-3 py-6 text-sm text-muted-foreground text-center">'
            "No AI systems modelled yet.</td></tr>"
        )

    header_cells = [
        "AI system",
        "Provider",
        "Model",
        "Model currency",
        "Autonomy",
        "Approval gate",
        "Human review",
        "Data sensitivity",
        "Risk class",
        "Release state",
        "Owner",
        "Risk flags",
        "Lineage",
        "Obligations",
    ]
    head = "".join(
        f'<th class="px-3 py-2 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">{escape(h)}</th>'
        for h in header_cells
    )

    summary = (
        '<div class="text-xs text-muted-foreground" data-testid="ai-systems-summary">'
        f'Total {int(counts.get("total", 0))} · '
        f'retired {int(counts.get("retired", 0))} · '
        f'stale {int(counts.get("stale", 0))} · '
        f'current {int(counts.get("current", 0))} · '
        f'flagged {int(counts.get("flagged", 0))}'
        "</div>"
    )
    provenance = (
        f'<div class="text-xs text-muted-foreground font-mono" data-spec-hash="{escape(spec_hash)}">'
        f"spec: {escape(spec_hash) or EM_DASH}</div>"
    )

    html = (
        '<div class="space-y-3" data-testid="ai-systems-register">'
        f'<div class="flex items-center justify-between">{summary}{provenance}</div>'
        '<div class="overflow-x-auto rounded-lg border border-border" tabindex="0" role="region" aria-label="AI systems register">'
        '<table class="min-w-full divide-y divide-border">'
        f"<thead><tr>{head}</tr></thead>"  # raw-html-ok: head is built entirely from escape()'d header labels above
        f"<tbody>{body}</tbody>"  # raw-html-ok: body is built entirely from _cell()/_tri_cell()/_flags_cell()-escaped rows above
        "</table></div></div>"
    )
    # Every database-derived string is escaped above and identifiers/counts are
    # integer-coerced before this trusted fragment reaches the outer template.
    return Markup(html)  # nosec B704
