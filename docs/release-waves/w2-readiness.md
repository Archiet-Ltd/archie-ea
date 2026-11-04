# Wave 2 readiness

What each wave 2 page shows when the product is started with `RELEASE_WAVE=2` and the demo seed
(`seed_demo_company()`, signed in as the seeded demo user). For each group in
`app/release_waves/wave_map.json` with rows marked `W2` + `page`, the group's pages were opened, then every
same-site link inside `<main>` on each page.

How a page was judged, from the HTML the server returns:

- **content**: the page has its heading and real text
- **empty state**: the page renders and says there is nothing yet
- **client-rendered**: the server HTML holds only the page frame; the list or detail is fetched by the browser
  afterwards (Alpine `x-show` / `x-cloak` blocks). The static walk cannot judge these. The data call
  they make was checked on its own (see each row).
- **early access**: a link on an open page leads to the early-access page of a capability a later wave opens
- **dead link**: 404

## Summary

| Group | Pages | Ready | Needs work before W2 |
|---|---|---|---|
| PG-ANS Answers, twin map, history and briefings | 5 | 3 | briefings list (Linux re-check), briefing detail (no seeded briefing) |
| PG-DIA Composer, diagrams, model exchange and traceability | 2 | 2 | links to W3 pages lead to early access |
| PG-ELE Element pages and browse by layer | 4 | 4 | element detail and list need a browser walk; W3 import button |
| PG-RTP Moved addresses | 3 | 3 | same W3 import button as PG-ELE |
| PG-SHR Answer and artefact sharing | 2 | 1 | shared-link page (no seeded share) |

## Findings, with cause

### 1. Open pages link to wave 3 pages, so the link lands on "early access"

The element list, the by-layer and search pages (same template) and the traceability page show an
**Import model file** link. Model import (`/architecture/import/oef`) is W3 in the wave map, so at wave 2
`point_closed_links_at_early_access` (`app/release_waves/hook.py:270`) rewrites it to
`/early-access/composer-diagrams-model-exchange-and-traceability`.

- `app/templates/architecture/elements.html:229`: toolbar "Import" link to `architect_ui.import_oef`
- `app/templates/architecture/elements.html:312`: empty-state "Import model file" link
- `/architecture/traceability/<id>`: two links to W3 traceability pages (`/architecture/traceability` is W3)

A wave 2 customer clicks an action on a page they were sold and gets a waiting-list page. Either move
OEF import and `/architecture/traceability` to W2, or hide these links while their target is closed (the
template can ask the wave switch whether a URL is open).

### 2. `/solutions/briefings` raises on Windows only

`ValueError: Invalid format string` at
`app/modules/solutions_strategic/v2/services/weekly_briefing.py:443`: the `%-d` day format is glibc-only.
Production is Linux, so this is a local-only crash, **not** a release blocker, but the page was not
judged. Needs a re-check on Linux, and `%-d` should become `str(dt.day)` so the code does not depend on the
platform.

### 3. Pages the demo seed does not cover

- `/solutions/briefings/<id>`: the demo seed creates no briefing, so the detail page could not be opened
  with real data.
- `/shared/<token>`: the demo seed creates no share link.

Both need a seeded record (or a journey test that creates one) before W2 can claim them.

### 4. Element pages are client-rendered: browser walk still owed

`/architecture/elements`, `/architecture/by-layer/<layer>`, `/architecture/search` and
`/architecture/elements/<id>` return a page frame whose list is fetched by Alpine. Their data call,
`/archimate/api/elements/search`, is W1 in the wave map and **answers 200 with the seeded elements at
wave 2** (checked directly). So the "the list was not read" text in the HTML is the hidden error block,
not a failure. A browser walk (Playwright) on these four pages is still needed to confirm they draw.

### 5. Ready

- `/intelligence/answers`: content
- `/intelligence/history/as-of`: content ("Model as of")
- `/intelligence/twin-map`: redirects to the composer focused on the most-connected element (intended)
- `/archimate/composer`: content
- `/architecture/traceability/<id>`: renders; empty layers for an element with no traces (correct empty state)
- `/architecture/<layer>/<type>/<id>`: element detail answers 200 with a seeded element (`/architecture/application/ApplicationComponent/560`)
- `/architecture/relationships`: content
- `/enterprise/analysis/impact-analysis`: content
- `/share/artefacts`: renders (no shares yet); its links to `/capability-map/` render

## Before W2 opens

1. Decide finding 1: open OEF import and `/architecture/traceability` in W2, or hide their links at W2.
2. Replace `%-d` in `weekly_briefing.py:443`; re-check `/solutions/briefings` on Linux.
3. Seed a briefing and a share link in `seed_demo_company`, then open both detail pages.
4. Browser walk of the four client-rendered element pages.
