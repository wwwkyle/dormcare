/* DormCare · 我要报修 — the repair request page
 *
 * One menu card, one page, one job: describe the fault and file the request.
 * Everything else a student can do — read their records, correct their room,
 * change their password — lives on its own page behind its own card.
 *
 * The AI intake and the form are on the same page on purpose: the intake
 * FILLS the form, so splitting them would mean the student filling in the same
 * fields twice or being sent away and back with the text in their hands.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, md = DC.md, fill = DC.fill;
  var fillSelect = DC.fillSelect, priLabel = DC.priLabel, pairsText = DC.pairsText;
  var SLOT_COUNT = DC.SLOT_COUNT;

  var types = [];
  var priorities = ["urgent", "high", "normal", "low"];
  var selType = null;
  var selPriority = "normal";
  var slots = [];
  var slotPainting = null;
  //: Off by default. The address is a property of the account, so the room
  //: fields stay hidden until the student says this is a shared area.
  var publicArea = false;

  function $(id) { return document.getElementById(id); }

  /* ====================================================================== */
  /*  chips                                                                 */
  /* ====================================================================== */

  function renderTypeChips() {
    var node = $("typeChips");
    if (!node) return;
    // The fixed labour time used to ride along here as a <small>. It is an
    // internal scheduling constant: showing it to the student invites an
    // argument about a number that was never negotiable, and it is none of
    // their business how the technician's day is costed.
    node.innerHTML = types.map(function (x) {
      return '<button type="button" data-t="' + esc(x.id) + '" aria-pressed="' +
        (x.id === selType ? "true" : "false") + '" title="' +
        esc(DC.lang === "zh" ? x.zh_note : x.en_note) + '">' +
        esc(DC.lang === "zh" ? x.zh : x.en) + "</button>";
    }).join("");
  }

  function renderPriorityChips() {
    var node = $("priChips");
    if (!node) return;
    node.innerHTML = priorities.map(function (p) {
      var info = (DC.cfg && DC.cfg.priorities && DC.cfg.priorities[p]) || {};
      var name = info[DC.lang] || p;
      var desc = (DC.lang === "zh" ? info.zh_desc : info.en_desc) || "";
      return '<button type="button" data-p="' + esc(p) + '" aria-pressed="' +
        (p === selPriority ? "true" : "false") + '" title="' + esc(desc) + '">' +
        esc(name) + "</button>";
    }).join("");
  }

  /* ====================================================================== */
  /*  address + dates                                                       */
  /* ====================================================================== */

  function renderCollegeOptions() {
    if (!DC.cfg) return;
    fillSelect($("fCollege"), DC.collegeOptionsHtml());
    renderBlockOptions();
  }

  /** Block/tower picker — driven by the college's recorded block list. */
  function renderBlockOptions() {
    var sel = $("fCollege"), blk = $("fBlock"), hint = $("blockHint");
    if (!sel || !blk || !DC.cfg) return;
    fillSelect(blk, DC.blockOptionsHtml(sel.value));
    blk.disabled = !DC.realBlockCount(sel.value);
    if (hint) hint.textContent = DC.blockHintText(sel.value);
  }

  function renderDateOptions() {
    if (!DC.cfg) return;
    fillSelect($("fDate"), (DC.cfg.days || []).map(function (d) {
      return '<option value="' + esc(d) + '">' + esc(DC.dayLabel(d)) + "</option>";
    }).join(""));
  }

  /**
   * The address block: locked to the account, unlocked only for a shared area.
   *
   * This used to be three editable fields filled in on every report, which is
   * how one account ended up holding tickets in nine colleges. The server now
   * stamps the address from the account, so the inputs only exist for the one
   * case that legitimately needs a different address.
   */
  function renderAddress(user) {
    var own = $("addrOwn"), fields = $("addrFields"), note = $("addrNote");
    if (own) own.textContent = DC.ownAddress(user) || t("addrUnknown");
    if (fields) fields.hidden = !publicArea;
    if (note) note.textContent = publicArea ? t("addrPublicNote") : t("addrLockedNote");
    var box = $("addr");
    if (box) box.dataset.public = publicArea ? "1" : "0";
    if (publicArea) renderBlockOptions();
  }

  /* ====================================================================== */
  /*  availability grid                                                     */
  /* ====================================================================== */

  function renderSlots() {
    var node = $("slotGrid");
    if (!node) return;
    var html = "";
    for (var i = 0; i < SLOT_COUNT; i++) {
      var lunch = (i >= DC.LUNCH_FROM && i < DC.LUNCH_TO);
      html += '<button type="button" data-i="' + i + '" class="' + (lunch ? "lunch" : "") +
        '" aria-pressed="' + (slots[i] ? "true" : "false") + '">' +
        DC.slotTimeText(i) + "</button>";
    }
    node.innerHTML = html;
    renderSlotSummary();
  }

  /** Merge consecutive selected slots into [start, end] pairs. */
  function selectedPairs() {
    var pairs = [], run = null;
    for (var i = 0; i < SLOT_COUNT; i++) {
      if (slots[i]) {
        if (run === null) run = i;
      } else if (run !== null) {
        pairs.push([run, i - 1]);
        run = null;
      }
    }
    if (run !== null) pairs.push([run, SLOT_COUNT - 1]);
    return pairs;
  }

  function renderSlotSummary() {
    var node = $("slotSummary");
    if (!node) return;
    var pairs = selectedPairs();
    if (!pairs.length) {
      node.className = "slot-summary";
      node.textContent = t("slotNone");
    } else {
      var count = slots.filter(Boolean).length;
      node.className = "slot-summary has";
      node.textContent = fill(t("slotPicked"), { n: count, list: pairsText(pairs) });
    }
  }

  function setAll(on) {
    slots = slots.map(function () { return on; });
    renderSlots();
  }

  /* ====================================================================== */
  /*  AI intake                                                             */
  /* ====================================================================== */

  /**
   * The AI's answer, in the order a student actually needs it:
   *
   *   1. what it thinks is wrong,
   *   2. what it still does not know — as questions the student can answer
   *      right here, one click from being pasted into the description box,
   *   3. what is worth trying before anybody turns up.
   *
   * The fixed labour time is deliberately absent. It is an internal scheduling
   * constant — telling a resident "this takes 60 minutes" only invites an
   * argument about a number that was never negotiable.
   */
  function renderIntake(res, extraHtml) {
    var d = res.data || {};
    var out = $("intakeOut");
    var html = "";
    var questions = [];
    if (d.out_of_scope) {
      html = "<strong>" + esc(t("outOfScope")) + "</strong><br>" + esc(d.message || "");
    } else {
      html = "<strong>" + esc(t("aiDetected")) + "</strong>" + '<div class="kv">' +
        "<b>" + esc(t("aiClass")) + "：" + esc(d.repair_label || "") + "</b>" +
        "<b>" + esc(t("aiPriority")) + "：" + esc(priLabel(d.priority)) + "</b>" +
        "<b>" + esc(t("aiConfidence")) + "：" + esc(t("conf" + DC.cap(d.confidence))) + "</b>" +
        "<b>" + esc(t("aiSource")) + "：" +
        esc(d.source === "model" ? t("sourceModel") : t("sourceRules")) + "</b>" +
        (d.needs_certified ? "<b>⚠ " + esc(t("certNeeded")) + "</b>" : "") +
        (d.parts ? "<b>" + esc(t("partsNeeded")) + "：" + esc(d.parts) + "</b>" : "") +
        "</div>";
      if (d.reason) {
        html += '<div class="ai-note">' + esc(t("aiReason")) + "：" + esc(d.reason) + "</div>";
      }

      // 追问 — the description was thin, so ask instead of guessing.
      questions = d.follow_up || [];
      if (d.need_more_info && questions.length) {
        html += '<div class="ai-ask"><h4>❓ ' + esc(t("aiAskTitle")) + "</h4><ul>" +
          questions.map(function (q) { return "<li>" + esc(q) + "</li>"; }).join("") +
          "</ul><button type=\"button\" class=\"btn tiny\" id=\"aiAskFill\">" +
          esc(t("aiAskFill")) + "</button></div>";
      }

      // 自检建议 — things worth trying before anybody turns up with a toolbox.
      var tips = d.self_help || [];
      if (tips.length) {
        html += '<div class="ai-tips"><h4>🛠 ' + esc(t("aiTipsTitle")) + "</h4><ol>" +
          tips.map(function (s) { return "<li>" + md(s) + "</li>"; }).join("") +
          "</ol></div>";
      }
    }
    if (extraHtml) html += extraHtml;
    out.innerHTML = html;
    out.hidden = false;

    var fillBtn = $("aiAskFill");
    if (fillBtn) {
      fillBtn.addEventListener("click", function () {
        var desc = $("fDesc");
        var block = questions.map(function (q) { return "• " + q + " → "; }).join("\n");
        desc.value = (desc.value.trim() ? desc.value.trim() + "\n" : "") + block;
        desc.focus();
        desc.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }
  }

  function doIntake() {
    var out = $("intakeOut"), msg = $("formMsg");
    var text = $("intakeText").value.trim();
    if (!text) { DC.setMsg(msg, "err_empty_text", "err"); return; }
    DC.busy($("intakeBtn"), true);
    out.hidden = true;
    DC.post("/api/intake/parse", { text: text, lang: DC.lang }).then(function (res) {
      DC.busy($("intakeBtn"), false);
      if (!res.ok) {
        out.hidden = false;
        out.textContent = DC.errorText(res.data);
        return;
      }
      renderIntake(res, null);
      var d = res.data;
      if (!d.out_of_scope) {
        selType = d.repair_type;
        selPriority = d.priority || "normal";
        $("formAiTag").hidden = false;
        renderTypeChips();
        renderPriorityChips();
        var desc = $("fDesc");
        if (!desc.value.trim()) desc.value = text;
        $("typeChips").scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }).catch(function () {
      DC.busy($("intakeBtn"), false);
      out.hidden = false;
      out.textContent = t("err_network");
    });
  }

  function doAdvice() {
    var out = $("intakeOut"), msg = $("formMsg");
    var text = $("intakeText").value.trim();
    if (!text) { DC.setMsg(msg, "err_empty_text", "err"); return; }
    DC.busy($("intakeAdviceBtn"), true);
    out.hidden = true;
    var ctx = {
      repair_type: selType,
      priority: selPriority,
      college: publicArea ? $("fCollege").value : ((DC.me && DC.me.college) || ""),
      dorm: publicArea ? $("fDorm").value.trim() : ((DC.me && DC.me.dorm) || ""),
      window_text: pairsText(selectedPairs())
    };
    DC.post("/api/intake/advice", { text: text, lang: DC.lang, context: ctx }).then(function (res) {
      DC.busy($("intakeAdviceBtn"), false);
      out.hidden = false;
      if (!res.ok) { out.textContent = DC.errorText(res.data); return; }
      out.innerHTML = md(res.data.reply || "");
    }).catch(function () {
      DC.busy($("intakeAdviceBtn"), false);
      out.hidden = false;
      out.textContent = t("err_network");
    });
  }

  /* ====================================================================== */
  /*  submit                                                                */
  /* ====================================================================== */

  function submitTicket() {
    var pairs = selectedPairs();
    var msg = $("formMsg");
    if (!selType) { DC.setMsg(msg, "needType", "err"); return; }
    // The room is only the student's problem to state when it is NOT their
    // own room — otherwise the server already knows it.
    if (publicArea && !$("fDorm").value.trim()) {
      DC.setMsg(msg, "needPublicWhere", "err");
      return;
    }
    if (!pairs.length) { DC.setMsg(msg, "needSlots", "err"); return; }

    DC.busy($("submitBtn"), true);
    DC.setMsg(msg, "", "");

    var out = $("intakeOut");
    var aiReason = "";
    if (!out.hidden) {
      var note = out.querySelector(".ai-note");
      if (note) aiReason = note.textContent.slice(0, 300);
    }

    var payload = {
      lang: DC.lang,
      // The one flag that lets a ticket carry an address other than the
      // account's own room. Everything below it is ignored unless it is set.
      public_area: publicArea,
      repair_type: selType,
      priority: selPriority,
      date: $("fDate").value,
      slots: pairs,
      description: $("fDesc").value.trim(),
      student_name: $("fName").value.trim(),
      contact: $("fContact").value.trim(),
      ai_reason: aiReason,
      ai_source: ""
    };
    if (publicArea) {
      payload.college = $("fCollege").value;
      payload.block = $("fBlock") ? ($("fBlock").value || "") : "";
      payload.dorm = $("fDorm").value.trim();
    }

    DC.post("/api/tickets", payload).then(function (res) {
      DC.busy($("submitBtn"), false);
      if (!res.ok) {
        msg.dataset.code = "";
        msg.dataset.state = "err";
        msg.textContent = DC.errorText(res.data);
        return;
      }
      msg.dataset.code = "";
      msg.dataset.state = "ok";
      // The new ticket now lives on the records page, so send them there
      // rather than leaving them on a form they have already submitted.
      DC.setMsg(msg, "submitOkRedirect", "ok");
      window.setTimeout(function () { window.location.replace("/records.html"); }, 1400);
      DC.refreshHealth();
    }).catch(function () {
      DC.busy($("submitBtn"), false);
      DC.setMsg(msg, "err_network", "err");
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function wire() {
    $("typeChips").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-t]");
      if (!b) return;
      selType = b.getAttribute("data-t");
      renderTypeChips();
    });

    $("priChips").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-p]");
      if (!b) return;
      selPriority = b.getAttribute("data-p");
      renderPriorityChips();
    });

    $("fCollege").addEventListener("change", renderBlockOptions);

    // Ticking "public area" is the only way to unlock the address fields.
    $("fPublic").addEventListener("change", function () {
      publicArea = !!this.checked;
      if (publicArea) {
        // Start from the student's own college so the picker is never blank,
        // then let them move the pin to wherever the shared area actually is.
        if (DC.me && DC.me.college) $("fCollege").value = DC.me.college;
      }
      renderAddress();
    });

    /* drag-select the availability grid */
    var grid = $("slotGrid");
    function slotFromEvent(e) {
      var b = e.target.closest("button[data-i]");
      return b ? Number(b.getAttribute("data-i")) : null;
    }
    grid.addEventListener("mousedown", function (e) {
      var i = slotFromEvent(e);
      if (i === null) return;
      e.preventDefault();
      slotPainting = !slots[i];
      slots[i] = slotPainting;
      renderSlots();
    });
    grid.addEventListener("mouseover", function (e) {
      if (slotPainting === null) return;
      var i = slotFromEvent(e);
      if (i === null || slots[i] === slotPainting) return;
      slots[i] = slotPainting;
      renderSlots();
    });
    document.addEventListener("mouseup", function () { slotPainting = null; });
    grid.addEventListener("click", function (e) {
      // keyboard / tap fallback: mousedown already handled mouse, so only act
      // when the pointer did not come from a mouse drag
      if (e.detail === 0) {
        var i = slotFromEvent(e);
        if (i === null) return;
        slots[i] = !slots[i];
        renderSlots();
      }
    });

    $("slotClear").addEventListener("click", function () { setAll(false); });
    $("slotMorning").addEventListener("click", function () {
      slots = slots.map(function (_, i) { return i < DC.LUNCH_FROM; }); renderSlots();
    });
    $("slotAfternoon").addEventListener("click", function () {
      slots = slots.map(function (_, i) { return i >= DC.LUNCH_TO; }); renderSlots();
    });
    $("slotAll").addEventListener("click", function () { setAll(true); });

    $("intakeBtn").addEventListener("click", doIntake);
    $("intakeAdviceBtn").addEventListener("click", doAdvice);
    $("intakeClearBtn").addEventListener("click", function () {
      $("intakeText").value = "";
      $("intakeOut").hidden = true;
      $("formAiTag").hidden = true;
      DC.setMsg($("formMsg"), "", "");
    });

    $("submitBtn").addEventListener("click", submitTicket);
  }

  DC.afterLang(function () {
    renderCollegeOptions();
    renderTypeChips();
    renderPriorityChips();
    renderDateOptions();
    renderSlots();
    renderAddress();
  });

  wire();
  slots = slots.map(function () { return false; });

  // `"student"` is the second argument: identity is settled before the page
  // paints, and anyone who is not a student is sent to the menu instead.
  DC.start(function (user) {
    return DC.loadConfig().then(function () {
      types = (DC.cfg && DC.cfg.repair_types) || [];
      if (!selType && types.length) selType = types[0].id;
      renderCollegeOptions();
      // The picker starts on the student's own college — it is hidden until
      // "public area" is ticked, and it should already point somewhere sane
      // when it appears.
      if (user && user.college) $("fCollege").value = user.college;
      renderBlockOptions();
      renderTypeChips();
      renderPriorityChips();
      renderDateOptions();
      renderSlots();
      renderAddress(user);
    });
  }, "student");
})(window.DC);
