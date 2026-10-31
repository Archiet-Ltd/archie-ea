# Webhook settings - redesign spec

Block: Settings. One column, each group of settings in a card at most 42rem wide.

## Job
A platform administrator sends event notifications to Slack, Teams or any address, and finds out whether a subscription
works.

## What moves
- Active subscriptions, the add form and the setup notes become three cards in one column, in that order.
- Every field has its label: Webhook Type, Webhook URL, Description, Signing Secret, and one checkbox per event.
- Each subscription row has Send test and Delete as labelled buttons; the type is an outline badge.
- Save subscription is the one primary action of the add card; Cancel returns to Admin.

## What merges
- Nothing. Two flash styles become one alert component.

## What is removed
- Nothing is hidden. Icon-only test and delete buttons gain words.

## States
- Empty: no subscriptions says what to do next (add one, then Send test).
- Error: an address the platform refuses is refused in words and the form keeps what was typed.
- The signing secret is never shown after it is saved. A test answers in words, after one delivery attempt.

## Journey that proves it
An administrator adds a Slack subscription with a secret and an event, reloads and sees it listed without the secret;
types an address the platform refuses and reads why with the fields kept; presses Send test and reads the result.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
