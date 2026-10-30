# Licence entitlement - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
A procurement lead records how many licences a contract entitles and how many are deployed.

## What moves
- Two field sets, Licence and Quantities, in one card; the contract is required; compliance is calculated on save.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- A missing contract is refused by the browser; a bad number returns the form filled with the message.

## Journey that proves it
Procurement records a licence, sees it judged against its contract, edits it and the change stays.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
