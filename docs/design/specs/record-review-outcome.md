# Record a review outcome - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
A reviewer says what a due review found and when the decision is next reviewed.

## What moves
- The original decision stays readable above the two fields; outcome and next review date are labelled with a line each.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- The outcome is required. An empty outcome is refused by the browser.

## Journey that proves it
An architect records an outcome and next review date and sees the outcome on the decision.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
