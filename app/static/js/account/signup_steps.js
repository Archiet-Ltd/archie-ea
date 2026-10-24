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

    function paint(sources) {
        (sources || []).forEach(function (row) {
            var line = root.querySelector('[data-source="' + row.key + '"]');
            if (!line) return;
            line.textContent = row.text;
            line.setAttribute('data-state', row.state);
        });
    }

    var timer = null;
    function poll() {
        if (timer) return;
        timer = window.setInterval(function () {
            window.Platform.fetch.get(URLS.status, null, { silent: true }).then(function (body) {
                paint(body.sources);
                var open = (body.sources || []).some(function (row) { return row.state === 'waiting' || row.state === 'reading'; });
                if (!open) { window.clearInterval(timer); timer = null; }
            }).catch(function () { /* swallow-ok: a missed poll is retried on the next tick; the last line stays on screen */ });
        }, 3000);
    }

    function give(name, url, body, button, done) {
        busy(button, true);
        say(name, 'Connecting…', false);
        post(url, body).then(function (result) {
            say(name, done, false);
            paint(result.sources);
            poll();
        }).catch(function (error) {
            say(name, error.message || 'That did not work. Check it and try again.', true);
        }).then(function () { busy(button, false); });
    }

    function chosen() {
        var boxes = root.querySelectorAll('[data-framework]');
        var picked = [];
        boxes.forEach(function (box) { if (box.checked) picked.push(box.value); });
        return { industry: value('welcome-industry'), frameworks: picked };
    }

    root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action]');
        if (!button) return;
        var action = button.getAttribute('data-action');
        if (action === 'github') {
            give('github', URLS.github, { organization: value('welcome-github-org'), token: value('welcome-github-token') },
                button, 'Connected. Reading your repositories in the background; carry on.');
        } else if (action === 'jira') {
            give('jira', URLS.jira, {
                instance_url: value('welcome-jira-url'), email: value('welcome-jira-email'), token: value('welcome-jira-token')
            }, button, 'Connected. Reading your issues in the background; carry on.');
        } else if (action === 'website') {
            var consent = document.getElementById('welcome-website-consent');
            give('website', URLS.website, { url: value('welcome-website'), consent: !!(consent && consent.checked) },
                button, 'Saved. We will read this site soon.');
        } else if (action === 'continue') {
            busy(button, true);
            post(URLS.choices, chosen()).then(function () {
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

    var reading = root.querySelector('[data-state="waiting"], [data-state="reading"]');
    if (reading) poll();
}());
