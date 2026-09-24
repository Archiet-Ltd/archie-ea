# DESIGN.md — Entelim Design System

> **For AI agents:** Read this file before editing any template or UI file.
> This describes the complete design system used by this Flask + Tailwind CSS + shadcn/ui + Alpine.js application.
> The canonical source of truth for patterns is `docs/design_system/pattern_registry.json`.
> The canonical color token map is `docs/design_system/token_map.json`.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python / Flask (Jinja2 templates) |
| Styling | Tailwind CSS v3 + shadcn/ui design tokens |
| Interactivity | Alpine.js v3 (no jQuery, no React) |
| Icons | Lucide Icons (via CDN, `data-lucide="icon-name"`) |
| Layout | Three base templates: `layouts/admin_base.html`, `layouts/base.html`, `layouts/public_base.html` |

---

## Color Tokens (MANDATORY — never use raw Tailwind colors)

All colors use CSS variables defined in `app/static/css/shadcn_tokens.css`. Reference them via Tailwind semantic classes only.

### Core Tokens

| Purpose | Tailwind class | CSS variable | Light mode |
|---------|---------------|-------------|-----------|
| Page background | `bg-background` | `--background` | white |
| Page text | `text-foreground` | `--foreground` | near-black |
| Card background | `bg-card` | `--card` | white |
| Card text | `text-card-foreground` | `--card-foreground` | near-black |
| Muted background | `bg-muted` | `--muted` | light gray |
| Muted text | `text-muted-foreground` | `--muted-foreground` | medium gray |
| Primary (blue) | `bg-primary` / `text-primary` | `--primary` | #3b82f6 |
| Primary label | `text-primary-foreground` | `--primary-foreground` | near-white |
| Destructive (red) | `bg-destructive` / `text-destructive` | `--destructive` | red |
| Success | `bg-success` / `text-success` | `--success` | emerald |
| Warning | `bg-warning` / `text-warning` | `--warning` | amber |
| Info | `bg-info` / `text-info` | `--info` | blue |
| Border | `border-border` | `--border` | light gray |
| Input border | `border-input` | `--input` | light gray |
| Focus ring | `ring-ring` | `--ring` | blue |
| Border radius | `rounded-lg` | `--radius` | 0.5rem |

### ArchiMate Layer Colors (domain-specific)

| Layer | Color | Usage |
|-------|-------|-------|
| Motivation | violet (`--layer-motivation`) | Goals, Drivers, Outcomes, Requirements |
| Strategy | amber (`--layer-strategy`) | Courses of Action |
| Business | emerald (`--layer-business`) | Processes, Roles, Actors |
| Application | blue (`--layer-application`) | Services, Components, Data |
| Technology | green (`--layer-technology`) | Nodes, Devices, Networks |
| Implementation | sky (`--layer-implementation`) | Work Packages, Plateaus |
| Risk | red (`--layer-risk`) | Assessments, Issues |

Use them as Tailwind utilities: `bg-layer-<name>` / `border-layer-<name>` for the
fill and border, and **`text-layer-<name>-emphasis`** for any text on a
`bg-layer-<name>/10` tint. The base hue is picked for distinctness between the
seven layers, not for contrast — as text it is under WCAG AA, exactly as
`--info` is against `bg-info/10`. Never hand-roll the layer palette from raw
`amber-*` / `violet-*` / `emerald-*` classes; a layer badge is these tokens.

### Forbidden Color Classes

**Never** use raw Tailwind color scales — the pre-commit hook `check_token_migration.py` will block the commit.

```
❌ bg-white         → ✅ bg-background
❌ bg-gray-50       → ✅ bg-muted/30
❌ bg-gray-100      → ✅ bg-muted
❌ text-gray-500    → ✅ text-muted-foreground
❌ text-gray-700    → ✅ text-foreground
❌ text-gray-900    → ✅ text-foreground
❌ bg-blue-500      → ✅ bg-primary
❌ text-blue-600    → ✅ text-primary
❌ bg-red-500       → ✅ bg-destructive
❌ text-red-500     → ✅ text-destructive
❌ border-gray-200  → ✅ border-border
❌ text-white       → ✅ text-primary-foreground (on primary bg)
```

Green and yellow have no semantic tokens — use emerald and amber scales directly for status colors.

