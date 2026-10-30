# Architecture vision deliverable - redesign spec

Block: Form. Follows the shadcn forms example (https://ui.shadcn.com/examples/forms): one column in the page column,
at most 42rem wide, inside a card; every field has its label above it.

## Job
An architect reads the vision deliverable section by section beside its readiness and traceability.

## What moves
- Section fields are labelled read-only inputs; Save Draft, Submit for Review and Export are disabled library buttons that say they are not available yet.
- Save is the one primary action; Cancel returns to where the person came from.

## What merges
- Nothing is repeated: a hint says what to enter once, beside the field.

## What is removed
- Nothing is hidden. Hand-written buttons, inputs, selects and textareas become library controls.

## States
- Not set shows for an empty field; blockers are listed in words under Readiness Summary.

## Journey that proves it
An architect opens the vision deliverable and reads its sections, readiness and handoffs.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
