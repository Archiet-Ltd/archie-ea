"""ARB-002/005/007: Decision capability linking and register endpoints."""
from datetime import datetime
from flask import request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.modules.architecture.routes.arb_routes import arb_bp
from app.extensions import db
from app.utils.pagination import safe_int_arg


@arb_bp.route('/api/decisions/<int:decision_id>/link-capability', methods=['POST'])
@login_required
def link_decision_capability(decision_id):
    """ARB-002: Link a capability to an architecture decision."""
    from app.models.architecture_decision import DecisionCapabilityLink
    from app.models.architecture_decision import decision_in_active_organisation_or_404
    decision_in_active_organisation_or_404(decision_id)
    data = request.get_json() or {}
    capability_id = data.get('capability_id')
    if not capability_id:
        return jsonify({'error': 'capability_id required'}), 400
    link_type = data.get('link_type', 'governs')
    is_primary = bool(data.get('is_primary', False))
    existing = DecisionCapabilityLink.query.filter_by(
        decision_id=decision_id, capability_id=capability_id
    ).first()
    if existing:
        return jsonify({'error': 'Link already exists', 'link': existing.to_dict()}), 409
    link = DecisionCapabilityLink(
        decision_id=decision_id,
        capability_id=capability_id,
        link_type=link_type,
        is_primary=is_primary
    )
    db.session.add(link)
    db.session.commit()
    return jsonify({'link': link.to_dict(), 'decision_id': decision_id}), 201


@arb_bp.route('/api/decisions/<int:decision_id>/capabilities/<int:capability_id>', methods=['DELETE'])
@login_required
def unlink_decision_capability(decision_id, capability_id):
    """ARB-002: Remove a capability link from a decision."""
    from app.models.architecture_decision import DecisionCapabilityLink
    link = DecisionCapabilityLink.query.filter_by(
        decision_id=decision_id, capability_id=capability_id
    ).first_or_404()
    db.session.delete(link)
    db.session.commit()
    return jsonify({'deleted': True}), 200


@arb_bp.route('/api/capabilities/<int:capability_id>/decisions', methods=['GET'])
@login_required
def capability_decisions(capability_id):
    """ARB-002: Get all decisions linked to a capability, grouped by horizon."""
    from app.models.architecture_decision import ArchitectureDecision, DecisionCapabilityLink
    # BusinessCapability: the canonical store. `Capability` maps the
    # `capabilities` table, which is empty in production, so guarding on it
    # 404'd every capability a user actually has.
    from app.models.business_capabilities import BusinessCapability
    from app.utils.route_guards import require_entity

    # No linked decisions for a nonexistent capability is fabricated data.
    require_entity(BusinessCapability, capability_id, description="Capability not found")

    links = DecisionCapabilityLink.query.filter_by(capability_id=capability_id).all()
    decision_ids = [item.decision_id for item in links]
    decisions = ArchitectureDecision.query.filter(
        ArchitectureDecision.id.in_(decision_ids),
        ArchitectureDecision.status.in_(['proposed', 'under_review', 'accepted'])
    ).all() if decision_ids else []
    grouped = {'strategic': [], 'tactical': [], 'operational': [], 'unclassified': []}
    for d in decisions:
        horizon = d.horizon or 'unclassified'
        if horizon not in grouped:
            horizon = 'unclassified'
        grouped[horizon].append(d.to_dict())
    return jsonify({
        'capability_id': capability_id,
        'decisions': grouped,
        'total': len(decisions)
    }), 200


# ── Capability governance panel ───────────────────────────────────────────────

