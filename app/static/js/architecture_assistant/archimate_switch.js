// Plain language by default; ArchiMate types and layers behind a switch (R1-08,
// US-16). State lives in sessionStorage AND a preference cookie so it survives a
// reload and is also readable by the server (see the templates, which render
// the initial state from the cookie so nothing flashes). Top-level window
// factory: the CSP-safe Alpine evaluator resolves x-data names against window.
function archimateSwitch() {
  var KEY = 'archimateDetail';
  var COOKIE = 'archimate_detail';

  function readCookie() {
    var match = document.cookie.match(new RegExp('(?:^|; )' + COOKIE + '=([^;]*)'));
    return match ? match[1] === '1' : false;
  }

  return {
    detail: false,

    init() {
      var stored = null;
      try { stored = window.sessionStorage.getItem(KEY); } catch (e) { stored = null; }
      if (stored === '1' || stored === '0') {
        this.detail = stored === '1';
      } else {
        this.detail = this.$root.dataset.detail === '1' || readCookie();
      }
    },

    toggle() {
      this.detail = !this.detail;
      var value = this.detail ? '1' : '0';
      try { window.sessionStorage.setItem(KEY, value); } catch (e) { /* storage may be blocked */ }
      document.cookie = COOKIE + '=' + value + '; path=/; max-age=31536000; SameSite=Lax';
    },
  };
}
window.archimateSwitch = archimateSwitch;
