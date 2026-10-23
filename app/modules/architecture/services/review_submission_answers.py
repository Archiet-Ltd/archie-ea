"""R1-B74 slice 1: stored submission-review answers for an ARB review.

Maps a review to the permanent addresses of its ``set_impact`` and
``risk_propagation`` answers, each read from the one answer store at its
permanent address. Nothing here is recomputed: an answer already stored for
the same organisation and normalised parameters is re-read by its address;
otherwise it is asked and stored once.
"""

from __future__ import annotations

import datetime as _dt

from app.extensions import db
from app.middleware.tenant_context import current_org_id
from app.modules.intelligence.services.answer_envelope import (
    KIND_RISK_PROPAGATION,
    KIND_SET_IMPACT,
    AnswerNotAskable,
    ask_and_store,
    normalise_params,
    open_answer,
    present,
)
from sqlalchemy.dialects.postgresql import JSONB


def submission_review_answers(review):
    """Return the review's two stored submission answers.

    The review's board session carries ``impacted_element_ids``: the elements
    the submission touches. ``set_impact`` is asked with the whole element set
    (what fails if they fail together) and ``risk_propagation`` with the first
    element (the risks the change reaches through it). Each answer is asked
    with the review's organisation and at most once per review and parameter
    set per day: an answer already stored today for the same organisation and
    normalised parameters is re-read by its address; otherwise a new answer is
    asked and stored.

    Returns ``{"set_impact": <envelope or None>, "risk_propagation": <envelope or None>}``,
    each envelope the presented stored answer with its permanent address, or
    None when the review names no elements, or ``{"not_found": <reason>}`` when
    the answer cannot be asked (for example none of the named elements exists
    in the active organisation); the reason is the envelope's own.
    """
    board = review.arb_session
    element_ids = list(board.impacted_element_ids or []) if board is not None else []
    if not element_ids:
        return {"set_impact": None, "risk_propagation": None}

    org_id = current_org_id()
    if org_id is None:
        return {"set_impact": None, "risk_propagation": None}

    today = _dt.datetime.utcnow().date()
    answers = {"set_impact": None, "risk_propagation": None}

    for kind, raw_params in (
        (KIND_SET_IMPACT, {"element_ids": element_ids}),
        (KIND_RISK_PROPAGATION, {"element_id": element_ids[0]}),
    ):
        params = normalise_params(kind, raw_params)
        record = _stored_today(org_id, kind, params, today)
        if record is None:
            try:
                record = ask_and_store(kind, raw_params)
            except AnswerNotAskable as exc:
                answers[kind] = {"not_found": exc.reason}
                continue
        if record is not None:
            answers[kind] = present(open_answer(record.address))
    return answers


def _stored_today(org_id, kind, params, today):
    from app.models.answer_record import AnswerRecord

    start = _dt.datetime.combine(today, _dt.time.min)
    return db.session.execute(
        db.select(AnswerRecord).where(
            AnswerRecord.organization_id == org_id,
            AnswerRecord.question_kind == kind,
            db.cast(AnswerRecord.params, JSONB) == db.cast(params, JSONB),
            AnswerRecord.created_at >= start,
            AnswerRecord.created_at < start + _dt.timedelta(days=1),
        )
        .order_by(AnswerRecord.created_at.desc())
        .limit(1)
    ).scalars().first()