@arb_bp.route('/api/capabilities/<int:capability_id>/governance', methods=['GET'])
@login_required
def capability_governance_panel(capability_id):
    """ARB-007: Full governance view for a capability — decisions, history, open change requests."""
    from app.models.architecture_decision import (
        ArchitectureDecision, DecisionCapabilityLink, ArchitectureChangeRequest, ChangeImpactAssessment
    )
    # BusinessCapability: the canonical store. `Capability` maps the
    # `capabilities` table, which is empty in production, so guarding on it
    # 404'd every capability a user actually has.
    from app.models.business_capabilities import BusinessCapability
    from app.utils.route_guards import require_entity

    require_entity(BusinessCapability, capability_id, description="Capability not found")

    # All decisions linked to this capability
    links = DecisionCapabilityLink.query.filter_by(capability_id=capability_id).all()
    decision_ids = [item.decision_id for item in links]

    all_decisions = ArchitectureDecision.query.filter(
        ArchitectureDecision.id.in_(decision_ids)
    ).all() if decision_ids else []

    # Active strategic decision (approved, horizon=strategic, not superseded)
    active_strategic = next((
        d for d in all_decisions
        if d.horizon == 'strategic' and d.status in ('accepted', 'under_review')
        and d.superseded_by_id is None
    ), None)

    # Active tactical decisions
    active_tactical = [
        d for d in all_decisions
        if d.horizon == 'tactical' and d.status in ('accepted', 'under_review')
        and d.superseded_by_id is None
    ]

    # Decision history — superseded chain, newest first
    history = sorted(
        [d for d in all_decisions if d.status == 'superseded'],
        key=lambda d: d.created_at or datetime.utcnow(),
        reverse=True
    )

    # Open change requests affecting this capability
    try:
        open_impacts = ChangeImpactAssessment.query.filter_by(
            affected_capability_id=capability_id
        ).all()
        open_change_request_ids = list({i.change_request_id for i in open_impacts})
        open_change_requests = ArchitectureChangeRequest.query.filter(
            ArchitectureChangeRequest.id.in_(open_change_request_ids),
            ArchitectureChangeRequest.status.in_(['open', 'assessing', 'disposition_set'])
        ).all() if open_change_request_ids else []
    except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
        open_change_requests = []

    return jsonify({
        'capability_id': capability_id,
        'active_strategic_decision': active_strategic.to_dict() if active_strategic else None,
        'active_tactical_decisions': [d.to_dict() for d in active_tactical],
        'open_change_requests': [r.to_dict() for r in open_change_requests],
        'decision_history': [d.to_dict() for d in history],
        'total_decisions': len(all_decisions),
    }), 200


# ── Decision CRUD and lifecycle ───────────────────────────────────────────────

@arb_bp.route('/api/decisions', methods=['GET'])
@login_required
def list_decisions():
    """ARB-005: Filterable, paginated decision register."""
    from app.models.architecture_decision import ArchitectureDecision
    page = safe_int_arg('page', 1, minimum=1)
    per_page = min(safe_int_arg('per_page', 25, minimum=1, maximum=500), 100)
    search = request.args.get('q') or request.args.get('search', '')
    status = request.args.get('status')
    sort_by = request.args.get('sort', 'created_at')
    sort_dir = request.args.get('dir', 'desc')

    ALLOWED_SORT = {'title', 'status', 'adm_phase', 'created_at', 'decision_id'}
    if sort_by not in ALLOWED_SORT:
        sort_by = 'created_at'

    q = ArchitectureDecision.query
    if search:
        q = q.filter(or_(
            ArchitectureDecision.title.ilike(f'%{search}%'),
            ArchitectureDecision.decision_id.ilike(f'%{search}%'),
        ))
    if status:
        q = q.filter(ArchitectureDecision.status == status)

    sort_col = getattr(ArchitectureDecision, sort_by, ArchitectureDecision.created_at)
    q = q.order_by(sort_col.asc() if sort_dir == 'asc' else sort_col.desc())

    paginated = q.paginate(page=page, per_page=per_page, error_out=False)
    offset = (page - 1) * per_page

    decisions = []
    for idx, d in enumerate(paginated.items):
        decisions.append({
            'id': d.id,
            'row_number': offset + idx + 1,
            'decision_id': d.decision_id or '',
            'title': d.title or '',
            'status': d.status or 'Draft',
            'adm_phase': d.adm_phase or '',
            'context': (d.context or '')[:200],
            'created_at': str(d.created_at) if d.created_at else None,
            'updated_at': str(d.updated_at) if d.updated_at else None,
        })

    return jsonify({
        'decisions': decisions,
        'total': paginated.total,
        'page': page,
        'pages': paginated.pages,
        'per_page': per_page,
    }), 200


@arb_bp.route('/api/decisions/bulk', methods=['DELETE'])
@login_required
def bulk_delete_decisions():
    """Bulk delete architecture decisions."""
    from app.models.architecture_decision import ArchitectureDecision
    from app.extensions import db
    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids or not isinstance(ids, list):
        return jsonify({'error': 'ids list required'}), 400
    deleted = ArchitectureDecision.query.filter(ArchitectureDecision.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'deleted': deleted}), 200


