let APP_CONFIG = window.__APP_CONFIG__ || {};

function duplicateDetection() {
    return {
        showRunDetectionModal: false,
        isRunningDetection: false,
        isLoading: true,
        stats: {
            total_groups: 0,
            total_estimated_savings: 0,
            high_priority_groups: 0,
            last_run_date: 'Never',
            last_run_summary: 'Run detection to generate results'
        },
        allGroups: [],
        // H-01: ArchiMate element duplicate groups (exact-name, per layer/type).
        // Computed live server-side; not tied to a "run" the way application
        // detection is.
        elementGroups: [],
        elementGroupsLoading: true,
        elementGroupsTotalDuplicated: 0,
        elementGroupsError: '',
        // The template referenced selectedGroups 6 times - including
        // :class="selectedGroups.includes(group.id)" inside <template x-for> -
        // but nothing defined it, so every row render threw a ReferenceError and
        // the bulk action bar (x-show="selectedGroups.length > 0") could never
        // appear. An array, not a Set: Alpine cannot observe Set mutations
        // (DESIGN.md).
        selectedGroups: [],
        currentPage: 1,
        perPage: 20,
        sortColumn: 'estimated_savings',
        sortDirection: 'desc',
        filters: {
            search: '',
            type: '',
            status: '',
            risk: ''
        },
        detectionConfig: {
            strategy: 'hybrid',
            similarity_threshold: 0.60,
            run_name: 'Duplicate Detection'
        },
        runFeedback: {
            status: '',
            title: '',
            message: '',
            run_id: null,
            run_name: '',
            groups_found: 0,
            exact_matches: 0,
            fuzzy_matches: 0,
            applications_analyzed: 0,
            estimated_savings: 0,
            duration_seconds: null
        },
        runElapsedSeconds: 0,
        runProgressMessage: '',
        runFeedbackTimer: null,

        init() {
            this.loadStats();
            this.loadGroups();
            this.loadElementGroups();
            this.$nextTick(function() { lucide.createIcons(); });
        },

        async loadElementGroups() {
            this.elementGroupsLoading = true;
            this.elementGroupsError = '';
            try {
                let data = await Platform.fetch('/duplicate-detection/simple/api/element-groups');
                this.elementGroups = data.groups || [];
                this.elementGroupsTotalDuplicated = data.total_duplicated_elements || 0;
                this.$nextTick(function() { lucide.createIcons(); });
            } catch (error) {
                this.elementGroupsError = 'Could not load ArchiMate element duplicate groups.';
                // The error toast is already shown by Platform.fetch (unless silent:true).
                // We keep the inline error state for the UI.
            } finally {
                this.elementGroupsLoading = false;
            }
        },

        clearRunFeedback() {
            this.stopRunFeedbackTimer();
            this.runFeedback = {
                status: '',
                title: '',
                message: '',
                run_id: null,
                run_name: '',
                groups_found: 0,
                exact_matches: 0,
                fuzzy_matches: 0,
                applications_analyzed: 0,
                estimated_savings: 0,
                duration_seconds: null
            };
            this.runElapsedSeconds = 0;
            this.runProgressMessage = '';
        },

        stopRunFeedbackTimer() {
            if (this.runFeedbackTimer) {
                clearInterval(this.runFeedbackTimer);
                this.runFeedbackTimer = null;
            }
        },

        startRunFeedback() {
            this.stopRunFeedbackTimer();
            this.runElapsedSeconds = 0;
            this.runProgressMessage = 'Preparing applications for comparison...';

            let thresholdValue = parseFloat(this.detectionConfig.similarity_threshold || 0.6);
            if (isNaN(thresholdValue)) {
                thresholdValue = 0.6;
            }

            this.runFeedback = {
                status: 'running',
                title: 'Detection is running',
                message: this.getStrategyLabel(this.detectionConfig.strategy) +
                    ' at ' + Math.round(thresholdValue * 100) + '% threshold. Results will appear automatically when complete.',
                run_id: null,
                run_name: this.detectionConfig.run_name || '',
                groups_found: 0,
                exact_matches: 0,
                fuzzy_matches: 0,
                applications_analyzed: 0,
                estimated_savings: 0,
                duration_seconds: null
            };

            let self = this;
            this.runFeedbackTimer = setInterval(function() {
                self.runElapsedSeconds += 1;
                if (self.runElapsedSeconds < 8) {
                    self.runProgressMessage = 'Preparing applications for comparison...';
                } else if (self.runElapsedSeconds < 20) {
                    self.runProgressMessage = 'Computing exact and fuzzy matches...';
                } else {
                    self.runProgressMessage = 'Scoring groups and calculating estimated savings...';
                }
            }, 1000);
        },

        getFilteredGroups() {
            let groups = this.allGroups.slice();
            let self = this;

            if (this.filters.search) {
                let term = this.filters.search.toLowerCase();
                groups = groups.filter(function(g) {
                    return (g.name || '').toLowerCase().includes(term) ||
                        (g.description || '').toLowerCase().includes(term);
                });
            }
            if (this.filters.type) {
                let filterType = this.filters.type;
                groups = groups.filter(function(g) { return g.duplicate_type === filterType; });
            }
            if (this.filters.status) {
                let filterStatus = this.filters.status;
                groups = groups.filter(function(g) { return g.status === filterStatus; });
            }
            if (this.filters.risk) {
                let filterRisk = this.filters.risk;
                groups = groups.filter(function(g) { return g.risk_level === filterRisk; });
            }

            if (this.sortColumn) {
                groups.sort(function(a, b) {
                    let aVal = a[self.sortColumn];
                    let bVal = b[self.sortColumn];
                    if (typeof aVal === 'string') aVal = (aVal || '').toLowerCase();
                    if (typeof bVal === 'string') bVal = (bVal || '').toLowerCase();
                    aVal = aVal || 0;
                    bVal = bVal || 0;
                    if (aVal < bVal) return self.sortDirection === 'asc' ? -1 : 1;
                    if (aVal > bVal) return self.sortDirection === 'asc' ? 1 : -1;
                    return 0;
                });
            }

            return groups;
        },

        paginatedGroups() {
            let filtered = this.getFilteredGroups();
            let start = (this.currentPage - 1) * this.perPage;
            return filtered.slice(start, start + this.perPage);
        },

        totalPages() {
            return Math.max(1, Math.ceil(this.getFilteredGroups().length / this.perPage));
        },

        sortBy(column) {
            if (this.sortColumn === column) {
                this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortColumn = column;
                this.sortDirection = 'asc';
            }
            this.currentPage = 1;
        },

        applyFilters() {
            this.currentPage = 1;
        },

        typeBadgeClass(type) {
            let map = {
                'exact': 'bg-green-500/10 text-emerald-600 border border-green-500/30',
                'fuzzy': 'bg-amber-500/10 text-amber-600 border border-amber-500/30',
                'functional': 'bg-blue-500/10 text-primary border border-blue-500/30',
                'technical': 'bg-purple-500/10 text-primary border border-purple-500/30',
                'capability': 'bg-cyan-500/10 text-cyan-600 border border-cyan-500/30'
            };
            return map[type] || 'bg-slate-500/10 text-slate-600 border border-slate-500/30';
        },

        statusBadgeClass(status) {
            let map = {
                'pending': 'bg-amber-500/10 text-amber-600 border border-amber-500/30',
                'reviewing': 'bg-blue-500/10 text-primary border border-blue-500/30',
                'approved': 'bg-green-500/10 text-emerald-600 border border-green-500/30',
                'resolved': 'bg-green-500/10 text-emerald-600 border border-green-500/30',
                'ignored': 'bg-slate-500/10 text-slate-600 border border-slate-500/30'
            };
            return map[status] || 'bg-slate-500/10 text-slate-600 border border-slate-500/30';
        },

        riskBadgeClass(risk) {
            let map = {
                'high': 'bg-red-500/10 text-destructive border border-red-500/30',
                'medium': 'bg-amber-500/10 text-amber-600 border border-amber-500/30',
                'low': 'bg-green-500/10 text-emerald-600 border border-green-500/30'
            };
            return map[risk] || 'bg-slate-500/10 text-slate-600 border border-slate-500/30';
        },

        async loadStats() {
            try {
                let data = await Platform.fetch('/duplicate-detection/simple/api/statistics');
                // Unchecked, a 500 parsed to `{}` and every `|| 0` below fired, so the
                // KPI tiles showed a measured-looking zero for figures never computed.
                let self = this;
                let latestRun = data.latest_run || null;
                let groupsFound = latestRun && latestRun.groups_found != null ? latestRun.groups_found : 0;
                let lastRunSummary = 'Run detection to generate results';

                if (latestRun) {
                    if ((latestRun.status || '').toLowerCase() !== 'completed') {
                        lastRunSummary = 'Last run status: ' + self.formatRunStatus(latestRun.status);
                    } else if (groupsFound === 0) {
                        lastRunSummary = 'Last run found no duplicate groups';
                    } else {
                        lastRunSummary = 'Last run found ' + groupsFound + ' duplicate group' + (groupsFound === 1 ? '' : 's');
                    }
                }

                this.stats = {
                    total_groups: data.total_groups || 0,
                    total_estimated_savings: data.total_estimated_savings ?? null,
                    high_priority_groups: data.high_priority_groups || 0,
                    last_run_date: latestRun ? self.formatDate(latestRun.created_at) : 'Never',
                    last_run_summary: lastRunSummary
                };
                this.$nextTick(function() { lucide.createIcons(); });
            } catch (error) {
                // null, not 0 — nothing was measured, so the tiles must render an em dash.
                this.stats = {
                    total_groups: null,
                    total_estimated_savings: null,
                    high_priority_groups: null,
                    last_run_date: null,
                    last_run_summary: null
                };
                // Platform.fetch already shows a toast; we keep the inline null state.
            }
        },

        async loadGroups() {
            this.isLoading = true;
            try {
                let data = await Platform.fetch('/duplicate-detection/simple/api/groups');
                // Unchecked, a 500 parsed to `{}` and the table rendered "no duplicate
                // groups found" — a detection failure dressed up as a clean portfolio.
                this.allGroups = data.groups || [];
                this.currentPage = 1;
                this.$nextTick(function() { lucide.createIcons(); });
            } catch (error) {
                this.allGroups = [];
                // Platform.fetch already shows a toast; we keep the empty array state.
            } finally {
                this.isLoading = false;
            }
        },

        async runDetection() {
            this.isRunningDetection = true;
            this.showRunDetectionModal = false;
            this.startRunFeedback();
            let threshold = parseFloat(this.detectionConfig.similarity_threshold);
            if (isNaN(threshold)) {
                threshold = 0.6;
            }

            try {
                let data = await Platform.fetch.post('/duplicate-detection/simple/api/run-detection', {
                    method: this.detectionConfig.strategy || 'hybrid',
                    threshold: threshold,
                    run_name: this.detectionConfig.run_name || ''
                });

                // Platform.fetch throws on non-ok, so reaching here means HTTP succeeded.
                // The payload may still indicate logical failure via data.success.
                if (data.success) {
                    await this.loadStats();
                    await this.loadGroups();
                    let feedbackMessage = data.message || 'Duplicate detection finished successfully.';
                    if (data.warning) {
                        feedbackMessage += ' ' + data.warning;
                    }
                    this.runFeedback = {
                        status: 'success',
                        title: 'Detection completed',
                        message: feedbackMessage,
                        run_id: data.run_id || null,
                        run_name: this.detectionConfig.run_name || '',
                        groups_found: data.groups_found != null ? data.groups_found : 0,
                        exact_matches: data.exact_matches != null ? data.exact_matches : 0,
                        fuzzy_matches: data.fuzzy_matches != null ? data.fuzzy_matches : 0,
                        applications_analyzed: data.applications_analyzed != null ? data.applications_analyzed : 0,
                        estimated_savings: data.estimated_savings != null ? data.estimated_savings : 0,
                        duration_seconds: data.duration_seconds != null ? data.duration_seconds : this.runElapsedSeconds
                    };
                } else {
                    this.runFeedback = {
                        status: 'error',
                        title: 'Detection failed',
                        message: data.error || data.message || 'Unknown error',
                        run_id: null,
                        run_name: '',
                        groups_found: 0,
                        exact_matches: 0,
                        fuzzy_matches: 0,
                        applications_analyzed: 0,
                        estimated_savings: 0,
                        duration_seconds: null
                    };
                }
            } catch (error) {
                this.runFeedback = {
                    status: 'error',
                    title: 'Detection failed',
                    message: error.message || 'Unexpected error occurred',
                    run_id: null,
                    run_name: '',
                    groups_found: 0,
                    exact_matches: 0,
                    fuzzy_matches: 0,
                    applications_analyzed: 0,
                    estimated_savings: 0,
                    duration_seconds: null
                };
            } finally {
                this.isRunningDetection = false;
                this.stopRunFeedbackTimer();
                this.$nextTick(function() { lucide.createIcons(); });
            }
        },

        async addToConsolidation(groupId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Add to Consolidation List',
                content: '<p class="text-sm text-muted-foreground">Add all applications from this group to the consolidation list?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Add to List', class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90', action: 'add', handler: async function() {
                        try {
                            let data = await Platform.fetch.post('/duplicate-detection/api/groups/' + groupId + '/add-to-consolidation', {});
                            // Platform.fetch throws on non-ok, so reaching here means HTTP succeeded.
                            // The payload may still indicate logical failure via data.success.
                            if (data.success) {
                                let msg = 'Added ' + (data.added_count || 0) + ' application(s) to consolidation list' +
                                    (data.skipped_count ? ' (' + data.skipped_count + ' already listed)' : '');
                                Platform.toast.info(msg);
                                await self.loadGroups();
                            } else {
                                Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                            }
                        } catch (error) {
                            Platform.toast.error('Failed to add to consolidation: ' + error.message);
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        async dismissGroup(groupId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Dismiss Group',
                content: '<p class="text-sm text-muted-foreground">Are you sure you want to dismiss this group? It will be marked as ignored.</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Dismiss', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'dismiss', handler: async function() {
                        try {
                            let data = await Platform.fetch.post('/duplicate-detection/api/groups/' + groupId + '/ignore', {});
                            // Platform.fetch throws on non-ok, so reaching here means HTTP succeeded.
                            // The payload may still indicate logical failure via data.success.
                            if (data.success) {
                                self.allGroups = self.allGroups.filter(function(g) { return g.id !== groupId; });
                                await self.loadStats();
                            } else {
                                Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                            }
                        } catch (error) {
                            Platform.toast.error('Failed to dismiss group: ' + error.message);
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        toggleSelectGroup(groupId) {
            let index = this.selectedGroups.indexOf(groupId);
            if (index === -1) {
                this.selectedGroups.push(groupId);
            } else {
                this.selectedGroups.splice(index, 1);
            }
        },

        clearSelection() {
            this.selectedGroups = [];
        },

        isAllPageSelected() {
            let self = this;
            let page = this.paginatedGroups();
            if (page.length === 0) return false;
            return page.every(function(group) {
                return self.selectedGroups.indexOf(group.id) !== -1;
            });
        },

        toggleSelectAll() {
            let self = this;
            let pageIds = this.paginatedGroups().map(function(group) { return group.id; });
            if (this.isAllPageSelected()) {
                // Clear only this page, so a selection made on another page of
                // results is not silently discarded.
                this.selectedGroups = this.selectedGroups.filter(function(id) {
                    return pageIds.indexOf(id) === -1;
                });
                return;
            }
            let merged = this.selectedGroups.slice();
            pageIds.forEach(function(id) {
                if (merged.indexOf(id) === -1) merged.push(id);
            });
            this.selectedGroups = merged;
        },

        /**
         * Apply a per-group action across the selection.
         *
         * The API exposes one action per group and no bulk endpoint, so this
         * fans out. Partial failure is reported as partial and the groups that
         * failed stay selected so the user can retry exactly those - reporting
         * a clean success for a run that half-failed would be inventing an
         * outcome the system does not have.
         */
        async _applyToSelection(ids, action, pastTense) {
            let succeeded = 0;
            let failed = [];
            for (let i = 0; i < ids.length; i++) {
                try {
                    let data = await action(ids[i]);
                    // Platform.fetch throws on a non-ok response, so reaching
                    // here means HTTP succeeded; the payload may still say no.
                    if (data && data.success === false) {
                        failed.push(ids[i]);
                    } else {
                        succeeded++;
                    }
                } catch (error) {
                    failed.push(ids[i]);
                }
            }

            if (succeeded > 0) {
                Platform.toast.info(succeeded + ' group(s) ' + pastTense + '.');
            }
            if (failed.length > 0) {
                Platform.toast.error(
                    failed.length + ' group(s) could not be processed and remain selected.'
                );
            }
            this.selectedGroups = failed;

            await this.loadGroups();
            await this.loadStats();
        },

        async bulkAddToConsolidation() {
            let self = this;
            let ids = this.selectedGroups.slice();
            if (ids.length === 0) return;
            let modalId = window.modalManager.createModal({
                title: 'Add to Consolidation List',
                content: '<p class="text-sm text-muted-foreground">Add all applications from ' +
                    ids.length + ' selected group(s) to the consolidation list?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Add to List', class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90', action: 'add', handler: async function() {
                        await self._applyToSelection(ids, function(groupId) {
                            return Platform.fetch.post(
                                '/duplicate-detection/api/groups/' + groupId + '/add-to-consolidation', {}
                            );
                        }, 'added to the consolidation list');
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        async bulkDismiss() {
            let self = this;
            let ids = this.selectedGroups.slice();
            if (ids.length === 0) return;
            let modalId = window.modalManager.createModal({
                title: 'Dismiss Groups',
                content: '<p class="text-sm text-muted-foreground">Dismiss ' + ids.length +
                    ' selected group(s)? They will be marked as ignored.</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Dismiss', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'dismiss', handler: async function() {
                        await self._applyToSelection(ids, function(groupId) {
                            return Platform.fetch.post(
                                '/duplicate-detection/api/groups/' + groupId + '/ignore', {}
                            );
                        }, 'dismissed');
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        formatNumber(num) {
            if (!num) return '0';
            return new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(num);
        },

        formatPercent(num) {
            if (!num) return '0%';
            return Math.round(num * 100) + '%';
        },

        formatDate(dateString) {
            if (!dateString) return 'N/A';
            let date = new Date(dateString);
            return date.toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' });
        },

        formatRunStatus(status) {
            if (!status) return 'Unknown';
            return status.charAt(0).toUpperCase() + status.slice(1);
        },

        getStrategyLabel(strategy) {
            let strategyMap = {
                hybrid: 'Hybrid detection',
                fast: 'Fast detection',
                enhanced: 'Enhanced detection'
            };
            return strategyMap[strategy] || 'Duplicate detection';
        },

        formatDuration(totalSeconds) {
            let seconds = Number(totalSeconds || 0);
            if (seconds <= 0) {
                return '<1s';
            }
            if (seconds < 60) {
                return seconds + 's';
            }
            let minutes = Math.floor(seconds / 60);
            let remainingSeconds = seconds % 60;
            return minutes + 'm ' + remainingSeconds + 's';
        }
    };
}
