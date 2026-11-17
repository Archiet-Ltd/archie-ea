"""Install the activity record: one entry per signed-in request."""


def install_activity_record(app):
    from app.services import activity_record

    @app.before_request
    def _start_activity_clock():
        activity_record.mark_started()

    @app.after_request
    def _record_activity(response):
        return activity_record.record_request(response)
