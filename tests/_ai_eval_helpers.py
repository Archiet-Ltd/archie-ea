"""Shared helper: give an AI system a current, passing evaluation (R1-B55).

Releasing an AI system needs a current evaluation run that meets the plan's
thresholds. Tests of the other parts of the release gate (assessment,
classification, ownership) use this to satisfy the evaluation part, so each
still tests the thing it names.
"""
from app.modules.ai_chat.services.ai_evaluation_service import record_run, set_plan


def passing_evaluation(element, organization_id, user_id=None):
    set_plan(
        element_id=element.id,
        organization_id=organization_id,
        test_set_name="Helper test set",
        test_set_size=10,
        thresholds={"groundedness": 0.8},
        user_id=user_id,
    )
    return record_run(
        element_id=element.id,
        organization_id=organization_id,
        metrics={"groundedness": 0.9},
        cases_run=10,
        user_id=user_id,
    )
