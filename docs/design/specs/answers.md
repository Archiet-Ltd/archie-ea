# Answers: ask and keep the answer - redesign spec

Block: Data table. The question form above the tasks table example
(https://ui.shadcn.com/examples/tasks).

## Job

A startup CTO asks what breaks if several elements fail together, or which risks reach one
element, and keeps the answer at an address they can open again.

## What moves

- The question choice is the shared radio group, not two hand-written radio labels.
- The chip removing a chosen element, and the submit action, are the shared button component.
- The saved-answers list is the shared list table: a toolbar, sortable columns (Question, Rows,
  Computed) and the full pagination footer, in place of a hand-rolled list of cards.

## What merges

- Nothing. The ask form and the saved list stay two sections on one page.

## What is removed

- Nothing is hidden.

## States

- Empty, no organisation: says answers belong to an organisation and to switch to one.
- Empty, nothing asked yet: says ask a question above and offers no action beyond that.
- Error: the ask button's own alert names what went wrong without losing the chosen elements.

## Journey that proves it

A CTO types two element names, picks "What breaks if these fail together?", asks, and is taken to
the stored answer. Reopening Answers lists it at its permanent address with the right row count.

Every control on the page is a component from the shared library: no hand-written button, input,
select, table or dialog, no raw colour, no shadow and no inline style. The page is judged at 1440
and 390 wide, in light and dark.
