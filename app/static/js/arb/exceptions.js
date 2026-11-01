/**
 * arb/exceptions.js: request and decide an exception on an ARB review.
 *
 * Calls the exception routes (create, approve, deny) through Platform.fetch and
 * reloads the review so the page shows what was stored. A refusal from the
 * server is shown in the form, in the server's own words.
 */
(function (global) {
    'use strict';

    function messageOf(error) {
        var body = error && (error.data || error.body || error.response);
        if (body && typeof body === 'object' && body.error) return body.error;
        return (error && error.message) || 'The action could not be completed. Try again.';
    }

    function panel(config) {
        return {
            request: { open: false, busy: false, error: '' },
            decision: { open: false, busy: false, error: '', id: null, mode: 'approve', number: '', note: '' },
            notice: '',

            openRequest: function () {
                this.request.error = '';
                this.request.open = true;
            },

            openDecision: function (id, mode, number) {
                this.decision = { open: true, busy: false, error: '', id: id, mode: mode, number: number, note: '' };
            },

            submitRequest: function (event) {
                var form = event.target;
                var self = this;
                var body = {
                    standard_id: parseInt(form.standard_id.value, 10),
                    exception_type: form.exception_type.value,
                    exception_reason: form.exception_reason.value.trim(),
                    review_item_id: config.reviewId
                };
                var days = form.requested_duration_days.value.trim();
                if (days !== '') body.requested_duration_days = parseInt(days, 10);
                var scope = form.scope.value.trim();
                if (scope) body.scope = scope;
                if (isNaN(body.standard_id)) { self.request.error = 'Choose the standard this exception is against.'; return Promise.resolve(); }
                if (!body.exception_reason) { self.request.error = 'Say why the exception is needed.'; return Promise.resolve(); }
                if (days !== '' && !(body.requested_duration_days >= 1)) {
                    self.request.error = 'Duration must be a whole number of days, 1 or more.';
                    return Promise.resolve();
                }
                self.request.busy = true;
                self.request.error = '';
                return global.Platform.fetch.post(config.createUrl, body, { silent: true }).then(function () {
                    global.location.hash = 'exceptions';
                    global.location.reload();
                }).catch(function (error) {
                    self.request.error = messageOf(error);
                }).finally(function () {
                    self.request.busy = false;
                });
            },

            submitDecision: function () {
                var self = this;
                var note = self.decision.note.trim();
                if (!note) { self.decision.error = 'Give a reason before you decide.'; return Promise.resolve(); }
                var approve = self.decision.mode === 'approve';
                var body = approve ? { approval_notes: note } : { denial_reason: note };
                var url = config.decideUrl + self.decision.id + (approve ? '/approve' : '/deny');
                self.decision.busy = true;
                self.decision.error = '';
                return global.Platform.fetch.put(url, body, { silent: true }).then(function () {
                    global.location.hash = 'exceptions';
                    global.location.reload();
                }).catch(function (error) {
                    self.decision.error = messageOf(error);
                }).finally(function () {
                    self.decision.busy = false;
                });
            }
        };
    }

    global.arbExceptionPanel = panel;
    global.document.addEventListener('alpine:init', function () {
        if (global.Alpine) global.Alpine.data('arbExceptionPanel', panel);
    });
}(window));
