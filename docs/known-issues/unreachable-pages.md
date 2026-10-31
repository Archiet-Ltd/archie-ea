# Pages that existed and could not be reached — resolved

**Found:** 2026-08-08, by `scripts/check_broken_surfaces.py --kind orphan-page`
**Decided:** 2026-08-08 — all thirteen deleted. This is the decision record.

Thirteen templates extended a layout — which is what made them **pages** rather
than partials or macro files — and no `render_template()` call named them, and
no other template included them. They had no URL. Someone built each of these
and nobody could open any of them.

## The decision

**All thirteen were deleted.** Not one turned out to be a feature waiting for a
route: every single one was either superseded by a live equivalent or had no
back end at all. Resolving each against the running app's `url_map` — rather
than by grep — is what made that call safe, and it twice contradicted what a
grep had suggested.

| Template | Why it had no URL | Live equivalent |
|---|---|---|
| `adm_kanban/index.html` | superseded v1 board | `adm_kanban/board_v2.html`, served at `/adm-kanban/` |
| `admin/admin_overview.html` | superseded | `admin/index.html` |
| `archimate/viewpoints.html` | `/archimate/viewpoints` **redirects** to the unified composer — viewpoints became a mode within it | composer |
| `archimate_crud/list.html` | superseded by the dashboard/detail pair | `archimate_crud/dashboard.html` |
| `archimate_crud/create.html` | ditto | `archimate_crud/detail.html` |
| `archimate_crud/edit.html` | ditto | `archimate_crud/detail.html` |
| `archimate_crud/health_scorecard.html` | ditto | `archimate_crud/dashboard.html` |
| `capability_framework/dashboard.html` | its route redirects to `main.framework_management.dashboard` | framework management |
| `capability_maturity/frameworks/cobit_dashboard.html` | no route, no API — never wired | none |
| `capability_maturity/frameworks/level4_dashboard.html` | no route, no API — never wired | none |
| `duplicate-detection/dashboard.html` | stale copy under a **hyphenated** directory; the live one is the underscore path | `duplicate_detection/dashboard.html` |
| `errors/custom_error.html` | unused | `errors/{403,404,500,generic_error}.html` |
| `testing/artifact_viewer.html` | its only script was deleted as dead code earlier the same day | none |

## Two corrections to the original version of this document

The first draft asserted things that turned out to be false. Both were caught
by resolving against the real route table instead of grepping:

1. It said the ADM Kanban board's back end was **absent** — that `board_v2.js`
   called `/api/adm-kanban/v2/{phases,deliverables}`, which "do not exist". They
   do. `adm_kanban_v2_routes.py` registers **22** routes including `/cards`,
   `/config`, `/metrics`, `/phases/<phase>/deliverables` and four
   `/suggestions/*` endpoints. The API is complete and it serves the *live*
   board, `board_v2.html`. Only the superseded `index.html` was orphaned.

2. It implied wiring up a route was a live option for some of these. It was not
   for any of them: every page was already replaced or had nothing behind it.

## The two that had a data layer but no front door

`cobit_dashboard.html` and `level4_dashboard.html` are the only ones deleted
with no live equivalent. The COBIT **models do exist** — `cobit_domains`,
`cobit_processes` and the `cobit_capability_mapping` association in
`app/models/capabilities.py`. What was missing was any API serving them and any
route rendering the dashboards.

Wiring them up would mean building a feature, not fixing a defect, so the
templates were deleted rather than left to rot in every grep, audit and token
count. Git holds them if the work is ever resumed — and whoever resumes it
should know the data layer is already there.

## Why deletion rather than "leave it and document it"

A template nobody can open still costs something every day: it appears in every
grep, every design-token count, every audit, and it invites the reasonable-but
-wrong assumption that the feature exists. Deleting is reversible; the
confusion it causes is not free. Where a page had genuinely unfinished work
behind it, that fact is recorded above so the decision can be revisited with
the evidence rather than re-derived from scratch.

## Related finding: blueprint name collisions — resolved

**Investigated:** 2026-08-10, by booting the app and mapping every view function
back to its defining module, then registering each losing blueprint on a
throwaway `Flask()` and diffing its rules against the live `url_map`. Two pages
were genuinely lost and are now reachable; everything else turned out to be
deliberate, and one part of the original write-up was simply wrong.

### The correction: `/admin/security` was not a name collision

The original text said `app/_bootstrap/blueprints.py` registers "a *different*
blueprint that happens to share the name `security_bp`, from
`app/routes/security_api.py`". `security_bp` there is the **Python variable**;
the Flask blueprint name is `"security"`. Flask never saw a collision, because
nothing ever tried to register the other one — `app/modules/admin/security_routes.py`
was imported by no module anywhere in the tree. The page 404'd for the duller
reason that it was never wired up at all.