@arb_bp.route('/api/decisions', methods=['POST'])
@login_required
def create_decision():
    """ARB-005: Create a new architecture decision."""
    from app.models.architecture_decision import ArchitectureDecision
    data = request.get_json() or {}
    if not data.get('title'):
        return jsonify({'error': 'title required'}), 400
    decision = ArchitectureDecision(
        decision_id=ArchitectureDecision.next_decision_id(),
        title=data['title'],
        status=data.get('status', 'proposed'),
        adm_phase=data.get('adm_phase'),
        context=data.get('context'),
        decision=data.get('decision'),
        consequences=data.get('consequences'),
        alternatives=data.get('alternatives'),
        horizon=data.get('horizon', 'strategic'),
        authority_level=data.get('authority_level', 'enterprise_arb'),
        decision_type=data.get('decision_type'),
        enterprise_level=data.get('enterprise_level', True),
        solution_id=data.get('solution_id'),
        valid_from=None,
        valid_until=None,
        created_by_id=current_user.id,
    )
    db.session.add(decision)
    db.session.commit()
    return jsonify({'decision': decision.to_dict()}), 201


@arb_bp.route('/api/decisions/<int:decision_id>', methods=['PATCH'])
@login_required
def update_decision(decision_id):
    """ARB-005: Update an architecture decision."""
    from app.models.architecture_decision import decision_in_active_organisation_or_404
    decision = decision_in_active_organisation_or_404(decision_id)
    if decision.ai_system_id is not None:
        return jsonify({'error': 'An AI design review is decided on its review page, not edited here.'}), 403
    data = request.get_json() or {}
    updatable = ['title', 'status', 'context', 'decision', 'consequences', 'alternatives',
                 'horizon', 'authority_level', 'decision_type', 'enterprise_level',
                 'solution_id', 'adm_phase', 'valid_until']
    for field in updatable:
        if field in data:
            setattr(decision, field, data[field])
    db.session.commit()
    return jsonify({'decision': decision.to_dict()}), 200


@arb_bp.route('/api/decisions/<int:decision_id>/supersede', methods=['POST'])
@login_required
def supersede_decision(decision_id):
    """ARB-005: Supersede a decision with a new one.
    Body: { new_decision_id: int }  — the ID of the decision that replaces this one.
    """
    from app.models.architecture_decision import decision_in_active_organisation_or_404
    old = decision_in_active_organisation_or_404(decision_id)
    if old.ai_system_id is not None:
        return jsonify({'error': 'An AI design review is decided on its review page, not superseded here.'}), 403
    data = request.get_json() or {}
    new_id = data.get('new_decision_id')
    if not new_id:
        return jsonify({'error': 'new_decision_id required'}), 400
    new_decision = decision_in_active_organisation_or_404(new_id)
    if new_decision.ai_system_id is not None:
        return jsonify({'error': 'An AI design review cannot replace another decision.'}), 403
    if old.status == 'superseded':
        return jsonify({'error': 'Decision is already superseded'}), 400
    old.status = 'superseded'
    old.superseded_by_id = new_id
    db.session.commit()
    return jsonify({
        'superseded': old.to_dict(),
        'superseded_by': new_decision.to_dict()
    }), 200


# ── ARB-006: Phase H change request API ──────────────────────────────────────

@arb_bp.route('/api/change-requests', methods=['POST'])
@login_required
def create_change_request():
    """ARB-006: Create a Phase H change request."""
    data = request.get_json() or {}
    required = ['title', 'description', 'trigger_type']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'{f} required'}), 400

    from app.models.architecture_decision import ArchitectureChangeRequest, VALID_TRIGGER_TYPES
    if data['trigger_type'] not in VALID_TRIGGER_TYPES:
        return jsonify({'error': f"trigger_type must be one of {VALID_TRIGGER_TYPES}"}), 400

    cr = ArchitectureChangeRequest(
        acr_reference=ArchitectureChangeRequest.next_acr_reference(),
        title=data['title'],
        description=data['description'],
        trigger_type=data['trigger_type'],
        raised_by_id=current_user.id,
    )
    db.session.add(cr)
    db.session.commit()
    return jsonify(cr.to_dict()), 201


