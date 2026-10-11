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


def _owner_cell(owner) -> str:
    """Render an owner dict as a display name, or em dash if unset."""
    if owner is None:
        return EM_DASH
    name = owner.get("user_name") or EM_DASH
    return str(escape(name))


def _action_form(element_id: int, endpoint: str, label: str) -> str:
    """A small CSP-safe POST form: hidden csrf token + labelled button.

    Empty string outside a request context (no CSRF token to mint), so the
    deterministic emitter stays byte-stable in unit tests that never render a
    live page; a real register render carries the live forms.
    """
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


def _release_cell(system: dict) -> str:
    """The Release state cell: the state, the gate refusal reason when release
    is blocked, and the admin actions (Release / Attach assessment / Classify).

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
        if element_id is not None:
            attach = _action_form(
                element_id,
                "ai_systems_genome.ai_system_attach_assessment",
                "Attach assessment",
            )
            if attach:
                parts.append(f'<div class="mt-1">{attach}</div>')  # raw-html-ok: _action_form() escapes its action URL, csrf token and label
    elif state != "released" and element_id is not None:
        release = _action_form(
            element_id,
            "ai_systems_genome.ai_system_release",
            "Release",
        )
        if release:
            parts.append(f'<div class="mt-1">{release}</div>')  # raw-html-ok: _action_form() escapes its action URL, csrf token and label

    return "".join(parts)


def _classify_cell(system: dict) -> str:
    """The Risk class cell: the current class and a classify form to propose
    a new classification. Renders only inside a request context."""
    risk_class = system.get("risk_class")
    element_id = system.get("archimate_element_id")

    parts = [_cell(risk_class)]
    if element_id is not None and risk_class != "not set":
        # Show a small form to re-classify.
        from flask import has_request_context, url_for
        from flask_wtf.csrf import generate_csrf

        if has_request_context():
            action = url_for("ai_systems_genome.ai_system_classify", element_id=element_id)
            token = generate_csrf()
            parts.append(
                '<form method="POST" action="' + str(escape(action)) + '" class="inline-block mt-1">'
                '<input type="hidden" name="csrf_token" value="' + str(escape(token)) + '">'
                '<select name="risk_class" class="h-7 rounded border border-border bg-card px-2 text-xs">'
                '<option value="high">High</option>'
                '<option value="limited">Limited</option>'
                '<option value="minimal">Minimal</option>'
                '<option value="unacceptable">Unacceptable</option>'
                '</select>'
                '<button type="submit" class="h-7 rounded-md border border-border bg-card px-2 text-xs font-medium text-foreground hover:bg-accent ml-1">Classify</button>'
                '</form>'
            )
    elif element_id is not None:
        # No classification yet — show a classify form.
        from flask import has_request_context, url_for
        from flask_wtf.csrf import generate_csrf

        if has_request_context():
            action = url_for("ai_systems_genome.ai_system_classify", element_id=element_id)
            token = generate_csrf()
            parts.append(
                '<form method="POST" action="' + str(escape(action)) + '" class="inline-block mt-1">'
                '<input type="hidden" name="csrf_token" value="' + str(escape(token)) + '">'
                '<select name="risk_class" class="h-7 rounded border border-border bg-card px-2 text-xs">'
                '<option value="high">High</option>'
                '<option value="limited">Limited</option>'
                '<option value="minimal">Minimal</option>'
                '<option value="unacceptable">Unacceptable</option>'
                '</select>'
                '<button type="submit" class="h-7 rounded-md border border-border bg-card px-2 text-xs font-medium text-foreground hover:bg-accent ml-1">Classify</button>'
                '</form>'
            )

    return "".join(parts)


def emit_ai_systems_register(slice_data: dict) -> Markup:
    """Emit the register table for a slice. Returns a ``Markup`` fragment."""
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
             f'<td class="px-3 py-2 text-sm">{_classify_cell(s)}</td>'  # raw-html-ok: _classify_cell() (defined above) escapes internally
             f'<td class="px-3 py-2 text-sm">{_release_cell(s)}</td>'  # raw-html-ok: _release_cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm">{_owner_cell(s.get("owner"))}</td>'  # raw-html-ok: _owner_cell() (defined above) escapes internally
            f'<td class="px-3 py-2 text-sm text-destructive">{_flags_cell(flags)}</td>'  # raw-html-ok: _flags_cell() (defined above) escapes internally
            "</tr>"
        )

    if rows:
        body = "".join(rows)
    else:
        body = (
            '<tr><td colspan="12" class="px-3 py-6 text-sm text-muted-foreground text-center">'
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
        '<div class="overflow-x-auto rounded-lg border border-border">'
        '<table class="min-w-full divide-y divide-border">'
        f"<thead><tr>{head}</tr></thead>"  # raw-html-ok: head is built entirely from escape()'d header labels above
        f"<tbody>{body}</tbody>"  # raw-html-ok: body is built entirely from _cell()/_tri_cell()/_flags_cell()-escaped rows above
        "</table></div></div>"
    )
    # Every database-derived string is escaped above and identifiers/counts are
    # integer-coerced before this trusted fragment reaches the outer template.
    return Markup(html)  # nosec B704
