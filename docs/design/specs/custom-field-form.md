# Custom field - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
An administrator defines a field every application carries.

## What moves
- Name, type, description and the required switch are field-set controls in one card with the errors beside each field.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- Each field shows its own validation message beside it.

## Journey that proves it
No route renders this page today, so no journey can open it; it follows the field names the create and edit routes read, ready for when a route renders it.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
