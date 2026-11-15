/* Suggestions panel of the canvas page: asks for entries for one box, lists each with its source,
 * and lets the person accept (the canvas's own add form), edit the name first, or dismiss. */
(function () {
    'use strict';

    var panel = document.querySelector('[data-testid="bmc-suggest-panel"]');
    var row = document.getElementById('bmc-suggest-row');
    if (!panel || !row) return;

    var status = panel.querySelector('[data-suggest-status]');
    var errorBox = panel.querySelector('[data-suggest-error]');
    var list = panel.querySelector('[data-suggest-list]');
    var root = document.getElementById('bmc-suggest-root');
    var NONE = 'Nothing to suggest for this box from what is on the canvas and in your model yet.';

    function showError(text) {
        errorBox.textContent = text;
        errorBox.hidden = false;
        status.textContent = '';
    }

    function render(boxKey, draft, notice) {
        (draft.proposals || []).forEach(function (p) {
            var item = row.content.firstElementChild.cloneNode(true);
            var form = item.querySelector('form');
            form.elements.box.value = boxKey;
            form.elements.element_id.value = p.element_id || '';
            form.elements.name.value = p.name;
            var field = form.elements.name;
            field.id = 'suggest-name-' + list.children.length;
            item.querySelector('label').setAttribute('for', field.id);
            item.querySelector('[data-suggest-notice]').textContent = notice || '';
            item.querySelector('[data-suggest-source]').textContent = p.source;
            // An edited name is a new name, not the existing element it was proposed from.
            field.addEventListener('input', function () {
                if (field.value !== p.name) form.elements.element_id.value = '';
            });
            item.querySelector('[data-suggest-dismiss]').addEventListener('click', function () {
                item.remove();
                if (!list.children.length) status.textContent = 'All suggestions dismissed.';
            });
            list.appendChild(item);
        });
        status.textContent = list.children.length ? '' : NONE;
    }

    function open(button) {
        var boxKey = button.getAttribute('data-bmc-suggest').replace(/-/g, '_');
        var boxTitle = button.getAttribute('data-box-title');
        list.replaceChildren();
        errorBox.hidden = true;
        status.textContent = 'Drafting suggestions for ' + boxTitle + '…';
        var state = window.Alpine.$data(root);
        state.suggestTitle = 'Suggestions for ' + boxTitle;
        state.suggestOpen = true;
        Platform.fetch.post(panel.getAttribute('data-draft-url'), { block: boxKey }, { silent: true })
            .then(function (data) {
                if (!data || !data.draft) throw new Error('empty');
                render(boxKey, data.draft, data.notice);
            })
            .catch(function (err) {
                var body = err && err.data;
                showError((body && (body.message || body.error)) || (err && err.message && err.message !== 'empty' ? err.message : 'Suggestions could not be drafted just now. Your entries are unchanged.'));
            });
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-bmc-suggest]');
        if (button) open(button);
    });
}());
