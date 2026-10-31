# Security dashboard - redesign spec

Block: Settings. One column, each group in a card at most 42rem wide.

## Job
A platform administrator reads which security headers this deployment sends and what the rate limiter has counted.

## What moves
- Response headers and Rate limit become two cards; each table uses the shared table parts with headings that name
  their columns (Header and Value, Key and Count, timestamp). The old page headed both columns "Header name".

## What merges
- Nothing.

## What is removed
- Nothing. The page has no setting to change and none is added; the page says so beneath its title.

## States
- Not determined or not measured is said in a sentence that explains why, never as a bare dash.
- Empty: no requests counted yet says so.

## Journey that proves it
An administrator opens the page and reads the headers with their values under distinct headings and the limiter's state
in words; a security architect who is not a platform administrator is refused; nothing scrolls sideways on a phone.

Every control is a component from the shared library: no hand-written table, no raw colour, no shadow and no inline
style. Judged at 1440 and 390 wide, in light and dark.
