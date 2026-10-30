# My Applications dashboard - rebuild spec

Block: Dashboard. Follows the Overview dashboard and shadcn examples/dashboard.

## Job
An application owner sees how many applications they own, how those applications are doing, and which to open next.

## What moves
- Four figures become the Stat card row: the applications the person owns (the Applications list's own count), healthy, at risk and critical. No icons, no coloured borders.
- Applications by recorded health becomes the primary chart with its figures table, which also carries the applications not assessed yet and the link to record a status. The Health overview is one button on that card.
- The roadmap block becomes a card that points to Roadmap impact; the page no longer claims "No upcoming roadmap impacts", which it could not know.
- The owned applications sit in the library list table with the health state as a badge; View opens the application.
- The four navigation cards become page actions (My portfolio, All applications) and buttons inside the cards they belong to (Health overview, Roadmap impact). Every destination is still one click away.

## What is fixed
- A missing ownership type reads as an owner, and a missing health status as Not assessed, never a dash.

## States
- Owns nothing: the figures read zero, the health card says it appears once applications are owned, and the list card says why and offers Browse all applications.

## Journey that proves it
tests/smoke/test_my_applications_dashboard_journey.py: the count equals the owner's list and leaves out applications nobody here owns; the health figures equal the Health page's; the stat row has at most four cards with no icons; every old destination opens a page with content; an owner of nothing is told why.
