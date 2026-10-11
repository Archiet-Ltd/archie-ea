/* /ai-chat side panels: context, natural-language query, alerts and
 * recommendations, and the genome extraction rail.
 *
 * Ported verbatim from the inline script in
 * app/templates/ai_chat/index.html. The pieces that are easy to lose while
 * moving and are therefore called out here:
 *
 *   - /ai-chat/context/<domain> returns a `context` envelope containing
 *     `architecture_elements` or `technology_stacks`. ArchiMate elements are
 *     selectable; stacks are read-only because stack focus is not supported
 *     by the chat context resolver. Older direct element/application responses
 *     retain their existing selection behavior.
 *   - switchTab's panel list must contain all four names. 'history' was
 *     missing, so choosing another tab never hid the Chats panel.
 *   - executeNLQuery hands advisory questions to the chat instead of the
 *     entity-lookup endpoint (ArchieChat.commands.isAdvisoryQuery).
 *   - genomePanel() is an Alpine component reached through
 *     window._genomePanelInstance after every answer when ?mode=genome, so it
 *     has to stay a global function.
 */
(function (window, document) {
    'use strict';

    var ArchieChat = (window.ArchieChat = window.ArchieChat || {});
    var state = ArchieChat.state;
    var render = ArchieChat.render;
    var appendSystemMessage = render.appendSystemMessage;
    /* The template used to shadow `fetch` with a CSRF-injecting wrapper for
       its whole inline block. Outside that block the wrapper has to be named. */
    // No longer needed because we use Platform.fetch directly.

    function personaConfig() { return window.personaConfig || {}; }
    function userInput() { return document.getElementById('user-input'); }
    function chatForm() { return document.getElementById('chat-form'); }
    function selectedElementIdInput() { return document.getElementById('selected-element-id'); }
    function isAdvisoryQuery(q) { return ArchieChat.commands.isAdvisoryQuery(q); }

    function updateSamplePrompts(persona) {
        if (!persona || !personaConfig()[persona]) return;
        const config = personaConfig()[persona];
        const prompts = config.sample_prompts || [];
        if (prompts.length > 0) {
            const promptsHtml = prompts.map(p => `<button type="button" data-action="use-prompt" data-prompt="${p.replace(/"/g, '&quot;')}" class="text-left p-2 rounded-lg border hover:bg-accent text-xs truncate">${p}</button>`).join('');
            const contextDiv = document.getElementById('domain-context');
            contextDiv.innerHTML = `
                <h3 class="font-medium text-sm mb-3">Sample Prompts for ${config.name}</h3>
                <div class="space-y-2">${promptsHtml}</div>
                <div class="mt-4 p-3 bg-muted/50 rounded-lg">
                    <h4 class="font-medium text-xs mb-2">Expertise Areas</h4>
                    <div class="flex flex-wrap gap-1">
                        ${(config.expertise || []).map(e => `<span class="text-xs px-2 py-0.5 bg-info/10 text-info-emphasis rounded-full">${e}</span>`).join('')}
                    </div>
                </div>
            `;
        }
    }

    let contextLoad = null;
    let contextPageHidden = false;

    function cancelDomainContext() {
        if (contextLoad) {
            contextLoad.controller.abort();
            contextLoad = null;
        }
    }

    // beforeunload cancels while the old document can still receive fetch
    // rejections; pagehide also covers history navigation / the back-forward cache.
    window.addEventListener('beforeunload', cancelDomainContext);
    window.addEventListener('pagehide', () => {
        contextPageHidden = true;
        cancelDomainContext();
    });
    window.addEventListener('pageshow', (event) => {
        contextPageHidden = false;
        if (event.persisted) loadDomainContext(state.currentDomain);
    });

    function loadDomainContext(domain) {
        const target = document.getElementById('domain-context');
        if (!target || contextPageHidden) {
            cancelDomainContext();
            return Promise.resolve();
        }
        // Persona initialization and the page initializer can request the same
        // domain in one turn. Share that operation instead of issuing two loads.
        if (contextLoad && contextLoad.domain === domain && contextLoad.target === target) {
            return contextLoad.promise;
        }
        cancelDomainContext();
        const request = { domain, target, controller: new AbortController(), promise: null };
        contextLoad = request;
        request.promise = renderDomainContext(domain, request);
        return request.promise;
    }

    async function renderDomainContext(domain, request) {
        try {
            const payload = await Platform.fetch(`/ai-chat/context/${domain}`, {
                signal: request.controller.signal
            });
            // Unchecked, a 500 fell through to the else-branch below and rendered
            // "No specific context available for <domain>" — a load failure dressed
            // up as an empty model.
            // Platform.fetch throws on non-ok responses, so we only reach here on success.

            const contextContainer = document.getElementById('domain-context');
            if (request.controller.signal.aborted || contextLoad !== request ||
                contextContainer !== request.target || !contextContainer?.isConnected) return;
            if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
                payload.success === false || payload.error) {
                throw new Error('The context response could not be loaded');
            }
            const data = payload.context ?? payload;
            if (!data || typeof data !== 'object' || Array.isArray(data) || data.error) {
                throw new Error('The context response could not be loaded');
            }
            const elements = 'architecture_elements' in data ? data.architecture_elements : data.elements;
            const stacks = data.technology_stacks;
            if ((domain === 'architecture' && !Array.isArray(elements)) ||
                (domain === 'technology' && ('technology_stacks' in data
                    ? !Array.isArray(stacks) : !Array.isArray(data.applications))) ||
                ('applications' in data && !Array.isArray(data.applications))) {
                throw new Error('The context response has an invalid collection');
            }
            const escape = value => Platform.sanitize.escape(String(value ?? '—'));
            contextContainer.innerHTML = '';

            if (domain === 'architecture' && elements.length) {
                contextContainer.innerHTML = `
                    <h3 class="font-medium text-sm mb-3">ArchiMate Elements</h3>
                    <div class="space-y-2">
                        ${elements.map(el => `
                            <div class="rounded-lg border bg-card p-3 shadow-sm cursor-pointer hover:bg-accent transition-colors"
                                 data-action="select-context" data-id="${escape(el.id)}" data-type="archimate_element">
                                <div class="flex items-center gap-2 mb-1">
                                    <span class="bg-muted text-foreground text-xs px-1.5 py-0.5 rounded font-medium">${escape(el.type)}</span>
                                    <span class="font-medium text-sm">${escape(el.name)}</span>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            } else if (domain === 'technology' && stacks?.length) {
                contextContainer.innerHTML = `
                    <h3 class="font-medium text-sm mb-3">Technology Stacks</h3>
                    <div class="space-y-2">
                        ${stacks.map(stack => `
                            <div class="rounded-lg border bg-card p-3 shadow-sm">
                                <h4 class="font-medium text-sm">${escape(stack.name)}</h4>
                                <p class="text-xs text-muted-foreground">${escape(stack.description || '—')}</p>
                            </div>
                        `).join('')}
                    </div>
                `;
            } else if (domain === 'technology' && !('technology_stacks' in data) && data.applications?.length) {
                contextContainer.innerHTML = `
                    <h3 class="font-medium text-sm mb-3">Applications</h3>
                    <div class="space-y-2">
                        ${data.applications.map(app => `
                            <div class="rounded-lg border bg-card p-3 shadow-sm cursor-pointer hover:bg-accent transition-colors"
                                 data-action="select-context" data-id="${escape(app.id)}" data-type="application">
                                <div class="flex items-center gap-2 mb-1">
                                    <span class="bg-info/10 text-info-emphasis text-xs px-1.5 py-0.5 rounded font-medium">Application</span>
                                    <span class="font-medium text-sm">${escape(app.name)}</span>
                                </div>
                                <p class="text-xs text-muted-foreground">${escape(app.technology || 'No technology info')}</p>
                            </div>
                        `).join('')}
                    </div>
                `;
            } else {
                contextContainer.innerHTML = `
                    <div class="text-center py-8 text-muted-foreground">
                        <i data-lucide="inbox" class="h-8 w-8 mx-auto mb-2 opacity-50"></i>
                        <p class="text-sm">No specific context available for ${escape(domain)}</p>
                    </div>
                `;
            }

            // Re-initialize lucide icons
            lucide.createIcons();

        } catch (error) {
            // Only this request's cancellation/obsolescence is quiet. Real
            // transport errors were reported by Platform.fetch and remain visible.
            if (request.controller.signal.aborted || contextLoad !== request) return;
            // Surface the failure — otherwise the panel is left showing whatever the
            // previous domain rendered (or nothing), which reads as "no context" rather
            // than "the load failed".
            const contextContainer = document.getElementById('domain-context');
            if (contextContainer === request.target && contextContainer?.isConnected) {
                contextContainer.innerHTML = `
                    <div class="text-center py-8 text-muted-foreground">
                        <p class="text-sm text-destructive">Couldn't load context for ${Platform.sanitize.escape(String(domain))}.</p>
                    </div>
                `;
            }
        } finally {
            if (contextLoad === request) contextLoad = null;
        }
    }

    function selectContext(elementId, contextType) {
        selectedElementIdInput().value = elementId;
        state.contextElement = { id: elementId, type: contextType };

        // Visual feedback
        appendSystemMessage(`Context set: ${contextType} #${elementId}`, 'info');
    }

    // ==========================================================================
    // Sidebar Tab Management
    // ==========================================================================

    function switchSidebarTab(tab) {
        // 'history' was missing, so choosing another tab never hid the Chats
        // panel. The [id^="tab-"] reset below already covered all four.
        const panels = ['context', 'query', 'alerts', 'history'];

        // Hide all panels
        panels.forEach(panelName => {
            const panel = document.getElementById('panel-' + panelName);
            if (panel) {
                panel.classList.add('hidden');
                panel.style.display = '';
            }
        });

        // Reset all tab styles — remove active state including white background
        document.querySelectorAll('[id^="tab-"]').forEach(t => {
            t.classList.remove('border-b-2', 'border-primary', 'text-primary', 'bg-background');
            t.classList.add('text-muted-foreground');
        });

        // Show selected panel and style active tab
        const selectedPanel = document.getElementById('panel-' + tab);
        const selectedTab = document.getElementById('tab-' + tab);

        if (selectedPanel) {
            selectedPanel.classList.remove('hidden');
            selectedPanel.style.display = 'flex';
        }

        if (selectedTab) {
            selectedTab.classList.add('border-b-2', 'border-primary', 'text-primary', 'bg-background');
            selectedTab.classList.remove('text-muted-foreground');
        }

        // Load data for specific tabs
        if (tab === 'alerts') {
            loadRecommendations();
        }

        lucide.createIcons();
    }

    // ==========================================================================
    // Natural Language Query Functions
    // ==========================================================================

    async function executeNLQuery() {
        const input = document.getElementById('nl-query-input');
        const query = input.value.trim();
        if (!query) return;

        const resultsDiv = document.getElementById('nl-query-results');
        const resultsList = document.getElementById('nl-results-list');
        const explanationDiv = document.getElementById('nl-query-explanation');
        const countSpan = document.getElementById('nl-result-count');

        resultsDiv.classList.remove('hidden');

        // Advisory question -> hand off to the chat conversation pipeline.
        if (isAdvisoryQuery(query)) {
            input.value = '';
            if (explanationDiv) explanationDiv.classList.add('hidden');
            if (countSpan) countSpan.textContent = '';
            resultsList.innerHTML = '<div class="p-3 text-sm text-muted-foreground flex items-start gap-2">'
                + '<i data-lucide="messages-square" class="h-4 w-4 mt-0.5 shrink-0"></i>'
                + '<span>That\'s an advisory question — sent to the AI chat for a full answer. See the conversation →</span></div>';
            lucide.createIcons();
            userInput().value = query;
            if (chatForm().requestSubmit) { chatForm().requestSubmit(); }
            else { chatForm().dispatchEvent(new Event('submit', { cancelable: true, bubbles: true })); }
            const mc = document.getElementById('messages-container');
            if (mc) mc.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return;
        }

        resultsList.innerHTML = '<div class="text-center py-4"><i data-lucide="loader-2" class="h-5 w-5 animate-spin mx-auto"></i></div>';
        lucide.createIcons();

        try {
            const data = await Platform.fetch.post('/ai-chat/nl-query', {
                query: query,
                persona: state.currentPersona
            }, { silent: true }); // silent: true because we paint our own inline error state

            if (data.success) {
                countSpan.textContent = data.result_count + ' found';
                explanationDiv.textContent = data.explanation || '';
                explanationDiv.classList.toggle('hidden', !data.explanation);

                if (data.results && data.results.length > 0) {
                    // Entity names/descriptions are user-editable free text: they must be
                    // escaped, or an application named with an <img> error-handler payload
                    // executes script here.
                    const esc = window.Platform.sanitize.escape;
                    resultsList.innerHTML = data.results.map(item => `
                        <div class="p-2 border rounded-lg hover:bg-accent/50 cursor-pointer" data-action="show-entity" data-entity-type="${esc(item.entity_type)}" data-entity-id="${esc(String(item.id))}" data-entity-name="${esc(item.name)}">
                            <div class="flex items-center justify-between mb-1">
                                <span class="text-sm font-medium">${esc(item.name)}</span>
                                <span class="text-xs bg-muted px-1.5 py-0.5 rounded">${esc(item.entity_type)}</span>
                            </div>
                            <p class="text-xs text-muted-foreground line-clamp-2">${esc(item.description || item.status || item.business_owner || '')}</p>
                        </div>
                    `).join('');
                } else {
                    resultsList.innerHTML = '<p class="text-sm text-muted-foreground text-center py-4">No results found</p>';
                }

                // Show follow-up suggestions
                if (data.suggestions && data.suggestions.length > 0) {
                    const esc = window.Platform.sanitize.escape;
                    resultsList.innerHTML += `
                        <div class="pt-3 border-t mt-3">
                            <p class="text-xs font-medium text-muted-foreground mb-2">Related queries:</p>
                            ${data.suggestions.map(s => `
                                <button type="button" data-quick-query="${esc(s)}" class="block text-xs text-primary hover:underline mb-1">→ ${esc(s)}</button>
                            `).join('')}
                        </div>
                    `;
                }
            } else {
                const esc = window.Platform.sanitize.escape;
                resultsList.innerHTML = `
                    <div class="text-center py-4">
                        <p class="text-sm text-destructive-emphasis mb-2">${esc(data.error || 'Query failed')}</p>
                        ${data.suggestions ? data.suggestions.map(s => `<p class="text-xs text-muted-foreground">${esc(s)}</p>`).join('') : ''}
                    </div>
                `;
            }
        } catch (error) {
            resultsList.innerHTML = `<p class="text-sm text-destructive-emphasis text-center py-4">Error: ${window.Platform.sanitize.escape(error.message)}</p>`;
        }

        lucide.createIcons();
    }

    function runQuickQuery(query) {
        /* Every [data-quick-query] click funnels through here into
           /ai-chat/nl-query. A button carrying a slash command therefore posted
           the literal string to the natural-language query endpoint instead of
           running the command — including "/map-apqc", which IS a registered
           command, so it looked correct and quietly did the wrong thing.
           handleChatCommand is only reachable from the chat-form submit. */
        if (query.startsWith('/')) {
            userInput().value = query;
            chatForm().requestSubmit();
            return;
        }
        // Switch to Query tab first
        switchSidebarTab('query');
        // Set the query in the input
        document.getElementById('nl-query-input').value = query;
        // Execute the query after a small delay to ensure tab is visible
        setTimeout(() => executeNLQuery(), 150);
    }

    function askAboutRecommendation(title, description) {
        // Create a prompt asking the AI for help with this recommendation
        const prompt = `Help me implement this recommendation: "${title}"\n\nContext: ${description}\n\nPlease provide:\n1. Step-by-step implementation plan\n2. Key stakeholders to involve\n3. Potential challenges and mitigations\n4. Expected timeline and milestones`;

        // Set the prompt in the input field
        userInput().value = prompt;

        // Submit the form
        chatForm().dispatchEvent(new Event('submit'));
    }

    // ==========================================================================
    // Recommendations and Alerts Functions
    // ==========================================================================

    function exportContextLog() {
        const contextEl = document.getElementById('domain-context');
        const descEl = document.getElementById('context-description');
        const lines = [
            'Entelim Context Log',
            'Exported: ' + new Date().toISOString(),
            '---',
            'Domain: ' + (document.getElementById('domain-selector')?.value || 'global'),
            'Context: ' + (descEl?.textContent?.trim() || 'Global'),
            '---',
            contextEl?.innerText?.trim() || 'No context items selected.',
        ];
        const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'archie-context-' + Date.now() + '.txt';
        a.click();
        URL.revokeObjectURL(url);
    }


    /* ── Opening screen: this tenant's portfolio, not 21 generic buttons ─────
       Uses /ai-chat/recommendations, which the page already called into a
       sidebar tab nobody opens.

       Every state is designed, because each is a real state a real customer is
       in on a real day:

         loading      the most prominent element now depends on a network call
         failed       the endpoint answers 500 with EMPTY ARRAYS, so "no items"
                      and "outage" are the same payload unless status is checked
         empty        day one, zero applications — the moment the product has to
                      be most persuasive, so the empty state IS the onboarding
                      path rather than a blank panel
         populated    with the denominator, because "3 items" out of an unstated
                      total is an editorial claim the user cannot audit

       Nothing here invents a number. A count that was not returned renders as
       an em dash, never as 0.                                              */
    async function loadPortfolioBriefing() {
        const host = document.getElementById('portfolio-briefing');
        if (!host) return;

        const done = (html) => {
            host.innerHTML = html;
            host.setAttribute('aria-busy', 'false');
            if (window.lucide) lucide.createIcons();
        };

        try {
            const persona = state.currentPersona || '';
            const data = await Platform.fetch.get('/ai-chat/recommendations', { persona: persona }, { silent: true });
            // Platform.fetch.get throws on non-ok responses, so we only reach here on success.

            const alerts = Array.isArray(data.alerts) ? data.alerts : [];
            const recs = Array.isArray(data.recommendations) ? data.recommendations : [];
            const items = alerts.concat(recs);

            if (items.length === 0) {
                done(
                    '<div class="text-center py-2">' +
                      '<p class="text-sm font-medium text-foreground">Nothing flagged in your portfolio yet.</p>' +
                      '<p class="text-xs text-muted-foreground mt-1">Import an application portfolio, or start a ' +
                        'solution design — that one needs no portfolio at all.</p>' +
                      '<div class="mt-3 flex flex-wrap items-center justify-center gap-2">' +
                        '<a href="/import" class="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 ' +
                          'text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors">' +
                          '<i data-lucide="upload" class="h-3 w-3" aria-hidden="true"></i>Import a portfolio</a>' +
                        '<button type="button" id="briefing-start-solution" class="inline-flex items-center gap-1.5 ' +
                          'rounded-md border border-input bg-background px-3 py-1.5 text-xs font-medium ' +
                          'hover:bg-accent transition-colors">' +
                          '<i data-lucide="plus-circle" class="h-3 w-3" aria-hidden="true"></i>Design a solution</button>' +
                      '</div>' +
                    '</div>'
                );
                return;
            }

            /* The denominator matters. Surfacing 3 items out of an unstated
               total is an editorial claim: the user cannot see what was ranked
               above what, or how much was left out. */
            const total = (data.summary && typeof data.summary.total === 'number')
                ? data.summary.total : null;
            const shown = items.slice(0, 3);
            const scope = total === null
                ? shown.length + (shown.length === 1 ? ' item' : ' items')
                : shown.length + ' of ' + total + ' open finding' + (total === 1 ? '' : 's');

            const rows = shown.map((it, i) => {
                const title = ArchieChat.render.escapeForHtml(it.title || it.message || 'Untitled');
                const detail = ArchieChat.render.escapeForHtml(
                    (it.description || it.detail || '').slice(0, 140));
                const sev = String(it.severity || it.priority || '').toLowerCase();
                const tone = sev === 'critical' ? 'text-destructive-emphasis'
                           : sev === 'high' ? 'text-warning-emphasis'
                           : 'text-muted-foreground';
                return '<button type="button" class="js-briefing-item w-full text-left rounded-lg border ' +
                         /* No hover:bg-accent: --muted-foreground scores 4.42 on --accent
                            versus 4.76 on --background, so tinting the row on hover
                            pushed the detail line under WCAG AA (axe measured 4.41,
                            expected 4.5). The border carries the affordance. The same
                            pattern fails anywhere hover:bg-accent sits under
                            text-muted-foreground, and only while hovering — which is
                            why no screenshot would ever show it. */
                         'border-border bg-background px-3 py-2 hover:border-primary ' +
                         'transition-colors focus-visible:outline-none focus-visible:ring-2 ' +
                         'focus-visible:ring-ring" data-index="' + i + '">' +
                         '<div class="flex items-start gap-2">' +
                           '<i data-lucide="alert-circle" class="h-3.5 w-3.5 mt-0.5 shrink-0 ' + tone + '" aria-hidden="true"></i>' +
                           '<span class="min-w-0">' +
                             '<span class="block text-sm font-medium text-foreground">' + title + '</span>' +
                             (detail ? '<span class="block text-xs text-muted-foreground truncate">' + detail + '</span>' : '') +
                           '</span>' +
                         '</div>' +
                       '</button>';
            }).join('');

            done(
                '<div class="flex items-center justify-between mb-2">' +
                  '<span class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">' +
                    'In your portfolio</span>' +
                  '<span class="text-xs text-muted-foreground">' +
                    ArchieChat.render.escapeForHtml(scope) + ', ranked by impact</span>' +
                '</div>' +
                '<div class="space-y-1.5">' + rows + '</div>'
            );

            host._items = shown;
        } catch (err) {
            /* An outage must not read as a clean portfolio. The endpoint's own
               500 body carries empty arrays, which is precisely the shape of
               "nothing to report". */
            done(
                '<div class="flex items-start gap-2 text-xs">' +
                  '<i data-lucide="alert-triangle" class="h-3.5 w-3.5 mt-0.5 shrink-0 text-warning-emphasis" aria-hidden="true"></i>' +
                  '<span class="flex-1">' +
                    '<span class="block text-foreground font-medium">Could not read your portfolio.</span>' +
                    '<span class="block text-muted-foreground">This is an error, not an empty portfolio — ' +
                      'nothing below reflects your data.</span>' +
                  '</span>' +
                  '<button type="button" id="briefing-retry" class="shrink-0 rounded-md border border-input ' +
                    'bg-background px-2 py-1 font-medium hover:bg-accent transition-colors">Retry</button>' +
                '</div>'
            );
        }
    }

    /* Delegated: the briefing re-renders, so per-element handlers would leak. */
    document.addEventListener('click', function (e) {
        if (!e.target.closest) return;
        if (e.target.closest('#briefing-retry')) { loadPortfolioBriefing(); return; }
        if (e.target.closest('#briefing-start-solution')) {
            const input = document.getElementById('user-input');
            if (input) {
                input.value = 'I need a new solution about ';
                input.focus();
            }
            return;
        }
        const item = e.target.closest('.js-briefing-item');
        if (!item) return;
        const host = document.getElementById('portfolio-briefing');
        const picked = host && host._items && host._items[Number(item.dataset.index)];
        if (!picked) return;
        const input = document.getElementById('user-input');
        const form = document.getElementById('chat-form');
        if (!input || !form) return;
        /* Ask about the finding in the user's own terms; the assistant then
           looks it up rather than being handed a conclusion to agree with. */
        input.value = 'Tell me about this finding and what you would do about it: ' +
                      (picked.title || picked.message || '');
        form.requestSubmit();
    });

    async function loadRecommendations(refresh = false) {
        const alertsList = document.getElementById('alerts-list');
        const recsContent = document.getElementById('recs-content');
        const healthScore = document.getElementById('health-score');
        const healthBar = document.getElementById('health-bar');
        const alertBadge = document.getElementById('alert-badge');

        alertsList.innerHTML = '<div class="text-center py-4"><i data-lucide="loader-2" class="h-5 w-5 animate-spin mx-auto"></i></div>';
        lucide.createIcons();

        try {
            const data = await Platform.fetch.get('/ai-chat/recommendations', {
                persona: state.currentPersona,
                refresh: refresh
            }, { silent: true });
            /* Platform.fetch.get throws on non-ok responses, so we only reach here on success.
               The wrapper ensures that a 500 with {"error": ...} will throw, preventing
               an outage from being rendered as a clean empty state. */

            /* null, not 0: a health score that was never computed must render
               as an em dash, because 0% is a legitimate measured value and the
               user cannot tell the two apart. */
            const score = (typeof data.health_score === 'number') ? data.health_score : null;
            healthScore.textContent = score === null ? '—' : score + '%';
            healthBar.style.width = (score === null ? 0 : score) + '%';

            // Color based on score
            if (score >= 70) { // token-migration-ok — health score status colors
                healthScore.className = 'text-lg font-bold text-emerald-600'; // token-migration-ok
                healthBar.className = 'bg-emerald-500 h-2 rounded-full transition-all'; // token-migration-ok
            } else if (score >= 40) {
                healthScore.className = 'text-lg font-bold text-amber-600'; // token-migration-ok
                healthBar.className = 'bg-amber-500 h-2 rounded-full transition-all'; // token-migration-ok
            } else {
                healthScore.className = 'text-lg font-bold text-destructive'; // token-migration-ok
                healthBar.className = 'bg-destructive h-2 rounded-full transition-all'; // token-migration-ok
            }

            // Update alert badge
            const criticalCount = (data.summary?.critical_count || 0) + (data.summary?.high_count || 0);
            if (criticalCount > 0) {
                alertBadge.textContent = criticalCount > 99 ? '99+' : criticalCount;
                alertBadge.classList.remove('hidden');
                alertBadge.style.display = 'flex';
            } else {
                alertBadge.classList.add('hidden');
                alertBadge.style.display = 'none';
            }

            // Render alerts with action links (no inline onclick — use addEventListener)
            if (data.alerts && data.alerts.length > 0) {
                let alertHtml = '';
                data.alerts.slice(0, 5).forEach(function(alert) {
                    let actions = '';
                    if (alert.action_url) {
                        actions += '<a href="' + alert.action_url + '" class="inline-flex items-center gap-1 text-xs text-primary hover:underline font-medium"><i data-lucide="external-link" class="h-3 w-3"></i>' + (alert.action_label || 'Open') + '</a>';
                    }
                    if (alert.query) {
                        actions += (actions ? '<span class="text-muted-foreground mx-1">|</span>' : '');
                        actions += '<button type="button" class="text-xs text-primary hover:underline js-alert-query" data-query="' + alert.query.replace(/"/g, '&quot;') + '"><i data-lucide="search" class="h-3 w-3 inline"></i> Ask AI</button>';
                    }
                    alertHtml += '<div class="p-3 border rounded-lg mb-2 ' + getPriorityBorderClass(alert.priority) + '">' +
                        '<div class="flex items-start justify-between gap-2 mb-1">' +
                        '<span class="text-sm font-medium">' + alert.title + '</span>' +
                        '<span class="text-xs px-1.5 py-0.5 rounded shrink-0 ' + getPriorityBadgeClass(alert.priority) + '">' + alert.priority + '</span>' +
                        '</div>' +
                        '<p class="text-xs text-muted-foreground mb-2">' + alert.description + '</p>' +
                        '<div class="flex items-center gap-1 flex-wrap">' + actions + '</div></div>';
                });
                alertsList.innerHTML = alertHtml;
                alertsList.querySelectorAll('.js-alert-query').forEach(function(btn) {
                    btn.addEventListener('click', function() { runQuickQuery(this.dataset.query); });
                });
            } else {
                alertsList.innerHTML = '<p class="text-sm text-center text-muted-foreground py-4"><i data-lucide="check-circle" class="h-5 w-5 mx-auto mb-2 text-emerald-500"></i>No active alerts</p>'; // token-migration-ok
            }

            // Render recommendations with details (steps, benefits, timeline)
            if (data.recommendations && data.recommendations.length > 0) {
                let recHtml = '';
                data.recommendations.slice(0, 3).forEach(function(rec) {
                    let details = '';
                    if (rec.steps && rec.steps.length > 0) {
                        details += '<div class="mt-2 text-[11px] text-muted-foreground space-y-1">';
                        if (rec.timeline) details += '<div><strong>Timeline:</strong> ' + rec.timeline + ' · <strong>Effort:</strong> ' + (rec.effort || 'TBD') + '</div>';
                        details += '<div class="font-medium mt-1">Steps:</div><ol class="list-decimal pl-4 space-y-0.5">';
                        rec.steps.forEach(function(s) { details += '<li>' + s + '</li>'; });
                        details += '</ol>';
                        if (rec.benefits && rec.benefits.length > 0) {
                            details += '<div class="font-medium mt-1">Benefits:</div><ul class="list-disc pl-4">';
                            rec.benefits.forEach(function(b) { details += '<li>' + b + '</li>'; });
                            details += '</ul>';
                        }
                        details += '</div>';
                    }
                    recHtml += '<div class="p-2 border rounded-lg mb-2 bg-gradient-to-r from-primary/5 to-transparent">' +
                        '<div class="flex items-center justify-between mb-1">' +
                        '<span class="text-xs font-medium">' + rec.title + '</span>' +
                        '<span class="text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded">' + (rec.impact_score || 0) + '%</span>' +
                        '</div>' +
                        '<p class="text-[11px] text-muted-foreground">' + (rec.description || '').substring(0, 100) + '</p>' +
                        details +
                        '<div class="flex gap-2 mt-2">' +
                        '<button type="button" class="text-xs text-primary hover:underline js-rec-item" data-title="' + (rec.title || '').replace(/"/g, '&quot;') + '" data-desc="' + (rec.description || '').substring(0, 200).replace(/"/g, '&quot;') + '"><i data-lucide="message-circle" class="h-3 w-3 inline"></i> Ask AI to help</button>' +
                        '</div></div>';
                });
                recsContent.innerHTML = recHtml;
                recsContent.querySelectorAll('.js-rec-item').forEach(function(el) {
                    el.addEventListener('click', function() {
                        askAboutRecommendation(this.dataset.title, this.dataset.desc);
                    });
                });
            } else {
                recsContent.innerHTML = '<p class="text-xs text-muted-foreground">No recommendations at this time.</p>';
            }

        } catch (error) {
            alertsList.innerHTML =
                '<p class="text-sm text-destructive-emphasis text-center py-4">Could not load alerts. ' +
                'This is an error, not an empty portfolio.</p>';
            /* Do not leave a stale or zero score behind an error — an unknown
               value renders as an em dash (DESIGN.md), never as 0%. */
            if (healthScore) healthScore.textContent = '—';
            if (healthBar) healthBar.style.width = '0%';
        }

        lucide.createIcons();
    }

    function refreshRecommendations() {
        loadRecommendations(true);
    }

    /* Four distinct levels from the semantic palette. Amber has no semantic
       token of its own (DESIGN.md), so high and medium are separated by
       weight — solid warning vs a muted warning — rather than by hue. */
    function getPriorityBorderClass(priority) {
        const classes = {
            'critical': 'border-l-4 border-l-destructive',
            'high': 'border-l-4 border-l-warning',
            'medium': 'border-l-4 border-l-warning/50',
            'low': 'border-l-4 border-l-info'
        };
        return classes[priority] || '';
    }

    /* Coloured text on its own tint needs the -emphasis variant, not the base
       token. The previous raw-scale values scored 2.35, 2.60 and 3.85 against
       their own tints — all far under the AA 4.5 required for normal text. */
    function getPriorityBadgeClass(priority) {
        const classes = {
            'critical': 'bg-destructive/10 text-destructive-emphasis',
            'high': 'bg-warning/10 text-warning-emphasis',
            'medium': 'bg-muted text-muted-foreground',
            'low': 'bg-info/10 text-info-emphasis'
        };
        return classes[priority] || 'bg-muted text-foreground';
    }

    /* Genome extraction rail — an Alpine component, so it must remain a
       global function for x-data="genomePanel()" to resolve it, and the
       instance is published on window._genomePanelInstance by x-init because
       the chat pipeline calls extractAfterMessage() after every answer. */
    function genomePanel() {
        return {
            isGenomeMode: new URLSearchParams(window.location.search).get('mode') === 'genome',
            genome: {},
            completeness: 0,
            readyToGenerate: false,
            genomeItems: [],

            async extractAfterMessage(conversation) {
                if (!this.isGenomeMode) return;
                try {
                    const data = await Platform.fetch.post('/api/codegen/genome/extract', {
                        conversation: conversation,
                        current_genome: this.genome
                    }, { silent: true });
                    // Platform.fetch.post throws on non-ok responses, so we only reach here on success.
                    // The wrapper also ensures CSRF token is injected automatically.
                    if (!data.success) throw new Error('extraction did not succeed');

                    this.genome = data.genome_partial;
                    this.completeness = data.completeness_pct;
                    this.readyToGenerate = data.ready_to_generate;
                    this._refreshItems();
                    this._extractErrorShown = false;
                } catch (e) {
                    // Called after every answer while in genome mode — toast once per
                    // outage, not on every message, or a bad run spams the user.
                    if (!this._extractErrorShown) {
                        this._extractErrorShown = true;
                        if (window.Platform && Platform.toast) {
                            Platform.toast.error('Could not update the solution genome from this answer.');
                        }
                    }
                }
            },

            _refreshItems() {
                const g = this.genome;
                const allItems = [
                    {
                        key: 'problem',
                        label: 'Problem',
                        value: g.problem?.statement ? g.problem.statement.slice(0, 100) : '',
                        status: g.problem?.statement ? 'confirmed' : 'pending'
                    },
                    {
                        key: 'entities',
                        label: 'Entities',
                        value: Object.keys(g.modules || {})
                            .flatMap(m => Object.keys((g.modules[m]?.entities) || {}))
                            .join(', '),
                        status: Object.keys(g.modules || {}).length > 0 ? 'confirmed' : 'pending'
                    },
                    {
                        key: 'auth',
                        label: 'Authentication',
                        value: g.infrastructure?.auth?.provider || '',
                        status: g.infrastructure?.auth?.provider ? 'confirmed' : 'pending'
                    },
                    {
                        key: 'tenancy',
                        label: 'Multi-tenancy',
                        value: g.security?.multi_tenancy?.enabled != null
                            ? (g.security.multi_tenancy.enabled ? 'Yes — isolated per org' : 'Single shared system')
                            : '',
                        status: g.security?.multi_tenancy?.enabled != null ? 'confirmed' : 'pending'
                    },
                    {
                        key: 'notifications',
                        label: 'Notifications',
                        value: (g.notifications?.channels || []).join(', '),
                        status: g.notifications?.channels?.length > 0 ? 'confirmed' : 'pending'
                    },
                    {
                        key: 'payments',
                        label: 'Payments',
                        value: g.payments?.provider && g.payments.provider !== 'none' ? g.payments.provider : '',
                        status: g.payments?.provider && g.payments.provider !== 'none' ? 'confirmed' : 'pending'
                    },
                ];
                // Show confirmed items + first pending item
                const confirmed = allItems.filter(i => i.status === 'confirmed');
                const firstPending = allItems.find(i => i.status === 'pending');
                this.genomeItems = firstPending ? [...confirmed, firstPending] : confirmed;
            },

            generate() {
                if (!this.readyToGenerate) return;
                // The old /codegen/workbench destination never existed (404 dead-end).
                // The Architecture Journey wizard is the canonical idea-to-code flow;
                // the genome draft is kept in sessionStorage for future prefill work.
                sessionStorage.setItem('genome_draft', JSON.stringify(this.genome));
                window.location.href = '/architecture-journey/';
            }
        };
    }

    ArchieChat.panels = {
        switchTab: switchSidebarTab,
        loadDomainContext: loadDomainContext,
        selectContext: selectContext,
        updateSamplePrompts: updateSamplePrompts,
        executeNLQuery: executeNLQuery,
        runQuickQuery: runQuickQuery,
        loadRecommendations: loadRecommendations,
        loadPortfolioBriefing: loadPortfolioBriefing,
        refreshRecommendations: refreshRecommendations,
        exportContextLog: exportContextLog,
        genomePanel: genomePanel
    };

    /* Alpine expressions and the markup resolve these off window. */
    window.switchSidebarTab = switchSidebarTab;
    window.executeNLQuery = executeNLQuery;
    window.runQuickQuery = runQuickQuery;
    window.refreshRecommendations = refreshRecommendations;
    window.exportContextLog = exportContextLog;
    window.genomePanel = genomePanel;
})(window, document);
