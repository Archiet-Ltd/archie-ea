# CTO scorecard - rebuild spec

Block: Dashboard. Four panels on the shadcn dashboard card, one answer each.

## Job
A CTO reads how much of the estate is on supported technology, which exceptions are open, what reaches end of support
soon and where screens disagree, and acts on an exception without leaving the page.

## What moves
- Every panel sits in the library card; Escalate is the library button and Escalated is the library badge.

## What merges
- Nothing: each panel still reads its one store.

## What is removed
- Nothing is hidden. A missing share used to end in a bare dash; it now says why there is no share and offers the
  next step, recording a vendor product on an application.

## States
- Empty share: the reason and the next step. No open exceptions, nothing reaching end of support, no disagreement:
  each says so in words.

## Journey that proves it
A CTO opens the scorecard in an organisation with two applications on technology products, reads 1 of 2 supported and
the exposure list, escalates the open exception and sees it marked after a reload, at 1440 and 390 wide; an empty
organisation reads the reason and follows the next step to a page with content.

Every control is a component from the shared library: no hand-written button, input, select, table or dialog, no raw
colour, no shadow and no inline style.
