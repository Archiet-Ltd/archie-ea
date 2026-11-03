/**
 * Batch Import Dashboard - External JavaScript
 * Extracted from app/templates/batch_import/dashboard.html
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

function batchImportDashboard() {
    return {
        loading: true,
        jobs: [],
        stats: {
            totalJobs: 0,
            processing: 0,
            elementsGenerated: 0,
            totalCost: null
        },
        filterStatus: 'all',
        searchQuery: '',
        sortKey: 'created_at',
        sortOrder: 'desc',
        currentPage: 1,
        perPage: 10,
        showCancelModal: false,
        cancelJobData: null,

        statusOptions: [
            { value: 'Pending', label: 'Pending', activeClass: 'bg-slate-600 text-primary-foreground', inactiveClass: 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-400' },
            { value: 'Processing', label: 'Processing', activeClass: 'bg-primary text-primary-foreground', inactiveClass: 'bg-primary/10 text-primary hover:bg-primary/20 dark:bg-blue-900/30 dark:text-blue-400' },
            { value: 'Paused', label: 'Paused', activeClass: 'bg-amber-600 text-primary-foreground', inactiveClass: 'bg-amber-100 text-amber-600 hover:bg-amber-200 dark:bg-amber-900/30 dark:text-amber-400' },
            { value: 'Completed', label: 'Completed', activeClass: 'bg-emerald-600 text-primary-foreground', inactiveClass: 'bg-emerald-500/10 text-emerald-600 hover:bg-green-200 dark:bg-green-900/30 dark:text-green-400' },
            { value: 'Failed', label: 'Failed', activeClass: 'bg-destructive text-primary-foreground', inactiveClass: 'bg-destructive/10 text-destructive hover:bg-red-200 dark:bg-red-900/30 dark:text-red-400' },
            { value: 'Cancelled', label: 'Cancelled', activeClass: 'bg-muted-foreground text-primary-foreground', inactiveClass: 'bg-muted text-muted-foreground hover:bg-accent dark:bg-muted dark:text-muted-foreground' }
        ],

        init() {
            this.loadJobs();
            // Poll for updates every 30 seconds
            setInterval(() => {
                if (!this.showCancelModal) {
                    this.loadJobs(true);
                }
            }, 30000);
        },

        get filteredJobs() {
            let filtered = this.jobs;

            if (this.filterStatus !== 'all') {
                filtered = filtered.filter(j => j.status === this.filterStatus);
            }

            if (this.searchQuery) {
                let query = this.searchQuery.toLowerCase();
                filtered = filtered.filter(j =>
                    j.name.toLowerCase().includes(query) ||
                    j.mode.toLowerCase().includes(query)
                );
            }

            // Sort
            filtered = [...filtered].sort((a, b) => {
                let aVal = a[this.sortKey];
                let bVal = b[this.sortKey];

                if (this.sortKey === 'created_at') {
                    aVal = new Date(aVal);
                    bVal = new Date(bVal);
                }

                if (this.sortOrder === 'asc') {
                    return aVal > bVal ? 1 : -1;
                }
                return aVal < bVal ? 1 : -1;
            });

            return filtered;
        },

        get totalPages() {
            return Math.ceil(this.filteredJobs.length / this.perPage);
        },

        get startIndex() {
            return (this.currentPage - 1) * this.perPage;
        },

        get endIndex() {
            return this.startIndex + this.perPage;
        },

        get paginatedJobs() {
            return this.filteredJobs.slice(this.startIndex, this.endIndex);
        },

        sortBy(key) {
            if (this.sortKey === key) {
                this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortKey = key;
                this.sortOrder = 'asc';
            }
        },

        getStatusBadgeClass(status) {
            let classes = {
                'Pending': 'border-transparent bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
                'Processing': 'border-transparent bg-primary/10 text-primary dark:bg-blue-900/30 dark:text-blue-400',
                'Paused': 'border-transparent bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400',
                'Completed': 'border-transparent bg-emerald-500/10 text-emerald-600 dark:bg-green-900/30 dark:text-green-400',
                'Failed': 'border-transparent bg-destructive/10 text-destructive dark:bg-red-900/30 dark:text-red-400',
                'Cancelled': 'border-transparent bg-muted text-muted-foreground dark:bg-muted dark:text-muted-foreground'
            };
            return classes[status] || classes['Pending'];
        },

        formatDate(dateStr) {
            let date = new Date(dateStr);
            return date.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        },

        async loadJobs(silent) {
            if (!silent) {
                this.loading = true;
            }

            try {
                // Platform.fetch throws on non-ok responses, returns parsed data directly
                const data = await Platform.fetch('/api/batch-import/jobs', { silent: true });
                // If we reach here, the request succeeded (status 2xx)
                if (data.success) {
                    this.jobs = data.jobs || [];
                    this.stats = data.stats || {
                        totalJobs: 0,
                        processing: 0,
                        elementsGenerated: 0,
                        totalCost: null
                    };
                } else {
                    // The server returned a 200 with { success: false, error: ... }
                    // This is not an HTTP error, so Platform.fetch didn't throw.
                    // We must treat it as a failure and show toast if not silent.
                    if (!silent) {
                        this.showToast(data.error || 'Failed to load import jobs', 'error');
                    }
                }
            } catch (error) {
                // Platform.fetch throws on network errors or HTTP errors (non-2xx)
                // We already passed silent:true, so no global toast was shown.
                if (!silent) {
                    this.showToast(error.message || 'Failed to load import jobs', 'error');
                }
            } finally {
                this.loading = false;
            }
        },

        refreshJobs() {
            this.loadJobs();
        },

        async startJob(jobId) {
            try {
                // Platform.fetch automatically injects CSRF token and serializes body
                const data = await Platform.fetch.post(`/api/batch-import/jobs/${jobId}/start`, undefined, { silent: true });
                // If we reach here, HTTP status is 2xx
                if (data.success) {
                    this.showToast('Job started successfully', 'success');
                    this.loadJobs(true);
                } else {
                    this.showToast(data.message || data.error || 'Failed to start job', 'error');
                }
            } catch (error) {
                // Platform.fetch throws on network errors or HTTP errors (non-2xx)
                // We already passed silent:true, so no global toast was shown.
                this.showToast(error.message || 'Failed to start job', 'error');
            }
        },
        async pauseJob(jobId) {
            try {
                // Platform.fetch automatically injects CSRF token and serializes body
                const data = await Platform.fetch.post(`/api/batch-import/jobs/${jobId}/pause`, undefined, { silent: true });
                // If we reach here, HTTP status is 2xx
                if (data.success) {
                    this.showToast('Job paused', 'success');
                    this.loadJobs(true);
                } else {
                    this.showToast(data.message || data.error || 'Failed to pause job', 'error');
                }
            } catch (error) {
                // Platform.fetch throws on network errors or HTTP errors (non-2xx)
                // We already passed silent:true, so no global toast was shown.
                this.showToast(error.message || 'Failed to pause job', 'error');
            }
        },
        async resumeJob(jobId) {
            try {
                // Platform.fetch automatically injects CSRF token and serializes body
                const data = await Platform.fetch.post(`/api/batch-import/jobs/${jobId}/resume`, undefined, { silent: true });
                // If we reach here, HTTP status is 2xx
                if (data.success) {
                    this.showToast('Job resumed', 'success');
                    this.loadJobs(true);
                } else {
                    this.showToast(data.message || data.error || 'Failed to resume job', 'error');
                }
            } catch (error) {
                // Platform.fetch throws on network errors or HTTP errors (non-2xx)
                // We already passed silent:true, so no global toast was shown.
                this.showToast(error.message || 'Failed to resume job', 'error');
            }
        },
        confirmCancel(job) { this.cancelJobData = job; this.showCancelModal = true; },
        async cancelJob(jobId) {
            try {
                // Platform.fetch automatically injects CSRF token and serializes body
                const data = await Platform.fetch.post(`/api/batch-import/jobs/${jobId}/cancel`, undefined, { silent: true });
                // If we reach here, HTTP status is 2xx
                if (data.success) {
                    this.showToast('Job cancelled', 'success');
                    this.loadJobs(true);
                } else {
                    this.showToast(data.message || data.error || 'Failed to cancel job', 'error');
                }
            } catch (error) {
                // Platform.fetch throws on network errors or HTTP errors (non-2xx)
                // We already passed silent:true, so no global toast was shown.
                this.showToast(error.message || 'Failed to cancel job', 'error');
            } finally {
                this.showCancelModal = false;
                this.cancelJobData = null;
            }
        },

        showToast(message, type) {
            if (type === undefined) type = 'info';
            if (window.showToast) {
                window.showToast(message, type);
            } else if (window.Platform && Platform.toast) {
                // Without this the notification reached nobody when the page-local
                // showToast helper was absent.
                (Platform.toast[type] || Platform.toast.info)(message);
            }
        }
    };
}

// Initialize Lucide icons
if (typeof lucide !== 'undefined') {
    lucide.createIcons();
}
