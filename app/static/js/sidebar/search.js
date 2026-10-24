/*
 * The one search box: "Search or ask..." at the top of the sidebar (navigation v3).
 *
 * Typing two or more characters finds pages (by their current name and every old name,
 * from the navigation registry through /api/sidebar/search) and records (applications,
 * capabilities, solutions, elements). Enter opens the highlighted result; with nothing
 * highlighted, a question opens the AI chat with the question filled in, and anything
 * else opens the best page match (or the chat when nothing matches). The last row is
 * always "Ask the AI chat", so asking is one action from any page.
 *
 * Ctrl+K (Cmd+K on a Mac) focuses the box from anywhere, opening the sidebar first on a
 * phone, unless the page has its own Ctrl+K (data-suppress-global-search-shortcut).
 *
 * Network behaviour comes from SidebarModuleSearch (module_search.js): no request under
 * two characters, one debounced request per query, a slow answer never overwrites a newer
 * one, and a failure is shown as a failure, not as "nothing found".
 */
(function () {
    'use strict';

    var QUESTION_START = /^(who|whom|whose|what|which|why|how|when|where|is|are|am|was|were|can|could|does|do|did|should|would|will|has|have|list|show|tell|explain|compare|summarise|summarize|give|find)\b/i;

    function looksLikeQuestion(text) {
        var q = (text || '').trim();
        if (!q) return false;
        if (q.charAt(q.length - 1) === '?') return true;
        return QUESTION_START.test(q) && q.split(/\s+/).length >= 3;
    }

    function register() {
        if (!window.Alpine || !window.Alpine.data) return;
        window.Alpine.data('sidebarSearch', function (chatUrl) {
            return {
                query: '',
                pages: [],
                records: [],
                loading: false,
                error: false,
                highlighted: -1,
                chatUrl: chatUrl,
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
                    if (window.Alpine && window.Alpine.store && window.Alpine.store('sidebar')) {
                        var store = window.Alpine.store('sidebar');
                        if (window.innerWidth < 1024) store.open = true;
                        if (store.collapsed && store.toggle) store.toggle();
                    }
                    setTimeout(function () { if (input) { input.focus(); input.select(); } }, 30);
                },
                searching: function () {
                    return this.query.trim().length >= 2;
                },
                // Every row a person can choose, in screen order: pages, records, then Ask.
                rows: function () {
                    var out = [];
                    this.pages.forEach(function (p) { out.push({ kind: 'page', url: p.url }); });
                    this.records.forEach(function (r) { out.push({ kind: 'record', url: r.url }); });
                    out.push({ kind: 'ask', url: this.askUrl() });
                    return out;
                },
                askUrl: function () {
                    var q = this.query.trim();
                    return this.chatUrl + (q ? (this.chatUrl.indexOf('?') === -1 ? '?' : '&') + 'q=' + encodeURIComponent(q) : '');
                },
                isQuestion: function () {
                    return looksLikeQuestion(this.query);
                },
                recordIndex: function (i) { return this.pages.length + i; },
                askIndex: function () { return this.pages.length + this.records.length; },
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
                    } else if (this.isQuestion() || this.pages.length === 0) {
                        this.go(this.askUrl());
                    } else {
                        this.go(this.pages[0].url);
                    }
                },
                clear: function () {
                    this.query = '';
                    this.highlighted = -1;
                }
            };
        });
    }

    window.SidebarSearchQuestion = { looksLikeQuestion: looksLikeQuestion };

    if (window.Alpine && window.Alpine.data) {
        register();
    } else {
        document.addEventListener('alpine:init', register);
    }
})();
