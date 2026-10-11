/**
 * Workflow Designer — Alpine.js app for drag-drop workflow building.
 *
 * Wires JointJS graph, WorkflowShapes palette, properties panel,
 * and API calls for save/load/compile.
 */
function workflowDesigner() {
    'use strict';

    return {
        /* -- State ------------------------------------------------ */
        graph: null,
        paper: null,
        workflowName: 'New Workflow',
        workflowId: null,
        solutionId: null,
        selectedStep: null,
        paletteSearch: '',
        templates: [],
        templatesError: null,
        compileResult: null,

        /* -- Palette ---------------------------------------------- */
        get paletteCategories() {
            let cats = [];
            WorkflowShapes.PALETTE.forEach(function(p) {
                if (cats.indexOf(p.category) === -1) cats.push(p.category);
            });
            return cats;
        },

        filteredPalette: function(category) {
            let search = (this.paletteSearch || '').toLowerCase();
            return WorkflowShapes.PALETTE.filter(function(p) {
                return p.category === category &&
                       (!search || p.label.toLowerCase().indexOf(search) !== -1);
            });
        },

        stepColor: function(type) {
            return WorkflowShapes.stepColor(type).fill;
        },

        /* -- Init ------------------------------------------------- */
        init: function() {
            let self = this;

            // Parse URL params
            let params = new URLSearchParams(window.location.search);
            self.solutionId = parseInt(params.get('solution_id')) || null;
            self.workflowId = parseInt(params.get('workflow_id')) || null;

            // Create JointJS graph + paper
            self.graph = new joint.dia.Graph();
            self.paper = new joint.dia.Paper({
                el: document.getElementById('wf-canvas'),
                model: self.graph,
                width: '100%',
                height: '100%',
                gridSize: 12,
                drawGrid: false,
                background: { color: 'transparent' },
                interactive: { linkMove: true, labelMove: false },
                defaultLink: function() {
                    return new joint.shapes.standard.Link({
                        attrs: {
                            line: {
                                stroke: '#64748b',
                                strokeWidth: 2,
                                targetMarker: { type: 'path', d: 'M 10 -5 0 0 10 5 z', fill: '#64748b' }
                            }
                        }
                    });
                },
                validateConnection: function(cellViewS, magnetS, cellViewT, magnetT) {
                    // Prevent self-loops
                    if (cellViewS === cellViewT) return false;
                    // Only connect output to input
                    let targetPort = magnetT ? magnetT.getAttribute('port') : null;
                    return targetPort === 'in';
                },
                snapLinks: { radius: 20 },
                linkPinning: false
            });

            // Element click -> select step
            self.paper.on('element:pointerclick', function(cellView) {
                self._selectElement(cellView.model);
            });

            // Blank click -> deselect
            self.paper.on('blank:pointerclick', function() {
                self.selectedStep = null;
            });

            // Load templates
            self._loadTemplates();

            // Load existing workflow if ID provided
            if (self.workflowId) {
                self._loadWorkflow(self.workflowId);
            }
        },

        /* -- Palette drag-drop ------------------------------------ */
        _dragGhost: null,

        onPaletteDragStart: function(event, item) {
            event.dataTransfer.setData('text/plain', JSON.stringify(item));
            event.dataTransfer.effectAllowed = 'copy';

            // Create drag ghost
            let ghost = document.createElement('div');
            ghost.className = 'wf-drag-ghost';
            ghost.textContent = item.label;
            document.body.appendChild(ghost);
            this._dragGhost = ghost;

            let moveGhost = function(e) {
                ghost.style.left = e.clientX + 'px';
                ghost.style.top = e.clientY + 'px';
            };
            document.addEventListener('mousemove', moveGhost);
            ghost._moveHandler = moveGhost;
        },

        onPaletteDragEnd: function() {
            if (this._dragGhost) {
                document.removeEventListener('mousemove', this._dragGhost._moveHandler);
                this._dragGhost.remove();
                this._dragGhost = null;
            }
        },

        onCanvasDrop: function(event) {
            let data;
            try {
                data = JSON.parse(event.dataTransfer.getData('text/plain'));
            } catch (e) { return; }

            // Account for paper pan/zoom
            let localPoint = this.paper.clientToLocalPoint({ x: event.clientX, y: event.clientY });

            let stepDef = {
                id: 'step-' + Date.now(),
                type: data.type,
                position: { x: localPoint.x - 90, y: localPoint.y - 30 },
                properties: {}
            };

            let el = WorkflowShapes.createStep(this.graph, stepDef);
            this._selectElement(el);
        },

        /* -- Element selection ------------------------------------ */
        _selectElement: function(element) {
            this.selectedStep = {
                _element: element,
                type: element.get('stepType'),
                id: element.get('stepId'),
                entity: (element.get('stepProperties') || {}).entity || '',
                event: (element.get('stepProperties') || {}).event || 'created',
                field: (element.get('stepProperties') || {}).field || '',
                operator: (element.get('stepProperties') || {}).operator || 'eq',
                value: (element.get('stepProperties') || {}).value || '',
                to: (element.get('stepProperties') || {}).to || '',
                subject: (element.get('stepProperties') || {}).subject || '',
                properties: element.get('stepProperties') || {}
            };
        },

        updateStepProperties: function() {
            if (!this.selectedStep || !this.selectedStep._element) return;
            let el = this.selectedStep._element;
            let props = Object.assign({}, this.selectedStep.properties, {
                entity: this.selectedStep.entity,
                event: this.selectedStep.event,
                field: this.selectedStep.field,
                operator: this.selectedStep.operator,
                value: this.selectedStep.value,
                to: this.selectedStep.to,
                subject: this.selectedStep.subject
            });
            el.set('stepProperties', props);
        },

        deleteSelectedStep: function() {
            if (!this.selectedStep || !this.selectedStep._element) return;
            this.selectedStep._element.remove();
            this.selectedStep = null;
        },

        /* -- Canvas actions --------------------------------------- */
        clearCanvas: function() {
            this.graph.clear();
            this.selectedStep = null;
        },

        /* -- Templates -------------------------------------------- */
        /* Platform.fetch (core/03-fetch.js) is this application's HTTP wrapper:
           it returns the PARSED body, serialises a plain-object body, injects
           CSRF, and rejects on a non-ok response. The previous code called
           `Entelim.fetch`/`Entelim.toast` — no such global has ever existed in
           this codebase — so every request and every toast on this page threw
           "Entelim is not defined" and the designer could not load templates,
           save, or compile. */
        _loadTemplates: function() {
            let self = this;
            Platform.fetch('/api/codegen/workflow-templates', { silent: true })
                .then(function(data) { self.templates = data.templates || []; })
                .catch(function() {
                    self.templates = [];
                    self.templatesError = 'Workflow templates could not be loaded.';
                    Platform.toast.error('Failed to load workflow templates');
                });
        },

        loadTemplate: function(templateId) {
            let self = this;
            Platform.fetch('/api/codegen/workflow-templates/' + templateId + '/instantiate', {
                method: 'POST',
                body: {}
            })
            .then(function(data) {
                if (!data || !data.workflow_definition) {
                    throw new Error('Template returned no workflow definition');
                }
                self.workflowName = data.workflow_definition.name || 'Template Workflow';
                WorkflowShapes.loadWorkflow(self.graph, data.workflow_definition);
                Platform.toast.success('Template loaded');
            })
            .catch(function() { Platform.toast.error('Failed to load template'); });
        },

        /* -- Save / Load / Compile -------------------------------- */
        saveWorkflow: function() {
            let self = this;
            let serialized = WorkflowShapes.serializeGraph(self.graph);
            let payload = {
                name: self.workflowName,
                solution_id: self.solutionId,
                workflow_definition: Object.assign({ name: self.workflowName }, serialized)
            };

            let url = self.workflowId
                ? '/api/codegen/workflow-designs/' + self.workflowId
                : '/api/codegen/workflow-designs';
            let method = self.workflowId ? 'PUT' : 'POST';

            Platform.fetch(url, { method: method, body: payload })
                .then(function(data) {
                    if (data && data.id) self.workflowId = data.id;
                    Platform.toast.success('Workflow saved');
                })
                .catch(function() { Platform.toast.error('Save failed'); });
        },

        _loadWorkflow: function(id) {
            let self = this;
            Platform.fetch('/api/codegen/workflow-designs/' + id, { silent: true })
                .then(function(data) {
                    self.workflowName = data.name || 'Workflow';
                    self.solutionId = data.solution_id;
                    if (data.workflow_definition) {
                        WorkflowShapes.loadWorkflow(self.graph, data.workflow_definition);
                    }
                })
                .catch(function() { Platform.toast.error('Failed to load workflow'); });
        },

        compileWorkflow: function() {
            let self = this;
            let serialized = WorkflowShapes.serializeGraph(self.graph);
            let payload = Object.assign({ name: self.workflowName }, serialized);

            Platform.fetch('/api/codegen/workflow-designs/compile', {
                method: 'POST',
                body: payload
            })
            .then(function(data) {
                self.compileResult = (data && data.n8n_workflow) || null;
                let nodes = (data && data.n8n_workflow && data.n8n_workflow.nodes) || [];
                Platform.toast.success('Compiled to n8n workflow (' + nodes.length + ' nodes)');
            })
            .catch(function() { Platform.toast.error('Compilation failed'); });
        }
    };
}
