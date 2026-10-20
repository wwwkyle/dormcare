/* DormCare · 全部工单 — the whole board
 *
 * One menu card, one page. It is the only place in the system where every
 * ticket and its owner are visible at once — the student console and the
 * technician console each get a *slice*, and a slice is exactly what an
 * operator must not be given.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;
  var row = DC.row, priLabel = DC.priLabel, pairsText = DC.pairsText;

  var overview = null;
  var tickets = [];
  var statusFilter = "";

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

  var STATUS_ORDER = ["pending", "accepted", "done", "confirmed"];

  function nameOf(w) {
    if (!w) return "";
    return DC.lang === "zh" ? (w.name || w.name_en) : (w.name_en || w.name);
  }

  /** A read-only five-star verdict, unlike the student's clickable one. */
  function stars(n) {
    var out = "";
    for (var i = 1; i <= 5; i++) out += (i <= n) ? "★" : "☆";
    return '<span class="fb-stars">' + out + "</span>";
  }

  function renderStatusFilter() {
    var sel = $("adStatusFilter");
    if (!sel) return;
    var labels = (overview && overview.status_labels) || {};
    var by = (overview && overview.by_status) || {};
    var keep = sel.value || statusFilter;
    sel.innerHTML = '<option value="">' + esc(t("adFilterAll")) + "</option>" +
      STATUS_ORDER.filter(function (k) { return by[k] !== undefined; }).map(function (k) {
        return '<option value="' + esc(k) + '">' + esc(labels[k] || k) + "</option>";
      }).join("");
    sel.value = keep || "";
    statusFilter = sel.value || "";
  }

  function ticketRow(tk) {
    var done = (tk.status === "done" || tk.status === "confirmed");
    var badges =
      '<span class="pill ' + esc(tk.status || "pending") + '">' + esc(tk.status_label || "") +
      "</span>" +
      '<span class="pill ' + esc(tk.priority) + '">' + esc(priLabel(tk.priority)) + "</span>" +
      '<span class="pill q-' + esc(tk.queue_state || "unassigned") + '">' +
      esc(tk.queue_label || "") + "</span>" +
      (tk.public ? '<span class="pill public">' + esc(t("tagPublic")) + "</span>" : "");

    var where = esc(tk.college_label) +
      (tk.block ? " · " + esc(tk.block) : "") + " · " + esc(tk.dorm);

    var detail = [];
    // The one field an administrator gets and nobody else does: who filed it.
    detail.push(row(t("adOwner"),
      esc(tk.student_name || t("unitNone")) +
      (tk.user_username ? ' <code class="who-id">' + esc(tk.user_username) + "</code>" : "")));
    detail.push(row(t("adContact"), esc(tk.contact || t("unitNone"))));
    detail.push(row(t("adAssign"), tk.assigned_worker
      ? esc(nameOf(tk.assigned_worker)) +
        (tk.assigned_worker.worker_id
          ? ' <code class="who-id">' + esc(tk.assigned_worker.worker_id) + "</code>" : "")
      : esc(t("tkNoWorker"))));
    detail.push(row(t("tkAppointment"), tk.appointment
      ? '<strong class="appt">' + esc(DC.dayLabel(tk.date)) + " " +
        esc(tk.appointment.start) + "-" + esc(tk.appointment.end) + "</strong>"
      : esc(t("unitNone"))));
    detail.push(row(t("tkDate"), esc(DC.dayLabel(tk.date))));
    detail.push(row(t("tkWindow"), esc(pairsText(tk.slots))));
    detail.push(row(t("tkDuration"), esc(tk.duration_minutes) + " " + esc(t("minutes"))));
    if (tk.description) detail.push(row(t("tkDesc"), esc(tk.description)));
    if (tk.rating > 0) {
      detail.push(row(t("adRating"), stars(tk.rating) +
        (tk.feedback ? " " + esc(tk.feedback) : "")));
    }

    return '<details class="ticket fold-item' + (done ? " done" : "") +
      '" data-p="' + esc(tk.priority) + '">' +
      "<summary>" +
      '<span class="tk-type">' + esc(tk.repair_label) + "</span>" +
      '<span class="tk-where">' + where + "</span>" +
      '<span class="tk-when">' + esc(DC.dayLabel(tk.date)) + " · " +
      esc(pairsText(tk.slots)) + "</span>" +
      '<span class="tk-badges">' + badges + "</span>" +
      '<span class="chev" aria-hidden="true">▾</span>' +
      "</summary>" +
      '<div class="tk-body">' + detail.join("") +
      '<div class="tk-foot"><code>' + esc(tk.id) + "</code>" +
      '<span class="tk-actions"><button type="button" class="btn tiny danger" ' +
      'data-act="delete-ticket" data-id="' + esc(tk.id) + '">' +
      esc(t("adDelete")) + "</button></span></div></div></details>";
  }

  function renderTickets() {
    var node = $("adTicketList");
    if (!node) return;
    var list = tickets.filter(function (tk) {
      return !statusFilter || tk.status === statusFilter;
    });
    var count = $("adTicketCount");
    if (count) {
      count.textContent = list.length
        ? fill(t("adUserCount"), { n: list.length })
        : "";
    }
    node.innerHTML = list.length
      ? list.map(ticketRow).join("")
      : '<p class="empty">' + esc(t("adNoTickets")) + "</p>";
  }

  /* ====================================================================== */
  /*  data                                                                  */
  /* ====================================================================== */

  function deleteTicket(id, btn) {
    if (typeof window.confirm === "function" && !window.confirm(t("adDeleteConfirm"))) return;
    DC.busy(btn, true);
    DC.post("/api/admin/ticket/delete", { id: id }).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) { flash(null, "err", res.data); return; }
      flash("adDeleted", "ok");
      return loadTickets();
    }).catch(function () {
      DC.busy(btn, false);
      flash("err_network", "err");
    });
  }

  function q(path) { return path + "?lang=" + encodeURIComponent(DC.lang); }

  /* The overview is read here only for its status labels — the numbers live on
   * /overview.html, and this page must not show a second copy of them. */
  function loadOverviewLabels() {
    return DC.api(q("/api/admin/overview")).then(function (res) {
      if (res.ok) overview = res.data || {};
    });
  }

  function loadTickets() {
    return DC.api(q("/api/admin/tickets")).then(function (res) {
      if (!res.ok) return;
      tickets = (res.data && res.data.tickets) || [];
      renderTickets();
    });
  }

  function onTicketListClick(e) {
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    if (b.getAttribute("data-act") === "delete-ticket") {
      deleteTicket(b.getAttribute("data-id"), b);
    }
  }

  function wire() {
    var reload = $("adReload");
    if (reload) {
      reload.addEventListener("click", function () {
        loadOverviewLabels().then(renderStatusFilter);
        loadTickets();
      });
    }
    var filter = $("adStatusFilter");
    if (filter) {
      filter.addEventListener("change", function (e) {
        statusFilter = e.target.value || "";
        renderTickets();
      });
    }
    var list = $("adTicketList");
    if (list) list.addEventListener("click", onTicketListClick);
  }

  DC.afterLang(function () {
    renderIdentity();
    renderStatusFilter();
    renderTickets();
  });

  wire();

  DC.start(function () {
    return DC.loadConfig().then(function () {
      renderIdentity();
      return loadOverviewLabels();
    }).then(function () {
      renderStatusFilter();
      return loadTickets();
    });
  }, "admin");
})(window.DC);
