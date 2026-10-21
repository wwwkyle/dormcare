/* DormCare · 登录页 — sign-in / student sign-up
 *
 * The other half of the split: until you have an account, there are no
 * consoles at all — just this page. Which console you land on is decided by
 * the SERVER (`home` in the login response), so the role can never be chosen
 * from the browser.
 *
 * Sign-in is the default view. Student sign-up is available from the tab above
 * the form, keeping the two tasks in one focused panel.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;

  function $(id) { return document.getElementById(id); }

  var DEMO = [
    { role: "student", account: "2024001", password: "dorm2026",
      college: "shaw", block: "东座 1 栋", dorm: "A-301" },
    { role: "student", account: "2024002", password: "dorm2026",
      college: "diligentia", block: "A 座", dorm: "F-303" },
    { role: "worker",  account: "A01 / B02 / C03", password: "fix2026" },
    { role: "admin",   account: "admin", password: "admin2026" }
  ];

  /* ====================================================================== */
  /*  renderers                                                             */
  /* ====================================================================== */

  /** College display name for an id, falling back to the raw id. */
  function collegeName(id) {
    var hit = ((DC.cfg && DC.cfg.colleges) || []).filter(function (c) {
      return c.id === id;
    })[0];
    if (!hit) return id || "";
    return DC.lang === "zh" ? hit.zh : hit.en;
  }

  function renderDemo() {
    var body = $("demoBody");
    if (!body) return;
    body.innerHTML = DEMO.map(function (d) {
      var role = DC.roleLabel(d.role);
      // The room is part of the demo: it is the account's address, so it is
      // worth showing that each demo student lives somewhere different.
      var room = d.dorm
        ? esc(collegeName(d.college)) + (d.block ? " · " + esc(d.block) : "") +
          " · " + esc(d.dorm)
        : '<span class="muted">—</span>';
      return "<tr><td><span class=\"who-role " + esc(d.role) + "\">" + esc(role) +
        "</span></td><td><code>" + esc(d.account) + "</code></td><td><code>" +
        esc(d.password) + "</code></td><td class=\"demo-room\">" + room + "</td></tr>";
    }).join("");
  }

  function setAuthView(view) {
    var register = view === "register";
    $("loginPane").hidden = register;
    $("registerPane").hidden = !register;
    $("loginTab").classList.toggle("active", !register);
    $("registerTab").classList.toggle("active", register);
    $("loginTab").setAttribute("aria-selected", register ? "false" : "true");
    $("registerTab").setAttribute("aria-selected", register ? "true" : "false");
    var first = register ? $("regUser") : $("loginUser");
    if (first) first.focus();
  }

  function renderCollegeOptions() {
    var sel = $("regCollege");
    if (!sel || !DC.cfg) return;
    var keep = sel.value;
    var html = '<option value="">' + esc(t("regCollegeNone")) + "</option>" +
      (DC.cfg.colleges || []).map(function (c) {
        return '<option value="' + esc(c.id) + '">' +
          esc(DC.lang === "zh" ? c.zh : c.en) + "</option>";
      }).join("");
    sel.innerHTML = html;
    sel.value = keep || "";
    renderBlockOptions();
  }

  /**
   * Tower picker for the chosen college.
   *
   * The registration form asks for the room because the room IS the address
   * every future ticket gets filed against — collecting it once here is what
   * stops a report from arriving from an unknown place later.
   */
  function renderBlockOptions() {
    var sel = $("regCollege"), blk = $("regBlock"), hint = $("regBlockHint");
    if (!sel || !blk || !DC.cfg) return;
    var college = (DC.cfg.colleges || []).filter(function (c) {
      return c.id === sel.value;
    })[0];
    var blocks = (college && college.blocks) || [];
    var real = blocks.filter(function (b) { return b.indexOf("以现场标识为准") < 0; });
    var keep = blk.value;
    blk.innerHTML = '<option value="">' + esc(t("unitNone")) + "</option>" +
      real.map(function (b) {
        return '<option value="' + esc(b) + '">' + esc(b) + "</option>";
      }).join("");
    blk.value = (keep && real.indexOf(keep) >= 0) ? keep : "";
    blk.disabled = !real.length;
    if (hint) {
      hint.textContent = college
        ? (real.length ? (DC.lang === "zh" ? college.blocks_zh : college.blocks_en)
                       : t("blkAuto"))
        : "";
    }
  }

  /* ====================================================================== */
  /*  actions                                                               */
  /* ====================================================================== */

  /** Both forms end the same way: the server says where this user belongs. */
  function goHome(res, msgNode, okKey, params) {
    var home = (res.data && res.data.home) || "/menu.html";
    if (msgNode) {
      msgNode.dataset.state = "ok";
      msgNode.dataset.code = "";
      msgNode.textContent = fill(t(okKey), params || {});
    }
    // A short beat so the confirmation is readable before the page swaps.
    window.setTimeout(function () { window.location.replace(home); }, 500);
  }

  /**
   * Turn a server error into something the reader can act on. Auth codes
   * (`auth_bad_credentials`) have an i18n key of exactly the same name, so they
   * come through verbatim; anything else falls back to the generic mapping.
   */
  function fail(msgNode, res) {
    msgNode.dataset.state = "err";
    msgNode.dataset.code = "";
    var code = (res && res.data && res.data.code) || "";
    if (code && t(code) !== code) { msgNode.textContent = t(code); return; }
    if (code && t("err_" + code) !== "err_" + code) {
      msgNode.textContent = t("err_" + code);
      return;
    }
    msgNode.textContent = DC.errorText((res && res.data) || {});
  }

  function submitLogin(e) {
    e.preventDefault();
    var msg = $("loginMsg");
    var btn = $("loginBtn");
    var username = $("loginUser").value.trim();
    var password = $("loginPass").value;
    if (!username) { fail(msg, { data: { code: "auth_missing_username" } }); return; }
    if (!password) { fail(msg, { data: { code: "auth_missing_password" } }); return; }

    DC.busy(btn, true);
    msg.dataset.state = "";
    msg.textContent = "";
    DC.post("/api/auth/login", { username: username, password: password, lang: DC.lang })
      .then(function (res) {
        DC.busy(btn, false);
        if (!res.ok) { fail(msg, res); return; }
        $("loginPass").value = "";
        var role = (res.data.user && DC.roleLabel(res.data.user.role)) || "";
        goHome(res, msg, "authWelcome", { role: role });
      }).catch(function () {
        DC.busy(btn, false);
        fail(msg, { data: { code: "err_network" } });
      });
  }

  function submitRegister(e) {
    e.preventDefault();
    var msg = $("registerMsg");
    var btn = $("registerBtn");
    var username = $("regUser").value.trim();
    var name = $("regName").value.trim();
    var phone = $("regPhone").value.trim();
    var password = $("regPass").value;
    var again = $("regPass2").value;

    if (!username) { fail(msg, { data: { code: "auth_missing_username" } }); return; }
    if (!name) { fail(msg, { data: { code: "auth_missing_name" } }); return; }
    if (!phone) { fail(msg, { data: { code: "auth_missing_phone" } }); return; }
    if (!password) { fail(msg, { data: { code: "auth_missing_password" } }); return; }
    if (password !== again) { fail(msg, { data: { code: "auth_need_password" } }); return; }

    // The room is required: it becomes the address of every ticket this
    // account will ever file.
    var college = $("regCollege").value;
    var block = $("regBlock") ? ($("regBlock").value || "") : "";
    var dorm = $("regDorm").value.trim();
    if (!college) { fail(msg, { data: { code: "auth_missing_college" } }); return; }
    if ($("regBlock") && !$("regBlock").disabled && !block) {
      fail(msg, { data: { code: "auth_need_block" } }); return;
    }
    if (!dorm) { fail(msg, { data: { code: "auth_missing_dorm" } }); return; }

    DC.busy(btn, true);
    msg.dataset.state = "";
    msg.textContent = "";
    DC.post("/api/auth/register", {
      username: username, name: name, password: password,
      phone: phone, college: college, block: block, dorm: dorm, lang: DC.lang
    }).then(function (res) {
      DC.busy(btn, false);
      if (!res.ok) { fail(msg, res); return; }
      $("regPass").value = "";
      $("regPass2").value = "";
      goHome(res, msg, "authCreated", null);
    }).catch(function () {
      DC.busy(btn, false);
      fail(msg, { data: { code: "err_network" } });
    });
  }

  /* ====================================================================== */
  /*  events + boot                                                         */
  /* ====================================================================== */

  function wire() {
    $("loginForm").addEventListener("submit", submitLogin);
    $("registerForm").addEventListener("submit", submitRegister);
    $("regCollege").addEventListener("change", renderBlockOptions);
    $("loginTab").addEventListener("click", function () { setAuthView("login"); });
    $("registerTab").addEventListener("click", function () { setAuthView("register"); });
  }

  DC.afterLang(function () {
    renderDemo();
    renderCollegeOptions();
    renderBlockOptions();
  });

  wire();
  setAuthView("login");

  DC.start(function (user) {
    // Already signed in? There is nothing for you here — go to your own console
    // (the server would redirect there anyway; this avoids a visible flash).
    if (user) {
      window.location.replace(DC.homeFor(user.role));
      return null;
    }
    // The college list is public — the registration form needs it, and it holds
    // no personal data. The demo table needs it too, for the college names.
    return DC.loadConfig().then(function () {
      renderCollegeOptions();
      renderDemo();
    });
  });
})(window.DC);
