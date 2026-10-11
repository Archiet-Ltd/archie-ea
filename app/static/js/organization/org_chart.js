/**
 * Organization Chart — D3 v7 tree of BusinessActors linked by
 * Composition/Aggregation ArchiMateRelationships.
 *
 * Alpine.js component: orgChart()
 * Mirrors app/static/js/capability_map/tree.js's use of D3 v7 (loaded via
 * the same CDN tag as the capability network view,
 * /static/vendor/d3.min.js) but builds a dendrogram (d3.tree) instead
 * of a force simulation, since org charts are strict hierarchies.
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('orgChart', () => ({
        loading: true,
        fallback: false,
        roots: [],
        groups: [],
        actorCount: 0,
        relationshipCount: 0,

        // Selected-unit panel state.
        selectedUnit: null,
        panelCapabilities: null,
        capabilitiesLoading: false,
        addChildName: '',
        renameName: '',
        moveParentId: '',
        allNodes: [],
        movableParents: [],

        async init() {
            await this.loadData();
        },

        async loadData() {
            this.loading = true;
            try {
                // Platform.fetch throws on non-ok responses, returning parsed data directly.
                const raw = await Platform.fetch('/organization/chart/api/data');
                // Unchecked, a 500 parsed to `{}`: roots and groups came out empty and
                // the chart rendered blank, reading as "no org structure modelled".
                // (Now handled by Platform.fetch throwing before reaching this point.)
                // F-16, Capgemini dry-run: the route wraps its payload in
                // success_response() -> {"data": {...}}; this read the fields
                // straight off the envelope, so a real Business Actor never
                // showed — "No Business Actors yet" while the layer had one.
                const data = raw.data || raw;

                this.fallback = !!data.fallback;
                this.actorCount = data.actor_count || 0;
                this.relationshipCount = data.relationship_count || 0;

                if (this.fallback) {
                    this.groups = data.groups || [];
                } else {
                    this.roots = data.roots || [];
                    this.$nextTick(() => this.renderTree());
                }
                this.refreshMovableParents();
            } catch (e) {
                // Counts are null, not 0: nothing was counted.
                this.roots = [];
                this.groups = [];
                this.actorCount = null;
                this.relationshipCount = null;
                // The component already paints its own inline error state (blank canvas),
                // so we pass silent:true to avoid a duplicate global toast.
                // However, we still need to inform the user that the request failed.
                // The existing toast below is the user‑visible error path; we keep it.
                if (window.Platform && Platform.toast) {
                    // duration 0: with counts null the page renders nothing at all, so
                    // this toast is the only thing on screen — it must not time out.
                    Platform.toast.error('Could not load the org chart — the canvas is blank because the request failed.', { duration: 0 });
                }
                // Rethrow to propagate the failure (rule 2).
                throw e;
            } finally {
                this.loading = false;
            }
        },

        // ── Selected-unit panel ────────────────────────────────────────────

        selectUnit(unit) {
            if (!unit || !unit.id) return;
            this.selectedUnit = { id: unit.id, name: unit.name, actor_type: unit.actor_type || '' };
            this.addChildName = '';
            this.renameName = '';
            this.moveParentId = '';
            this.refreshMovableParents();
            this.loadUnitCapabilities(unit.id);
        },

        clearSelection() {
            this.selectedUnit = null;
            this.panelCapabilities = null;
        },

        async loadUnitCapabilities(unitId) {
            this.capabilitiesLoading = true;
            try {
                const raw = await Platform.fetch(`/organization/chart/api/units/${unitId}/capabilities`);
                // The route routes capacity: {"data": {...}}
                this.panelCapabilities = raw.data || raw;
            } catch (e) {
                this.panelCapabilities = null;
                if (window.Platform && Platform.toast) {
                    Platform.toast.error('Could not load this unit\'s capabilities.');
                }
            } finally {
                this.capabilitiesLoading = false;
            }
        },

        refreshMovableParents() {
            const nodes = [];
            const walk = (roots) => {
                for (const node of roots || []) {
                    nodes.push({ id: node.id, name: node.name, actor_type: node.actor_type });
                    walk(node.children);
                }
            };
            if (this.fallback) {
                for (const group of this.groups || []) {
                    for (const actor of group.actors || []) {
                        nodes.push({ id: actor.id, name: actor.name, actor_type: group.actor_type });
                    }
                }
            } else {
                walk(this.roots);
            }
            this.allNodes = nodes;
            if (this.selectedUnit) {
                // Neither the unit nor any of its descendants is a valid move
                // target (each would make a cycle).
                const excluded = this.descendantIds(this.selectedUnit.id);
                excluded.add(this.selectedUnit.id);
                this.movableParents = nodes.filter(n => !excluded.has(n.id));
            } else {
                this.movableParents = nodes;
            }
        },

        descendantIds(unitId) {
            const found = new Set();
            const seen = new Set();
            const locate = (nodes) => {
                for (const node of nodes || []) {
                    if (node.id === unitId) return node;
                    const hit = locate(node.children);
                    if (hit) return hit;
                }
                return null;
            };
            const start = this.fallback ? null : locate(this.roots);
            const stack = start ? [...(start.children || [])] : [];
            while (stack.length) {
                const node = stack.pop();
                if (seen.has(node.id)) continue;
                seen.add(node.id);
                found.add(node.id);
                stack.push(...(node.children || []));
            }
            return found;
        },

        // The server's refusal reason when it sent one, else a fixed line.
        reportFailure(e, fallback) {
            if (!(window.Platform && Platform.toast)) return;
            const reason = e && e.message && !/^HTTP \d+$/.test(e.message) ? e.message : '';
            Platform.toast.error(reason || fallback);
        },

        // ── Hierarchy editing ──────────────────────────────────────────────
        // Every call goes to a role-gated route; a caller without the roles
        // gets a 403 the same way the RACI cell editor refuses them.

        async addChild() {
            const name = (this.addChildName || '').trim();
            if (!this.selectedUnit || !name) return;
            try {
                await Platform.fetch('/organization/chart/api/units', {
                    method: 'POST',
                    silent: true,
                    body: { name, parent_id: this.selectedUnit.id, actor_type: 'Department' },
                });
                this.addChildName = '';
                await this.loadData();
            } catch (e) {
                this.reportFailure(e, 'Could not add the unit.');
            }
        },

        async renameUnit() {
            const name = (this.renameName || '').trim();
            if (!this.selectedUnit || !name) return;
            try {
                await Platform.fetch(`/organization/chart/api/units/${this.selectedUnit.id}`, {
                    method: 'PATCH',
                    silent: true,
                    body: { name },
                });
                this.selectedUnit.name = name;
                this.renameName = '';
                await this.loadData();
            } catch (e) {
                this.reportFailure(e, 'Could not rename the unit.');
            }
        },

        async moveUnit() {
            if (!this.selectedUnit) return;
            try {
                await Platform.fetch(`/organization/chart/api/units/${this.selectedUnit.id}/parent`, {
                    method: 'PATCH',
                    silent: true,
                    body: { parent_id: this.moveParentId ? Number(this.moveParentId) : null },
                });
                this.moveParentId = '';
                await this.loadData();
            } catch (e) {
                this.reportFailure(e, 'Could not move the unit.');
            }
        },

        async removeUnit() {
            if (!this.selectedUnit) return;
            if (!(await Platform.modal.confirm(`Remove "${this.selectedUnit.name}"? Refused while it has children, assigned people or other relationships.`))) {
                return;
            }
            try {
                await Platform.fetch(`/organization/chart/api/units/${this.selectedUnit.id}`, {
                    method: 'DELETE',
                    silent: true,
                });
                this.clearSelection();
                await this.loadData();
            } catch (e) {
                this.reportFailure(e, 'Could not remove the unit.');
            }
        },

        renderTree() {
            const container = document.getElementById('org-chart-container');
            if (!container || typeof d3 === 'undefined') return;
            container.innerHTML = '';

            if (!this.roots || this.roots.length === 0) return;

            // Multiple roots are common (several top-level Business Units) —
            // wrap them under a synthetic invisible root so d3.hierarchy can
            // lay out a single tree.
            const syntheticRoot = {
                name: 'Organization',
                actor_type: null,
                _synthetic: true,
                children: this.roots,
            };

            const root = d3.hierarchy(syntheticRoot);
            const nodeCount = root.descendants().length;

            const nodeWidth = 190;
            const nodeHeight = 90;
            const width = Math.max(container.clientWidth || 900, root.leaves().length * nodeWidth);
            const height = Math.max(500, root.height * nodeHeight + 120);

            const treeLayout = d3.tree().size([width - 160, height - 100]);
            treeLayout(root);

            const svg = d3.select(container)
                .append('svg')
                .attr('id', 'org-chart-svg')
                .attr('width', width)
                .attr('height', height);

            const g = svg.append('g').attr('transform', 'translate(80,60)');

            // Links (skip the synthetic root's own edges to itself — d3 still
            // draws them, so just style them the same; visually harmless
            // since the synthetic root renders as an invisible node too).
            g.selectAll('.org-link')
                .data(root.links())
                .enter()
                .append('path')
                .attr('class', 'org-link')
                .attr('d', d3.linkVertical().x(d => d.x).y(d => d.y));

            const node = g.selectAll('.org-node')
                .data(root.descendants())
                .enter()
                .append('g')
                .attr('class', 'org-node')
                .attr('transform', d => `translate(${d.x},${d.y})`);

            // Hide the synthetic root visually but keep it in the layout so
            // multiple top-level actors fan out symmetrically.
            const visibleNode = node.filter(d => !d.data._synthetic);

            visibleNode.append('rect')
                .attr('x', -80)
                .attr('y', -22)
                .attr('width', 160)
                .attr('height', 44)
                .attr('rx', 6)
                .attr('fill', 'hsl(var(--card))')
                .attr('stroke', 'hsl(var(--primary))')
                .attr('style', 'cursor: pointer;');

            visibleNode.append('text')
                .attr('text-anchor', 'middle')
                .attr('y', -4)
                .attr('fill', 'hsl(var(--foreground))')
                .style('font-weight', '600')
                .text(d => this.truncate(d.data.name, 22));

            visibleNode.append('text')
                .attr('text-anchor', 'middle')
                .attr('y', 12)
                .attr('fill', 'hsl(var(--muted-foreground))')
                .text(d => d.data.actor_type || '');

            // Clicking a unit selects it and opens the capabilities/editing
            // panel next to the chart.
            visibleNode.on('click', (event, d) => {
                event.stopPropagation();
                this.selectUnit({ id: d.data.id, name: d.data.name, actor_type: d.data.actor_type });
            });

            svg.call(
                d3.zoom().scaleExtent([0.4, 2]).on('zoom', (event) => {
                    g.attr('transform', event.transform);
                })
            );
        },

        truncate(text, max) {
            if (!text) return '';
            return text.length > max ? text.substring(0, max) + '…' : text;
        },
    }));
});
