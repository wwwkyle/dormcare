/* DormCare · 账号管理 — the account desk
 *
 * One menu card, one page: the three things that genuinely need somebody with
 * authority and no stake in any one ticket — a room typed wrong at sign-up, a
 * forgotten password, and issuing a technician account.
 *
 * The roster is on /roster.html; the board on /tickets.html.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;

  var users = [];
  var createMode = null;          // null | "student" | "worker"
  var specPicked = {};            // specialty id -> true, for the create form

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

  /* The college picker here allows an EMPTY choice, unlike the report form's:
   * a row may be a technician or an administrator, who have no college at all. */
  function collegeOptionsHtml(selected) {
    return '<option value="">' + esc(t("regCollegeNone")) + "</option>" +
      DC.collegeOptionsHtml(selected);
  }

  /* ====================================================================== */
  /*  the account list                                                      */
  /* ====================================================================== */

  function roomText(u) {
    if (u.role !== "student") return '<span class="muted">' + esc(t("unitNone")) + "</span>";
    if (!u.college_label && !u.dorm) {
      return '<span class="muted">' + esc(t("adNoRoom")) + "</span>";
    }
    return esc(u.college_label) + (u.block ? " · " + esc(u.block) : "") +
      " · " + esc(u.dorm);
  }

  function userRow(u) {
    var isStudent = (u.role === "student");
    var meta = isStudent
      ? fill(t("adTicketCount"), { n: u.ticket_count || 0 })
      : (u.trade || "");

    var body = [];
    if (isStudent) {
      body.push('<div class="grid2">' +
        '<div><label class="lbl">' + esc(t("lblRegName")) + "</label>" +
        '<input type="text" id="ad-name-' + esc(u.id) + '" maxlength="40" value="' +
        esc(u.name || "") + '"></div>' +
        '<div><label class="lbl">' + esc(t("lblRegDorm")) + "</label>" +
        '<input type="text" id="ad-dorm-' + esc(u.id) + '" maxlength="30" value="' +
        esc(u.dorm || "") + '"></div>' +
        "</div>" +
        '<div class="grid2">' +
        '<div><label class="lbl">' + esc(t("lblRegCollege")) + "</label>" +
        '<select id="ad-college-' + esc(u.id) + '" data-college-for="' + esc(u.id) + '">' +
        collegeOptionsHtml(u.college) + "</select></div>" +
        '<div><label class="lbl">' + esc(t("lblBlock")) + "</label>" +
        '<select id="ad-block-' + esc(u.id) + '">' +
        DC.blockOptionsHtml(u.college, u.block) + "</select></div>" +
        "</div>");
    } else {
      body.push('<div class="tk-row"><span class="lbl2">' + esc(t("adTrade")) +
        "</span><span>" + esc(u.trade || t("unitNone")) + "</span></div>");
      if (u.phone) {
        body.push('<div class="tk-row"><span class="lbl2">' + esc(t("adPhone")) +
          "</span><span>" + esc(u.phone) + "</span></div>");
      }
    }
    body.push('<div class="ad-actions">' +
      (isStudent
        ? '<button type="button" class="btn tiny primary" data-act="save-user" data-id="' +
          esc(u.id) + '">' + esc(t("adSave")) + "</button>"
        : "") +
      '<input type="password" id="ad-pass-' + esc(u.id) +
      '" class="ad-pass" maxlength="128" placeholder="' + esc(t("adNewPw")) + '">' +
      '<button type="button" class="btn tiny" data-act="reset-pw" data-id="' +
      esc(u.id) + '">' + esc(t("adResetPw")) + "</button>" +
      "</div>");

    return '<details class="ad-item">' +
      "<summary>" +
      '<span class="who-role ' + esc(u.role) + '">' + esc(DC.roleLabel(u.role)) + "</span>" +
      '<span class="ad-name">' + esc(u.name || u.username) + "</span>" +
      '<code class="who-id">' + esc(u.worker_id || u.username) + "</code>" +
      '<span class="ad-room">' + roomText(u) + "</span>" +
      (meta ? '<span class="ad-meta">' + esc(meta) + "</span>" : "") +
      '<span class="chev" aria-hidden="true">▾</span>' +
      "</summary>" +
      '<div class="ad-body">' + body.join("") + "</div></details>";
  }

  function renderUsers() {
    var node = $("adUserList");
    if (!node) return;
    var count = $("adUserCount");
    if (count) count.textContent = users.length ? fill(t("adUserCount"), { n: users.length }) : "";
    node.innerHTML = users.length
      ? users.map(userRow).join("")
      : '<p class="empty">' + esc(t("adNoData")) + "</p>";
  }

  /* ====================================================================== */
  /*  issue an account                                                      */
  /* ====================================================================== */

  function setCreateMsg(code, state, data) {
    var m = $("adCreateMsg");
    if (!m) return;
    m.dataset.code = "";
    if (code) { m.dataset.state = state || "ok"; m.textContent = t(code); }
    else if (data) { m.dataset.state = "err"; m.textContent = DC.errorText(data); }
    else { m.dataset.state = ""; m.textContent = ""; }
  }

  function renderCreateCollege() {
    var sel = $("adCreateCollege");
    if (!sel) return;
    sel.innerHTML = collegeOptionsHtml(sel.value || "");
    renderCreateBlock();
  }

  function renderCreateBlock() {
    var college = $("adCreateCollege"), blk = $("adCreateBlock");
    if (!college || !blk) return;
    blk.innerHTML = DC.blockOptionsHtml(college.value, blk.value || "");
    blk.disabled = !DC.realBlockCount(college.value);
  }

  function renderSpecs() {
    var node = $("adCreateSpecs");
    if (!node || !DC.cfg) return;
    node.innerHTML = (DC.cfg.repair_types || []).map(function (x) {
      return '<button type="button" data-s="' + esc(x.id) + '" aria-pressed="' +
        (specPicked[x.id] ? "true" : "false") + '">' +
        esc(DC.lang === "zh" ? x.zh : x.en) + "</button>";
    }).join("");
  }

  function clearCreateInputs() {
    ["adCreateUser", "adCreateName", "adCreatePass", "adCreateDorm",
     "adCreateTradeZh", "adCreateTradeEn", "adCreatePhone"].forEach(function (id) {
      var el = $(id);
      if (el) el.value = "";
    });
    var cb = $("adCreateCertified");
    if (cb) cb.checked = false;
  }

  function openCreate(mode) {
    createMode = mode;
    specPicked = {};
    var card = $("adCreateCard");
    if (card) card.hidden = false;
    var title = $("adCreateTitle");
    if (title) title.textContent = t(mode === "worker" ? "adCreateWorker" : "adCreateStudent");
    var lbl = $("adCreateUserLbl");
    if (lbl) lbl.textContent = t(mode === "worker" ? "adWorkerId" : "adAccount");
    var sf = $("adCreateStudentFields");
    if (sf) sf.hidden = (mode === "worker");
    var wf = $("adCreateWorkerFields");
    if (wf) wf.hidden = (mode !== "worker");
    clearCreateInputs();
    setCreateMsg("");
    if (mode === "worker") renderSpecs();
    else renderCreateCollege();
    if (card && card.scrollIntoView) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function closeCreate() {
    createMode = null;
    var card = $("adCreateCard");
    if (card) card.hidden = true;
    setCreateMsg("");
  }

  function submitCreate() {
    if (!createMode) return;
    var btn = $("adCreateSubmit");
    var username = ($("adCreateUser") ? $("adCreateUser").value : "").trim();
    var name = ($("adCreateName") ? $("adCreateName").value : "").trim();
    var password = $("adCreatePass") ? $("adCreatePass").value : "";
    if (!username) { setCreateMsg("auth_missing_username", "err"); return; }
    if (!name) { setCreateMsg("auth_missing_name", "err"); return; }
    if (!password) { setCreateMsg("auth_missing_password", "err"); return; }

    var path, body, okKey;
    if (createMode === "worker") {
      var specs = Object.keys(specPicked).filter(function (k) { return specPicked[k]; });
      // A technician with no coverage would never be assigned anything: the
      // engine matches on `specialties`, so an empty list is a dead account.
      if (!specs.length) { setCreateMsg("err_need_specialty", "err"); return; }
      okKey = "adCreatedWorker";
      path = "/api/admin/worker/create";
      body = {
        worker_id: username, name: name, password: password,
        trade_zh: ($("adCreateTradeZh") ? $("adCreateTradeZh").value : "").trim(),
        trade_en: ($("adCreateTradeEn") ? $("adCreateTradeEn").value : "").trim(),
        phone: ($("adCreatePhone") ? $("adCreatePhone").value : "").trim(),
        certified: !!($("adCreateCertified") && $("adCreateCertified").checked),
        specialties: specs, lang: DC.lang
      };
    } else {
      var college = $("adCreateCollege") ? $("adCreateCollege").value : "";
      var blk = $("adCreateBlock");
      var block = blk ? (blk.value || "") : "";
      var dorm = ($("adCreateDorm") ? $("adCreateDorm").value : "").trim();
      if (!college) { setCreateMsg("auth_missing_college", "err"); return; }
      if (blk && !blk.disabled && !block) { setCreateMsg("auth_need_block", "err"); return; }
      if (!dorm) { setCreateMsg("auth_missing_dorm", "err"); return; }
      okKey = "adCreatedUser";
      path = "/api/admin/user/create";
      body = {
        username: username, name: name, password: password,
        college: college, block: block, dorm: dorm, lang: DC.lang
      };
    }

    DC.busy(btn, true);
    DC.post(path, body).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) { setCreateMsg(null, "err", res.data); return; }
      closeCreate();
      flash(okKey, "ok");
      return loadUsers();
    }).catch(function () {
      DC.busy(btn, false);
      setCreateMsg("err_network", "err");
    });
  }

  /* ====================================================================== */
  /*  edits                                                                 */
  /* ====================================================================== */

  function saveUser(id, btn) {
    var nameEl = $("ad-name-" + id);
    var dormEl = $("ad-dorm-" + id);
    var collegeEl = $("ad-college-" + id);
    var blockEl = $("ad-block-" + id);
    var body = {
      id: id, lang: DC.lang,
      name: nameEl ? nameEl.value.trim() : "",
      college: collegeEl ? collegeEl.value : "",
      block: blockEl ? (blockEl.value || "") : "",
      dorm: dormEl ? dormEl.value.trim() : ""
    };
    DC.busy(btn, true);
    DC.post("/api/admin/user/update", body).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) { flash(null, "err", res.data); return; }
      flash("adSaved", "ok");
      return loadUsers();
    }).catch(function () {
      DC.busy(btn, false);
      flash("err_network", "err");
    });
  }

  function resetPassword(id, btn) {
    var input = $("ad-pass-" + id);
    var password = input ? input.value : "";
    if (!password) {
      var m = $("adMsg");
      if (m) { m.dataset.state = "err"; m.textContent = t("auth_missing_password"); }
      return;
    }
    DC.busy(btn, true);
    DC.post("/api/admin/user/password", { id: id, password: password, lang: DC.lang })
      .then(function (res) {
        DC.busy(btn, false);
        if (!res.ok) { flash(null, "err", res.data); return; }
        if (input) input.value = "";
        flash("adPwReset", "ok");
      }).catch(function () {
        DC.busy(btn, false);
        flash("err_network", "err");
      });
  }

  /* ====================================================================== */
  /*  data                                                                  */
  /* ====================================================================== */

  function q(path) { return path + "?lang=" + encodeURIComponent(DC.lang); }

  function loadUsers() {
    return DC.api(q("/api/admin/users")).then(function (res) {
      if (!res.ok) return;
      users = (res.data && res.data.users) || [];
      renderUsers();
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function on(id, ev, fn) {
    var el = $(id);
    if (el) el.addEventListener(ev, fn);
  }

  function onUserListClick(e) {
    var b = e.target.closest("button[data-act]");
    if (!b) return;
    e.preventDefault();
    var act = b.getAttribute("data-act");
    var id = b.getAttribute("data-id");
    if (act === "save-user") saveUser(id, b);
    else if (act === "reset-pw") resetPassword(id, b);
  }

  /** Changing a row's college re-populates that row's tower list. */
  function onUserListChange(e) {
    var sel = e.target.closest("select[data-college-for]");
    if (!sel) return;
    var id = sel.getAttribute("data-college-for");
    var blk = $("ad-block-" + id);
    if (blk) blk.innerHTML = DC.blockOptionsHtml(sel.value, "");
  }

  function onSpecClick(e) {
    var b = e.target.closest("button[data-s]");
    if (!b) return;
    e.preventDefault();
    var key = b.getAttribute("data-s");
    specPicked[key] = !specPicked[key];
    renderSpecs();
  }

  function wire() {
    on("adReload", "click", function () { loadUsers(); });
    on("adUserList", "click", onUserListClick);
    on("adUserList", "change", onUserListChange);
    on("adNewStudentBtn", "click", function () { openCreate("student"); });
    on("adNewWorkerBtn", "click", function () { openCreate("worker"); });
    on("adCreateCollege", "change", renderCreateBlock);
    on("adCreateSpecs", "click", onSpecClick);
    on("adCreateSubmit", "click", submitCreate);
    on("adCreateCancel", "click", closeCreate);
  }

  DC.afterLang(function () {
    renderIdentity();
    renderUsers();
    if (createMode === "worker") renderSpecs();
    else if (createMode === "student") renderCreateCollege();
    var title = $("adCreateTitle");
    if (title && createMode) {
      title.textContent = t(createMode === "worker" ? "adCreateWorker" : "adCreateStudent");
    }
    var lbl = $("adCreateUserLbl");
    if (lbl && createMode) {
      lbl.textContent = t(createMode === "worker" ? "adWorkerId" : "adAccount");
    }
  });

  wire();

  DC.start(function () {
    return DC.loadConfig().then(function () {
      renderIdentity();
      renderCreateCollege();
      renderSpecs();
      return loadUsers();
    });
  }, "admin");
})(window.DC);
