// Draws a viewpoint page's diagram with the shared ComposerRenderer from the JSON the page embeds.
(function () {
  function draw() {
    var host = document.querySelector('[data-viewpoint-diagram]');
    if (!host || typeof ComposerRenderer === 'undefined') return;
    var data = document.getElementById(host.getAttribute('data-viewpoint-diagram'));
    if (!data) return;
    var model;
    try { model = JSON.parse(data.textContent || '{}'); } catch (e) { return; }
    if (!model.elements || !model.elements.length) return;
    var r = ComposerRenderer.create(host, { mode: 'view', width: '100%', height: host.clientHeight || 320 });
    r.loadElements(model.elements, model.relationships || []);
    if (r.fitToContent) r.fitToContent();
    host.setAttribute('data-drawn', 'true');
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', draw); else draw();
})();
