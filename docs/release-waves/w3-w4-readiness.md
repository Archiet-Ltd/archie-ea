# Wave 3 and wave 4 readiness

I walked every wave 3 and wave 4 page and GET route with `RELEASE_WAVE=3` and `RELEASE_WAVE=4`, signed in as the demo-seed user. Each page was loaded and every in-page link followed. Route parameters were filled from the app's real URL map. A walk with an org admin is still to do (see 5).

## Blockers

### 1. Wave 3 moves the Architecture pages to `/catalogues/`, which does not exist

From wave 3, `wave_map.json` moves these addresses to `/catalogues/…`:

| From | To | Wave today |
|---|---|---|
| `/architecture/` | `/catalogues/` | RT |
| `/architecture/dashboard` | `/catalogues/` | RT |
| `/architecture/elements` | `/catalogues/all` | W2 |
| `/architecture/search` | `/catalogues/all?q=` | W2 |
| `/architecture/by-layer/<x>` | `/catalogues/<layer>` | W2 |
| `/enterprise/architecture/elements` | `/catalogues/all` | RT |
| `/enterprise/reference-catalog` | `/catalogues/reference-capabilities` | RT |
| `/auto-dashboard/<x>` | `/catalogues/<group>` | RT |

No route serves `/catalogues`. Only `wave_map.json`, `wave_plan_pages.json` and `app/release_waves/tests/test_hook.py` mention it. So at wave 3:

- the Architecture breadcrumb and all seven layer links on every layer page (`/architecture/<layer>/<type>`, its `/new` and `/<id>/edit`) are 404s;
- the OEF import page's "Architecture Elements" and "Back to Dashboard" links are 404s;
- the wave 2 element list and search stop working: opening wave 3 would take away a wave 2 page.

**Done (lead decision, 2026-11-04): the moves are deferred.** `build.py` now leaves out any move whose destination no route answers, so these addresses keep answering as they do today until the catalogue pages are built. Three more moves to missing addresses were dropped the same way: `/auto-dashboard/<x>/<x>` ("the record's own page") and the two W5 dashboards to `/capability-map/taxonomy-checks`. `test_every_move_in_the_map_leads_to_an_address_a_route_answers` keeps it so.

The options were: (a) build the catalogue pages (`/catalogues/`, `/catalogues/all`, `/catalogues/<layer>`, `/catalogues/<group>`, `/catalogues/reference-capabilities`) before wave 3 opens; or (b) push these moves to the wave that ships the catalogues. A test should make sure every move target is a real route at the wave the move takes effect.

### 2. Traceability "Export CSV" is a dead link

`archimate_views/traceability.html:81` links to `/api/archimate/traceability/export`, which is RT in the wave map, so at wave 3 the button on an open page leads nowhere. Open the export with the page (W3), or hide the button until RT.

## Gaps

### 3. `/opportunities`, `/recommendations`, `/simulations` show the public marketing page

These are W3 `override` rows. Signed in, each answers with the public site layout: marketing nav, "Coming soon" modules, `/features#…` anchors. It doesn't show the user's own opportunities, recommendations or scenarios. Check that the in-app page is what wave 3 means to open.

### 4. Two import pages render an empty main area

`/archimate/import-review` and `/archimate/import/diagram` return 200, but `<main>` has no content beyond the footer. Either they render client-side (needs a browser walk) or they lack an empty state.

### 5. Admin pages not yet walked

These pages answered 403 to the demo user, which is the right answer for a non-admin:
- `/dashboard/admin/custom-fields` (W3)
- `/admin/api-settings` and `/admin/sso` (W4)

They need a walk as an org admin.

### 6. No seed data for some detail pages

`/metamodel/properties/<id>` and `/architecture/import/oef/restore-points/<id>` were 404s because the demo seed has no property definition or restore point. These are unverified, not broken.

## Ready

- `/metamodel/properties`
- `/archimate/diagrams`
- `/architecture/import/oef` (except the links in 1)
- `/architecture/traceability`: renders with counts per type; its "8 requirements" link correctly leads to early access
- layer pages: render, but see 1
- the sprints API (`/api/sprints/default-board`, `/velocity`; `/api/sprints` correctly asks for `board_id`)
- `/admin/connectors/jira`
- the SSO entry points answer 400/405 without parameters, as expected

The walker is the wave 2 one (#948) plus a wave switch, real route names, and override and parameter-free blueprint routes included.