It is now registered from `_register_optional_standalone()` under the
unambiguous name `admin_security`, which is tier-independent (as billing and team
already are), so `/admin/security` works whether admin v1 or v2 is active.

Two things had to be fixed to make registering it defensible:

* Its header table was a **hardcoded literal** presented as the live header set —
  it advertised an HSTS policy this app does not send at all. It now reports the
  headers the page's own response carries, by running the app's registered
  `add_security_headers` over a probe response. That function is pure, so this
  observes the real policy rather than restating it, and when it cannot be found
  the page says "not determined" instead of inventing a table.
* Neither the page nor the secret-generating POST had **any authentication**.
  Both are now `@login_required @admin_required`, and the POST no longer logs
  "SECRET_KEY rotated" for an operation that rotates nothing.

### The five names, each resolved

| Name | Winner | Loser | Verdict |
|---|---|---|---|
| `capability_map` | `app/modules/capabilities/routes/*` | `app/routes/capability_map_routes.py` | **Nothing lost.** All 24 of the loser's endpoints are served by the winner at the same URLs — it is the pre-decomposition original (`app/modules/capabilities/__init__.py`: "capability_map_routes.py (6,562 lines) split into 9 focused" modules). Superseded duplicate; reported, not deleted. |
| `dashboard_pages` (×3) | `app/modules/dashboard/v2/routes/dashboard_pages_routes.py` | `app/api/dashboard_routes.py`; `app/modules/dashboard/routes/dashboard_pages_routes.py` | **One page lost.** Both losers are the documented `USE_DASHBOARD_GUARDRAILS` fallbacks, registered by `if not _ff_dashboard` / the v1 `else` branch — never both. But v2's docstring claim that "all 40 routes preserved exactly from v1" was false: it dropped `/dashboard/capability-heatmap`. **Fixed** — see below. |
| `deprecation` | `app/modules/admin/v2/routes/deprecation_routes.py` | `app/modules/admin/routes/deprecation_routes.py` | **Nothing lost.** Admin v1 vs v2, chosen by `USE_ADMIN_GUARDRAILS`; v2 serves all 7 URLs identically. Same dual-layout design as `admin` itself. |
| `health` | `app/routes/health_routes.py` (`/health`, `/health/db`) | `app/modules/monitoring/routes/health_routes.py` (9 routes under `/api/health`) | **Deliberately absent.** `blueprints.py` states it: monitoring blueprints are ops tooling not needed in the architect-facing app, and `_register_monitoring()` was removed as dead code. The name clash is a latent trap if that decision is ever reversed — registering the module would raise on `health` and take `metrics_bp` down with it — so rename it *before* re-enabling. |
| `sidebar_mgmt` | `app/modules/admin/v2/routes/sidebar_mgmt_routes.py` | `app/modules/admin/routes/sidebar_mgmt_routes.py` | **Nothing lost.** As `deprecation`. |

So of the five, three were sub-blueprints of the same v1/v2 module pairs CLAUDE.md
already documents (`admin` owns `deprecation` and `sidebar_mgmt`; `dashboard` owns
`dashboard_pages`), one was a superseded pre-split original, and one was a
documented omission.

### The second lost page: `/dashboard/capability-heatmap`

`app/templates/dashboard/capability_heatmap.html` and the API it fetches were
both live the whole time; only the route rendering the page was missing, because
the v2 rewrite kept `/dashboard/api/capability-heatmap` and dropped the page next
to it. The page route is now on the v2 blueprint, with the same endpoint name
(`dashboard_pages.capability_heatmap_page`) it had under v1.

The v2 API also had to regain `?group_by=domain`, which v1 supported and v2 did
not. The page's "Investment" view mode requests exactly that, and the template
falls back to `|| 0` — so without it every currency figure on that tab would have
rendered as a measured zero.

Update: that page and `/capability-maturity/heatmap` now answer 302 to the one
capability map (`/capability-map/dashboard`, the latter with `?heat=gap`); the
old templates are kept until the component inventory is regenerated.

### Endpoint renames

None. `admin_security` is a new blueprint name, not a rename of a live one, and
`grep` found zero `url_for` references to `security_bp.*` or
`capability_heatmap_page` anywhere in `app/templates/**` or `app/**/*.py`. The
restored heatmap page keeps its v1 endpoint name, so any future reference resolves
the way it always would have.

Pinned by `tests/test_blueprint_name_collisions.py` (database-free, like
`tests/test_boot_health.py`).