**Coloured text on its own tint** — use `-emphasis`, not the base token:

```
❌ bg-destructive/10 text-destructive           → ✅ bg-destructive/10 text-destructive-emphasis
❌ bg-info/10 text-info                         → ✅ bg-info/10 text-info-emphasis
❌ bg-warning/10 text-warning-foreground        → ✅ bg-warning/10 text-warning-emphasis
```

`--destructive` scores 3.30 against `bg-destructive/10` and `--info` scores 4.50
against `bg-info/10`, both under the WCAG AA 4.5 required for normal text. shadcn's
`-foreground` variants do not cover this case: they are for text on a *solid* fill.
The `-emphasis` variants are darker in light mode and lighter in dark mode, where
the tint is dark.

**Warning is the trap.** `--warning-foreground` is pure black — `0 0% 0%` — in
*both* themes. On `bg-warning/10` that is 19.43 in light mode and **1.19 in dark
mode**, where the tint composites to near-black: invisible, not merely low-contrast.
Use `--warning-emphasis` (5.62 light / 11.81 dark) on any warning tint.

All three emphasis tokens are now declared in **both** `:root` and `.dark`.
`--info-emphasis` was previously light-mode only, so `text-info-emphasis` resolved
to an invalid declaration in dark mode and silently fell back to the inherited
colour. When adding an `-emphasis` token, declare both halves or it will fail in
exactly one theme — the one you are less likely to be looking at.

Note on blue: `bg-blue-500` maps to `bg-info`, not `bg-primary`, despite the table
above. In light mode the two are the same colour, but `--primary` becomes near-white
in dark mode (`210 40% 98%`), which would turn every blue info badge white for
dark-theme users. `--info` tracks blue in both.

---

## Typography

**Brand typeface (ARCH-111, decided 18 Aug 2026): Inter**, SIL Open Font
License 1.1 — no royalty, no source-disclosure obligation, embedding and
redistribution explicitly permitted, so there was no licensing question to
escalate. It was already referenced in the Caddy 503 page's font stack before
being adopted for the app itself.

Vendored locally, never loaded from Google Fonts or any CDN: four static
woff2 weights (400/500/600/700, latin subset) at
`app/static/vendor/inter-{400,500,600,700}.woff2`, tracked with their upstream
URL and sha384 in `app/static/vendor/VENDOR_MANIFEST.txt`, licence text at
`app/static/vendor/inter-LICENSE.txt`. Wired via `@font-face` and a
`--font-sans` CSS variable in `app/static/css/shadcn_tokens.css` (a plain,
non-Tailwind stylesheet loaded directly by `partials/_head.html`), **not**
through `tailwind.config.js`'s `fontFamily` — that file would be a silent
no-op until the next Tailwind rebuild, and `tailwind-output.css` is committed
pre-built with no Node toolchain available to regenerate it. `body` sets
`font-family: var(--font-sans)`; do not set `font-family` per-template.

The system stack remains the fallback in `--font-sans` — `ui-sans-serif,
system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial,
sans-serif` — so a missing or slow woff2 (`font-display: swap`) never blocks
text from painting or breaks layout.

Key classes (unchanged):

| Use | Class |
|-----|-------|
| Page heading | `text-2xl font-bold text-foreground` |
| Section heading | `text-lg font-semibold text-foreground` |
| Body text | `text-sm text-foreground` |
| Secondary / help text | `text-sm text-muted-foreground` |
| Label | `text-xs font-medium text-muted-foreground uppercase tracking-wide` |
| Code / mono | `font-mono text-sm` |

---

## Layout

### Base Templates

Every page **must** extend one of these — never write standalone HTML:

```jinja
{# Admin/dashboard pages #}
{% extends 'layouts/admin_base.html' %}

{# Standard pages #}
{% extends 'layouts/base.html' %}

{# Public / auth pages #}
{% extends 'layouts/public_base.html' %}
```

**Forbidden** — these do not exist:
- `{% extends 'layouts/main.html' %}` ❌
- `{% extends 'admin/base.html' %}` ❌
- `{% extends 'layouts/sidebar.html' %}` ❌

### Sidebar

Always include via:
```jinja
{% include 'components/admin_sidebar.html' %}
```

**Never** duplicate or inline the sidebar HTML.

### Page Structure (admin pages)

