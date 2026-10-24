/*
 * Company kind picker (components/company_kind.html): one click saves the choice and says so.
 * Plain script, no Alpine expression, so it runs under the CSP-safe evaluator.
 */
(function () {
    'use strict';

    var SELECTED = ['bg-primary', 'text-primary-foreground', 'font-semibold'];
    var UNSELECTED = ['border', 'border-input', 'bg-background', 'text-foreground'];
    var LABELS = { startup: 'Startup', scaleup: 'Scale-up', enterprise: 'Enterprise' };

    function show(picker, selectedKind) {
        picker.querySelectorAll('[data-company-kind]').forEach(function (btn) {
            var on = btn.getAttribute('data-company-kind') === selectedKind;
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
            SELECTED.forEach(function (c) { btn.classList.toggle(c, on); });
            UNSELECTED.forEach(function (c) { btn.classList.toggle(c, !on); });
        });
    }

    function say(picker, text, failed) {
        var status = picker.querySelector('[data-company-kind-status]');
        if (!status) return;
        status.textContent = text;
        status.classList.toggle('text-destructive', !!failed);
        status.classList.toggle('text-muted-foreground', !failed);
    }

    function save(picker, kind) {
        if (picker.getAttribute('data-saving') === 'true') return;
        var previous = null;
        picker.querySelectorAll('[data-company-kind]').forEach(function (btn) {
            if (btn.getAttribute('aria-pressed') === 'true') previous = btn.getAttribute('data-company-kind');
        });
        picker.setAttribute('data-saving', 'true');
        show(picker, kind);
        say(picker, 'Saving…', false);
        var request = window.Platform && window.Platform.fetch && window.Platform.fetch.post
            ? window.Platform.fetch.post('/dashboard/api/company-kind', { kind: kind }, { silent: true })
            : Promise.reject(new Error('unavailable'));
        request.then(function (body) {
            var saved = body && (body.data || body);
            if (!saved || saved.success === false) throw new Error('not saved');
            show(picker, saved.kind || kind);
            say(picker, 'Saved — your company is set as ' + LABELS[saved.kind || kind] + '.', false);
        }).catch(function () {
            if (previous) show(picker, previous);
            say(picker, 'Could not save your choice. Try again.', true);
        }).then(function () {
            picker.removeAttribute('data-saving');
        });
    }

    document.addEventListener('click', function (event) {
        var btn = event.target.closest && event.target.closest('[data-company-kind]');
        if (!btn) return;
        var picker = btn.closest('[data-company-kind-picker]');
        if (picker) save(picker, btn.getAttribute('data-company-kind'));
    });
})();
