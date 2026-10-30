# Edit a solution - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
An architect updates what a solution is, the value it gives, its scope and who owns it.

## What moves
- Four field sets in one card; people are found by search; selects have a Select option that is refused until chosen.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- Required fields are refused by the browser.

## Journey that proves it
An architect edits a solution, saves, and the record and the form show the change.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
