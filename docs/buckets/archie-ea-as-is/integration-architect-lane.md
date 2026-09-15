# As-Is: Integration Architecture lane

Author: `integration-architect`. Input: `current-state.md`.

## Deploy pipeline duplication

Two deploy paths exist and are **not reconciled**:
- `scripts/deploy_verified.sh` — bind-mount source-checkout, `git fetch` +
  `docker compose up -d --force-recreate`, then independently proves health,
  bind-mount presence, and running commit via `/version`'s `build_id`. This
  is what production actually runs (verified 2026-09-08).
- `deploy/deploy.sh` + `scripts/deploy.sh` — immutable GHCR-digest pipeline,
  `deploy/docker-compose.production.yml` stripping all bind mounts. Real and
  committed, but **not** live.

This is the one piece of "what does this call, and what does it do when
that's slow/absent/lying" (this lane's standing question) that has a clean
answer today: nothing calls the GHCR pipeline in production right now, so
its failure modes are untested in practice, whatever they look like on
paper.

## Blueprint registration as an internal integration seam

`init_blueprints` treats each blueprint's registration as a soft-fail
integration point — a broken module degrades one feature rather than
crashing boot. This is the right failure posture for an internal seam, but
its blast radius (any `url_for()` to the failed endpoint 500s the sidebar
everywhere) means the safety net (`_validate_critical_endpoints`,
`boot-health` gate) is load-bearing, not decorative.

## External integrations

`app/modules/ai_chat/services/llm_router.py` is the one real external-API
integration point in the codebase (LLM calls). No other outbound
third-party API integration is evident from the module list surveyed. The
new (uncommitted) `interface_register` module's name suggests SAP S4
integration intent, but its actual routes/services were not audited for
external calls in this pass — flagging as unaudited rather than assuming
either way, since that module's disposition is still undecided.

## To-be direction (this lane's opinion)

1. Pick one deploy pipeline as canonical and retire the other, rather than
   maintaining two indefinitely — every day both exist is a day someone can
   reasonably assume the wrong one is live (as already happened once,
   documented in `CLAUDE.md`: `docker compose restart` treated as a deploy
   and silently no-op'd for 6+ consecutive attempts).
2. If `interface_register` is kept, audit it for real external SAP
   connectivity vs. a locally-modeled register with no live integration —
   the name implies more than what may actually be implemented.

**Handoff:** to `tech-lead`, alongside the other three lane docs.
