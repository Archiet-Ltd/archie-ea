/*
 * The one search box at the top of the sidebar: pages and records, nothing else.
 *
 * Typing two or more characters finds pages (by their current name and every old name,
 * from the navigation registry through /api/sidebar/search) and records (applications,
 * capabilities, solutions, elements). Enter opens the highlighted result; with nothing
 * highlighted, the best page match opens, or the first record when no page matches. The
 * box never asks anything: questions are asked in the AI chat, which has its own entry.
 *
 * Ctrl+K (Cmd+K on a Mac) focuses the box from anywhere, opening the sidebar first on a
 * phone or from the icon rail, unless the page has its own Ctrl+K (data-suppress-global-search-shortcut).
 *
 * Network behaviour comes from SidebarModuleSearch (module_search.js): no request under
 * two characters, one debounced request per query, a slow answer never overwrites a newer
 * one, and a failure is shown as a failure, not as "nothing found".
 */
(function () {
    'use strict';

    function register() {
        if (!window.Alpine || !window.Alpine.data) return;
        window.Alpine.data('sidebarSearch', function () {
            return {
                query: '',
                pages: [],
                records: [],
                loading: false,
                error: false,
                highlighted: -1,
                kbd: /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '') ? '⌘K' : 'Ctrl K',
                init: function () {
                    var self = this;
                    if (!window.SidebarModuleSearch) return;
                    var finder = window.SidebarModuleSearch.create({
                        includeRecords: true,
                        fetchJson: function (q) {
                            return window.Platform.fetch.get('/api/sidebar/search', { q: q }, { silent: true });
                        },
                        onChange: function (state) {
                            self.pages = state.results || [];
                            self.records = state.records || [];
                            self.loading = state.loading;
                            self.error = state.error;
                            self.highlighted = -1;
                        }
                    });
                    this.$watch('query', function (q) { finder.search(q); });
                    document.addEventListener('keydown', function (e) {
                        if (!(e.metaKey || e.ctrlKey) || (e.key !== 'k' && e.key !== 'K')) return;
                        if (document.body.hasAttribute('data-suppress-global-search-shortcut')) return;
                        e.preventDefault();
                        self.focusBox();
                    });
                },
                focusBox: function () {
                    var input = this.$refs.input;
                    // Opens the drawer on a phone and expands the icon rail (js/sidebar/sidebar_shell.js).
                    window.dispatchEvent(new CustomEvent('sidebar:open'));
                    setTimeout(function () { if (input) { input.focus(); input.select(); } }, 30);
                },
                searching: function () {
                    return this.query.trim().length >= 2;
                },
                // Every row a person can choose, in screen order: pages, then records.
                rows: function () {
                    var out = [];
                    this.pages.forEach(function (p) { out.push({ kind: 'page', url: p.url }); });
                    this.records.forEach(function (r) { out.push({ kind: 'record', url: r.url }); });
                    return out;
                },
                recordIndex: function (i) { return this.pages.length + i; },
                // Arrow keys walk the rows; past either end the box itself is "highlighted" (-1).
                move: function (step) {
                    if (!this.searching()) return;
                    var n = this.rows().length;
                    var next = this.highlighted + step;
                    if (next >= n) next = -1;
                    if (next < -1) next = n - 1;
                    this.highlighted = next;
                },
                go: function (url) {
                    if (url) window.location.assign(url);
                },
                submit: function () {
                    var q = this.query.trim();
                    if (!q) return;
                    var rows = this.rows();
                    if (this.highlighted >= 0 && rows[this.highlighted]) {
                        this.go(rows[this.highlighted].url);
                    } else if (this.pages.length) {
                        this.go(this.pages[0].url);
                    } else if (this.records.length) {
                        this.go(this.records[0].url);
                    }
                },
                clear: function () {
                    this.query = '';
                    this.highlighted = -1;
                }
            };
        });
    }

    if (window.Alpine && window.Alpine.data) {
        register();
    } else {
        document.addEventListener('alpine:init', register);
    }
})();
