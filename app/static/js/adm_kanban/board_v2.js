// app/static/js/adm_kanban/board_v2.js
// ADM Kanban V2 — Alpine.js board component
// Uses Platform.fetch for AJAX (auto-injects CSRF)

(function (global) {
    'use strict';

    let _fetch = global.Platform.fetch;

    global.admKanbanV2 = function () {
        return {
            // State
            loading: true,
            error: null,
            cards: [],
            columns: [],
            phases: [],
            phaseCounts: {},
            columnCounts: {},
            totalCardCount: 0,
            wipLimits: {},
            cycleMetrics: {},

            // Filters
            filters: {
                phase: 'all',
                assignee: 'all',
                priority: '',
                card_type: '',
            },

            // Requirements: the panel beside the board (a sheet when narrow), the one picked, the phase shown
            requirements: [],
            requirementsLoaded: false,
            requirementsError: null,
            linkError: null,
            selectedReq: null,
            phaseFilter: '',
            wide: false,
            panelOpen: true,

            // Detail modal
            detailCard: null,

            // Bulk selection
            selectedCardIds: [],
            bulkMoveTarget: '',

            selectCard: function (cardId, event) {
                if (event && event.shiftKey && this.selectedCardIds.length > 0) {
                    // Range select within same column
                    let lastSelected = this.selectedCardIds[this.selectedCardIds.length - 1];
                    let lastCard = this.cards.find(function (c) { return c.id === lastSelected; });
                    let thisCard = this.cards.find(function (c) { return c.id === cardId; });
                    if (lastCard && thisCard && lastCard.column === thisCard.column) {
                        let colCards = this.columnCards(lastCard.column);
                        let startIdx = colCards.findIndex(function (c) { return c.id === lastSelected; });
                        let endIdx = colCards.findIndex(function (c) { return c.id === cardId; });
                        let lo = Math.min(startIdx, endIdx);
                        let hi = Math.max(startIdx, endIdx);
                        for (let i = lo; i <= hi; i++) {
                            if (this.selectedCardIds.indexOf(colCards[i].id) === -1) {
                                this.selectedCardIds.push(colCards[i].id);
                            }
                        }
                        return;
                    }
                }
                let idx = this.selectedCardIds.indexOf(cardId);
                if (idx === -1) {
                    this.selectedCardIds.push(cardId);
                } else {
                    this.selectedCardIds.splice(idx, 1);
                }
            },

            isSelected: function (cardId) {
                return this.selectedCardIds.indexOf(cardId) !== -1;
            },

            clearSelection: function () {
                this.selectedCardIds = [];
                this.bulkMoveTarget = '';
            },

            bulkMove: function () {
                let self = this;
                let target = self.bulkMoveTarget;
                if (!target || self.selectedCardIds.length === 0) return;

                let promises = self.selectedCardIds.map(function (cardId) {
                    let card = self.cards.find(function (c) { return c.id === cardId; });
                    if (card && card.column !== target) {
                        card.column = target;
                        return _fetch('/api/adm-kanban/v2/cards/' + cardId + '/move', {
                            method: 'POST',
                            body: { to_column: target },
                            silent: true,
                        });
                    }
                    return Promise.resolve();
                });

                Promise.all(promises).then(function () {
                    self.clearSelection();
                    self.reload();
                    if (global.Platform && global.Platform.toast) {
                        global.Platform.toast.success('Moved ' + promises.length + ' cards');
                    }
                });
            },

            // Deliverables dialog: the solution card it was opened from, or null when closed
            deliverableSolution: null,
            deliverableState: { loading: false, error: null, phase: null, checklist: [], also_produced: [], total: 0, complete: 0 },


            // -- Init ------------------------------------------------

            init: function () {
                let self = this;
                // A return address is kept only when it is a path on this site: one
                // leading slash, then neither a slash nor a backslash (a browser
                // reads "/\host" as "//host"), and it resolves to this origin.
                function sameSiteReturn(raw, fallback) {
                    if (typeof raw !== 'string' || raw.charAt(0) !== '/') return fallback;
                    let second = raw.charAt(1);
                    if (second === '/' || second === '\\') return fallback;
                    try {
                        let url = new URL(raw, global.location.origin);
                        return url.origin === global.location.origin ? url.pathname + url.search + url.hash : fallback;
                    } catch (e) {
                        return fallback;
                    }
                }
                let query = global.matchMedia('(min-width: 1280px)');
                self.wide = query.matches;
                query.addEventListener('change', function (e) { self.wide = e.matches; });
                self.loadRequirements();
                _fetch('/api/adm-kanban/v2/config').then(function (cfg) {
                    if (cfg && cfg.wip_limits) self.wipLimits = cfg.wip_limits;
                });
                _fetch('/api/adm-kanban/v2/metrics').then(function (m) {
                    if (m && m.phase_metrics) self.cycleMetrics = m;
                });
                // A link from another page can open the New Architecture Task form
                // with an element type chosen, and ask to come back after saving.
                let q = new URLSearchParams(global.location.search);
                if (q.get('new') === 'Gap') {
                    self.pendingCreate = {
                        arch_element_type: 'Gap',
                        phase: 'E',
                        next: sameSiteReturn(q.get('next'), '/adm-kanban/gap-analysis'),
                    };
                }
                this.reload();
            },

            pendingCreate: null,

            // WIP helpers
            wipLimit: function (colId) {
                return this.wipLimits[colId] || 0;
            },
            // Counted from the cards the board shows, so the summary and the lanes agree.
            wipCount: function (colId) {
                return this.cards.filter(function (c) { return c.card_type !== 'deliverable' && c.column === colId; }).length;
            },
            wipAtLimit: function (colId) {
                let lim = this.wipLimit(colId);
                return lim > 0 && this.wipCount(colId) >= lim;
            },
            wipNearLimit: function (colId) {
                let lim = this.wipLimit(colId);
                return lim > 0 && this.wipCount(colId) === lim - 1;
            },

            // Cycle time helpers
            phaseCycleAvg: function (phaseCode) {
                let m = this.cycleMetrics && this.cycleMetrics.phase_metrics;
                return m && m[phaseCode] ? m[phaseCode].avg_cycle_days : null;
            },
            colSlaStatus: function (colId) {
                let s = this.cycleMetrics && this.cycleMetrics.column_sla;
                return s && s[colId] ? s[colId] : null;
            },

            // quiet: refresh the cards in place, without the loading skeleton
            reload: function (quiet) {
                let self = this;
                if (quiet !== true) self.loading = true;
                self.error = null;

                let params = new URLSearchParams();
                if (self.filters.phase && self.filters.phase !== 'all') {
                    params.set('phase', self.filters.phase);
                }
                if (self.filters.assignee && self.filters.assignee !== 'all') {
                    params.set('assignee', self.filters.assignee);
                }
                if (self.filters.priority) {
                    params.set('priority', self.filters.priority);
                }

                let url = '/api/adm-kanban/v2/cards';
                let qs = params.toString();
                if (qs) url += '?' + qs;

                _fetch(url, { silent: true }).then(function (data) {
                    self.loading = false;
                    if (!data) {
                        self.error = 'The board could not load. Try again.';
                        return;
                    }
                    if (data.success === false) {
                        self.error = 'The board could not load. Try again.';
                        return;
                    }
                    self.cards = Array.isArray(data)
                        ? data
                        : Array.isArray(data.cards)
                            ? data.cards
                            : Array.isArray(data.items)
                                ? data.items
                                : [];
                    self.columns = data.columns || [];
                    self.phases = data.phases || [];
                    self.phaseCounts = data.phase_counts || {};
                    self.columnCounts = data.column_counts || {};
                    self.totalCardCount = self.cards.length;
                    if (self.pendingCreate) {
                        let preset = self.pendingCreate;
                        self.pendingCreate = null;
                        self.openCreateTask(null, 'proposed', preset);
                    }
                }).catch(function (err) {
                    self.loading = false;
                    self.error = 'The board could not load. Try again.';
                });
            },

            // -- Lanes: the ADM cycle, one lane per phase --------------

            // -- Requirements and the cycle ----------------------------

            loadRequirements: function () {
                let self = this;
                self.requirementsError = null;
                _fetch('/api/adm-kanban/v2/requirements', { silent: true }).then(function (data) {
                    if (!data || data.success === false) throw new Error('load');
                    self.requirements = data.requirements || [];
                    self.requirementsLoaded = true;
                }).catch(function () {
                    self.requirementsError = 'The requirements could not load. Try again.';
                });
            },

            requirementTotal: function () {
                return this.requirements.length;
            },

            solutionCards: function () {
                return this.cards.filter(function (c) { return c.card_type === 'solution'; });
            },

            // The line on a card: its count, then its two highest-priority names.
            requirementChipText: function (card) {
                let n = card.requirement_total || 0;
                return n ? this.plural(n, 'requirement') : 'No requirements';
            },

            topRequirements: function (card) {
                return (card.requirements || []).slice(0, 2);
            },

            requirementPhaseText: function (r) {
                return r.phase_label ? r.phase_label : 'No phase set';
            },

            isHighlighted: function (card) {
                if (this.selectedReq === null || card.card_type !== 'solution') return false;
                let r = this.requirements.find(function (x) { return x.id === this.selectedReq; }, this);
                return !!r && r.solution_id === card.entity_id;
            },

            selectRequirement: function (r) {
                this.selectedReq = this.selectedReq === r.id ? null : r.id;
                if (!this.wide) global.closeDrawer('adm-requirements');
            },

            openRequirements: function () {
                if (this.wide) {
                    this.panelOpen = !this.panelOpen;
                } else {
                    global.openDrawer('adm-requirements');
                }
            },

            linkRequirement: function (r, value) {
                let self = this;
                self.linkError = null;
                _fetch('/api/adm-kanban/v2/requirements/' + r.id + '/solution', {
                    method: 'PATCH',
                    body: { solution_id: value === '' ? null : parseInt(value, 10) },
                    silent: true,
                }).then(function (data) {
                    if (!data || data.success === false || !data.requirement) throw new Error('save');
                    Object.assign(r, data.requirement);
                    self.reload(true);
                }).catch(function () {
                    self.linkError = 'The link could not be saved. Try again.';
                    self.loadRequirements();
                });
            },

            cycleCards: function (code) {
                return this.visibleCards().filter(function (c) { return c.phase === code; });
            },

            cycleCount: function (code) {
                return this.cycleCards(code).length;
            },

            cycleLabel: function (code) {
                let p = this.phases.find(function (x) { return x.code === code; });
                let n = this.cycleCount(code);
                let name = p ? p.label : code;
                let prefix = (code === 'PRELIM') ? '' : code + ': ';
                return prefix + name + ', ' + this.plural(n, 'card');
            },

            // The name read aloud starts with the words drawn on the circle ("B 1"), then says what they mean.
            cycleAria: function (code, short) {
                let p = this.phases.find(function (x) { return x.code === code; });
                let n = this.cycleCount(code);
                return short + ' ' + n + ': ' + (p ? p.label : code) + ', ' + this.plural(n, 'card');
            },

            cycleTip: '',

            cycleName: function (code) {
                let p = this.phases.find(function (x) { return x.code === code; });
                return (code === 'PRELIM' ? '' : code + ': ') + (p ? p.label : code);
            },

            chooseCycle: function (code) {
                this.phaseFilter = this.phaseFilter === code ? '' : code;
            },

            // The lanes on the board: the whole cycle, or the one phase chosen in the ring.
            // Requirements Management is the panel, not a lane; a lane appears for it only
            // while cards recorded against it exist.
            lanes: function () {
                let self = this;
                return this.phases.filter(function (p) {
                    if (self.phaseFilter) return p.code === self.phaseFilter;
                    return p.code !== 'REQ' || self.cycleCount('REQ') > 0;
                });
            },

            shownCards: function () {
                let f = this.phaseFilter;
                return this.visibleCards().filter(function (c) { return !f || c.phase === f; });
            },

            plural: function (n, word) {
                return n + ' ' + word + (n === 1 ? '' : 's');
            },

            columnLabel: function (colId) {
                let col = this.columns.find(function (c) { return c.id === colId; });
                return col ? col.label : colId;
            },

            phaseKicker: function (code) {
                if (code === 'PRELIM') return 'Before the cycle';
                if (code === 'REQ') return 'Centre of the cycle';
                return 'Phase ' + code;
            },

            viewpointUrl: function (code) {
                let map = { A: 'stakeholder', B: 'business_process', C: 'application_cooperation', D: 'technology', E: 'implementation_deployment', F: 'migration', G: 'motivation', H: 'motivation' };
                return '/archimate/viewpoints?active=' + (map[code] || 'basic');
            },

            // What the board shows: solutions and tasks by default, one type when filtered.
            visibleCards: function () {
                let type = this.filters.card_type;
                return this.cards.filter(function (c) {
                    return type ? c.card_type === type : c.card_type !== 'deliverable';
                });
            },

            // 'board' when there is something to lay out; otherwise why the board is empty.
            emptyKind: function () {
                if (this.visibleCards().length > 0) return 'board';
                if (this.filters.card_type === 'deliverable') return 'no-deliverables';
                if (this.filters.assignee !== 'all' || this.filters.priority || this.filters.card_type) return 'filtered';
                return 'nothing';
            },

            visibleNoun: function () {
                return { solution: 'solution', task: 'task', deliverable: 'deliverable' }[this.filters.card_type] || 'card';
            },

            clearFilters: function () {
                this.filters.assignee = 'all';
                this.filters.priority = '';
                this.filters.card_type = '';
                this.reload();
            },

            laneCards: function (phaseCode) {
                return this.visibleCards().filter(function (c) { return c.phase === phaseCode; });
            },

            // Cards of one lane grouped by status; deliverables are not a status, so they form their own group.
            laneGroups: function (phaseCode) {
                let cards = this.laneCards(phaseCode);
                let groups = [];
                this.columns.forEach(function (col) {
                    let inCol = cards.filter(function (c) { return c.card_type !== 'deliverable' && c.column === col.id; });
                    if (inCol.length) groups.push({ id: col.id, label: col.label, cards: inCol });
                });
                let deliverables = cards.filter(function (c) { return c.card_type === 'deliverable'; });
                if (deliverables.length) groups.push({ id: 'deliverables', label: 'Deliverables', cards: deliverables });
                return groups;
            },

            laneCountText: function (phaseCode) {
                let cards = this.laneCards(phaseCode);
                if (!cards.length) return '';
                let self = this;
                let parts = [];
                [['solution', 'solution'], ['task', 'task'], ['deliverable', 'deliverable']].forEach(function (t) {
                    let n = cards.filter(function (c) { return c.card_type === t[0]; }).length;
                    if (n) parts.push(self.plural(n, t[1]));
                });
                return parts.join(', ');
            },

            laneEmptyText: function () {
                let type = this.filters.card_type;
                if (type === 'deliverable') return 'No deliverables in this phase';
                if (type === 'task') return 'No tasks in this phase';
                return 'No solutions in this phase';
            },

            // The chip on a solution: its own deliverables and how many are complete.
            deliverableChipText: function (card) {
                let total = (card.meta && card.meta.deliverables_total) || 0;
                if (!total) return 'No deliverables';
                return (card.meta.deliverables_complete || 0) + ' of ' + this.plural(total, 'deliverable') + ' complete';
            },

            stateLabel: function (item) {
                return item.complete ? 'Complete' : ({ planned: 'Planned', in_progress: 'In progress', delayed: 'Delayed' }[item.status] || 'In progress');
            },

            stateLabelFor: function (colId) {
                return { proposed: 'Planned', done: 'Complete' }[colId] || 'In progress';
            },

            deliverableTitle: function () {
                return this.deliverableSolution ? 'Deliverables — ' + this.deliverableSolution.title : '';
            },

            deliverableSummary: function () {
                let st = this.deliverableState;
                if (st.loading || st.error) return '';
                let phase = st.phase ? this.phaseLabel(st.phase) : 'its phase';
                return st.complete + ' of ' + this.plural(st.total, 'deliverable') + ' complete, set against the TOGAF checklist for ' + phase;
            },

            openDeliverables: function (card) {
                this.deliverableSolution = card;
                this.loadSolutionDeliverables(card);
            },

            closeDeliverables: function () {
                this.deliverableSolution = null;
            },

            // Change a card's status from the select on the card.
            moveStatus: function (card, toColumn) {
                if (!card || card.column === toColumn) return;
                let self = this;
                let prev = card.column;
                card.column = toColumn;
                _fetch('/api/adm-kanban/v2/cards/' + card.id + '/move', {
                    method: 'POST',
                    body: { to_column: toColumn },
                    silent: true,
                }).then(function (data) {
                    if (data && data.card) Object.assign(card, data.card);
                }).catch(function (err) {
                    card.column = prev;
                    let errData = err && err.data;
                    let msg = (errData && errData.error) || (err && err.message) || 'Move failed';
                    if (errData && errData.wip_exceeded) {
                        msg = '⚠ WIP limit reached: ' + msg;
                    }
                    if (global.Platform && global.Platform.toast) {
                        global.Platform.toast.error(msg);
                    }
                });
            },

            // -- Column helpers --------------------------------------

            columnCards: function (colId) {
                return this.cards.filter(function (c) { return c.column === colId; });
            },

            // -- Card detail -----------------------------------------

            openDetail: function (card) {
                this.detailCard = card;
                this.loadArtifacts(card);
                window.dispatchEvent(new CustomEvent('adm-card-open', {
                    detail: { card: card, columns: this.columns, phases: this.phases }
                }));
            },

            moveFromDetail: function (card, toColumn) {
                if (!card || card.column === toColumn) return;
                let self = this;
                let prevColumn = card.column;

                // Optimistic UI
                card.column = toColumn;

                _fetch('/api/adm-kanban/v2/cards/' + card.id + '/move', {
                    method: 'POST',
                    body: { to_column: toColumn },
                    silent: true,
                }).then(function (data) {
                    if (data && data.card) {
                        Object.assign(card, data.card);
                        self.detailCard = card;
                    }
                }).catch(function (err) {
                    card.column = prevColumn;
                    let errData = err && err.data;
                    let msg = (errData && errData.error) || (err && err.message) || 'Move failed';
                    if (errData && errData.wip_exceeded) {
                        msg = '\u26a0 WIP limit reached: ' + msg;
                    }
                    if (global.Platform && global.Platform.toast) {
                        global.Platform.toast.error(msg);
                    }
                });
            },

            // -- Create task modal -----------------------------------

            openCreateTask: function (phaseCode, colId, preset) {
                window.dispatchEvent(new CustomEvent('adm-create-task-open', {
                    detail: {
                        preset: preset || null,
                        phases: this.phases,
                        columns: this.columns,
                        lockedPhase: phaseCode || null,
                        lockedColumn: colId || null,
                    },
                }));
            },

            onCardCreated: function (card) {
                if (!card) return;
                this.cards.push(card);
                this.totalCardCount = this.cards.length;
            },

            // -- Artifact helpers -------------------------------------

            artifactTotal: function (card) {
                return (card && card.meta && card.meta.artifact_total) || 0;
            },

            phaseGateValid: function (card) {
                return card && card.meta && card.meta.phase_gate && card.meta.phase_gate.valid;
            },

            phaseGateErrors: function (card) {
                return (card && card.meta && card.meta.phase_gate && card.meta.phase_gate.errors) || [];
            },

            phaseGateWarnings: function (card) {
                return (card && card.meta && card.meta.phase_gate && card.meta.phase_gate.warnings) || [];
            },

            loadArtifacts: function (card) {
                let self = this;
                if (!card || card._artifactsLoaded) return;
                _fetch('/api/adm-kanban/v2/cards/' + card.id + '/artifacts')
                    .then(function (data) {
                        if (data && data.success) {
                            card._artifacts = data.artifacts || {};
                            card._artifactTotal = data.artifact_total || 0;
                            card._phaseGate = data.phase_gate || {};
                            card._artifactsLoaded = true;
                        }
                    })
                    .catch(function () {
                        card._artifactsLoaded = false;
                    });
            },

            // -- Display helpers -------------------------------------

            phaseClass: function (phase) {
                let map = {
                    'PRELIM': 'bg-slate-500/10 text-slate-600',
                    'A': 'bg-blue-500/10 text-primary',
                    'B': 'bg-emerald-500/10 text-emerald-600',
                    'C': 'bg-violet-500/10 text-violet-600',
                    'D': 'bg-orange-500/10 text-orange-600',
                    'E': 'bg-amber-500/10 text-amber-600',
                    'F': 'bg-cyan-500/10 text-cyan-600',
                    'G': 'bg-rose-500/10 text-rose-600',
                    'H': 'bg-indigo-500/10 text-primary',
                    'REQ': 'bg-gray-500/10 text-muted-foreground',
                };
                return map[phase] || 'bg-muted text-muted-foreground';
            },

            priorityClass: function (priority) {
                let map = {
                    'critical': 'bg-destructive/10 text-destructive',
                    'high': 'bg-orange-500/10 text-orange-600',
                    'medium': 'bg-amber-500/10 text-amber-600',
                    'low': 'bg-muted text-muted-foreground',
                };
                return map[priority] || 'bg-muted text-muted-foreground';
            },

            formatDate: function (isoDate) {
                if (!isoDate) return '';
                try {
                    let d = new Date(isoDate);
                    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
                } catch (e) {
                    return isoDate;
                }
            },

            isOverdue: function (isoDate) {
                if (!isoDate) return false;
                try {
                    return new Date(isoDate) < new Date();
                } catch (e) {
                    return false;
                }
            },

            phaseLabel: function (code) {
                if (!code) return '';
                let p = this.phases.find(function (ph) { return ph.code === code; });
                return p ? p.label : code;
            },

            solutionCount: function () {
                return this.cards.filter(function (c) { return c.card_type === 'solution'; }).length;
            },

            // -- Deliverable checklist helpers ---------------------------

            loadSolutionDeliverables: function (card) {
                let self = this;
                self.deliverableState = { loading: true, error: null, phase: null, checklist: [], also_produced: [], total: 0, complete: 0 };
                _fetch('/api/adm-kanban/v2/solutions/' + card.entity_id + '/deliverables')
                    .then(function (data) {
                        if (self.deliverableSolution !== card) return;
                        if (!data || !data.success) {
                            self.deliverableState.loading = false;
                            self.deliverableState.error = (data && data.error) || 'The deliverables could not be loaded.';
                            return;
                        }
                        self.deliverableState = {
                            loading: false, error: null, phase: data.phase, checklist: data.checklist,
                            also_produced: data.also_produced, total: data.total, complete: data.complete,
                        };
                        // The chip on the card reads the same figures.
                        card.meta.deliverables_total = data.total;
                        card.meta.deliverables_complete = data.complete;
                    })
                    .catch(function () {
                        self.deliverableState.loading = false;
                        self.deliverableState.error = 'The deliverables could not be loaded.';
                    });
            },

            // The tick is the deliverable's own status: save it, then show what was saved.
            toggleDeliverable: function (item, newComplete) {
                let self = this;
                let card = self.deliverableSolution;
                _fetch('/api/adm-kanban/v2/deliverables/' + item.id + '/complete', {
                    method: 'PATCH',
                    body: { complete: newComplete },
                }).then(function (data) {
                    if (!data || !data.success) throw new Error((data && data.error) || 'not saved');
                    self.loadSolutionDeliverables(card);
                    self.syncDeliverableCard(item.id, data.deliverable);
                }).catch(function () {
                    item.complete = !newComplete;
                    self.deliverableState.error = 'That change could not be saved. Try again.';
                });
            },

            // Keep the deliverable's own card (the Deliverables view) in step with what was saved.
            syncDeliverableCard: function (deliverableId, saved) {
                let own = this.cards.find(function (c) { return c.id === 'deliverable:' + deliverableId; });
                if (own) own.column = saved.complete ? 'done' : 'under_development';
            },

        };
    };

    // -- Phase → Architecture Domain pre-selection map ----------------
    // Primary domain per TOGAF ADM phase (soft default; override allowed).
    let PHASE_DOMAIN_MAP = {
        'Preliminary': 'Business',
        'A': 'Business',
        'B': 'Business',
        'C': 'Application',
        'D': 'Technology',
        'E': 'Business',
        'F': 'Business',
        'G': 'Technology',
        'H': 'Business',
    };

    function _domainForPhase(phase) {
        return PHASE_DOMAIN_MAP[phase] || 'Business';
    }
    global._domainForPhase = _domainForPhase;

    // -- Create Task Modal Component ----------------------------------

    global.admKanbanCreateTask = function () {
        return {
            show: false,
            phases: [],
            columns: [],
            lockedPhase: null,
            lockedColumn: null,
            form: {
                title: '',
                phase: 'A',
                column: 'proposed',
                priority: 'medium',
                arch_element_type: 'WorkPackage',
                arch_domain: 'Business',
                togaf_deliverable: '',
                closes_gap_id: null,
                requires_arb_signoff: false,
                target_plateau_id: null,
                description: '',
                issue_type: 'Task',
                assignee: null,
                assignee_label: '',
                story_points: null,
                labels: [],
                arch_layer: '',
                acceptance_criteria: '',
                target_start_date: '',
                target_end_date: '',
                progress_pct: 0,
                requirement_ids: [],
                goal_ids: [],
                driver_ids: [],
                principle_ids: [],
            },
            domainOverridden: false,
            preset: null,
            error: null,
            loading: false,
            assigneeSearch: '',
            userResults: [],
            requirementSearch: '',
            requirementResults: [],
            goalSearch: '',
            goalResults: [],
            driverSearch: '',
            driverResults: [],
            principleSearch: '',
            principleResults: [],

            onOpen: function (e) {
                this.phases = (e.detail && e.detail.phases) || [];
                this.columns = (e.detail && e.detail.columns) || [];
                this.lockedPhase = (e.detail && e.detail.lockedPhase) || null;
                this.lockedColumn = (e.detail && e.detail.lockedColumn) || null;
                this.preset = (e.detail && e.detail.preset) || null;
                let phase = this.lockedPhase || (this.phases.length ? this.phases[0].code : 'A');
                this.domainOverridden = false;
                this.form = {
                    title: '',
                    phase: phase,
                    column: this.lockedColumn || 'proposed',
                    priority: 'medium',
                    arch_element_type: 'WorkPackage',
                    arch_domain: _domainForPhase(phase),
                    togaf_deliverable: '',
                    closes_gap_id: null,
                    requires_arb_signoff: false,
                    target_plateau_id: null,
                    description: '',
                    issue_type: 'Task',
                    assignee: null,
                    assignee_label: '',
                    story_points: null,
                    labels: [],
                    arch_layer: '',
                    acceptance_criteria: '',
                    target_start_date: '',
                    target_end_date: '',
                    progress_pct: 0,
                    requirement_ids: [],
                    goal_ids: [],
                    driver_ids: [],
                    principle_ids: [],
                };
                this.requirementSearch = '';
                this.requirementResults = [];
                this.goalSearch = '';
                this.goalResults = [];
                this.driverSearch = '';
                this.driverResults = [];
                this.principleSearch = '';
                this.principleResults = [];
                this.assigneeSearch = '';
                this.userResults = [];
                this.error = null;
                if (this.preset) {
                    this.form.arch_element_type = this.preset.arch_element_type;
                    if (this.preset.phase && this.phases.some(function (p) { return p.code === this.preset.phase; }, this)) {
                        this.form.phase = this.preset.phase;
                        this.form.arch_domain = _domainForPhase(this.form.phase);
                    }
                }
                this.show = true;
            },

            onPhaseChange: function () {
                let recommended = _domainForPhase(this.form.phase);
                if (!this.domainOverridden) {
                    this.form.arch_domain = recommended;
                }
            },

            onDomainChange: function () {
                let recommended = _domainForPhase(this.form.phase);
                this.domainOverridden = (this.form.arch_domain !== recommended);
            },

            submit: function () {
                let self = this;
                let title = (self.form.title || '').trim();
                if (!title) {
                    self.error = 'Title is required';
                    return;
                }
                self.loading = true;
                self.error = null;

                const body = Object.assign({}, self.form, {
                    issue_type: self.form.issue_type,
                    assignee: self.form.assignee,
                    story_points: self.form.story_points,
                    labels: self.form.labels,
                    arch_layer: self.form.arch_layer,
                    acceptance_criteria: self.form.acceptance_criteria,
                    target_start_date: self.form.target_start_date || null,
                    target_end_date: self.form.target_end_date || null,
                    progress_pct: self.form.progress_pct,
                    requirement_ids: self.form.requirement_ids.map(function (x) { return x.id; }),
                    goal_ids: self.form.goal_ids.map(function (x) { return x.id; }),
                    driver_ids: self.form.driver_ids.map(function (x) { return x.id; }),
                    principle_ids: self.form.principle_ids.map(function (x) { return x.id; }),
                });

                _fetch('/api/adm-kanban/v2/cards', {
                    method: 'POST',
                    body: body,
                }).then(function (data) {
                    self.loading = false;
                    if (data && data.success && data.card) {
                        window.dispatchEvent(new CustomEvent('adm-card-created', {
                            detail: data.card,
                        }));
                        self.show = false;
                        if (self.preset && self.preset.next) global.location.href = self.preset.next;
                    } else {
                        self.error = (data && data.error) || 'Failed to create task';
                    }
                }).catch(function (err) {
                    self.loading = false;
                    self.error = (err && err.message) || 'Network error';
                });
            },

            /* Every picker below reports a lookup failure through `error`, which the
               modal already renders as a destructive banner. Unguarded, a 500 parsed
               to `{}` and the dropdown showed "no matches" — the user concludes the
               person or requirement does not exist and files the task without it. */
            _searchFailed: function (what, e) {
                this.error = 'Could not search ' + what + ' — ' + ((e && e.message) || 'request failed') +
                             '. This is a lookup failure, not an empty result.';
            },

            searchUsers: async function () {
                try {
                    const data = await global.Platform.fetch.get('/api/adm-kanban/v2/suggestions/users', { q: this.assigneeSearch }, { silent: true });
                    this.userResults = data.results || [];
                } catch (e) { this.userResults = []; this._searchFailed('users', e); }
            },
            selectUser: function (u) {
                this.form.assignee = u.id;
                this.form.assignee_label = u.label;
                this.assigneeSearch = '';
                this.userResults = [];
            },
            searchRequirements: async function () {
                try {
                    const data = await Platform.fetch.get('/api/adm-kanban/v2/suggestions/requirements', { q: this.requirementSearch }, { silent: true });
                    const selected = this.form.requirement_ids.map(function (x) { return x.id; });
                    this.requirementResults = (data.results || []).filter(function (r) { return !selected.includes(r.id); });
                } catch (e) { this.requirementResults = []; this._searchFailed('requirements', e); }
            },
            addRequirement: function (item) {
                if (!this.form.requirement_ids.find(function (x) { return x.id === item.id; })) {
                    this.form.requirement_ids.push(item);
                }
                this.requirementSearch = '';
                this.requirementResults = [];
            },
            removeRequirement: function (item) {
                this.form.requirement_ids = this.form.requirement_ids.filter(function (x) { return x.id !== item.id; });
            },

            searchGoals: async function () {
                try {
                    const data = await Platform.fetch.get('/api/adm-kanban/v2/suggestions/goals', { q: this.goalSearch }, { silent: true });
                    const selected = this.form.goal_ids.map(function (x) { return x.id; });
                    this.goalResults = (data.results || []).filter(function (r) { return !selected.includes(r.id); });
                } catch (e) { this.goalResults = []; this._searchFailed('goals', e); }
            },
            addGoal: function (item) {
                if (!this.form.goal_ids.find(function (x) { return x.id === item.id; })) {
                    this.form.goal_ids.push(item);
                }
                this.goalSearch = '';
                this.goalResults = [];
            },
            removeGoal: function (item) {
                this.form.goal_ids = this.form.goal_ids.filter(function (x) { return x.id !== item.id; });
            },

            searchDrivers: async function () {
                try {
                    const data = await Platform.fetch.get('/api/adm-kanban/v2/suggestions/drivers', { q: this.driverSearch }, { silent: true });
                    const selected = this.form.driver_ids.map(function (x) { return x.id; });
                    this.driverResults = (data.results || []).filter(function (r) { return !selected.includes(r.id); });
                } catch (e) { this.driverResults = []; this._searchFailed('drivers', e); }
            },
            addDriver: function (item) {
                if (!this.form.driver_ids.find(function (x) { return x.id === item.id; })) {
                    this.form.driver_ids.push(item);
                }
                this.driverSearch = '';
                this.driverResults = [];
            },
            removeDriver: function (item) {
                this.form.driver_ids = this.form.driver_ids.filter(function (x) { return x.id !== item.id; });
            },

            searchPrinciples: async function () {
                try {
                    const data = await Platform.fetch.get('/api/adm-kanban/v2/suggestions/principles', { q: this.principleSearch }, { silent: true });
                    const selected = this.form.principle_ids.map(function (x) { return x.id; });
                    this.principleResults = (data.results || []).filter(function (r) { return !selected.includes(r.id); });
                } catch (e) { this.principleResults = []; this._searchFailed('principles', e); }
            },
            addPrinciple: function (item) {
                if (!this.form.principle_ids.find(function (x) { return x.id === item.id; })) {
                    this.form.principle_ids.push(item);
                }
                this.principleSearch = '';
                this.principleResults = [];
            },
            removePrinciple: function (item) {
                this.form.principle_ids = this.form.principle_ids.filter(function (x) { return x.id !== item.id; });
            },
            searchEditUsers: async function () {
                try {
                    const q = (this.editAssigneeSearch || '');
                    const data = await Platform.fetch.get('/api/adm-kanban/v2/suggestions/users', { q: q }, { silent: true });
                    this.editUserResults = data.results || [];
                } catch (e) { this.editUserResults = []; this._searchFailed('users', e); }
            },
            selectEditUser: function (u) {
                this.editFields.assignee = u.id;
                this.editFields.assignee_label = u.label;
                this.editAssigneeSearch = '';
                this.editUserResults = [];
            },
        };
    };

})(window);
