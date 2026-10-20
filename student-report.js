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
  var reportUploads = [];
  var agentSessionId = "";

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

  function renderPhotos() {
    var node = $("photoPreview");
    node.innerHTML = reportUploads.map(function (x) {
      return '<figure><img src="' + esc(x.url) + '" alt=""><button type="button" ' +
        'class="btn tiny danger" data-remove-photo="' + esc(x.id) + '">×</button></figure>';
    }).join("");
  }

  function fileData(file) {
    return new Promise(function (resolve, reject) {
      if (!/^(image\/jpeg|image\/png)$/.test(file.type) || file.size > 5 * 1024 * 1024) {
        reject(new Error(t("photoInvalid"))); return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        resolve({ mime: file.type, data: String(reader.result).split(",", 2)[1] });
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  function uploadFiles(files, purpose) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return Promise.resolve([]);
    if (list.length + reportUploads.length > 5) return Promise.reject(new Error(t("photoTooMany")));
    return Promise.all(list.map(fileData)).then(function (images) {
      return DC.post("/api/uploads", { purpose: purpose || "ticket", images: images });
    }).then(function (res) {
      if (!res.ok) throw new Error(DC.errorText(res.data));
      return res.data.uploads || [];
    });
  }

  /* ====================================================================== */
  /*  Unified conversational intake                                         */
  /* ====================================================================== */

  var conversation = [];

  function renderConversation() {
    var log = $("agentLog");
    log.hidden = !conversation.length;
    log.textContent = conversation.map(function (x) {
      return (x.role === "user" ? t("agentYou") : t("agentAssistant")) + "： " + x.text;
    }).join("\n\n");
  }

  function applyAgentDraft(draft) {
    if (!draft) return;
    if (draft.repair_type) selType = draft.repair_type;
    if (draft.priority) selPriority = draft.priority;
    if (draft.description) $("fDesc").value = draft.description;
    $("formAiTag").hidden = false;
    renderTypeChips();
    renderPriorityChips();
    $("agentDraft").hidden = false;
    $("agentDraft").textContent =
      t("agentDraftSummary") + "\n" +
      t("aiClass") + "：" + (draft.repair_type || "") + "\n" +
      t("aiPriority") + "：" + priLabel(draft.priority) + "\n" +
      t("lblDesc") + "：" + (draft.description || "") + "\n" +
      t("reportPhotos") + "：" + (draft.image_ids || []).length;
  }

  function sendAgentMessage() {
    var text = $("intakeText").value.trim();
    if (!text && !reportUploads.length) {
      DC.setMsg($("formMsg"), "err_empty_text", "err");
      return;
    }
    DC.busy($("agentSend"), true);
    var start = agentSessionId ? Promise.resolve() :
      DC.post("/api/agent/session", { lang: DC.lang }).then(function (res) {
        if (!res.ok) throw new Error(DC.errorText(res.data));
        agentSessionId = res.data.session.id;
      });
    start.then(function () {
      return DC.post("/api/agent/message", {
        id: agentSessionId,
        text: text,
        image_ids: reportUploads.map(function (x) { return x.id; })
      });
    }).then(function (res) {
      if (!res.ok) throw new Error(DC.errorText(res.data));
      conversation.push({
        role: "user",
        text: text || fill(t("agentImagesOnly"), { n: reportUploads.length })
      });
      conversation.push({
        role: "assistant",
        text: (res.data.safety_warning ? "⚠ " + res.data.safety_warning + "\n" : "") +
          res.data.reply
      });
      renderConversation();
      applyAgentDraft(res.data.draft);
      $("agentConfirm").hidden = !res.data.ready;
      $("intakeText").value = "";
      DC.setMsg($("formMsg"), "", "");
    }).catch(function (e) {
      conversation.push({ role: "assistant", text: e.message || t("err_network") });
      renderConversation();
    }).then(function () {
      DC.busy($("agentSend"), false);
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
      ai_reason: "",
      ai_source: "",
      image_ids: reportUploads.map(function (x) { return x.id; })
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

    $("agentSend").addEventListener("click", sendAgentMessage);
    $("intakeClearBtn").addEventListener("click", function () {
      $("intakeText").value = "";
      $("fDesc").value = "";
      $("agentLog").hidden = true;
      $("agentDraft").hidden = true;
      $("agentConfirm").hidden = true;
      $("formAiTag").hidden = true;
      conversation = [];
      reportUploads = [];
      agentSessionId = "";
      renderPhotos();
      DC.setMsg($("formMsg"), "", "");
    });

    $("submitBtn").addEventListener("click", submitTicket);

    $("photoInput").addEventListener("change", function () {
      uploadFiles(this.files, "agent").then(function (items) {
        reportUploads = reportUploads.concat(items);
        $("agentConfirm").hidden = true;
        renderPhotos();
      }).catch(function (e) { $("formMsg").textContent = e.message; });
      this.value = "";
    });
    $("photoPreview").addEventListener("click", function (e) {
      var b = e.target.closest("[data-remove-photo]");
      if (!b) return;
      reportUploads = reportUploads.filter(function (x) {
        return x.id !== b.getAttribute("data-remove-photo");
      });
      $("agentConfirm").hidden = true;
      renderPhotos();
    });

    var Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Speech) {
      $("speechBtn").disabled = true;
      $("speechHint").textContent = t("speechUnsupported");
    } else {
      $("speechBtn").addEventListener("click", function () {
        var rec = new Speech();
        rec.lang = DC.lang === "zh" ? "zh-CN" : "en-US";
        rec.onresult = function (event) {
          var text = event.results[0][0].transcript || "";
          $("intakeText").value = ($("intakeText").value.trim() + " " + text).trim();
          $("intakeText").focus();
        };
        rec.onerror = function () { $("speechHint").textContent = t("speechFailed"); };
        try { rec.start(); } catch (e) { $("speechHint").textContent = t("speechFailed"); }
      });
    }

    $("agentConfirm").addEventListener("click", function () {
      var pairs = selectedPairs();
      if (!pairs.length) { DC.setMsg($("formMsg"), "needSlots", "err"); return; }
      DC.busy($("agentConfirm"), true);
      DC.post("/api/agent/confirm", {
        id: agentSessionId, date: $("fDate").value, slots: pairs,
        contact: $("fContact").value.trim(),
        repair_type: selType, priority: selPriority,
        description: $("fDesc").value.trim(),
        public_area: publicArea,
        college: publicArea ? $("fCollege").value : "",
        block: publicArea ? $("fBlock").value : "",
        dorm: publicArea ? $("fDorm").value.trim() : ""
      }).then(function (res) {
        if (!res.ok) throw new Error(DC.errorText(res.data));
        window.location.replace("/records.html");
      }).catch(function (e) {
        DC.busy($("agentConfirm"), false);
        DC.setMsg($("formMsg"), "", "err");
        $("formMsg").textContent = e.message;
      });
    });
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
