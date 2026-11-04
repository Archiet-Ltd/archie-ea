# Value streams at risk - rebuild spec

Block: Dashboard. One question answered in one place, following the shadcn dashboard example for the card and table.

## Job
A CTO sees which value streams depend on a capability below the maturity they choose, and opens the stream to act.

## What moves
- The threshold chooser, the answer cards and the table are built from the library's card, input, button, badge and
  table parts. The table sits in the library's table container so it scrolls inside its card on a phone.
- Each row gains an "Open value stream" link to the stream's own page.

## What merges
- Nothing: the page keeps its single answer; the figures are the intelligence API's own.

## What is removed
- Nothing is hidden.

## States
- Loading: a skeleton table. Error: a plain message with Try again. Nothing linked: the empty state names the next
  step, linking capabilities to value streams. Nothing below the threshold: said plainly.

## Journey that proves it
A CTO opens the page, sees each stream's linked-capability figure equal the capabilities on that stream's own page,
and opens each stream from its row to find its stages and capabilities there, at 1440 and 390 wide.

Every control is a component from the shared library: no hand-written button, input, select, table or dialog, no raw
colour, no shadow and no inline style.
