/* Blue Ocean value curve — one line per player across the competing factors.
 *
 * Reads the points embedded by the page (factor, player, offering_level) and
 * draws them with the shared Chart.js build. A factor with no level for a
 * player leaves a gap in that line rather than a point at zero, so the curve
 * never shows a fabricated level. Theme colours come from the shared token
 * helper, so the curve matches every other chart in the product in light and
 * dark. No new charting library is introduced.
 */
(function () {
  "use strict";

  function readData() {
    var node = document.getElementById("blue-ocean-value-curve-data");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent || "[]");
    } catch (e) {
      return null;
    }
  }

  function colour(index) {
    // Cycle the layer accent tokens so each player's line is distinct and
    // theme-aware. ArchieColorTokens resolves a CSS custom property to a
    // usable colour; fall back to the foreground token.
    var tokens = [
      "--layer-strategy",
      "--layer-business",
      "--layer-motivation",
      "--primary",
    ];
    var name = tokens[index % tokens.length];
    if (window.ArchieColorTokens && window.ArchieColorTokens.cssHSL) {
      return window.ArchieColorTokens.cssHSL(name);
    }
    return "currentColor";
  }

  function build() {
    var canvas = document.getElementById("blue-ocean-value-curve");
    if (!canvas || typeof window.Chart === "undefined") return;
    var points = readData();
    if (!points || !points.length) return;

    // Factors in the order the page provided them (the axis), and the players.
    var factors = [];
    points.forEach(function (p) {
      if (factors.indexOf(p.factor) === -1) factors.push(p.factor);
    });

    var byPlayer = {};
    points.forEach(function (p) {
      var player = p.player || "This organisation";
      if (!byPlayer[player]) byPlayer[player] = {};
      byPlayer[player][p.factor] = p.offering_level;
    });

    var datasets = Object.keys(byPlayer).map(function (player, i) {
      var c = colour(i);
      return {
        label: player,
        data: factors.map(function (f) {
          var v = byPlayer[player][f];
          return v === null || v === undefined ? null : v;
        }),
        borderColor: c,
        backgroundColor: c,
        spanGaps: false,
        tension: 0.3,
        pointRadius: 4,
      };
    });

    new window.Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: factors, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { min: 0, max: 10, title: { display: true, text: "Offering level" } },
          x: { title: { display: true, text: "Competing factor" } },
        },
        plugins: { legend: { position: "top" } },
      },
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
