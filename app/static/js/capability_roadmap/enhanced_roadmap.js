/**
 * Enhanced Roadmap - External JavaScript
 * Extracted from enhanced_roadmap_fixed.html inline scripts
 * Depends on: window.__APP_CONFIG__ (injected by template)
 *
 * This file contains the capabilityRoadmapManager() Alpine.js component
 * which manages the enhanced capability-based roadmap with Gantt chart,
 * work package CRUD, tasks, deliverables, export, and capability hierarchy.
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

function capabilityRoadmapManager() {
    return {
        selectedImportance: '',
        selectedParentCapabilityId: '',  // Hierarchy filter: filter by L1 parent
        showAddModal: false,
        editingWorkPackage: null,
        workPackages: [],
        filteredWorkPackages: [],
        timelineDisplay: 'months',
        timelinePeriods: [],

        // Toast notification state
        toasts: [],
        toastIdCounter: 0,

        // Loading states
        isLoading: false,
        isSaving: false,
        loadingMessage: '',

        formData: {
            name: '',
            description: '',
            business_capability: '',  // Legacy single capability (primary)
            selected_capabilities: [],  // Multi-capability support: [{id, name, level}]
            assigned_to: '',
            custom_assigned_to: '',
            status: 'planned',
            start_date: '',
            end_date: '',
            progress_percentage: 0,
            estimated_cost: 0,
            priority: 'medium',
            risk_level: 'medium'
        },

        async initializeRoadmap() {
            // Initialize importance filter from server data
            try {
                let serverData = this.$el.dataset;
                if (serverData.selectedImportance) {
                    this.selectedImportance = serverData.selectedImportance;
                }
            } catch (error) {
                this.selectedImportance = '';
            }

            // Get timeline dates from data attributes with fallback
            let currentYear = new Date().getFullYear();
            let startDate = this.$el.dataset.startDate || (currentYear + '-01-01');
            let endDate = this.$el.dataset.endDate || ((currentYear + 5) + '-12-31');

            // Store in component for later use
            this.timelineStart = new Date(startDate);
            this.timelineEnd = new Date(endDate);

            // Calculate timeline periods based on display mode
            this.updateTimelineDisplay();

            // Initialize with server-side work packages
            try {
                let serverWorkPackages = JSON.parse(this.$el.dataset.workPackages);
                this.workPackages = serverWorkPackages;

                // Work packages loaded successfully
            } catch (error) {
                this.workPackages = [];
                // Fallback to API call
                this.loadWorkPackages();
            }

            // Load grouped capabilities for hierarchy filter dropdown
            await this.loadGroupedCapabilities();

            // Filter and display
            this.filterWorkPackages();
        },

        filterWorkPackages() {
            // Get all capability IDs that belong to the selected L1 hierarchy
            let allowedCapabilityIds = this.getCapabilityHierarchy(this.selectedParentCapabilityId);

            this.filteredWorkPackages = this.workPackages.filter(wp => {
                // Handle importance filtering
                let matchesImportance = !this.selectedImportance ||
                    this.selectedImportance === '' ||
                    wp.strategic_importance === this.selectedImportance;

                // Handle hierarchy filtering
                let matchesHierarchy = true;
                if (this.selectedParentCapabilityId && allowedCapabilityIds.size > 0) {
                    // Check if any of the work package's capabilities are in the allowed set
                    if (wp.capability_ids && wp.capability_ids.length > 0) {
                        matchesHierarchy = wp.capability_ids.some(id => allowedCapabilityIds.has(id));
                    } else if (wp.capability_id) {
                        matchesHierarchy = allowedCapabilityIds.has(wp.capability_id);
                    } else {
                        // Try matching by capability name
                        matchesHierarchy = this.isCapabilityInHierarchy(wp.business_capability, allowedCapabilityIds);
                    }
                }

                return matchesImportance && matchesHierarchy;
            });
        },

        // Get all capability IDs in the hierarchy of a parent L1 capability
        getCapabilityHierarchy(parentId) {
            if (!parentId) return new Set();

            let allowedIds = new Set();
            allowedIds.add(parentId);  // Include the L1 itself

            // Find all L2 capabilities that have this L1 as parent
            let l2Caps = this.groupedCapabilities.L2 || [];
            let childL2s = l2Caps.filter(cap => cap.parent_capability_id === parentId);
            childL2s.forEach(cap => allowedIds.add(cap.id));

            // Find all L3 capabilities that have this L1 or any of its L2 children as parent
            let l3Caps = this.groupedCapabilities.L3 || [];
            l3Caps.forEach(cap => {
                if (cap.parent_capability_id === parentId || allowedIds.has(cap.parent_capability_id)) {
                    allowedIds.add(cap.id);
                }
            });

            return allowedIds;
        },

        // Check if a capability name matches any capability in the allowed set
        isCapabilityInHierarchy(capabilityName, allowedIds) {
            if (!capabilityName) return false;

            // Search through all grouped capabilities to find by name
            for (let level of ['L1', 'L2', 'L3']) {
                let caps = this.groupedCapabilities[level] || [];
                let found = caps.find(cap => cap.name === capabilityName);
                if (found && allowedIds.has(found.id)) {
                    return true;
                }
            }
            return false;
        },

        updateTimelineDisplay() {
            let start = new Date(this.timelineStart);
            let end = new Date(this.timelineEnd);
            this.timelinePeriods = [];

            if (this.timelineDisplay === 'months') {
                let current = new Date(start);
                current.setDate(1);
                while (current <= end) {
                    this.timelinePeriods.push({
                        label: current.toLocaleDateString('en-US', { month: 'short', year: 'numeric' }),
                        start: new Date(current),
                        end: new Date(current.getFullYear(), current.getMonth() + 1, 0)
                    });
                    current.setMonth(current.getMonth() + 1);
                }
            } else if (this.timelineDisplay === 'weeks') {
                let current = new Date(start);
                // Set to start of week (Monday)
                current.setDate(current.getDate() - ((current.getDay() + 6) % 7));
                let weekNum = 1;
                while (current <= end) {
                    let weekStart = new Date(current);
                    let weekEnd = new Date(current);
                    weekEnd.setDate(weekEnd.getDate() + 6);
                    if (weekEnd > end) weekEnd = new Date(end);
                    this.timelinePeriods.push({
                        label: 'Week ' + weekNum + ' ' + weekStart.getFullYear(),
                        start: weekStart,
                        end: weekEnd
                    });
                    current.setDate(current.getDate() + 7);
                    weekNum++;
                }
            } else if (this.timelineDisplay === 'quarters') {
                let current = new Date(start);
                current.setDate(1);
                current.setMonth(Math.floor(current.getMonth() / 3) * 3);

                while (current <= end) {
                    let quarterStart = new Date(current);
                    let quarterEnd = new Date(current.getFullYear(), current.getMonth() + 3, 0);

                    this.timelinePeriods.push({
                        label: 'Q' + (Math.floor(current.getMonth() / 3) + 1) + ' ' + current.getFullYear(),
                        start: quarterStart,
                        end: quarterEnd
                    });

                    current.setMonth(current.getMonth() + 3);
                }
            } else if (this.timelineDisplay === 'years') {
                let current = new Date(start);
                current.setMonth(0, 1);

                while (current <= end) {
                    this.timelinePeriods.push({
                        label: current.getFullYear().toString(),
                        start: new Date(current),
                        end: new Date(current.getFullYear(), 11, 31)
                    });
                    current.setFullYear(current.getFullYear() + 1);
                }
            }

            // Force DOM updates after timeline periods are calculated
            this.$nextTick(() => {
                this.updateTimelineHeader();
                this.updateTimelineGrid();
                // Re-calculate work package positions for new timeline
                this.$nextTick(() => {
                    this.updateWorkPackagePositions();
                });
            });
        },

        updateTimelineHeader() {
            let headerElement = document.getElementById('timeline-header');

            // Calculate column width based on display mode
            let columnWidth = this.getColumnWidth();
            let totalWidth = this.timelinePeriods.length * columnWidth;

            if (headerElement && this.timelinePeriods.length > 0) {
                // Set the header container width to fit all columns
                headerElement.style.width = totalWidth + 'px';
                headerElement.style.minWidth = totalWidth + 'px';

                let headerHTML = this.timelinePeriods.map(period =>
                    '<div class="p-2 text-center text-xs border-r border-border bg-muted flex-shrink-0" style="width: ' + columnWidth + 'px; min-width: ' + columnWidth + 'px;">' + period.label + '</div>'
                ).join('');

                safeHTML(headerElement, headerHTML);
            }
        },

        getColumnWidth() {
            // Return appropriate column width based on timeline display mode
            switch (this.timelineDisplay) {
                case 'months': return 80;
                case 'quarters': return 120;
                case 'years': return 150;
                default: return 100;
            }
        },

        updateTimelineGrid() {
            // Calculate column width based on display mode
            let columnWidth = this.getColumnWidth();
            let totalWidth = this.timelinePeriods.length * columnWidth;

            // Update all timeline grid instances with unique IDs
            this.filteredWorkPackages.forEach(workPackage => {
                let gridElement = document.getElementById('timeline-grid-' + workPackage.id);
                if (gridElement && this.timelinePeriods.length > 0) {
                    // Set the grid container width to match header
                    gridElement.style.width = totalWidth + 'px';
                    gridElement.style.minWidth = totalWidth + 'px';

                    let gridHTML = this.timelinePeriods.map(() =>
                        '<div class="border-r border-border/20 flex-shrink-0" style="width: ' + columnWidth + 'px; min-width: ' + columnWidth + 'px;"></div>'
                    ).join('');
                    safeHTML(gridElement, gridHTML);
                }
            });
        },

        updateWorkPackagePositions() {
            // Force Alpine.js to recalculate work package positions
            this.filteredWorkPackages = [...this.filteredWorkPackages];
        },

        async loadWorkPackages() {
            this.isLoading = true;
            this.loadingMessage = 'Loading work packages...';
            try {
                let data = await Platform.fetch('/api/capability-work-packages');
                // Unchecked, a 500 parsed to `{}` and the timeline rendered empty —
                // indistinguishable from a roadmap with no work packages.
                this.workPackages = data.work_packages || [];
                this.filteredWorkPackages = [...this.workPackages];
            } catch (error) {
                this.showNotification('Error loading work packages', 'error');
                // No fallback data — show empty state on API failure
                this.workPackages = [];
                this.filteredWorkPackages = [...this.workPackages];
            } finally {
                this.isLoading = false;
                this.loadingMessage = '';
            }
        },

        getStrategicImportanceColor(importance) {
            let colors = {
                critical: 'bg-destructive',
                high: 'bg-orange-500',
                medium: 'bg-amber-500',
                low: 'bg-emerald-500'
            };
            return colors[importance] || 'bg-muted/50';
        },

        getStrategicImportanceBadgeColor(importance) {
            let colors = {
                critical: 'bg-destructive/10 text-red-800',
                high: 'bg-orange-100 text-orange-800',
                medium: 'bg-amber-500/10 text-yellow-800',
                low: 'bg-emerald-500/10 text-green-800'
            };
            return colors[importance] || 'bg-muted text-foreground';
        },

        // Export Functions
        exportToCSV() {
            let rows = [
                ['Name', 'Strategic Importance', 'Status', 'Start Date', 'End Date', 'Progress %', 'Assigned To', 'Priority', 'Risk Level', 'Estimated Cost']
            ];

            this.filteredWorkPackages.forEach(wp => {
                rows.push([
                    wp.name || '',
                    wp.strategic_importance || '',
                    wp.status || '',
                    wp.start_date || '',
                    wp.end_date || '',
                    Platform.figure.chartValue(wp.progress_percentage) ?? '',
                    wp.assigned_to || '',
                    wp.priority || '',
                    wp.risk_level || '',
                    Platform.figure.chartValue(wp.estimated_cost) ?? ''
                ]);
            });

            let csvContent = rows.map(row =>
                row.map(cell => {
                    let cellStr = String(cell);
                    // Escape quotes and wrap in quotes if contains comma, quote, or newline
                    if (cellStr.includes(',') || cellStr.includes('"') || cellStr.includes('\n')) {
                        return '"' + cellStr.replace(/"/g, '""') + '"';
                    }
                    return cellStr;
                }).join(',')
            ).join('\n');

            let blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            let link = document.createElement('a');
            let url = URL.createObjectURL(blob);
            link.setAttribute('href', url);
            link.setAttribute('download', 'capability_roadmap_' + new Date().toISOString().split('T')[0] + '.csv');
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        },

        async exportToImage(format, quality) {
            if (quality === undefined) quality = 1.0;
            // Show loading indicator
            let loadingDiv = document.createElement('div');
            safeHTML(loadingDiv, '<div class="modal-root" style="position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:50;"><div class="bg-background p-4 rounded-lg shadow-lg flex items-center gap-3"><svg class="animate-spin h-5 w-5 text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><p class="text-foreground">Generating professional roadmap export...</p></div></div>');
            document.body.appendChild(loadingDiv);

            try {
                if (typeof html2canvas === 'undefined') {
                    await new Promise((resolve, reject) => {
                        let script = document.createElement('script');
                        script.src = '/static/vendor/html2canvas.min.js';
                        script.onload = resolve;
                        script.onerror = reject;
                        document.head.appendChild(script);
                    });
                }

                // For professional export, we capture the ACTUAL GANTT CONTAINER
                // This ensures it matches the high-end UI exactly.
                let originalElement = document.getElementById('gantt-chart-container');
                let mainScroll = document.getElementById('main-scroll-container');

                // Save original styles
                let originalScrollLeft = mainScroll.scrollLeft;
                let originalScrollTop = mainScroll.scrollTop;
                let originalMaxHeight = mainScroll.style.maxHeight;
                let originalOverflow = mainScroll.style.overflow;

                // Prepare for capture: Expand container and remove scrolling
                mainScroll.style.maxHeight = 'none';
                mainScroll.style.overflow = 'visible';
                mainScroll.scrollLeft = 0;
                mainScroll.scrollTop = 0;

                // Small delay for style application
                await new Promise(resolve => setTimeout(resolve, 500));

                let canvas = await html2canvas(originalElement, {
                    backgroundColor: '#ffffff',
                    scale: 3, // Premium high-res
                    useCORS: true,
                    logging: false,
                    allowTaint: true,
                    scrollX: 0,
                    scrollY: 0,
                    width: originalElement.scrollWidth,
                    height: originalElement.scrollHeight,
                    onclone: (clonedDoc) => {
                        // Ensure the cloned scroll container is expanded and not clipping
                        let clonedScroll = clonedDoc.getElementById('main-scroll-container');
                        if (clonedScroll) {
                            clonedScroll.style.maxHeight = 'none';
                            clonedScroll.style.overflow = 'visible';
                            clonedScroll.style.width = 'auto';
                        }
                    }
                });

                // Restore original styles
                mainScroll.style.maxHeight = originalMaxHeight;
                mainScroll.style.overflow = originalOverflow;
                mainScroll.scrollLeft = originalScrollLeft;
                mainScroll.scrollTop = originalScrollTop;

                // Convert and download
                let mimeType = format === 'png' ? 'image/png' : 'image/jpeg';
                let dataUrl = canvas.toDataURL(mimeType, quality);

                let link = document.createElement('a');
                link.download = 'capability_roadmap_export_' + new Date().toISOString().split('T')[0] + '.' + format;
                link.href = dataUrl;
                link.click();

            } catch (error) {
                this.showNotification('Failed to generate professional export', 'error');
            } finally {
                document.body.removeChild(loadingDiv);
            }
        },

        exportToPNG() {
            this.exportToImage('png');
        },

        exportToJPEG() {
            this.exportToImage('jpeg', 0.92);
        },

        exportToJPG() {
            this.exportToImage('jpg', 0.92);
        },

        getBarStyle(workPackage) {
            // Ensure dates are properly parsed
            let startDate = new Date(workPackage.start_date);
            let endDate = new Date(workPackage.end_date);

            // Validate dates
            if (isNaN(startDate.getTime()) || isNaN(endDate.getTime()) || this.timelinePeriods.length === 0) {
                return {
                    left: '0px',
                    width: '0px',
                    top: '50%',
                    transform: 'translateY(-50%)'
                };
            }

            // Get column width in pixels (must match header/grid)
            let columnWidth = this.getColumnWidth();

            // Find which period the work package starts and ends in
            let startPeriodIndex = -1;
            let endPeriodIndex = -1;

            for (let i = 0; i < this.timelinePeriods.length; i++) {
                let period = this.timelinePeriods[i];
                if (startPeriodIndex === -1 && startDate <= period.end) {
                    startPeriodIndex = i;
                }
                if (endDate >= period.start) {
                    endPeriodIndex = i;
                }
            }

            // Handle edge cases
            if (startPeriodIndex === -1) startPeriodIndex = 0;
            if (endPeriodIndex === -1) endPeriodIndex = this.timelinePeriods.length - 1;
            if (startPeriodIndex > endPeriodIndex) endPeriodIndex = startPeriodIndex;

            // Calculate position and width in pixels
            let leftPx = startPeriodIndex * columnWidth;
            let widthPx = (endPeriodIndex - startPeriodIndex + 1) * columnWidth;

            return {
                left: leftPx + 'px',
                width: widthPx + 'px',
                top: '50%',
                transform: 'translateY(-50%)',
                minWidth: '20px' // Ensure minimum visibility
            };
        },

        getProgressStyle(workPackage) {
            return {
                width: Platform.figure.barWidth(workPackage.progress_percentage) + '%'
            };
        },

        getDurationText(workPackage) {
            let startDate = new Date(workPackage.start_date);
            let endDate = new Date(workPackage.end_date);
            let duration = Math.ceil((endDate - startDate) / (1000 * 60 * 60 * 24));

            if (duration <= 0) {
                return 'Invalid';
            } else if (duration === 1) {
                return '1 day';
            } else if (duration < 30) {
                return duration + ' days';
            } else if (duration < 365) {
                let months = Math.floor(duration / 30);
                let days = duration % 30;
                if (days === 0) {
                    return months + ' month' + (months > 1 ? 's' : '');
                } else {
                    return months + 'm ' + days + 'd';
                }
            } else {
                let years = Math.floor(duration / 365);
                let months = Math.floor((duration % 365) / 30);
                if (months === 0) {
                    return years + ' year' + (years > 1 ? 's' : '');
                } else {
                    return years + 'y ' + months + 'm';
                }
            }
        },

        showNotification(message, type) {
            if (!type) type = 'success';
            // Create a unique toast ID
            let id = ++this.toastIdCounter;
            let toast = { id: id, message: message, type: type };

            // Add to toasts array
            this.toasts.push(toast);

            // Auto-remove after 4 seconds
            setTimeout(() => {
                this.removeToast(id);
            }, 4000);
        },

        removeToast(id) {
            this.toasts = this.toasts.filter(t => t.id !== id);
        },

        // Work package CRUD operations
        editWorkPackage(workPackage) {
            this.editingWorkPackage = workPackage;

            // Rebuild selected_capabilities from stored arrays
            let selectedCapabilities = [];
            if (workPackage.capability_ids && workPackage.capability_names) {
                // Use stored arrays
                selectedCapabilities = workPackage.capability_ids.map((id, index) => ({
                    id: id,
                    name: workPackage.capability_names[index] || '',
                    level: this.getCapabilityLevel(id) || 1
                }));
            } else if (workPackage.capability_id && workPackage.business_capability) {
                // Fallback to single capability for backward compatibility
                selectedCapabilities = [{
                    id: workPackage.capability_id,
                    name: workPackage.business_capability,
                    level: this.getCapabilityLevel(workPackage.capability_id) || 1
                }];
            }

            this.formData = {
                name: workPackage.wp_name || workPackage.name,
                description: workPackage.description || '',
                business_capability: workPackage.business_capability || '',
                assigned_to: workPackage.assigned_to || '',
                custom_assigned_to: '',
                capability_levels: [],
                selected_capabilities: selectedCapabilities,
                status: workPackage.status,
                start_date: workPackage.start_date ? new Date(workPackage.start_date).toISOString().split('T')[0] : '',
                end_date: workPackage.end_date ? new Date(workPackage.end_date).toISOString().split('T')[0] : '',
                progress_percentage: workPackage.progress_percentage ?? null,
                estimated_cost: workPackage.estimated_cost ?? null,
                priority: workPackage.priority || 'medium',
                risk_level: workPackage.risk_level || 'medium'
            };
            this.showAddModal = true;
        },

        // Helper to get capability level from grouped data
        getCapabilityLevel(capId) {
            for (let level = 1; level <= 3; level++) {
                let key = 'L' + level;
                if (this.groupedCapabilities[key]) {
                    let found = this.groupedCapabilities[key].find(c => c.id === capId);
                    if (found) return level;
                }
            }
            return null;
        },

        async saveWorkPackage() {
            this.isSaving = true;
            this.loadingMessage = 'Saving work package...';
            try {
                // Handle assigned_to - use custom name if "Other" was selected
                let assignedTo = 'Unassigned';
                if (this.formData.assigned_to === '__other__') {
                    assignedTo = this.formData.custom_assigned_to || 'Unassigned';
                } else if (this.formData.assigned_to) {
                    assignedTo = this.formData.assigned_to;
                }

                // Prepare data for submission
                let capabilityIds = this.formData.selected_capabilities.map(c => c.id);
                let capabilityNames = this.formData.selected_capabilities.map(c => c.name);

                let submissionData = {
                    ...this.formData,
                    assigned_to: assignedTo,
                    // Multi-capability support
                    capability_ids: capabilityIds,
                    capability_names: capabilityNames,
                    // Use first selected as primary (backward compatibility)
                    business_capability: this.formData.selected_capabilities.length > 0
                        ? this.formData.selected_capabilities[0].name
                        : '',
                    // Remove fields not needed in API
                    custom_assigned_to: undefined,
                    selected_capabilities: undefined
                };

                let url = this.editingWorkPackage ?
                    '/api/capability-work-packages/' + this.editingWorkPackage.id :
                    '/api/capability-work-packages';

                let result;
                if (this.editingWorkPackage) {
                    result = await Platform.fetch.put(url, submissionData);
                } else {
                    result = await Platform.fetch.post(url, submissionData);
                }

                this.showNotification('Work package saved successfully!', 'success');
                this.showAddModal = false;
                this.resetForm();
                await this.loadWorkPackages();
            } catch (error) {
                this.showNotification('Error saving work package', 'error');
            } finally {
                this.isSaving = false;
                this.loadingMessage = '';
            }
        },

        async deleteWorkPackage(workPackage) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Delete Work Package',
                content: '<p class="text-sm text-muted-foreground">Are you sure you want to delete &ldquo;' + workPackage.name + '&rdquo;?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: async function() {
                        self.isSaving = true;
                        self.loadingMessage = 'Deleting work package...';
                        try {
                            await Platform.fetch.delete('/api/capability-work-packages/' + workPackage.id);
                            self.showNotification('Work package deleted successfully', 'success');
                            await self.loadWorkPackages();
                        } catch (error) {
                            self.showNotification('Error deleting work package', 'error');
                        } finally {
                            self.isSaving = false;
                            self.loadingMessage = '';
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        resetForm() {
            this.editingWorkPackage = null;
            this.formData = {
                name: '',
                description: '',
                business_capability: '',
                assigned_to: '',
                custom_assigned_to: '',
                capability_levels: [],
                selected_capabilities: [],
                status: 'planned',
                start_date: '',
                end_date: '',
                progress_percentage: 0,
                estimated_cost: 0,
                priority: 'medium',
                risk_level: 'medium'
            };
        },

        // ================================================================
        // GROUPED CAPABILITY SELECTOR - Selection and User-Driven Addition
        // ================================================================

        showCapabilityDropdown: false,
        groupedCapabilities: { L1: [], L2: [], L3: [] },
        capabilitySearchTerm: '',
        capabilityGroupExpanded: { L1: true, L2: true, L3: true },
        selectedCapabilityLevel: '',
        showAddCapabilityModal: false,
        capabilityDuplicateError: null,
        newCapabilityForm: {
            name: '',
            description: '',
            level: 3,
            strategic_importance: 'medium',
            parent_capability_id: ''
        },

        async loadGroupedCapabilities() {
            try {
                let data = await Platform.fetch('/api/capabilities/grouped');
                if (data.success) {
                    this.groupedCapabilities = data.grouped_capabilities;
                }
            } catch (error) {
                if (window.Platform && Platform.toast) Platform.toast.error('Could not load capabilities.');
            }
        },

        toggleCapabilityDropdown() {
            this.showCapabilityDropdown = !this.showCapabilityDropdown;
            if (this.showCapabilityDropdown && this.groupedCapabilities.L1.length === 0) {
                this.loadGroupedCapabilities();
            }
        },

        getFilteredCapabilitiesByLevel(level) {
            let levelKey = 'L' + level;
            let caps = this.groupedCapabilities[levelKey] || [];
            if (!this.capabilitySearchTerm) return caps;

            let search = this.capabilitySearchTerm.toLowerCase();
            return caps.filter(cap =>
                cap.name.toLowerCase().includes(search) ||
                (cap.description && cap.description.toLowerCase().includes(search))
            );
        },

        selectCapability(cap) {
            // Multi-select: toggle capability in selection
            let capObj = { id: cap.id, name: cap.name, level: cap.level };
            let existingIndex = this.formData.selected_capabilities.findIndex(c => c.id === cap.id);

            if (existingIndex >= 0) {
                // Remove if already selected
                this.formData.selected_capabilities.splice(existingIndex, 1);
            } else {
                // Add to selection
                this.formData.selected_capabilities.push(capObj);
            }

            // Update legacy field with first selected capability (for backward compatibility)
            if (this.formData.selected_capabilities.length > 0) {
                let primary = this.formData.selected_capabilities[0];
                this.formData.business_capability = primary.name;
                this.selectedCapabilityLevel = this.formData.selected_capabilities.map(c => 'L' + c.level).join(', ');
            } else {
                this.formData.business_capability = '';
                this.selectedCapabilityLevel = '';
            }
        },

        isCapabilitySelected(cap) {
            return this.formData.selected_capabilities.some(c => c.id === cap.id);
        },

        removeSelectedCapability(capId) {
            let index = this.formData.selected_capabilities.findIndex(c => c.id === capId);
            if (index >= 0) {
                this.formData.selected_capabilities.splice(index, 1);
                // Update legacy field
                if (this.formData.selected_capabilities.length > 0) {
                    this.formData.business_capability = this.formData.selected_capabilities[0].name;
                } else {
                    this.formData.business_capability = '';
                    this.selectedCapabilityLevel = '';
                }
            }
        },

        // Get available parent capabilities based on selected level
        // L1 has no parents, L2 can have L1 parents, L3 can have L1 or L2 parents
        getAvailableParentCapabilities() {
            let level = this.newCapabilityForm.level;
            if (level === 1) return []; // L1 has no parents

            let parentCaps = [];
            if (level === 2) {
                // L2 can only have L1 as parent
                parentCaps = this.groupedCapabilities['L1'] || [];
            } else if (level === 3) {
                // L3 can have L1 or L2 as parent
                let l1Caps = (this.groupedCapabilities['L1'] || []).map(c => ({ ...c, displayLevel: 'L1' }));
                let l2Caps = (this.groupedCapabilities['L2'] || []).map(c => ({ ...c, displayLevel: 'L2' }));
                parentCaps = [...l1Caps, ...l2Caps];
            }
            return parentCaps;
        },

        // Get parent capability name by ID
        getParentCapabilityName(parentId) {
            if (!parentId) return null;
            for (let levelKey of ['L1', 'L2', 'L3']) {
                let caps = this.groupedCapabilities[levelKey] || [];
                let found = caps.find(c => c.id === parentId);
                if (found) return 'L' + found.level + ' - ' + found.name;
            }
            return null;
        },

        // Handle level change - reset parent if invalid
        onLevelChange() {
            // Reset parent if current parent is not valid for new level
            if (this.newCapabilityForm.level === 1) {
                this.newCapabilityForm.parent_capability_id = '';
            } else if (this.newCapabilityForm.level === 2) {
                // L2 can only have L1 parent - check if current parent is L1
                let parent = this.getParentCapabilityById(this.newCapabilityForm.parent_capability_id);
                if (parent && parent.level !== 1) {
                    this.newCapabilityForm.parent_capability_id = '';
                }
            }
            this.checkCapabilityDuplicate();
        },

        getParentCapabilityById(parentId) {
            if (!parentId) return null;
            for (let levelKey of ['L1', 'L2', 'L3']) {
                let caps = this.groupedCapabilities[levelKey] || [];
                let found = caps.find(c => c.id === parentId);
                if (found) return found;
            }
            return null;
        },

        async openAddCapabilityModal() {
            this.showCapabilityDropdown = false;
            this.showAddCapabilityModal = true;
            this.capabilityDuplicateError = null;
            this.newCapabilityForm = {
                name: '',
                description: '',
                level: 3,
                strategic_importance: 'medium',
                parent_capability_id: ''
            };
            // Load grouped capabilities if not already loaded (needed for parent selector)
            if (!this.groupedCapabilities.L1 || this.groupedCapabilities.L1.length === 0) {
                await this.loadGroupedCapabilities();
            }
        },

        closeAddCapabilityModal() {
            this.showAddCapabilityModal = false;
            this.capabilityDuplicateError = null;
        },

        async checkCapabilityDuplicate() {
            let name = this.newCapabilityForm.name.trim();
            let level = this.newCapabilityForm.level;

            if (!name || !level) {
                this.capabilityDuplicateError = null;
                return;
            }

            try {
                let data = await Platform.fetch.get('/api/capabilities/check-duplicate', {
                    name: name,
                    level: level
                });
                if (data.success && data.is_duplicate) {
                    this.capabilityDuplicateError = data.message;
                } else {
                    this.capabilityDuplicateError = null;
                }
            } catch (error) {
                // Platform.fetch surfaced this failure before it threw; the
                // catch only stops it becoming an unhandled rejection.
            }
        },

        async createNewCapability() {
            if (this.capabilityDuplicateError) return;

            try {
                let result = await Platform.fetch.post('/api/capabilities', this.newCapabilityForm);
                // Add to grouped capabilities
                let levelKey = 'L' + result.capability.level;
                if (!this.groupedCapabilities[levelKey]) {
                    this.groupedCapabilities[levelKey] = [];
                }
                this.groupedCapabilities[levelKey].push(result.capability);

                // Auto-select the new capability
                this.formData.business_capability = result.capability.name;
                this.selectedCapabilityLevel = 'L' + result.capability.level + ' - ' + result.capability.name;

                // Close modal and show notification
                this.closeAddCapabilityModal();
                this.showNotification(result.message, 'success');
            } catch (error) {
                // Platform.fetch throws on non-ok responses, including 409
                // The error data is available in error.data
                if (error.status === 409) {
                    this.capabilityDuplicateError = error.data?.error || 'Duplicate capability';
                } else {
                    this.capabilityDuplicateError = error.data?.error || 'Error creating capability';
                }
            }
        },

        // ================================================================
        // ENHANCED DETAILS MODAL - Tasks and Deliverables Management
        // ================================================================

        showDetailsModal: false,
        detailsData: null,
        activeTab: 'details',
        showAddTaskForm: false,
        showAddDeliverableForm: false,
        taskForm: {
            title: '',
            description: '',
            capability_level: 'L3',
            start_date: '',
            end_date: '',
            status: 'planned',
            estimated_hours: 0
        },
        deliverableForm: {
            name: '',
            description: '',
            deliverable_type: '',
            due_date: '',
            status: 'planned',
            approval_criteria: ''
        },

        async openDetailsModal(workPackage) {
            this.isLoading = true;
            this.loadingMessage = 'Loading work package details...';
            try {
                let data = await Platform.fetch('/api/capability-work-packages/' + workPackage.id + '/details');
                if (data.success) {
                    this.detailsData = data;
                    this.showDetailsModal = true;
                    this.activeTab = 'details';
                    this.showAddTaskForm = false;
                    this.showAddDeliverableForm = false;
                } else {
                    this.showNotification('Error loading work package details', 'error');
                }
            } catch (error) {
                this.showNotification('Error loading work package details', 'error');
            } finally {
                this.isLoading = false;
                this.loadingMessage = '';
            }
        },

        closeDetailsModal() {
            this.showDetailsModal = false;
            this.detailsData = null;
            this.activeTab = 'details';
            this.showAddTaskForm = false;
            this.showAddDeliverableForm = false;
        },

        openEditMode() {
            if (this.detailsData?.work_package) {
                this.editWorkPackage(this.detailsData.work_package);
                this.showDetailsModal = false;
            }
        },

        // Task Management
        resetTaskForm() {
            this.taskForm = {
                title: '',
                description: '',
                capability_level: 'L3',
                start_date: '',
                end_date: '',
                status: 'planned',
                estimated_hours: 0
            };
        },

        async saveTask() {
            if (!this.taskForm.title) {
                this.showNotification('Task title is required', 'warning');
                return;
            }

            this.isSaving = true;
            this.loadingMessage = 'Creating task...';
            try {
                let wpId = this.detailsData.work_package.id;
                let result = await Platform.fetch.post('/api/capability-work-packages/' + wpId + '/tasks', this.taskForm);
                this.detailsData.tasks.push(result.task);
                this.detailsData.statistics.total_tasks++;
                this.showAddTaskForm = false;
                this.resetTaskForm();
                this.showNotification('Task created successfully', 'success');
            } catch (error) {
                this.showNotification('Error creating task', 'error');
            } finally {
                this.isSaving = false;
                this.loadingMessage = '';
            }
        },

        async deleteTask(taskId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Delete Task',
                content: '<p class="text-sm text-muted-foreground">Are you sure you want to delete this task?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: async function() {
                        self.isSaving = true;
                        self.loadingMessage = 'Deleting task...';
                        try {
                            let wpId = self.detailsData.work_package.id;
                            let result = await Platform.fetch.delete('/api/capability-work-packages/' + wpId + '/tasks/' + taskId);
                            self.detailsData.tasks = self.detailsData.tasks.filter(t => t.id !== taskId);
                            self.detailsData.statistics.total_tasks--;
                            self.showNotification('Task deleted', 'success');
                        } catch (error) {
                            self.showNotification('Error deleting task', 'error');
                        } finally {
                            self.isSaving = false;
                            self.loadingMessage = '';
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        // Deliverable Management
        resetDeliverableForm() {
            this.deliverableForm = {
                name: '',
                description: '',
                deliverable_type: '',
                due_date: '',
                status: 'planned',
                approval_criteria: ''
            };
        },

        async saveDeliverable() {
            if (!this.deliverableForm.name) {
                this.showNotification('Deliverable name is required', 'warning');
                return;
            }

            this.isSaving = true;
            this.loadingMessage = 'Creating deliverable...';
            try {
                let wpId = this.detailsData.work_package.id;
                let result = await Platform.fetch.post('/api/capability-work-packages/' + wpId + '/deliverables', this.deliverableForm);
                this.detailsData.deliverables.push(result.deliverable);
                this.detailsData.statistics.total_deliverables++;
                this.showAddDeliverableForm = false;
                this.resetDeliverableForm();
                this.showNotification('Deliverable created successfully', 'success');
            } catch (error) {
                this.showNotification('Error creating deliverable', 'error');
            } finally {
                this.isSaving = false;
                this.loadingMessage = '';
            }
        },

        async deleteDeliverable(deliverableId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Delete Deliverable',
                content: '<p class="text-sm text-muted-foreground">Are you sure you want to delete this deliverable?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: async function() {
                        self.isSaving = true;
                        self.loadingMessage = 'Deleting deliverable...';
                        try {
                            let wpId = self.detailsData.work_package.id;
                            let result = await Platform.fetch.delete('/api/capability-work-packages/' + wpId + '/deliverables/' + deliverableId);
                            self.detailsData.deliverables = self.detailsData.deliverables.filter(d => d.id !== deliverableId);
                            self.detailsData.statistics.total_deliverables--;
                            self.showNotification('Deliverable deleted', 'success');
                        } catch (error) {
                            self.showNotification('Error deleting deliverable', 'error');
                        } finally {
                            self.isSaving = false;
                            self.loadingMessage = '';
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        // UI Helper Functions for Details Modal
        formatDisplayDate(dateStr) {
            if (!dateStr) return 'Not set';
            let date = new Date(dateStr);
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        },

        getStatusBadgeColor(status) {
            let colors = {
                'planned': 'bg-primary/10 text-primary/90',
                'in_progress': 'bg-amber-500/10 text-yellow-800',
                'completed': 'bg-emerald-500/10 text-green-800',
                'on_hold': 'bg-muted text-foreground',
                'cancelled': 'bg-destructive/10 text-red-800'
            };
            return colors[status] || 'bg-muted text-foreground';
        },

        getPriorityBadgeColor(priority) {
            let colors = {
                'critical': 'bg-destructive/10 text-red-800',
                'high': 'bg-orange-100 text-orange-800',
                'medium': 'bg-amber-500/10 text-yellow-800',
                'low': 'bg-emerald-500/10 text-green-800'
            };
            return colors[priority] || 'bg-muted text-foreground';
        },

        getRiskBadgeColor(risk) {
            let colors = {
                'critical': 'bg-destructive/10 text-red-800',
                'high': 'bg-orange-100 text-orange-800',
                'medium': 'bg-amber-500/10 text-yellow-800',
                'low': 'bg-emerald-500/10 text-green-800'
            };
            return colors[risk] || 'bg-muted text-foreground';
        },

        getDeliverableStatusColor(status) {
            let colors = {
                'planned': 'bg-primary/10 text-primary/90',
                'in_progress': 'bg-amber-500/10 text-yellow-800',
                'delivered': 'bg-emerald-500/10 text-green-800',
                'approved': 'bg-emerald-100 text-emerald-800'
            };
            return colors[status] || 'bg-muted text-foreground';
        },

        getApprovalStatusColor(status) {
            let colors = {
                'pending': 'bg-amber-500/10 text-amber-700',
                'approved': 'bg-emerald-500/10 text-emerald-700',
                'rejected': 'bg-destructive/10 text-destructive'
            };
            return colors[status] || 'bg-muted text-foreground';
        }
    };
}
