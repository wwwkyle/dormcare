/* DormCare · 维修班组 — the technician roster
 *
 * One menu card, one page: who is on the team, what they cover, and whether
 * they are certified. This is read-only on purpose — issuing a technician
 * account is an action, and lives with the account desk on /users.html.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;

  var overview = null;

  function $(id) { return document.getElementById(id); }

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

  function listSep() { return DC.lang === "zh" ? "、" : ", "; }

  function workerRow(w) {
    return '<div class="ad-item ad-roster">' +
      '<div class="ad-rost-top">' +
      '<span class="ad-name">' + esc(DC.lang === "zh" ? w.name : (w.name_en || w.name)) + "</span>" +
      '<code class="who-id">' + esc(w.worker_id) + "</code>" +
      (w.certified ? '<span class="pill cert">⚠ ' + esc(t("certNeeded")) + "</span>" : "") +
      "</div>" +
      '<div class="tk-row"><span class="lbl2">' + esc(t("adTrade")) + "</span><span>" +
      esc(w.trade || t("unitNone")) + "</span></div>" +
      '<div class="tk-row"><span class="lbl2">' + esc(t("adSpecialties")) + "</span><span>" +
      esc((w.specialty_labels || []).join(listSep()) || t("unitNone")) + "</span></div>" +
      (w.phone ? '<div class="tk-row"><span class="lbl2">' + esc(t("adPhone")) +
        "</span><span>" + esc(w.phone) + "</span></div>" : "") +
      "</div>";
  }

  function renderWorkers() {
    var node = $("adWorkerList");
    if (!node) return;
    var list = (overview && overview.workers) || [];
    var count = $("adRosterCount");
    if (count) {
      count.textContent = list.length
        ? fill(t("adUserCount"), { n: list.length }) : "";
    }
    node.innerHTML = list.length
      ? list.map(workerRow).join("")
      : '<p class="empty">' + esc(t("adNoData")) + "</p>";
  }

  function q(path) { return path + "?lang=" + encodeURIComponent(DC.lang); }

  function loadRoster() {
    return DC.api(q("/api/admin/overview")).then(function (res) {
      if (!res.ok) { flash(null, "err", res.data); return; }
      overview = res.data || {};
      renderWorkers();
    });
  }

  DC.afterLang(function () {
    renderIdentity();
    renderWorkers();
  });

  var reload = $("adReload");
  if (reload) reload.addEventListener("click", function () { loadRoster(); });

  // Issuing a technician is an action on the account desk, so this button is a
  // shortcut to that page rather than a second copy of the create form.
  var issue = $("adNewWorkerBtn");
  if (issue) {
    issue.addEventListener("click", function () {
      window.location.replace("/users.html");
    });
  }

  DC.start(function () {
    return DC.loadConfig().then(function () {
      renderIdentity();
      return loadRoster();
    });
  }, "admin");
})(window.DC);
