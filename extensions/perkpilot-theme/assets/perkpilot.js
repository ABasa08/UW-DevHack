/*
 * PerkPilot storefront script, shared by the theme app extension (assistant embed + product fit guidance block)
 * and the manual Custom Liquid snippet (served by the PerkPilot app at /perkpilot-embed.js).
 *
 * - Session: signed-in customers get a session through the Shopify app proxy (/apps/perkpilot/session), which
 *   Shopify signs; guests get an anonymous session from the PerkPilot API. Kept in this store's localStorage.
 * - Assistant: a floating button opens the PerkPilot assistant in an iframe. Checkout always happens on this store's
 *   Shopify checkout (the assistant links the top window to the verified draft-order checkout).
 * - Fit guidance: on product pages, shows merchant-approved size guidance for the selected size. The selection only
 *   changes if the shopper presses "Switch".
 */
(function () {
  if (window.PerkPilot) return;
  var KEY = "perkpilot_session";

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }
  function clean(url) {
    return (url || "").trim().replace(/\/+$/, "");
  }
  function load() {
    try { return JSON.parse(window.localStorage.getItem(KEY) || "null"); } catch (e) { return null; }
  }
  function save(s) {
    try { window.localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private mode: session lasts this page */ }
  }
  function normSize(s) {
    s = String(s == null ? "" : s).trim();
    if (/^\d+(\.\d+)?$/.test(s)) return String(parseFloat(s));
    return s.toUpperCase().slice(0, 24);
  }

  var sessionPromise = null;
  function session(appUrl, signedIn) {
    if (sessionPromise) return sessionPromise;
    sessionPromise = (async function () {
      var cur = load();
      var fresh = cur && cur.expires_at * 1000 > Date.now() + 86400000;
      if (fresh && (!signedIn || cur.signed_in)) return cur;
      try {
        // Shopify signs app-proxy requests and adds logged_in_customer_id, so this is the trusted path.
        var r = await fetch("/apps/perkpilot/session", { credentials: "same-origin", headers: cur ? { "x-perkpilot-session": cur.token } : {} });
        if (r.ok && (r.headers.get("content-type") || "").indexOf("json") >= 0) {
          var s = await r.json();
          save(s);
          return s;
        }
      } catch (e) { /* app proxy not configured: fall back to a guest session */ }
      var res = await fetch(appUrl + "/api/session/anonymous", {
        method: "POST",
        headers: { "content-type": "application/json", "ngrok-skip-browser-warning": "1" },
        body: JSON.stringify({ previous_token: cur && cur.token }),
      });
      if (!res.ok) throw new Error("PerkPilot session unavailable");
      var g = await res.json();
      save(g);
      return g;
    })();
    sessionPromise.catch(function () { sessionPromise = null; });
    return sessionPromise;
  }

  // ------------------------------------------------------------------ assistant launcher

  var ui = null;
  function buildAssistant(root) {
    var d = root.dataset;
    var appUrl = clean(d.appUrl);
    if (!appUrl) return null;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "perkpilot-launcher";
    btn.textContent = d.label || "Shopping assistant";
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", "perkpilot-panel");
    if (d.accent) btn.style.background = d.accent;
    var panel = document.createElement("div");
    panel.id = "perkpilot-panel";
    panel.className = "perkpilot-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Shopping assistant");
    panel.hidden = true;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "perkpilot-close";
    close.setAttribute("aria-label", "Close shopping assistant");
    close.textContent = "\u00d7";
    var frame = document.createElement("iframe");
    frame.title = "Shopping assistant";
    frame.className = "perkpilot-frame";
    frame.setAttribute("allow", "clipboard-write");
    panel.appendChild(close);
    panel.appendChild(frame);
    document.body.appendChild(btn);
    document.body.appendChild(panel);

    function src(opts) {
      opts = opts || {};
      return session(appUrl, d.signedIn === "1").then(function (s) {
        var q = new URLSearchParams({ shop: d.shop || "", store: d.storeName || "" });
        if (opts.productId || d.productId) q.set("product", opts.productId || d.productId);
        if (opts.q) q.set("q", opts.q);
        return appUrl + "/embed?" + q.toString() + "#pp_token=" + encodeURIComponent(s.token);
      });
    }
    function open(opts) {
      panel.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      if (!frame.getAttribute("src") || (opts && opts.q)) {
        src(opts).then(function (u) { frame.src = u; }).catch(function () {
          frame.srcdoc = "<p style='font:15px system-ui;padding:24px'>The shopping assistant is unavailable right now. You can still shop and check out as usual.</p>";
        });
      }
      close.focus();
    }
    function hide() {
      panel.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      btn.focus();
    }
    btn.addEventListener("click", function () { panel.hidden ? open() : hide(); });
    close.addEventListener("click", hide);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) hide(); });
    window.addEventListener("message", function (e) {
      if (e.origin !== new URL(appUrl).origin || !e.data || e.data.source !== "perkpilot") return;
      if (e.data.type === "close") hide();
      if (e.data.type === "navigate" && typeof e.data.url === "string") {
        // Only ever send the shopper to this store's own Shopify pages (e.g. the verified checkout).
        var u = new URL(e.data.url, location.href);
        if (u.host === location.host || u.host === d.shop) location.assign(u.href);
      }
    });
    return { open: open, hide: hide };
  }

  // ------------------------------------------------------------------ product fit guidance block

  function json(el, sel) {
    var n = el.querySelector(sel);
    try { return n ? JSON.parse(n.textContent || "null") : null; } catch (e) { return null; }
  }
  function initFit(el) {
    var d = el.dataset;
    var data = json(el, "[data-perkpilot-guidance]");
    var options = json(el, "[data-perkpilot-options]") || [];
    var variants = json(el, "[data-perkpilot-variants]") || [];
    var want = (d.sizeOption || "Size").toLowerCase();
    var sizeIdx = options.findIndex(function (o) { return String(o.name || o).toLowerCase() === want; });
    var msg = el.querySelector(".perkpilot-fit__message");
    var ev = el.querySelector(".perkpilot-fit__evidence");
    var sw = el.querySelector(".perkpilot-fit__switch");
    var ask = el.querySelector(".perkpilot-fit__ask");
    var last = null;

    function currentId() {
      var p = new URLSearchParams(location.search).get("variant");
      if (p) return Number(p);
      var input = document.querySelector('form[action*="/cart/add"] [name="id"]');
      return input && input.value ? Number(input.value) : variants[0] && variants[0].id;
    }
    function render() {
      var v = variants.find(function (x) { return x.id === currentId(); });
      var size = v && sizeIdx >= 0 ? normSize(v.options[sizeIdx]) : null;
      var g = data && size ? data[size] : null;
      var key = (v && v.id) + ":" + (g ? g.rule_id : "");
      if (key === last) return;
      last = key;
      if (!g) { el.hidden = true; return; }
      el.hidden = false;
      msg.textContent = g.message;
      ev.textContent = "Based on " + g.evidence + ". Approved by the store. Your size stays " + size + " unless you switch.";
      var target = variants.find(function (x) {
        return x.options.every(function (o, i) { return i === sizeIdx ? normSize(o) === g.suggest_size : o === v.options[i]; });
      });
      sw.hidden = !target || !target.available;
      sw.textContent = "Switch to size " + g.suggest_size;
      sw.onclick = function () {
        var u = new URL(location.href);
        u.searchParams.set("variant", target.id);
        location.assign(u.href);
      };
    }
    ask.addEventListener("click", function () {
      if (ui) ui.open({ productId: d.productId, q: "Help me choose the right size for the " + d.productTitle });
    });
    ask.hidden = !document.getElementById("perkpilot-root");
    var appUrl = clean(d.appUrl);
    if (appUrl && d.productId) {
      fetch(appUrl + "/api/storefront/guidance?product=" + encodeURIComponent(d.productId), { headers: { "ngrok-skip-browser-warning": "1" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { if (j) { data = j.guidance; last = null; render(); } })
        .catch(function () { /* keep the metafield copy */ });
    }
    document.addEventListener("change", function () { setTimeout(render, 50); }, true);
    setInterval(render, 600); // themes switch variants with history.replaceState, which fires no event
    render();
  }

  window.PerkPilot = {
    open: function (opts) { if (ui) ui.open(opts); },
    close: function () { if (ui) ui.hide(); },
  };

  ready(function () {
    var root = document.getElementById("perkpilot-root");
    if (root) ui = buildAssistant(root);
    Array.prototype.forEach.call(document.querySelectorAll("[data-perkpilot-fit]"), initFit);
  });
})();
