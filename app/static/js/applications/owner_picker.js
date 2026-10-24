/**
 * Owner Picker for the Application Edit form and AI Systems Register.
 *
 * Provides:
 *  - Debounced live-search (300 ms) against the canonical user-search endpoint
 *  - Enables the "Add owner" button only when a user is selected
 *  - POST /applications/<id>/owners to add
 *  - DELETE /applications/<id>/owners/<id> to remove
 *  - PUT /applications/<id>/owners/<id> to change type
 *  - Re-renders the owner list after each mutation
 *  - Platform.toast on error
 *
 * Two modes:
 *  1. Legacy singleton mode — initialises from #owner_picker_search and
 *     sibling DOM ids (applications edit page, unchanged behaviour).
 *  2. Per-instance mode — one picker per [data-owner-picker][data-owners-url]
 *     root (AI Systems Register rows, no module-level state).
 *
 * Requires: core/00-namespace.js, core/03-fetch.js, lucide
 */
(function () {
    'use strict';

    if (!window.Platform || !window.Platform.fetch) return;

    var OWNERSHIP_TYPES = ['primary', 'backup', 'technical', 'business'];
    var OWNERSHIP_LABELS = {'primary': 'Primary', 'backup': 'Backup', 'technical': 'Technical', 'business': 'Business'};

    // ── Debounce helper ───────────────────────────────────────────────────────
    function debounce(fn, ms) {
        var timer = null;
        return function () {
            var args = arguments;
            var ctx = this;
            if (timer) clearTimeout(timer);
            timer = setTimeout(function () { fn.apply(ctx, args); }, ms);
        };
    }

    // ── Escape HTML for safe innerHTML ────────────────────────────────────────
    function escapeHtml(str) {
        if (!str) return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    // ── Instance-based picker (for [data-owner-picker] roots) ─────────────────
    // A combobox: the search box owns a listbox of people; arrow keys move
    // through them, Enter chooses, Escape closes, and a status line says how
    // many people matched.
    var pickerCount = 0;
    var SAVED_KEY = 'ownerStepSaved';

    // A save reloads the page so every figure on it follows; the confirmation
    // is kept across the reload and shown once the page is back.
    function reloadWithConfirmation(message) {
        try { window.sessionStorage.setItem(SAVED_KEY, message); } catch (e) { /* storage blocked: the page still reloads */ }
        window.location.reload();
    }

    (function showPendingConfirmation() {
        var message = null;
        try {
            message = window.sessionStorage.getItem(SAVED_KEY);
            window.sessionStorage.removeItem(SAVED_KEY);
        } catch (e) { return; }
        if (message && window.Platform.toast && window.Platform.toast.success) {
            window.Platform.toast.success(message);
        }
    })();

    function initPicker(root) {
        var url = root.getAttribute('data-owners-url');
        var input = root.querySelector('[data-owner-search]');
        var results = root.querySelector('[data-owner-results]');
        var saveBtn = root.querySelector('[data-owner-save]');
        var status = root.querySelector('[data-owner-status]');
        if (!url || !input || !results || !saveBtn) return;

        var replace = root.hasAttribute('data-owner-replace');
        var selectedId = null;
        var active = -1;
        var options = [];
        var wantFirst = false;
        pickerCount += 1;
        var listId = 'owner-results-' + pickerCount;

        results.id = listId;
        results.setAttribute('role', 'listbox');
        results.setAttribute('aria-label', 'People matching your search');
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-controls', listId);
        input.setAttribute('aria-expanded', 'false');

        function say(text) {
            if (status) status.textContent = text;
        }

        function setActive(index) {
            if (active >= 0 && options[active]) options[active].setAttribute('aria-selected', 'false');
            active = index;
            if (active >= 0 && options[active]) {
                options[active].setAttribute('aria-selected', 'true');
                input.setAttribute('aria-activedescendant', options[active].id);
                options[active].scrollIntoView({ block: 'nearest' });
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        function hideResults() {
            results.textContent = '';
            results.classList.add('hidden');
            options = [];
            setActive(-1);
            input.setAttribute('aria-expanded', 'false');
        }

        function choose(u, label) {
            selectedId = u.id;
            input.value = label;
            saveBtn.disabled = false;
            hideResults();
            say(label + ' chosen. Save owner to record them.');
        }

        function render(users) {
            results.textContent = '';
            options = [];
            setActive(-1);
            if (!users.length) {
                var div = document.createElement('div');
                div.className = 'px-3 py-2 text-xs text-muted-foreground';
                div.textContent = 'No matching users';
                results.appendChild(div);
                results.classList.remove('hidden');
                input.setAttribute('aria-expanded', 'true');
                say('No matching people');
                return;
            }
            users.forEach(function (u, i) {
                var label = u.name || u.email || '';
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.id = listId + '-' + i;
                btn.setAttribute('role', 'option');
                btn.setAttribute('aria-selected', 'false');
                btn.tabIndex = -1;
                btn.className = 'block w-full px-3 py-2 text-left text-xs hover:bg-accent hover:text-accent-foreground aria-selected:bg-accent aria-selected:text-accent-foreground';
                btn.setAttribute('data-user-id', String(u.id));
                btn.textContent = label + (u.email && u.email !== label ? ' — ' + u.email : '');
                btn.addEventListener('click', function () { choose(u, label); });
                btn._owner = { user: u, label: label };
                results.appendChild(btn);
                options.push(btn);
            });
            results.classList.remove('hidden');
            input.setAttribute('aria-expanded', 'true');
            if (wantFirst) {
                wantFirst = false;
                setActive(0);
            }
            say(users.length + (users.length === 1 ? ' person' : ' people') + ' found. Use the arrow keys to choose.');
        }

        function runSearch(query) {
            query = (query || '').trim();
            if (query.length < 2) {
                wantFirst = false;
                hideResults();
                return;
            }
            Platform.fetch.get('/api/users', { q: query, limit: 10 }, { silent: true })
                .then(function (data) { render(data.users || []); })
                .catch(function () { hideResults(); });
        }
        var search = debounce(runSearch, 300);

        input.addEventListener('input', function () {
            selectedId = null;
            wantFirst = false;
            saveBtn.disabled = true;
            search(input.value);
        });

        input.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown' && !options.length && input.value.trim().length >= 2 && !selectedId) {
                // The list is still on its way: ask now and land on the first person when it arrives.
                event.preventDefault();
                wantFirst = true;
                runSearch(input.value);
            } else if (event.key === 'ArrowDown' && options.length) {
                event.preventDefault();
                setActive((active + 1) % options.length);
            } else if (event.key === 'ArrowUp' && options.length) {
                event.preventDefault();
                setActive(active <= 0 ? options.length - 1 : active - 1);
            } else if (event.key === 'Enter') {
                if (active >= 0 && options[active]) {
                    event.preventDefault();
                    choose(options[active]._owner.user, options[active]._owner.label);
                } else if (selectedId) {
                    event.preventDefault();
                    saveBtn.click();
                }
            } else if (event.key === 'Escape' && options.length) {
                event.preventDefault();
                hideResults();
            }
        });

        saveBtn.addEventListener('click', function () {
            if (!selectedId) return;
            saveBtn.disabled = true;
            var body = { user_id: selectedId, ownership_type: 'primary' };
            if (replace) body.replace = true;
            Platform.fetch.post(url, body)
                .then(function () { reloadWithConfirmation('Owner saved'); })
                .catch(function () { saveBtn.disabled = false; });
        });
    }

    // ── Clearing an owner: one confirmation, then the row is removed ──────────
    function initClear(btn) {
        btn.addEventListener('click', function () {
            var url = btn.getAttribute('data-owner-clear');
            if (!url) return;
            Platform.confirm('Clear this owner? The record will show no owner until you assign one.').then(function (ok) {
                if (!ok) return;
                btn.disabled = true;
                Platform.fetch.delete(url)
                    .then(function () { reloadWithConfirmation('Owner cleared'); })
                    .catch(function () { btn.disabled = false; });
            });
        });
    }

    // ── Legacy singleton mode (applications edit page) ────────────────────────
    var searchInput = document.getElementById('owner_picker_search');
    var resultsContainer = document.getElementById('owner_picker_results');
    var addBtn = document.getElementById('add-owner-btn');
    var typeSelect = document.getElementById('owner_type_select');
    var ownerListContainer = document.getElementById('owner-list');

    if (searchInput && resultsContainer && addBtn && ownerListContainer) {
        var appId = searchInput.getAttribute('data-app-id');

        // ── State ─────────────────────────────────────────────────────────────
        var state = {
            selectedUserId: null,
            selectedLabel: null,
        };

        // ── Search ────────────────────────────────────────────────────────────
        function doSearch(query) {
            query = (query || '').trim();
            if (query.length < 2) {
                resultsContainer.innerHTML = '';
                resultsContainer.classList.add('hidden');
                return;
            }

            Platform.fetch.get('/api/users', { q: query, limit: 20 }, { silent: true })
                .then(function (data) {
                    renderResults((data.users || []).map(function (u) {
                        return {
                            id: u.id,
                            label: u.name || u.email || '',
                            email: u.email || '',
                        };
                    }));
                })
                .catch(function () {
                    // Type-ahead errors are transient and not shown to the user
                });
        }

        var debouncedSearch = debounce(doSearch, 300);

        searchInput.addEventListener('input', function () {
            debouncedSearch(searchInput.value);
            // Clear selection when user types anew
            state.selectedUserId = null;
            state.selectedLabel = null;
            addBtn.disabled = true;
        });

        searchInput.addEventListener('focus', function () {
            if (searchInput.value.trim().length >= 2) {
                doSearch(searchInput.value);
            }
        });

        // Hide results when clicking outside
        document.addEventListener('click', function (e) {
            if (!searchInput.contains(e.target) && !resultsContainer.contains(e.target)) {
                resultsContainer.classList.add('hidden');
            }
        });

        // ── Render results ────────────────────────────────────────────────────
        function renderResults(users) {
            if (users.length === 0) {
                resultsContainer.innerHTML =
                    '<div class="px-3 py-2 text-sm text-muted-foreground">No matching users</div>';
                resultsContainer.classList.remove('hidden');
                return;
            }

            var html = '';
            users.forEach(function (u) {
                html +=
                    '<div class="owner-picker-result px-3 py-2 text-sm cursor-pointer hover:bg-accent hover:text-accent-foreground border-b border-border last:border-b-0" data-user-id="' +
                    u.id + '" data-label="' + escapeHtml(u.label) + '" data-email="' + escapeHtml(u.email) + '">' +
                    '<div class="font-medium">' + escapeHtml(u.label) + '</div>' +
                    '<div class="text-xs text-muted-foreground">' + escapeHtml(u.email) + '</div>' +
                    '</div>';
            });
            resultsContainer.innerHTML = html;
            resultsContainer.classList.remove('hidden');

            // Bind click on each result
            resultsContainer.querySelectorAll('.owner-picker-result').forEach(function (el) {
                el.addEventListener('click', function () {
                    state.selectedUserId = parseInt(el.getAttribute('data-user-id'), 10);
                    state.selectedLabel = el.getAttribute('data-label');
                    searchInput.value = state.selectedLabel;
                    resultsContainer.classList.add('hidden');
                    addBtn.disabled = false;
                });
            });
        }

        // ── Add owner ─────────────────────────────────────────────────────────
        addBtn.addEventListener('click', function () {
            if (!state.selectedUserId) return;
            var otype = typeSelect ? typeSelect.value : 'primary';

            addBtn.disabled = true;
            addBtn.innerHTML = '<i data-lucide="loader" class="h-4 w-4 animate-spin"></i> Adding…';

            Platform.fetch.post('/applications/' + appId + '/owners', {
                user_id: state.selectedUserId,
                ownership_type: otype,
            })
                .then(function () {
                    state.selectedUserId = null;
                    state.selectedLabel = null;
                    searchInput.value = '';
                    addBtn.disabled = true;
                    addBtn.innerHTML = '<i data-lucide="plus" class="h-4 w-4"></i> Add owner';
                    return reloadOwnerList();
                })
                .catch(function () {
                    addBtn.disabled = false;
                    addBtn.innerHTML = '<i data-lucide="plus" class="h-4 w-4"></i> Add owner';
                })
                .finally(function () {
                    lucide.createIcons();
                });
        });

        // ── Remove owner & change type ────────────────────────────────────────
        ownerListContainer.addEventListener('click', function (e) {
            var btn = e.target.closest('.remove-owner-btn');
            if (btn) {
                var ownerId = btn.getAttribute('data-owner-id');
                if (!ownerId) return;

                Platform.confirm('Remove this owner?').then(function (ok) {
                    if (!ok) return;

                    Platform.fetch.delete('/applications/' + appId + '/owners/' + ownerId)
                        .then(function () {
                            return reloadOwnerList();
                        })
                        .catch(function () {
                            // Error toast shown by Platform.fetch
                        });
                });
                return;
            }

            var typeSelectEl = e.target.closest('.change-owner-type');
            if (typeSelectEl) {
                var ownerId = typeSelectEl.getAttribute('data-owner-id');
                var newType = typeSelectEl.value;
                Platform.fetch.put('/applications/' + appId + '/owners/' + ownerId, {
                    ownership_type: newType,
                })
                    .then(function () {
                        return reloadOwnerList();
                    })
                    .catch(function () {
                        // Error toast shown by Platform.fetch
                    });
            }
        });

        // ── Reload owner list ─────────────────────────────────────────────────
        function reloadOwnerList() {
            return Platform.fetch.get('/applications/' + appId + '/owners')
                .then(function (data) {
                    renderOwnerList(data.owners || []);
                })
                .catch(function () {
                    // Error toast shown by Platform.fetch
                });
        }

        function renderOwnerList(owners) {
            if (owners.length === 0) {
                ownerListContainer.innerHTML =
                    '<p class="text-sm text-muted-foreground" id="no-owners-msg">No owners assigned yet.</p>';
                return;
            }

            var html = '<p class="text-sm font-medium">Current owners</p>';
            owners.forEach(function (o) {
                var typeOptions = '';
                OWNERSHIP_TYPES.forEach(function (t) {
                    var sel = (t === o.ownership_type) ? ' selected' : '';
                    typeOptions += '<option value="' + t + '"' + sel + '>' + OWNERSHIP_LABELS[t] + '</option>';
                });
                html +=
                    '<div class="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2 text-sm" data-owner-id="' +
                    o.id + '">' +
                    '<div class="flex items-center gap-2">' +
                    '<span class="font-medium">' + escapeHtml(o.user_name) + '</span>' +
                    '<select class="change-owner-type text-xs rounded border border-input bg-background px-1 py-0.5" data-owner-id="' +
                    o.id + '">' + typeOptions + '</select>' +
                    '</div>' +
                    '<button type="button" class="text-destructive hover:text-destructive-emphasis text-xs remove-owner-btn" data-owner-id="' +
                    o.id + '" data-app-id="' + appId + '">Remove</button>' +
                    '</div>';
            });
            ownerListContainer.innerHTML = html;
            lucide.createIcons();
        }

        // ── Initial load ──────────────────────────────────────────────────────
        reloadOwnerList();
    }

    // ── Instance-based pickers (AI Systems Register rows) ─────────────────────
    document.querySelectorAll('[data-owner-picker]').forEach(initPicker);
    document.querySelectorAll('[data-owner-clear]').forEach(initClear);

})();