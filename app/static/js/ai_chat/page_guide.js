(function () {
    'use strict';

    function byId(id) {
        return document.getElementById(id);
    }

    function emitTelemetry(eventType, context, extra) {
        const detail = Object.assign({
            event_type: eventType,
            page_key: context && context.page_key ? context.page_key : null,
            scope_key: context && context.scope_key ? context.scope_key : null,
            guide_mode: context && context.guide_mode ? context.guide_mode : 'specialized',
            page_title: context && context.title ? context.title : 'AI Guide',
            timestamp: new Date().toISOString()
        }, extra || {});

        window.__pageGuideTelemetry = window.__pageGuideTelemetry || [];
        window.__pageGuideTelemetry.push(detail);
        window.dispatchEvent(new CustomEvent('page-guide-telemetry', { detail }));
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Lightweight markdown → safe HTML for guide assistant responses.
    // DOMPurify (already loaded globally in _head.html) sanitizes the output.
    function renderMarkdown(text) {
        if (!text) return '';
        let html = String(text);
        // Bold **text**
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Italic *text* (single asterisk, not part of **)
        html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
        // Inline code `code`
        html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
        // H3 headers
        html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
        // H2 headers
        html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
        // Unordered list items (-, *, •)
        html = html.replace(/^[ \t]*[-*•] (.+)$/gm, '<li>$1</li>');
        // Ordered list items
        html = html.replace(/^[ \t]*\d+\. (.+)$/gm, '<li>$1</li>');
        // Paragraph breaks from double newlines
        html = html.replace(/\n\n+/g, '</p><p>');
        html = '<p>' + html + '</p>';
        // Single newlines within paragraphs
        html = html.replace(/\n/g, '<br>');
        // Sanitize — DOMPurify is loaded globally via _head.html
        if (window.DOMPurify) {
            html = DOMPurify.sanitize(html, {
                ALLOWED_TAGS: ['p', 'strong', 'em', 'code', 'h3', 'h4', 'ul', 'ol', 'li', 'br'],
                ALLOWED_ATTR: []
            });
        }
        return html;
    }

    function renderMessages(messages) {
        const root = byId('page-guide-messages');
        if (!root) return;

        if (!messages.length) {
            root.innerHTML = '<p class="text-sm text-muted-foreground">No messages yet. Start with one of the suggested questions.</p>';
            return;
        }

        root.innerHTML = messages.map((message) => {
            const isAssistant = message.role === 'assistant';
            // Assistant responses: render markdown. User messages: escape HTML (raw text).
            const body = isAssistant
                ? `<div class="text-sm text-foreground guide-markdown">${renderMarkdown(message.content)}</div>`
                : `<div class="text-sm text-foreground whitespace-pre-wrap">${escapeHtml(message.content)}</div>`;
            return `
                <div class="rounded-lg px-3 py-2 ${isAssistant ? 'bg-background border border-border' : 'bg-primary/10'}">
                    <p class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">${isAssistant ? 'Guide' : 'You'}</p>
                    ${body}
                </div>
            `;
        }).join('');

        root.scrollTop = root.scrollHeight;
    }

    function clearError() {
        const root = byId('page-guide-error');
        if (!root) return;
        root.textContent = '';
        root.classList.add('hidden');
    }

    function setError(message) {
        const root = byId('page-guide-error');
        if (!root) return;
        root.textContent = message;
        root.classList.remove('hidden');
    }

    function renderActions(actions) {
        const root = byId('page-guide-actions');
        if (!root) return;

        root.innerHTML = (actions || []).map((action) => `
            <a href="${escapeHtml(action.url || '#')}"
               data-guide-action="true"
               data-guide-action-label="${escapeHtml(action.label || '')}"
               data-guide-action-url="${escapeHtml(action.url || '#')}"
               class="block rounded-lg border border-border bg-background px-3 py-2 hover:bg-accent transition-colors">
                <p class="text-sm font-medium text-foreground">${escapeHtml(action.label)}</p>
                <p class="text-xs text-muted-foreground mt-0.5">${escapeHtml(action.description || '')}</p>
            </a>
        `).join('');
    }

    function renderStarters(starters) {
        const root = byId('page-guide-starters');
        if (!root) return;

        root.innerHTML = (starters || []).map((question) => `
            <button type="button"
                    class="page-guide-starter inline-flex items-center rounded-full border border-input px-3 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                    data-question="${escapeHtml(question)}">
                ${escapeHtml(question)}
            </button>
        `).join('');
    }

    function renderGlossary(items) {
        const root = byId('page-guide-glossary');
        if (!root) return;

        root.innerHTML = (items || []).map((item) => `
            <div class="rounded-lg border border-border bg-background px-3 py-2">
                <dt class="text-sm font-medium text-foreground">${escapeHtml(item.term)}</dt>
                <dd class="text-xs text-muted-foreground mt-1">${escapeHtml(item.definition)}</dd>
            </div>
        `).join('');
    }

    async function fetchHistory(context) {
        const url = `/ai-chat/guide/history?page_key=${encodeURIComponent(context.page_key)}&scope_key=${encodeURIComponent(context.scope_key)}`;
        const data = await window.Platform.fetch(url, { silent: true });
        return data.messages || [];
    }

    async function clearHistory(context) {
        await window.Platform.fetch('/ai-chat/guide/history/clear', {
            method: 'POST',
            body: {
                page_key: context.page_key,
                scope_key: context.scope_key
            }
        });
    }

    async function sendMessage(context, message) {
        return window.Platform.fetch('/ai-chat/guide/message', {
            method: 'POST',
            body: {
                page_key: context.page_key,
                scope_key: context.scope_key,
                page_title: context.title,
                message
            }
        });
    }

    async function loadGuide(context) {
        clearError();
        byId('page-guide-page-title').textContent = context.title || 'AI Guide';
        byId('page-guide-summary').textContent = context.summary || '';
        const modeNote = byId('page-guide-mode-note');
        if (modeNote) {
            modeNote.textContent = context.guide_mode === 'generic'
                ? 'General guide mode: this page does not yet have specialized guidance.'
                : 'Page-specific guide mode';
        }
        renderActions(context.suggested_actions || []);
        renderStarters(context.starter_questions || []);
        renderGlossary(context.glossary || []);
        const history = await fetchHistory(context);
        renderMessages(history);
        emitTelemetry('response_success', context, {
            operation: 'load',
            response_length: history.length
        });
    }

    function init() {
        const context = window.pageGuideContext;
        if (!context || !context.enabled || !context.page_key || !context.scope_key) return;

        window.addEventListener('open-drawer-page-guide', () => {
            emitTelemetry('open', context);
            loadGuide(context).catch((error) => {
                setError('The guide could not load right now. You can still use the starter questions or try again.');
                emitTelemetry('response_failure', context, {
                    operation: 'load',
                    error: error && error.message ? error.message : 'Unknown page guide load failure'
                });
            });
        });

        document.addEventListener('click', (event) => {
            const starter = event.target.closest('.page-guide-starter');
            if (!starter) return;
            const input = byId('page-guide-input');
            if (!input) return;
            input.value = starter.dataset.question || '';
            input.focus();
        });

        const actionsRoot = byId('page-guide-actions');
        if (actionsRoot) {
            actionsRoot.addEventListener('click', (event) => {
                const action = event.target.closest('[data-guide-action="true"]');
                if (!action) return;
                emitTelemetry('action_click', context, {
                    action_label: action.dataset.guideActionLabel || '',
                    action_url: action.dataset.guideActionUrl || '#'
                });
            });
        }

        const form = byId('page-guide-form');
        if (form) {
            form.addEventListener('submit', async (event) => {
                event.preventDefault();
                const input = byId('page-guide-input');
                const message = (input.value || '').trim();
                if (!message) return;

                emitTelemetry('message_send', context, {
                    operation: 'send',
                    message_length: message.length
                });

                try {
                    clearError();
                    const current = await fetchHistory(context);
                    current.push({ role: 'user', content: message });
                    renderMessages(current);
                    input.value = '';

                    const response = await sendMessage(context, message);

                    /* A 200 is not the same as an answer. When no provider is
                       configured the route still replies success:true and puts
                       the administrative "AI features aren't configured yet"
                       text in `response`, flagging it only via `agent_error`.
                       Ignoring that field rendered the notice as ordinary
                       guide prose AND recorded it as `response_success`, so
                       the telemetry said the guide was working while every
                       answer was really a configuration notice. */
                    if (response.agent_error) {
                        setError(
                            response.response ||
                            'The assistant could not answer just now. Your question is saved; try again in a moment.'
                        );
                        input.value = message;
                        emitTelemetry('response_failure', context, {
                            operation: 'send',
                            reason: 'agent_error'
                        });
                        return;
                    }

                    current.push({ role: 'assistant', content: response.response || '' });
                    renderMessages(current);
                    emitTelemetry('response_success', context, {
                        operation: 'send',
                        response_length: (response.response || '').length
                    });
                } catch (error) {
                    // The 503 the feature gate raises when no provider is configured
                    // already carries the one plain sentence a person should read
                    // (see FeatureFlagService.require_ai_for_route /
                    // ai_unavailable_message) -- show it as-is rather than a
                    // second, locally-invented wording that could drift from it.
                    let errMsg = 'The guide could not respond right now. Your conversation is still here, and you can retry.';
                    if (error && error.status === 429) {
                        errMsg = 'You have reached the guide request limit. Please wait a few minutes and try again.';
                    } else if (error && error.status === 503) {
                        errMsg = (error.message && error.message.trim()) || errMsg;
                    }
                    setError(errMsg);
                    input.value = message;
                    emitTelemetry('response_failure', context, {
                        operation: 'send',
                        error: error && error.message ? error.message : 'Unknown page guide send failure'
                    });
                }
            });
        }

        const clearButton = byId('page-guide-clear');
        if (clearButton) {
            clearButton.addEventListener('click', async () => {
                try {
                    await clearHistory(context);
                    clearError();
                    renderMessages([]);
                    emitTelemetry('clear_history', context);
                } catch (error) {
                    setError('The guide history could not be cleared right now. Please try again.');
                    emitTelemetry('response_failure', context, {
                        operation: 'clear',
                        error: error && error.message ? error.message : 'Unknown page guide clear failure'
                    });
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
