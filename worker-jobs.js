/* DormCare · 我的工单 — the jobs assigned to this technician
 *
 * One menu card, one page. It answers one question: what jobs are mine, and
 * what state are they in. The route and the AI briefing live on /plan.html.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc;
  var priLabel = DC.priLabel, pairsText = DC.pairsText, row = DC.row;

  var allTickets = [];
  var allExpanded = false;
  var completingId = "";

  function $(id) { return document.getElementById(id); }

  /* ====================================================================== */
  /*  identity                                                              */
  /* ====================================================================== */

  function renderIdentity() {
    var me = DC.me;
    if (!me) return;
    var node = $("wWorkerSelf");
    var bar = $("workerIdentity");
    // The identity read-out lives on the schedule page; here the role bar only
    // needs the staff number, so a missing node is not a failure.
    if (bar) {
      bar.textContent = DC.fill(t("workerIdentityBar"),
        { id: me.worker_id || me.username });
    }
    if (node) node.textContent = DC.displayName(me);
  }

  /* ====================================================================== */
  /*  one job row                                                           */
  /* ====================================================================== */

  function statusOf(ticketId) {
    var tk = allTickets.filter(function (x) { return x.id === ticketId; })[0];
    return (tk && tk.status) || "pending";
  }

  /**
   * The two moves a technician actually makes: 接单 when they set off, 完工 when
   * the job is done. Everything after that belongs to the student (验收).
   * The `data-act` values ARE the target statuses, which is why onAction can
   * forward them straight to the server.
   */
  function jobActions(ticketId) {
    var st = statusOf(ticketId);
    function b(act, key, cls) {
      return '<button type="button" class="btn tiny ' + cls + '" data-act="' + act +
        '" data-id="' + esc(ticketId) + '">' + esc(t(key)) + "</button>";
    }
    var closed = (st === "done" || st === "confirmed");
    if (closed) return "";
    return (st === "pending" ? b("accepted", "btnAccept", "primary")
                             : b("done", "btnComplete", "primary"));
  }

  function poolRow(tk) {
    var done = (tk.status === "done" || tk.status === "confirmed");
    var badges = '<span class="pill ' + esc(tk.status) + '">' +
        esc(tk.status_label || "") + "</span>" +
      '<span class="pill ' + esc(tk.priority) + '">' + esc(priLabel(tk.priority)) +
      "</span>" + '<span class="pill">' + esc(tk.duration_minutes) + " " +
      esc(t("minutes")) + "</span>";

    var where = esc(tk.college_label) +
      (tk.block ? " · " + esc(tk.block) : "") + " · " + esc(tk.dorm);

    var detail = [];
    detail.push(row(t("tkAppointment"), tk.appointment
      ? '<strong class="appt">' + esc(DC.dayLabel(tk.date)) + " " +
        esc(tk.appointment.start) + "-" + esc(tk.appointment.end) + "</strong>"
      : '<span class="muted">' + esc(t("tkNoAppointment")) + "</span>"));
    detail.push(row(t("tkWindow"), esc(pairsText(tk.slots))));
    detail.push(row(t("tkDate"), esc(DC.dayLabel(tk.date))));
    detail.push(row(t("tkDuration"), esc(tk.duration_minutes) + " " + esc(t("minutes"))));
    if (tk.student_name || tk.contact) {
      detail.push(row(t("tkContact"),
        esc(tk.student_name || "") + (tk.student_name && tk.contact ? " · " : "") +
        esc(tk.contact || "")));
    }
    if (tk.description) detail.push(row(t("tkDesc"), esc(tk.description)));
    if (tk.image_ids && tk.image_ids.length) {
      detail.push(row(t("reportPhotos"), '<span class="ticket-images">' +
        tk.image_ids.map(function (id) {
          return '<a href="/api/uploads/' + esc(id) + '" target="_blank" rel="noopener">' +
            '<img src="/api/uploads/' + esc(id) + '" alt=""></a>';
        }).join("") + "</span>"));
    }
    if (tk.ai_reason) detail.push(row("🤖", esc(tk.ai_reason)));
    // A completed job carries the student's verdict back to the technician —
    // the feedback half of the loop, visible on the side that earned it.
    if (tk.rating > 0) {
      var stars = "";
      for (var i = 1; i <= 5; i++) stars += (i <= tk.rating) ? "★" : "☆";
      detail.push(row(t("tkFeedback"), "<span class=\"fb-stars\">" + stars + "</span>" +
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
      '<span class="tk-actions">' + jobActions(tk.id) + "</span></div></div></details>";
  }

  function renderPool() {
    var node = $("ticketList");
    if (!node) return;
    var count = $("poolCount");
    if (count) count.textContent = allTickets.length
      ? (DC.lang === "zh" ? allTickets.length + " 条" : allTickets.length + " tickets") : "";
    if (!allTickets.length) {
      node.innerHTML = '<p class="empty">' + esc(t("emptyPool")) + "</p>";
      return;
    }
    node.innerHTML = allTickets.map(poolRow).join("");
    var items = node.querySelectorAll("details.fold-item");
    for (var i = 0; i < items.length; i++) items[i].open = allExpanded;
  }

  function loadPool() {
    return DC.loadTickets().then(function () {
      allTickets = DC.tickets;
      renderPool();
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function onAction(e) {
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    var id = b.getAttribute("data-id");
    if (b.getAttribute("data-act") === "done") {
      completingId = id;
      $("completionPhotos").click();
      return;
    }
    // `data-act` is already the target status; the server checks that this job
    // really is ours before accepting it.
    DC.post("/api/tickets/status", {
      id: id, status: b.getAttribute("data-act"), lang: DC.lang
    }).then(function (res) {
      if (!res.ok) {
        var m = $("planMsg");
        m.dataset.code = "";
        m.dataset.state = "err";
        m.textContent = DC.errorText(res.data);
      }
      loadPool();
    });
  }

  function wire() {
    $("reloadTickets").addEventListener("click", loadPool);
    $("toggleAll").addEventListener("click", function () {
      allExpanded = !allExpanded;
      var items = $("ticketList").querySelectorAll("details.fold-item");
      for (var i = 0; i < items.length; i++) items[i].open = allExpanded;
      this.textContent = t(allExpanded ? "collapseAll" : "expandAll");
    });
    $("ticketList").addEventListener("click", onAction);
    $("completionPhotos").addEventListener("change", function () {
      var files = Array.prototype.slice.call(this.files || []);
      this.value = "";
      if (!completingId || !files.length || files.length > 5) return;
      Promise.all(files.map(function (file) {
        return new Promise(function (resolve, reject) {
          if (!/^(image\/jpeg|image\/png)$/.test(file.type) ||
              file.size > 5 * 1024 * 1024) {
            reject(new Error(t("photoInvalid"))); return;
          }
          var reader = new FileReader();
          reader.onload = function () {
            resolve({ mime: file.type, data: String(reader.result).split(",", 2)[1] });
          };
          reader.onerror = reject;
          reader.readAsDataURL(file);
        });
      })).then(function (images) {
        return DC.post("/api/uploads", { purpose: "completion", images: images });
      }).then(function (res) {
        if (!res.ok) throw new Error(DC.errorText(res.data));
        return DC.post("/api/tickets/status", {
          id: completingId, status: "done", lang: DC.lang,
          completion_image_ids: (res.data.uploads || []).map(function (x) { return x.id; })
        });
      }).then(function (res) {
        if (!res.ok) throw new Error(DC.errorText(res.data));
        completingId = ""; return loadPool();
      }).catch(function (err) {
        $("planMsg").textContent = err.message || t("err_generic");
      });
    });
  }

  DC.afterLang(function () {
    renderIdentity();
    renderPool();
    var btn = $("toggleAll");
    if (btn) btn.textContent = t(allExpanded ? "collapseAll" : "expandAll");
  });

  wire();

  DC.start(function () { return loadPool(); }, "worker");
})(window.DC);
