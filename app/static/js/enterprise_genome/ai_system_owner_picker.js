/**
 * Owner picker for the AI Systems Register.
 *
 * One picker per register row (`[data-owner-picker]`): a debounced (300 ms)
 * live search against the canonical user-search endpoint, click to select, then
 * POST the owner to the row's owners endpoint and reload so the register shows
 * the saved owner.
 *
 * Requires: core/00-namespace.js, core/03-fetch.js
 */
(function () {
    'use strict';

    if (!window.Platform || !window.Platform.fetch) return;

    function debounce(fn, ms) {
        var timer = null;
        return function () {
            var args = arguments;
            if (timer) clearTimeout(timer);
            timer = setTimeout(function () { fn.apply(null, args); }, ms);
        };
    }

    function initPicker(root) {
        var url = root.getAttribute('data-owners-url');
        var input = root.querySelector('[data-owner-search]');
        var results = root.querySelector('[data-owner-results]');
        var saveBtn = root.querySelector('[data-owner-save]');
        if (!url || !input || !results || !saveBtn) return;

        var selectedId = null;

        function hideResults() {
            results.textContent = '';
            results.classList.add('hidden');
        }

        function showMessage(text) {
            results.textContent = '';
            var div = document.createElement('div');
            div.className = 'px-3 py-2 text-xs text-muted-foreground';
            div.textContent = text;
            results.appendChild(div);
            results.classList.remove('hidden');
        }

        function render(users) {
            if (!users.length) {
                showMessage('No matching users');
                return;
            }
            results.textContent = '';
            users.forEach(function (u) {
                var label = u.name || u.email || '';
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'block w-full px-3 py-2 text-left text-xs hover:bg-accent hover:text-accent-foreground';
                btn.setAttribute('data-user-id', String(u.id));
                btn.textContent = label + (u.email && u.email !== label ? ' — ' + u.email : '');
                btn.addEventListener('click', function () {
                    selectedId = u.id;
                    input.value = label;
                    saveBtn.disabled = false;
                    hideResults();
                });
                results.appendChild(btn);
            });
            results.classList.remove('hidden');
        }

        var search = debounce(function (query) {
            query = (query || '').trim();
            if (query.length < 2) {
                hideResults();
                return;
            }
            Platform.fetch.get('/api/users', { q: query, limit: 10 }, { silent: true })
                .then(function (data) { render(data.users || []); })
                .catch(function () { hideResults(); });
        }, 300);

        input.addEventListener('input', function () {
            selectedId = null;
            saveBtn.disabled = true;
            search(input.value);
        });

        saveBtn.addEventListener('click', function () {
            if (!selectedId) return;
            saveBtn.disabled = true;
            Platform.fetch.post(url, { user_id: selectedId, ownership_type: 'primary' })
                .then(function () { window.location.reload(); })
                .catch(function () { saveBtn.disabled = false; });
        });
    }

    document.querySelectorAll('[data-owner-picker]').forEach(initPicker);
}());
