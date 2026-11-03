let APP_CONFIG = window.__APP_CONFIG__ || {};

// Roadmap Builder Application
let RoadmapBuilder = {
    workPackages: [],
    plateaus: [],
    currentTab: 'timeline',
    editingWorkPackageId: null,

    init: function() {
        this.setupEventListeners();
        this.loadData();
        lucide.createIcons();
    },

    setupEventListeners: function() {
        let self = this;
        // Tab navigation
        document.querySelectorAll('.tab-btn').forEach(function(btn) {
            btn.addEventListener('click', function(e) { self.switchTab(e.target.dataset.tab); });
        });

        // Dialog controls
        document.getElementById('btn-add-work-package').addEventListener('click', function() {
            self.resetWorkPackageForm();
            self.showDialog('add-wp-dialog');
        });
        document.getElementById('btn-add-plateau').addEventListener('click', function() { self.showDialog('add-plateau-dialog'); });
        document.getElementById('btn-critical-path').addEventListener('click', function() { self.showCriticalPath(); });

        document.getElementById('cancel-add-wp').addEventListener('click', function() {
            self.resetWorkPackageForm();
            self.hideDialog('add-wp-dialog');
        });
        document.getElementById('cancel-add-plateau').addEventListener('click', function() { self.hideDialog('add-plateau-dialog'); });
        document.getElementById('close-critical-path').addEventListener('click', function() { self.hideDialog('critical-path-dialog'); });

        // Form submissions
        document.getElementById('add-wp-form').addEventListener('submit', function(e) {
            e.preventDefault();
            self.createWorkPackage();
        });
        document.getElementById('add-plateau-form').addEventListener('submit', function(e) {
            e.preventDefault();
            self.createPlateau();
        });

        // Filters
        document.getElementById('filter-status').addEventListener('change', function() { self.renderWorkPackagesList(); });
        document.getElementById('filter-priority').addEventListener('change', function() { self.renderWorkPackagesList(); });
        document.getElementById('timeline-group-by').addEventListener('change', function() { self.loadTimeline(); });

        // Delegated actions for dynamically rendered rows/cards.
        document.addEventListener('click', function(e) {
            let actionEl = e.target.closest('[data-action]');
            if (!actionEl) return;

            let action = actionEl.getAttribute('data-action');
            let id = actionEl.getAttribute('data-id');

            if (action === 'RoadmapBuilder.showWorkPackageDetail') {
                self.showWorkPackageDetail(id);
            } else if (action === 'RoadmapBuilder.editWorkPackage') {
                e.preventDefault();
                e.stopPropagation();
                self.editWorkPackage(id);
            } else if (action === 'RoadmapBuilder.deleteWorkPackage') {
                e.preventDefault();
                e.stopPropagation();
                self.deleteWorkPackage(id);
            }
        });
    },

    loadData: function() {
        let self = this;
        Promise.all([
            self.loadWorkPackages(),
            self.loadPlateaus(),
            self.loadSummary()
        ]).then(function() {
            self.loadTimeline();
        });
    },

    loadWorkPackages: function() {
        let self = this;
        return Platform.fetch('/api/roadmap-builder/work-packages')
            .then(function(data) {
                self.workPackages = data.data.work_packages || [];
                self.renderWorkPackagesList();
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error loading work packages. Please retry.', 'error');
            });
    },

    loadPlateaus: function() {
        let self = this;
        return Platform.fetch('/api/roadmap-builder/plateaus')
            .then(function(data) {
                self.plateaus = data.data.plateaus || [];
                self.renderPlateaus();
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error loading plateaus. Please retry.', 'error');
            });
    },

    loadSummary: function() {
        let self = this;
        return Platform.fetch('/api/roadmap-builder/summary')
            .then(function(data) {
                if (data.data) {
                    document.getElementById('total-packages').textContent = data.data.total_work_packages || 0;
                    document.getElementById('completed-packages').textContent = data.data.completed_count || 0;
                    document.getElementById('total-plateaus').textContent = data.data.total_plateaus || 0;
                    document.getElementById('total-cost').textContent = (data.data.total_estimated_cost == null ? Platform.figure.NOT_RECORDED : '$' + data.data.total_estimated_cost.toLocaleString());
                }
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error loading roadmap summary. Please retry.', 'error');
            });
    },

    loadTimeline: function() {
        let self = this;
        let groupBy = document.getElementById('timeline-group-by').value;
        Platform.fetch.get('/api/roadmap-builder/timeline', { group_by: groupBy }, { silent: true })
            .then(function(data) {
                self.renderTimeline(data.data);
            })
            .catch(function(err) {
                safeHTML(document.getElementById('timeline-container'),
                    '<div class="flex items-center justify-center h-64 text-muted-foreground">' +
                        '<div class="text-center">' +
                            '<i data-lucide="alert-circle" class="h-8 w-8 mx-auto mb-2"></i>' +
                            '<p>Error loading timeline</p>' +
                        '</div>' +
                    '</div>');
                lucide.createIcons();
            });
    },

    renderTimeline: function(data) {
        let container = document.getElementById('timeline-container');
        if (!data || !data.groups || data.groups.length === 0) {
            safeHTML(container,
                '<div class="flex items-center justify-center h-64 text-muted-foreground">' +
                    '<div class="text-center">' +
                        '<i data-lucide="calendar-x" class="h-8 w-8 mx-auto mb-2"></i>' +
                        '<p>No work packages found</p>' +
                        '<p class="text-sm">Create a work package to get started</p>' +
                    '</div>' +
                '</div>');
            lucide.createIcons();
            return;
        }

        let html = '<div class="space-y-6">';
        data.groups.forEach(function(group) {
            html += '<div>' +
                '<h3 class="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">' + group.name + '</h3>' +
                '<div class="space-y-2">';
            (group.items || []).forEach(function(item) {
                let statusClass = 'status-' + item.status;
                let priorityClass = 'priority-' + item.priority;
                let priorityBadge = item.priority === 'high' ? 'bg-destructive/10 text-destructive' : item.priority === 'medium' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-500/10 text-emerald-700';
                html += '<div class="work-package-card p-4 bg-card border border-border rounded-lg ' + statusClass + ' ' + priorityClass + '" data-action="RoadmapBuilder.showWorkPackageDetail" data-id="' + item.id + '">' +
                    '<div class="flex justify-between items-start">' +
                        '<div>' +
                            '<div class="font-medium">' + item.name + '</div>' +
                            '<div class="text-sm text-muted-foreground mt-1">' + (item.start_date || 'No date') + ' - ' + (item.end_date || 'No date') + '</div>' +
                        '</div>' +
                        '<div class="flex items-center gap-2">' +
                            '<span class="px-2 py-1 text-xs rounded-full bg-muted">' + item.status + '</span>' +
                            '<span class="px-2 py-1 text-xs rounded-full ' + priorityBadge + '">' + item.priority + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>';
            });
            html += '</div></div>';
        });
        html += '</div>';

        safeHTML(container, html);
        lucide.createIcons();
    },

    renderWorkPackagesList: function() {
        let statusFilter = document.getElementById('filter-status').value;
        let priorityFilter = document.getElementById('filter-priority').value;

        let filtered = this.workPackages;
        if (statusFilter) {
            filtered = filtered.filter(function(wp) { return wp.status === statusFilter; });
        }
        if (priorityFilter) {
            filtered = filtered.filter(function(wp) { return wp.priority === priorityFilter; });
        }

        let container = document.getElementById('work-packages-list');
        if (filtered.length === 0) {
            safeHTML(container,
                '<div class="p-8 text-center text-muted-foreground">' +
                    '<i data-lucide="inbox" class="h-8 w-8 mx-auto mb-2"></i>' +
                    '<p>No work packages found</p>' +
                '</div>');
            lucide.createIcons();
            return;
        }

        safeHTML(container, filtered.map(function(wp) {
            let priorityBadge = wp.priority === 'high' ? 'bg-destructive/10 text-destructive' : wp.priority === 'medium' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-500/10 text-emerald-700';
            let costDisplay = wp.estimated_cost ? wp.estimated_cost.toLocaleString() : '0';
            return '<div class="p-4 hover:bg-accent/50">' +

                '<div class="flex justify-between items-start">' +
                    '<div>' +
                        '<div class="font-medium">' + wp.name + '</div>' +
                        '<div class="text-sm text-muted-foreground mt-1">' + (wp.description || 'No description') + '</div>' +
                        '<div class="flex gap-4 mt-2 text-xs text-muted-foreground">' +
                            '<span><i data-lucide="calendar" class="h-3 w-3 inline mr-1"></i>' + (wp.start_date || 'No start') + ' - ' + (wp.end_date || 'No end') + '</span>' +
                            '<span><i data-lucide="user" class="h-3 w-3 inline mr-1"></i>' + (wp.assigned_to || 'Unassigned') + '</span>' +
                            '<span><i data-lucide="dollar-sign" class="h-3 w-3 inline mr-1"></i>' + costDisplay + '</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="flex items-center gap-2">' +
                        '<span class="px-2 py-1 text-xs rounded-full bg-muted capitalize">' + wp.status + '</span>' +
                        '<span class="px-2 py-1 text-xs rounded-full ' + priorityBadge + '">' + wp.priority + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="mt-3 flex items-center justify-end gap-2">' +
                    '<button type="button" class="px-2.5 py-1.5 text-xs rounded-md border border-border hover:bg-accent" data-action="RoadmapBuilder.editWorkPackage" data-id="' + wp.id + '">Edit</button>' +
                    '<button type="button" class="px-2.5 py-1.5 text-xs rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10" data-action="RoadmapBuilder.deleteWorkPackage" data-id="' + wp.id + '">Delete</button>' +
                '</div>' +
            '</div>';
        }).join(''));
        lucide.createIcons();
    },

    renderPlateaus: function() {
        let container = document.getElementById('plateaus-timeline');
        if (this.plateaus.length === 0) {
            safeHTML(container,
                '<div class="p-8 text-center text-muted-foreground">' +
                    '<i data-lucide="mountain" class="h-8 w-8 mx-auto mb-2"></i>' +
                    '<p>No plateaus defined</p>' +
                    '<p class="text-sm">Create plateaus to define architecture states</p>' +
                '</div>');
            lucide.createIcons();
            return;
        }

        safeHTML(container, this.plateaus.map(function(plateau) {

            let typeColor = plateau.plateau_type === 'baseline' ? 'blue' :
                           plateau.plateau_type === 'target' ? 'green' : 'purple';
            return '<div class="p-4 bg-' + typeColor + '-50 border border-' + typeColor + '-200 rounded-lg">' +
                '<div class="flex justify-between items-start">' +
                    '<div>' +
                        '<div class="flex items-center gap-2">' +
                            '<i data-lucide="mountain" class="h-5 w-5 text-' + typeColor + '-600"></i>' +
                            '<span class="font-semibold text-' + typeColor + '-800">' + plateau.name + '</span>' +
                            '<span class="px-2 py-1 text-xs rounded-full bg-' + typeColor + '-100 text-' + typeColor + '-700 uppercase">' + plateau.plateau_type + '</span>' +
                        '</div>' +
                        '<div class="text-sm text-' + typeColor + '-700 mt-2">' + (plateau.description || '') + '</div>' +
                        '<div class="text-xs text-' + typeColor + '-600 mt-2">' +
                            '<i data-lucide="calendar" class="h-3 w-3 inline mr-1"></i>' +
                            (plateau.start_date || 'No start') + ' - ' + (plateau.end_date || 'No end') +
                        '</div>' +
                    '</div>' +
                '</div>' +
                (plateau.business_value ? '<div class="mt-3 p-2 bg-background/50 rounded text-sm text-' + typeColor + '-700"><strong>Business Value:</strong> ' + plateau.business_value + '</div>' : '') +
            '</div>';
        }).join(''));
        lucide.createIcons();
    },

    showCriticalPath: function() {
        let self = this;
        Platform.fetch('/api/roadmap-builder/critical-path')
            .then(function(data) {
                let content = document.getElementById('critical-path-content');

                if (data.data) {
                    let cp = data.data;
                    let pathHtml = (cp.critical_path || []).map(function(item, idx) {
                        return '<div class="flex items-center gap-2 p-3 bg-destructive/5 border border-destructive/20 rounded-md">' +
                            '<span class="w-6 h-6 rounded-full bg-destructive text-primary-foreground text-xs flex items-center justify-center">' + (idx + 1) + '</span>' +
                            '<span class="font-medium">' + item.name + '</span>' +
                            '<span class="text-sm text-muted-foreground ml-auto">' + (item.duration || 0) + ' days</span>' +
                        '</div>';
                    }).join('');

                    safeHTML(content,
                        '<div class="space-y-4">' +
                            '<div class="grid grid-cols-3 gap-4">' +
                                '<div class="p-4 bg-destructive/5 border border-destructive/20 rounded-lg text-center">' +
                                    '<div class="text-2xl font-bold text-destructive">' + (cp.total_duration || 0) + '</div>' +
                                    '<div class="text-sm text-destructive">Total Duration (days)</div>' +
                                '</div>' +
                                '<div class="p-4 bg-primary/5 border border-primary/20 rounded-lg text-center">' +
                                    '<div class="text-2xl font-bold text-primary">' + (cp.critical_path_length || 0) + '</div>' +
                                    '<div class="text-sm text-primary">Critical Path Items</div>' +
                                '</div>' +

                                '<div class="p-4 bg-amber-50 border border-amber-200 rounded-lg text-center">' +
                                    '<div class="text-2xl font-bold text-amber-700">' + (cp.total_slack || 0) + '</div>' +
                                    '<div class="text-sm text-amber-600">Total Slack (days)</div>' +
                                '</div>' +
                            '</div>' +
                            '<div>' +
                                '<h4 class="font-medium mb-2">Critical Path Sequence:</h4>' +
                                '<div class="space-y-2">' + pathHtml + '</div>' +
                            '</div>' +
                        '</div>');
                } else {
                    safeHTML(content, '<div class="text-center text-muted-foreground py-8">Unable to calculate critical path</div>');
                }

                self.showDialog('critical-path-dialog');
                lucide.createIcons();
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error loading critical path. Please retry.', 'error');
            });
    },

    createWorkPackage: function() {
        let self = this;
        let data = {
            name: document.getElementById('wp-name').value,
            description: document.getElementById('wp-description').value,
            start_date: document.getElementById('wp-start-date').value || null,
            end_date: document.getElementById('wp-end-date').value || null,
            priority: document.getElementById('wp-priority').value,
            status: document.getElementById('wp-status').value,
            assigned_to: document.getElementById('wp-assigned-to').value || null,
            estimated_cost: parseFloat(document.getElementById('wp-cost').value) || null
        };

        let isEdit = Boolean(self.editingWorkPackageId);
        let endpoint = isEdit
            ? '/api/roadmap-builder/work-packages/' + self.editingWorkPackageId
            : '/api/roadmap-builder/work-packages';

        let promise;
        if (isEdit) {
            promise = Platform.fetch.put(endpoint, data);
        } else {
            promise = Platform.fetch.post(endpoint, data);
        }

        promise
            .then(function(result) {
                self.hideDialog('add-wp-dialog');
                self.resetWorkPackageForm();
                self.loadData();
                self.showToast(isEdit ? 'Work package updated' : 'Work package created', 'success');
            })
            .catch(function(err) {
                self.showToast(err.message || (isEdit ? 'Error updating work package' : 'Error creating work package'), 'error');
            });
    },

    createPlateau: function() {
        let self = this;
        let data = {
            name: document.getElementById('plateau-name').value,
            plateau_type: document.getElementById('plateau-type').value,
            start_date: document.getElementById('plateau-start-date').value || null,
            end_date: document.getElementById('plateau-end-date').value || null,
            description: document.getElementById('plateau-description').value || null,
            business_value: document.getElementById('plateau-value').value || null
        };

        Platform.fetch.post('/api/roadmap-builder/plateaus', data)
            .then(function(result) {
                self.hideDialog('add-plateau-dialog');
                document.getElementById('add-plateau-form').reset();
                self.loadData();
                self.showToast('Plateau created', 'success');
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error creating plateau', 'error');
            });
    },

    showWorkPackageDetail: function(id) {
        this.editWorkPackage(id);
    },

    editWorkPackage: function(id) {
        let self = this;
        Platform.fetch('/api/roadmap-builder/work-packages/' + id)
            .then(function(result) {
                let wp = result.data;
                self.editingWorkPackageId = id;
                document.getElementById('add-wp-dialog-title').textContent = 'Edit Work Package';
                document.getElementById('add-wp-submit-btn').textContent = 'Save Changes';
                document.getElementById('wp-name').value = wp.name || '';
                document.getElementById('wp-description').value = wp.description || '';
                document.getElementById('wp-start-date').value = wp.start_date ? String(wp.start_date).slice(0, 10) : '';
                document.getElementById('wp-end-date').value = wp.end_date ? String(wp.end_date).slice(0, 10) : '';
                document.getElementById('wp-priority').value = wp.priority || 'medium';
                document.getElementById('wp-status').value = wp.status || 'planned';
                document.getElementById('wp-assigned-to').value = wp.assigned_to || '';
                document.getElementById('wp-cost').value = wp.estimated_cost ?? '';
                self.showDialog('add-wp-dialog');
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error loading work package', 'error');
            });
    },

    deleteWorkPackage: function(id) {
        let self = this;
        let modalId = window.modalManager.createModal({
            title: 'Delete Work Package',
            content: '<p class="text-sm text-muted-foreground">Delete this work package? This action cannot be undone.</p>',
            size: 'small',
            buttons: [
                { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: function() {
                    Platform.fetch.delete('/api/roadmap-builder/work-packages/' + id)
                        .then(function(result) {
                            self.loadData();
                            self.showToast('Work package deleted', 'success');
                        })
                        .catch(function(err) {
                            self.showToast(err.message || 'Error deleting work package', 'error');
                        });
                } }
            ]
        });
        window.modalManager.open(modalId);
    },

    resetWorkPackageForm: function() {
        this.editingWorkPackageId = null;
        document.getElementById('add-wp-form').reset();
        document.getElementById('add-wp-dialog-title').textContent = 'Add Work Package';
        document.getElementById('add-wp-submit-btn').textContent = 'Create';
        document.getElementById('wp-priority').value = 'medium';
        document.getElementById('wp-status').value = 'planned';
        document.getElementById('wp-cost').value = 0;
    },

    switchTab: function(tab) {
        let self = this;
        self.currentTab = tab;
        document.querySelectorAll('.tab-btn').forEach(function(btn) {
            btn.classList.remove('border-b-2', 'border-primary', 'text-primary');
            btn.classList.add('text-muted-foreground');
        });
        let activeBtn = document.querySelector('.tab-btn[data-tab="' + tab + '"]');
        activeBtn.classList.remove('text-muted-foreground');
        activeBtn.classList.add('border-b-2', 'border-primary', 'text-primary');

        document.querySelectorAll('.tab-panel').forEach(function(panel) { panel.classList.add('hidden'); });
        document.getElementById(tab + '-tab').classList.remove('hidden');

        if (tab === 'dependencies') {
            self.loadDependencyGraph();
        }
    },

    loadDependencyGraph: function() {
        let self = this;
        Platform.fetch('/api/roadmap-builder/dependency-graph', { silent: true })
            .then(function(data) {
                self.renderDependencyGraph(data.data);
            })
            .catch(function(err) {
                self.showToast(err.message || 'Error loading dependency graph. Please retry.', 'error');
            });
    },

    renderDependencyGraph: function(data) {
        let container = document.getElementById('dependency-graph');
        if (!data || !data.nodes || data.nodes.length === 0) {
            safeHTML(container,
                '<div class="flex items-center justify-center h-full text-muted-foreground">' +
                    '<div class="text-center">' +
                        '<i data-lucide="git-branch" class="h-8 w-8 mx-auto mb-2"></i>' +
                        '<p>No dependencies to display</p>' +
                    '</div>' +
                '</div>');
            lucide.createIcons();
            return;
        }

        // Simple node rendering (a full implementation would use ReactFlow or similar)
        let html = '<div class="p-4 space-y-4">';
        data.nodes.forEach(function(node) {
            let deps = data.edges.filter(function(e) { return e.target === node.id; });
            html += '<div class="p-4 bg-card border border-border rounded-lg">' +
                '<div class="font-medium">' + (node.data && node.data.label ? node.data.label : node.id) + '</div>' +
                (deps.length > 0 ? '<div class="text-sm text-muted-foreground mt-2">Depends on: ' + deps.map(function(d) { return d.source; }).join(', ') + '</div>' : '') +
            '</div>';
        });
        html += '</div>';
        safeHTML(container, html);
    },

    showDialog: function(id) {
        document.getElementById(id).classList.remove('hidden');
        document.getElementById(id).classList.add('flex');
    },

    hideDialog: function(id) {
        document.getElementById(id).classList.add('hidden');
        document.getElementById(id).classList.remove('flex');
    },

    showToast: function(message, type) {
        type = type || 'info';
        if (typeof window.showToast === 'function') {
            window.showToast(message, type);
        } else {
            Platform.toast.info(message);
        }
    }
};

document.addEventListener('DOMContentLoaded', function() {
    RoadmapBuilder.init();
});
