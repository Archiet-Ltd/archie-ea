# Architecture Health Scorecard - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
A CTO reads one health score and the four things behind it - solution maturity, risk, ArchiMate coverage and the review pipeline - and can check each figure against the screen it summarises.

## What moves
- The four headline figures (average solution maturity, solutions in the organisation, ArchiMate elements, open risks) become the Stat card row: tabular figure, one-sentence meaning, no icons.
- ArchiMate coverage by layer becomes the primary chart with its figures table; ADM phase distribution is the side panel as a table.
- The risk register by severity (severity as a state badge) and the ARB review pipeline are the equal pair of tables.
- The AI executive briefing becomes one card at the foot with the library button; it still writes nothing back.
- The score line under the title is unchanged: one value, one decimal, and what it was weighted from.

## What is fixed
- Open Risks no longer reads "0" when the risk register could not be read; it says Not available and why.
- The retired metrics card, table and page header macros are gone.

## States
- No elements: the layer card says they appear once a source is connected or an architect adds one.
- Phase data unreadable: the ADM card says so and offers a reload.
- Only unclassified solutions: average maturity reads as unmeasured and Unclassified holds 100.0%.

## Journey that proves it
tests/smoke/test_health_scorecard_journey.py: the score equals the executive summary's; open risks equal the four severity figures; the layer table adds up to the element total; the ADM table accounts for every solution; every link lands on a page with content; the briefing is still offered.
