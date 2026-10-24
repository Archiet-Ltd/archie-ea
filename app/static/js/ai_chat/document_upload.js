/** ai_chat/document_upload - External JavaScript
 *  Extracted from app/templates/ai_chat/document_upload.html
 */

function aiChatDocumentUploader() {
    return {
        showUpload: true, // Show upload section by default when panel opens
        showHistory: false, // Document history panel
        selectedFile: null,
        dragover: false,
        uploading: false,
        uploadStatus: 'Uploading...',
        uploadProgress: 0,
        analysisResults: null,
        showEntityDetails: false, // Toggle for entity details section
        selectedProvider: 'huggingface',
        useSimpleParsing: false,  // Toggle for simple parsing (no LLM)
        currentDomain: null,
        currentPersona: null,
        documentHistory: [],
        loadingHistory: false,

        // Context-aware analysis state
        analysisContext: 'general',  // 'general', 'application', or 'vendor'
        targetApplicationId: '',      // Selected application ID
        targetVendorId: '',           // Selected vendor ID
        applicationsList: [],         // List of available applications
        vendorsList: [],              // List of available vendors
        loadingApplications: false,
        loadingVendors: false,

        // Preview mode state - allows user review before creation
        previewMode: true,            // If true, show preview before creating
        previewElements: [],          // Elements extracted but not yet created
        selectedElements: {},         // Map of element index -> boolean for selection
        editingElement: null,         // Index of element being edited
        originalElements: {},          // Store original element data for feedback
        creatingElements: false,      // Loading state for element creation
        currentUploadId: null,        // Store upload ID for feedback
        creationDetails: null,        // Store detailed creation results for display

        providers: {
            claude: { name: 'Claude', icon: 'bot' },
            openai: { name: 'GPT-4', icon: 'sparkles' },
            gemini: { name: 'Gemini', icon: 'zap' },
            nvidia: { name: 'NVIDIA', icon: 'cpu' },
            huggingface: { name: 'Hugging Face', icon: 'brain' }
        },

        init() {
            // Get current domain and persona from parent scope
            this.currentDomain = this.$el.closest('[x-data]')?.currentDomain || 'general';
            this.currentPersona = this.$el.closest('[x-data]')?.currentPersona || '';
            // Listen for persona changes
            window.addEventListener('persona-changed', function(e) {
                this.currentPersona = e.detail.persona;
            }.bind(this));
            // History loads on demand (when user opens the panel), not on init.
            // Pre-loading fires a fetch even when the panel is hidden, and a
            // failure produces a confusing error toast on every AI chat page load.
        },

        async loadApplications() {
            if (this.applicationsList.length > 0) return; // Already loaded
            this.loadingApplications = true;
            try {
                // Try the capability map API endpoint which returns applications
                let data = await Platform.fetch('/capability-map/api/applications', { silent: true });
                if (data.applications) {
                    this.applicationsList = data.applications;
                } else if (Array.isArray(data)) {
                    this.applicationsList = data;
                }
            } catch (error) {
                // Fallback to application_mgmt route
                try {
                    let fallbackData = await Platform.fetch('/dashboard/api/applications/table-data', { silent: true });
                    this.applicationsList = fallbackData.applications || fallbackData.data || [];
                } catch (fallbackError) {
                    Platform.toast.error('Could not load applications list');
                    this.showNotification('Could not load applications list', 'error');
                }
            } finally {
                this.loadingApplications = false;
            }
        },

        async loadVendors() {
            if (this.vendorsList.length > 0) return; // Already loaded
            this.loadingVendors = true;
            try {
                // Try the Flask-RESTx API endpoint for vendors
                let data = await Platform.fetch('/api/vendors/vendors/', { silent: true });
                if (Array.isArray(data)) {
                    this.vendorsList = data;
                } else if (data.vendors) {
                    this.vendorsList = data.vendors;
                }
            } catch (error) {
                // Fallback to vendor organizations endpoint
                try {
                    let fallbackData = await Platform.fetch('/dashboard/api/vendors/organizations', { silent: true });
                    this.vendorsList = fallbackData.vendors || fallbackData.organizations || fallbackData || [];
                } catch (fallbackError) {
                    Platform.toast.error('Could not load vendors list');
                    this.showNotification('Could not load vendors list', 'error');
                }
            } finally {
                this.loadingVendors = false;
            }
        },

        async loadDocumentHistory() {
            this.loadingHistory = true;
            try {
                let data = await Platform.fetch('/ai-chat/documents?limit=10', { silent: true });
                if (data.success) {
                    this.documentHistory = data.documents;
                } else {
                    /* Platform.fetch throws on a non-2xx, but a 200 carrying
                       success:false reached here and fell through the `if`
                       silently, leaving the panel at its initialiser — the
                       exact "empty panel is indistinguishable from no
                       documents uploaded yet" the catch below exists to
                       prevent. Both failure shapes must report. */
                    throw new Error(data.error || 'document history unavailable');
                }
            } catch (error) {
                // History load failed — surface it: an empty panel is
                // indistinguishable from "no documents uploaded yet".
                Platform.toast.warning('Could not load document history');
                this.documentHistory = [];
            } finally {
                this.loadingHistory = false;
            }
        },

        async deleteDocument(docId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Delete Document',
                content: '<p class="text-sm text-muted-foreground">Are you sure you want to delete this document?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: async function() { await self._doDeleteDocument(docId); } }
                ]
            });
            window.modalManager.open(modalId);
        },

        async _doDeleteDocument(docId) {
            // Ensure docId is a number (handle large integers)
            let docIdNum = typeof docId === 'string' ? parseInt(docId, 10) : docId;


            try {
                let data = await Platform.fetch('/ai-chat/documents/' + docIdNum, {
                    method: 'DELETE'
                });

                if (data.success) {
                    this.showNotification('Document deleted successfully', 'success');
                    // Reload history to update the list
                    await this.loadDocumentHistory();
                } else {
                    this.showNotification('Failed to delete document: ' + (data.error || 'Unknown error'), 'error');
                }
            } catch (error) {
                let errorMsg = error.message || 'Unknown error occurred';
                Platform.toast.error('Error deleting document: ' + errorMsg);
                this.showNotification('Error deleting document: ' + errorMsg, 'error');
            }
        },

        async analyzeDocumentFromHistory(docId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Analyze Document',
                content: '<p class="text-sm text-muted-foreground">Analyze this document? This will extract and create ArchiMate elements.</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Analyze', class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90', action: 'analyze', handler: async function() {
                        try {
                            // Show loading state
                            self.loadingHistory = true;

                            let data = await Platform.fetch('/ai-chat/documents/' + docId + '/re-analyze', {
                                method: 'POST'
                            });

                            if (data.success) {
                                let createdCount = data.analysis_results?.created_elements || 0;
                                self.showNotification(
                                    'Document analyzed successfully! Created ' + createdCount + ' elements.',
                                    'success'
                                );
                                // Reload history to show updated status
                                await self.loadDocumentHistory();
                            } else {
                                self.showNotification('Failed to analyze: ' + (data.error || 'Unknown error'), 'error');
                            }
                        } catch (error) {
                            Platform.toast.error('Error analyzing document');
                            self.showNotification('Error analyzing document: ' + error.message, 'error');
                        } finally {
                            self.loadingHistory = false;
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        async reAnalyzeDocument(docId) {
            let self = this;
            let modalId = window.modalManager.createModal({
                title: 'Re-analyze Document',
                content: '<p class="text-sm text-muted-foreground">Re-analyze this document? This may create duplicate elements.</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Re-analyze', class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-amber-600 border border-transparent rounded-md hover:bg-amber-700', action: 'reanalyze', handler: async function() {
                        try {
                            // Show loading state
                            self.loadingHistory = true;

                            let data = await Platform.fetch('/ai-chat/documents/' + docId + '/re-analyze', {
                                method: 'POST'
                            });
                            if (data.success) {
                                let createdCount = data.analysis_results?.created_elements || 0;
                                self.showNotification(
                                    'Document re-analyzed successfully! Created ' + createdCount + ' new elements.',
                                    'success'
                                );
                                // Reload history to show updated status
                                await self.loadDocumentHistory();
                            } else {
                                self.showNotification('Failed to re-analyze: ' + (data.error || 'Unknown error'), 'error');
                            }
                        } catch (error) {
                            Platform.toast.error('Error re-analyzing document');
                            self.showNotification('Error re-analyzing: ' + error.message, 'error');
                        } finally {
                            self.loadingHistory = false;
                        }
                    } }
                ]
            });
            window.modalManager.open(modalId);
        },

        formatFileSize(bytes) {
            if (!bytes) return 'Unknown';
            let units = ['B', 'KB', 'MB', 'GB'];
            let size = bytes;
            let unitIndex = 0;
            while (size >= 1024 && unitIndex < units.length - 1) {
                size /= 1024;
                unitIndex++;
            }
            return size.toFixed(1) + ' ' + units[unitIndex];
        },

        getStatusColor(status) {
            let colors = {
                'completed': 'text-emerald-600 bg-emerald-500/10',
                'failed': 'text-destructive bg-destructive/10',
                'partial': 'text-amber-600 bg-amber-500/10',
                'analyzing': 'text-primary bg-primary/10',
                'uploading': 'text-muted-foreground bg-muted'
            };
            return colors[status] || colors['uploading'];
        },

        handleDrop(event) {
            this.dragover = false;
            let files = event.dataTransfer.files;
            if (files.length > 0) {
                this.selectFile(files[0]);
            }
        },

        handleFileSelect(event) {
            let files = event.target.files;
            if (files.length > 0) {
                this.selectFile(files[0]);
            }
        },

        selectFile(file) {
            // Validate file size (50MB limit)
            let maxSize = 50 * 1024 * 1024;
            if (file.size > maxSize) {
                this.showNotification('File too large. Maximum size is 50MB.', 'error');
                return;
            }

            this.selectedFile = file;
            this.clearResults();
        },

        clearFile() {
            this.selectedFile = null;
            this.clearResults();
            if (this.$refs.fileInput) {
                this.$refs.fileInput.value = '';
            }
        },

        clearResults() {
            this.analysisResults = null;
            this.uploadProgress = 0;
            this.previewElements = [];
            this.selectedElements = {};
            this.editingElement = null;
        },

        clearPreview() {
            this.previewElements = [];
            this.selectedElements = {};
            this.editingElement = null;
            this.originalElements = {};
        },

        selectAllElements() {
            this.previewElements.forEach(function(_, index) {
                this.selectedElements[index] = true;
            }.bind(this));
        },

        selectNoneElements() {
            this.previewElements.forEach(function(_, index) {
                this.selectedElements[index] = false;
            }.bind(this));
        },

        getSelectedCount() {
            return Object.values(this.selectedElements).filter(function(v) { return v === true; }).length;
        },

        removePreviewElement(index) {
            this.previewElements.splice(index, 1);
            // Rebuild selectedElements with updated indices
            let newSelected = {};
            this.previewElements.forEach(function(_, i) {
                if (i < index) {
                    newSelected[i] = this.selectedElements[i];
                } else {
                    newSelected[i] = this.selectedElements[i + 1];
                }
            }.bind(this));
            this.selectedElements = newSelected;
            if (this.editingElement === index) {
                this.editingElement = null;
            } else if (this.editingElement > index) {
                this.editingElement--;
            }
        },

        async previewDocument() {
            if (!this.selectedFile) return;

            this.uploading = true;
            this.uploadStatus = 'Extracting elements...';
            this.uploadProgress = 0;

            let progressInterval = null;
            try {
                let formData = new FormData();
                formData.append('file', this.selectedFile);
                formData.append('domain', this.currentDomain);
                formData.append('provider', this.selectedProvider);
                formData.append('persona', this.currentPersona || '');
                formData.append('analysis_context', this.analysisContext);
                formData.append('preview_only', 'true');  // Key flag for preview mode
                formData.append('use_simple_parsing', this.useSimpleParsing ? 'true' : 'false');  // Bypass LLM flag

                if (this.analysisContext === 'application' && this.targetApplicationId) {
                    formData.append('target_application_id', this.targetApplicationId);
                } else if (this.analysisContext === 'vendor' && this.targetVendorId) {
                    formData.append('target_vendor_id', this.targetVendorId);
                }

                // Simulate progress
                let self = this;
                progressInterval = setInterval(function() {
                    if (self.uploadProgress < 90) {
                        self.uploadProgress += Math.random() * 10;
                    }
                }, 200);

                let result = await Platform.fetch('/ai-chat/upload-document', {
                    method: 'POST',
                    body: formData,
                    errorMsg: 'Failed to extract elements from document'
                });

                clearInterval(progressInterval);
                progressInterval = null;
                this.uploadProgress = 100;

                if (result.success) {
                    // Store elements for preview (not created yet)
                    this.previewElements = result.extracted_elements || [];

                    if (this.previewElements.length === 0) {
                        // No elements found - show helpful message
                        this.uploadStatus = 'No elements found';
                        let metadata = result.metadata || {};
                        let errorMsgText = metadata.error || 'The document was analyzed but no ArchiMate elements were identified.';
                        let suggestion = metadata.suggestion || 'Try uploading a document that contains application names, system descriptions, or architecture diagrams.';
                        this.showNotification(
                            'No elements extracted. ' + errorMsgText + ' ' + suggestion,
                            'warning'
                        );
                        // Still set previewElements to empty array so UI can show "no elements" state
                    } else {
                        // Select all by default
                        this.previewElements.forEach(function(_, index) {
                            this.selectedElements[index] = true;
                        }.bind(this));
                        this.uploadStatus = 'Elements extracted!';
                        this.showNotification('Found ' + this.previewElements.length + ' elements. Review and select which to create.', 'info');
                    }
                } else {
                    throw new Error(result.error || result.message || 'Preview failed');
                }

            } catch (error) {
                Platform.toast.error('Failed to extract elements: ' + (error.message || 'Unknown error'));
                this.showNotification('Failed to extract elements: ' + error.message, 'error');
            } finally {
                if (progressInterval) clearInterval(progressInterval);
                this.uploading = false;
            }
        },

        async createSelectedElements() {
            let selectedToCreate = this.previewElements.filter(function(_, index) {
                return this.selectedElements[index];
            }.bind(this));

            if (selectedToCreate.length === 0) {
                this.showNotification('No elements selected to create', 'error');
                return;
            }

            this.creatingElements = true;

            try {
                let result = await Platform.fetch('/ai-chat/create-elements', {
                    method: 'POST',
                    body: {
                        elements: selectedToCreate,
                        analysis_context: this.analysisContext,
                        target_application_id: this.targetApplicationId || null,
                        target_vendor_id: this.targetVendorId || null
                    }
                });

                if (result.success) {
                    this.analysisResults = {
                        created_elements: result.created_count,
                        created_details: result.created_elements,
                        errors: result.errors,
                        confidence: 'high',
                        chat_context_summary: 'Created ' + result.created_count + ' ArchiMate elements'
                    };
                    this.previewElements = [];
                    this.selectedElements = {};

                    let targetInfo = this.analysisContext === 'application' && this.targetApplicationId
                        ? ' linked to application'
                        : this.analysisContext === 'vendor' && this.targetVendorId
                        ? ' linked to vendor'
                        : '';

                    // Show detailed success notification with element breakdown
                    this.showElementCreationDetails(result.created_count, result.created_elements, result.errors, targetInfo);

                    // Reload document history
                    this.loadDocumentHistory();
                } else {
                    throw new Error(result.error || 'Creation failed');
                }

            } catch (error) {
                Platform.toast.error('Failed to create elements: ' + (error.message || 'Unknown error'));
                this.showNotification('Failed to create elements: ' + error.message, 'error');
            } finally {
                this.creatingElements = false;
            }
        },

        async analyzeDocument() {
            if (!this.selectedFile) return;

            this.uploading = true;
            this.uploadStatus = 'Analyzing document...';
            this.uploadProgress = 0;

            let progressInterval = null;
            try {
                let formData = new FormData();
                formData.append('file', this.selectedFile);
                formData.append('domain', this.currentDomain);
                formData.append('provider', this.selectedProvider);
                formData.append('persona', this.currentPersona || '');
                formData.append('conversation_context', JSON.stringify(this.getConversationContext()));
                formData.append('use_simple_parsing', this.useSimpleParsing ? 'true' : 'false');  // Bypass LLM flag

                // Add context-aware parameters
                formData.append('analysis_context', this.analysisContext);
                if (this.analysisContext === 'application' && this.targetApplicationId) {
                    formData.append('target_application_id', this.targetApplicationId);
                } else if (this.analysisContext === 'vendor' && this.targetVendorId) {
                    formData.append('target_vendor_id', this.targetVendorId);
                }

                // Simulate progress
                let self = this;
                progressInterval = setInterval(function() {
                    if (self.uploadProgress < 90) {
                        self.uploadProgress += Math.random() * 10;
                    }
                }, 200);

                let result = await Platform.fetch('/ai-chat/upload-document', {
                    method: 'POST',
                    body: formData,
                    errorMsg: 'Failed to analyze document'
                });

                clearInterval(progressInterval);
                progressInterval = null;
                this.uploadProgress = 100;

                if (result.success) {
                    this.analysisResults = result.analysis_results;
                    this.uploadStatus = 'Analysis complete!';

                    // Populate the detailed breakdown (by type, by layer, names) so the
                    // panel's "Show Details" view renders real data instead of blank
                    // sections. The targeted-upload path already does this; the main
                    // document-upload path previously did not.
                    if (result.analysis_results.created_elements > 0) {
                        this.buildCreationDetails(
                            result.analysis_results.created_elements,
                            result.analysis_results.created_details || [],
                            result.analysis_results.errors || []
                        );
                        this.showNotification(
                            'Successfully created ' + result.analysis_results.created_elements + ' ArchiMate elements! View them in the Architecture dashboard.',
                            'success'
                        );
                    }
                    // Reload document history
                    this.loadDocumentHistory();

                    // Auto-add to chat after a short delay
                    let self2 = this;
                    setTimeout(function() {
                        self2.addToChat();
                    }, 1000);
                } else {
                    throw new Error(result.error || 'Analysis failed');
                }

            } catch (error) {
                Platform.toast.error('Failed to analyze document: ' + (error.message || 'Unknown error'));
                this.showNotification('Failed to analyze document: ' + error.message, 'error');
            } finally {
                if (progressInterval) clearInterval(progressInterval);
                this.uploading = false;
            }
        },

        getConversationContext() {
            // Try to get conversation context from parent chat component
            let parentData = this.$el.closest('[x-data]')?._data;
            return parentData?.messages || [];
        },

        addToChat() {
            if (!this.analysisResults) return;

            // Extract the correct data structure to match what the listener expects
            let eventData = {
                filename: this.selectedFile?.name || 'Unknown',
                summary: this.analysisResults?.chat_context_summary || 'Document has been analyzed.',
                elementsFound: this.analysisResults?.created_elements || 0,
                elementsCreated: this.analysisResults?.created_details?.length || this.analysisResults?.created_elements || 0,
                confidence: this.analysisResults?.confidence || 'Medium',
                // Keep the full analysisResults for any additional processing
                analysisResults: this.analysisResults,
                domain: this.currentDomain
            };

            // Emit event to parent chat component with the correct property structure
            this.$dispatch('add-document-analysis', eventData);

            this.showUpload = false;
            this.clearFile();
        },

        askQuestion(question) {
            // Emit event to ask question in chat
            this.$dispatch('ask-question', { question: question });
        },

        analyzeWithDifferentProvider() {
            // Switch provider and re-analyze
            let providers = Object.keys(this.providers);
            let currentIndex = providers.indexOf(this.selectedProvider);
            this.selectedProvider = providers[(currentIndex + 1) % providers.length];

            this.clearResults();
            let self = this;
            setTimeout(function() { self.analyzeDocument(); }, 100);
        },

        startEditingElement(index) {
            // Store original element for feedback
            this.originalElements[index] = JSON.parse(JSON.stringify(this.previewElements[index]));
            this.editingElement = index;
        },

        cancelElementEdit(index) {
            // Restore original element
            if (this.originalElements[index]) {
                this.previewElements[index] = this.originalElements[index];
                delete this.originalElements[index];
            }
            this.editingElement = null;
        },

        async saveElementEdit(index) {
            let original = this.originalElements[index];
            let corrected = this.previewElements[index];

            // Check if element was actually changed
            let changed = JSON.stringify(original) !== JSON.stringify(corrected);

            if (changed && this.currentUploadId && original) {
                // Record feedback for learning. Deliberately best-effort/silent: this is a
                // background training signal, not the edit itself — the edit below always
                // succeeds locally regardless of whether this call lands.
                try {
                    await Platform.fetch('/ai-chat/documents/' + this.currentUploadId + '/feedback', {
                        method: 'POST',
                        body: {
                            original_element: original,
                            corrected_element: corrected
                        },
                        silent: true
                    });
                } catch (error) { /* swallow-ok: background training signal, not the user's edit; the edit below is applied locally either way and telling the user their correction "failed" would be false */
                }
            }

            delete this.originalElements[index];
            this.editingElement = null;
            this.showNotification('Element updated', 'success');
        },

        formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            let k = 1024;
            let sizes = ['Bytes', 'KB', 'MB', 'GB'];
            let i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        },

        showNotification(message, type) {
            type = type || 'info';
            // Emit notification event
            this.$dispatch('show-notification', { message: message, type: type });
        },

        // Pure aggregation: groups created elements by type/layer and stores the
        // breakdown for the panel's "Show Details" view. No toast/console side
        // effects, so it is safe to call on every expand click.
        buildCreationDetails(totalCount, createdElements, errors, targetInfo) {
            targetInfo = targetInfo || '';
            let byType = {};
            let byLayer = {};
            let elementNames = [];

            if (createdElements && Array.isArray(createdElements)) {
                createdElements.forEach(function(elem) {
                    let type = elem.type || 'Unknown';
                    byType[type] = (byType[type] || 0) + 1;

                    let layer = elem.layer || 'Unknown';
                    byLayer[layer] = (byLayer[layer] || 0) + 1;

                    if (elem.name) {
                        elementNames.push(elem.name);
                    }
                });
            }

            this.creationDetails = {
                totalCount: totalCount,
                byType: byType,
                byLayer: byLayer,
                elementNames: elementNames,
                errors: errors || [],
                targetInfo: targetInfo
            };
            return this.creationDetails;
        },

        showElementCreationDetails(totalCount, createdElements, errors, targetInfo) {
            targetInfo = targetInfo || '';
            // Store the visual breakdown (by type/layer/names)
            this.buildCreationDetails(totalCount, createdElements, errors, targetInfo);
            let byType = this.creationDetails.byType;
            let byLayer = this.creationDetails.byLayer;
            let elementNames = this.creationDetails.elementNames;

            // Build detailed message for notification
            let details = 'Successfully created ' + totalCount + ' ArchiMate element' + (totalCount !== 1 ? 's' : '') + targetInfo + '!\n\n';

            // Breakdown by type
            if (Object.keys(byType).length > 0) {
                details += 'By Element Type:\n';
                Object.entries(byType)
                    .sort(function(a, b) { return b[1] - a[1]; })
                    .forEach(function(entry) {
                        details += '  \u2022 ' + entry[0] + ': ' + entry[1] + '\n';
                    });
                details += '\n';
            }

            // Breakdown by layer
            if (Object.keys(byLayer).length > 0) {
                details += 'By ArchiMate Layer:\n';
                Object.entries(byLayer)
                    .sort(function(a, b) { return b[1] - a[1]; })
                    .forEach(function(entry) {
                        details += '  \u2022 ' + entry[0] + ': ' + entry[1] + '\n';
                    });
                details += '\n';
            }

            // Show element names (first 20, then count remaining)
            if (elementNames.length > 0) {
                let maxShow = 20;
                let shown = elementNames.slice(0, maxShow);
                details += 'Element Names (' + elementNames.length + ' total):\n';
                shown.forEach(function(name, idx) {
                    details += '  ' + (idx + 1) + '. ' + name + '\n';
                });
                if (elementNames.length > maxShow) {
                    details += '  ... and ' + (elementNames.length - maxShow) + ' more\n';
                }
            }

            // Show errors if any
            if (errors && errors.length > 0) {
                details += '\nErrors (' + errors.length + '):\n';
                errors.slice(0, 5).forEach(function(err) {
                    details += '  \u2022 ' + err + '\n';
                });
                if (errors.length > 5) {
                    details += '  ... and ' + (errors.length - 5) + ' more errors\n';
                }
            }

            // Show notification with details
            this.showNotification(details, 'success');

        },

        linkToEntity(match) {
            // Emit event to link document analysis to existing entity
            this.$dispatch('link-to-entity', {
                entityId: match.entity_id,
                entityType: match.entity_type,
                entityName: match.entity_name,
                analysisResults: this.analysisResults
            });
            this.showNotification('Linked to ' + match.entity_name, 'success');
        },

        createEntity(entity) {
            // Emit event to create new entity from extracted data
            this.$dispatch('create-entity', {
                name: entity.name,
                type: entity.type,
                extractedData: entity.extracted_data,
                recommendation: entity.recommendation
            });
            this.showNotification('Creating ' + entity.type + ': ' + entity.name, 'info');
        }
    };
}
