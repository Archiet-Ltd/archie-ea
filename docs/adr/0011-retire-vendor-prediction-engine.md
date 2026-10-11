# ADR 0011 — Retire the vendor performance prediction engine

- **Status:** Accepted
- **Date:** 2026-09-26

## Context

`AIRecommendationEngine` (`app/modules/ai_chat/services/ai_recommendation_engine.py`)
included a `_predict_vendor_performance` method whose "prediction" was fixed
arithmetic on the inputs, not a model output — it read as a forecast without
being one.

The class was re-exported from `app/modules/ai_chat/services/ai_analysis_service.py`
and named in an example import line in the module docstring of
`app/modules/ai_chat/services/__init__.py`, but nothing constructed it: no
route, service, scheduled job or CLI command instantiated
`AIRecommendationEngine`, so it was unreachable from the running product.

### Evidence

```
$ git grep -n "AIRecommendationEngine\|ai_recommendation_engine" origin/main -- app tests scripts manage.py docs
origin/main:app/modules/ai_chat/services/__init__.py:13:    from app.modules.ai_chat.services.ai_analysis_service import AIRecommendationEngine
origin/main:app/modules/ai_chat/services/ai_analysis_service.py:7:- ai_recommendation_engine (AIRecommendationEngine)
origin/main:app/modules/ai_chat/services/ai_analysis_service.py:23:from app.modules.ai_chat.services.ai_recommendation_engine import (  # noqa: F401
origin/main:app/modules/ai_chat/services/ai_analysis_service.py:24:    AIRecommendationEngine,
origin/main:app/modules/ai_chat/services/ai_recommendation_engine.py:31:class AIRecommendationEngine:
```

Every hit is the module itself, its re-export in `ai_analysis_service.py`, the
docstring lines listing it in both files, or the example import in
`__init__.py`'s "Usage" docstring — no caller.

## Decision

Remove the module and its re-export:

- delete `app/modules/ai_chat/services/ai_recommendation_engine.py`
- remove the re-export in `app/modules/ai_chat/services/ai_analysis_service.py`
  (`from ... import AIRecommendationEngine`) and its docstring line
- remove the example import line naming it from the module docstring in
  `app/modules/ai_chat/services/__init__.py`

## Successor

Vendor performance forecasting is planned as its own service in Release 3
(story TB-0117, the forecasting service). This removal does not block that
work: the engine was never wired to anything, so nothing needs to be
repointed before the successor lands.

## Consequences

- No behaviour change: the class was never reachable, so no route, job or
  test exercised it.
- `tests/test_vendor_prediction_engine_retired.py` pins the removal — it
  asserts the module can no longer be imported, the class is no longer
  exported, and `app.modules.ai_chat.services` still imports cleanly.

## Reversible variant

If the removed logic is ever needed again before the forecasting service
ships, find the commit that deleted the file and restore its last version:

```
git log --diff-filter=D --format=%H -1 -- app/modules/ai_chat/services/ai_recommendation_engine.py
git show <that sha>^:app/modules/ai_chat/services/ai_recommendation_engine.py \
  > app/modules/ai_chat/services/ai_recommendation_engine.py
```
