/* DormCare · 主菜单 / Main menu
 *
 * The one front door. Signing in lands every role here, and this page offers
 * only the sections that role may open — a single place to navigate from,
 * without weakening anything: each console behind a card is still gated
 * server-side, so typing its URL is still not enough.
 *
 * Cards link to a console AND an anchor inside it, so "my repair records"
 * opens the console already scrolled to the records rather than the top of a
 * long form. The back-to-menu control in each console's header is rendered by
 * common.js, so it is the same everywhere.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function () {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;

  function $(id) { return document.getElementById(id); }

  /* The whole menu, as data. `href` is a page that does ONE thing; the server
   * decides whether this person may actually be there, so the menu is a
   * directory, never a grant. */
  var SECTIONS = {
    student: [
      { icon: "📝", href: "/report.html",  key: "menuStudentReport",  sub: "menuStudentReportSub" },
      { icon: "📋", href: "/records.html", key: "menuStudentRecords", sub: "menuStudentRecordsSub" },
      { icon: "🔑", href: "/account.html", key: "menuStudentAccount", sub: "menuStudentAccountSub" }
    ],
    worker: [
      { icon: "⚡", href: "/plan.html",   key: "menuWorkerPlan",     sub: "menuWorkerPlanSub" },
      { icon: "📋", href: "/jobs.html",   key: "menuWorkerPool",     sub: "menuWorkerPoolSub" },
      { icon: "🔑", href: "/account.html", key: "menuWorkerAccount",  sub: "menuWorkerAccountSub" }
    ],
    admin: [
      { icon: "📊", href: "/overview.html", key: "menuAdminOverview", sub: "menuAdminOverviewSub" },
      { icon: "📋", href: "/tickets.html",  key: "menuAdminTickets",  sub: "menuAdminTicketsSub" },
      { icon: "👥", href: "/users.html",    key: "menuAdminUsers",    sub: "menuAdminUsersSub" },
      { icon: "🛠️", href: "/roster.html",  key: "menuAdminRoster",   sub: "menuAdminRosterSub" },
      { icon: "🔑", href: "/account.html",  key: "menuAdminAccount",  sub: "menuAdminAccountSub" }
    ]
  };

  /* The icon on the top-left tile follows the role, so the menu looks like the
   * console it belongs to rather than a neutral lobby. */
  var MARKS = { student: "🎓", worker: "🛠️", admin: "🧭" };
  var MARK_CLASS = { student: "", worker: " work", admin: " admin" };

  function renderMark(role) {
    var box = $("menuMark");
    if (!box) return;
    box.textContent = MARKS[role] || "🏠";
    // The class is what paints the tile, and it must be rewritten rather than
    // accumulated — otherwise switching language on a page reload would stack
    // " work admin admin".
    box.className = "mark" + (MARK_CLASS[role] || "");
  }

  function renderGreeting() {
    var me = DC.me || {};
    var role = me.role || "";
    var label = $("menuRole");
    if (label) {
      label.textContent = DC.roleLabel(role);
      label.className = "here " + esc(role);
    }
    var hello = $("menuHello");
    if (hello) hello.textContent = fill(t("menuHello"), { name: DC.displayName(me) });
  }

  function renderCards() {
    var grid = $("menuGrid");
    if (!grid) return;
    var items = SECTIONS[DC.me && DC.me.role] || [];
    grid.innerHTML = items.map(function (it) {
      return '<a class="menu-card" href="' + esc(it.href) + '">' +
        '<span class="menu-icon" aria-hidden="true">' + it.icon + "</span>" +
        '<span class="menu-body">' +
        '<b class="menu-name">' + esc(t(it.key)) + "</b>" +
        '<span class="menu-sub">' + esc(t(it.sub)) + "</span>" +
        "</span>" +
        '<span class="menu-go" aria-hidden="true">→</span>' +
        "</a>";
    }).join("");
  }

  function render() {
    renderMark(DC.me && DC.me.role);
    renderGreeting();
    renderCards();
  }

  DC.afterLang(render);

  // No role argument: the menu belongs to every role, which is exactly why it
  // cannot assume one — it only requires that somebody is signed in. The server
  // already refuses this page to an anonymous visitor; this is the same
  // decision echoed in the browser, so an expired session lands on the sign-in
  // page instead of an empty menu.
  DC.start(function (user) {
    if (!user) { window.location.replace("/login.html"); return; }
    render();
  });
})();
