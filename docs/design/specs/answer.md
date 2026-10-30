# Answer: one stored answer - redesign spec

Block: Chat. The cascade/affected table sits inside the answer's own facts card, following
docs/design/specs/ask.md's "a table inside a message" ruling, the shadcn playground example
(https://ui.shadcn.com/examples/playground).

## Job

A person reopens a saved answer and reads exactly what was worked out, with nothing recomputed.

## What moves

- The "Affected" table (element, reached from, steps away, owner, basis, read from) is the shared
  table component (basecoat/table.html) in place of a hand-written table.

## What merges

- Nothing.

## What is removed

- Nothing is hidden.

## States

- No rows, risk question: the shared empty state, "No open risk reaches this".
- No rows, other questions: the shared empty state, "Nothing is recorded as depending on this yet".
- Risks: unchanged, a list of cards (no table in this branch).

## Journey that proves it

A CTO reopens a saved "what breaks together" answer and the affected table lists the same rows,
in the same order, as when it was first worked out; reloading changes nothing.

Every control on the page is a component from the shared library: no hand-written button, input,
select, table or dialog, no raw colour, no shadow and no inline style. The page is judged at 1440
and 390 wide, in light and dark.
