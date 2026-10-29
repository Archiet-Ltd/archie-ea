# Technology radar: place an element - redesign spec

Block: Record detail. One element and one short form, following the shadcn forms example
(https://ui.shadcn.com/examples/forms).

## Job
An architect places one technology on the radar: the ring, why, and when to look at it again.

## What moves
- The form is one column, at most 42rem wide. Each field has its label: Ring, Rationale, Review by.
- Save is the one primary action. Cancel returns to the radar.

## What merges
- The page header states the name, type and current ring of the element once.

## What is removed
- Nothing is hidden.

## States
- Empty: for an element not yet on the radar, the first option reads "Choose a ring".
- Error: a missing ring is refused with a plain message beside the field, and the text already entered is kept.

## Journey that proves it
An architect opens an element from the radar, chooses a ring and saves. The radar then shows the element in that ring.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
