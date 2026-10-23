# Ask question chips

Cause: the eight chips only opened the shared name picker. An answer was fetched only when a name was chosen, so after choosing an element under one chip, clicking another chip left the first answer on screen. The result heading was also fixed text.

Fix: the chosen element is remembered; opening another chip answers that chip's question for it straight away, through the same readers and answer blocks as before. Each result heading is the chip's question with the element's name in place of the blank.

| Chip | Question key | Reader called | Result block |
|---|---|---|---|
| What breaks if ... | impact | impact | ask-results |
| What are we trying to achieve at ... | strategy | strategy | ask-strategy-results |
| What could hurt ... | risk | risk | ask-risk-results |
| What is the rationalization status of ... | portfolio | portfolio component | ask-portfolio-results |
| What's changing at ... | programme | programme | ask-programme-results |
| What data does ... | data | data | ask-data-results |
| Which regulations and controls apply to ... | compliance | compliance | ask-compliance-results |
| Who's accountable for ... | accountability | accountability | ask-accountability-results |

Not done: screenshots on the demo organisation with Calibration Ledger selected were not captured (no demo organisation in the test database). The accountability reader still returns its "Ownership data is not yet connected." state; that is existing behaviour pinned by an existing test and was not changed.
