"""Shared reads for the roadmap journeys: wait for a page to settle, and watch every animation frame.

A tile and the evidence line under it are compared in the browser on every frame, so a figure that
disagrees for one frame is seen, not missed between two polls.
"""

SETTLE_SCRIPT = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"

START_SAMPLER = """() => {
  const tiles = {
    'Plateaus': 'rdm-plateaus-tile',
    'Work packages': 'rdm-work-packages-tile',
    'Gaps': 'rdm-recorded-gaps',
  };
  window.__frames = { count: 0, bad: [], stop: false };
  const tick = () => {
    const line = (document.querySelector('[data-testid="roadmap-summary-footer"]') || {}).textContent || '';
    window.__frames.count += 1;
    for (const [label, id] of Object.entries(tiles)) {
      const tile = ((document.querySelector('[data-testid="' + id + '"]') || {}).textContent || '').trim();
      const m = line.match(new RegExp(label + ': (\\\\d+|none recorded)'));
      if (!m || !/^\\d+$/.test(tile)) continue;
      const shown = m[1] === 'none recorded' ? '0' : m[1];
      if (shown !== tile) window.__frames.bad.push(label + ': tile ' + tile + ', line ' + shown);
    }
    if (!window.__frames.stop) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}"""


def settle(page):
    page.wait_for_load_state("networkidle")
    page.evaluate(SETTLE_SCRIPT)


def start_sampler(page):
    page.evaluate(START_SAMPLER)


def stop_sampler(page):
    """Stop watching and return (frames seen, the disagreements)."""
    page.evaluate("() => { window.__frames.stop = true; }")
    page.evaluate(SETTLE_SCRIPT)
    return page.evaluate("() => [window.__frames.count, window.__frames.bad]")
