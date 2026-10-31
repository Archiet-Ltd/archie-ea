# Vendor Capability Matrix - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
An architect picks a product category and reads how well each vendor product covers each capability, then opens a cell for the gaps and strengths behind it.

## What moves
- Four figures become the Stat card row: capabilities, vendors, average coverage and strong fits, each counted from the cells the matrix shows, each with a sentence. No icons, no coloured borders.
- The filters sit in one card with the library's field, input and native select; the coverage scale is the legend under them.
- The matrix is a library table. Each step of the scale is a theme token (a single hue, calm to strong), never a raw colour, and each cell is a real button that opens that coverage.
- The cell detail is the library dialog: coverage, out of the box, with customisation, complexity, gaps, strengths and evidence.

## What is fixed
- A product with no coverage for a capability says "No data" in the cell instead of a bare dash.
- Before a category is chosen, and when a filter leaves nothing, the page says why and offers the next step (choose a category, clear the filters).

## States
- Loading, error with Try again, no category chosen, filters leave nothing, populated.

## Journey that proves it
tests/smoke/test_vendor_matrix_journey.py: a cell equals the coverage on the vendor's own page; the figures are counted from the cells; a cell opens its detail; the stat row has at most four cards with no icons or coloured borders and the scale uses tokens with a legend; an empty filter says why and clears; the page asks for a category first.
