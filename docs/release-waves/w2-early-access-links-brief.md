# Brief: wave 2 links to wave 3 pages say "Early access" before the click

**Decision (Entelim lead, 2026-11-04):** keep the wave 2 links to wave 3 pages. Don't hide them. Each link says "Early access" before anyone clicks it, and the page it leads to offers the waiting list.

## Links in scope

| Page (wave 2) | Link | Wave 3 target |
|---|---|---|
| Element list (`elements.html:229`) | Import model | `/architecture/import/oef` |
| Element list (`elements.html:312`) | Import model (empty state) | `/architecture/import/oef` |
| Element detail, traceability/&lt;id&gt; | Traceability | `/architecture/traceability` |

Any other link from an open page to a closed page needs the same treatment. The rule must hold for every wave, so no template should have to know which wave is released.

## Approach

Make the change in one place: `point_closed_links_at_early_access` in `app/release_waves/hook.py:270`.

1. In `swap`, when the target starts with `EARLY_ACCESS_PREFIX`, also write `data-early-access="<slug>"` into the opening `<a>` tag. The regex ends at the href value, so append the attribute right after the closing quote.
2. One stylesheet rule shows the label: `a[data-early-access]::after` with `content: "Early access"`, styled as the shared badge (same tokens as `ui.badge`, variant outline, small). Add it to the app stylesheet source, then rebuild the stylesheet.
3. Accessibility: also write `title="Early access: join the waiting list"`. A label that exists only in CSS isn't announced reliably, so add a visually hidden " (early access)" only if the shared badge pattern already does the same.
4. The early-access page already offers the waiting-list form (`public/early_access.html`). No change there.

## Acceptance

- With `RELEASE_WAVE=2`, the element list shows "Early access" on the Import model link before any click (server HTML carries `data-early-access`). Clicking it opens `/early-access/<group>` with the waiting-list form.
- With `RELEASE_WAVE=3` (import open), the same link has no label and opens the import page.
- Links between open pages are unchanged byte for byte.
- Tests in `app/release_waves/tests/`: a closed link gains `data-early-access` and the title; an open link is untouched; the query string is still dropped for early-access targets. Show each test red before the change.
- Browser walk at 1440 and 390 wide, light and dark: the badge sits inline and doesn't wrap the link onto a new line.

## Not in scope

Opening the import or traceability list pages in wave 2.
