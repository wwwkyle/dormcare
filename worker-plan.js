/* DormCare · 排班与派活 — the technician's day
 *
 * One menu card, one page. It answers one question: what am I doing today, and
 * what did the engine decide? The job list itself lives on /jobs.html.
 *
 * Both pages need each other: the 接单 / 完工 button on a timeline stop is
 * decided by that job's live status, so this page loads the pool too. It does
 * not RENDER it — the other page does that.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, md = DC.md, fill = DC.fill;
  var fillSelect = DC.fillSelect, priLabel = DC.priLabel;

  /** Loaded for the action buttons' state, rendered only on /jobs.html. */
  var allTickets = [];

  function $(id) { return document.getElementById(id); }

  /* ====================================================================== */
  /*  identity + pickers                                                    */
  /* ====================================================================== */

  function renderIdentity() {
    var me = DC.me;
    var node = $("wWorkerSelf");
    if (!node) return;
    if (!me) { node.textContent = ""; return; }
    node.textContent = DC.displayName(me);
    var bar = $("workerIdentity");
    if (bar) bar.textContent = fill(t("workerIdentityBar"), { id: me.worker_id || me.username });
  }

  function renderCollegeOptions() {
    if (!DC.cfg) return;
    var sel = $("wCollege");
    if (!sel) return;
    fillSelect(sel, '<option value="">' + esc(t("allColleges")) + "</option>" +
      DC.collegeOptionsHtml());
  }

  function renderDateOptions() {
    if (!DC.cfg) return;
    fillSelect($("wDate"), (DC.cfg.days || []).map(function (d) {
      return '<option value="' + esc(d) + '">' + esc(DC.dayLabel(d)) + "</option>";
    }).join(""));
  }

  /* ====================================================================== */
  /*  the route                                                             */
  /* ====================================================================== */

  function optimize() {
    var m = $("planMsg");
    DC.busy($("optimizeBtn"), true);
    DC.setMsg(m, "", "");
    DC.post("/api/schedule/optimize", { date: $("wDate").value, lang: DC.lang })
      .then(function (res) {
        DC.busy($("optimizeBtn"), false);
        if (!res.ok) {
          m.dataset.code = "";
          m.dataset.state = "err";
          m.textContent = DC.errorText(res.data);
          return;
        }
        DC.lastPlan = res.data;
        renderPlan(res.data);
      }).catch(function () {
        DC.busy($("optimizeBtn"), false);
        DC.setMsg(m, "err_network", "err");
      });
  }

  /** The two moves a technician actually makes. Shared with /jobs.html. */
  function statusOf(ticketId) {
    var tk = allTickets.filter(function (x) { return x.id === ticketId; })[0];
    return (tk && tk.status) || "pending";
  }

  function jobActions(ticketId) {
    var st = statusOf(ticketId);
    function b(act, key, cls) {
      return '<button type="button" class="btn tiny ' + cls + '" data-act="' + act +
        '" data-id="' + esc(ticketId) + '">' + esc(t(key)) + "</button>";
    }
    var closed = (st === "done" || st === "confirmed");
    return (st === "pending" ? b("accepted", "btnAccept", "primary")
                             : b("done", "btnComplete", closed ? "ghost" : "primary"));
  }

  function renderPlan(plan) {
    var jobs = plan.jobs || [];
    var stats = plan.stats || {};
    var filterNode = $("wCollege");
    var filter = filterNode ? filterNode.value : "";

    /* ---- stats ---- */
    var cards = [
      [stats.jobs, t("stJobs"), false],
      [stats.work_minutes, t("stWork"), false],
      [stats.travel_minutes, t("stTravel"), false],
      [stats.idle_minutes, t("stIdle"), false],
      [stats.unscheduled || 0, t("stUnsched"), (stats.unscheduled || 0) > 0],
      [stats.first_start || t("unitNone"), t("stFirst"), false],
      [stats.last_end || t("unitNone"), t("stLast"), false]
    ];
    var row_ = $("statRow");
    if (row_) {
      row_.innerHTML = cards.map(function (c) {
        var isTime = (c[1] === t("stFirst") || c[1] === t("stLast"));
        var unit = (typeof c[0] === "number")
          ? '<span class="stat-unit"> ' + esc(t("unitMin")) + "</span>"
          : (isTime ? "" : "");
        return '<div class="stat' + (c[2] ? " warn" : "") + '"><b>' + esc(c[0]) + unit +
          "</b><span>" + esc(c[1]) + "</span></div>";
      }).join("");
      row_.hidden = false;
    }

    /* ---- timeline ---- */
    var shown = jobs.filter(function (j) { return !filter || j.college === filter; });
    var card = $("timelineCard");
    if (card) card.hidden = false;
    var meta = $("timelineMeta");
    if (meta) {
      meta.textContent = fill(t("stops"), { n: shown.length }) +
        (jobs.length ? " · " + fill(t("schedRange"), {
          a: jobs[0].start, b: jobs[jobs.length - 1].end
        }) : "");
    }

    var line = $("timeline");
    if (line) {
      line.innerHTML = shown.map(function (j) {
        var travel = j.seq === 1
          ? fill(t("travelFromWorkshop"), { m: j.travel_in })
          : (j.travel_in === DC.cfg.travel_rules.same_college_minutes
              ? fill(t("travelSame"), { m: j.travel_in })
              : fill(t("travelCross"), { m: j.travel_in }));
        var back = (j.seq === jobs.length && j.travel_out)
          ? " · " + fill(t("travelBack"), { m: j.travel_out }) : "";

        return '<article class="stop" data-p="' + esc(j.priority) + '">' +
          '<div class="stop-time"><span class="seq">' + j.seq + "</span>" +
          "<strong>" + esc(j.start) + "</strong><em>" + esc(j.end) + "</em></div>" +
          '<div class="stop-main">' +
          '<div class="stop-top">' +
          '<span class="type">' + esc(j.repair_label) + "</span>" +
          '<span class="id">' + esc(j.ticket_id) + "</span>" +
          '<span class="pill ' + esc(j.priority) + '">' + esc(priLabel(j.priority)) + "</span>" +
          '<span class="pill">' + esc(j.duration) + " " + esc(t("minutes")) + "</span>" +
          (j.certified ? '<span class="pill cert">⚠ ' + esc(t("certNeeded")) + "</span>" : "") +
          "</div>" +
          '<div class="stop-meta">' +
          '<span class="lbl2">' + esc(t("tkCollege")) + "：</span>" + esc(j.college_label) +
          (j.block ? ' · <span class="lbl2">' + esc(t("tkBlock")) + "：</span>" + esc(j.block) : "") +
          ' · <span class="lbl2">' + esc(t("tkDorm")) + "：</span>" + esc(j.dorm) +
          (j.student ? " · " + esc(j.student) : "") +
          (j.student_contact ? " · " + esc(j.student_contact) : "") +
          "</div>" +
          (j.symptom ? '<div class="stop-meta"><span class="lbl2">' + esc(t("tkDesc")) +
            "：</span>" + esc(j.symptom) + "</div>" : "") +
          '<div class="stop-meta"><span class="lbl2">' + esc(t("partsNeeded")) +
          "：</span>" + esc(j.parts) + "</div>" +
          '<div class="travel-line">🚶 ' + esc(travel) + esc(back) + "</div>" +
          '<div class="stop-actions">' + jobActions(j.ticket_id) + "</div>" +
          "</div></article>";
      }).join("") || '<p class="empty">' + esc(t("emptyPool")) + "</p>";
    }

    /* ---- 未排进当天的单 / jobs the engine could not place ---- */
    //
    // These are not failures, they are decisions waiting to be made. The engine
    // only knows the declared windows and the shift; the technician knows
    // whether they will be walking past that door anyway. So each row carries
    // enough to decide — address, contact, described symptom, why it did not
    // fit, when the student said they were free — and two buttons that record
    // the answer, which is what finally tells the student something.
    var un = plan.unscheduled || [];
    var box = $("unschedBox");
    if (box) {
      if (un.length) {
        box.hidden = false;
        box.innerHTML = "<h4>⚠ " + esc(t("unschedTitle")) +
          ' <span class="count">' + un.length + "</span></h4>" +
          '<p class="hint">' + esc(t("unschedHint")) + "</p>" +
          un.map(unschedRow).join("");
      } else {
        box.hidden = true;
      }
    }

    var msg = $("planMsg");
    msg.dataset.code = "";
    msg.dataset.state = "ok";
    msg.textContent = (DC.lang === "zh" ? "排班已生成：" : "Schedule generated: ") +
      stats.jobs + " " + t("unitJob") + " · " + stats.work_minutes + "+" +
      stats.travel_minutes + " " + t("minutes");
  }

  /** One decision row for a ticket that would not fit into today's route. */
  function unschedRow(u) {
    var reasonMap = {
      no_time_left: "reasonNoTime",
      outside_availability: "reasonWindow",
      window_too_short: "reasonShort",
      no_availability: "reasonNone"
    };
    var where = esc(u.college_label) +
      (u.block ? " · " + esc(u.block) : "") + " · " + esc(u.dorm);

    var detail = [];
    detail.push(row(t("tkDate"), esc(DC.dayLabel(u.date))));
    detail.push(row(t("tkWindow"),
      esc((u.windows || []).join(" / ") || t("unitNone"))));
    detail.push(row(t("tkDuration"),
      esc(u.duration_minutes) + " " + esc(t("minutes"))));
    detail.push(row(t("tkPriority"), esc(priLabel(u.priority))));
    detail.push(row(t("unschedWhy"), esc(t(reasonMap[u.reason] || "reasonWindow"))));
    if (u.parts) detail.push(row(t("partsNeeded"), esc(u.parts)));
    if (u.student || u.contact) {
      detail.push(row(t("tkContact"),
        esc(u.student || "") + (u.student && u.contact ? " · " : "") +
        esc(u.contact || "")));
    }
    if (u.description) detail.push(row(t("tkDesc"), esc(u.description)));

    var dayOpts = dateOptions(u.date);
    var actions;
    if (u.off_route) {
      actions = '<span class="unsched-done">✓ ' + esc(t("unschedTaken")) + "</span>";
    } else if (u.reschedule_requested) {
      actions = '<span class="unsched-done">→ ' + esc(t("unschedSuggested")) +
        (u.suggested_date ? " · " + esc(DC.dayLabel(u.suggested_date)) : "") + "</span>";
    } else {
      actions =
        '<button type="button" class="btn tiny primary" data-act="offroute-take" data-id="' +
          esc(u.ticket_id) + '">' + esc(t("btnTakeEnRoute")) + "</button>" +
        '<select class="unsched-date" data-date-for="' + esc(u.ticket_id) + '" aria-label="' +
          esc(t("unschedPickDate")) + '">' + dayOpts + "</select>" +
        '<button type="button" class="btn tiny" data-act="offroute-suggest" data-id="' +
          esc(u.ticket_id) + '">' + esc(t("btnSuggestReschedule")) + "</button>";
    }

    return '<article class="unsched-item' + (u.public ? " public" : "") + '" data-p="' +
      esc(u.priority) + '">' +
      '<div class="unsched-top">' +
      '<span class="type">' + esc(u.repair_label) + "</span>" +
      '<code class="id">' + esc(u.ticket_id) + "</code>" +
      '<span class="pill ' + esc(u.priority) + '">' + esc(priLabel(u.priority)) + "</span>" +
      (u.public ? '<span class="pill public">' + esc(t("tagPublic")) + "</span>" : "") +
      "</div>" +
      '<p class="unsched-where">' + where + "</p>" +
      '<div class="unsched-body">' + detail.join("") + "</div>" +
      '<div class="unsched-actions">' + actions + "</div>" +
      "</article>";
  }

  /** The schedulable days as <option>s, minus the one already being planned. */
  function dateOptions(skipDay) {
    var days = ((DC.cfg && DC.cfg.days) || []).filter(function (d) { return d !== skipDay; });
    return days.map(function (d) {
      return '<option value="' + esc(d) + '">' + esc(DC.dayLabel(d)) + "</option>";
    }).join("");
  }

  /** A label/value pair inside a card body. */
  function row(label, value) {
    return '<div class="u-row"><span class="u-k">' + esc(label) +
      '</span><span class="u-v">' + value + "</span></div>";
  }

  /** Record the technician's answer for an off-route job, then re-plan. */
  function onUnschedAction(e) {
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    var id = b.getAttribute("data-id");
    var act = b.getAttribute("data-act");
    var body = { id: id, lang: DC.lang };
    if (act === "offroute-take") {
      body.action = "take";
    } else {
      body.action = "reschedule";
      var sel = $("unschedBox").querySelector('select[data-date-for="' + id + '"]');
      body.suggested_date = sel ? sel.value : "";
    }
    DC.busy(b, true);
    DC.post("/api/tickets/offroute", body).then(function (res) {
      DC.busy(b, false);
      var m = $("planMsg");
      m.dataset.code = "";
      if (!res.ok) {
        m.dataset.state = "err";
        m.textContent = DC.errorText(res.data);
        return;
      }
      m.dataset.state = "ok";
      m.textContent = t(act === "offroute-take" ? "offrouteTaken" : "offrouteSuggested");
      // Re-plan so the row shows the recorded answer instead of the buttons.
      optimize();
    }).catch(function () {
      DC.busy(b, false);
      DC.setMsg($("planMsg"), "err_network", "err");
    });
  }

  /* ====================================================================== */
  /*  AI briefing                                                           */
  /* ====================================================================== */

  function askAI(mode) {
    if (!DC.lastPlan) { DC.setMsg($("planMsg"), "needOptimizeFirst", "err"); return; }
    var btn = mode === "rationale" ? $("rationaleBtn") : $("dispatchBtn");
    DC.busy(btn, true);
    DC.post("/api/dispatch/advice", {
      date: $("wDate").value, lang: DC.lang, mode: mode
    }).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) {
        var m = $("planMsg");
        m.dataset.code = "";
        m.dataset.state = "err";
        m.textContent = DC.errorText(res.data);
        return;
      }
      $("aiCard").hidden = false;
      $("aiCardTitle").textContent = t(mode === "rationale" ? "rationaleBtn" : "dispatchBtn");
      $("aiModeTag").textContent = res.data.ai_enabled ? t("aiMode") : t("aiFallback");
      $("aiOut").innerHTML = md(res.data.reply || "");
      $("aiCard").scrollIntoView({ behavior: "smooth", block: "start" });
    }).catch(function () {
      DC.busy(btn, false);
      DC.setMsg($("planMsg"), "err_network", "err");
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function loadPool() {
    return DC.loadTickets().then(function () { allTickets = DC.tickets; });
  }

  function onAction(e) {
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    var id = b.getAttribute("data-id");
    // `data-act` is already the target status ("accepted" / "done"); the server
    // checks that this job really is on our route before accepting it.
    DC.post("/api/tickets/status", {
      id: id, status: b.getAttribute("data-act"), lang: DC.lang
    }).then(function (res) {
      if (!res.ok) {
        var m = $("planMsg");
        m.dataset.code = "";
        m.dataset.state = "err";
        m.textContent = DC.errorText(res.data);
      }
      loadPool().then(function () { optimize(); });
    });
  }

  function wire() {
    $("optimizeBtn").addEventListener("click", optimize);
    $("dispatchBtn").addEventListener("click", function () { askAI("dispatch"); });
    $("rationaleBtn").addEventListener("click", function () { askAI("rationale"); });
    $("wCollege").addEventListener("change", function () {
      if (DC.lastPlan) renderPlan(DC.lastPlan);
    });
    $("wDate").addEventListener("change", optimize);
    $("reloadTickets").addEventListener("click", function () {
      loadPool().then(function () { optimize(); });
    });
    $("timeline").addEventListener("click", onAction);
    // Its own listener: the off-route panel answers a different question
    // ("can you fit it in?") from the one the timeline answers ("done?").
    $("unschedBox").addEventListener("click", onUnschedAction);
  }

  DC.afterLang(function () {
    renderCollegeOptions();
    renderDateOptions();
    renderIdentity();
    if (DC.lastPlan) renderPlan(DC.lastPlan);
  });

  wire();

  // `"worker"` as the second argument: the server refuses to hand this page to
  // anybody else, and the JS agrees — a stranger who guesses the URL is sent
  // back to the menu rather than shown an empty shell.
  DC.start(function () {
    return DC.loadConfig().then(function () {
      renderCollegeOptions();
      renderDateOptions();
      renderIdentity();
      // The pool is loaded first so each stop's 接单 / 完工 button reflects that
      // job's live status.
      return loadPool();
    }).then(function () {
      // Opening this page should already show the day's route: the whole point
      // of this side is the schedule, not an empty form.
      if ($("wDate").value) optimize();
    });
  }, "worker");
})(window.DC);
