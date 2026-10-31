        // Close modal when clicking outside
        document.addEventListener('DOMContentLoaded', function() {
            const modal = document.getElementById('mapping-modal');
            if (modal) {
                modal.addEventListener('click', function(e) {
                    if (e.target === this) {
                        closeMappingModal();
                    }
                });
            }
        });

        // ============================================================================
        // Capability Mapping to APQC Processes and ArchiMate Elements
        // ============================================================================

        function openCapabilityAPQCMapping(capabilityId, capabilityName) {
            // Open unified mapping modal in APQC context
            openUnifiedMappingModal(capabilityId, capabilityName, { context: 'apqc' });
        }

        function openCapabilityArchimateMapping(capabilityId, capabilityName) {
            // Open unified mapping modal in ArchiMate context
            openUnifiedMappingModal(capabilityId, capabilityName, { context: 'archimate' });
        }

        // =============================================================================
        // ACM Technical Capability Tab Functions
        // =============================================================================

        let acmDomainsData = [];
        let acmCapabilitiesData = [];
        let acmFilteredData = [];
        let acmCurrentPage = 1;
        let acmPageSize = 25;
        let acmApplicationsList = [];

        async function loadTechnicalTab() {
            try {
                // Load domains
                const domainsData = await Platform.fetch('/capability-map/api/acm/domains');
                // Unchecked, a 500 parsed to `{}`: `domainsData.domains` was falsy, the
                // render was skipped, and the tab sat empty as if no domains existed.
                if (domainsData.domains) {
                    acmDomainsData = domainsData.domains;
                    renderACMDomains(domainsData);
                }

                // Load capabilities
                const capsData = await Platform.fetch('/capability-map/api/acm/capabilities');
                if (capsData.capabilities) {
                    acmCapabilitiesData = capsData.capabilities;
                    acmFilteredData = capsData.capabilities;
                    acmCurrentPage = 1;
                    renderACMCapabilitiesPage();
                }

                // Load applications for mapping modal
                loadApplicationsForACMMapping();

                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            } catch (error) {
                safeHTML(document.getElementById('acm-domains-grid'), `
                    <div class="col-span-full text-center text-destructive py-8">
                        <i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>
                        <p>Error loading ACM data. Please try again.</p>
                    </div>
                `);
                // Platform.fetch already shows a toast for non‑silent errors; this inline error is the user‑visible fallback.
                // No console call remains.
            }
        }

        async function loadApplicationsForACMMapping() {
            try {
                const data = await Platform.fetch('/capability-map/api/applications');
                // Unchecked, a 500 left the mapping dropdown holding only its
                // "Choose an application..." placeholder.
                if (data.applications) {
                    acmApplicationsList = data.applications;
                    populateACMApplicationsDropdown();
                }
            } catch (error) {
                // Platform.fetch already shows a toast for non‑silent errors; this inline error is the user‑visible fallback.
                if (window.Platform && Platform.toast) Platform.toast.error('Failed to load applications for mapping');
            }
        }

        function populateACMApplicationsDropdown() {
            const select = document.getElementById('acm-mapping-application');
            if (!select) return;

            safeHTML(select, '<option value="">Choose an application...</option>' +
                acmApplicationsList.map(app => `<option value="${app.id}">${app.name}</option>`).join(''));
        }

        function renderACMDomains(data) {
            const grid = document.getElementById('acm-domains-grid');
            const stats = data.statistics || {};

            // Update stats
            document.getElementById('acm-domain-count').textContent = stats.total_domains || 7;
            document.getElementById('acm-cap-count').textContent = stats.total_capabilities || 0;
            document.getElementById('acm-mapped-count').textContent = stats.mapped_capabilities || 0;
            document.getElementById('acm-coverage').textContent = (stats.overall_coverage || 0) + '%';

            // Domain colors
            const domainColors = {
                'USER-EXPERIENCE': { bg: 'bg-muted', border: 'border-primary/20', text: 'text-primary', icon: 'monitor' },
                'APPLICATION-SERVICES': { bg: 'bg-sky-50', border: 'border-sky-200', text: 'text-sky-700', icon: 'server' },
                'DATA-STORAGE': { bg: 'bg-muted', border: 'border-purple-200', text: 'text-purple-700', icon: 'database' },
                'SECURITY-IDENTITY': { bg: 'bg-destructive/5', border: 'border-destructive/20', text: 'text-destructive', icon: 'shield' },
                'DEVOPS-PLATFORM': { bg: 'bg-orange-50', border: 'border-orange-200', text: 'text-orange-700', icon: 'settings' },
                'AI-ANALYTICS': { bg: 'bg-indigo-50', border: 'border-indigo-200', text: 'text-indigo-700', icon: 'brain' },
                'COMMUNICATION': { bg: 'bg-teal-50', border: 'border-teal-200', text: 'text-teal-700', icon: 'message-circle' },
            };

            safeHTML(grid, data.domains.map(domain => {
                const colors = domainColors[domain.domain] || { bg: 'bg-muted/30', border: 'border-border', text: 'text-muted-foreground', icon: 'layers' };
                const statusColor = domain.status === 'covered' ? 'bg-primary' : domain.status === 'partial' ? 'bg-amber-500' : 'bg-destructive';

                return `
                    <div class="${colors.bg} ${colors.border} border-2 rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"
                         role="button" tabindex="0"
                         data-cmx-action="filter-acm-domain" data-domain="${cmxEscapeAttr(domain.domain)}"
                         title="Filter capabilities to ${cmxEscapeAttr(domain.name)}">
                        <div class="flex items-start justify-between mb-3">
                            <div class="p-2 rounded-lg ${colors.bg}">
                                <i data-lucide="${colors.icon}" class="w-6 h-6 ${colors.text}"></i>
                            </div>
                            <div class="w-3 h-3 rounded-full ${statusColor}" title="${domain.coverage_percentage}% coverage"></div>
                        </div>
                        <h3 class="font-semibold ${colors.text} mb-1">${domain.name}</h3>
                        <p class="text-xs text-muted-foreground mb-3 line-clamp-2">${domain.description}</p>
                        <div class="flex items-center justify-between text-sm">
                            <span class="text-muted-foreground">${domain.total_capabilities} capabilities</span>
                            <span class="${colors.text} font-medium">${domain.coverage_percentage}%</span>
                        </div>
                        <div class="mt-2 w-full bg-border rounded-full h-1.5">
                            <div class="${statusColor} h-1.5 rounded-full transition-all" style="width: ${domain.coverage_percentage}%"></div>
                        </div>
                        <div class="mt-2 flex gap-2 text-xs text-muted-foreground">
                            <span>L1: ${domain.by_level.L1}</span>
                            <span>L2: ${domain.by_level.L2}</span>
                            <span>L3: ${domain.by_level.L3}</span>
                        </div>
                    </div>
                `;
            }).join(''));
        }

        function renderACMCapabilitiesPage() {
            const totalRecords = acmFilteredData.length;
            const totalPages = Math.ceil(totalRecords / acmPageSize) || 1;
            const startIndex = (acmCurrentPage - 1) * acmPageSize;
            const endIndex = Math.min(startIndex + acmPageSize, totalRecords);
            const pageData = acmFilteredData.slice(startIndex, endIndex);

            // Update pagination info
            document.getElementById('acm-total-records').textContent = totalRecords;
            document.getElementById('acm-start-record').textContent = totalRecords > 0 ? startIndex + 1 : 0;
            document.getElementById('acm-end-record').textContent = endIndex;
            document.getElementById('acm-current-page').textContent = acmCurrentPage;
            document.getElementById('acm-total-pages').textContent = totalPages;

            // Update pagination buttons
            document.getElementById('acm-prev-btn').disabled = acmCurrentPage <= 1;
            document.getElementById('acm-next-btn').disabled = acmCurrentPage >= totalPages;

            // Render table
            renderACMCapabilities(pageData);
        }

        function renderACMCapabilities(capabilities) {
            const tbody = document.getElementById('acm-capabilities-table');

            if (!capabilities || capabilities.length === 0) {
                safeHTML(tbody, `
                    <tr>
                        <td colspan="7" class="px-6 py-12 text-center text-muted-foreground">
                            No capabilities found matching filters.
                        </td>
                    </tr>
                `);
                return;
            }

            safeHTML(tbody, capabilities.map(cap => {
                const domainBadge = getACMDomainBadge(cap.acm_domain);
                const levelBadge = getACMLevelBadge(cap.level);
                const statusBadge = cap.status === 'mapped'
                    ? '<span class="px-2 py-1 text-xs font-medium rounded-full bg-accent text-green-800">Mapped</span>'
                    : '<span class="px-2 py-1 text-xs font-medium rounded-full bg-destructive/10 text-red-800">Gap</span>';


                const roadmapBadge = cap.on_roadmap
                    ? '<span class="px-1.5 py-0.5 text-xs rounded bg-accent text-purple-800 ml-1" title="On Roadmap"><i data-lucide="map" class="w-3 h-3 inline"></i></span>'
                    : '';

                return `
                    <tr class="hover:bg-accent">
                        <td class="px-6 py-4 whitespace-nowrap text-sm font-mono text-muted-foreground">${cap.code || '-'}</td>
                        <td class="px-6 py-4">
                            <div class="text-sm font-medium text-foreground">${cap.name}</div>
                            <div class="text-xs text-muted-foreground truncate max-w-xs">${cap.description || ''}</div>
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap">${domainBadge}</td>
                        <td class="px-6 py-4 whitespace-nowrap">${levelBadge}</td>
                        <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">${cap.applications_count}</td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <div class="flex items-center gap-1">
                                ${statusBadge}${roadmapBadge}
                            </div>
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <div class="flex items-center gap-2">
                                <button type="button" data-cmx-action="open-acm-mapping" data-cap-id="${cmxEscapeAttr(cap.id)}" data-cap-name="${cmxEscapeAttr(cap.name || '')}" data-domain="${cmxEscapeAttr(cap.acm_domain)}" data-level="${cmxEscapeAttr(cap.level)}"
                                        class="text-cyan-600 hover:text-cyan-800 text-sm">
                                    Map
                                </button>
                                <button type="button" data-cmx-action="open-acm-detail" data-cap-id="${cmxEscapeAttr(cap.id)}" data-cap-name="${cmxEscapeAttr(cap.name || '')}"
                                        class="text-muted-foreground hover:text-foreground text-sm">
                                    View
                                </button>
                                <button type="button" data-cmx-action="add-to-roadmap" data-cap-id="${cmxEscapeAttr(cap.id)}" data-cap-name="${cmxEscapeAttr(cap.name || '')}" data-cap-level="${cmxEscapeAttr(cap.level_number || 1)}"
                                        class="text-primary hover:text-purple-800 text-sm ${cap.on_roadmap ? 'opacity-50 cursor-not-allowed' : ''}"
                                        ${cap.on_roadmap ? 'disabled title="Already on roadmap"' : 'title="Add to roadmap"'}>
                                    <i data-lucide="map" class="w-4 h-4 inline"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join(''));
        }

        // Pagination functions
        function changeACMPageSize() {
            acmPageSize = parseInt(document.getElementById('acm-page-size').value);
            acmCurrentPage = 1;
            renderACMCapabilitiesPage();
            if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
        }

        function previousACMPage() {
            if (acmCurrentPage > 1) {
                acmCurrentPage--;
                renderACMCapabilitiesPage();
                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            }
        }

        function nextACMPage() {
            const totalPages = Math.ceil(acmFilteredData.length / acmPageSize);
            if (acmCurrentPage < totalPages) {
                acmCurrentPage++;
                renderACMCapabilitiesPage();
                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            }
        }

        function getACMDomainBadge(domain) {
            const colors = {
                'USER-EXPERIENCE': 'bg-accent text-primary/90',
                'APPLICATION-SERVICES': 'bg-accent text-sky-800',
                'DATA-STORAGE': 'bg-accent text-purple-800',
                'SECURITY-IDENTITY': 'bg-destructive/10 text-red-800',
                'DEVOPS-PLATFORM': 'bg-orange-100 text-orange-800',
                'AI-ANALYTICS': 'bg-indigo-100 text-indigo-800',
                'COMMUNICATION': 'bg-teal-100 text-teal-800',
            };
            const colorClass = colors[domain] || 'bg-muted text-foreground';
            const shortName = domain ? domain.split('-').map(w => w[0]).join('') : '?';
            return `<span class="px-2 py-1 text-xs font-medium rounded-full ${colorClass}" title="${domain}">${shortName}</span>`;
        }

        function getACMLevelBadge(level) {
            const colors = {
                'L0': 'bg-card text-primary-foreground',
                'L1': 'bg-cyan-100 text-cyan-800',
                'L2': 'bg-accent text-primary/90',
                'L3': 'bg-accent text-purple-800',
                'L4': 'bg-pink-100 text-pink-800',
            };
            const colorClass = colors[level] || 'bg-muted text-foreground';
            return `<span class="px-2 py-1 text-xs font-medium rounded-full ${colorClass}">${level}</span>`;
        }

        function filterACMByDomain(domain) {
            document.getElementById('acm-domain-filter').value = domain;
            filterACMCapabilities();
        }

        function filterACMCapabilities() {
            const domainFilter = document.getElementById('acm-domain-filter').value;
            const levelFilter = document.getElementById('acm-level-filter').value;
            const statusFilter = document.getElementById('acm-status-filter').value;

            let filtered = acmCapabilitiesData;

            if (domainFilter) {
                filtered = filtered.filter(c => c.acm_domain === domainFilter);
            }
            if (levelFilter) {
                filtered = filtered.filter(c => c.level === levelFilter);
            }
            if (statusFilter) {
                filtered = filtered.filter(c => c.status === statusFilter);
            }

            acmFilteredData = filtered;
            acmCurrentPage = 1;
            renderACMCapabilitiesPage();
            if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
        }

        function openCapabilityDetail(capabilityId, capabilityName) {
            // Show or create the detail panel
            let panel = document.getElementById('capability-detail-panel');
            if (!panel) {
                panel = document.createElement('div');
                panel.id = 'capability-detail-panel';
                panel.className = 'fixed inset-y-0 right-0 w-[420px] bg-card border-l border-border shadow-xl z-50 transform translate-x-full transition-transform duration-200 overflow-y-auto';
                panel.innerHTML = '<div class="p-6"><div class="flex items-center justify-between mb-4"><h3 class="text-lg font-semibold" id="cap-detail-title"></h3><button type="button" data-cmx-action="close-capability-detail" aria-label="Close capability detail" class="text-muted-foreground hover:text-foreground p-1"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button></div><div id="cap-detail-content"><p class="text-sm text-muted-foreground">Loading...</p></div></div>';
                document.body.appendChild(panel);
            }

            document.getElementById('cap-detail-title').textContent = capabilityName;
            document.getElementById('cap-detail-content').innerHTML = '<p class="text-sm text-muted-foreground">Loading...</p>';
            panel.classList.remove('translate-x-full');

            Platform.fetch('/capability-map/api/capability/' + capabilityId + '/applications')
                .then(data => {
                    let html = '';
                    const cap = data.capability || {};

                    // Capability info
                    html += '<div class="mb-4 space-y-1">';
                    if (cap.code) html += '<p class="text-xs text-muted-foreground">' + cap.code + '</p>';
                    html += '<div class="flex items-center gap-2">';
                    html += '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-info/10 text-info-emphasis border border-info/30">Level ' + (cap.level || '?') + '</span>';
                    html += '<span class="text-sm text-muted-foreground">' + (data.mapped_count || 0) + ' of ' + (data.total_count || 0) + ' apps mapped</span>';
                    html += '</div></div>';

                    // Maturity bars if available
                    if (cap.current_maturity !== undefined || cap.target_maturity !== undefined) {
                        html += '<div class="mb-4 p-3 bg-muted/30 rounded-lg space-y-2">';
                        html += '<p class="text-xs font-medium text-muted-foreground uppercase tracking-wider">Maturity</p>';
                        const cur = cap.current_maturity || 0;
                        const tgt = cap.target_maturity || 0;
                        html += '<div class="flex items-center gap-2"><span class="text-xs w-16">Current</span><div class="flex-1 h-2 bg-muted rounded-full"><div class="h-2 bg-blue-500 rounded-full" style="width:' + (cur * 20) + '%"></div></div><span class="text-xs">' + cur + '/5</span></div>';
                        html += '<div class="flex items-center gap-2"><span class="text-xs w-16">Target</span><div class="flex-1 h-2 bg-muted rounded-full"><div class="h-2 bg-green-500 rounded-full" style="width:' + (tgt * 20) + '%"></div></div><span class="text-xs">' + tgt + '/5</span></div>';
                        html += '</div>';
                    }

                    // Linked applications — "Linked Applications" must list only apps
                    // actually mapped to this capability (is_mapped === true). The
                    // endpoint returns every application in the org (mapped and
                    // unmapped) so callers can offer them for mapping; showing that
                    // full list here under a "Linked" heading is what produced the
                    // "0 of 25 apps mapped" / "Linked Applications (25)" contradiction
                    // — filtering here makes both figures come from the same
                    // is_mapped flag, so they can no longer disagree.
                    const apps = (data.applications || []).filter(function(app) { return app.is_mapped; });
                    html += '<div class="mb-2"><p class="text-sm font-medium mb-2">Linked Applications (' + apps.length + ')</p>';
                    if (apps.length === 0) {
                        html += '<p class="text-sm text-muted-foreground py-4 text-center">No applications mapped to this capability.</p>';
                    } else {
                        html += '<div class="space-y-2">';
                        apps.forEach(function(app) {
                            html += '<div class="p-3 rounded-lg border border-border hover:bg-muted/30 transition-colors">';
                            html += '<div class="flex items-center justify-between mb-1">';
                            html += '<a href="/applications/' + app.id + '" class="text-sm font-medium text-foreground hover:text-primary">' + (app.name || 'Unknown') + '</a>';
                            const covPct = app.coverage_percentage || 0;
                            const covColor = covPct >= 70 ? 'green' : covPct >= 40 ? 'amber' : 'red';
                            html += '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-' + covColor + '-500/10 text-' + covColor + '-600 border border-' + covColor + '-500/30">' + covPct + '%</span>';
                            html += '</div>';
                            if (app.support_level) html += '<p class="text-xs text-muted-foreground">Support: ' + app.support_level + '</p>';
                            if (app.domain) html += '<p class="text-xs text-muted-foreground">Domain: ' + app.domain + '</p>';
                            html += '</div>';
                        });
                        html += '</div>';
                    }
                    html += '</div>';

                    document.getElementById('cap-detail-content').innerHTML = html;
                })
                .catch(function(err) {
                    document.getElementById('cap-detail-content').innerHTML = '<p class="text-sm text-destructive">Failed to load details: ' + err.message + '</p>';
                });
        }

        // Alias for backward compatibility
        function openACMCapabilityDetail(capabilityId, capabilityName) {
            openCapabilityDetail(capabilityId, capabilityName);
        }

        function closeCapabilityDetail() {
            let panel = document.getElementById('capability-detail-panel');
            if (panel) panel.classList.add('translate-x-full');
        }

        // =============================================================================
        // ACM Mapping Modal Functions (Consistent with other tabs)
        // =============================================================================

        let acmCurrentCapabilityId = null;
        let acmCurrentCapabilityName = '';
        let acmModalApplicationsData = [];
        let acmSelectedApplications = new Map();

        function openACMMappingModal(capabilityId, capabilityName, domain, level) {
            acmCurrentCapabilityId = String(capabilityId);
            acmCurrentCapabilityName = capabilityName;
            acmSelectedApplications.clear();

            // Update modal title
            const modalNameEl = document.getElementById('acm-modal-capability-name');
            if (modalNameEl) {
                modalNameEl.textContent = capabilityName;
            }

            // Show modal
            const modal = document.getElementById('acm-mapping-modal');
            if (modal) {
                Platform.modal.open('acm-mapping-modal');
                document.body.classList.add('overflow-hidden');
            }

            // Load applications for this technical capability
            loadACMApplicationsForCapability(capabilityId);
        }

        function closeACMMappingModal() {
            const modal = document.getElementById('acm-mapping-modal');
            if (modal) {
                Platform.modal.close('acm-mapping-modal');
                document.body.classList.remove('overflow-hidden');
            }
            acmCurrentCapabilityId = null;
            acmCurrentCapabilityName = '';
            acmSelectedApplications.clear();
            acmModalApplicationsData = [];
        }

        async function loadACMApplicationsForCapability(capabilityId) {
            try {
                const id = String(capabilityId);
                const data = await Platform.fetch(`/capability-map/api/acm/capability/${id}/applications`);
                // Unchecked, a 500 opened the mapping modal with an empty application
                // list — the existing mappings looked as though they had been cleared.

                if (data.error) {
                    showACMNotification('Error loading applications: ' + data.error, 'error');
                    return;
                }

                acmModalApplicationsData = data.applications || [];

                // Pre-select mapped applications
                acmSelectedApplications.clear();
                acmModalApplicationsData.forEach(app => {
                    if (app.is_mapped) {
                        acmSelectedApplications.set(app.id, {
                            application_id: app.id,
                            mapping_id: app.mapping_id,
                            mapping: {
                                capability_coverage: app.capability_coverage || 'partial',
                                maturity_level: app.maturity_level || 'defined',
                                notes: app.notes || ''
                            }
                        });
                    }
                });

                // Reset filters
                const searchInput = document.getElementById('acm-application-search');
                const typeFilter = document.getElementById('acm-filter-type');
                const domainFilter = document.getElementById('acm-filter-domain');
                const statusFilter = document.getElementById('acm-filter-status');
                const sortSelect = document.getElementById('acm-sort-applications');

                if (searchInput) searchInput.value = '';
                if (typeFilter) typeFilter.value = '';
                if (domainFilter) domainFilter.value = '';
                if (statusFilter) statusFilter.value = 'all';
                if (sortSelect) sortSelect.value = 'name-asc';

                // Populate filter options
                populateACMFilterOptions();

                // Render applications
                renderACMApplicationsList();

                // Focus search input
                setTimeout(() => {
                    if (searchInput) searchInput.focus();
                }, 100);
            } catch (error) {
                showACMNotification('Error loading applications', 'error');
            }
        }

        function populateACMFilterOptions() {
            const typeFilter = document.getElementById('acm-filter-type');
            if (typeFilter && acmModalApplicationsData.length > 0) {
                const types = [...new Set(acmModalApplicationsData.map(app => app.type).filter(Boolean))].sort();
                const currentValue = typeFilter.value;
                safeHTML(typeFilter, '<option value="">All Types</option>' +
                    types.map(type => `<option value="${type}">${type}</option>`).join(''));
                if (currentValue) typeFilter.value = currentValue;
            }

            const domainFilter = document.getElementById('acm-filter-domain');
            if (domainFilter && acmModalApplicationsData.length > 0) {
                const domains = [...new Set(acmModalApplicationsData.map(app => app.domain).filter(Boolean))].sort();
                const currentValue = domainFilter.value;
                safeHTML(domainFilter, '<option value="">All Domains</option>' +
                    domains.map(domain => `<option value="${domain}">${domain}</option>`).join(''));
                if (currentValue) domainFilter.value = currentValue;
            }
        }

        function filterACMApplications() {
            renderACMApplicationsList();
        }

        function renderACMApplicationsList() {
            const container = document.getElementById('acm-applications-list');
            const searchTerm = document.getElementById('acm-application-search')?.value.toLowerCase() || '';
            const filterType = document.getElementById('acm-filter-type')?.value || '';
            const filterDomain = document.getElementById('acm-filter-domain')?.value || '';
            const filterStatus = document.getElementById('acm-filter-status')?.value || 'all';
            const sortBy = document.getElementById('acm-sort-applications')?.value || 'name-asc';

            if (!container) return;

            // Filter applications
            let filtered = acmModalApplicationsData.filter(app => {
                const matchesSearch = !searchTerm ||
                    (app.name && app.name.toLowerCase().includes(searchTerm)) ||
                    (app.type && app.type.toLowerCase().includes(searchTerm)) ||
                    (app.domain && app.domain.toLowerCase().includes(searchTerm)) ||
                    (app.description && app.description.toLowerCase().includes(searchTerm));

                const matchesType = !filterType || app.type === filterType;
                const matchesDomain = !filterDomain || app.domain === filterDomain;

                let matchesStatus = true;
                if (filterStatus === 'mapped') {
                    matchesStatus = app.is_mapped === true;
                } else if (filterStatus === 'unmapped') {
                    matchesStatus = app.is_mapped !== true;
                }

                return matchesSearch && matchesType && matchesDomain && matchesStatus;
            });

            // Sort applications
            filtered.sort((a, b) => {
                switch(sortBy) {
                    case 'name-asc': return (a.name || '').localeCompare(b.name || '');
                    case 'name-desc': return (b.name || '').localeCompare(a.name || '');
                    case 'type-asc': return (a.type || '').localeCompare(b.type || '');
                    case 'mapped-first':
                        if (a.is_mapped && !b.is_mapped) return -1;
                        if (!a.is_mapped && b.is_mapped) return 1;
                        return (a.name || '').localeCompare(b.name || '');
                    case 'unmapped-first':
                        if (!a.is_mapped && b.is_mapped) return -1;
                        if (a.is_mapped && !b.is_mapped) return 1;
                        return (a.name || '').localeCompare(b.name || '');
                    default: return 0;
                }
            });

            // Update counts
            const filteredCountEl = document.getElementById('acm-filtered-count');
            const totalCountEl = document.getElementById('acm-total-count');
            const selectedCountEl = document.getElementById('acm-selected-count');
            if (filteredCountEl) filteredCountEl.textContent = filtered.length;
            if (totalCountEl) totalCountEl.textContent = acmModalApplicationsData.length;
            if (selectedCountEl) selectedCountEl.textContent = acmSelectedApplications.size;

            if (filtered.length === 0) {
                safeHTML(container, `
                    <div class="text-center py-12 text-muted-foreground">
                        <i data-lucide="search-x" class="w-12 h-12 mx-auto mb-3 text-muted-foreground"></i>
                        <p class="text-lg font-medium">No applications found</p>
                        <p class="text-sm mt-1">Try adjusting your search or filters</p>
                    </div>
                `);
                if (typeof lucide !== 'undefined') lucide.createIcons();
                return;
            }

            safeHTML(container, filtered.map(app => {
                const isSelected = acmSelectedApplications.has(app.id);
                const mapping = acmSelectedApplications.get(app.id);

                return `
                    <div class="border rounded-lg p-4 transition-all ${isSelected ? 'bg-cyan-50 border-cyan-300 shadow-sm' : 'bg-background border-border hover:border-input hover:shadow-sm'}">
                        <div class="flex items-start justify-between">
                            <div class="flex items-start space-x-3 flex-1">
                                <input
                                    type="checkbox"
                                    ${isSelected ? 'checked' : ''}
                                    onchange="toggleACMApplicationSelection('${app.id}')"
                                    class="mt-1 h-5 w-5 text-cyan-600 focus:ring-cyan-500 border-input rounded cursor-pointer"
                                    title="${isSelected ? 'Deselect' : 'Select'} ${app.name}"
                                />
                                <div class="flex-1">
                                    <div class="flex items-center space-x-2">
                                        <div class="font-medium text-foreground">${app.name || 'Unknown'}</div>
                                        ${app.is_mapped ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-accent text-green-800 rounded-full"><i data-lucide="check-circle" class="w-3 h-3 mr-1"></i>Mapped</span>' : ''}
                                    </div>
                                    <div class="flex items-center space-x-2 mt-1 text-sm text-muted-foreground">
                                        <span class="flex items-center">
                                            <i data-lucide="layers" class="w-3 h-3 mr-1"></i>
                                            ${app.type || 'Unknown'}
                                        </span>
                                        <span>•</span>
                                        <span class="flex items-center">
                                            <i data-lucide="building" class="w-3 h-3 mr-1"></i>
                                            ${app.domain || 'Not specified'}
                                        </span>
                                    </div>
                                    ${app.description ? `<div class="text-xs text-muted-foreground mt-2 line-clamp-2">${app.description}</div>` : ''}
                                </div>
                            </div>
                            ${app.is_mapped && app.mapping_id ? `
                                <button
                                    type="button"
                                    data-cmx-action="delete-acm-mapping"
                                    data-mapping-id="${cmxEscapeAttr(app.mapping_id)}"
                                    data-app-id="${cmxEscapeAttr(app.id)}"
                                    class="ml-2 px-3 py-1.5 text-xs bg-destructive text-primary-foreground rounded hover:bg-destructive transition-colors flex items-center space-x-1"
                                    title="Remove mapping"
                                >
                                    <i data-lucide="trash-2" class="w-3 h-3"></i>
                                    <span>Remove</span>
                                </button>
                            ` : ''}
                        </div>
                        ${isSelected ? renderACMApplicationSettings(app.id, mapping) : ''}
                    </div>
                `;
            }).join(''));

            if (typeof lucide !== 'undefined') lucide.createIcons();
        }

        function renderACMApplicationSettings(appId, mapping) {
            const mappingData = mapping?.mapping || {};
            return `
                <div class="mt-4 pt-4 border-t border-border space-y-3">
                    <div class="grid grid-cols-3 gap-4">
                        <div>
                            <label class="block text-xs font-medium text-muted-foreground mb-1">Coverage Level</label>
                            <select
                                class="w-full text-sm border border-input rounded px-2 py-1"
                                onchange="updateACMApplicationMapping('${appId}', 'capability_coverage', this.value)"
                            >
                                <option value="full" ${mappingData.capability_coverage === 'full' ? 'selected' : ''}>Full</option>
                                <option value="partial" ${mappingData.capability_coverage === 'partial' || !mappingData.capability_coverage ? 'selected' : ''}>Partial</option>
                                <option value="minimal" ${mappingData.capability_coverage === 'minimal' ? 'selected' : ''}>Minimal</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-muted-foreground mb-1">Maturity Level</label>
                            <select
                                class="w-full text-sm border border-input rounded px-2 py-1"
                                onchange="updateACMApplicationMapping('${appId}', 'maturity_level', this.value)"
                            >
                                <option value="initial" ${mappingData.maturity_level === 'initial' ? 'selected' : ''}>Initial</option>
                                <option value="developing" ${mappingData.maturity_level === 'developing' ? 'selected' : ''}>Developing</option>
                                <option value="defined" ${mappingData.maturity_level === 'defined' || !mappingData.maturity_level ? 'selected' : ''}>Defined</option>
                                <option value="managed" ${mappingData.maturity_level === 'managed' ? 'selected' : ''}>Managed</option>
                                <option value="optimized" ${mappingData.maturity_level === 'optimized' ? 'selected' : ''}>Optimized</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-muted-foreground mb-1">Notes</label>
                            <input
                                type="text"
                                value="${mappingData.notes || ''}"
                                placeholder="Optional notes..."
                                class="w-full text-sm border border-input rounded px-2 py-1"
                                onchange="updateACMApplicationMapping('${appId}', 'notes', this.value)"
                            />
                        </div>
                    </div>
                </div>
            `;
        }

        function toggleACMApplicationSelection(appId) {
            if (acmSelectedApplications.has(appId)) {
                acmSelectedApplications.delete(appId);
            } else {
                acmSelectedApplications.set(appId, {
                    application_id: appId,
                    mapping: {
                        capability_coverage: 'partial',
                        maturity_level: 'defined',
                        notes: ''
                    }
                });
            }
            renderACMApplicationsList();
        }

        function updateACMApplicationMapping(appId, field, value) {
            const existing = acmSelectedApplications.get(appId);
            if (existing) {
                if (!existing.mapping) existing.mapping = {};
                existing.mapping[field] = value;
                acmSelectedApplications.set(appId, existing);
            }
        }

        function selectAllACMFiltered() {
            const container = document.getElementById('acm-applications-list');
            if (!container) return;

            const checkboxes = container.querySelectorAll('input[type="checkbox"]:not(:checked)');
            checkboxes.forEach(checkbox => {
                const match = checkbox.getAttribute('onchange')?.match(/toggleACMApplicationSelection\('([^']+)'\)/);
                if (match) {
                    const appId = match[1];
                    if (!acmSelectedApplications.has(appId)) {
                        toggleACMApplicationSelection(appId);
                    }
                }
            });
        }

        function deselectAllACM() {
            acmSelectedApplications.clear();
            renderACMApplicationsList();
        }

        async function deleteACMMapping(mappingId, appId) {
            const modalId = window.modalManager.createModal({
                title: 'Remove Mapping',
                content: '<p class="text-sm text-muted-foreground">Remove this application mapping?</p>',
                size: 'small',
                buttons: [
                    { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                    { text: 'Remove', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'remove', handler: async function() { await _doDeleteACMMapping(mappingId, appId); } }
                ]
            });
            window.modalManager.open(modalId);
        }

        async function _doDeleteACMMapping(mappingId, appId) {
            try {
                const data = await Platform.fetch.delete(`/capability-map/api/acm/mapping/${mappingId}`);
                if (data.success) {
                    // Update local state
                    acmSelectedApplications.delete(appId);
                    const appIndex = acmModalApplicationsData.findIndex(a => a.id == appId);
                    if (appIndex >= 0) {
                        acmModalApplicationsData[appIndex].is_mapped = false;
                        acmModalApplicationsData[appIndex].mapping_id = null;
                    }
                    renderACMApplicationsList();
                    showACMNotification('Mapping removed successfully', 'success');
                } else {
                    showACMNotification('Error: ' + (data.error || 'Unknown error'), 'error');
                }
            } catch (error) {
                showACMNotification('Network error', 'error');
            }
        }

        async function saveACMMappings() {
            if (acmSelectedApplications.size === 0) {
                showACMNotification('No applications selected', 'error');
                return;
            }

            try {
                const mappings = Array.from(acmSelectedApplications.entries()).map(([appId, data]) => ({
                    application_id: parseInt(appId),
                    technical_capability_id: parseInt(acmCurrentCapabilityId),
                    capability_coverage: data.mapping?.capability_coverage || 'partial',
                    maturity_level: data.mapping?.maturity_level || 'defined',
                    notes: data.mapping?.notes || ''
                }));

                const data = await Platform.fetch.post('/capability-map/api/acm/mappings/bulk', { mappings });
                if (data.success) {
                    closeACMMappingModal();
                    showACMNotification(`Successfully saved ${data.created || 0} new and updated ${data.updated || 0} mappings`, 'success');
                    loadTechnicalTab();
                } else {
                    showACMNotification('Error: ' + (data.error || 'Unknown error'), 'error');
                }
            } catch (error) {
                showACMNotification('Network error. Please try again.', 'error');
            }
        }

        function showACMNotification(message, type = 'info') {
            const toast = document.createElement('div');
            toast.className = `fixed bottom-4 right-4 z-50 px-6 py-3 rounded-lg shadow-lg text-primary-foreground ${type === 'success' ? 'bg-primary' : type === 'error' ? 'bg-destructive' : 'bg-cyan-600'}`;
            toast.textContent = message;
            document.body.appendChild(toast);

            setTimeout(() => {
                toast.remove();
            }, 3000);
        }

        // =============================================================================
        // Process Mapping Modal Functions (99.99% copy from ACM)
        // =============================================================================

        let processCurrentProcessId = null;
        let processCurrentProcessName = '';
        let processModalApplicationsData = [];
        let processSelectedApplications = new Map();

        function openProcessMappingModal(processId, processName, processCode, processType) {
            // Check if user is authenticated before opening modal
            Platform.fetch('/capability-map/api/check-auth')
                .then(data => {
                    if (data.authenticated) {
                        // User is authenticated, proceed with modal
                        processCurrentProcessId = String(processId);
                        processCurrentProcessName = processName;
                        processSelectedApplications.clear();

                        // Update modal title
                        const modalNameEl = document.getElementById('process-modal-process-name');
                        if (modalNameEl) {
                            modalNameEl.textContent = processName;
                        }

                        // Show modal
                        const modal = document.getElementById('process-mapping-modal');
                        if (modal) {
                            Platform.modal.open('process-mapping-modal');
                            document.body.classList.add('overflow-hidden');
                        }

                        // Load applications for this process
                        loadProcessApplicationsForProcess(processId);
                    } else {
                        // FAR-001: Show login prompt without auto-redirect (was causing page to navigate away)
                        showProcessNotification('Please log in to map applications to processes', 'error');
                    }
                })
                .catch(error => {
                    showProcessNotification('Error checking authentication status', 'error');
                });
        }

        function closeProcessMappingModal() {
            const modal = document.getElementById('process-mapping-modal');
            if (modal) {
                Platform.modal.close('process-mapping-modal');
                document.body.classList.remove('overflow-hidden');
            }
            processCurrentProcessId = null;
            processCurrentProcessName = '';
            processSelectedApplications.clear();
            processModalApplicationsData = [];
        }

        async function loadProcessApplicationsForProcess(processId) {
            try {
                const id = String(processId);
                const data = await Platform.fetch(`/capability-map/api/process-gaps/process/${id}/applications`);
                // Unchecked, a 500 opened the mapping modal with an empty application
                // list — the existing mappings looked as though they had been cleared.

                if (data.error) {
                    showProcessNotification('Error loading applications: ' + data.error, 'error');
                    return;
                }

                processModalApplicationsData = data.applications || [];

                // Pre-select mapped applications
                processSelectedApplications.clear();
                processModalApplicationsData.forEach(app => {
                    if (app.is_mapped) {
                        processSelectedApplications.set(app.id, {
                            application_id: app.id,
                            mapping_id: app.mapping_id,
                            mapping: {
                                support_level: app.support_level || 'partial',
                                automation_level: app.automation_level || 1,
                                notes: app.notes || ''
                            }
                        });
                    }
                });

                // Reset filters
                const searchInput = document.getElementById('process-application-search');
                const typeFilter = document.getElementById('process-filter-type');
                const domainFilter = document.getElementById('process-filter-domain');
                const statusFilter = document.getElementById('process-filter-status');
                const sortSelect = document.getElementById('process-sort-applications');

                if (searchInput) searchInput.value = '';
                if (typeFilter) typeFilter.value = '';
                if (domainFilter) domainFilter.value = '';
                if (statusFilter) statusFilter.value = 'all';
                if (sortSelect) sortSelect.value = 'name-asc';

                // Populate filter options
                populateProcessFilterOptions();

                // Render applications
                renderProcessApplicationsList();

                // Focus search input
                setTimeout(() => {
                    if (searchInput) searchInput.focus();
                }, 100);
            } catch (error) {
                showProcessNotification('Error loading applications', 'error');
            }
        }

        function populateProcessFilterOptions() {
            const typeFilter = document.getElementById('process-filter-type');
            if (typeFilter && processModalApplicationsData.length > 0) {
                const types = [...new Set(processModalApplicationsData.map(app => app.type).filter(Boolean))].sort();
                safeHTML(typeFilter, '<option value="">All Types</option>' +
                    types.map(type => `<option value="${type}">${type}</option>`).join(''));
            }

            const domainFilter = document.getElementById('process-filter-domain');
            if (domainFilter && processModalApplicationsData.length > 0) {
                const domains = [...new Set(processModalApplicationsData.map(app => app.domain).filter(Boolean))].sort();
                safeHTML(domainFilter, '<option value="">All Domains</option>' +
                    domains.map(domain => `<option value="${domain}">${domain}</option>`).join(''));
            }
        }

        function renderProcessApplicationsList() {
            const container = document.getElementById('process-applications-list');
            if (!container) return;

            const filteredData = filterProcessApplicationsData();
            const sortedData = sortProcessApplicationsData(filteredData);

            safeHTML(container, sortedData.map(app => renderProcessApplicationItem(app)).join(''));
            updateProcessSelectionCount();

            if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
        }

        function renderProcessApplicationItem(app) {
            const isSelected = processSelectedApplications.has(app.id);
            const mapping = processSelectedApplications.get(app.id);

            return `
                <div class="border rounded-lg p-4 ${isSelected ? 'border-primary bg-muted' : 'border-border'}">
                    <div class="flex items-start justify-between">
                        <div class="flex items-start space-x-3 flex-1">
                            <input type="checkbox"
                                   id="process-app-${app.id}"
                                   ${isSelected ? 'checked' : ''}
                                   onchange="toggleProcessApplicationSelection(${app.id})"
                                   class="mt-1 rounded border-input text-primary focus-visible:ring-ring">
                            <div class="flex-1">
                                <label for="process-app-${app.id}" class="font-medium text-foreground cursor-pointer">
                                    ${app.name}
                                </label>
                                <div class="text-sm text-muted-foreground mt-1">
                                    ${app.type} ${app.domain ? `• ${app.domain}` : ''}
                                </div>
                                ${app.description ? `<div class="text-sm text-muted-foreground mt-2">${app.description}</div>` : ''}
                            </div>
                        </div>
                        <div class="text-right">
                            <span class="px-2 py-1 text-xs rounded-full ${app.status === 'active' ? 'bg-accent text-green-800' : 'bg-muted text-foreground'}">
                                ${app.status}
                            </span>
                        </div>
                    </div>

                    ${isSelected ? `
                        <div class="mt-4 pt-4 border-t border-border">
                            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div>
                                    <label class="block text-sm font-medium text-muted-foreground mb-1">Support Level</label>
                                    <select id="process-support-${app.id}" class="w-full px-3 py-2 text-sm border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                                        <option value="full" ${mapping?.mapping?.support_level === 'full' ? 'selected' : ''}>Full Support</option>
                                        <option value="partial" ${mapping?.mapping?.support_level === 'partial' ? 'selected' : ''}>Partial Support</option>
                                        <option value="minimal" ${mapping?.mapping?.support_level === 'minimal' ? 'selected' : ''}>Minimal Support</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-sm font-medium text-muted-foreground mb-1">Automation Level</label>
                                    <select id="process-automation-${app.id}" class="w-full px-3 py-2 text-sm border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                                        <option value="1" ${mapping?.mapping?.automation_level === 1 ? 'selected' : ''}>Manual</option>
                                        <option value="2" ${mapping?.mapping?.automation_level === 2 ? 'selected' : ''}>Basic Automation</option>
                                        <option value="3" ${mapping?.mapping?.automation_level === 3 ? 'selected' : ''}>Moderate Automation</option>
                                        <option value="4" ${mapping?.mapping?.automation_level === 4 ? 'selected' : ''}>High Automation</option>
                                        <option value="5" ${mapping?.mapping?.automation_level === 5 ? 'selected' : ''}>Full Automation</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-sm font-medium text-muted-foreground mb-1">Notes</label>
                                    <input type="text" id="process-notes-${app.id}" value="${mapping?.mapping?.notes || ''}"
                                           placeholder="Add notes..."
                                           class="w-full px-3 py-2 text-sm border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                                </div>
                            </div>
                        </div>
                    ` : ''}
                </div>
            `;
        }

        function toggleProcessApplicationSelection(applicationId) {
            if (processSelectedApplications.has(applicationId)) {
                processSelectedApplications.delete(applicationId);
            } else {
                processSelectedApplications.set(applicationId, {
                    application_id: applicationId,
                    mapping_id: null,
                    mapping: {
                        support_level: 'partial',
                        automation_level: 1,
                        notes: ''
                    }
                });
            }
            renderProcessApplicationsList();
        }

        function filterProcessApplicationsData() {
            const searchTerm = document.getElementById('process-application-search')?.value.toLowerCase() || '';
            const typeFilter = document.getElementById('process-filter-type')?.value || '';
            const domainFilter = document.getElementById('process-filter-domain')?.value || '';
            const statusFilter = document.getElementById('process-filter-status')?.value || '';

            return processModalApplicationsData.filter(app => {
                const matchesSearch = !searchTerm ||
                    app.name.toLowerCase().includes(searchTerm) ||
                    (app.type && app.type.toLowerCase().includes(searchTerm)) ||
                    (app.domain && app.domain.toLowerCase().includes(searchTerm));

                const matchesType = !typeFilter || app.type === typeFilter;
                const matchesDomain = !domainFilter || app.domain === domainFilter;

                const matchesStatus = statusFilter === 'all' ||
                    (statusFilter === 'mapped' && processSelectedApplications.has(app.id)) ||
                    (statusFilter === 'unmapped' && !processSelectedApplications.has(app.id));

                return matchesSearch && matchesType && matchesDomain && matchesStatus;
            });
        }

        function sortProcessApplicationsData(data) {
            const sortValue = document.getElementById('process-sort-applications')?.value || 'name-asc';

            return [...data].sort((a, b) => {
                switch (sortValue) {
                    case 'name-asc':
                        return a.name.localeCompare(b.name);
                    case 'name-desc':
                        return b.name.localeCompare(a.name);
                    case 'type-asc':
                        return (a.type || '').localeCompare(b.type || '');
                    case 'mapped-first':
                        return (processSelectedApplications.has(b.id) ? 1 : 0) - (processSelectedApplications.has(a.id) ? 1 : 0);
                    case 'unmapped-first':
                        return (processSelectedApplications.has(a.id) ? 1 : 0) - (processSelectedApplications.has(b.id) ? 1 : 0);
                    default:
                        return 0;
                }
            });
        }

        function filterProcessApplications() {
            renderProcessApplicationsList();
        }

        function selectAllProcessFiltered() {
            const filteredData = filterProcessApplicationsData();
            filteredData.forEach(app => {
                if (!processSelectedApplications.has(app.id)) {
                    processSelectedApplications.set(app.id, {
                        application_id: app.id,
                        mapping_id: null,
                        mapping: {
                            support_level: 'partial',
                            automation_level: 1,
                            notes: ''
                        }
                    });
                }
            });
            renderProcessApplicationsList();
        }

        function deselectAllProcess() {
            processSelectedApplications.clear();
            renderProcessApplicationsList();
        }

        function updateProcessSelectionCount() {
            const selectedCountEl = document.getElementById('process-selected-count');
            const filteredCountEl = document.getElementById('process-filtered-count');
            const totalCountEl = document.getElementById('process-total-count');

            if (selectedCountEl) selectedCountEl.textContent = processSelectedApplications.size;
            if (filteredCountEl) filteredCountEl.textContent = filterProcessApplicationsData().length;
            if (totalCountEl) totalCountEl.textContent = processModalApplicationsData.length;
        }

        async function saveProcessMappings() {
            try {
                // Check authentication before saving
                const authData = await Platform.fetch('/capability-map/api/check-auth');
                // Unchecked, a 500 made `authenticated` undefined and the save aborted
                // with "Please log in" at a user who was logged in the whole time.

                if (!authData.authenticated) {
                    // FAR-001: Show login prompt without auto-redirect (was causing page to navigate away)
                    showProcessNotification('Please log in to save mappings', 'error');
                    return;
                }

                const mappings = [];

                processSelectedApplications.forEach((mapping, applicationId) => {
                    const supportLevel = document.getElementById(`process-support-${applicationId}`)?.value || 'partial';
                    const automationLevel = document.getElementById(`process-automation-${applicationId}`)?.value || 1;
                    const notes = document.getElementById(`process-notes-${applicationId}`)?.value || '';

                    mappings.push({
                        application_id: applicationId,
                        apqc_process_id: processCurrentProcessId,
                        support_level: supportLevel,
                        automation_level: parseInt(automationLevel),
                        notes: notes,
                        mapping_id: mapping.mapping_id
                    });
                });

                const result = await Platform.fetch.post('/capability-map/api/process-gaps/mappings/bulk', { mappings });
                if (result.success) {
                    showProcessNotification(`Successfully saved ${mappings.length} mappings`, 'success');
                    closeProcessMappingModal();
                    // Refresh process gap data
                    loadProcessGapData();
                } else {
                    showProcessNotification('Error saving mappings: ' + (result.error || 'Unknown error'), 'error');
                }
            } catch (error) {
                showProcessNotification('Mappings were NOT saved — ' + ((error && error.message) || 'request failed'), 'error');
            }
        }

        function showProcessNotification(message, type = 'info') {
            // Simple notification - could be enhanced with a toast library
            const notification = document.createElement('div');
            notification.className = `fixed top-4 right-4 p-4 rounded-md shadow-lg z-50 ${
                type === 'success' ? 'bg-primary text-primary-foreground' :
                type === 'error' ? 'bg-destructive text-primary-foreground' :
                'bg-primary text-primary-foreground'
            }`;
            notification.textContent = message;

            document.body.appendChild(notification);

            setTimeout(() => {
                if (notification.parentNode) {
                    notification.parentNode.removeChild(notification);
                }
            }, 3000);
        }

        // =============================================================================
        // Business Domain Cards Functions (Unified Tab)
        // =============================================================================

        async function loadBusinessDomainCards() {
            try {
                const data = await Platform.fetch('/capability-map/api/unified/domains');

                if (data.success) {
                    // Update statistics
                    document.getElementById('unified-domain-count').textContent = data.domain_count || 0;
                    document.getElementById('unified-cap-count').textContent = data.total_capabilities || 0;
                    document.getElementById('unified-mapped-count').textContent = data.mapped_count || 0;
                    document.getElementById('unified-coverage').textContent = (data.coverage || 0) + '%';

                    // Render domain cards
                    renderBusinessDomainCards(data.domains || []);
                }

                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            } catch (error) {
                if (window.Platform && Platform.toast) Platform.toast.error('Failed to load business domains');
            }
        }

        function renderBusinessDomainCards(domains) {
            const grid = document.getElementById('unified-domains-grid');
            if (!domains || domains.length === 0) {
                safeHTML(grid, '<div class="col-span-full text-center py-8 text-muted-foreground">No business domains configured.</div>');
                return;
            }

            // Full class strings to avoid dynamic class construction (breaks Tailwind purge)
            const domainColorSets = [
                { bg: 'bg-muted', border: 'border-primary/20', textBold: 'text-primary', textHead: 'text-primary/90', badge: 'bg-accent' },
                { bg: 'bg-sky-50', border: 'border-sky-200', textBold: 'text-sky-600', textHead: 'text-sky-800', badge: 'bg-accent' },
                { bg: 'bg-muted', border: 'border-purple-200', textBold: 'text-primary', textHead: 'text-purple-800', badge: 'bg-accent' },
                { bg: 'bg-orange-50', border: 'border-orange-200', textBold: 'text-orange-600', textHead: 'text-orange-800', badge: 'bg-orange-100' },
                { bg: 'bg-teal-50', border: 'border-teal-200', textBold: 'text-teal-600', textHead: 'text-teal-800', badge: 'bg-teal-100' },
                { bg: 'bg-indigo-50', border: 'border-indigo-200', textBold: 'text-primary', textHead: 'text-indigo-800', badge: 'bg-indigo-100' },
                { bg: 'bg-pink-50', border: 'border-pink-200', textBold: 'text-pink-600', textHead: 'text-pink-800', badge: 'bg-pink-100' },
                { bg: 'bg-cyan-50', border: 'border-cyan-200', textBold: 'text-cyan-600', textHead: 'text-cyan-800', badge: 'bg-cyan-100' },
                { bg: 'bg-amber-50', border: 'border-amber-200', textBold: 'text-amber-600', textHead: 'text-amber-800', badge: 'bg-amber-100' },
            ];

            safeHTML(grid, domains.map((domain, index) => {
                const cs = domainColorSets[index % domainColorSets.length];
                const coverageColor = domain.coverage >= 70 ? 'text-sky-600' : domain.coverage >= 40 ? 'text-amber-600' : 'text-rose-600';
                const barColor = domain.coverage >= 70 ? 'bg-primary' : domain.coverage >= 40 ? 'bg-amber-500' : 'bg-rose-500';
                const statusColor = domain.coverage >= 70 ? 'bg-sky-400' : domain.coverage >= 40 ? 'bg-amber-400' : 'bg-rose-400';

                return `
                    <div class="${cs.bg} border-2 ${cs.border} rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"
                         role="button" tabindex="0"
                         data-cmx-action="filter-unified-domain" data-domain-code="${cmxEscapeAttr(domain.code)}"
                         title="Filter capabilities to domain ${cmxEscapeAttr(domain.code)}">
                        <div class="flex items-start justify-between mb-3">
                            <span class="text-xs font-bold ${cs.textBold} ${cs.badge} px-2 py-0.5 rounded">${domain.code}</span>
                            <div class="w-2 h-2 rounded-full ${statusColor}"></div>
                        </div>
                        <h4 class="text-sm font-semibold ${cs.textHead} mb-1">${domain.name}</h4>
                        <p class="text-xs text-muted-foreground mb-3 line-clamp-2">${domain.description || ''}</p>
                        <div class="flex justify-between text-xs text-muted-foreground">
                            <span>${domain.capability_count || 0} capabilities</span>
                            <span class="font-medium ${coverageColor}">${domain.coverage || 0}%</span>
                        </div>
                        <div class="mt-2 w-full bg-border rounded-full h-1.5">
                            <div class="${barColor} h-1.5 rounded-full transition-all" style="width: ${domain.coverage || 0}%"></div>
                        </div>
                        <div class="mt-2 text-xs text-muted-foreground">
                            L1: ${domain.l1_count || 0} | L2: ${domain.l2_count || 0} | L3: ${domain.l3_count || 0}
                        </div>
                    </div>
                `;
            }).join(''));
        }

        // =============================================================================
        // Manufacturing Domain Stats Functions
        // =============================================================================

        async function loadManufacturingDomainStats() {
            try {
                const data = await Platform.fetch('/capability-map/api/manufacturing/domains');

                if (data.success) {
                    // Update statistics
                    document.getElementById('mfg-cap-count').textContent = data.total_capabilities || 0;
                    document.getElementById('mfg-mapped-count').textContent = data.mapped_count || 0;
                    document.getElementById('mfg-coverage').textContent = (data.coverage || 0) + '%';
                    document.getElementById('mfg-avg-oee').textContent = (data.avg_oee || 0) + '%';

                    // Update domain cards
                    const domains = data.domains || {};
                    updateMfgDomainCard('prod', domains.production);
                    updateMfgDomainCard('sc', domains.supply_chain);
                    updateMfgDomainCard('qual', domains.quality);
                    updateMfgDomainCard('maint', domains.maintenance);
                    updateMfgDomainCard('eng', domains.engineering);
                }

                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            } catch (error) {
                if (window.Platform && Platform.toast) Platform.toast.error('Failed to load manufacturing domains');
            }
        }

        function updateMfgDomainCard(prefix, domainData) {
            if (!domainData) domainData = { count: 0, coverage: 0 };
            const countEl = document.getElementById(`mfg-${prefix}-count`);
            const pctEl = document.getElementById(`mfg-${prefix}-pct`);
            const barEl = document.getElementById(`mfg-${prefix}-bar`);
            const statusEl = document.getElementById(`mfg-${prefix}-status`);

            if (countEl) countEl.textContent = `${domainData.count || 0} capabilities`;
            if (pctEl) pctEl.textContent = `${domainData.coverage || 0}%`;
            if (barEl) barEl.style.width = `${domainData.coverage || 0}%`;
            if (statusEl) {
                statusEl.className = `w-3 h-3 rounded-full ${domainData.coverage >= 70 ? 'bg-sky-400' : domainData.coverage >= 40 ? 'bg-amber-400' : 'bg-muted/70'}`;
            }
        }

        // =============================================================================
        // Process Category Stats Functions
        // =============================================================================

        async function loadProcessCategoryStats() {
            try {
                const data = await Platform.fetch('/capability-map/api/process/categories');

                if (data.success) {
                    const categories = data.categories || {};
                    for (let i = 1; i <= 13; i++) {
                        const cat = categories[`${i}.0`] || { count: 0, coverage: 0 };
                        const countEl = document.getElementById(`proc-cat-${i}-count`);
                        const pctEl = document.getElementById(`proc-cat-${i}-pct`);
                        const statusEl = document.getElementById(`proc-cat-${i}-status`);

                        if (countEl) countEl.textContent = `${cat.count || 0}`;
                        if (pctEl) pctEl.textContent = `${cat.coverage || 0}%`;
                        if (statusEl) {
                            statusEl.className = `w-2 h-2 rounded-full ${cat.coverage >= 70 ? 'bg-sky-400' : cat.coverage >= 40 ? 'bg-amber-400' : 'bg-muted/70'}`;
                        }
                    }
                }

                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            } catch (error) {
                if (window.Platform && Platform.toast) Platform.toast.error('Failed to load process categories');
            }
        }

        function filterProcessByCategory(category) {
            // Set a hidden filter or search by category prefix
            document.getElementById('process-search-filter').value = category;
            filterProcessGapTable();
        }

        // =============================================================================
        // ACM Gap Analysis Functions (within Gap Analysis Tab)
        // =============================================================================

        let acmGapData = [];
        let acmGapDomainStats = [];

        async function loadACMGapAnalysis() {
            try {
                const data = await Platform.fetch('/capability-map/api/acm/gap-analysis');

                if (data.success) {
                    acmGapData = data.capabilities || [];
                    acmGapDomainStats = data.domain_gaps || [];

                    // Update statistics
                    const stats = data.statistics || {};
                    document.getElementById('acm-gap-total').textContent = stats.total || 0;
                    document.getElementById('acm-gap-unmapped').textContent = stats.unmapped || 0;
                    document.getElementById('acm-gap-partial').textContent = stats.partial || 0;
                    document.getElementById('acm-gap-coverage-pct').textContent = (stats.coverage_rate || 0).toFixed(1) + '%';

                    // Render domain coverage grid
                    renderACMGapDomainCoverage(acmGapDomainStats);

                    // Render gap table
                    renderACMGapTable(acmGapData);
                }

                if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
            } catch (error) {
                safeHTML(document.getElementById('acm-gap-table-body'), `
                    <tr>
                        <td colspan="6" class="px-6 py-8 text-center text-destructive">
                            <i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>
                            <p>Error loading ACM gap analysis. Please try again.</p>
                        </td>
                    </tr>
                `);
            }
        }

        function renderACMGapDomainCoverage(domainStats) {
            const grid = document.getElementById('acm-domain-coverage-grid');

            const domainColors = {
                'USER-EXPERIENCE': { bg: 'bg-accent', text: 'text-primary', short: 'UX' },
                'APPLICATION-SERVICES': { bg: 'bg-accent', text: 'text-sky-700', short: 'APP' },
                'DATA-STORAGE': { bg: 'bg-accent', text: 'text-purple-700', short: 'DATA' },
                'SECURITY-IDENTITY': { bg: 'bg-destructive/10', text: 'text-destructive', short: 'SEC' },
                'DEVOPS-PLATFORM': { bg: 'bg-orange-100', text: 'text-orange-700', short: 'OPS' },
                'AI-ANALYTICS': { bg: 'bg-indigo-100', text: 'text-indigo-700', short: 'AI' },
                'COMMUNICATION': { bg: 'bg-teal-100', text: 'text-teal-700', short: 'COM' },
            };

            if (!domainStats || domainStats.length === 0) {
                safeHTML(grid, '<div class="text-center text-muted-foreground col-span-7">No domain coverage data available.</div>');
                return;
            }

            safeHTML(grid, domainStats.map(domain => {
                const colors = domainColors[domain.domain] || { bg: 'bg-muted', text: 'text-muted-foreground', short: '?' };
                const coverageColor = domain.coverage >= 70 ? 'text-sky-600' : domain.coverage >= 40 ? 'text-amber-600' : 'text-rose-600';
                const barColor = domain.coverage >= 70 ? 'bg-primary' : domain.coverage >= 40 ? 'bg-amber-500' : 'bg-rose-500';

                return `
                    <div class="${colors.bg} rounded-lg p-3 text-center cursor-pointer hover:shadow-md transition-shadow"
                         role="button" tabindex="0"
                         data-cmx-action="filter-acm-gap-domain" data-domain="${cmxEscapeAttr(domain.domain)}"
                         aria-label="Filter gaps to domain ${cmxEscapeAttr(domain.domain)}">
                        <div class="font-semibold ${colors.text} text-sm mb-1">${colors.short}</div>
                        <div class="${coverageColor} font-bold text-lg">${domain.coverage}%</div>
                        <div class="w-full bg-border rounded-full h-1 mt-2">
                            <div class="${barColor} h-1 rounded-full" style="width: ${domain.coverage}%"></div>
                        </div>
                        <div class="text-xs text-muted-foreground mt-1">${domain.total} caps</div>
                        <div class="text-xs ${domain.unmapped > 0 ? 'text-rose-500' : 'text-sky-500'}">${domain.unmapped} gaps</div>
                    </div>
                `;
            }).join(''));
        }

        function renderACMGapTable(capabilities) {
            const tbody = document.getElementById('acm-gap-table-body');
            const showingEl = document.getElementById('acm-gap-showing');

            if (!capabilities || capabilities.length === 0) {
                safeHTML(tbody, `
                    <tr>
                        <td colspan="6" class="px-6 py-12 text-center text-muted-foreground">
                            <i data-lucide="check-circle" class="w-8 h-8 mx-auto mb-2 text-emerald-500"></i>
                            <p>No gaps found matching current filters.</p>
                        </td>
                    </tr>
                `);
                showingEl.textContent = '0';
                return;
            }

            showingEl.textContent = capabilities.length;

            safeHTML(tbody, capabilities.map(cap => {
                const domainBadge = getACMDomainBadge(cap.acm_domain);
                const levelBadge = getACMLevelBadge(cap.level);

                let statusBadge;
                if (cap.applications_count === 0) {
                    statusBadge = '<span class="px-2 py-1 text-xs font-medium rounded-full bg-destructive/10 text-red-800">Gap</span>';
                } else if (cap.applications_count < 3) {
                    statusBadge = '<span class="px-2 py-1 text-xs font-medium rounded-full bg-amber-100 text-amber-800">Partial</span>';
                } else {
                    statusBadge = '<span class="px-2 py-1 text-xs font-medium rounded-full bg-accent text-green-800">Covered</span>';
                }

                return `
                    <tr class="hover:bg-accent">
                        <td class="px-6 py-4">
                            <div class="flex items-start">
                                <div>
                                    <div class="text-sm font-medium text-foreground">${cap.name}</div>
                                    <div class="text-xs text-muted-foreground font-mono">${cap.code || ''}</div>
                                </div>
                            </div>
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap">${domainBadge}</td>
                        <td class="px-6 py-4 whitespace-nowrap">${levelBadge}</td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <span class="text-sm ${cap.applications_count > 0 ? 'text-foreground' : 'text-destructive font-medium'}">${cap.applications_count}</span>
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap">
                            <span class="text-sm ${cap.vendors_count > 0 ? 'text-foreground' : 'text-muted-foreground'}">${cap.vendors_count || 0}</span>
                        </td>
                        <td class="px-6 py-4 whitespace-nowrap">${statusBadge}</td>
                    </tr>
                `;
            }).join(''));
        }

        function filterACMGapTable() {
            const domainFilter = document.getElementById('acm-gap-domain-filter').value;
            const levelFilter = document.getElementById('acm-gap-level-filter').value;
            const statusFilter = document.getElementById('acm-gap-status-filter').value;
            const searchFilter = document.getElementById('acm-gap-search-filter').value.toLowerCase();

            let filtered = acmGapData;

            if (domainFilter) {
                filtered = filtered.filter(c => c.acm_domain === domainFilter);
            }
            if (levelFilter) {
                filtered = filtered.filter(c => c.level === levelFilter);
            }
            if (statusFilter) {
                if (statusFilter === 'gap') {
                    filtered = filtered.filter(c => c.applications_count === 0);
                } else if (statusFilter === 'partial') {
                    filtered = filtered.filter(c => c.applications_count > 0 && c.applications_count < 3);
                } else if (statusFilter === 'covered') {
                    filtered = filtered.filter(c => c.applications_count >= 3);
                }
            }
            if (searchFilter) {
                filtered = filtered.filter(c =>
                    (c.name && c.name.toLowerCase().includes(searchFilter)) ||
                    (c.code && c.code.toLowerCase().includes(searchFilter))
                );
            }

            renderACMGapTable(filtered);
            if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
        }

        function clearACMGapFilters() {
            document.getElementById('acm-gap-domain-filter').value = '';
            document.getElementById('acm-gap-level-filter').value = '';
            document.getElementById('acm-gap-status-filter').value = '';
            document.getElementById('acm-gap-search-filter').value = '';
            renderACMGapTable(acmGapData);
            if (typeof lucide !== 'undefined') setTimeout(() => lucide.createIcons(), 100);
        }


// ============================================================================
// Delegated action dispatcher
// ============================================================================
//
// The app ships script-src 'self' 'nonce-...' 'strict-dynamic' with neither
// 'unsafe-inline' nor 'unsafe-hashes', so an on*= attribute never executes --
// innerHTML-injected ones included. The controls above are now data-driven and
// dispatched by one document-level listener; delegation is required because
// these grids are re-rendered wholesale from fetched data.
//
// NOTE: this file is not referenced by any template -- `index.html` loads only
// index.js and index_inline.js, and nothing else in the repo names it. It is a
// dormant duplicate of index_inline.js's ACM functions. It is converted rather
// than left as-is so that it is not a trap if it is ever wired up, but nothing
// here runs today. The data-cmx-action namespace is distinct from index.js's
// data-cm-action and index_inline.js's data-acm-action so that, if all three
// ever load together, no dispatcher sees another's controls.

function cmxEscapeAttr(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function cmxSetFilterAndApply(selectId, value, apply) {
    const select = document.getElementById(selectId);
    if (!select) return;
    select.value = value;
    apply();
}

const CMX_ACTIONS = {
    'filter-acm-domain':       (el) => filterACMByDomain(el.dataset.domain),
    'open-acm-mapping':        (el) => openACMMappingModal(
                                         el.dataset.capId, el.dataset.capName,
                                         el.dataset.domain, el.dataset.level),
    'open-acm-detail':         (el) => openACMCapabilityDetail(el.dataset.capId, el.dataset.capName),
    'add-to-roadmap':          (el) => addToRoadmap(el.dataset.capId, el.dataset.capName,
                                         'technical', parseInt(el.dataset.capLevel, 10) || 1, 'medium'),
    'close-capability-detail': ()   => closeCapabilityDetail(),
    'delete-acm-mapping':      (el) => deleteACMMapping(el.dataset.mappingId, el.dataset.appId),
    'filter-unified-domain':   (el) => cmxSetFilterAndApply(
                                         'unified-domain-filter', el.dataset.domainCode,
                                         () => filterTable('unified')),
    'filter-acm-gap-domain':   (el) => cmxSetFilterAndApply(
                                         'acm-gap-domain-filter', el.dataset.domain,
                                         filterACMGapTable),
};

function runCmxAction(event) {
    const el = event.target.closest('[data-cmx-action]');
    if (!el) return;
    if (el.disabled) return;
    const handler = CMX_ACTIONS[el.getAttribute('data-cmx-action')];
    if (!handler) return;
    handler(el, event);
}

document.addEventListener('click', runCmxAction);
document.addEventListener('keydown', function(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const el = event.target.closest('[data-cmx-action][role="button"]');
    if (!el) return;
    event.preventDefault();
    runCmxAction(event);
});
