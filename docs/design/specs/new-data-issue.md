# Raise a data issue - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
A data architect reports a quality or governance problem on one data entity.

## What moves
- Entity number, title and description are labelled fields in one card; Cancel and Raise issue sit together at the end.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- Title and entity are required. A missing title is refused by the browser.

## Journey that proves it
A data architect raises an issue on an entity and finds it, with the entity name, on the issues list.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
