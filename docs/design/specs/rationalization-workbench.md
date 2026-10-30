# Decision workbench - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
A portfolio manager triages the scored applications: how many are scored and ready to decide, what the scores
recommend, and which applications to open, compare or approve in bulk.

## What moves
- Four figures become the Stat card row: the portfolio's applications (the Applications list's own count), scored,
  decision ready, and awaiting review.
- The disposition split becomes the primary chart with its figures table beside the seven-option guide, on the Chart grid.
- Filters, bulk actions and the table use the library input, native select, button and table; the paging is the card footer.
- The score breakdown becomes the library sheet, opened by a row's View button.
- The comparison panel is a card with a radar chart and the library table.

## What is fixed
- The breakdown sheet reads the workbench's state although the runtime moves it to the page body.
- Missing values read "Not set" or "Not measured", never a dash.

## States
- No scored applications: the chart card and the table say so and point to detection and scoring on the Dashboard.
- Scores unreadable: the chart card says so and offers a reload; the figure cards say Not available.

## Journey that proves it
tests/smoke/test_rationalization_family_journey.py: the application count equals the Applications list on workbench,
planning and tracking; the scored, ready and awaiting figures, the chart's figures table and the rows agree; View
opens the breakdown sheet and the row opens its planning page.
