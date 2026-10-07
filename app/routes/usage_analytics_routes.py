"""
Usage Analytics Dashboard Routes

Provides dashboard and API endpoints for viewing PARTIAL feature usage analytics.
Generates baseline reports for Phase 0 triage decision-making.
"""

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import login_required
from app.models.usage_analytics import UsageAnalytics
from app.utils.pagination import safe_int_arg

usage_analytics_bp = Blueprint('usage_analytics', __name__, url_prefix='/usage-analytics')


@usage_analytics_bp.route('/', methods=['GET'])
@login_required
def analytics_root():
    """Redirect root /usage-analytics to the dashboard."""
    return redirect(url_for('usage_analytics.analytics_dashboard'))


def _visible_events(query):
    """Events carry user, session, route and metadata, so a user sees only their own; a platform
    admin sees everyone's."""
    from flask_login import current_user

    from app.middleware.tenant_decorators import is_platform_admin

    if is_platform_admin(current_user):
        return query
    return query.filter(UsageAnalytics.user_id == current_user.id)


@usage_analytics_bp.route('/dashboard', methods=['GET'])
@login_required
def analytics_dashboard():
    """Display usage analytics dashboard for PARTIAL features."""
    # Get summary data for all features
    summary = UsageAnalytics.get_usage_summary()

    # Get recent events for timeline
    recent_events = _visible_events(UsageAnalytics.query.order_by(
        UsageAnalytics.timestamp.desc()
    )).limit(100).all()

    return render_template(
        'usage_analytics/dashboard.html',
        summary=summary,
        recent_events=recent_events
    )


@usage_analytics_bp.route('/api/summary', methods=['GET'])
@login_required
def api_usage_summary():
    """API endpoint for usage summary data."""
    feature_name = request.args.get('feature')
    days = int(request.args.get('days', 30))

    summary = UsageAnalytics.get_usage_summary(feature_name, days)
    return jsonify(summary)


@usage_analytics_bp.route('/api/events', methods=['GET'])
@login_required
def api_usage_events():
    """API endpoint for usage events data."""
    feature_name = request.args.get('feature')
    event_type = request.args.get('event_type')
    limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

    query = _visible_events(UsageAnalytics.query.order_by(UsageAnalytics.timestamp.desc()))

    if feature_name:
        query = query.filter_by(feature_name=feature_name)
    if event_type:
        query = query.filter_by(event_type=event_type)

    events = query.limit(limit).all()

    return jsonify([{
        'id': event.id,
        'feature_name': event.feature_name,
        'event_type': event.event_type,
        'route_path': event.route_path,
        'user_id': event.user_id,
        'session_id': event.session_id,
        'timestamp': event.timestamp.isoformat(),
        'event_metadata': event.event_metadata
    } for event in events])


@usage_analytics_bp.route('/api/baseline-report', methods=['GET'])
@login_required
def api_baseline_report():
    """Generate baseline usage report for Phase 0 triage."""
    days = int(request.args.get('days', 30))

    summary = UsageAnalytics.get_usage_summary(days=days)

    # Generate triage recommendations based on usage
    triage_list = {}
    for feature_name, data in summary.items():
        unique_users = data.get('unique_users', 0)
        total_events = data.get('total_events', 0)

        if unique_users == 0:
            recommendation = 'DELETE'
        elif unique_users < 10:
            recommendation = 'DEFER'
        elif unique_users < 100:
            recommendation = 'PRIORITIZE_LOW'
        elif unique_users < 1000:
            recommendation = 'PRIORITIZE_MEDIUM'
        else:
            recommendation = 'PRIORITIZE_HIGH'

        triage_list[feature_name] = {
            'unique_users': unique_users,
            'total_events': total_events,
            'recommendation': recommendation,
            'confidence': 'HIGH' if unique_users > 0 else 'LOW'
        }

    report = {
        'period_days': days,
        'generated_at': '2026-02-03T22:51:00Z',
        'summary': summary,
        'triage_recommendations': triage_list,
        'thresholds': {
            'DELETE': '0 MAU',
            'DEFER': '< 10 MAU',
            'PRIORITIZE_LOW': '10-99 MAU',
            'PRIORITIZE_MEDIUM': '100-999 MAU',
            'PRIORITIZE_HIGH': '1000+ MAU'
        }
    }

    return jsonify(report)