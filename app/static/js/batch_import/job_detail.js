/**
 * Batch Import Job Detail - Alpine.js Component
 * Extracted from app/templates/batch_import/job_detail.html
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

function jobDetail(jobId) {
    return {
        jobId: jobId,
        job: null,
        batches: [],
        loading: true,
        loadingBatches: true,
        refreshing: false,
        showCancelModal: false,
        // Import preview state
        previewData: null,
        previewLoading: false,
        conflicts: [],
        resolutions: {},
        // SSE streaming state
        streaming: false,
        eventSource: null,
        streamMessages: [],
        currentBatch: null,
        currentApp: null,
        batchProgress: 0,

        init() {
            this.loadJob();
            this.loadBatches();

            // Cleanup on page unload
            window.addEventListener('beforeunload', () => {
                this.stopStream();
            });
        },

        get estimatedTimeRemaining() {
            if (!this.job || this.job.processed_batches === 0) return 'Calculating...';

            let avgTimePerBatch = this.job.processing_time_ms / this.job.processed_batches;
            let remainingBatches = this.job.total_batches - this.job.processed_batches;
            let remainingMs = avgTimePerBatch * remainingBatches;

            if (remainingMs < 60000) return 'Less than a minute';
            if (remainingMs < 3600000) return Math.ceil(remainingMs / 60000) + ' minutes';
            return Math.ceil(remainingMs / 3600000) + ' hours';
        },

        get costVariance() {
            if (!this.job) return 0;
            return this.job.actual_cost - this.job.estimated_cost;
        },

        get costVarianceText() {
            let variance = this.costVariance;
            if (Math.abs(variance) < 0.01) return window.BatchMoney.format(0);
            return window.BatchMoney.format(variance, { signed: true });
        },

        get costVarianceClass() {
            if (this.costVariance > 0) return 'text-destructive';
            if (this.costVariance < 0) return 'text-emerald-600';
            return '';
        },

        get costVarianceIconClass() {
            if (this.costVariance > 0) return 'text-destructive';
            if (this.costVariance < 0) return 'text-emerald-600';
            return 'text-muted-foreground';
        },

        get costVarianceBackgroundClass() {
            if (this.costVariance > 0) return 'bg-destructive/10';
            if (this.costVariance < 0) return 'bg-emerald-500/10 dark:bg-green-900/30';
            return 'bg-muted/50';
        },

        getStatusIcon(status) {
            let icons = {
                'Pending': 'clock',
                'Processing': 'loader-2',
                'Paused': 'pause',
                'Completed': 'check-circle',
                'Failed': 'x-circle',
                'Cancelled': 'x-circle'
            };
            return icons[status] || 'help-circle';
        },

        getStatusIconBackground(status) {
            let backgrounds = {
                'Pending': 'bg-slate-500',
                'Processing': 'bg-primary',
                'Paused': 'bg-amber-500',
                'Completed': 'bg-emerald-500',
                'Failed': 'bg-destructive',
                'Cancelled': 'bg-muted-foreground'
            };
            return backgrounds[status] || 'bg-slate-500';
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

        getBatchStatusBadgeClass(status) {
            let classes = {
                'Pending': 'border-transparent bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
                'Processing': 'border-transparent bg-primary/10 text-primary dark:bg-blue-900/30 dark:text-blue-400',
                'Processed': 'border-transparent bg-purple-100 text-primary dark:bg-purple-900/30 dark:text-purple-400',
                'Reviewing': 'border-transparent bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400',
                'Committed': 'border-transparent bg-emerald-500/10 text-emerald-600 dark:bg-green-900/30 dark:text-green-400',
                'Failed': 'border-transparent bg-destructive/10 text-destructive dark:bg-red-900/30 dark:text-red-400'
            };
            return classes[status] || classes['Pending'];
        },

        formatDate(dateStr) {
            if (!dateStr) return '';
            let date = new Date(dateStr);
            return date.toLocaleDateString('en-US', {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        },

        async loadJob() {
            // API /api/batch-import/jobs/{id} not yet implemented
            this.job = null;
            this.loading = false;
            this.refreshing = false;
        },

        async loadBatches() {
            // API /api/batch-import/jobs/{id}/batches not yet implemented
            this.batches = [];
            this.loadingBatches = false;
        },

        refreshJob() {
            this.loadJob();
            this.loadBatches();
        },

        async startJob() {
            // API not yet implemented
            this.showToast('Batch import API is not yet available', 'info');
        },

        connectProgressStream() {
            // Close existing connection if any
            if (this.eventSource) {
                this.eventSource.close();
            }

            this.streaming = true;
            this.streamMessages = [];
            this.eventSource = new EventSource('/api/batch-import/jobs/' + this.jobId + '/stream');

            let self = this;

            this.eventSource.addEventListener('job_started', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('info', 'Job started: ' + data.total_batches + ' batches, ' + data.total_applications + ' applications');
                self.loadJob(true);
            });

            this.eventSource.addEventListener('batch_started', function(e) {
                let data = JSON.parse(e.data);
                self.currentBatch = data.batch_number;
                self.addStreamMessage('info', 'Processing batch ' + data.batch_number + '...');
                self.loadBatches(true);
            });

            this.eventSource.addEventListener('app_processing', function(e) {
                let data = JSON.parse(e.data);
                self.currentApp = data.app_name;
                self.batchProgress = Math.round((data.progress / data.total) * 100);
            });

            this.eventSource.addEventListener('app_completed', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('success', '\u2713 ' + data.app_name + ': ' + data.elements_generated + ' elements (' + data.processing_time + 's)');
                self.batchProgress = Math.round((data.progress / data.total) * 100);
            });

            this.eventSource.addEventListener('app_failed', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('error', '\u2717 ' + data.app_name + ': ' + data.error);
            });

            this.eventSource.addEventListener('batch_completed', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('success', 'Batch ' + data.batch_number + ' completed: ' + data.elements_generated + ' elements generated');
                self.loadJob(true);
                self.loadBatches(true);
            });

            this.eventSource.addEventListener('batch_failed', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('error', 'Batch ' + data.batch_number + ' failed: ' + data.error);
                self.loadBatches(true);
            });

            this.eventSource.addEventListener('job_paused', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('warning', 'Job paused: ' + data.message);
                self.stopStream();
                self.loadJob(true);
            });

            this.eventSource.addEventListener('job_ready_for_review', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('success', 'All batches processed! ' + data.batches_ready + ' batches ready for review.');
                self.showToast('Processing complete! Review batches to commit.', 'success');
                self.stopStream();
                self.loadJob(true);
                self.loadBatches(true);
            });

            this.eventSource.addEventListener('job_completed', function(e) {
                let data = JSON.parse(e.data);
                self.addStreamMessage('success', 'Job completed! ' + data.total_elements + ' elements, $' + data.total_cost.toFixed(4) + ' total cost');
                self.showToast('Job completed successfully!', 'success');
                self.stopStream();
                self.loadJob(true);
            });

            this.eventSource.addEventListener('error', function(e) {
                if (e.data) {
                    let data = JSON.parse(e.data);
                    self.addStreamMessage('error', 'Error: ' + data.message);
                }
                self.stopStream();
                self.loadJob(true);
            });

            this.eventSource.onerror = function(error) {
                self.addStreamMessage('error', 'Live progress updates stopped (connection lost). Refreshing job status.');
                self.stopStream();
                self.loadJob(true);
            };
        },

        stopStream() {
            if (this.eventSource) {
                this.eventSource.close();
                this.eventSource = null;
            }
            this.streaming = false;
            this.currentBatch = null;
            this.currentApp = null;
            this.batchProgress = 0;
        },

        addStreamMessage(type, message) {
            let timestamp = new Date().toLocaleTimeString();
            this.streamMessages.unshift({ type: type, message: message, timestamp: timestamp });
            // Keep only last 50 messages
            if (this.streamMessages.length > 50) {
                this.streamMessages.pop();
            }
        },

        async pauseJob() { this.showToast('Batch import API is not yet available', 'info'); },
        async resumeJob() { this.showToast('Batch import API is not yet available', 'info'); },
        async cancelJob() {
            this.showToast('Batch import API is not yet available', 'info');
            this.showCancelModal = false;
        },

        // === Import Preview Methods ===

        async loadPreview() {
            // API /api/batch-import/jobs/{id}/preview not yet implemented
            this.previewData = null;
            this.previewLoading = false;
            this.showToast('Batch import API is not yet available', 'info');
        },

        resolveAllAsNew() {
            if (!this.conflicts) return;
            let self = this;
            this.conflicts.forEach(function(c) {
                self.resolutions[c.import_row] = {
                    import_row: c.import_row,
                    action: 'create_new',
                    target_app_id: null
                };
            });
            this.showToast('Set all ' + this.conflicts.length + ' conflicts to "Create New"', 'info');
        },

        async saveResolutions() {
            // API /api/batch-import/jobs/{id}/resolve-conflicts not yet implemented
            this.showToast('Batch import API is not yet available', 'info');
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
