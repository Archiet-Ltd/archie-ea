/**
 * components/gantt_timeline.js — Gantt timeline Alpine.js component (v2)
 *
 * Composes Platform.gantt.alpineMixin() and layers on:
 *   - Per-day task bar styling (position, accent stripe, progress overlay, hover)
 *   - Status badge classes (full platform STATUS_CLASSES)
 *   - Default status color hex map (for bar fills when API doesn't provide)
 *   - Detail panel helpers (metadata, null formatting, dependencies bidirectional)
 *   - Duration / remaining calculations
 *   - Milestone rendering helpers
 *   - Group helpers (label, count, summary)
 *   - Tooltip content
 *   - ARIA labels
 *
 * Requires: Platform.gantt (ui/gantt.js), Alpine.js
 *
 * NOTE: Task bar colors use inline styles from config.statusColors (data-driven hex).
 * This is a deliberate exception to token migration rules — see design doc Section 13.
 */

(function (global) {
    'use strict';

    if (!global.Platform || !global.Platform.gantt) {
        throw new Error(
            '[Platform.ganttTimeline] ui/gantt.js must be loaded before components/gantt_timeline.js'
        );
    }

    let ROW_HEIGHT   = global.Platform.gantt.ROW_HEIGHT;
    let GROUP_HEIGHT = global.Platform.gantt.GROUP_HEIGHT;

    // ── STATUS_CLASSES (full platform set, semantic tokens) ────────────────
    let STATUS_CLASSES = {
        'identified':       'bg-muted text-foreground',
        'under_review':     'bg-primary/10 text-primary',
        'impact_assessed':  'bg-primary/10 text-primary',
        'migration_planned':'bg-violet-500/10 text-violet-600',
        'planned':          'bg-primary/10 text-primary',
        'planning':         'bg-violet-500/10 text-violet-600',
        'approved':         'bg-amber-500/10 text-amber-600',
        'in_progress':      'bg-orange-500/10 text-orange-600',
        'completed':        'bg-emerald-500/10 text-emerald-600',
        'tolerate':         'bg-muted text-foreground',
        'invest':           'bg-emerald-500/10 text-emerald-600',
        'migrate':          'bg-primary/10 text-primary',
        'eliminate':        'bg-destructive/10 text-destructive',
        'decommission':     'bg-destructive/10 text-destructive',
        'retire':           'bg-destructive/10 text-destructive',
        'merge':            'bg-amber-500/10 text-amber-600',
        'replace':          'bg-primary/10 text-primary',
        'modernize':        'bg-violet-500/10 text-violet-600',
        'pending_review':   'bg-muted text-foreground',
        'critical':         'bg-destructive/10 text-destructive',
        'high':             'bg-orange-500/10 text-orange-600',
        'medium':           'bg-amber-500/10 text-amber-600',
        'low':              'bg-emerald-500/10 text-emerald-600',
        'active':           'bg-emerald-500/10 text-emerald-600',
        'retiring':         'bg-amber-500/10 text-amber-600',
        'retired':          'bg-muted text-foreground',
        'deprecated':       'bg-destructive/10 text-destructive',
        'not_started':      'bg-muted text-foreground',
        'at_risk':          'bg-amber-500/10 text-amber-600',
        'blocked':          'bg-destructive/10 text-destructive',
        'migrating':        'bg-teal-500/10 text-teal-600',
        'draft':            'bg-muted text-foreground',
        'submitted':        'bg-primary/10 text-primary',
        'rejected':         'bg-destructive/10 text-destructive',
        'archived':         'bg-muted text-foreground',
        'cancelled':        'bg-muted text-foreground'
    };

    // ── DEFAULT STATUS COLORS (hex, for bar fills) ────────────────────────
    // Used when API response does not provide config.statusColors
    let DEFAULT_STATUS_COLORS = {
        'identified':       { fill: '#9ca3af', bg: '#f3f1ee', text: '#4b5563' },
        'planned':          { fill: '#ea6a47', bg: '#fef0ec', text: '#9a3412' },
        'planning':         { fill: '#8b5cf6', bg: '#f5f3ff', text: '#6d28d9' },
        'in_progress':      { fill: '#3b82f6', bg: '#eff6ff', text: '#1d4ed8' },
        'at_risk':          { fill: '#f59e0b', bg: '#fffbeb', text: '#92400e' },
        'blocked':          { fill: '#ef4444', bg: '#fef2f2', text: '#b91c1c' },
        'completed':        { fill: '#22c55e', bg: '#f0fdf4', text: '#15803d' },
        'approved':         { fill: '#22c55e', bg: '#f0fdf4', text: '#15803d' },
        'not_started':      { fill: '#d1d5db', bg: '#f9fafb', text: '#6b7280' },
        'critical':         { fill: '#ef4444', bg: '#fef2f2', text: '#b91c1c' },
        'high':             { fill: '#f97316', bg: '#fff7ed', text: '#9a3412' },
        'medium':           { fill: '#f59e0b', bg: '#fffbeb', text: '#92400e' },
        'low':              { fill: '#22c55e', bg: '#f0fdf4', text: '#15803d' },
        'decommission':     { fill: '#ea6a47', bg: '#fef0ec', text: '#9a3412' },
        'migrating':        { fill: '#14b8a6', bg: '#f0fdfa', text: '#0f766e' },
        'tolerate':         { fill: '#9ca3af', bg: '#f3f1ee', text: '#4b5563' },
        'invest':           { fill: '#22c55e', bg: '#f0fdf4', text: '#15803d' },
        'migrate':          { fill: '#3b82f6', bg: '#eff6ff', text: '#1d4ed8' },
        'eliminate':        { fill: '#ef4444', bg: '#fef2f2', text: '#b91c1c' },
        'retire':           { fill: '#ef4444', bg: '#fef2f2', text: '#b91c1c' },
        'merge':            { fill: '#f59e0b', bg: '#fffbeb', text: '#92400e' },
        'replace':          { fill: '#3b82f6', bg: '#eff6ff', text: '#1d4ed8' },
        'modernize':        { fill: '#8b5cf6', bg: '#f5f3ff', text: '#6d28d9' },
        'under_review':     { fill: '#ea6a47', bg: '#fef0ec', text: '#9a3412' },
        'active':           { fill: '#22c55e', bg: '#f0fdf4', text: '#15803d' },
        'retiring':         { fill: '#f59e0b', bg: '#fffbeb', text: '#92400e' },
        'retired':          { fill: '#9ca3af', bg: '#f3f1ee', text: '#4b5563' },
        'deprecated':       { fill: '#ef4444', bg: '#fef2f2', text: '#b91c1c' }
    };

    let FALLBACK_COLORS = { fill: '#9ca3af', bg: '#f3f1ee', text: '#4b5563' };

    // ── Mixin factory ────────────────────────────────────────────────────────

    function mixin(cfg) {
        cfg = cfg || {};
        let baseMixin = global.Platform.gantt.alpineMixin(cfg);

        return Object.assign({}, baseMixin, {

            // ═══════════════════════════════════════════════════════════════
            // TASK BAR STYLING
            // ═══════════════════════════════════════════════════════════════

            /**
             * Returns inline style string for a task bar.
             * Uses per-day positioning from Platform.gantt.
             */
            getTaskBarStyle: function (task) {
                if (task.unscheduled) return 'display: none;';
                let left  = this.getBarLeft(task);
                let width = this.getBarWidth(task);
                let colors = this._getStatusColors(task.status);
                return 'position: absolute;' +
                       ' left: ' + left + 'px;' +
                       ' width: ' + width + 'px;' +
                       ' height: 28px;' +
                       ' background-color: ' + colors.bg + ';' +
                       ' border-left: 3px solid ' + colors.fill + ';' +
                       ' border-radius: 4px;' +
                       ' top: 50%; transform: translateY(-50%);' +
                       ' cursor: pointer; z-index: 3;' +
                       ' transition: box-shadow 0.15s, transform 0.1s;';
            },

            /**
             * Progress fill overlay style.
             */
            getProgressBarStyle: function (task) {
                let feats = this.config.features;
                if (feats && feats.progress === false) return 'display: none;';
                let progress = Math.max(0, Math.min(100, task.progress || 0));
                let colors = this._getStatusColors(task.status);
                return 'width: ' + progress + '%;' +
                       ' height: 100%;' +
                       ' background-color: ' + colors.fill + ';' +
                       ' opacity: 0.25;' +
                       ' border-radius: 0 4px 4px 0;' +
                       ' position: absolute; left: 0; top: 0;';
            },

            /**
             * Text color for bar label.
             */
            getTaskTextColor: function (task) {
                let colors = this._getStatusColors(task.status);
                return 'color: ' + colors.text + ';';
            },

            /**
             * Whether bar is wide enough for inline text.
             */
            isBarWideEnough: function (task) {
                return this.getBarWidth(task) > 100;
            },

            /**
             * Accent stripe color (the 3px left border).
             */
            getAccentColor: function (task) {
                return this._getStatusColors(task.status).fill;
            },

            /**
             * Status color lookup with API override, then default map, then fallback.
             */
            _getStatusColors: function (status) {
                if (this.config && this.config.statusColors && this.config.statusColors[status]) {
                    return this.config.statusColors[status];
                }
                return DEFAULT_STATUS_COLORS[status] || FALLBACK_COLORS;
            },

            // ═══════════════════════════════════════════════════════════════
            // STATUS BADGE
            // ═══════════════════════════════════════════════════════════════

            getStatusBadgeClass: function (status) {
                return STATUS_CLASSES[status] || STATUS_CLASSES['identified'] || 'bg-muted text-foreground';
            },

            // ═══════════════════════════════════════════════════════════════
            // FORMATTING HELPERS
            // ═══════════════════════════════════════════════════════════════

            formatDate: function (d) {
                if (!d) return '\u2014';
                let date = new Date(d);
                if (isNaN(date.getTime())) return '\u2014';
                return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
            },

            formatStatus: function (s) {
                if (!s) return '';
                return s.replace(/_/g, ' ').replace(/\b\w/g, function (l) { return l.toUpperCase(); });
            },

            calcDuration: function (start, end) {
                if (!start || !end) return '\u2014';
                let s = new Date(start);
                let e = new Date(end);
                if (isNaN(s.getTime()) || isNaN(e.getTime())) return '\u2014';
                let days = Math.ceil((e - s) / 86400000);
                if (days <= 0) return '\u2014';
                if (days === 1) return '1 day';
                if (days < 14) return days + ' days';
                if (days < 60) return Math.round(days / 7) + ' weeks';
                return Math.round(days / 30) + ' months';
            },

            calcRemaining: function (end) {
                if (!end) return '\u2014';
                let e = new Date(end);
                if (isNaN(e.getTime())) return '\u2014';
                let days = Math.ceil((e - new Date()) / 86400000);
                if (days < 0) return Math.abs(days) + ' days overdue';
                if (days === 0) return 'Due today';
                if (days === 1) return '1 day';
                if (days < 14) return days + ' days';
                if (days < 60) return Math.round(days / 7) + ' weeks';
                return Math.round(days / 30) + ' months';
            },

            /**
             * Format a metadata value with null handling.
             * Null/undefined/empty: '\u2014' (em dash)
             * Financial values (containing currency symbols): displayed as-is
             */
            formatMetaValue: function (value) {
                if (value === null || value === undefined || value === '') return '\u2014';
                if (Array.isArray(value)) return value.join(', ');
                return String(value);
            },

            isNullValue: function (value) {
                return value === null || value === undefined || value === '';
            },

            // ═══════════════════════════════════════════════════════════════
            // DETAIL PANEL HELPERS
            // ═══════════════════════════════════════════════════════════════

            /**
             * Returns metadata field array for the detail panel.
             * Priority: _metadataFields (caller) > config.detailFields (API) > auto-detect
             */
            getDetailFields: function (task) {
                if (!task) return [];

                let fields = this._metadataFields || (this.config && this.config.detailFields);
                if (fields && fields.length > 0) {
                    let self = this;
                    return fields.map(function (field) {
                        let value = self.resolveField(task, field.key);
                        if (Array.isArray(value)) value = value.join(', ');
                        let isNull = value === null || value === undefined || value === '';
                        return {
                            label:     field.label || field.key,
                            value:     isNull ? '\u2014' : String(value),
                            isNull:    isNull,
                            highlight: field.highlight || null
                        };
                    });
                }

                return this.getAutoDetailFields(task);
            },

            /**
             * Auto-detect metadata from task.meta keys.
             */
            getAutoDetailFields: function (task) {
                if (!task || !task.meta || typeof task.meta !== 'object') return [];
                let SKIP = ['id', 'group', 'name', 'start_date', 'end_date', 'status', 'progress', 'dependencies'];
                let results = [];
                Object.keys(task.meta).forEach(function (key) {
                    if (SKIP.indexOf(key) > -1) return;
                    let value = task.meta[key];
                    if (Array.isArray(value)) value = value.join(', ');
                    let isNull = value === null || value === undefined || value === '';
                    let label = key.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
                    results.push({
                        label: label,
                        value: isNull ? '\u2014' : String(value),
                        isNull: isNull,
                        highlight: null
                    });
                });
                return results;
            },

            /**
             * Configured action buttons for detail panel.
             */
            getDetailPanelActions: function () {
                return this._detailPanelActions || [];
            },

            // ═══════════════════════════════════════════════════════════════
            // DEPENDENCY HELPERS
            // ═══════════════════════════════════════════════════════════════

            /**
             * Tasks that the given task depends ON (must finish before this task).
             */
            getTaskDependencies: function (task) {
                if (!task) return [];
                let self = this;
                let results = [];
                self._depPairs.forEach(function (pair) {
                    if (pair[1] === task.id) {
                        let src = self.getTaskById(pair[0]);
                        if (src) results.push(src);
                    }
                });
                // Also check task.dependencies array (API format)
                if (task.dependencies && task.dependencies.length) {
                    task.dependencies.forEach(function (depId) {
                        // Avoid duplicates
                        let already = results.some(function (r) { return r.id === depId; });
                        if (!already) {
                            let t = self.getTaskById(depId);
                            if (t) results.push(t);
                        }
                    });
                }
                return results;
            },

            /**
             * Tasks that depend on the given task (this task blocks them).
             */
            getTaskDependents: function (task) {
                if (!task) return [];
                let self = this;
                let results = [];
                self._depPairs.forEach(function (pair) {
                    if (pair[0] === task.id) {
                        let tgt = self.getTaskById(pair[1]);
                        if (tgt) results.push(tgt);
                    }
                });
                // Also check all tasks' dependencies arrays
                self.tasks.forEach(function (t) {
                    if (t.dependencies && t.dependencies.indexOf(task.id) > -1) {
                        let already = results.some(function (r) { return r.id === t.id; });
                        if (!already) results.push(t);
                    }
                });
                return results;
            },

            /**
             * Milestones relevant to the task's group or global.
             */
            getTaskMilestones: function (task) {
                if (!task || !this.milestones.length) return [];
                return this.milestones.filter(function (m) {
                    return !m.group || m.group === task.group;
                });
            },

            // ═══════════════════════════════════════════════════════════════
            // GROUP HELPERS
            // ═══════════════════════════════════════════════════════════════

            isGroupCollapsed: function (groupId) {
                return this.collapsedGroupIds.indexOf(groupId) > -1;
            },

            getGroupTaskCount: function (groupId) {
                let count = 0;
                this.tasks.forEach(function (t) {
                    if (t.group === groupId) count++;
                });
                return count;
            },

            getGroupLabel: function () {
                return (this.config && this.config.groupLabel) || 'Group';
            },

            getGroupLabelById: function (groupId) {
                for (let i = 0; i < this.groups.length; i++) {
                    if (this.groups[i].id === groupId) return this.groups[i].label;
                }
                return groupId || '';
            },

            // ═══════════════════════════════════════════════════════════════
            // MILESTONE RENDERING
            // ═══════════════════════════════════════════════════════════════

            getMilestoneOffset: function (milestone) {
                return this.dateToX(milestone.date);
            },

            // ═══════════════════════════════════════════════════════════════
            // TODAY MARKER
            // ═══════════════════════════════════════════════════════════════

            getTodayMarkerOffset: function () {
                return this.todayX;
            },

            // ═══════════════════════════════════════════════════════════════
            // FILTER HELPERS
            // ═══════════════════════════════════════════════════════════════

            getDistinctStatuses: function () {
                let seen = {};
                let statuses = [];
                this.tasks.forEach(function (t) {
                    if (t.status && !seen[t.status]) {
                        seen[t.status] = true;
                        statuses.push(t.status);
                    }
                });
                return statuses.sort();
            },

            getFilteredTaskCount: function () {
                if (!this.getHasActiveFilters()) return this.tasks.length;
                let ft = (this.filterText || '').toLowerCase();
                let fs = this.filterStatus || '';
                return this.tasks.filter(function (t) {
                    if (ft && (t.name || '').toLowerCase().indexOf(ft) === -1) return false;
                    if (fs && t.status !== fs) return false;
                    return true;
                }).length;
            },

            getHasActiveFilters: function () {
                return !!(this.filterText || this.filterStatus);
            },

            // ═══════════════════════════════════════════════════════════════
            // FEATURE CHECKS
            // ═══════════════════════════════════════════════════════════════

            hasFeature: function (name) {
                // Default: all features enabled unless explicitly disabled
                if (!this.config.features) return true;
                let val = this.config.features[name];
                return val !== false;
            },

            canExport: function (format) {
                if (!this.config.features) return true; // default: enabled
                if (!this.config.features.export) return true;
                return Array.isArray(this.config.features.export) &&
                       this.config.features.export.indexOf(format) > -1;
            },

            // ═══════════════════════════════════════════════════════════════
            // ARIA HELPERS
            // ═══════════════════════════════════════════════════════════════

            // The accessible name begins with the row's visible text (WCAG 2.5.3,
            // Label in Name), then adds context the sighted reader gets from the
            // layout.
            getRowAriaLabel: function (row) {
                if (row.type === 'group') {
                    return row.label + ' ' + row.taskCount + ', group, ' + row.taskCount + ' items' +
                           (row.collapsed ? ', collapsed' : ', expanded');
                }
                if (row.type === 'task' && row.data) {
                    let t = row.data;
                    let showsProgress = t.progress > 0 && t.progress < 100;
                    let parts = [showsProgress ? t.name + ' ' + t.progress + '%' : t.name];
                    if (t.status) parts.push(t.status.replace(/_/g, ' '));
                    if (t.progress != null && !showsProgress) parts.push(t.progress + ' percent');
                    if (t.start_date) parts.push(t.start_date + ' to ' + (t.end_date || ''));
                    return parts.join(', ');
                }
                return '';
            }
        });
    }

    // ── SVG <template> polyfill ──────────────────────────────────────────────
    // Browsers treat <template> inside <svg> as SVG elements (no .content property).
    // Alpine.js x-for requires HTML <template> elements.  This polyfill converts
    // SVG-namespace templates into proper HTML templates before Alpine walks the DOM.
    // See: https://github.com/alpinejs/alpine/issues/637
    document.addEventListener('alpine:init', function _svgTemplateFix() {
        let svgTemplates = document.querySelectorAll('svg template');
        svgTemplates.forEach(function (el) {
            let htmlTpl = document.createElement('template');
            // Copy all attributes (x-for, :key, etc.)
            let attrs = Array.from(el.attributes);
            attrs.forEach(function (attr) {
                htmlTpl.setAttribute(attr.name, attr.value);
            });
            // Move child elements into the HTML template's content fragment
            while (el.firstElementChild) {
                htmlTpl.content.appendChild(el.firstElementChild);
            }
            el.replaceWith(htmlTpl);
        });
    });

    // ── Alpine.data registration ─────────────────────────────────────────────
    document.addEventListener('alpine:init', function () {
        if (typeof Alpine !== 'undefined') {
            Alpine.data('ganttTimeline', function (cfg) {
                return Object.assign({}, mixin(cfg), {
                    init: function () { this._ganttInit(); }
                });
            });
        }
    });

    // ── Public API ────────────────────────────────────────────────────────────
    let ganttTimelineModule = {
        mixin:                mixin,
        STATUS_CLASSES:       STATUS_CLASSES,
        DEFAULT_STATUS_COLORS: DEFAULT_STATUS_COLORS
    };

    global.Platform.register('ganttTimeline', ganttTimelineModule);

}(window));
