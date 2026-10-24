// Shared Kanban board component — Alpine.js
//
// One board behaviour for every Kanban in the product: the SAFe portfolio
// board and the ADM architecture board render the same shell (the
// kanban_board macro) and move cards the same way. A board that needs richer
// per-card behaviour (the ADM board's swimlanes, bulk select and detail
// drawer) layers its own component on top of these methods rather than
// forking the shell.
//
// Uses Platform.fetch for AJAX (auto-injects CSRF and surfaces failures as a
// toast rather than a silent swallow).
//
// Config (all optional):
//   columns     array of {id, label, state} the board shows, in order.
//   cards       initial card list; each card carries a `state` naming its
//               column and a `sortKey` number used to keep a column ordered.
//   canWrite    whether the signed-in person may move cards. A viewer gets a
//               read-only board: no drag handles, no "Move to" select.
//   moveUrl     a function (card, toState) -> url for the PATCH that persists a
//               move, or a string template with "{id}" in it.
//   moveField   the request-body field the move endpoint reads ("kanban_state"
//               for SAFe). Defaults to "kanban_state".
//   wipLimits   {state: limit} map; 0 or absent means no limit on that column.
//   sortKey     a function (card) -> number; a column stays sorted by this,
//               highest first (WSJF for the portfolio board).

(function (global) {
    'use strict';

    function _fetch() {
        return global.Platform && global.Platform.fetch;
    }

    function _toastError(msg) {
        if (global.Platform && global.Platform.toast && global.Platform.toast.error) {
            global.Platform.toast.error(msg);
        }
    }

    global.kanbanBoard = function (config) {
        config = config || {};
        var moveField = config.moveField || 'kanban_state';
        var wipLimits = config.wipLimits || {};
        var sortKey;
        if (typeof config.sortKey === 'function') {
            sortKey = config.sortKey;
        } else if (typeof config.sortBy === 'string') {
            // A field name (e.g. "wsjf_score") when the config comes from JSON,
            // which cannot carry a function.
            var field = config.sortBy;
            sortKey = function (card) { return card[field] || 0; };
        } else {
            sortKey = function (card) { return card.sortKey || 0; };
        }

        function moveUrl(card, toState) {
            if (typeof config.moveUrl === 'function') return config.moveUrl(card, toState);
            if (typeof config.moveUrl === 'string') return config.moveUrl.replace('{id}', card.id);
            return null;
        }

        return {
            // -- State -------------------------------------------------
            columns: config.columns || [],
            cards: config.cards || [],
            canWrite: !!config.canWrite,
            moving: null,
            draggingCard: null,
            dragOverColumn: null,

            // -- Column helpers ---------------------------------------

            // The cards in one column, kept in sortKey order (highest first).
            columnCards: function (state) {
                return this.cards
                    .filter(function (c) { return c.state === state; })
                    .sort(function (a, b) { return sortKey(b) - sortKey(a); });
            },

            columnCount: function (state) {
                return this.cards.filter(function (c) { return c.state === state; }).length;
            },

            // -- WIP limits -------------------------------------------
            // Text as well as colour (section 26 / WCAG): the header reads
            // "3 / 4" and turns to the warning/destructive token with a lock
            // icon when the column is at its limit.

            wipLimit: function (state) {
                return wipLimits[state] || 0;
            },

            wipCount: function (state) {
                return this.columnCount(state);
            },

            wipAtLimit: function (state) {
                var lim = this.wipLimit(state);
                return lim > 0 && this.wipCount(state) >= lim;
            },

            wipNearLimit: function (state) {
                var lim = this.wipLimit(state);
                return lim > 0 && this.wipCount(state) === lim - 1;
            },

            // The highest-sortKey card in a column, so the board can accent it
            // (the top-WSJF epic on the portfolio board).
            isTopCard: function (card) {
                var inColumn = this.columnCards(card.state);
                return inColumn.length > 0 && inColumn[0].id === card.id;
            },

            // -- Drag and drop ----------------------------------------
            // Dragging is never the only way to move a card: the "Move to"
            // select below does the same thing from the keyboard (WCAG 2.2).

            onDragStart: function (event, card) {
                if (!this.canWrite) return;
                this.draggingCard = card;
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', String(card.id));
            },

            onDragEnd: function () {
                this.draggingCard = null;
                this.dragOverColumn = null;
            },

            onDragOver: function (event, state) {
                if (!this.draggingCard) return;
                event.dataTransfer.dropEffect = 'move';
                this.dragOverColumn = state;
            },

            onDragLeave: function (event) {
                if (!event.currentTarget.contains(event.relatedTarget)) {
                    this.dragOverColumn = null;
                }
            },

            onDrop: function (event, toState) {
                this.dragOverColumn = null;
                if (!this.draggingCard) return;
                var card = this.draggingCard;
                this.draggingCard = null;
                if (card.state === toState) return;
                this._persistMove(card, toState);
            },

            // -- Keyboard / select move -------------------------------

            moveCard: function (card, selectEl) {
                var toState = selectEl ? selectEl.value : null;
                if (!toState || this.moving === card.id) return;
                if (toState === card.state) {
                    selectEl.value = card.state;
                    return;
                }
                this._persistMove(card, toState, selectEl);
            },

            // Shared move: optimistic update, PATCH, re-sort, revert on failure.
            _persistMove: function (card, toState, selectEl) {
                var self = this;
                var prevState = card.state;
                var url = moveUrl(card, toState);
                if (!url) return;

                self.moving = card.id;
                card.state = toState;   // optimistic

                var body = {};
                body[moveField] = toState;

                var platformFetch = _fetch();
                var request = platformFetch
                    ? platformFetch(url, { method: 'PATCH', body: body })
                    : Promise.reject(new Error('Platform.fetch unavailable'));

                return request.then(function (updated) {
                    if (updated && typeof updated === 'object') {
                        Object.assign(card, updated);
                    }
                    card.state = toState;
                }).catch(function (err) {
                    card.state = prevState;      // revert
                    if (selectEl) selectEl.value = prevState;
                    var data = err && err.data;
                    var msg = (data && data.error) || (err && err.message) || 'Move failed';
                    _toastError(msg);
                }).finally(function () {
                    self.moving = null;
                });
            },
        };
    };

    // The SAFe portfolio board: reads its config from the JSON data island the
    // template emits and returns a shared kanbanBoard(). Registered as its own
    // named factory so x-data stays a bare call ("safePortfolioBoard()"), which
    // the CSP expression evaluator accepts — an inline object/function literal
    // in an attribute does not parse under the CSP build.
    global.safePortfolioBoard = function () {
        var config = {};
        var el = global.document && global.document.getElementById('safe-portfolio-kanban-config');
        if (el) {
            try { config = JSON.parse(el.textContent); } catch (e) { config = {}; }
        }
        return global.kanbanBoard(config);
    };
})(typeof window !== 'undefined' ? window : this);
