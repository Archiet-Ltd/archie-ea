# Capability mapping - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
A business architect maps applications to the capabilities they support.

## What moves
- Coverage cards, a row per capability, and a dialog of labelled checkboxes opened from each row.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- No application mapped says so on the row; Not measured stands for coverage with nothing to measure.

## Journey that proves it
An architect maps an application to a capability and the row lists it after reload.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
