/**
 * Generic Tree — Observable Plot tree visualization with CRUD.
 *
 * Alpine.js component: genericTree(apiUrl, fields)
 * Renders a dendrogram from any tree API endpoint registered in TREE_REGISTRY
 * with click-to-select, right-click context menu, depth control, and full CRUD.
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('genericTree', (apiUrl, fields) => ({
        apiUrl: apiUrl,
        allFields: fields || [],
        treeData: null,
        loading: true,
        selected: null,
        editing: false,
        nodeCount: 0,
        visibleCount: 0,
        showDeleteConfirm: false,
        ctx: { show: false, x: 0, y: 0 },
        editForm: {},
        maxDepth: 1,
        expandedIds: new Set(),

        get displayFields() {
            return this.allFields;
        },

        get editableFields() {
            return this.allFields.filter(f => f !== 'level' && f !== 'performance_score');
        },

        async init() {
            await this.loadTree();
        },

        async loadTree() {
            this.loading = true;
            try {
                // Unchecked, a 500 body became the tree: countNodes returned 0 and the
                // dendrogram rendered blank, reading as "nothing modelled here".
                this.treeData = await Platform.fetch(this.apiUrl, { silent: true });
                this.nodeCount = this.countNodes(this.treeData);
                this.renderTree();
            } catch (e) {
                this.treeData = null;
                this.nodeCount = 0;
                Platform.toast.error('Could not load the tree — the canvas is blank because the request failed.');
            } finally {
                this.loading = false;
            }
        },

        countNodes(node) {
            if (!node) return 0;
            let count = node.id ? 1 : 0;
            for (const child of (node.children || [])) {
                count += this.countNodes(child);
            }
            return count;
        },

        setDepth(d) {
            this.maxDepth = d;
            this.expandedIds.clear();
            this.renderTree();
        },

        pruneTree(node, depth) {
            if (!node) return null;
            const children = (node.children || []).map(child => {
                const isExpanded = this.expandedIds.has(child.id);
                if (depth < this.maxDepth || isExpanded) {
                    return this.pruneTree(child, depth + 1);
                }
                return {
                    ...child,
                    children: [],
                    _hasChildren: (child.children || []).length > 0,
                };
            }).filter(Boolean);

            return { ...node, children };
        },

        toggleExpand(nodeId) {
            if (this.expandedIds.has(nodeId)) {
                this.expandedIds.delete(nodeId);
            } else {
                this.expandedIds.add(nodeId);
            }
            this.renderTree();
        },

        expandAll() {
            this.maxDepth = 10;
            this.expandedIds.clear();
            this.renderTree();
        },

        renderTree() {
            const container = document.getElementById('tree-container');
            if (!container || !this.treeData || !this.treeData.children?.length) {
                if (container) container.innerHTML = '';
                return;
            }

            const pruned = this.pruneTree(this.treeData, 0);

            const links = [];
            const nodeByName = {};
            const collapsedNodes = new Set();

            const flatten = (node, parentPath) => {
                const safeName = (node.name || '').replace(/\//g, '|');
                const path = parentPath ? `${parentPath}/${safeName}` : safeName;
                links.push(path);  // every capability is drawn and counted, the top level included
                if (node.id) {
                    nodeByName[safeName] = node;
                }
                if (node._hasChildren && (!node.children || node.children.length === 0)) {
                    collapsedNodes.add(safeName);
                }
                for (const child of (node.children || [])) {
                    flatten(child, path);
                }
            };

            for (const root of pruned.children) {
                flatten(root, '');
            }

            this.visibleCount = links.length;
            const self = this;
            const treeHeight = Math.max(500, this.visibleCount * 32);

            try {
                const plot = Plot.plot({
                    axis: null,
                    margin: 10,
                    marginLeft: 40,
                    marginRight: 260,
                    width: container.clientWidth || 900,
                    height: treeHeight,
                    marks: [
                        Plot.tree(links, {
                            delimiter: '/',
                            stroke: '#64748b',
                            strokeWidth: 2.5,
                            strokeOpacity: 0.6,
                            fill: '#1e293b',
                            r: 5,
                            fontSize: 13,
                            textLayout: 'mirrored',
                        })
                    ]
                });

                // The chart carries its own <style>; the page policy admits it only with the page nonce.
                plot.querySelectorAll('style').forEach(el => el.setAttribute('nonce', container.dataset.cspNonce || ''));
                container.innerHTML = '';
                container.appendChild(plot);

                const texts = plot.querySelectorAll('text');
                texts.forEach(textEl => {
                    const rawLabel = textEl.textContent.trim();
                    const leafName = rawLabel.includes('/') ? rawLabel.split('/').pop() : rawLabel;
                    const displayName = leafName.replace(/\|/g, '/');
                    const nodeData = nodeByName[leafName];

                    textEl.textContent = displayName;
                    textEl.style.cursor = 'pointer';
                    textEl.style.userSelect = 'none';

                    if (collapsedNodes.has(leafName) && nodeData) {
                        textEl.textContent = displayName + ' [+]';
                        textEl.style.fontWeight = '600';
                    }

                    textEl.addEventListener('click', (e) => {
                        e.stopPropagation();
                        if (nodeData) {
                            if (collapsedNodes.has(leafName)) {
                                self.toggleExpand(nodeData.id);
                            } else {
                                self.selectNode(nodeData);
                            }
                        }
                    });

                    textEl.addEventListener('dblclick', (e) => {
                        e.stopPropagation();
                        if (nodeData && nodeData._hasChildren) {
                            self.toggleExpand(nodeData.id);
                        }
                    });

                    textEl.addEventListener('contextmenu', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        if (nodeData) {
                            self.selectNode(nodeData);
                            self.ctx = { show: true, x: e.clientX, y: e.clientY };
                        }
                    });
                });

                const circles = plot.querySelectorAll('circle');
                circles.forEach(circle => {
                    circle.style.cursor = 'pointer';
                });

                container.addEventListener('click', () => {
                    this.ctx.show = false;
                });

            } catch (e) {
                container.replaceChildren();
                const errEl = document.createElement('p');
                errEl.className = 'p-6 text-sm text-destructive';
                errEl.textContent = 'Tree rendering failed — ' + (e && e.message ? e.message : 'unexpected error');
                container.appendChild(errEl);
            }

            if (window.lucide) lucide.createIcons();
        },

        selectNode(nodeData) {
            this.editing = false;
            this.selected = { ...nodeData };
            this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
        },

        addRoot() {
            const form = { id: null, name: '', parent_id: null };
            for (const f of this.allFields) { form[f] = ''; }
            this.editForm = form;
            this.selected = { name: '' };
            this.editing = true;
            this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
        },

        startAddChild() {
            if (!this.selected?.id) return;
            const parentId = this.selected.id;
            const form = { id: null, name: '', parent_id: parentId };
            for (const f of this.allFields) {
                form[f] = this.selected[f] || '';
            }
            form.name = '';
            this.editForm = form;
            this.selected = { name: 'New Child' };
            this.editing = true;
            this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
        },

        startEdit() {
            if (!this.selected?.id) return;
            const form = { id: this.selected.id, name: this.selected.name || '' };
            for (const f of this.allFields) {
                form[f] = this.selected[f] || '';
            }
            this.editForm = form;
            this.editing = true;
            this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
        },

        cancelEdit() {
            this.editing = false;
            if (!this.editForm.id) this.selected = null;
        },

        async saveEdit() {
            const form = this.editForm;
            if (!form.name?.trim()) return;

            try {
                let saved;
                if (form.id) {
                    saved = await Platform.fetch.patch(`${this.apiUrl}/${form.id}`, form, { silent: true });
                } else {
                    saved = await Platform.fetch.post(this.apiUrl, form, { silent: true });
                }
                this.editing = false;
                this.selected = saved;
                await this.loadTree();
            } catch (e) {
                Platform.toast.error('Save failed. Check console.');
            }
        },

        confirmDelete() {
            if (!this.selected?.id) return;
            this.showDeleteConfirm = true;
        },

        async doDelete() {
            if (!this.selected?.id) return;
            try {
                await Platform.fetch.delete(`${this.apiUrl}/${this.selected.id}`, { silent: true });
                this.selected = null;
                this.showDeleteConfirm = false;
                await this.loadTree();
            } catch (e) {
                Platform.toast.error('Delete failed. Check console.');
            }
        },
    }));
});
