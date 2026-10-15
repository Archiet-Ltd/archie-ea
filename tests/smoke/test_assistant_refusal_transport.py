"""The chat transport shows a refused answer, not what streamed before it.

Runs the real app/static/js/ai_chat/transport.js under node against a scripted
event stream (no browser, no server)."""

import pytest

pytestmark = [pytest.mark.smoke]


def test_the_browser_shows_the_refusal_when_a_preface_was_streamed_first(tmp_path):
    """Run the real transport.js against a scripted event stream: tokens of an
    unsupported text arrive, then a done event whose citation gate refused.
    The text the user ends up with is the refusal."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    root = Path(__file__).resolve().parents[2]
    transport = root / "app" / "static" / "js" / "ai_chat" / "transport.js"
    events = [
        {"type": "token", "text": "Payments Gateway depends on Exadata."},
        {"type": "done", "response": "Not recorded: refused", "sources": [],
         "citation_gate": {"checked": True, "refused": True, "uncited_count": 1}},
    ]
    harness = tmp_path / "run.js"
    harness.write_text(
        """
const fs = require('fs'), vm = require('vm');
const events = JSON.parse(process.argv[3]);
const body = events.map(e => 'data: ' + JSON.stringify(e) + '\\n\\n').join('');
const enc = new TextEncoder();
let sent = false;
const window = {
  fetch: async () => ({ ok: true, body: { getReader: () => ({
    read: async () => sent ? { done: true } : (sent = true, { done: false, value: enc.encode(body) }),
    cancel: () => {} }) }, json: async () => ({}) }),
  ArchieChat: undefined,
};
const ctx = vm.createContext({ window, document: { querySelector: () => null },
  Headers: class { constructor(){ this.m = {}; } has(k){ return k in this.m; } set(k,v){ this.m[k]=v; } },
  AbortController, TextDecoder, setTimeout, clearTimeout, console, Promise, Object });
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const tokens = [];
window.ArchieChat.transport.streamMessage('{}', {
  onToken: (t, full) => tokens.push(full),
  onDone: async (r) => { console.log(JSON.stringify({ final: r.text, streamed: tokens })); },
}).then(() => {});
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(transport), json.dumps(events)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["final"] == "Not recorded: refused"
