# SSO configuration (organisation) - redesign spec

Block: Settings. One column; the settings form and the test sign-in each sit in a card at most 42rem wide.

## Job
A platform administrator connects the organisation's identity provider (OIDC or SAML), turns single sign-on on, and
checks it with a test sign-in before enforcing it.

## What moves
- One Identity provider card: Protocol, Email Domain(s), then the fields of the chosen protocol (OIDC: discovery URL,
  client ID, client secret; SAML: SSO URL, signing certificate, entity ID), then the Enable switch.
- One primary action, Save configuration, in the card footer.
- Callback URLs and Test sign-in become their own cards beneath; the test result is an alert saying Success or Failed.

## What merges
- The enable checkbox becomes a switch with its label.

## What is removed
- Nothing is hidden. The fields of the protocol not chosen are folded away, not removed.

## States
- Empty: a new organisation sees empty fields and "No test sign-in has been run yet."
- Error: a setting the platform refuses is explained in words and the form keeps what was typed.
- A stored client secret is acknowledged ("Secret stored") and never written back into the field.

## Journey that proves it
An administrator saves an OIDC configuration, reloads and finds it saved with the secret acknowledged, not shown;
chooses SAML, turns SSO on without a certificate and is told what is missing, with the typed values kept.

Every control on the page is a component from the shared library: no hand-written button, input, select, table or
dialog, no raw colour, no shadow and no inline style. The page is judged at 1440 and 390 wide, in light and dark.
