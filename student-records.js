/* DormCare · 我的报修记录 — the student's own repair records
 *
 * One menu card, one page. It answers exactly one question: what is happening
 * with the repairs I filed. Filing a new request is a different page
 * (/report.html), and correcting the room is another (/account.html).
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;
  var priLabel = DC.priLabel, pairsText = DC.pairsText, stars = DC.stars;
  var row = DC.row, workerName = DC.workerName;

  var myTickets = [];
  var allExpanded = false;
  /** Star picks made in this session, before they are submitted back. */
  var pendingRating = {};

  function $(id) { return document.getElementById(id); }

  /* ====================================================================== */
  /*  one record row                                                        */
  /* ====================================================================== */

  /**
   * One collapsible row per ticket. The <details> element gives us keyboard
   * access and native expand/collapse for free; we only style it.
   *
   * The row is where the loop finally closes: who is coming, when, and — once
   * the job is done — the student's own verdict on it.
   */
  function ticketRow(tk) {
    var badges = '<span class="pill ' + esc(tk.status || "pending") + '">' +
        esc(tk.status_label || "") + "</span>" +
      '<span class="pill ' + esc(tk.priority) + '">' + esc(priLabel(tk.priority)) +
      // The queue state, spelled out. "Somebody is responsible for this" and
      // "somebody is coming at 09:00" are different facts, and the student is
      // owed the one that is actually true.
      "</span>" + '<span class="pill q-' + esc(tk.queue_state || "unassigned") + '">' +
      esc(tk.queue_label || "") + "</span>";

    var where = esc(tk.college_label) +
      (tk.block ? " · " + esc(tk.block) : "") + " · " + esc(tk.dorm);
    // A ticket filed against a shared area is labelled, so a resident can tell
    // their own room apart from the corridor at a glance.
    if (tk.public) {
      badges += '<span class="pill public">' + esc(t("tagPublic")) + "</span>";
    }

    var detail = [];
    detail.push(row(t("tkWorker"), tk.assigned_worker
      ? esc(workerName(tk.assigned_worker)) +
        (tk.assigned_worker.worker_id
          ? ' <code class="who-id">' + esc(tk.assigned_worker.worker_id) + "</code>" : "")
      : '<span class="muted">' + esc(t("tkNoWorker")) + "</span>"));
    detail.push(row(t("tkAppointment"), tk.appointment
      ? '<strong class="appt">' + esc(DC.dayLabel(tk.date)) + " " +
        esc(tk.appointment.start) + "-" + esc(tk.appointment.end) + "</strong>"
      : '<span class="muted">' + esc(tk.queue_label || t("tkNoAppointment")) + "</span>"));
    detail.push(row(t("tkWindow"), esc(pairsText(tk.slots))));
    detail.push(row(t("tkDate"), esc(DC.dayLabel(tk.date))));
    detail.push(row(t("tkPriority"), esc(priLabel(tk.priority))));
    if (tk.student_name || tk.contact) {
      detail.push(row(t("tkContact"),
        esc(tk.student_name || "") + (tk.student_name && tk.contact ? " · " : "") +
        esc(tk.contact || "")));
    }
    if (tk.description) detail.push(row(t("tkDesc"), esc(tk.description)));
    if (tk.ai_reason) detail.push(row("🤖", esc(tk.ai_reason)));

    return '<details class="ticket fold-item st-' + esc(tk.status || "pending") +
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
      (tk.appointment && tk.assigned_worker
        ? '<p class="tk-note">' + esc(fill(t("studentEtaNote"), {
            w: workerName(tk.assigned_worker),
            d: DC.dayLabel(tk.date),
            a: tk.appointment.start
          })) + "</p>"
        : "") +
      (tk.queue_state === "off_route" && tk.assigned_worker
        ? '<p class="tk-note">' + esc(fill(t("studentOffRouteNote"), {
            w: workerName(tk.assigned_worker)
          })) + "</p>"
        : "") +
      (tk.queue_state === "reschedule" ? rescheduleBlock(tk) : "") +
      feedbackBlock(tk) +
      '<div class="tk-foot"><code>' + esc(tk.id) + "</code>" +
      '<span class="tk-actions">' + ticketActions(tk) + "</span></div></div></details>";
  }

  /**
   * The technician says "today is full — shall we move it?" — and the student,
   * who owns the timetable, gets the last word. Accepting keeps the windows
   * they already declared and moves the ticket to the suggested day, where the
   * engine gets another go at placing it.
   */
  function rescheduleBlock(tk) {
    var when = tk.suggested_date ? DC.dayLabel(tk.suggested_date) : t("unitNone");
    return '<div class="resched">' +
      '<p class="resched-head">🗓 ' +
      esc(fill(t("reschedOffer"), { d: when })) + "</p>" +
      (tk.reschedule_note
        ? '<p class="resched-note">' + esc(t("reschedNote")) + "：" +
          esc(tk.reschedule_note) + "</p>"
        : "") +
      '<div class="resched-actions">' +
      '<button type="button" class="btn tiny primary" data-act="resched-accept" data-id="' +
        esc(tk.id) + '">' + esc(t("btnReschedAccept")) + "</button>" +
      '<button type="button" class="btn tiny ghost" data-act="resched-decline" data-id="' +
        esc(tk.id) + '">' + esc(t("btnReschedDecline")) + "</button>" +
      "</div></div>";
  }

  function ticketActions(tk) {
    var out = [];
    function b(act, label, cls) {
      return '<button type="button" class="btn tiny ' + cls + '" data-act="' + act +
        '" data-id="' + esc(tk.id) + '">' + esc(label) + "</button>";
    }
    if (tk.status === "done") {
      out.push(b("confirm", t("btnConfirm"), "primary"));
      out.push(b("reopen", t("btnReopen"), "ghost"));
    } else if (tk.status === "confirmed") {
      out.push(b("reopen", t("btnReopen"), "ghost"));
    } else if (tk.status === "pending") {
      out.push(b("del", DC.lang === "zh" ? "删除" : "Delete", "danger"));
    }
    return out.join("");
  }

  /** Five clickable stars, or a read-only verdict once one has been given. */
  function feedbackBlock(tk) {
    if (tk.rating > 0) {
      return '<div class="fb fb-done">' +
        '<div class="fb-head"><span class="lbl2">' + esc(t("fbCurrent")) + "</span>" +
        '<span class="fb-stars">' + stars(tk.rating) + "</span></div>" +
        (tk.feedback ? '<p class="fb-text">' + esc(tk.feedback) + "</p>" : "") +
        "</div>";
    }
    if (tk.status !== "done") return "";
    return '<div class="fb">' +
      '<p class="fb-hint">' + esc(t("fbHint")) + "</p>" +
      '<div class="fb-head"><span class="lbl2">' + esc(t("fbRating")) + "</span>" +
      '<span class="fb-stars" id="stars-' + esc(tk.id) + '">' + stars(0, tk.id) +
      "</span></div>" +
      '<textarea class="fb-note" rows="2" maxlength="500" data-id="' + esc(tk.id) +
      '" data-i18n-ph="fbPlaceholder" placeholder="' + esc(t("fbPlaceholder")) +
      '"></textarea>' +
      '<div class="row"><button type="button" class="btn tiny primary" data-act="feedback" ' +
      'data-id="' + esc(tk.id) + '">' + esc(t("fbSubmit")) + "</button></div>" +
      "</div>";
  }

  function renderTickets() {
    var node = $("ticketList");
    if (!node) return;
    var count = $("ticketCount");
    if (count) count.textContent = myTickets.length
      ? (DC.lang === "zh" ? myTickets.length + " 条" : myTickets.length + " records") : "";

    if (!myTickets.length) {
      node.innerHTML = '<p class="empty">' + esc(t("emptyTickets")) + "</p>";
      return;
    }
    node.innerHTML = myTickets.map(ticketRow).join("");
    // preserve the "expand all" state across re-renders
    var items = node.querySelectorAll("details.fold-item");
    for (var i = 0; i < items.length; i++) items[i].open = allExpanded;
  }

  /* ====================================================================== */
  /*  data                                                                  */
  /* ====================================================================== */

  /**
   * The server already returns exactly this account's tickets, so the browser
   * holds no ledger of its own. The previous version kept a list of ticket ids
   * in localStorage — which meant the history vanished with the browser cache
   * and did not follow the student to another device.
   */
  function loadMine() {
    return DC.loadTickets().then(function () {
      myTickets = DC.tickets;
      renderTickets();
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function wire() {
    $("reloadTickets").addEventListener("click", loadMine);

    $("toggleAll").addEventListener("click", function () {
      allExpanded = !allExpanded;
      var items = $("ticketList").querySelectorAll("details.fold-item");
      for (var i = 0; i < items.length; i++) items[i].open = allExpanded;
      this.textContent = t(allExpanded ? "collapseAll" : "expandAll");
    });

    $("ticketList").addEventListener("click", function (e) {
      // ---- stars: paint locally first so the click feels instant ---------- //
      var star = e.target.closest("button[data-star]");
      if (star) {
        e.preventDefault();
        e.stopPropagation();
        var sid = star.getAttribute("data-id");
        var n = Number(star.getAttribute("data-star"));
        pendingRating[sid] = n;
        var box = $("stars-" + sid);
        if (box) box.innerHTML = stars(n, sid);
        return;
      }

      var b = e.target.closest("button[data-act]");
      if (!b) return;
      e.preventDefault();
      e.stopPropagation();
      var id = b.getAttribute("data-id");
      var act = b.getAttribute("data-act");

      if (act === "del") {
        DC.post("/api/tickets/delete", { id: id }).then(function (res) {
          if (!res.ok) {
            $("formMsg").dataset.code = "";
            $("formMsg").dataset.state = "err";
            $("formMsg").textContent = DC.errorText(res.data);
          }
          loadMine();
        });
        return;
      }

      if (act === "feedback") {
        var note = $("ticketList").querySelector('textarea.fb-note[data-id="' + id + '"]');
        DC.post("/api/tickets/feedback", {
          id: id,
          rating: pendingRating[id] || 0,
          feedback: note ? note.value.trim() : "",
          lang: DC.lang
        }).then(function (res) {
          delete pendingRating[id];
          if (!res.ok) {
            $("formMsg").dataset.code = "";
            $("formMsg").dataset.state = "err";
            $("formMsg").textContent = DC.errorText(res.data);
          } else {
            // A 1-2 star verdict re-opens the job rather than filing it away,
            // so the confirmation has to say which of the two just happened.
            var back = res.data.ticket && res.data.ticket.status === "pending";
            DC.setMsg($("formMsg"), back ? "fbReopened" : "fbThanks", "ok");
          }
          loadMine();
        });
        return;
      }

      // The technician offered another day — the student answers.
      if (act === "resched-accept" || act === "resched-decline") {
        var accept = act === "resched-accept";
        DC.post("/api/tickets/reschedule", {
          id: id, accept: accept, lang: DC.lang
        }).then(function (res) {
          var msg = $("formMsg");
          msg.dataset.code = "";
          if (!res.ok) {
            msg.dataset.state = "err";
            msg.textContent = DC.errorText(res.data);
          } else {
            msg.dataset.state = "ok";
            msg.textContent = t(accept ? "reschedAccepted" : "reschedDeclined");
          }
          loadMine();
        });
      }
    });
  }

  DC.afterLang(function () {
    renderTickets();
    var btn = $("toggleAll");
    if (btn) btn.textContent = t(allExpanded ? "collapseAll" : "expandAll");
  });

  wire();

  DC.start(function () { return loadMine(); }, "student");
})(window.DC);
