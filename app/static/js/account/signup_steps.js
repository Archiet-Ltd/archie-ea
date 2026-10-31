/*
 * Sign-up after the account: each source starts its import the moment it is given, the choices are
 * saved when the person moves on, and nothing here waits on a slow job. Plain script driven by data
 * attributes, so it runs under the CSP-safe evaluator.
 */
(function () {
    'use strict';

    var root = document.querySelector('[data-signup-steps]');
    if (!root) return;

    var URLS = {
        github: root.getAttribute('data-url-github'),
        jira: root.getAttribute('data-url-jira'),
        website: root.getAttribute('data-url-website'),
        choices: root.getAttribute('data-url-choices'),
        profile: root.getAttribute('data-url-profile'),
        status: root.getAttribute('data-url-status'),
        test: root.getAttribute('data-url-ai-test')
    };

    function post(url, body) {
        return window.Platform.fetch.post(url, body, { silent: true });
    }

    function value(id) {
        var el = document.getElementById(id);
        return el ? el.value : '';
    }

    function say(name, text, failed) {
        var line = root.querySelector('[data-say="' + name + '"]');
        if (!line) return;
        line.textContent = text;
        line.classList.toggle('text-destructive-emphasis', !!failed);
        line.classList.toggle('text-muted-foreground', !failed);
    }

    function busy(button, on) {
        if (!button) return;
        button.disabled = on;
        button.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    function link(label, href, testid) {
        var template = root.querySelector('template[data-reading-link]');
        if (!template) return null;
        var anchor = template.content.firstElementChild.cloneNode(true);
        anchor.textContent = label;
        anchor.setAttribute('href', href);
        anchor.setAttribute('data-testid', testid);
        return anchor;
    }

    // The website reading is more than a sentence: a bar of pages read against the limit, and the next step
    // (confirm what was read) or what to do instead when the site could not be read.
    function paintReading(row) {
        var block = root.querySelector('[data-reading]');
        if (!block) return;
        var bar = block.querySelector('[data-reading-bar]');
        if (bar) {
            var shown = row.state === 'reading' && row.progress;
            bar.classList.toggle('hidden', !shown);
            var fill = bar.querySelector('[role="progressbar"]');
            if (shown && fill) {
                fill.setAttribute('aria-valuenow', String(row.progress.percent));
                fill.style.transform = 'translateX(-' + (100 - row.progress.percent) + '%)';
            }
        }
        var actions = block.querySelector('[data-reading-actions]');
        if (!actions) return;
        var wanted = [];
        if (row.next) wanted.push(link(row.next.label, row.next.url, 'website-next'));
        (row.instead || []).forEach(function (item) { wanted.push(link(item.label, item.url, 'website-instead')); });
        var signature = JSON.stringify([row.next || null, row.instead || null]);
        if (actions.getAttribute('data-signature') === signature) return;
        actions.setAttribute('data-signature', signature);
        actions.textContent = '';
        wanted.forEach(function (anchor) { if (anchor) actions.appendChild(anchor); });
    }

    var announced = false;
    function paint(sources) {
        (sources || []).forEach(function (row) {
            var line = root.querySelector('[data-source="' + row.key + '"]');
            if (!line || row.state === 'not_given') return;
            line.textContent = row.text;
            line.setAttribute('data-state', row.state);
            line.classList.toggle('text-destructive-emphasis', row.state === 'failed');
            line.classList.toggle('text-muted-foreground', row.state !== 'failed');
            if (row.key === 'website') {
                paintReading(row);
                if (row.state === 'done' && row.next && !announced && window.Platform && window.Platform.toast) {
                    announced = true;
                    window.Platform.toast.success(row.text);
                }
            }
        });
    }

    var timer = null;
    function poll() {
        if (timer) return;
        timer = window.setInterval(function () {
            window.Platform.fetch.get(URLS.status, null, { silent: true }).then(function (body) {
                paint(body.sources);
                var site = (body.sources || []).filter(function (row) { return row.key === 'website'; })[0];
                if (site && (site.state === 'done' || site.facts_ready) && root.hasAttribute('data-reload-when-read')) {
                    saveEverything().then(function () { window.location.reload(); }).catch(function () { window.location.reload(); });
                }
                var open = (body.sources || []).some(function (row) { return row.state === 'waiting' || row.state === 'reading'; });
                if (!open) { window.clearInterval(timer); timer = null; }
            }).catch(function () { /* swallow-ok: a missed poll is retried on the next tick; the last line stays on screen */ });
        }, 3000);
    }

    // Sources being given right now: Continue waits for them, so leaving the page cannot cut a save short.
    var giving = [];

    function give(name, url, body, button) {
        busy(button, true);
        say(name, 'Connecting…', false);
        var sent = post(url, body).then(function (result) {
            paint(result.sources);
            poll();
        }).catch(function (error) {
            say(name, error.message || 'That did not work. Check it and try again.', true);
        }).then(function () {
            busy(button, false);
            giving = giving.filter(function (item) { return item !== sent; });
        });
        giving.push(sent);
    }

    function chosen() {
        var boxes = root.querySelectorAll('[data-framework]');
        var picked = [];
        boxes.forEach(function (box) { if (box.checked) picked.push(box.value); });
        return { kind: value('welcome-kind'), industry: value('welcome-industry'), frameworks: picked };
    }

    function answers(withFacts) {
        var body = { size_band: value('welcome-size'), role: value('welcome-role'), goal: value('welcome-goal'), facts: [] };
        if (withFacts) {
            root.querySelectorAll('[data-fact]').forEach(function (row) {
                var keep = row.querySelector('[data-fact-keep]');
                var edit = row.querySelector('[data-fact-value]');
                body.facts.push({ id: row.getAttribute('data-fact'), accept: !!(keep && keep.checked), value: edit ? edit.value : '' });
            });
        }
        return body;
    }

    function saveEverything() {
        return post(URLS.choices, chosen()).then(function () { return post(URLS.profile, answers(true)); });
    }

    // Offer the frameworks that suit the goal and the company kind; ones already ticked stay.
    function suggest() {
        var group = root.querySelector('[data-suggest]');
        if (!group) return;
        var map = JSON.parse(group.getAttribute('data-suggest'));
        var wanted = (map.goal[value('welcome-goal')] || []).concat(map.kind[value('welcome-kind')] || []);
        group.querySelectorAll('[data-framework]').forEach(function (box) {
            var holder = box.closest('div');
            holder.classList.toggle('hidden', !(box.checked || wanted.indexOf(box.value) !== -1));
        });
    }
    root.addEventListener('change', function (event) {
        if (event.target.id === 'welcome-goal' || event.target.id === 'welcome-kind') suggest();
    });

    root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action]');
        if (!button) return;
        var action = button.getAttribute('data-action');
        if (action === 'github') {
            give('github', URLS.github, { organization: value('welcome-github-org'), token: value('welcome-github-token') }, button);
        } else if (action === 'jira') {
            give('jira', URLS.jira, {
                instance_url: value('welcome-jira-url'), email: value('welcome-jira-email'), token: value('welcome-jira-token')
            }, button);
        } else if (action === 'website') {
            var consent = document.getElementById('welcome-website-consent');
            give('website', URLS.website, { url: value('welcome-website'), consent: !!(consent && consent.checked) }, button);
        } else if (action === 'continue') {
            busy(button, true);
            Promise.all(giving).then(saveEverything).then(function () {
                window.location.assign(button.getAttribute('data-next'));
            }).catch(function (error) {
                busy(button, false);
                say('choices', (error.message || 'Your choices were not saved.') + ' You can set them later in Settings.', true);
            });
        } else if (action === 'save-choices') {
            busy(button, true);
            say('choices', 'Saving…', false);
            post(URLS.choices, chosen()).then(function () {
                say('choices', 'Saved.', false);
            }).catch(function (error) {
                say('choices', error.message || 'Your choices were not saved.', true);
            }).then(function () { busy(button, false); });
        } else if (action === 'show-frameworks') {
            root.querySelectorAll('[data-framework]').forEach(function (box) { box.closest('div').classList.remove('hidden'); });
            root.querySelector('[data-suggest]').removeAttribute('data-suggest');
            button.classList.add('hidden');
        } else if (action === 'test-key') {
            busy(button, true);
            say('key', 'Testing the key…', false);
            post(URLS.test, { provider: value('welcome-provider'), key: value('welcome-key') }).then(function (result) {
                say('key', result.message, false);
                var key = document.getElementById('welcome-key');
                if (key) key.value = '';
                root.setAttribute('data-key-saved', 'true');
                var finish = root.querySelector('[data-finish]');
                if (finish) finish.textContent = 'Finish';
            }).catch(function (error) {
                say('key', error.message || 'The provider did not accept this key.', true);
            }).then(function () { busy(button, false); });
        }
    });

    if (root.hasAttribute('data-reload-when-read')) poll();
    var reading = root.querySelector('[data-state="waiting"], [data-state="reading"]');
    if (reading) poll();
}());