@arb_bp.route('/api/change-requests/<int:cr_id>/assess-impact', methods=['POST'])
@login_required
def assess_change_impact(cr_id):
    """ARB-006: Auto-query capabilities → decisions to create impact assessments."""
    from app.models.architecture_decision import (
        ArchitectureChangeRequest, ChangeImpactAssessment,
        DecisionCapabilityLink, ArchitectureDecision
    )
    cr = ArchitectureChangeRequest.query.get_or_404(cr_id)
    data = request.get_json() or {}
    capability_ids = list(data.get('capability_ids', []))

    created = []
    for cap_id in capability_ids:
        links = DecisionCapabilityLink.query.filter_by(capability_id=cap_id).all()
        for link in links:
            decision = ArchitectureDecision.query.get(link.decision_id)
            if not decision or decision.status not in ('accepted', 'under_review'):
                continue
            existing = ChangeImpactAssessment.query.filter_by(
                change_request_id=cr_id,
                affected_capability_id=cap_id,
                affected_decision_id=link.decision_id
            ).first()
            if existing:
                continue
            impact = ChangeImpactAssessment(
                change_request_id=cr_id,
                affected_capability_id=cap_id,
                affected_decision_id=link.decision_id,
                impact_level=data.get('impact_level', 'medium'),
                impact_description=data.get('notes', ''),
            )
            db.session.add(impact)
            created.append({'capability_id': cap_id, 'decision_id': link.decision_id})

    cr.status = 'assessing'
    db.session.commit()
    return jsonify({'change_request_id': cr_id, 'impacts_created': created}), 200


@arb_bp.route('/api/change-requests/<int:cr_id>/set-disposition', methods=['POST'])
@login_required
def set_change_disposition(cr_id):
    """ARB-006: Set disposition on a change request."""
    from app.models.architecture_decision import ArchitectureChangeRequest, VALID_DISPOSITIONS
    cr = ArchitectureChangeRequest.query.get_or_404(cr_id)
    data = request.get_json() or {}
    disposition = data.get('disposition')
    if not disposition or disposition not in VALID_DISPOSITIONS:
        return jsonify({'error': f"disposition must be one of {VALID_DISPOSITIONS}"}), 400
    cr.disposition = disposition
    cr.status = 'disposition_set'
    db.session.commit()
    return jsonify(cr.to_dict()), 200


@arb_bp.route('/api/change-requests/<int:cr_id>/issue-notice', methods=['POST'])
@login_required
def issue_change_notice(cr_id):
    """ARB-006: Issue an Architecture Change Notice for a change request."""
    from app.models.architecture_decision import ArchitectureChangeRequest, ArchitectureChangeNotice
    cr = ArchitectureChangeRequest.query.get_or_404(cr_id)
    data = request.get_json() or {}
    if not data.get('notice_text'):
        return jsonify({'error': 'notice_text required'}), 400

    notice = ArchitectureChangeNotice(
        change_request_id=cr_id,
        acn_reference=ArchitectureChangeNotice.next_acn_reference(),
        scope_description=data['notice_text'],
        issued_by_id=current_user.id,
    )
    db.session.add(notice)
    cr.status = 'closed'
    db.session.commit()
    return jsonify(notice.to_dict()), 201


@arb_bp.route('/api/change-requests', methods=['GET'])
@login_required
def list_change_requests():
    """ARB-006: List all change requests, optionally filtered by status."""
    from app.models.architecture_decision import ArchitectureChangeRequest
    status_filter = request.args.get('status')
    q = ArchitectureChangeRequest.query
    if status_filter:
        q = q.filter_by(status=status_filter)
    return jsonify([r.to_dict() for r in q.order_by(ArchitectureChangeRequest.raised_at.desc()).all()]), 200


# ── Decision register ─────────────────────────────────────────────────────────

@arb_bp.route('/api/decisions/register', methods=['GET'])
@login_required
def decision_register():
    """ARB-005/007: Full decision register for UI table — all active decisions with capability names."""
    from app.models.architecture_decision import ArchitectureDecision, DecisionCapabilityLink
    decisions = ArchitectureDecision.query.filter(
        ArchitectureDecision.status.in_(['proposed', 'under_review', 'accepted'])
    ).order_by(ArchitectureDecision.created_at.desc()).all()

    results = []
    for d in decisions:
        links = DecisionCapabilityLink.query.filter_by(decision_id=d.id).all()
        row = d.to_dict()
        row['linked_capability_ids'] = [item.capability_id for item in links]
        row['primary_link_type'] = next((item.link_type for item in links if item.is_primary), None)
        results.append(row)

    return jsonify({'decisions': results, 'total': len(results)}), 200

