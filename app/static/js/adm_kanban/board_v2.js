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

            // Deliverables dialog (phase code, or null when closed)
            deliverablePhase: null,

            // Deliverable checklist state
            phaseDeliverables: {},


            // -- Init ------------------------------------------------

            init: function () {
                let self = this;
                _fetch('/api/adm-kanban/v2/config').then(function (cfg) {
                    if (cfg && cfg.wip_limits) self.wipLimits = cfg.wip_limits;
                });
                _fetch('/api/adm-kanban/v2/metrics').then(function (m) {
                    if (m && m.phase_metrics) self.cycleMetrics = m;
                });
                this.reload();
            },

            // WIP helpers
            wipLimit: function (colId) {
                return this.wipLimits[colId] || 0;
            },
            wipCount: function (colId) {
                return this.columnCounts[colId] || 0;
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

            reload: function () {
                let self = this;
                self.loading = true;
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

                _fetch(url).then(function (data) {
                    self.loading = false;
                    if (!data) {
                        self.error = 'Failed to load board';
                        return;
                    }
                    if (data.success === false) {
                        self.error = (data && data.error) || 'Failed to load board';
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
                }).catch(function (err) {
                    self.loading = false;
                    self.error = (err && err.message) || 'Network error';
                });
            },

            // -- Lanes: the ADM cycle, one lane per phase --------------

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

            // Deliverables a phase produces, from the cards the board already loaded.
            phaseDeliverableCards: function (phaseCode) {
                if (!phaseCode) return [];
                return this.cards.filter(function (c) { return c.card_type === 'deliverable' && c.phase === phaseCode; });
            },

            phaseDeliverableItems: function (phaseCode) {
                let pd = this.phaseDeliverables[phaseCode];
                let checkedById = {};
                if (pd && !pd.loading) {
                    pd.deliverables.forEach(function (d) { checkedById[d.id] = !!d.checked; });
                }
                return this.phaseDeliverableCards(phaseCode).map(function (c) {
                    return {
                        id: c.id,
                        entityId: c.entity_id,
                        title: c.title,
                        subtitle: c.subtitle,
                        ready: !!(pd && !pd.loading),
                        checked: !!checkedById[c.entity_id],
                    };
                });
            },

            openDeliverables: function (phaseCode) {
                this.deliverablePhase = phaseCode;
                this.loadPhaseDeliverables(phaseCode);
            },

            // Change a card's status from the select on the card.
            moveStatus: function (card, toColumn) {
                if (!card || card.column === toColumn) return;
                let self = this;
                let prev = card.column;
                card.column = toColumn;
                self.columnCounts = Object.assign({}, self.columnCounts, {
                    [prev]: Math.max(0, (self.columnCounts[prev] || 0) - 1),
                    [toColumn]: (self.columnCounts[toColumn] || 0) + 1,
                });
                _fetch('/api/adm-kanban/v2/cards/' + card.id + '/move', {
                    method: 'POST',
                    body: { to_column: toColumn },
                    silent: true,
                }).then(function (data) {
                    if (data && data.card) Object.assign(card, data.card);
                }).catch(function (err) {
                    card.column = prev;
                    self.columnCounts = Object.assign({}, self.columnCounts, {
                        [prev]: (self.columnCounts[prev] || 0) + 1,
                        [toColumn]: Math.max(0, (self.columnCounts[toColumn] || 0) - 1),
                    });
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

            openCreateTask: function (phaseCode, colId) {
                window.dispatchEvent(new CustomEvent('adm-create-task-open', {
                    detail: {
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

            loadPhaseDeliverables: function (phaseCode) {
                let self = this;
                if (self.phaseDeliverables[phaseCode]) return; // already loaded
                self.phaseDeliverables[phaseCode] = {
                    loading: true, total: 0, checked: 0, deliverables: [],
                };
                _fetch('/api/adm-kanban/v2/phases/' + phaseCode + '/deliverables')
                    .then(function (data) {
                        if (data && data.success) {
                            self.phaseDeliverables[phaseCode] = {
                                loading: false,
                                total: data.total,
                                checked: data.checked,
                                deliverables: data.deliverables,
                            };
                        } else {
                            self.phaseDeliverables[phaseCode] = {
                                loading: false, total: 0, checked: 0, deliverables: [],
                            };
                        }
                    })
                    .catch(function () {
                        self.phaseDeliverables[phaseCode] = {
                            loading: false, total: 0, checked: 0, deliverables: [],
                        };
                    });
            },

            toggleDeliverable: function (deliverableId, phaseCode, newChecked) {
                let self = this;
                let pd = self.phaseDeliverables[phaseCode];
                if (!pd) return;
                let item = pd.deliverables.find(function (d) { return d.id === deliverableId; });
                if (item) {
                    item.checked = newChecked;
                    pd.checked = pd.deliverables.filter(function (d) { return d.checked; }).length;
                }
                _fetch('/api/adm-kanban/v2/deliverables/' + deliverableId + '/check', {
                    method: 'PATCH',
                    body: { checked: newChecked },
                }).catch(function () {
                    if (item) {
                        item.checked = !newChecked;
                        pd.checked = pd.deliverables.filter(function (d) { return d.checked; }).length;
                    }
                });
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
