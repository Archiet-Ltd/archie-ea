# Demo organisation: Lantern Quay Systems

`flask seed-demo-company` creates the demonstration organisation. Passwords come from the
`DEMO_USER_PASSWORD` environment variable at seed time and are not stored in the repository.

Accounts with a role beyond viewer:

| Account | Role | Persona |
|---|---|---|
| ivo.reed@lantern-quay.example.com | Organisation administrator | IT Operations Lead; opens the team page |
| sage.itops@lantern-quay.example.com | Architect | Transformation lead; links gaps, work packages and plateaus |

Every other seeded account, including demo@lantern-quay.example.com, is a read-only viewer.