```html
{% block content %}
<div class="flex-1 overflow-auto">
  <!-- Page header with breadcrumbs -->
  {% from 'components/page_header.html' import page_header %}
  {{ page_header(title='Page Title', breadcrumbs=[('Home', url_for('main.index')), ('Current', None)]) }}

  <div class="p-6 space-y-6">
    <!-- page content here -->
  </div>
</div>
{% endblock %}
```

### `page_shell` — the screen-system header

`macros/page_shell.html`'s `page_shell()` is the header for screens rebuilt from
2026-08-12 onward, and the one the `shell_conformance` gate counts. Prefer it over
`components/page_header.html` for new work.

```jinja
{% from 'macros/page_shell.html' import page_shell %}

{% macro _ps_actions() %}<a href="/new" class="...">New</a>{% endmacro %}
{{ page_shell(
    title='Applications',
    subtitle='Manage your application portfolio',
    breadcrumb=[('Home', url_for('main.index')), ('Applications', None)],
    actions_caller=_ps_actions
) }}
```

`breadcrumb` takes `(label, href)` tuples — the last one is the current page and
takes `None`. Three optional slots exist for headers a plain title/subtitle cannot
express; all default to off:

| Slot | Use for |
|---|---|
| `icon='git-branch'` | a lucide icon beside the `<h1>` |
| `subtitle_caller` | a subtitle carrying markup (an Alpine `x-text` span, a badge macro, `<strong>`) — wins over `subtitle` |
| `meta_caller` | a row of identity badges / counters under the title that are not actions |

**Do not hand-roll a header row beside these.** A page with an icon, a badge or a
back-link next to its title belongs in these slots; a back-link belongs in
`breadcrumb`. There is exactly one `<h1>` per page and `page_shell` owns it.

---

## Components

### Button size scale (H-06 — mandatory, no arbitrary heights)

`/procurement/spend` and several other pages accumulated eight distinct
button heights (20/28/32/34/40/44px), seven font sizes and six border radii
with no scale governing any of them. There are exactly three permitted
button heights. Do not hand-write a height, padding, or `text-[Npx]` value on
a button — pick one of these three:

| Tier | Height class | Height | Use for |
|---|---|---|---|
| `sm` | `h-8` (32px) | compact / dense toolbars, inline table-row actions | |
| `md` | `h-9` (36px)\* | the default — most buttons, forms | |
| `lg` | `h-11` (44px) | primary page-level CTAs, WCAG 2.5.8 target-size-sensitive actions | |

\* The existing shadcn default is `h-9` (36px), not 40px — that default
predates this scale and is left as `md` rather than mass-migrated to `h-10`
across every existing button, which would be a large, low-value diff. New
`md` buttons should use `h-9` to match the existing convention; `h-10` (40px)
is not a fourth tier and must not appear on a button.

All three heights pair with `px-3 text-xs` (sm), `px-4 py-2 text-sm` (md), or
`px-6 text-base` (lg) — never a bespoke padding/font combination. Border
radius on interactive controls is `rounded-md` (buttons, inputs) or
`rounded-lg` (cards, panels) only — never a raw multi-value `border-radius`
or an arbitrary `rounded-[...]` value; a 3-value shorthand like `0px 0px 4px`
silently omits the fourth corner and is what ARCH-110 / H-06 flagged. No
instance of that malformed value was found in the current template tree
(`grep -rn "0px 0px 4px"` across `app/templates` and `app/modules/**/templates`
returns nothing) — it was already corrected in an earlier design-token wave.

### Buttons

```html
<!-- Primary -->
<button type="button" class="inline-flex items-center justify-center rounded-md text-sm font-medium
  bg-primary text-primary-foreground hover:bg-primary/90 h-9 px-4 py-2
  disabled:opacity-50 disabled:pointer-events-none">
  Save
</button>

<!-- Secondary -->
<button type="button" class="inline-flex items-center justify-center rounded-md text-sm font-medium
  bg-secondary text-secondary-foreground hover:bg-secondary/80 h-9 px-4 py-2">
  Cancel
</button>

<!-- Destructive -->
<button type="button" class="inline-flex items-center justify-center rounded-md text-sm font-medium
  bg-destructive text-destructive-foreground hover:bg-destructive/90 h-9 px-4 py-2">
  Delete
</button>

<!-- Outline -->
<button type="button" class="inline-flex items-center justify-center rounded-md text-sm font-medium
  border border-input bg-background hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2">
  Export
</button>

<!-- Ghost -->
<button type="button" class="inline-flex items-center justify-center rounded-md text-sm font-medium
  hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2">
  View
</button>

<!-- Icon-only (must have aria-label) -->
<button type="button" aria-label="Close" class="inline-flex items-center justify-center rounded-md
  hover:bg-accent hover:text-accent-foreground h-9 w-9">
  <i data-lucide="x" class="h-4 w-4"></i>
</button>
```

