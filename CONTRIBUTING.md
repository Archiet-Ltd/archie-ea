# Contributing to Entelim

Entelim is an open-source, AI-native enterprise architecture platform (TOGAF 9.2 /
ArchiMate 3.2). Contributions are welcome — bug fixes, features, docs, connectors,
and codegen targets.

## License of contributions

Entelim is **AGPL-3.0** with a commercial dual-licence. By submitting a contribution
you agree it is licensed under AGPL-3.0, and — so the dual-licence remains viable —
that the maintainers may also offer it under the commercial licence. If your
employer owns your work, get their sign-off first.

## Local setup

```bash
git clone <your-fork-url> && cd archie
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # then edit secrets
createdb archie                                    # create the PostgreSQL database
flask --app manage init-db                         # schema via db.create_all (no migrations)
python create_admin.py                             # first admin user from .env
flask --app manage run                             # or: docker compose up
```

A PostgreSQL database is required. The fastest path is `docker compose up`, which
brings up the app + Postgres together. On the Windows dev box a portable
PostgreSQL on **port 5439** is used instead — start/stop commands and the
`TEST_DATABASE_URL` for running the suite are in
[docs/local-test-database.md](docs/local-test-database.md).

## Before you open a PR

1. **Run the tests:** `pytest -q`. New behaviour needs a test.
2. **No secrets, ever.** No credentials, internal hostnames/IPs, or real customer
   data — not even "anonymized" samples. CI runs a `gitleaks` scan over full history
   and will fail the PR. User uploads live under `app/uploads/` and are gitignored;
   keep it that way.
3. **No debug leaks in shipped code:** no `console.log` in templates/JS (use the
   `Platform.toast` design-system notifications, never native `alert()`/`confirm()`),
   no stray `print()` in request handlers, no raw `db.session.execute("…")` strings
   (wrap in `db.text(...)` — bare strings raise under SQLAlchemy 2.0).
4. **Keep it accessible:** icon-only buttons need `aria-label`; images need `alt`.
5. **Lint** (advisory): `ruff check .`.

## Pull request process

- Branch from `main`, keep PRs focused (one concern).
- Describe what changed and why; link any issue.
- Stage specific files (`git add <file>`), not `git add -A`.
- CI must pass (secret-scan + tests) before review.

## Reporting bugs / requesting features

Open an issue using the templates in `.github/ISSUE_TEMPLATE/`. For **security**
issues, do **not** open a public issue — see [SECURITY.md](SECURITY.md).

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
