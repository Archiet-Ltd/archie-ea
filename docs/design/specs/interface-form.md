# Interface register entry - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
A solution architect records one interface and the two applications it couples.

## What moves
- Interface and Coupling field sets in one card; provider and consumer are searched, never typed.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- Required fields are refused by the browser; a server refusal shows in an alert above the form with the entries kept.

## Journey that proves it
An architect registers an interface between two applications and finds both on its edit form.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