**Rules:**
- Every `<button>` **must** have `type="button"` (or `type="submit"` if it's a submit button — never omit it)
- Never use `onclick=` HTML attributes — use Alpine `@click` or `data-action`
- Icon-only buttons must have `aria-label`
- Always include `disabled:opacity-50 disabled:pointer-events-none`

### Cards

```jinja
{% import 'components/card.html' as card %}

{% call card.card() %}
  {% call card.card_header() %}
    {% call card.card_title() %}Title{% endcall %}
    {% call card.card_description() %}Optional description{% endcall %}
  {% endcall %}
  {% call card.card_content() %}
    <p class="text-sm text-muted-foreground">Content here.</p>
  {% endcall %}
  {% call card.card_footer() %}
    <button type="button" class="...">Action</button>
  {% endcall %}
{% endcall %}
```

**Forbidden:** `<div class="card">`, Bootstrap `card-body`, `bg-white` on cards.

### Badges / Status Pills

```html
<!-- Success -->
<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium
  bg-emerald-500/10 text-emerald-600 border border-emerald-500/30">Active</span>

<!-- Warning -->
<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium
  bg-amber-500/10 text-amber-600 border border-amber-500/30">Pending</span>

<!-- Danger -->
<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium
  bg-red-500/10 text-red-600 border border-red-500/30">Error</span>

<!-- Info -->
<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium
  bg-blue-500/10 text-blue-600 border border-blue-500/30">Info</span>

<!-- Neutral -->
<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium
  bg-muted text-muted-foreground border border-border">Draft</span>
```

### Form Inputs

```html
<input type="text"
  class="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1
    text-sm shadow-sm transition-colors placeholder:text-muted-foreground
    focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring
    disabled:cursor-not-allowed disabled:opacity-50"
  placeholder="Search...">
```

**Forbidden:** Bootstrap `form-control`, `form-group`, `input-group`, inline width/height styles.

### Modals

```jinja
{% from 'components/modal.html' import modal %}

{# Trigger #}
<button type="button" data-modal-open="my-modal">Open Modal</button>

{# Modal definition #}
{% call modal(id='my-modal', title='Modal Title', size='default') %}
  <p class="text-sm text-muted-foreground">Modal content here.</p>
{% endcall %}
```

```js
// JavaScript API
Platform.modal.open('my-modal', { key: value });
Platform.modal.close('my-modal');
```

**Forbidden:**
- `x-show='showModal'` local Alpine state for modals
- `window.openModal()` / `window.closeModal()` (legacy)
- Inline modal HTML without the macro
- `x-cloak` on modal root elements (they use `hidden` attribute)

### Metrics Cards (KPI widgets)

```jinja
{% from 'components/metrics_card.html' import metrics_card %}

{{ metrics_card(
  title='Total Applications',
  value=metrics.applications,
  icon='layers',
  trend='+12%',
  trend_direction='up'
) }}
```

### Empty States

```jinja
{% from 'components/empty_state.html' import empty_state %}

{{ empty_state(
  icon='inbox',
  title='No results found',
  description='Try adjusting your search or filters.',
  cta_label='Add New',
  cta_href=url_for('resource.create')
) }}
```

### Skeleton / Loading States

```jinja
{% from 'components/skeleton.html' import skeleton_table, skeleton_card %}

{# Table loading state #}
<div x-show="loading">{{ skeleton_table(rows=5, cols=4) }}</div>

{# Card loading state #}
{{ skeleton_card() }}
```

**Button-level loading (inline is OK here):**
```html
<button type="button" :disabled="isSubmitting">
  <span x-show="isSubmitting"
    class="animate-spin h-4 w-4 border-2 border-current border-t-transparent rounded-full inline-block">
  </span>
  <span x-show="!isSubmitting">Submit</span>
</button>
```

### Data Tables

```js
// Alpine component (preferred)
x-data="dataTable({ apiUrl: '/api/resource', perPage: 25 })"

// Or composition mixin
Alpine.data('myPage', function() {
  return Object.assign({}, Platform.dataTable.mixin({ apiUrl: '/api/resource' }), {
    // page-specific state
  });
});
```

**Rules:**
- Null values render as em dash (`—`), never `£0.00` or blank
- Currency via `window.currencyManager.format()`, never hardcoded
- Use arrays (`[]`), not `Set`, for selection state (Alpine cannot observe Set mutations)
- Four required states: loading (skeleton), empty (no data), empty (filtered), populated

**Forbidden:** jQuery DataTables, Bootstrap `table-striped`, manual `innerHTML` string-building for rows.

---

## Alpine.js Patterns

### General Rules

- Every `x-show` element **must** also have `x-cloak` to prevent flash-of-unstyled-content (FOUC)
- **Exception:** Modal root elements use `hidden` attribute, NOT `x-show`/`x-cloak`
- `Alpine.data()` component names must be globally unique
- Use `$nextTick()` instead of `setTimeout()` for post-render operations
- Use `$watch()` instead of `setTimeout()` for reactive state monitoring
- Never nest `x-data` scopes with the same variable name

```html
<!-- Page-level Alpine component -->
<div x-data="myPage()" x-cloak>
  <!-- content -->
</div>

<script>
Alpine.data('myPage', function() {
  return {
    isOpen: false,
    items: [],
    async init() {
      const resp = await fetch('/api/items');
      this.items = await resp.json();
    }
  };
});
</script>
```

### Global Loading Overlay

```js
// Show/hide platform-wide loading overlay
Platform.loading.start();
// ... async work ...
Platform.loading.stop();
```

---

## Icons

Uses Lucide Icons. All icons are rendered via the data attribute and auto-initialized:

```html
<i data-lucide="layers" class="h-4 w-4"></i>
<i data-lucide="chevron-down" class="h-4 w-4 text-muted-foreground"></i>
```

Common sizes: `h-4 w-4` (small), `h-5 w-5` (default), `h-6 w-6` (large), `h-12 w-12` (hero).

---

## Navigation / Tabs

```jinja
{% from 'macros/nav_macros_shadcn.html' import pill_tabs, tab_panel %}
```

**Forbidden files:**
- `macros/nav_macros_shadcn_fixed.html` — DELETED
- `macros/nav_macros.html` — DEPRECATED

---

## Entity Pickers (search-select fields)

Any form field for a user, application, vendor, or ArchiMate element **must** use a live-search picker — not a plain `<input type="text">`.

| Entity | API endpoint |
|--------|-------------|
| Users | `GET /api/users` |
| Applications | `GET /applications/api/list?search={q}&limit=10` |
| ArchiMate elements | `GET /architecture/decisions/api/element-search?q={q}` |
| Vendor products | `GET /api/vendor/search?q={q}` |
| APQC processes | `GET /api/apqc/search?q={q}` |

Pattern: search input → 300ms debounce → API call → dropdown results → click to select → hidden input gets `id`.

---

## ArchiMate Layer Rules (domain-specific)

Every backend CREATE for a motivation entity (Driver, Goal, Constraint, Requirement, Risk, Metric, Plateau, WorkPackage) **must** call `_sync_archimate_element()` to create a corresponding `ArchiMateElement` row. Plain textareas are not acceptable for these entities — the field IS the ArchiMate element.

| Domain model | ArchiMate type | Layer |
|-------------|----------------|-------|
| SolutionDriver | Driver | Motivation |
| SolutionGoal | Goal | Motivation |
| SolutionConstraint | Constraint | Motivation |
| SolutionRequirement | Requirement | Motivation |
| SolutionRisk | Assessment | Motivation |
| SolutionMetric | Outcome | Motivation |
| SolutionPlateau | Plateau | Implementation |

---

## Impact Analysis API

Use the canonical endpoint — do not create new impact scoring logic:

```js
const resp = await fetch('/api/v1/impact/analyze', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ app_id, scenario })
});
const json = await resp.json();
const data = json.data ?? json; // unwrap success_response() wrapper
```

Risk level badge colors: `CRITICAL`/`HIGH` → `text-destructive`, `MEDIUM` → `text-amber-600`, `LOW` → `text-emerald-600`.

---

## Business Architecture surfaces (persona: `business_architect`)

`business_architect` is a first-class `enterprise_role` (`app/models/user.py`,
`ROLE_BUSINESS_ARCHITECT`) with a governed AI charter (`architect_persona_charters.py`,
in `ARCHITECT_PERSONAS`). Its pages are grouped in the **"Business Architecture"** sidebar
section (`components/admin_sidebar.html`, gated on the `business_architecture` nav section
in `role_access.py` / `context_processors.py`) and mirrored as "Your Workspace" cards on
`/dashboard/overview` (plus a "Business" persona tab). All these pages extend
`layouts/admin_base.html` and use the standard macros/tokens — **no new components or color
tokens were introduced** for them.

| Surface | Route (endpoint) | Notes |
|---|---|---|
| Capability map + visuals | `/capability-map` (`capability_map.index`) | nested-box map, maturity radar, 2×2 TIME investment bubble (Chart.js) |
| Value streams + BIZBOK grid | `/value-streams` (`value_stream.index`) | stage swimlane + capability×stage grid, click-to-set cells |
| Business / Operating Model canvas | `/business-model` (`business_model.index`) | 9-box BMC + operating-model archetype, inline block editing |
| Traceability matrix | `/architecture/traceability` (`architect_ui.traceability_matrix`) | existing; surfaced in nav |
| Impact analysis | `/strategic/impact-analysis` (`strategic.impact_analysis`) | existing; the old `/impact-analysis` nav item was a dead link — repointed here |
| Application rationalization | `/rationalization` (`unified_applications.rationalization_dashboard`) | existing; surfaced in nav |
| Organization & RACI | `/organization` (`organization.index`) | D3 org chart (BusinessActor composition) + enterprise RACI matrix |
| Business cases | `/business-case` (`business_case.index`) | structured business-case document |

**Guarded nav links (required pattern).** The value-stream / business-model / organization /
business-case blueprints register **non-fatally** (a load failure is logged, not raised).
A sidebar `url_for('<endpoint>')` to such a blueprint therefore MUST be guarded, or one
failed registration would `BuildError` and 500 **every** page:

```jinja
{% if 'value_stream.index' in flask.current_app.view_functions %}
<a href="{{ url_for('value_stream.index') }}">…</a>
{% endif %}
```

**Motivation bridge.** Journey-scoped `Solution*` motivation is promoted into the enterprise
motivation layer (Driver/Goal/Outcome/Principle + ArchiMate element) via the
`flask bridge-motivation` CLI — non-destructive and idempotent (`MotivationBridgeLink`).

---

## What Not to Do (summary)

| ❌ Forbidden | ✅ Use instead |
|-------------|--------------|
| Raw Tailwind colors (`gray-*`, `blue-*`, etc.) | Semantic tokens (`bg-background`, `text-primary`, etc.) |
| Bootstrap classes (`btn-primary`, `card-body`, `form-control`) | Tailwind + shadcn classes |
| jQuery / `$(...)` | Alpine.js |
| `onclick=` HTML attributes | Alpine `@click` or `data-action` |
| `window.openModal()` | `Platform.modal.open('id')` |
| Inline modal HTML | `{% call modal(...) %}` macro |
| `setTimeout()` for reactive state | `$nextTick()` or `$watch()` |
| `new Set()` for Alpine selection state | Arrays `[]` |
| `{% extends 'layouts/main.html' %}` | `{% extends 'layouts/admin_base.html' %}` |
| `{% include 'partials/sidebar.html' %}` | `{% include 'components/admin_sidebar.html' %}` |
| Standalone `<button>` without `type=` | Always `type="button"` or `type="submit"` |
| Hand-rolled stat divs | `{{ metrics_card(...) }}` macro |
| Inline empty state HTML | `{{ empty_state(...) }}` macro |
| Custom loading spinners | `{{ skeleton_table(...) }}` macros |

---

*Authoritative sources: `docs/design_system/pattern_registry.json` · `docs/design_system/token_map.json` · `app/static/css/shadcn_tokens.css` · `tailwind.config.js`*
