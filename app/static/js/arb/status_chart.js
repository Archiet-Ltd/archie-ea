/*
 * ARB "Review status" donut, shared by the typed and legacy dashboards.
 *
 * Chart.js passes colour strings to the canvas 2D context, which cannot resolve CSS custom
 * properties: 'hsl(var(--warning))' is an invalid fill and paints black. The design tokens are
 * bare HSL triplets ("37.7 92.1% 50.2%"), so they are read from the computed style at render
 * time (which also follows the active theme) and wrapped in hsl() here.
 *
 * The chart is sized by its container (maintainAspectRatio: false). A doughnut is 1:1, so letting
 * it size itself from the container width made it as tall as it was wide.
 */
(function (root) {
    'use strict';

    // Semantic tokens, in segment order: Pending / In Review, Approved, Rejected, Other.
    var TOKENS = ['--warning', '--success', '--destructive', '--muted-foreground'];
    // Used only when a token is undefined, so a missing stylesheet never paints black.
    var FALLBACKS = ['#f59e0b', '#10b981', '#ef4444', '#9ca3af'];

    function segmentColors() {
        var style = getComputedStyle(document.documentElement);
        return TOKENS.map(function (name, i) {
            var raw = style.getPropertyValue(name).trim();
            return raw ? 'hsl(' + raw + ')' : FALLBACKS[i];
        });
    }

    function render(canvas, counts) {
        if (!canvas || !root.Chart) return null;
        var pending = counts.pending || 0;
        var approved = counts.approved || 0;
        var rejected = counts.rejected || 0;
        var other = Math.max(0, (counts.total || 0) - pending - approved - rejected);
        return new root.Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: ['Pending / In Review', 'Approved', 'Rejected', 'Other'],
                datasets: [{
                    data: [pending, approved, rejected, other],
                    backgroundColor: segmentColors(),
                    borderWidth: 2,
                    borderColor: 'transparent'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: function (item) { return ' ' + item.label + ': ' + item.raw; } } }
                },
                cutout: '65%'
            }
        });
    }

    root.ArbStatusChart = { render: render };
})(window);
