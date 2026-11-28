/* DormCare · 运行总览 — the operations overview
 *
 * One menu card, one page: the state of the system in numbers, and nothing
 * else. The ticket board is on /tickets.html, the account desk on
 * /users.html, the roster on /roster.html.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;

  var overview = null;

  function $(id) { return document.getElementById(id); }

  /** Status messages for the page, keyed the same way as everywhere else. */
  function flash(code, state, data) {
    var m = $("adMsg");
    if (!m) return;
    m.dataset.code = "";
    if (code) {
      m.dataset.state = state || "ok";
      m.textContent = t(code);
    } else if (data) {
      m.dataset.state = "err";
      m.textContent = DC.errorText(data);
    } else {
      m.dataset.state = "";
      m.textContent = "";
    }
  }

  function renderIdentity() {
    var node = $("adminIdentity");
    if (!node) return;
    var me = DC.me;
    node.textContent = me ? fill(t("adminIdentityBar"), { id: me.username || "" }) : "";
  }

  function statCard(value, label, warn) {
    return '<div class="stat' + (warn ? " warn" : "") + '"><b>' + esc(value) +
      "</b><span>" + esc(label) + "</span></div>";
  }

  function renderStats() {
    var box = $("adminStats");
    if (!box) return;
    var o = overview || {};
    var tot = o.totals || {}, today = o.today || {}, fb = o.feedback || {};
    box.innerHTML = [
      statCard(tot.tickets || 0, t("adTotalTickets")),
      statCard(today.tickets || 0, t("adToday")),
      statCard(today.open || 0, t("adPendingTickets"), (today.open || 0) > 0),
      statCard(fb.rated ? fb.average : t("unitNone"), t("adAvgRating"))
    ].join("");
  }

  var STATUS_ORDER = ["pending", "accepted", "done", "confirmed"];
  var PRIORITY_ORDER = ["urgent", "high", "normal", "low"];

  // Every distribution block links into the ticket board pre-filtered, so a
  // number on this page is always one click away from the work behind it.
  var FILTER_QUERY = { status: "status=", priority: "priority=", college: "college=" };

  function breakdownGroup(title, counts, labels, order, kind) {
    var keys = kind ? order.slice() : order.filter(function (k) { return counts[k] !== undefined; });
    Object.keys(counts).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    var body = keys.length
      ? keys.map(function (k) {
          var label = esc(kind === "status" ? t("adStatus_" + k) : kind === "priority" ? DC.priLabel(k) : (labels[k] || k));
          var query = FILTER_QUERY[kind];
          var open = query && k !== "?"
            ? '<a class="ad-chip" href="/tickets.html?' + query + encodeURIComponent(k) + '" title="' +
              esc(t("adFilterAll")) + '">'
            : '<span class="ad-chip">';
          return open + "<em>" + label + "</em><b>" + esc(counts[k] || 0) + "</b>" +
            (query && k !== "?" ? "</a>" : "</span>");
        }).join("")
      : '<span class="muted">' + esc(t("adNoData")) + "</span>";
    return '<div class="ad-group' + (kind ? " ad-distribution-" + kind : "") + '"><h4>' + esc(title) + '</h4><div class="ad-chips">' +
      body + "</div></div>";
  }

  function renderBreakdown() {
    var box = $("adBreakdown");
    if (!box) return;
    var o = overview || {};
    var colCounts = {}, colLabels = {};
    (o.by_college || []).forEach(function (c) {
      var key = c.college || "?";
      colCounts[key] = c.count;
      colLabels[key] = DC.collegeLabel(c.college) || c.label || t("unitNone");
    });
    box.innerHTML =
      breakdownGroup(t("adByStatus"), o.by_status || {}, o.status_labels || {}, STATUS_ORDER, "status") +
      breakdownGroup(t("adByPriority"), o.by_priority || {}, o.priority_labels || {}, PRIORITY_ORDER, "priority") +
      breakdownGroup(t("adByCollege"), colCounts, colLabels, [], "college");
  }

  function q(path) { return path + "?lang=" + encodeURIComponent(DC.lang); }

  function loadOverview() {
    return DC.api(q("/api/admin/overview")).then(function (res) {
      if (!res.ok) { flash(null, "err", res.data); return; }
      overview = res.data || {};
      renderStats();
      renderBreakdown();
      flash(null, "");
    });
  }

  DC.afterLang(function () {
    renderIdentity();
    renderStats();
    renderBreakdown();
  });

  var reload = $("adReload");
  if (reload) reload.addEventListener("click", function () { loadOverview(); });

  // `"admin"` as the second argument: the server refuses to hand this page to
  // anybody else, and the JS agrees — a stranger who guesses the URL is sent
  // back to the menu rather than shown an empty shell.
  DC.start(function () {
    return DC.loadConfig().then(function () {
      renderIdentity();
      return loadOverview();
    });
  }, "admin");
})(window.DC);
