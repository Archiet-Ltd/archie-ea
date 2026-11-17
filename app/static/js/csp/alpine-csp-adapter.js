/* Alpine ↔ CSPExpr adapter (ARCH-070).
 *
 * Registers CSPExpr as Alpine's evaluator via Alpine.setEvaluator, so Alpine
 * stops using new Function() and the CSP can drop 'unsafe-eval'. Must run on
 * `alpine:init` (before Alpine.start compiles any expression).
 *
 * Alpine's evaluator contract (v3): setEvaluator(factory), where
 *   factory(el, expression) -> runner(receiver, { scope = {}, params = [] })
 * The runner evaluates `expression` against the element's data stack (plus any
 * extra `scope`, e.g. x-for vars, and Alpine magics), then calls receiver(result).
 */
(function (global) {
  'use strict';

  function install(Alpine) {
    var cache = Object.create(null);

    function compileCached(expression) {
      var ast = cache[expression];
      if (ast === undefined) {
        try { ast = global.CSPExpr.compile(expression); }
        catch (e) { ast = { __err: e }; }
        cache[expression] = ast;
      }
      return ast;
    }

    Alpine.setEvaluator(function (el, expression) {
      // Alpine passes either a string expression or (rarely) a function.
      if (typeof expression === 'function') {
        return function (receiver, extras) {
          extras = extras || {};
          var scope = scopeFor(el, extras.scope);
          var params = extras.params || [];
          var result = expression.apply(scope, params);
          runReceiver(receiver, result, scope, params);
        };
      }
      var expr = String(expression).trim();
      var ast = compileCached(expr);

      return function (receiver, extras) {
        extras = extras || {};
        receiver = receiver || function () {};
        if (ast && ast.__err) throw ast.__err;
        var scope = scopeFor(el, extras.scope);
        var params = extras.params || [];
        var result = global.CSPExpr.run(ast, scope);
        runReceiver(receiver, result, scope, params);
      };
    });

    // Build the evaluation scope: element data stack + Alpine magics + any extra
    // scope Alpine hands us (x-for iteration vars, $event on handlers, etc.).
    // Assignments write back to Alpine's reactive data so reactivity is preserved.
    function scopeFor(el, extraScope) {
      var base = el ? Alpine.$data(el) : {};
      var magics = buildMagics(el);
      return new Proxy({}, {
        has: function () { return true; },
        get: function (_t, k) {
          if (typeof k === 'symbol') return undefined;
          if (extraScope && k in extraScope) return extraScope[k];
          if (magics.hasOwnProperty(k)) return magics[k]();  // lazy magic
          // Component data next.
          if (base && (k in base)) return base[k];
          // CRITICAL: fall back to the REAL global for names not on the
          // component (window, document, localStorage, console, JSON, Date, …).
          // Without this, an Alpine expression like `window.addEventListener(...)`
          // or `localStorage.getItem(...)` resolves the identifier through this
          // proxy to undefined and throws — which broke every page whose Alpine
          // uses a browser global (e.g. the composer's resize handler). The
          // `has` trap must stay `true` (Alpine's with()-scope contract), so the
          // real fallback has to happen here in get().
          if (k in global) return global[k];
          return base ? base[k] : undefined;
        },
        set: function (_t, k, v) {
          if (extraScope && k in extraScope) { extraScope[k] = v; return true; }
          // Assign onto the component data when it owns the key, otherwise let a
          // genuine global assignment (rare in Alpine) hit the global.
          if (base && (k in base)) { base[k] = v; return true; }
          // A rejected assignment is an expression failure, not a successful
          // no-op. Let the browser's TypeError reach Alpine's error path.
          if (k in global) { global[k] = v; return true; }
          if (base) { base[k] = v; return true; }
          return true;
        }
      });
    }

    // Built-in Alpine 3 magics, reimplemented with Alpine's public API. Each is a
    // thunk so it's only constructed when the expression actually reads it.
    function buildMagics(el) {
      return {
        '$el': function () { return el; },
        '$refs': function () { return collectRefs(el); },
        // $store must expose the whole store REGISTRY ($store.modal, $store.foo),
        // not Alpine.store() with no args (which returns undefined). Proxy each
        // name through the public getter.
        '$store': function () {
          return new Proxy({}, {
            has: function () { return true; },
            get: function (_t, k) { return Alpine.store(k); },
            set: function (_t, k, v) { Alpine.store(k, v); return true; }
          });
        },
        '$data': function () { return Alpine.$data(el); },
        '$root': function () { return Alpine.closestRoot ? Alpine.closestRoot(el) : rootOf(el); },
        '$nextTick': function () { return Alpine.nextTick; },
        '$dispatch': function () {
          return function (name, detail) {
            el.dispatchEvent(new CustomEvent(name, { detail: detail, bubbles: true, composed: true, cancelable: true }));
          };
        },
        '$id': function () { return Alpine.$id ? Alpine.$id : function (n, k) { return n + (k !== undefined ? '-' + k : ''); }; },
        '$watch': function () {
          return function (path, cb) {
            // Evaluate `path` as a getter against the live scope and watch it.
            var getter = function () { return global.CSPExpr.run(path, scopeFor(el)); };
            return Alpine.effect(function () { var v = getter(); Alpine.nextTick(function () {}); return cb ? undefined : v; }), watchGetter(getter, cb);
          };
        }
      };
    }

    function watchGetter(getter, cb) {
      var old; var first = true;
      return Alpine.effect(function () {
        var v = getter();
        if (first) { old = v; first = false; return; }
        // A watcher whose callback the evaluator could not resolve must not abort the other effects.
        if (v !== old) { var prev = old; old = v; if (typeof cb === 'function') cb(v, prev); }
      });
    }

    function rootOf(el) { var n = el; while (n && !(n._x_dataStack)) n = n.parentElement; return n || el; }

    function collectRefs(el) {
      // Alpine stores refs at the nearest ancestor carrying x-ref children via
      // _x_refs. Merge refs found along the ancestor chain (nearest wins).
      var refs = {}, n = el;
      while (n) {
        if (n._x_refs) { for (var k in n._x_refs) { if (!(k in refs)) refs[k] = n._x_refs[k]; } }
        n = n.parentElement;
      }
      return refs;
    }

    function runReceiver(receiver, result, scope, params) {
      // Alpine's own evaluator does NOT hand a function straight to the receiver:
      // `runIfTypeOfFunction` calls it with the element's scope and the directive's
      // params, and awaits a promise before delivering the value. This adapter used
      // to only *describe* that behaviour in a comment and then skip it, which broke
      // every expression whose value is a function:
      //
      //   x-data="architectureJourneyHub"  -> Alpine.data() providers are injected as
      //       getters returning a FACTORY; unevaluated it yields a function, so the
      //       component initialised as {} and the whole Architecture Journey hub was
      //       inert (title/intent/layers never bound, "Start architecture journey"
      //       permanently disabled, "Start solution design" a no-op with no error).
      //   @click="startSolution"           -> the method was returned, never invoked.
      //
      // A rejected promise is re-thrown on a macrotask so an async handler that fails
      // surfaces in the console instead of vanishing (CLAUDE.md: no silent failure).
      //
      // Divergence, deliberate: Alpine's `dontAutoEvaluateFunctions` window is a
      // module-local flag in alpine.min.js (3.14.3) with exactly one internal caller,
      // `bound()`, so a custom evaluator cannot observe it. The cost is that an inline
      // binding whose expression is itself a bare function name gets called; the
      // alternative is the total breakage above.
      if (typeof result === 'function') {
        result = result.apply(scope, params || []);
      }
      if (result && typeof result.then === 'function') {
        result.then(function (value) {
          runReceiver(receiver, value, scope, params);
        }, function (error) {
          setTimeout(function () { throw error; }, 0);
        });
        return;
      }
      receiver(result);
    }
  }

  // Auto-install on alpine:init if Alpine is present.
  global.document && global.document.addEventListener('alpine:init', function () {
    if (global.Alpine && global.CSPExpr) install(global.Alpine);
  });

  global.CSPAlpineAdapter = { install: install };
})(typeof window !== 'undefined' ? window : globalThis);
