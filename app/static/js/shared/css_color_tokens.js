/**
 * Shared helper for resolving a shadcn/ui CSS custom property (e.g. `--warning`)
 * to a real, Chart.js-parseable colour string at runtime.
 *
 * `hsl(var(--warning))` is valid CSS but NOT a valid JavaScript colour literal:
 * `var(...)` only resolves inside an actual CSS property value, so handing it
 * to a JS charting library (Chart.js -> @kurkle/color) fails to parse silently
 * and the library falls back to its default fill (black). This resolves the
 * custom property via `getComputedStyle` first, so the string handed to the
 * chart is a real `H S% L%` triplet.
 *
 * Single source of truth — previously duplicated in
 * capability_map/maturity_radar.js and capability_map/investment_bubble.js,
 * and diverged into a broken inline copy in arb/dashboard.html (2026-09-17).
 */
(function () {
    'use strict';

    function cssHSL(varName, alpha) {
        var raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
        if (!raw) return null;
        return alpha !== undefined ? 'hsl(' + raw + ' / ' + alpha + ')' : 'hsl(' + raw + ')';
    }

    /**
     * The same token as a `#rrggbb` string, for the places that need one —
     * `<input type="color">` accepts nothing else, and a colour saved on a
     * record is stored that way. Computed from the token, never typed in, so
     * it follows the theme. Returns `fallbackToken`'s value (or null) when the
     * token is unset.
     */
    function cssHex(token, fallbackToken) {
        var raw = getComputedStyle(document.documentElement).getPropertyValue('--' + token).trim();
        var m = raw.match(/^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%/);
        if (!m) return fallbackToken ? cssHex(fallbackToken) : null;
        var h = parseFloat(m[1]), s = parseFloat(m[2]) / 100, l = parseFloat(m[3]) / 100;
        var a = s * Math.min(l, 1 - l);
        function channel(n) {
            var k = (n + h / 30) % 12;
            var v = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
            return ('0' + Math.round(v * 255).toString(16)).slice(-2);
        }
        return '#' + channel(0) + channel(8) + channel(4);
    }

    window.ArchieColorTokens = window.ArchieColorTokens || {};
    window.ArchieColorTokens.cssHSL = cssHSL;
    window.ArchieColorTokens.cssHex = cssHex;
})();
