/* One picker, one unlink confirmation and one error helper for linking a risk to
 * the records it threatens. The risk register's slide-over and the risk's own page
 * both draw these through components/risk_link_picker.html and
 * components/risk_edit_form.html; neither page carries its own copy. */
(function () {
    'use strict';

    var ENDPOINTS = {
        application: function (q) { return '/applications/api/list?search=' + encodeURIComponent(q) + '&limit=10'; },
        capability: function (q) { return '/solutions/capabilities/search?limit=10&q=' + encodeURIComponent(q); },
        solution: function (q) { return '/api/solutions/search?q=' + encodeURIComponent(q); },
        programme: function (q) { return '/api/programmes/search?q=' + encodeURIComponent(q); }
    };

    function say(message) {
        var live = document.getElementById('risk-live');
        if (live) { live.textContent = ''; setTimeout(function () { live.textContent = message; }, 30); }
    }

    // A page that reloads after a change asks for focus (and a spoken message) to be put back.
    function rememberFocus(targetId, message) {
        try { sessionStorage.setItem('risk-focus', JSON.stringify({ id: targetId, message: message || '' })); } catch (e) { /* storage blocked: focus falls to the top */ }
    }

    function restoreFocus() {
        var raw = null;
        try { raw = sessionStorage.getItem('risk-focus'); sessionStorage.removeItem('risk-focus'); } catch (e) { return; }
        if (!raw) return;
        var want;
        try { want = JSON.parse(raw); } catch (e) { return; }
        var target = want.id && document.getElementById(want.id);
        if (target) { target.focus({ preventScroll: false }); }
        if (want.message) { say(want.message); }
    }

    // Puts a server refusal on the field it names (and focuses it), or on the form as a whole.
    function applyError(scope, data, idPrefix) {
        scope.errors = {};
        scope.error = '';
        var message = (data && data.error) || 'Could not save the risk.';
        var field = data && data.field;
        if (field) {
            scope.errors[field] = message;
            var el = document.getElementById(idPrefix + field);
            if (el) { el.focus(); }
        } else {
            scope.error = message;
        }
    }

    function checkFields(form) {
        var errors = {};
        if (!String(form.title || '').trim()) { errors.title = 'Title is required.'; }
        ['likelihood', 'impact'].forEach(function (key) {
            var n = Number(form[key]);
            if (!(Number.isInteger(n) && n >= 1 && n <= 5)) {
                errors[key] = (key === 'likelihood' ? 'Likelihood' : 'Impact') + ' must be a whole number from 1 to 5.';
            }
        });
        return errors;
    }

    window.RiskLinks = { say: say, rememberFocus: rememberFocus, restoreFocus: restoreFocus, applyError: applyError, checkFields: checkFields };

    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', restoreFocus); } else { setTimeout(restoreFocus, 0); }

    // The application / capability / solution / programme picker, as an ARIA combobox.
    window.riskLinkPicker = function (getRiskId, types, listId) {
        return {
            types: types, type: types[0][0], listId: listId,
            query: '', results: [], active: -1, searched: false, busy: false, error: '', status: '',
            typeLabel: function () {
                var self = this;
                return types.filter(function (t) { return t[0] === self.type; })[0][1].toLowerCase();
            },
            optionId: function (i) { return this.listId + '-option-' + i; },
            countText: function () {
                if (!this.searched) { return ''; }
                var n = this.results.length;
                return n === 0 ? 'No ' + this.typeLabel() + ' in your organisation matches that.' : n + (n === 1 ? ' result' : ' results');
            },
            reset: function () { this.results = []; this.active = -1; this.searched = false; this.error = ''; },
            search: function () {
                var q = this.query.trim();
                var self = this;
                this.error = '';
                if (q.length < 2) { this.reset(); return; }
                Platform.fetch.get(ENDPOINTS[this.type](q), null, { silent: true })
                    .then(function (data) {
                        var rows = data.applications || data.items || data.capabilities || data || [];
                        self.results = rows.map(function (r) { return { id: r.id, name: r.name }; });
                        self.active = self.results.length ? 0 : -1;
                        self.searched = true;
                    })
                    .catch(function () { self.results = []; self.active = -1; self.searched = true; });
            },
            onKey: function (event) {
                var n = this.results.length;
                if (event.key === 'ArrowDown' && n) { event.preventDefault(); this.active = (this.active + 1) % n; }
                else if (event.key === 'ArrowUp' && n) { event.preventDefault(); this.active = (this.active - 1 + n) % n; }
                else if (event.key === 'Home' && n) { event.preventDefault(); this.active = 0; }
                else if (event.key === 'End' && n) { event.preventDefault(); this.active = n - 1; }
                else if (event.key === 'Enter' && this.active >= 0) { event.preventDefault(); this.choose(this.results[this.active]); }
                else if (event.key === 'Escape' && (n || this.query)) { event.preventDefault(); this.query = ''; this.reset(); }
            },
            choose: function (entity) {
                var self = this;
                var riskId = getRiskId();
                if (!entity || !riskId || this.busy) { return; }
                this.busy = true;
                this.error = '';
                Platform.fetch('/api/risks/' + riskId + '/links', {
                    method: 'POST', body: { entity_type: this.type, entity_id: entity.id }, silent: true
                }).then(function (link) {
                    if (link && link.error) { self.error = link.error; return; }
                    self.status = 'Linked to ' + (link.entity_label || entity.name) + '.';
                    self.query = ''; self.reset();
                    self.$dispatch('risk-link-added', { link: link, entity: entity, message: self.status });
                }).catch(function () {
                    self.error = 'Could not link this risk. Try again.';
                }).finally(function () { self.busy = false; });
            }
        };
    };

    // Unlink with a confirmation step; "Keep" gives focus back to the Unlink button.
    window.riskUnlink = function (getRiskId, linkId, name) {
        return {
            confirming: false, busy: false, error: '',
            ask: function () {
                var self = this;
                this.confirming = true; this.error = '';
                this.$nextTick(function () { self.$refs.confirm.focus(); });
            },
            keep: function () {
                var self = this;
                this.confirming = false;
                this.$nextTick(function () { self.$refs.opener.focus(); });
            },
            remove: function () {
                var self = this;
                if (this.busy) { return; }
                this.busy = true;
                Platform.fetch('/api/risks/' + getRiskId() + '/links/' + linkId, { method: 'DELETE', silent: true })
                    .then(function (data) {
                        if (data && data.error) { self.error = data.error; return; }
                        self.$dispatch('risk-link-removed', { linkId: linkId, message: 'Removed the link to ' + name + '.' });
                    })
                    .catch(function () { self.error = 'Could not remove that link. Try again.'; })
                    .finally(function () { self.busy = false; });
            }
        };
    };
}());
