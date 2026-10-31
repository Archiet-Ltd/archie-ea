# DESIGN.md — Entelim Mandatory Design System (`flask-basecoat` + `shadcn/ui`)

> **For AI agents:** Read this file before editing any template, CSS, or front-end JS file.
> Every screen in Entelim MUST be 100% compliant with [`flask-basecoat`](file:///C:/flask-basecoat) (`https://github.com/Anioko/flask-basecoat/`) and [`shadcn/ui`](https://ui.shadcn.com/docs).
> The sources of truth are:
> - `app/static/css/shadcn_tokens.css` (OKLCH design tokens, including `.theme-marketing`)
> - `C:\flask-basecoat\AGENTS.md`, `C:\flask-basecoat\docs\LAYOUT.md`, and `C:\flask-basecoat\docs\MACROS.md`
> - `docs/design-map.json` (page blocks, shell frames, allowed macros, and `data_types`)
> - `docs/data-type-register.yml` (the 12 platform data types and their required canonical components)
> - `docs/reuse-register.yml` (the single home for each component)

---

## 1. Zero-Tolerance Compliance Rules (Enforced by CI)

Every template is checked by `scripts/qa/entelim_shadcn_compliance.py`, `scripts/check_data_types.py`, and `scripts/check_design_map.py`. A page fails if any of these four counters is non-zero (`rc=0, col=0, sh=0, st=0`):

1. **Zero raw HTML controls (`rc=0`)**: Never write raw `<button>`, `<input>`, `<select>`, `<textarea>`, `<table>`, or `<dialog>` in a page template. Use the `basecoat/*.html` macros (`{% import "basecoat/macros.html" as ui %}` or specific `basecoat/*.html` / `components/*.html` macros). Only `<input type="hidden">` and `<input type="file" class="sr-only">` are exempt.
2. **Zero raw Tailwind colours or hex literals (`col=0`)**: Never use `slate-*`, `gray-*`, `zinc-*`, `neutral-*`, `stone-*`, `red-*`, `orange-*`, `amber-*`, `yellow-*`, `lime-*`, `green-*`, `emerald-*`, `teal-*`, `cyan-*`, `sky-*`, `blue-*`, `indigo-*`, `violet-*`, `purple-*`, `fuchsia-*`, `pink-*`, `rose-*`, `bg-white`, `text-white`, `bg-black`, `text-black`, or `#hex` / `rgb(...)` literals. Use semantic tokens (`bg-background`, `text-foreground`, `bg-card`, `text-card-foreground`, `bg-muted`, `text-muted-foreground`, `bg-primary`, `text-primary-foreground`, `bg-secondary`, `text-secondary-foreground`, `bg-accent`, `text-accent-foreground`, `bg-destructive`, `text-destructive`, `border-border`, `border-input`, `ring-ring`, `var(--chart-1)`..`var(--chart-5)`).
3. **Zero unapproved shadows (`sh=0`)**: Only `shadow-none` and `shadow-xs` (inside macros or the KPI card gradient recipe) are permitted. Never use `shadow-sm`, `shadow-md`, `shadow-lg`, `shadow-xl`, `shadow-2xl`, or `drop-shadow-*` in page templates.
4. **Zero arbitrary inline styles (`st=0`)**: Never write `style="..."` on page elements (except dynamic progress widths `style="width: {{ pct }}%"` inside progress bars).

---

## 2. Mandatory Decision Matrix: Block, Layout Recipe, and Data-Type Component

Before creating or modifying any page template, identify its **Block** (`docs/design-map.json`), its **Layout Recipe** (`C:\flask-basecoat\docs\LAYOUT.md`), and the **Data Types** (`docs/data-type-register.yml`) it displays:

| # | Data Type ID (`docs/data-type-register.yml`) | When a Page Has This Data | Canonical Macro / Recipe (Mandatory) | Retired / Forbidden Alternatives |
|---|---|---|---|---|
| 1 | `record-list` | Any list, catalogue, register, queue, log, or inventory of records (applications, solutions, vendors, contracts, decisions, ARB submissions, users, organisations, etc.) | `components/list_table.html` (`list_table(...)`), built on `basecoat/table.html` (`ui.table`, `ui.table_header`, `ui.table_body`, `ui.table_row`, `ui.table_head`, `ui.table_cell`) + `basecoat/pagination.html` (`pagination(...)`). Includes toolbar (`flex items-center justify-between gap-2`: search `ui.input` `h-8 w-[150px] lg:w-[250px]`, optional faceted filters, `View` column visibility `ui.dropdown_menu`), sortable/hideable column headers, cell truncation (`max-w-[500px] truncate font-medium`), and the shadcn Data Table pagination footer (`0 of N row(s) selected`, `Rows per page` `ui.select` `[10, 20, 25, 30, 40, 50]` default `25`, `Page X of Y`, first/prev/next/last `size-8` `ui.button` controls). Reference: `C:\flask-basecoat\flask_basecoat\templates\examples\ported\tasks.html`, `_admin.html`, `portfolio.html`. | Raw `<table>`, `components/dense_table.html`, `components/data_table.html`, `components/workbench_table.html`, `components/pagination_controls.html`, hand-rolled `<nav>` pagers |
| 2 | `archimate-elements` | Any list of ArchiMate elements (across the 51 element types or on a record's tabs) | `components/element_table.html` (`element_table(...)`), wrapping `list_table` with `Open in Composer` (`archimate.composer_page`) in the toolbar and row link to the element detail | Raw `<table>`, `dense_table.html`, list without `Open in Composer` |
| 3 | `headline-figures` | Top-of-page KPI / summary metrics (at most 4 cards per row) | `C:\flask-basecoat\docs\LAYOUT.md` KPI Stat Card Row: `<div class="*:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card grid grid-cols-1 gap-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:shadow-xs md:grid-cols-2 lg:grid-cols-4">` containing up to 4 `basecoat/card.html` (`ui.card`) instances, each with `ui.card_description` label, `text-2xl font-semibold tabular-nums @[250px]/card:text-3xl` value, `ui.card_action` + `ui.badge(variant="outline")` delta, and `ui.card_footer` context line | `components/metrics_card.html`, `components/data_card.html`, `macros/app_stats_grid.html`, 5+ cards in a row |
| 4 | `time-series-and-breakdowns` | Trends over time, category distributions, or primary chart + side breakdown | `C:\flask-basecoat\docs\LAYOUT.md` Chart + Side Panel Grid (`grid gap-4 md:grid-cols-2 lg:grid-cols-7` with `lg:col-span-4` primary chart card + `lg:col-span-3` breakdown card) or Equal 2-Column Grid (`grid gap-4 md:grid-cols-2`). Every chart sits inside `basecoat/card.html` (`ui.card`) and uses `var(--chart-1)` through `var(--chart-5)` | Raw `<canvas>` outside `ui.card`, hard-coded hex colours in Chart.js datasets |
| 5 | `risks` | Risk register items or record-level risks with probability and impact | `components/risk_matrix.html` (`risk_matrix(...)`) 5×5 probability-by-impact grid + `components/list_table.html` (`list_table(...)`) | Risk list without probability×impact matrix, raw `<table>` |
| 6 | `record-facts` | Key-value attributes of a single record (owner, status, dates, cost, tier, lifecycle) | `components/fact_list.html` (`fact_list(...)`) inside `basecoat/card.html` (`ui.card`) (`<dl class="divide-y divide-border text-sm">` with `grid-cols-3` `<dt class="text-muted-foreground">` / `<dd class="col-span-2 font-medium">`) | Ad-hoc `<div>` key-value pairs |
| 7 | `status` | Lifecycle state, approval status, health, severity, or tier label | `basecoat/badge.html` (`ui.badge(label, variant=...)`) with `default`, `secondary`, `outline`, or `destructive` only | `components/status_badge.html`, `components/status_indicator.html`, raw `<span class="rounded-full bg-emerald-...">` |
| 8 | `evidence` | Source attribution, confidence, or provenance on a figure or AI answer | `components/provenance_Tag.html` (`provenance_tag(...)`) + `basecoat/badge.html` (`ui.badge(variant="outline")`) | Unattributed numbers, bespoke source pills |
| 9 | `form-controls` | Create/edit forms, settings pages, account preferences, filter bars | `C:\flask-basecoat\docs\LAYOUT.md` Form Layout (`max-w-2xl space-y-6`), fields wrapped in `ui.field(label=..., for_id=..., description=..., error=...)`, controls via `ui.input`, `ui.select`, `ui.textarea`, `ui.checkbox`, `ui.switch`, `ui.radio_group`, and submit via `ui.button("Save", type="submit")` | Raw `<input>`, `<select>`, `<textarea>`, `<button>`, forms without `type="submit"` |
| 10 | `canvas-9box` | Business Model Canvas, Operating Model Canvas, or 9-box strategic grid | `macros/canvas.html` (`canvas_cell`, `canvas_header`, `bmc_grid`, `omc_grid`) inside `basecoat/card.html` (`ui.card`) + `basecoat/tabs.html` | Ad-hoc CSS grids or duplicate canvas templates |
| 11 | `board-kanban` | Work packages, roadmap stages, or status-column boards | `components/kanban_board.html` (`kanban_board(...)`) built on `basecoat/card.html`, `basecoat/badge.html`, and `basecoat/scroll_area.html` | Bespoke column markup |
| 12 | `diagram-canvas` | Interactive node-edge diagram (ArchiMate Composer, dependency graph, hierarchy tree) | `components/diagram/canvas.html`, `components/diagram/palette.html`, `components/diagram/toolbar.html`, `components/diagram/minimap.html` inside `workspace_shell` | Duplicate SVG/canvas shells |

---

## 3. Canonical Page Blocks & Layout Recipes (`C:\flask-basecoat\docs\LAYOUT.md`)

### 3.1 Base Templates & Page Header

Every signed-in page extends `layouts/admin_base.html` and uses `macros/page_shell.html` for its single `<h1>` header:

```jinja
{% extends "layouts/admin_base.html" %}
{% import "basecoat/macros.html" as ui %}
{% from "macros/page_shell.html" import page_shell %}

{% block content %}
<div class="flex flex-1 flex-col gap-6 p-6">
  {% macro _actions() %}
    {{ ui.button("Add Record", href=url_for("..."), size="sm", icon="plus") }}
  {% endmacro %}
  {{ page_shell(
      title="Page Title",
      subtitle="Concise subtitle explaining the page job.",
      breadcrumb=[("Home", url_for("dashboard.overview")), ("Current", None)],
      actions_caller=_actions
  ) }}

  {# Page body follows one of the LAYOUT.md recipes below #}
</div>
{% endblock %}
```

### 3.2 Recipe A — Dashboard (`block: dashboard`, `data_types: [headline-figures, time-series-and-breakdowns]`)

> **Golden Reference Protection (`/dashboard/overview`):** `app/templates/dashboards/overview.html` and `app/templates/dashboards/_overview_parts.html` are the frozen golden reference implementation for the `dashboard` block. Never modify `app/templates/dashboards/overview.html` or `app/templates/dashboards/_overview_parts.html` unless explicitly instructed by the founder.

```jinja
{# 1. KPI Stat Card Row (at most 4 cards) #}
<div class="*:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card grid grid-cols-1 gap-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:shadow-xs md:grid-cols-2 lg:grid-cols-4">
  {% for kpi in kpis[:4] %}
    {% call ui.card(class="@container/card") %}
      {% call ui.card_header() %}
        {{ ui.card_description(kpi.label) }}
        {{ ui.card_title(kpi.value, class="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl") }}
        {% if kpi.delta %}
          {% call ui.card_action() %}
            {% call ui.badge(variant="outline") %}{{ kpi.delta }}{% endcall %}
          {% endcall %}
        {% endif %}
      {% endcall %}
      {% if kpi.hint %}
        {% call ui.card_footer(class="flex-col items-start gap-1.5 text-sm") %}
          <div class="text-muted-foreground">{{ kpi.hint }}</div>
        {% endcall %}
      {% endif %}
    {% endcall %}
  {% endfor %}
</div>

{# 2. Primary Chart + Side Breakdown (4 + 3 columns on lg) #}
<div class="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
  {% call ui.card(class="lg:col-span-4") %}
    {% call ui.card_header() %}
      {{ ui.card_title("Primary Trend") }}
      {{ ui.card_description("...") }}
    {% endcall %}
    {% call ui.card_content() %}
      <div class="h-[300px] w-full"><canvas id="primaryChart"></canvas></div>
    {% endcall %}
  {% endcall %}

  {% call ui.card(class="lg:col-span-3") %}
    {% call ui.card_header() %}
      {{ ui.card_title("Breakdown") }}
      {{ ui.card_description("...") }}
    {% endcall %}
    {% call ui.card_content() %}
      {# Breakdown list or secondary chart #}
    {% endcall %}
  {% endcall %}
</div>
```

### 3.3 Recipe B — Data Table (`block: data-table`, `data_types: [record-list]`)

Every list page renders `list_table` from `components/list_table.html` (or `element_table` from `components/element_table.html` for ArchiMate elements):

```jinja
{% from "components/list_table.html" import list_table %}
{% from "basecoat/table.html" import table_row, table_cell %}

{% call(row) list_table(
    columns=[
      {"key": "name", "label": "Name", "sortable": True},
      {"key": "status", "label": "Status", "sortable": True},
      {"key": "owner", "label": "Owner", "sortable": True},
      {"key": "updated", "label": "Updated", "align": "right", "sortable": True},
    ],
    rows=items,
    empty_title="No records yet",
    empty_body="Add a record to start tracking this inventory.",
    test_id="records-table",
    search_placeholder="Filter records...",
    pagination=pagination
) %}
  {% call table_row() %}
    {% call table_cell(class="max-w-[500px] truncate font-medium") %}
      <a href="{{ row.href }}" class="underline decoration-muted-foreground/40 underline-offset-4 hover:decoration-foreground">{{ row.name }}</a>
    {% endcall %}
    {% call table_cell() %}
      {{ ui.badge(row.status_label, variant=row.status_variant|default("outline")) }}
    {% endcall %}
    {% call table_cell(class="text-muted-foreground") %}{{ row.owner or "—" }}{% endcall %}
    {% call table_cell(class="text-right tabular-nums text-muted-foreground") %}{{ row.updated or "—" }}{% endcall %}
  {% endcall %}
{% endcall %}
```

### 3.4 Recipe C — Record Detail (`block: record-detail`, `data_types: [record-facts, status, ...]`)

```jinja
{% from "components/fact_list.html" import fact_list %}
{% from "components/element_table.html" import element_table %}
{% from "components/risk_matrix.html" import risk_matrix %}

<div class="grid gap-6 lg:grid-cols-3">
  <div class="lg:col-span-2 space-y-6">
    {# Main tabbed content or related element_table / list_table #}
  </div>
  <div class="space-y-6">
    {% call ui.card() %}
      {% call ui.card_header() %}{{ ui.card_title("Summary") }}{% endcall %}
      {% call ui.card_content() %}
        {{ fact_list(facts) }}
      {% endcall %}
    {% endcall %}
  </div>
</div>
```

### 3.5 Recipe D — Form / Settings (`block: form` or `settings`, `data_types: [form-controls]`)

```jinja
<form method="post" class="max-w-2xl space-y-6">
  {% call ui.card() %}
    {% call ui.card_header() %}
      {{ ui.card_title("Details") }}
      {{ ui.card_description("Update the configuration below.") }}
    {% endcall %}
    {% call ui.card_content(class="space-y-4") %}
      {% call ui.field(label="Name", for_id="name", required=true) %}
        {{ ui.input(name="name", id="name", value=item.name) }}
      {% endcall %}
    {% endcall %}
    {% call ui.card_footer(class="flex justify-end gap-2 border-t pt-4") %}
      {{ ui.button("Cancel", variant="outline", href=cancel_url) }}
      {{ ui.button("Save Changes", type="submit") }}
    {% endcall %}
  {% endcall %}
</form>
```

---

## 4. Public Marketing Theme & ArchiMate Layer Tokens

- **Public marketing pages** (`layouts/public_base.html` with `{% block body_theme %}theme-marketing{% endblock %}`): Use `.public-btn`, `.public-card`, `.public-table`, `.public-faq`, and `.public-stepper` from `app/static/css/public_site.css`.
- **ArchiMate layer tokens**: Use `bg-layer-<name>`, `border-layer-<name>`, and `text-layer-<name>-emphasis` (`motivation`, `strategy`, `business`, `application`, `technology`, `implementation`, `risk`). Never use raw `violet-*`, `amber-*`, or `emerald-*` utilities.
- **Guarded nav links**: Blueprints that register non-fatally (`value_stream`, `business_model`, `organization`, `business_case`) must be guarded with `{% if 'value_stream.index' in current_app.view_functions %}` when linked outside the navigation registry.

---

*Authoritative sources: `C:\flask-basecoat\AGENTS.md` · `C:\flask-basecoat\docs\LAYOUT.md` · `C:\flask-basecoat\docs\MACROS.md` · `docs/data-type-register.yml` · `docs/design-map.json` · `docs/reuse-register.yml`*
