# Solution record - redesign spec

Block: Record detail. The header and tabs follow the shadcn dashboard example
(https://ui.shadcn.com/examples/dashboard). The facts beside the work follow the flask-basecoat ported example
"product detail".

## Job
A CTO or architect opens one solution and sees what it changes, what it costs, what is at risk and who signed off.
They then act on it without leaving the page.

## What moves
- The header carries two actions and a More menu. Nothing else sits in the header row.
- Overview puts what the solution changes and what it costs side by side. The design score appears once, as a figure.
- The old notes page (/solutions/<id>/communications) redirects to the Notes section of this record. It first runs
  the same organisation check as every other communication route.

## What merges
- Properties that are not set are named once, in one line, instead of one empty row each.

## What is removed
- Nothing is hidden.

## States
- Empty: a tab with nothing recorded says why and offers the next step.
- Loading: tab content shows a skeleton on first open.
- Error: when comments cannot be read, the page says "The comments could not be loaded. Reload the page to try
  again." instead of "No comments yet". A failed action shows a toast and leaves the page unchanged.

## Journey that proves it
A CTO opens a solution from the list, reads its Overview, opens each tab and adds a note. Every element link lands on
the page of the element it names.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
