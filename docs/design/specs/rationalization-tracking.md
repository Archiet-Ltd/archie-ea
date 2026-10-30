# Portfolio tracking - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
A portfolio manager sees what rationalisation has saved against what was projected, which applications carry
retirement risk, and the order they can retire in.

## What moves
- The two savings figures become the Stat card row (two cards, tabular figure, one-sentence meaning).
- Dependency risk becomes a card with the library table, a risk filter and the card footer paging.
- The retirement waves become library items inside one card.
- The import form becomes the library dialog with a labelled file field and a preview table.

## What is fixed
- The risk table read keys the endpoint does not send, so it always showed empty; it now reads the endpoint's own keys.
- The import result names the rows it could not read.

## States
- Empty dependency risk says that dependencies are imported to see what they block.
- Empty retirement sequence says it appears once applications are marked for retirement.

## Journey that proves it
tests/smoke/test_rationalization_family_journey.py: savings equal the benefit records, the risk table shows an
imported dependency, a second company's figures never appear.
