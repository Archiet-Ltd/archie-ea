# Edit your application - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
An application owner keeps the description, status and accountable people current.

## What moves
- Three field sets, Description, Status and Accountability, in one card; an unset select reads Not set.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- An AI suggestion is marked and still needs the owner to save.

## Journey that proves it
An application owner edits the record, saves, and the saved values come back in the form.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
