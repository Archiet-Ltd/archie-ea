# Capability Governance Dashboard - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
An enterprise architect sees how well the capability model is owned and described, finds the capabilities that need attention and fixes each one in place.

## What moves
- Three figures become the Stat card row: capabilities, those needing attention and the governance score, each counted from the rows of the table below it, each with a sentence. No icons, no coloured borders. The score carries a band word (Healthy, Partly governed, Weakly governed) in a library badge in place of the coloured icon.
- The table is the library table with sortable heads, a search field, a per-page select and bulk selection; the status of each row is a library badge.
- Edit governance and the delete confirmation are library dialogs on the library field, input, select and textarea.

## What is fixed
- A model with no capabilities says why there is nothing to score and offers the capability map; a search with no match says so and offers Clear search.
- A load failure is a library alert with Try again.

## Journey that proves it
tests/smoke/test_governance_dashboard_journey.py: the three figures equal the rows listed; the stat row has at most four cards, no icons or coloured borders; a row opens its editor and the change persists after a reload; an empty model says why and the link lands on content; nothing overflows at 390.
