/* /ai-chat page wiring.
 *
 * What is left once transport, render, commands and panels have been pulled
 * out: the selectors, the submit path, the delegated data-action vocabulary,
 * the entity modal, the create-solution modal, the document panel and mobile
 * sidebar toggles, the deep-link handling, and the conversation history rail.
 *
 * Seams that are invisible from the markup and break silently — do not remove
 * without reading spec §12.3:
 *   - the five window CustomEvents (add-document-analysis, show-notification,
 *     ask-question, link-to-entity, create-entity) are the ONLY connection
 *     between document_upload.js and the transcript;
 *   - the approvals modal opens through data-modal-open / data-modal-close and
 *     the `hidden` ATTRIBUTE, served by ui/modal.js. It is not Alpine. Turning
 *     it into x-show silently breaks the badge button;
 *   - window.loadSessionList is called from the streaming path and the
 *     non-streaming path. Lose it and a new chat does not appear in the rail
 *     until the page is reloaded.
 */
(function (window, document) {
    'use strict';

    var ArchieChat = window.ArchieChat;

    // Enhanced JavaScript for multi-domain chat with persona support

    /* The CSRF-injecting fetch wrapper that used to shadow `fetch` for this
       whole block now lives in transport.js, so every module shares it. */
    const fetch = ArchieChat.transport.apiFetch;

    const messagesContainer = document.getElementById('messages-container');
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const domainSelector = document.getElementById('domain-selector');
    const personaSelector = document.getElementById('persona-selector');
    const modelSelector = document.getElementById('model-selector');
    const selectedElementIdInput = document.getElementById('selected-element-id');

    // Get configurations from window object
    let domainConfig = window.domainConfig || {};
    let personaConfig = window.personaConfig || {};

    /* Conversation state and rendering now live in ai_chat/render.js, because
       more than one module reads them. `state` is the same object every module
       sees — assigning state.currentDomain here is visible to render.js. */
    const state = ArchieChat.state;
    const {
        getColorClass, appendMessage, appendSystemMessage, appendError,
        beginStreamedMessage, updateStreamedMessage, finaliseStreamedMessage
    } = ArchieChat.render;
    const {
        handle: handleChatCommand, detectArchimateFreeformIntent,
        handleArchimateFreeform, applyApqcMappings, handleGapAnalysis,
        handleDiscoverVendors, handleArchitectViewpoints, handleArbReady
    } = ArchieChat.commands;
    const {
        switchTab: switchSidebarTab, loadDomainContext, selectContext,
        updateSamplePrompts, runQuickQuery, loadRecommendations
    } = ArchieChat.panels;

    // Welcome UI helpers — hide suggestion chips on first user send
    function _hideWelcomeUI() {
        document.getElementById('suggestion-chips')?.classList.add('hidden');
        document.getElementById('suggestion-chips-label')?.classList.add('hidden');
        document.getElementById('domain-welcome-grid')?.classList.add('hidden');
    }

    // A deep link arrives already headed somewhere specific — collapse only the
    // browse-and-pick suggestion cards (portfolio briefing, persona/domain grids),
    // keeping the "How can I help you today?" heading itself visible, unlike
    // _hideWelcomeUI() above which hides the whole welcome block including it.
    function _hideWelcomeSuggestions() {
        document.getElementById('domain-welcome-suggestions')?.classList.add('hidden');
        document.getElementById('suggestion-chips')?.classList.add('hidden');
        document.getElementById('suggestion-chips-label')?.classList.add('hidden');
    }

    function setSuggestion(text) {
        const ta = document.getElementById('user-input');
        if (ta) {
            ta.value = text;
            ta.focus();
        }
    }

    // Hide chips on first form submit
    document.getElementById('chat-form')?.addEventListener('submit', _hideWelcomeUI, { once: true });

    // ARCH-045: an enabled selector offering only "Auto-Select Model" with an
    // empty value presents a choice that does not exist — that reads as a
    // stubbed capability, not as "nothing to configure". Populate it from
    // /ai-chat/models when there is a real choice to make (2+ models); when
    // there are 0 or 1, hide the control and show an honest note instead of
    // fabricating options. An empty list and one configured model need
    // different notes; neither is evidence that a provider is reachable.
    async function loadAvailableModels() {
        const wrap = document.getElementById('model-selector-wrap');
        const emptyNote = document.getElementById('model-selector-empty-note');
        try {
            const models = await ArchieChat.transport.loadModels();

            if (!models || models.length < 2) {
                wrap?.classList.add('hidden');
                if (emptyNote) {
                    emptyNote.textContent = models?.length === 1
                        ? 'One AI model is configured — responses use the platform default.'
                        : 'No AI models are configured.';
                    emptyNote.classList.remove('hidden');
                }
                return;
            }

            // Clear existing options
            modelSelector.innerHTML = '<option value="">Auto-Select Model</option>';

            // Add available models
            models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.model;
                option.textContent = model.display_name;

                // Add recommended for info
                if (model.recommended_for && model.recommended_for.length > 0) {
                    option.textContent += ` (${model.recommended_for[0]})`;
                }

                if (model.is_fallback) {
                    option.textContent += ` [Fallback ${model.fallback_order}]`;
                }

                if (model.test_status === 'failed') {
                    option.textContent += ' [Provider Warning]';
                }

                modelSelector.appendChild(option);
            });
            wrap?.classList.remove('hidden');
            emptyNote?.classList.add('hidden');
        } catch (error) {
            // Do not reuse the "no models configured" wording here: the model list
            // failed to load, which is not the same as there being none.
            wrap?.classList.add('hidden');
            if (emptyNote) {
                emptyNote.textContent = `The model list could not be loaded (${error.message || error}) — responses use the platform default.`;
                emptyNote.classList.remove('hidden');
            }
        }
    }

    // Initialize domain selector
    domainSelector.addEventListener('change', (e) => {
        state.currentDomain = e.target.value;
        updateDomainUI(state.currentDomain);
        loadDomainContext(state.currentDomain);
    });

    // ARCH-114: persona label echoed on every answer, so the user can always
    // see which assistant actually produced it — reads from the selector's
    // current option text (the source the user actually looked at), not the
    // config object, so it can never say something the header doesn't.
    function _activePersonaLabel() {
        const opt = personaSelector.selectedOptions && personaSelector.selectedOptions[0];
        return opt ? opt.textContent : '';
    }

    // ARCH-114: persona is a single source of truth — selector, state and
    // localStorage all derive from the same value, and switching applies the
    // full effect (domain + sample prompts) whether triggered by a user
    // click or by _syncPersonaFromSelector() on load/reload.
    function _applyPersonaChange(persona, persistChoice) {
        state.currentPersona = persona;
        // The second argument is "persist the choice": false when the page applies the preselected persona on
        // load, true for a real user change. Only a real change is announced in the transcript; announcing the
        // load-time sync scrolled the pane past the greeting before the welcome content had finished growing.
        updatePersonaUI(persona, persistChoice);
        if (persistChoice && persona) {
            try {
                localStorage.setItem(window.chatPersonaPreferenceKey, persona);
            } catch (error) {
                /* swallow-ok: localStorage is unavailable; the chosen persona remains active for this visit */
            }
        }
        if (persona && personaConfig[persona]) {
            // Auto-switch to persona's default domain
            const defaultDomain = personaConfig[persona].default_domain;
            if (defaultDomain && defaultDomain !== state.currentDomain) {
                domainSelector.value = defaultDomain;
                state.currentDomain = defaultDomain;
                updateDomainUI(state.currentDomain);
                loadDomainContext(state.currentDomain);
            }
            // Show sample prompts
            updateSamplePrompts(persona);
        }
    }

    // Initialize persona selector
    personaSelector.addEventListener('change', (e) => {
        _applyPersonaChange(e.target.value, true);
    });

    // ARCH-114: on load, state.currentPersona (default '', per render.js)
    // never matched the selector's `selected` option — the heading, the
    // request payload and the visible dropdown disagreed about which persona
    // was active. A deliberate v2 choice may override the signed-in role's
    // server default. The legacy key is intentionally ignored: it was written
    // automatically on load and therefore is not evidence of user intent.
    function _syncPersonaFromSelector() {
        const preferenceKey = window.chatPersonaPreferenceKey || 'archie_chat_persona_v2';
        let saved = null;
        try {
            saved = localStorage.getItem(preferenceKey);
        } catch (error) {
            /* swallow-ok: localStorage is unavailable; the signed-in role default remains active */
        }
        if (saved && personaSelector.querySelector(`option[value="${CSS.escape(saved)}"]`)) {
            personaSelector.value = saved;
        } else if (
            window.defaultChatPersona &&
            personaSelector.querySelector(`option[value="${CSS.escape(window.defaultChatPersona)}"]`)
        ) {
            personaSelector.value = window.defaultChatPersona;
        }
        _applyPersonaChange(personaSelector.value, false);
    }

    function updatePersonaUI(persona, announce) {
        if (!persona || !personaConfig[persona]) {
            // Reset to default
            document.getElementById('domain-description').textContent = 'Select a domain for specialized assistance';
            return;
        }
        const config = personaConfig[persona];
        document.getElementById('domain-description').textContent = config.description || config.name;
        if (announce) {
            appendSystemMessage(`Persona switched to: ${config.name}`, 'info');
        }
    }

    function usePrompt(prompt) {
        userInput.value = prompt;
        userInput.focus();
    }

    function updateDomainUI(domain) {
        const config = domainConfig[domain];
        if (!config) return;

        // Update header
        document.getElementById('domain-title').textContent = config.name;
        document.getElementById('domain-description').textContent = config.description;

        // Update icon
        const iconElement = document.getElementById('domain-icon');
        iconElement.innerHTML = `<i data-lucide="${config.icon}" class="h-5 w-5"></i>`;

        // Update colors using safe class mapping
        const bgClass = getColorClass(config.color || 'primary', 'bg');
        iconElement.className = `flex h-9 w-9 items-center justify-center rounded-lg ${bgClass} text-primary-foreground transition-all duration-300`; // token-migration-ok

        // Re-initialize lucide icons
        lucide.createIcons();
    }

    /* updateTemplateOptions() lived here. template_name was validated and
       HTML-sanitised by chat_core and then discarded — AgentRunner.run() has no
       such parameter and nothing read it after line 362 — so the dropdown it
       filled, and the AIPromptTemplate query behind it, affected no answer ever
       produced. Removed rather than left as decoration. */

    function handleEnter(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            // Preserve validation and a cancelable submit event. Firefox can
            // navigate on an untrusted, non-cancelable synthetic submit.
            chatForm.requestSubmit();
        }
    }

    /* Enter sends; Shift+Enter newlines.
       handleEnter() (defined above) was written for this and never bound - there
       were zero keydown listeners in the file - so the composer was a plain
       3-row textarea where Enter inserted a newline and the ONLY way to send a
       message was clicking the button. That is the first thing a user tries, and
       it is also an accessibility defect: the chat could not be submitted from
       the keyboard at all. */
    userInput.addEventListener('keydown', handleEnter);

    /* Send and Stop swap places while a turn is in flight. Salvaged from
       ai_chat.js, the 169 KB file no template loads: #stop-btn has been in the
       markup all along with its handler in a file that never ran. */
    function _setSendingUI(sending) {
        const sendBtn = document.getElementById('send-btn');
        const stopBtn = document.getElementById('stop-btn');
        if (sendBtn) { sendBtn.disabled = sending; sendBtn.classList.toggle('hidden', sending); }
        if (stopBtn) { stopBtn.classList.toggle('hidden', !sending); }
    }

    document.getElementById('stop-btn')?.addEventListener('click', () => {
        ArchieChat.transport.abort();
        _setSendingUI(false);
    });

    // Enhanced form submission
    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        _submitTurn().finally(() => _setSendingUI(false));
    });

    async function _submitTurn() {
        const message = userInput.value.trim();
        if (!message) return;
        _setSendingUI(true);

        // Clear input
        userInput.value = '';
        userInput.style.height = 'auto';

        // Add user message to history
        const timestamp = new Date().toISOString();
        state.chatHistory.push({ role: 'user', content: message, timestamp });
        appendMessage('user', message);

        // Check for client-side commands (actionable architect workflows)
        if (message.startsWith('/')) {
            const handled = await handleChatCommand(message);
            if (handled) return;
        }

        // ENT-122: intercept NL ArchiMate intent before regular chat
        if (detectArchimateFreeformIntent(message)) {
            await handleArchimateFreeform(message);
            return;
        }

        await _deliverTurn(message, timestamp);
    }

    /* The network half of a turn, separated from _submitTurn so the error
       card's Retry can run it again without echoing the user's message into
       the transcript a second time. */
    async function _deliverTurn(message, timestamp) {
        // Show loading indicator
        const loadingId = 'loading-' + Date.now();
        const loadingDiv = document.createElement('div');
        loadingDiv.id = loadingId;
        loadingDiv.className = 'flex gap-4';
        const loadingGradient = getColorClass(domainConfig[state.currentDomain]?.color || 'primary', 'gradient');
        loadingDiv.innerHTML = `
            <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${loadingGradient} text-primary-foreground shadow-lg"><!-- token-migration-ok -->
                <i data-lucide="${domainConfig[state.currentDomain]?.icon || 'bot'}" class="h-5 w-5 animate-pulse"></i>
            </div>
            <div class="rounded-xl bg-muted/50 p-4 text-sm flex items-center gap-3">
                <div class="flex space-x-1">
                    <div class="w-2 h-2 bg-primary rounded-full animate-bounce" style="animation-delay: 0ms"></div>
                    <div class="w-2 h-2 bg-primary rounded-full animate-bounce" style="animation-delay: 150ms"></div>
                    <div class="w-2 h-2 bg-primary rounded-full animate-bounce" style="animation-delay: 300ms"></div>
                </div>
                <span class="text-muted-foreground">Thinking...</span>
            </div>
        `;
        messagesContainer.appendChild(loadingDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
        lucide.createIcons();

        const startTime = performance.now();
        // Shared payload. thread_id keeps multi-turn chats in one conversation.
        const _payload = JSON.stringify({
            message: message,
            domain: state.currentDomain,
            element_id: selectedElementIdInput.value || null,
            context_type: state.contextElement?.type || null,
            persona: state.currentPersona || null,
            model: document.getElementById('model-selector')?.value || null,
            thread_id: window.__threadId || null,
        });

        // Stream the reply token-by-token (like ChatGPT). If streaming can't be
        // established, fall through to the non-streaming request below.
        try { if (await streamAiReply(_payload, loadingId, startTime, timestamp)) return; }
        catch (_streamErr) {
            /* A user-initiated stop is not a transport failure: re-issuing the
               turn against the non-streaming endpoint would be the opposite of
               what Stop means. Everything else falls through. */
            if (_streamErr && _streamErr.name === 'AbortError') {
                /* Stop pressed before any token or done event arrived: the
                   "Thinking..." indicator is torn down only inside
                   streamAiReply's onToken/onDone (see there), neither of
                   which ran, so it is still on screen and would otherwise
                   read "Thinking..." forever. */
                const _l = document.getElementById(loadingId);
                if (_l) _l.remove();
                return;
            }
            if (_streamErr && _streamErr.name === 'ChatTimeoutError') {
                /* Nothing arrived within transport.js's idle window. Falling
                   back to the non-streaming endpoint here would just start a
                   second, equally-slow request behind the same silent
                   "Thinking..." indicator — render the timeout instead of
                   compounding the wait. */
                const _l = document.getElementById(loadingId);
                if (_l) _l.remove();
                appendError(_streamErr.message, () => _retryTurn(message, timestamp));
                return;
            }
            if (_streamErr && _streamErr.isConfigFault) {
                /* No LLM provider is configured. The non-streaming endpoint is
                   behind the same gate and would 503 identically, so falling
                   through only spends a second round trip to reach the same
                   answer. Show the remedy now. */
                const _l = document.getElementById(loadingId);
                if (_l) _l.remove();
                // L3: a missing provider is already announced by the persistent
                // page-level banner (ai_chat/index.html) for the whole session,
                // so a toast on top of the inline error below was a third
                // surface for one fault. Keep the inline message (it carries
                // the admin-link remedy in context); drop the toast.
                appendError(_streamErr.message, null, { adminLink: true });
                return;
            }
            /* fall through to non-streaming */
        }

        try {
            const response = await ArchieChat.transport.sendMessage(_payload);

            const data = await response.json();
            // Remember the conversation this turn was saved to, and refresh the
            // history rail so a brand-new chat appears immediately.
            if (data.thread_id) {
                const _wasNew = data.thread_id !== window.__threadId;
                window.__threadId = data.thread_id;
                try { sessionStorage.setItem('archie_chat_thread_id', data.thread_id); } catch (e) { /* swallow-ok: private-browsing storage denial; the server-saved thread and current page still work, only reload restoration is unavailable */ }
                if (_wasNew && window.loadSessionList) window.loadSessionList();
            }
            const endTime = performance.now();
            const processingTime = Math.round(endTime - startTime);

            // Remove loading (guard: may already be gone if streaming ran first)
            { const _l = document.getElementById(loadingId); if (_l) _l.remove(); }

            if (!response.ok || data.error) {
                /* A failure renders as a failure. It used to render as an AI
                   turn beginning "**⚠️ Error:**" — the assistant's avatar, the
                   assistant's bubble, and no way to try again. */
                let errorTitle = 'Error';
                let errorMessage = '';

                if (data.error) {
                    // Handle different error formats
                    if (typeof data.error === 'string') {
                        errorMessage = data.error;
                    } else if (typeof data.error === 'object' && data.error.message) {
                        // Marshmallow validation error or structured error
                        errorMessage = data.error.message;
                        if (data.error.details && Array.isArray(data.error.details) && data.error.details.length > 0) {
                            // Append first validation detail for context
                            const detail = data.error.details[0];
                            if (typeof detail === 'string') {
                                errorMessage += ': ' + detail;
                            } else if (detail.message) {
                                errorMessage += ': ' + detail.message;
                            }
                        }
                    }
                }

                if (data.error_type === 'auth') {
                    errorTitle = 'Authentication Error';
                } else if (data.error_type === 'connection') {
                    errorTitle = 'Connection Error';
                } else if (data.error_type === 'timeout') {
                    errorTitle = 'Timeout Error';
                } else if (data.error_type === 'rate_limit') {
                    errorTitle = 'Rate Limit';
                } else if (data.error_type === 'model_error') {
                    errorTitle = 'Model Error';
                } else if (!response.ok) {
                    errorTitle = 'Request Error (HTTP ' + response.status + ')';
                }

                /* `data.error` is often a machine CODE, not prose — the
                   no-provider gate returns {"error":"service_unavailable",
                   "message":"AI feature 'chat' is not available. LLM provider
                   must be configured."}. Rendering only `error` showed the
                   user "Request Error (HTTP 503): service_unavailable" and
                   threw away the one sentence that said what to do. Prefer the
                   human `message` whenever the code carries no spaces. */
                if (data.message && typeof data.message === 'string' &&
                    (!errorMessage || errorMessage.indexOf(' ') === -1)) {
                    errorMessage = data.message;
                }

                /* A missing provider is a configuration fault, not a transient
                   one: Retry can only reproduce it. Route the user to the
                   remedy instead. */
                const _isConfigFault =
                    ArchieChat.transport.isProviderConfigurationFault(data);

                const fullError = errorMessage ? `${errorTitle}: ${errorMessage}` : errorTitle;
                if (_isConfigFault) {
                    // L3: the persistent page-level banner (ai_chat/index.html)
                    // already announces "no LLM provider configured" for the
                    // whole session -- a toast for the same fault made three
                    // surfaces (banner + toast + inline) say one thing. The
                    // inline message stays; it carries the admin-link remedy
                    // in context, which the banner and a toast both lack.
                    appendError(errorMessage || fullError, null, { adminLink: true });
                } else {
                    Platform.toast.error(fullError);
                    appendError(fullError, () => _retryTurn(message, timestamp));
                }
            } else {
                // Add AI response to history
                state.chatHistory.push({
                    role: 'ai',
                    content: data.response,
                    timestamp,
                    metadata: data.metadata
                });

                appendMessage('ai', data.response, {
                    domain: data.domain,
                    personaLabel: _activePersonaLabel(),
                    processing_time: data.metadata?.processing_time || processingTime,
                    sources: data.sources,
                    /* The fallback must show the same footer as the streamed
                       path. It carries no tool_start/tool_result events — there
                       is no stream to carry them — so the trail is empty and the
                       evidence strip reports "context only" rather than
                       inventing lookups it cannot see. actions_taken does come
                       back on this path, so receipts and next-artifact are the
                       same either way. */
                    trail: [],
                    contextUsed: true,
                    actions: data.actions_taken || [],
                    pendingApprovals: data.pending_approvals || [],
                    /* agent_result.error survives internally even when the
                       route reports success:true (the endpoint always has an
                       answer to show — see chat_core.py) — agent_error is
                       that field surfaced, so this reads as a failure the
                       same way the streamed path does rather than as an
                       ordinary answer that happens to mention Admin
                       -> API Settings. */
                    error: data.agent_error || null
                });

                // Genome extraction — fires after every AI response when ?mode=genome
                if (window._genomePanelInstance) {
                    await window._genomePanelInstance.extractAfterMessage(state.chatHistory);
                }
            }

        } catch (error) {
            { const _l = document.getElementById(loadingId); if (_l) _l.remove(); }
            appendError(`Network error: ${error.message}`, () => _retryTurn(message, timestamp));
        }
    }

    function _retryTurn(message, timestamp) {
        _setSendingUI(true);
        _deliverTurn(message, timestamp).finally(() => _setSendingUI(false));
    }

    // Stream an AI reply via SSE. Renders tokens live into a bubble; returns true
    // when it rendered a reply (caller then stops), throws/returns false to let
    // the caller fall back to the non-streaming endpoint.
    async function streamAiReply(payload, loadingId, startTime, timestamp) {
        let wrap = null;
        // One turn's tool calls, in execution order. Rebuilt per turn so an
        // answer can never inherit the previous answer's evidence.
        const _trail = [];
        // The bubble is only DROPPED when the answer never arrived. On success
        // it is finalised in place — removing and rebuilding it is what made
        // every answer flash and the transcript jump on completion.
        const dropBubble = () => { if (wrap) { wrap.remove(); wrap = null; } };

        // The "Thinking..." indicator (built in _deliverTurn) stays on screen
        // until there is actual content to show it beside — either the first
        // token, or the final answer if none ever streamed. It used to be
        // torn down in onOpen, the instant the response headers arrived,
        // and replaced with an empty bubble holding nothing but a blinking
        // caret; a turn that failed with no token events (every _fallback()
        // path — no LLM configured, quota, timeout, ...) left that caret as
        // the only thing on screen for the whole 15s+ round trip, which read
        // as no indicator at all.
        const dropLoading = () => { const _l = document.getElementById(loadingId); if (_l) _l.remove(); };

        try {
            return await ArchieChat.transport.streamMessage(payload, {
                onOpen: () => {},
                onThreadId: (id) => {
                    const wasNew = id !== window.__threadId;
                    window.__threadId = id;
                    try { sessionStorage.setItem('archie_chat_thread_id', id); } catch (e) { /* swallow-ok: private-browsing storage denial; the server-saved thread and current page still work, only reload restoration is unavailable */ }
                    if (wasNew && window.loadSessionList) window.loadSessionList();
                },
                /* The evidence trail. The server has always emitted these and
                   the client always discarded them; they are what lets the UI
                   state coverage structurally ("47 matched, showing 15") rather
                   than trusting the model to mention it. */
                onToolStart: (ev) => { _trail.push({ tool: ev.tool, args: ev.args, result: null }); },
                onToolResult: (ev) => {
                    for (let i = _trail.length - 1; i >= 0; i--) {
                        if (_trail[i].tool === ev.tool && _trail[i].result === null) {
                            _trail[i].result = ev.result;
                            return;
                        }
                    }
                    _trail.push({ tool: ev.tool, args: null, result: ev.result });
                },
                onToken: (_text, full) => {
                    if (!wrap) { dropLoading(); wrap = beginStreamedMessage(); }
                    updateStreamedMessage(wrap, full);
                },
                onDone: async (result) => {
                    dropLoading();
                    if (!wrap) wrap = beginStreamedMessage();
                    const meta = {
                        domain: result.domain || state.currentDomain,
                        personaLabel: _activePersonaLabel(),
                        processing_time: Math.round(performance.now() - startTime),
                        sources: result.sources,
                        trail: _trail,
                        actions: result.actions || [],
                        pendingApprovals: result.pendingApprovals || [],
                        // No tool ran, but the domain snapshot was in the prompt:
                        // that is "context only", not "ungrounded".
                        contextUsed: _trail.length === 0,
                        // The server's own "couldn't be completed" text,
                        // persisted and streamed back rather than thrown away
                        // — see transport.js's done handling. Non-null here
                        // means result.text is that message, not an answer.
                        error: result.error || null
                    };
                    state.chatHistory.push({ role: 'ai', content: result.text, timestamp, metadata: meta });
                    finaliseStreamedMessage(wrap, result.text, meta);
                    wrap = null;   // finalised in place; nothing left to drop
                    // Genome panel extraction is an optional side-panel enrichment;
                    // the chat turn above already completed and must not be undone by this failing.
                    if (window._genomePanelInstance) { try { await window._genomePanelInstance.extractAfterMessage(state.chatHistory); } catch (_) { /* swallow-ok: optional side-panel enrichment run after the answer is already on screen; an error toast here would read as the answer having failed when it did not */ } }
                }
            });
        } finally {
            dropBubble();
        }
    }

    // ==========================================================================
    // Document Upload Panel Toggle
    // ==========================================================================

    // UIQA-004: /ai-chat/document-upload redirects here with ?panel=docs
    if (new URLSearchParams(window.location.search).get('panel') === 'docs') {
        document.addEventListener('DOMContentLoaded', function() {
            toggleDocumentUploadPanel();
        });
    }

    function toggleDocumentUploadPanel() {
        const panel = document.getElementById('document-upload-panel');
        if (panel) {
            panel.classList.toggle('hidden');
            // Reinitialize Lucide icons when panel opens
            if (!panel.classList.contains('hidden') && typeof lucide !== 'undefined') {
                setTimeout(() => lucide.createIcons(), 100);
            }
        }
    }

    // ==========================================================================
    // Mobile Sidebar Toggle
    // ==========================================================================

    function toggleSidebar() {
        const sidebar = document.getElementById('context-sidebar');
        const backdrop = document.getElementById('sidebar-backdrop');
        const isOpen = !sidebar.classList.contains('-translate-x-full');

        if (isOpen) {
            // Close sidebar
            sidebar.classList.add('-translate-x-full');
            backdrop.classList.add('hidden');
            document.body.classList.remove('overflow-hidden');
        } else {
            // Open sidebar
            sidebar.classList.remove('-translate-x-full');
            backdrop.classList.remove('hidden');
            document.body.classList.add('overflow-hidden');
            // Ensure sidebar is visible on mobile
            sidebar.style.height = '100%';
        }
        setTimeout(() => lucide.createIcons(), 100);
    }

    // =========================================================================
    // CSP-safe event delegation — handles all dynamically-injected data-action buttons
    // =========================================================================
    document.addEventListener('click', (event) => {
        // --- quick-query (sidebar NL query suggestions) ---
        const quickQueryButton = event.target.closest('[data-quick-query]');
        if (quickQueryButton) {
            event.preventDefault();
            runQuickQuery(quickQueryButton.dataset.quickQuery);
            return;
        }

        const btn = event.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.dataset.action;

        // --- sample prompt ---
        if (action === 'use-prompt') {
            usePrompt(btn.dataset.prompt);
            return;
        }

        // --- select context item (architecture/technology panel) ---
        if (action === 'select-context') {
            selectContext(parseInt(btn.dataset.id), btn.dataset.type);
            return;
        }

        /* apply-ai-action and dismiss-action-card lived here. Their only
           producer was renderActionCard, on the dead handleCommand path, so
           neither branch could ever fire. Removed with it. */

        // --- apply APQC mappings ---
        if (action === 'apply-apqc') {
            applyApqcMappings(parseInt(btn.dataset.appId), btn.dataset.highConf === '1');
            return;
        }

        // --- gap analysis tab switch ---
        if (action === 'gap-analysis-tab') {
            handleGapAnalysis(btn.dataset.analysisType);
            return;
        }

        // --- rediscover vendors ---
        if (action === 'discover-vendors') {
            handleDiscoverVendors(btn.dataset.capability);
            return;
        }

        // --- generate architect viewpoints ---
        if (action === 'architect-viewpoints') {
            handleArchitectViewpoints(btn.dataset.solutionId);
            return;
        }

        // --- check ARB readiness ---
        if (action === 'arb-ready') {
            handleArbReady(btn.dataset.solutionId);
            return;
        }

        // --- show entity in chat (search sidebar results) ---
        if (action === 'show-entity') {
            showEntityInChat(btn.dataset.entityType, parseInt(btn.dataset.entityId), btn.dataset.entityName);
            return;
        }

        // --- entity action modal: close ---
        if (action === 'close-entity-modal') {
            const modal = document.getElementById('entity-action-modal');
            if (modal) modal.remove();
            return;
        }

        // --- entity action modal: navigate to entity page ---
        if (action === 'navigate-entity') {
            navigateToEntity(btn.dataset.entityType, parseInt(btn.dataset.entityId));
            const modal = document.getElementById('entity-action-modal');
            if (modal) modal.remove();
            return;
        }

        // --- entity action modal: ask AI about entity ---
        if (action === 'ask-ai-entity') {
            askAIAboutEntity(btn.dataset.entityType, parseInt(btn.dataset.entityId), btn.dataset.entityName);
            const modal = document.getElementById('entity-action-modal');
            if (modal) modal.remove();
            return;
        }

        // --- entity action modal: add entity to context ---
        if (action === 'add-context-entity') {
            addToContext(btn.dataset.entityType, parseInt(btn.dataset.entityId), btn.dataset.entityName);
            const modal = document.getElementById('entity-action-modal');
            if (modal) modal.remove();
            return;
        }
    });

    function showEntityInChat(entityType, entityId, entityName) {
        // Show options modal for what to do with this entity
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        modal.id = 'entity-action-modal';
        modal.onclick = (e) => { if (e.target === modal) modal.remove(); };

        modal.innerHTML = `
            <div class="bg-background rounded-lg border shadow-xl p-6 max-w-md w-full mx-4">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="font-semibold text-lg">${entityName}</h3>
                    <button aria-label="Close" type="button" data-action="close-entity-modal" class="text-muted-foreground hover:text-foreground">
                        <i data-lucide="x" class="h-5 w-5"></i>
                    </button>
                </div>
                <p class="text-sm text-muted-foreground mb-4">${entityType} • ID: ${entityId}</p>
                <div class="space-y-2">
                    <button type="button" data-action="navigate-entity" data-entity-type="${entityType}" data-entity-id="${entityId}"
                        class="w-full flex items-center gap-3 p-3 rounded-lg border hover:bg-accent transition-colors text-left">
                        <div class="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center"><!-- token-migration-ok -->
                            <i data-lucide="external-link" class="h-5 w-5 text-primary"></i><!-- token-migration-ok -->
                        </div>
                        <div>
                            <div class="font-medium text-sm">View Details</div>
                            <div class="text-xs text-muted-foreground">Open the full ${entityType.toLowerCase()} page</div>
                        </div>
                    </button>
                    <button type="button" data-action="ask-ai-entity" data-entity-type="${entityType}" data-entity-id="${entityId}" data-entity-name="${entityName.replace(/"/g, '&quot;')}"
                        class="w-full flex items-center gap-3 p-3 rounded-lg border hover:bg-accent transition-colors text-left">
                        <div class="h-10 w-10 rounded-lg bg-purple-100 flex items-center justify-center"><!-- token-migration-ok -->
                            <i data-lucide="bot" class="h-5 w-5 text-primary"></i><!-- token-migration-ok -->
                        </div>
                        <div>
                            <div class="font-medium text-sm">Ask AI</div>
                            <div class="text-xs text-muted-foreground">Get AI analysis and insights</div>
                        </div>
                    </button>
                    <button type="button" data-action="add-context-entity" data-entity-type="${entityType}" data-entity-id="${entityId}" data-entity-name="${entityName.replace(/"/g, '&quot;')}"
                        class="w-full flex items-center gap-3 p-3 rounded-lg border hover:bg-accent transition-colors text-left">
                        <div class="h-10 w-10 rounded-lg bg-emerald-500/10 flex items-center justify-center"><!-- token-migration-ok -->
                            <i data-lucide="plus-circle" class="h-5 w-5 text-emerald-600"></i><!-- token-migration-ok -->
                        </div>
                        <div>
                            <div class="font-medium text-sm">Add to Context</div>
                            <div class="text-xs text-muted-foreground">Use as context for AI queries</div>
                        </div>
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        lucide.createIcons();
    }

    function navigateToEntity(entityType, entityId) {
        // Map entity types to their detail URLs
        const urlMap = {
            'Application': `/applications/${entityId}`,
            'application': `/applications/${entityId}`,
            'Capability': `/capabilities/${entityId}`,
            'capability': `/capabilities/${entityId}`,
            'Vendor': `/vendors/${entityId}`,
            'vendor': `/vendors/${entityId}`,
            'Process': `/processes/${entityId}`,
            'process': `/processes/${entityId}`,
            'Technology': `/technologies/${entityId}`,
            'technology': `/technologies/${entityId}`
        };

        const url = urlMap[entityType] || `/applications/${entityId}`;
        window.open(url, '_blank');
    }

    function askAIAboutEntity(entityType, entityId, entityName) {
        const prompt = `Analyze "${entityName}" (${entityType}, ID: ${entityId}).\n\nPlease provide:\n1. Overview and current status\n2. Key relationships and dependencies\n3. Risk assessment\n4. Improvement recommendations`;

        userInput.value = prompt;
        chatForm.requestSubmit();
    }

    function addToContext(entityType, entityId, entityName) {
        selectedElementIdInput.value = entityId;
        state.contextElement = { id: entityId, type: entityType, name: entityName };
        appendSystemMessage(`Context set: ${entityName} (${entityType})`, 'info');
    }

    function selectDomainAndPrompt(domain, samplePrompt) {
        // Switch the domain selector
        domainSelector.value = domain;
        state.currentDomain = domain;

        // Update UI
        updateDomainUI(domain);
        loadDomainContext(domain);
        // Set the sample prompt in the input field
        userInput.value = samplePrompt;
        userInput.focus();

        // Show a system message about the switch
        const domainName = domainConfig[domain]?.name || domain;
        appendSystemMessage(`Switched to ${domainName}. Press Enter or click Send to use the sample prompt, or type your own question.`, 'info');
    }

    // Wire domain cards via addEventListener (CSP blocks inline onclick)
    document.querySelectorAll('.domain-card[data-domain]').forEach(function(card) {
        card.addEventListener('click', function() {
            selectDomainAndPrompt(this.dataset.domain, this.dataset.prompt);
        });
    });

    // AI-1: wire the welcome-screen AI Architect persona cards. Setting the
    // persona selector + dispatching 'change' runs the existing handler,
    // which sets state.currentPersona, the persona's default domain, and
    // the role's sample prompts.
    function selectArchitectPersona(persona, samplePrompt) {
        if (personaSelector) {
            // Only dispatch 'change' -- and so only announce "Persona switched to: ..." -- when the
            // value actually changes. Unlike a real <select> pick (which fires no event for a re-pick
            // of the already-selected option), setting .value programmatically and always dispatching
            // announced a switch even when clicking the card for the persona already active.
            const changed = personaSelector.value !== persona;
            personaSelector.value = persona;
            if (changed) personaSelector.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            state.currentPersona = persona;
        }
        if (userInput && samplePrompt) {
            userInput.value = samplePrompt;
            userInput.focus();
        }
        const label = (personaConfig && personaConfig[persona] && personaConfig[persona].name) || persona;
        // Best-effort welcome banner; persona selection above already took effect either way.
        try { appendSystemMessage(`${label} engaged — grounded in live platform data. Press Enter to use the suggested prompt, or ask your own.`, 'info'); } catch (e) { /* swallow-ok: cosmetic welcome banner; the persona switch above already took effect, so failing to print the banner is not a failure the user needs to act on */ }
    }
    document.querySelectorAll('.architect-card[data-persona]').forEach(function(card) {
        card.addEventListener('click', function() {
            selectArchitectPersona(this.dataset.persona, this.dataset.prompt);
        });
    });

    // ==========================================================================
    // Create Solution from Brief — modal handler
    // ==========================================================================

    function openCreateSolutionModal() { openCreateSolutionModalWithMessage(''); }
    function openCreateSolutionModalWithMessage(message) {
        let modal = document.getElementById('ai-chat-create-solution-modal');
        if (modal) modal.classList.remove('hidden');
        const briefEl = document.getElementById('ai-chat-solution-brief');
        if (briefEl && typeof message === 'string' && message.trim()) briefEl.value = message.trim();
        const titleEl = document.getElementById('ai-chat-solution-title');
        if (titleEl) titleEl.focus();
    }
    function closeCreateSolutionModal() {
        let modal = document.getElementById('ai-chat-create-solution-modal');
        if (modal) modal.classList.add('hidden');
    }

    // Wire the button
    const createSolBtn = document.getElementById('ai-chat-create-solution-btn');
    if (createSolBtn) createSolBtn.addEventListener('click', openCreateSolutionModal);
    const createSolCancel = document.getElementById('ai-chat-create-solution-cancel');
    if (createSolCancel) createSolCancel.addEventListener('click', closeCreateSolutionModal);
    const createSolForm = document.getElementById('ai-chat-create-solution-form');
    if (createSolForm) {
        createSolForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const titleEl = document.getElementById('ai-chat-solution-title');
            const briefEl = document.getElementById('ai-chat-solution-brief');
            const statusEl = document.getElementById('ai-chat-create-solution-status');
            const submitBtn = createSolForm.querySelector('button[type="submit"]');
            let title = (titleEl && titleEl.value || '').trim();
            const brief = (briefEl && briefEl.value || '').trim();
            if (!title || !brief) return;
            if (submitBtn) submitBtn.disabled = true;
            if (statusEl) { statusEl.classList.remove('hidden'); statusEl.textContent = 'Creating solution and generating draft…'; }
            try {
                const data = await Platform.fetch.post('/solutions/create-with-draft', 
                    { title: title, brief: brief },
                    { silent: true }
                );
                if (submitBtn) submitBtn.disabled = false;
                if (data.redirect) {
                    closeCreateSolutionModal();
                    appendSystemMessage('Solution created — redirecting to detail page…', 'info');
                    setTimeout(function() { window.location.href = data.redirect; }, 800);
                } else {
                    if (statusEl) statusEl.textContent = 'Error: ' + (data.error || 'Could not create solution');
                }
            } catch (err) {
                if (submitBtn) submitBtn.disabled = false;
                if (statusEl) statusEl.textContent = 'Network error: ' + (err.message || 'Request failed');
            }
        });
    }

    // ==========================================================================
    // Document Upload Event Listeners
    // ==========================================================================

    // Listen for document analysis results to add to chat
    window.addEventListener('add-document-analysis', (e) => {
        const { filename, summary, elementsFound, elementsCreated, confidence } = e.detail || {};
        const message = `**Document Analyzed: ${filename || 'Unknown'}**

${summary || 'Document has been analyzed.'}

**Results:**
- Elements Found: ${elementsFound || 0}
- Elements Created: ${elementsCreated || 0}
- Confidence: ${confidence || 'Medium'}

Would you like me to provide more details about the extracted elements or help you with follow-up questions?`;
        appendMessage('ai', message, { domain: state.currentDomain });
    });

    // Listen for notification events
    window.addEventListener('show-notification', (e) => {
        const { type, message } = e.detail || {};
        appendSystemMessage(message || 'Notification', type || 'info');
    });

    // Listen for questions from document panel
    window.addEventListener('ask-question', (e) => {
        const { question, context } = e.detail || {};
        if (question) {
            userInput.value = question;
            if (context) {
                userInput.value += `\n\n[Context: ${context}]`;
            }
            chatForm.requestSubmit();
        }
    });

    // Listen for entity linking requests
    window.addEventListener('link-to-entity', (e) => {
        const { entityType, entityId, entityName, documentId } = e.detail || {};
        appendSystemMessage(`Linking document to ${entityType}: ${entityName} (ID: ${entityId})`, 'info');
    });

    // Listen for entity creation requests
    window.addEventListener('create-entity', (e) => {
        const { entityType, entityData } = e.detail || {};
        appendSystemMessage(`Creating new ${entityType}: ${entityData?.name || 'Unknown'}`, 'info');
    });

    // ==========================================================================
    // Initialize on Load
    // ==========================================================================

    // Initialize on load
    document.addEventListener('DOMContentLoaded', () => {
        _syncPersonaFromSelector();
        updateDomainUI(state.currentDomain);
        loadDomainContext(state.currentDomain);
        loadAvailableModels(); // Load available models

        // Load initial alert count
        loadRecommendations();
        // The opening screen reads this tenant's portfolio rather than asking
        // the user to supply the agenda.
        ArchieChat.panels.loadPortfolioBriefing();

        /* The welcome grid used to be rebuilt here: messagesContainer.innerHTML
           overwrote the server-rendered grid with a second copy carrying
           different prompt strings, and both copies' cards were wired, so
           every card had two click handlers. The server-rendered one is the
           one that survives; the wiring above it runs once. */
        lucide.createIcons();
        // ARCH-112: #suggestion-chips now wraps instead of scrolling
        // horizontally (index.html), so the right-edge scroll-fade this
        // block used to maintain no longer applies — removed with it.

        // A95-005: Handle deep-link context params from entity detail pages.
        // Supports:
        //   ?context=application&id=<app_id>   (application detail page)
        //   ?element_id=<id>&context_type=vendor&domain=vendor_intelligence  (vendor detail "Ask AI")
        const urlParams = new URLSearchParams(window.location.search);
        const deepLinkContext = urlParams.get('context');
        const deepLinkId = urlParams.get('id');
        const elementId = urlParams.get('element_id');
        const contextType = urlParams.get('context_type');
        const deepLinkDomain = urlParams.get('domain');

        if (deepLinkContext === 'application' && deepLinkId && !isNaN(parseInt(deepLinkId))) {
            const appId = parseInt(deepLinkId);
            selectedElementIdInput.value = appId;
            state.contextElement = { id: appId, type: 'application' };
            if (domainSelector) {
                domainSelector.value = 'technology';
                state.currentDomain = 'technology';
                updateDomainUI('technology');
                loadDomainContext('technology');
            }
            if (userInput) {
                userInput.value = `/generate-archimate ${appId}`;
            }
            // A deep link arrives ready to act, not to browse the suggestion cards —
            // collapse them (keeping the greeting heading itself visible) as belt-and-
            // braces, and insert the notice above the greeting (placement: 'top') rather
            // than scrolling to it, so it lands inside the pane's visible area instead of
            // hundreds of px below the ~1000px of persona/domain cards.
            _hideWelcomeSuggestions();
            appendSystemMessage(`Application context loaded (ID: ${appId}). Ready to generate ArchiMate model.`, 'info', { placement: 'top' });
        } else if (elementId && contextType && !isNaN(parseInt(elementId))) {
            // Generic entity deep-link: ?element_id=<id>&context_type=<type>&domain=<domain>
            const entityId = parseInt(elementId);
            selectedElementIdInput.value = entityId;
            state.contextElement = { id: entityId, type: contextType };
            const targetDomain = deepLinkDomain || 'general';
            if (domainSelector && domainConfig[targetDomain]) {
                domainSelector.value = targetDomain;
                state.currentDomain = targetDomain;
                updateDomainUI(targetDomain);
                loadDomainContext(targetDomain);
            }
            // Build a context-aware welcome message
            const entityLabel = contextType.charAt(0).toUpperCase() + contextType.slice(1).replace(/_/g, ' ');
            if (userInput && userInput.value === '') {
                if (contextType === 'vendor') {
                    userInput.value = `Analyze this vendor — what are the key risks, capability gaps, and strategic recommendations?`;
                }
            }
            // Same reasoning as the application deep-link branch above.
            _hideWelcomeSuggestions();
            appendSystemMessage(`${entityLabel} context loaded (ID: ${entityId}). Ask me anything about this ${contextType.replace(/_/g, ' ')}.`, 'info', { placement: 'top' });
        }
    });

    // ==========================================================================
    // Export Conversation (AIC-EXPORT)
    // ==========================================================================

    /* The Export button in the header has been calling this since it was added.
       The implementation existed only in app/static/js/ai_chat.js, which no
       template ever loaded, so every click raised an Alpine expression error
       while the feature was listed as working. Ported from there unchanged. */
    function exportConversation() {
        if (state.chatHistory.length === 0) {
            appendSystemMessage('No conversation to export', 'info');
            return;
        }
        let lines = [
            '# Entelim Architecture Chat Export',
            '**Exported:** ' + new Date().toLocaleString(),
            '**Domain:** ' + (domainConfig[state.currentDomain] && domainConfig[state.currentDomain].name || state.currentDomain),
            '**Persona:** ' + (personaConfig[state.currentPersona] && personaConfig[state.currentPersona].name || state.currentPersona || 'Not set'),
            '',
            '---',
            ''
        ];
        state.chatHistory.forEach(function(msg) {
            if (msg.role === 'user') {
                lines.push('## 🧑 User');
                lines.push(msg.content);
                lines.push('');
            } else if (msg.role === 'ai' || msg.role === 'assistant') {
                lines.push('## 🤖 Entelim');
                lines.push(msg.content);
                lines.push('');
            }
        });
        lines.push('---');
        lines.push('*Generated by Entelim Enterprise Architecture AI Assistant*');

        let blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
        let url = URL.createObjectURL(blob);
        let a = document.createElement('a');
        a.href = url;
        a.download = 'entelim-chat-' + new Date().toISOString().slice(0, 10) + '.md';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        appendSystemMessage('Conversation exported as Markdown', 'info');
    }

    /* Alpine expressions and the markup resolve these off window. */
    window.exportConversation = exportConversation;
    window.toggleSidebar = toggleSidebar;
    window.toggleDocumentUploadPanel = toggleDocumentUploadPanel;
    window.setSuggestion = setSuggestion;
    window.usePrompt = usePrompt;
})(window, document);

/* Conversation history rail — wired into the LIVE inline chat. Defines the
   functions the template buttons call (loadSessionList / startNewConversation),
   plus open/delete, and keeps window.__threadId in sync so multi-turn chats
   stay in one conversation. Backed by the /ai-chat/threads API. */
(function () {
  var transport = window.ArchieChat.transport;
  var render = window.ArchieChat.render;
  function esc(s) { var d = document.createElement('div'); d.textContent = (s == null ? '' : String(s)); return d.innerHTML; }

  window.loadSessionList = async function () {
    var c = document.getElementById('session-list');
    if (!c) return;
    c.innerHTML = '<div class="text-center py-4"><i data-lucide="loader-2" class="h-5 w-5 animate-spin mx-auto"></i></div>';
    if (window.lucide) lucide.createIcons();
    try {
      var r = await transport.listThreads();
      /* Without this check a 500 parses as `{}`, `j.threads` is undefined, and
         the rail renders "No conversations yet" — the user is told their chat
         history is empty when in fact it could not be read. */
      if (!r.ok) throw new Error('Could not load conversations (HTTP ' + r.status + ')');
      var j = await r.json();
      var t = (j && j.threads) || [];
      if (!t.length) {
        c.innerHTML = '<p class="text-xs text-muted-foreground text-center py-8">No conversations yet. Ask anything below — your chats are saved here automatically.</p>';
        return;
      }
      c.innerHTML = t.map(function (x) {
        var active = (x.id === window.__threadId) ? ' bg-accent' : '';
        // An unparsable timestamp renders as an em dash, never as a blank that
        // would read as "this conversation has no date".
        var d = '—'; try { d = new Date(x.updated_at || x.created_at).toLocaleDateString(); } catch (e) { /* swallow-ok: date formatting of one row; the em dash above already tells the user the date is unknown */ }
        return '<div class="js-thr group relative flex items-center rounded-lg border border-border hover:bg-accent/50 transition-colors' + active + '" data-id="' + esc(x.id) + '">' +
          '<button type="button" class="js-thr-open flex-1 text-left p-3 min-w-0">' +
          '<div class="font-medium text-xs truncate">' + esc(x.title || 'New chat') + '</div>' +
          '<div class="text-xs text-muted-foreground mt-1">' + esc(d) + (x.message_count ? ' · ' + x.message_count + ' messages' : '') + '</div></button>' +
          '<button type="button" class="js-thr-del opacity-0 group-hover:opacity-100 shrink-0 p-2 text-muted-foreground hover:text-destructive" title="Delete conversation" aria-label="Delete conversation"><i data-lucide="trash-2" class="h-3.5 w-3.5"></i></button></div>';
      }).join('');
      c.querySelectorAll('.js-thr').forEach(function (row) {
        var id = row.getAttribute('data-id');
        var o = row.querySelector('.js-thr-open'); if (o) o.addEventListener('click', function () { window.loadSession(id); });
        var del = row.querySelector('.js-thr-del'); if (del) del.addEventListener('click', function (e) { e.stopPropagation(); window.deleteConversation(id); });
      });
      if (window.lucide) lucide.createIcons();
    } catch (e) {
      c.innerHTML = '<p class="text-xs text-destructive text-center py-4">Couldn\'t load conversations.</p>';
    }
  };

  window.loadSession = async function (id) {
    if (!id) return;
    try {
      var r = await transport.loadThread(id);
      if (!r.ok) {
        if (window.Platform && window.Platform.toast) window.Platform.toast.error('Could not load that conversation.');
        return;
      }
      var j = await r.json();
      var msgs = (j && j.messages) || [];
      window.__threadId = id;
      var wg = document.getElementById('domain-welcome-grid'); if (wg) wg.classList.add('hidden');
      var mc = document.getElementById('messages-container'); if (mc) mc.innerHTML = '';
      /* Resolved through the module, and deliberately unguarded.
         appendMessage used to be a window global — the old template's classic
         <script> hoisted every top-level function onto window — so
         `typeof appendMessage === 'function'` was true. Inside an IIFE it is
         always false, so this loop silently rendered nothing: opening a past
         conversation cleared the pane and left it empty, while __threadId above
         had already switched, so the next message continued a thread the user
         could not see. A guard that can be false for a module this file depends
         on is not a safety net, it is the bug. */
      msgs.forEach(function (m) {
        render.appendMessage(
          m.role === 'assistant' ? 'ai' : m.role,
          m.content,
          { domain: window.ArchieChat.state.currentDomain || 'general' }
        );
      });
      window.loadSessionList();
      if (mc) mc.scrollTop = mc.scrollHeight;
    } catch (e) {
      if (window.Platform && window.Platform.toast) window.Platform.toast.error('Could not load that conversation.');
    }
  };

  window.deleteConversation = async function (id) {
    if (!id) return;
    try {
      await transport.deleteThread(id);
    } catch (e) {
      // The thread was NOT deleted server-side — do not proceed to
      // startNewConversation()/refresh below, or the user is left believing
      // the delete worked while the conversation is still there.
      if (window.Platform && window.Platform.toast) window.Platform.toast.error('Could not delete that conversation.');
      return;
    }
    if (id === window.__threadId) { window.startNewConversation(); return; }
    window.loadSessionList();
  };

  // "New chat" — a fresh page load is the most reliable reset for the inline
  // chat (welcome grid, in-page state, and __threadId all reset cleanly).
  window.startNewConversation = function () {
    window.__threadId = null;
    try { sessionStorage.removeItem('archie_chat_thread_id'); } catch (e) { /* swallow-ok: best-effort cleanup, page reload below resets state regardless */ }
    window.location.href = window.location.pathname;
  };

  // Auto-load the rail so past chats are visible without clicking Refresh; failure here
  // just means the sidebar list stays empty until the next successful call, non-fatal to boot.
  // ARCH-115: also restore the active thread on reload — previously boot() only populated the
  // sidebar list, never re-opened the conversation itself, so a reload (including one forced by
  // ARCH-001's outage) always landed on the empty "How can I help you today?" state even though
  // the thread was safely persisted server-side and reachable via the Chats panel.
  function boot() {
    try { window.loadSessionList(); } catch (e) { /* swallow-ok: loadSessionList renders its own "Couldn't load conversations" state; this guard only stops a throw at page load from aborting the rest of the script */ }
    try {
      var savedId = sessionStorage.getItem('archie_chat_thread_id');
      if (savedId) window.loadSession(savedId);
    } catch (e) { /* swallow-ok: storage may be unavailable (private browsing); user still reaches the Chats panel manually */ }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
