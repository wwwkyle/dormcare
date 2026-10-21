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
  var users = [];
  var workers = [];
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

  function studentUsers() {
    return users.filter(function (u) { return u.role === "student"; });
  }

  function option(value, label) {
    return '<option value="' + esc(value) + '">' + esc(label) + "</option>";
  }

  function renderEditorOptions() {
    if (!DC.cfg) return;
    var owner = $("adOwnerUser"), type = $("adRepairType"), pri = $("adPriority");
    var day = $("adDate"), college = $("adCollege"), status = $("adStatus");
    var worker = $("adWorker");
    var keepOwner = owner.value, keepType = type.value, keepPri = pri.value;
    var keepDay = day.value, keepCollege = college.value, keepStatus = status.value;
    var keepWorker = worker.value;
    owner.innerHTML = option("", t("adChooseStudent")) + studentUsers().map(function (u) {
      return option(u.id, (u.name || u.username) + " · " + u.username);
    }).join("");
    type.innerHTML = (DC.cfg.repair_types || []).map(function (x) {
      return option(x.id, DC.lang === "zh" ? x.zh : x.en);
    }).join("");
    pri.innerHTML = ["urgent", "high", "normal", "low"].map(function (p) {
      return option(p, priLabel(p));
    }).join("");
    day.innerHTML = (DC.cfg.days || []).map(function (d) {
      return option(d, DC.dayLabel(d));
    }).join("");
    college.innerHTML = DC.collegeOptionsHtml();
    status.innerHTML = STATUS_ORDER.map(function (s) {
      var labels = (overview && overview.status_labels) || {};
      return option(s, labels[s] || s);
    }).join("");
    worker.innerHTML = option("", t("tkNoWorker")) + workers.map(function (w) {
      return option(w.id, nameOf(w) + " · " + (w.worker_id || w.id));
    }).join("");
    owner.value = keepOwner; type.value = keepType || "other";
    pri.value = keepPri || "normal"; day.value = keepDay || day.value;
    college.value = keepCollege || college.value; status.value = keepStatus || "pending";
    worker.value = keepWorker;
    renderEditorBlocks();
  }

  function renderEditorBlocks() {
    var college = $("adCollege").value;
    var block = $("adBlock"), keep = block.value;
    block.innerHTML = DC.blockOptionsHtml(college);
    block.disabled = !DC.realBlockCount(college);
    block.value = keep;
  }

  function slotsText(pairs) {
    return (pairs || []).map(function (p) { return p[0] + "-" + p[1]; }).join(",");
  }

  function parseSlots(text) {
    var out = [];
    String(text || "").split(",").forEach(function (part) {
      var m = part.trim().match(/^(\d{1,2})\s*-\s*(\d{1,2})$/);
      if (m) out.push([Number(m[1]), Number(m[2])]);
    });
    return out;
  }

  function fillOwnerDefaults() {
    if ($("adEditId").value) return;
    var u = studentUsers().filter(function (x) { return x.id === $("adOwnerUser").value; })[0];
    if (!u) return;
    $("adStudentName").value = u.name || "";
    $("adContactInput").value = u.phone || "";
    $("adCollege").value = u.college || "";
    renderEditorBlocks();
    $("adBlock").value = u.block || "";
    $("adDorm").value = u.dorm || "";
  }

  function openEditor(ticket, focusId) {
    var editing = !!ticket;
    $("adTicketEditor").hidden = false;
    $("adEditId").value = editing ? ticket.id : "";
    $("adEditVersion").value = editing ? (ticket.version || 0) : "";
    $("adOwnerUser").disabled = editing;
    $("adLifecycleFields").hidden = !editing;
    $("adEditorTitle").textContent = t(editing ? "adEditTicket" : "adCreateTicket");
    renderEditorOptions();
    if (editing) {
      $("adOwnerUser").value = ticket.user_id || "";
      $("adTitle").value = ticket.title || ticket.repair_label || "";
      $("adRepairType").value = ticket.repair_type || "other";
      $("adPriority").value = ticket.priority || "normal";
      if (!(DC.cfg.days || []).some(function (d) { return d === ticket.date; })) {
        $("adDate").innerHTML += option(ticket.date, DC.dayLabel(ticket.date));
      }
      $("adDate").value = ticket.date || "";
      $("adSlots").value = slotsText(ticket.slots);
      $("adStudentName").value = ticket.student_name || "";
      $("adContactInput").value = ticket.contact || "";
      $("adCollege").value = ticket.college || "";
      renderEditorBlocks();
      $("adBlock").value = ticket.block || "";
      $("adDorm").value = ticket.dorm || "";
      $("adPublic").checked = !!ticket.public;
      $("adDescription").value = ticket.description || "";
      $("adStatus").value = ticket.status || "pending";
      $("adWorker").value = ticket.assigned_worker ? ticket.assigned_worker.id : "";
      $("adOverride").checked = false;
      $("adOverrideReason").value = "";
    } else {
      $("adTicketForm").reset();
      renderEditorOptions();
      $("adPriority").value = "normal";
      $("adOverride").checked = false;
      $("adOverrideReason").value = "";
    }
    if (focusId && $(focusId) && typeof $(focusId).focus === "function") $(focusId).focus();
    if (typeof $("adTicketEditor").scrollIntoView === "function") {
      $("adTicketEditor").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function closeEditor() {
    $("adTicketEditor").hidden = true;
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
    if ((tk.image_ids || []).length) {
      detail.push(row(t("reportPhotos"), tk.image_ids.map(function (id) {
        return '<a href="/api/uploads/' + encodeURIComponent(id) + '" target="_blank">📷</a>';
      }).join(" ")));
    }
    if ((tk.completion_image_ids || []).length) {
      detail.push(row(t("completionPhotos"), tk.completion_image_ids.map(function (id) {
        return '<a href="/api/uploads/' + encodeURIComponent(id) + '" target="_blank">✅📷</a>';
      }).join(" ")));
    }
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
      '<span class="tk-actions"><button type="button" class="btn tiny" data-act="edit-ticket" ' +
      'data-id="' + esc(tk.id) + '">' + esc(t("adEdit")) + "</button>" +
      '<button type="button" class="btn tiny" data-act="status-ticket" data-id="' +
      esc(tk.id) + '">' + esc(t("adStatus")) + "</button>" +
      '<button type="button" class="btn tiny" data-act="assign-ticket" data-id="' +
      esc(tk.id) + '">' + esc(t("adAssignAction")) + "</button>" +
      '<button type="button" class="btn tiny danger" ' +
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

  function loadReferences() {
    return Promise.all([
      DC.api(q("/api/admin/users")),
      DC.api(q("/api/admin/overview"))
    ]).then(function (results) {
      if (results[0].ok) users = (results[0].data && results[0].data.users) || [];
      if (results[1].ok) {
        overview = results[1].data || overview;
        workers = (results[1].data && results[1].data.workers) || [];
      }
      renderEditorOptions();
    });
  }

  function saveTicket(e) {
    e.preventDefault();
    var id = $("adEditId").value;
    var body = {
      lang: DC.lang,
      title: $("adTitle").value.trim(),
      repair_type: $("adRepairType").value,
      priority: $("adPriority").value,
      date: $("adDate").value,
      slots: parseSlots($("adSlots").value),
      student_name: $("adStudentName").value.trim(),
      contact: $("adContactInput").value.trim(),
      college: $("adCollege").value,
      block: $("adBlock").value,
      dorm: $("adDorm").value.trim(),
      public_area: $("adPublic").checked,
      description: $("adDescription").value.trim()
    };
    var path = "/api/admin/ticket/create";
    if (id) {
      path = "/api/admin/ticket/update";
      body.id = id;
      body.expected_version = Number($("adEditVersion").value || 0);
      body.status = $("adStatus").value;
      body.assigned_worker = $("adWorker").value;
      body.override_completion_evidence = $("adOverride").checked;
      body.override_reason = $("adOverrideReason").value.trim();
    } else {
      body.owner_user_id = $("adOwnerUser").value;
      body.client_request_id = "admin-ui-" + Date.now() + "-" +
        Math.random().toString(16).slice(2);
    }
    DC.busy($("adTicketSave"), true);
    DC.post(path, body).then(function (res) {
      DC.busy($("adTicketSave"), false);
      if (!res.ok) { flash(null, "err", res.data); return; }
      flash(id ? "adTicketUpdated" : "adTicketCreated", "ok");
      closeEditor();
      return loadTickets();
    }).catch(function () {
      DC.busy($("adTicketSave"), false);
      flash("err_network", "err");
    });
  }

  function onTicketListClick(e) {
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    if (b.getAttribute("data-act") === "delete-ticket") {
      deleteTicket(b.getAttribute("data-id"), b);
      return;
    }
    var ticket = tickets.filter(function (x) { return x.id === b.getAttribute("data-id"); })[0];
    if (!ticket) return;
    var focus = b.getAttribute("data-act") === "status-ticket" ? "adStatus" :
      (b.getAttribute("data-act") === "assign-ticket" ? "adWorker" : "adTitle");
    openEditor(ticket, focus);
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
    $("adNewTicket").addEventListener("click", function () { openEditor(null); });
    $("adEditorCancel").addEventListener("click", closeEditor);
    $("adTicketCancel").addEventListener("click", closeEditor);
    $("adTicketForm").addEventListener("submit", saveTicket);
    $("adOwnerUser").addEventListener("change", fillOwnerDefaults);
    $("adCollege").addEventListener("change", renderEditorBlocks);
  }

  DC.afterLang(function () {
    renderIdentity();
    renderStatusFilter();
    renderTickets();
    renderEditorOptions();
  });

  wire();

  DC.start(function () {
    return DC.loadConfig().then(function () {
      renderIdentity();
      return loadOverviewLabels();
    }).then(function () {
      renderStatusFilter();
      return loadReferences();
    }).then(function () {
      return loadTickets();
    });
  }, "admin");
})(window.DC);
