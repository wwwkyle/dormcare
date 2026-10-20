/* DormCare · 账号设置 — account settings
 *
 * One menu card, one page, two things that used to need the maintenance
 * office and now do not: correcting the room on file, and changing the
 * password. Both act on the signed-in account only — the request carries no
 * account id at all, which is why these two can be open to every role.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc;
  var fillSelect = DC.fillSelect;

  function $(id) { return document.getElementById(id); }

  /* ====================================================================== */
  /*  the room half is for students only                                    */
  /* ====================================================================== */

  /**
   * A technician has no dorm and an administrator is not a resident, so the
   * room card is not merely disabled for them — it is removed. The server
   * refuses `POST /api/account/room` for either role anyway; hiding it means
   * the page does not offer an action that cannot succeed.
   */
  function applyRole(me) {
    var isStudent = !!me && me.role === "student";
    var block = document.getElementById("accRoomBlock");
    if (block) block.hidden = !isStudent;
    var hint = $("accRoomOnlyNote");
    if (hint) hint.hidden = isStudent;
    var title = $("accTitle");
    if (title) title.textContent = t(isStudent ? "accTitle" : "accPwTitleOnly");
    // The tile and the role pill follow the role too, so this page looks like
    // the console it belongs to instead of a neutral form.
    var mark = $("accMark");
    if (mark) {
      mark.textContent = isStudent ? "🎓" : (me && me.role === "worker" ? "🛠️" : "🧭");
      mark.className = "mark" + (isStudent ? "" : (me && me.role === "worker" ? " work" : " admin"));
    }
    var pill = $("accRole");
    if (pill && me) {
      pill.textContent = DC.roleLabel(me.role);
      pill.className = "here " + me.role;
    }
    var note = document.querySelector("[data-rolebar-note]");
    if (note && me) {
      var key = isStudent ? "menuStudentAccount"
        : (me.role === "worker" ? "menuWorkerAccount" : "menuAdminAccount");
      var attr = note.getAttribute("data-i18n");
      if (attr) note.setAttribute("data-i18n", key);
      note.textContent = t(key);
    }
    return isStudent;
  }

  function renderCollegeOptions() {
    if (!DC.cfg) return;
    fillSelect($("aCollege"), DC.collegeOptionsHtml());
  }

  /** The account's tower picker, driven by the account's tower list. */
  function renderBlockOptions() {
    var sel = $("aCollege"), blk = $("aBlock"), hint = $("aBlockHint");
    if (!sel || !blk || !DC.cfg) return;
    fillSelect(blk, DC.blockOptionsHtml(sel.value));
    blk.disabled = !DC.realBlockCount(sel.value);
    if (hint) hint.textContent = DC.blockHintText(sel.value);
  }

  /** Fill the pickers from the account, once — never on a language repaint. */
  function fillAccountForm(user) {
    var me = user || DC.me;
    if (!me) return;
    renderCollegeOptions();
    if (me.college && $("aCollege")) $("aCollege").value = me.college;
    renderBlockOptions();
    if ($("aBlock")) $("aBlock").value = me.block || "";
    if ($("aDorm")) $("aDorm").value = me.dorm || "";
    renderRoomNow(me);
  }

  function renderRoomNow(me) {
    var node = $("accRoomNow");
    if (!node) return;
    var room = DC.ownAddress(me || DC.me);
    node.textContent = (t("accCurrentRoom") + "：" + (room || t("addrUnknown")));
  }

  function saveRoom() {
    var btn = $("accRoomSave"), msg = $("accRoomMsg");
    var college = ($("aCollege") ? $("aCollege").value : "") || "";
    var block = $("aBlock") ? ($("aBlock").value || "") : "";
    var dorm = ($("aDorm") ? $("aDorm").value : "").trim();
    DC.busy(btn, true);
    DC.setMsg(msg, "", "");
    DC.post("/api/account/room", {
      college: college, block: block, dorm: dorm, lang: DC.lang
    }).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) {
        DC.setMsg(msg, (res.data && res.data.code) || "err_generic", "err");
        if (!(res.data && res.data.code)) msg.textContent = DC.errorText(res.data);
        return;
      }
      // The server returns the account as it now stands. Trust that rather than
      // the form: the room on file is what every later ticket is filed against,
      // so the page must show what the SERVER recorded, not what was typed.
      DC.me = res.data.user;
      DC.setMsg(msg, "accRoomOk", "ok");
      renderRoomNow(DC.me);
    }).catch(function () {
      DC.busy(btn, false);
      DC.setMsg(msg, "err_network", "err");
    });
  }

  /* ====================================================================== */
  /*  password                                                             */
  /* ====================================================================== */

  function savePassword() {
    var btn = $("accPwSave"), msg = $("accPwMsg");
    var cur = ($("aPwCur") ? $("aPwCur").value : "") || "";
    var next = ($("aPwNew") ? $("aPwNew").value : "") || "";
    var again = ($("aPwNew2") ? $("aPwNew2").value : "") || "";
    if (next !== again) {
      DC.setMsg(msg, "accPwMismatch", "err");
      return;
    }
    DC.busy(btn, true);
    DC.setMsg(msg, "", "");
    DC.post("/api/account/password", {
      current_password: cur, password: next, lang: DC.lang
    }).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) {
        DC.setMsg(msg, (res.data && res.data.code) || "err_generic", "err");
        if (!(res.data && res.data.code)) msg.textContent = DC.errorText(res.data);
        return;
      }
      // Clear the three boxes: leaving the new password sitting in a
      // type="password" field is asking for it to be read over a shoulder.
      ["aPwCur", "aPwNew", "aPwNew2"].forEach(function (id) {
        if ($(id)) $(id).value = "";
      });
      DC.setMsg(msg, "accPwOk", "ok");
    }).catch(function () {
      DC.busy(btn, false);
      DC.setMsg(msg, "err_network", "err");
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function wire() {
    var college = $("aCollege");
    if (college) college.addEventListener("change", renderBlockOptions);
    var roomBtn = $("accRoomSave");
    if (roomBtn) roomBtn.addEventListener("click", saveRoom);
    var pwBtn = $("accPwSave");
    if (pwBtn) pwBtn.addEventListener("click", savePassword);
  }

  DC.afterLang(function () {
    // Repaint only. The form is NOT refilled here: a language switch must
    // never overwrite half-typed corrections, and the college/tower labels in
    // the picker do follow the language through these two calls.
    var me = DC.me || {};
    applyRole(me);
    renderCollegeOptions();
    var accCollege = $("aCollege");
    if (accCollege && me.college) accCollege.value = me.college;
    renderBlockOptions();
    renderRoomNow();
  });

  wire();

  // No role argument: the password half of this page is valid for every role,
  // and the server refuses the room half for anyone who is not a student. The
  // page therefore requires only that somebody is signed in, and hides the
  // room card from the roles that have no room.
  DC.start(function (user) {
    if (!user) return null;
    applyRole(user);
    return DC.loadConfig().then(function () {
      if (user.role === "student") fillAccountForm(user);
    });
  });
})(window.DC);
