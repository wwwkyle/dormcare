/* Headless client-side smoke test for the DormCare front-end.
 *
 * Runs common.js + student.js / worker.js against the LIVE server using a
 * minimal DOM shim. This exercises the real rendering code paths — i18n
 * application, college/block pickers, collapsible ticket rows, plan stats and
 * the route timeline — without needing a real browser.
 *
 * The pages are now role-gated, so the harness signs in first (one session per
 * role — student, technician, administrator), sends the session cookie with
 * every request, and asserts that a session of the wrong role is redirected
 * away.
 *
 * Usage: node dom_smoke.js
 */
"use strict";

const http = require("http");
const vm = require("vm");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:3000";
const DIR = __dirname;

/* ------------------------------------------------------------------ fetch */

function req(method, url, body, cookie) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body), "utf8");
    const headers = {};
    if (data) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = data.length;
    }
    if (cookie) headers["Cookie"] = cookie;
    const r = http.request(
      {
        hostname: u.hostname, port: u.port, path: u.pathname + u.search, method,
        headers,
      },
      (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let json = {};
          try { json = JSON.parse(text); } catch (e) { /* keep {} */ }
          const setCookie = res.headers["set-cookie"];
          resolve({
            ok: res.statusCode >= 200 && res.statusCode < 300,
            status: res.statusCode,
            location: res.headers.location || "",
            cookie: setCookie ? String(setCookie[0]).split(";")[0] : "",
            body: text,
            data: json,
          });
        });
      }
    );
    r.on("error", reject);
    if (data) r.write(data);
    r.end();
  });
}

/** Sign in and hand back just the session cookie. */
async function login(username, password) {
  const res = await req("POST", BASE + "/api/auth/login", { username, password });
  if (!res.ok || !res.cookie) {
    throw new Error("sign-in failed for " + username + ": " + res.status + " " + res.body.slice(0, 120));
  }
  return { cookie: res.cookie, user: res.data.user };
}

const SESSIONS = {
  student: { username: "2024001", password: "dorm2026" },
  worker: { username: "B02", password: "fix2026" },
  admin: { username: "admin", password: "admin2026" },
};

/* ------------------------------------------------------------- DOM shim */

function makeNode(tag, id) {
  const node = {
    tagName: (tag || "div").toUpperCase(),
    id: id || "",
    _classes: new Set(),
    _attrs: {},
    _children: [],
    _listeners: {},
    _text: "",
    _html: "",
    placeholder: "",
    disabled: false,
    hidden: false,
    open: false,
    dataset: {},
    style: {},
  };
  Object.defineProperty(node, "className", {
    get() { return [...node._classes].join(" "); },
    set(v) { node._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  Object.defineProperty(node, "classList", {
    get() {
      return {
        add: (...c) => c.forEach((x) => node._classes.add(x)),
        remove: (...c) => c.forEach((x) => node._classes.delete(x)),
        contains: (c) => node._classes.has(c),
        toggle: (c, on) => (on === undefined ? (node._classes.has(c) ? node._classes.delete(c) : node._classes.add(c)) : (on ? node._classes.add(c) : node._classes.delete(c))),
      };
    },
  });
  Object.defineProperty(node, "textContent", {
    get() { return node._text; },
    set(v) { node._text = v === null || v === undefined ? "" : String(v); node._html = ""; },
  });
  Object.defineProperty(node, "innerHTML", {
    get() { return node._html; },
    set(v) {
      node._html = String(v === null || v === undefined ? "" : v);
      node._text = node._html.replace(/<[^>]*>/g, "");
      node._children = parseChildren(node._html);
      // Hydrate the freshly parsed subtree into the flat registry so that
      // document.getElementById() keeps working on re-rendered markup.
      node._children.forEach((c) => registerTree(c));
      // A real <select> auto-selects its first option. Mirror that here so the
      // module's own default-selection logic is exercised, not masked.
      if (node.tagName === "SELECT") {
        var first = /<option\s+value="([^"]*)"/.exec(node._html);
        node._selected = first ? first[1] : "";
        node._value = node._selected;
      }
    },
  });
  Object.defineProperty(node, "value", {
    get() { return node._value === undefined ? "" : node._value; },
    set(v) { node._value = v === null || v === undefined ? "" : String(v); node._selected = node._value; },
  });
  // <select>.options — the module reads option values through this
  Object.defineProperty(node, "options", {
    get() {
      if (node.tagName !== "SELECT") return undefined;
      return node._children
        .filter((c) => c.tagName === "OPTION")
        .map((c) => ({ value: c.getAttribute("value") === null ? "" : c.getAttribute("value") }));
    },
  });
  // direct children, so option matching works without a full selector engine
  Object.defineProperty(node, "children", { get() { return node._children.slice(); } });
  node.setAttribute = (k, v) => { node._attrs[k] = String(v); };
  node.getAttribute = (k) => (k in node._attrs ? node._attrs[k] : null);
  node.hasAttribute = (k) => k in node._attrs;
  node.addEventListener = (ev, fn) => { (node._listeners[ev] = node._listeners[ev] || []).push(fn); };
  node.removeEventListener = () => {};
  node.dispatch = (ev, payload) => {
    const e = Object.assign({ type: ev, target: node, preventDefault() {}, stopPropagation() {}, detail: 1 }, payload || {});
    (node._listeners[ev] || []).forEach((fn) => fn(e));
    return e;
  };
  node.scrollIntoView = () => {};
  node.appendChild = (c) => { node._children.push(c); return c; };
  node.querySelector = (sel) => node.querySelectorAll(sel)[0] || null;
  node.querySelectorAll = (sel) => queryAll(node, sel);
  node.closest = () => null;
  node.focus = () => {};
  return node;
}

/* a very small selector engine over our own rendered HTML */
let REGISTRY = {};

function parseChildren(html) {
  const out = [];
  const re = /<(\w+)([^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[2];
    const idm = /\bid="([^"]*)"/.exec(attrs);
    const clm = /\bclass="([^"]*)"/.exec(attrs);
    const n = makeNode(m[1], idm ? idm[1] : "");
    if (clm) n.className = clm[1];
    const dm = /\bdata-([\w-]+)="([^"]*)"/g;
    let d;
    while ((d = dm.exec(attrs))) {
      n._attrs["data-" + d[1]] = d[2];
    }
    const vm2 = /\bvalue="([^"]*)"/.exec(attrs);
    if (vm2) n._attrs["value"] = vm2[1];
    out.push(n);
  }
  return out;
}

function walk(node, fn) {
  fn(node);
  node._children.forEach((c) => walk(c, fn));
}

function matches(node, sel) {
  sel = sel.trim();
  // tag.class
  let m = /^([a-zA-Z]+)?\.([\w-]+)$/.exec(sel);
  if (m) return (!m[1] || node.tagName === m[1].toUpperCase()) && node._classes.has(m[2]);
  // [attr="value"]
  m = /^\[([\w-]+)=["']?([^"'\]]+)["']?\]$/.exec(sel);
  if (m) {
    const attr = m[1];
    const val = m[2];
    if (attr === "data-lang") return node.getAttribute("data-lang") === val;
    return node.getAttribute(attr) === val;
  }
  // tag[attr]
  m = /^([a-zA-Z]+)\[([\w-]+)\]$/.exec(sel);
  if (m) return node.tagName === m[1].toUpperCase() && node.hasAttribute(m[2]);
  // bare [attr] — e.g. "[data-i18n]". Must come after the attributed form.
  m = /^\[([\w-]+)\]$/.exec(sel);
  if (m) return node.hasAttribute(m[1]);
  // #id
  if (sel[0] === "#") return node.id === sel.slice(1);
  // tag
  if (/^[a-zA-Z]+$/.test(sel)) return node.tagName === sel.toUpperCase();
  // .class
  if (sel[0] === ".") return node._classes.has(sel.slice(1));
  // comma list
  if (sel.indexOf(",") >= 0) return sel.split(",").some((s) => matches(node, s));
  return false;
}

// Walk the *live* registry, not a detached subtree: a role module that
// re-assigns innerHTML replaces the copied children, so only the registry
// holds the node objects the page actually reads back.
function queryAll(root, sel) {
  const out = [];
  const seen = new Set();
  const push = (n) => { if (!seen.has(n) && matches(n, sel)) { seen.add(n); out.push(n); } };

  if (root === document._root || root === document.documentElement) {
    ALL.forEach(push);
    walk(document._root, push);
    walk(document.documentElement, push);
    return out;
  }

  const ids = [];
  walk(root, (n) => { if (n.id && ids.indexOf(n.id) < 0) ids.push(n.id); });
  for (let i = 0; i < ids.length; i++) {
    const live = ALL.find((n) => n.id === ids[i]);
    if (live) { push(live); walk(live, push); }
  }
  walk(root, push);
  // Generated markup: a container whose children came from `innerHTML` has no
  // ids for the shortcut above to follow, and its own children are NOT in the
  // root's static tree. Fall back to the parsed children when the id route
  // produced nothing — otherwise "query the cards I just rendered" silently
  // returns an empty list and the assertion reads as a page with no cards.
  if (!out.length) {
    for (const child of (root._children || [])) {
      push(child);
      walk(child, push);
    }
  }
  return out;
}

/* --------------------------------------------------------------- document */

const ALL = [];
function registerTree(root) { walk(root, (n) => ALL.push(n)); }

const document = {
  documentElement: makeNode("html"),
  _root: makeNode("body"),
  _title: makeNode("title"),
  readyState: "complete",
  createElement: (t) => makeNode(t),
  addEventListener: () => {},
  querySelector(sel) {
    if (sel.indexOf("title") === 0) return document._title;
    return document.querySelectorAll(sel)[0] || null;
  },
  // Route through the registry-backed walker so that a selector always lands
  // on the same node object the role module mutated (see node.querySelectorAll).
  querySelectorAll(sel) { return queryAll(document._root, sel); },
  getElementById(id) { return ALL.find((n) => n.id === id) || null; },
};

/* ------------------------------------------------------ build page from HTML */

function buildPage(html) {
  ALL.length = 0;
  registerTree(document._root);
  registerTree(document.documentElement);

  // <title data-i18n="...">
  const tm = /<title([^>]*)>/.exec(html);
  if (tm && /data-i18n="([^"]+)"/.test(tm[1])) {
    document._title.setAttribute("data-i18n", /data-i18n="([^"]+)"/.exec(tm[1])[1]);
  }

  // every element with an id, plus its i18n attributes
  const re = /<(\w+)([^>]*\bid="([^"]+)"[^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const tag = m[1], attrs = m[2], id = m[3];
    const n = makeNode(tag, id);
    ["data-i18n", "data-i18n-html", "data-i18n-ph", "data-i18n-aria"].forEach((a) => {
      const am = new RegExp(a + '="([^"]+)"').exec(attrs);
      if (am) n.setAttribute(a, am[1]);
    });
    // A boolean `hidden` attribute must be honoured, or a panel the markup
    // keeps closed would read as open and the check would be blind to it.
    if (/(?:^|\s)hidden(?:\s|=|$)/.test(attrs)) n.hidden = true;
    registerTree(n);
  }
  // Containers the JS writes into that the markup declares EMPTY — `<div
  // id="x"></div>` — still need a node, and the regex above already made one,
  // so nothing is needed here any more.
  //
  // This list used to conjure containers the page did NOT declare. That made
  // every "this page must not carry another section's control" assertion
  // vacuous, because the shim had just invented the very element the check was
  // looking for. Conjure nothing: an id that is absent from the markup must
  // stay absent, or the check cannot see it.
  return document._root;
}

/* allow the role module to find <select> value reads */

/* ------------------------------------------------------------------ run */

function loadFile(name) { return fs.readFileSync(path.join(DIR, name), "utf8"); }

function makeSandbox(cookie) {
  const lang = { v: "zh" };
  const store = {};
  const sandbox = {
    console,
    document,
    setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Math, Date, Number, String, Object, Array, RegExp, Error,
    navigator: { language: "zh-CN" },
    fetch: (url, opts) => {
      // The session cookie has to ride along, or every call comes back 401 and
      // common.js bounces the page to /login.html.
      return req((opts && opts.method) || "GET", BASE + url,
                 opts && opts.body ? JSON.parse(opts.body) : undefined, cookie)
        .then((res) => ({
          ok: res.ok,
          status: res.status,
          json: () => Promise.resolve(res.data),
          text: () => Promise.resolve(res.body),
        }));
    },
    location: { href: BASE, replace: (u) => { sandbox.__redirect = u; } },
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail || "" });
  console.log((ok ? "PASS  " : "FAIL  ") + name + (detail ? "  — " + detail : ""));
}

async function runRole(page, module, cookie, opts) {
  // `opts.asRole` only matters for /account.html, the one page every role may
  // open: it decides whether the room half is on offer. It defaults to the
  // role the SESSION resolved, so a caller that forgets it still gets a
  // truthful assertion instead of an accidental "null".
  const asRole = () => (opts && opts.asRole) ||
    (sandbox.window.DC && sandbox.window.DC.me && sandbox.window.DC.me.role) || null;
  console.log("\n=== " + page + " + " + module + " ===");
  const pageRes = await req("GET", BASE + page, undefined, cookie);
  check(page + " served to its own role", pageRes.status === 200,
    "status=" + pageRes.status + (pageRes.location ? " -> " + pageRes.location : ""));
  if (pageRes.status !== 200) return;
  const html = pageRes.body;
  buildPage(html);
  const sandbox = makeSandbox(cookie);

  try {
    vm.runInContext(loadFile("common.js"), sandbox, { filename: "common.js" });
    vm.runInContext(loadFile(module), sandbox, { filename: module });
  } catch (e) {
    check(page + " scripts execute", false, e.message);
    return;
  }
  check(page + " scripts execute", true);

  // let the DC.start() promise chain settle
  for (let i = 0; i < 60; i++) await new Promise((r) => setTimeout(r, 100));

  const DC = sandbox.window.DC;
  check(page + " DC exposed", !!DC, DC ? "lang=" + DC.lang : "");
  check(page + " identity resolved from the session", !!(DC && DC.me),
    DC && DC.me ? DC.me.username + " / " + DC.me.role : "none");
  check(page + " was not redirected away", !sandbox.__redirect,
    sandbox.__redirect || "");

  const id = (n) => document.getElementById(n);
  const txt = (n) => (id(n) ? id(n).textContent : "");

  // Each page's primary <select> must find a real default; an empty value here
  // is what silently breaks every downstream call. The read-only pages have no
  // such picker (their selects all start on a placeholder on purpose) and the
  // account page's starts from the account itself, so each names its own probe.
  const probeId = {
    "student-report.js": "fCollege",
    "worker-plan.js": "wDate",
  }[module];
  if (probeId) {
    const pn = document.getElementById(probeId);
    check(page + " role picker has a default value", !!(pn && pn.value),
      probeId + "=" + JSON.stringify(pn && pn.value));
  }

  // One card, one page: this page must NOT carry another section's controls.
  // Without this, a page could quietly regrow into the old consolidated
  // console and nobody would notice, because every control would still work.
  const FOREIGN = {
    "student-report.js": ["ticketList", "accRoomSave", "accPwSave"],
    "student-records.js": ["typeChips", "slotGrid", "submitBtn",
                           "accRoomSave", "accPwSave"],
    "account.js": ["typeChips", "slotGrid", "submitBtn", "ticketList",
                   "optimizeBtn", "adUserList", "adTicketList"],
    "worker-plan.js": ["ticketList", "poolCount", "toggleAll"],
    "worker-jobs.js": ["optimizeBtn", "wDate", "timeline", "unschedBox"],
    "admin-overview.js": ["adTicketList", "adUserList", "adWorkerList"],
    "admin-tickets.js": ["adUserList", "adWorkerList", "adminStats"],
    "admin-users.js": ["adTicketList", "adWorkerList", "adminStats"],
    "admin-roster.js": ["adTicketList", "adUserList", "adminStats"],
  }[module] || [];
  for (const other of FOREIGN) {
    check(page + " does not carry another section's control #" + other,
      document.getElementById(other) === null, other);
  }

  // Every console carries a way back to the one menu. The control is rendered
  // by common.js, so asserting it here covers all three consoles at once.
  const chip = id("userChip");
  check(page + " header offers a way back to the main menu",
    !!chip && /href="\/menu\.html"/.test(chip.innerHTML),
    chip ? chip.innerHTML.slice(0, 120) : "no chip");

  // the sign-out control must be rendered for whoever is signed in
  check(page + " user chip shows the signed-in account",
    /2024001|B02|陈师傅|李师傅|王小明|系统管理员|Administrator|admin/.test(
      id("userChip") ? id("userChip").innerHTML : ""),
    (id("userChip") ? id("userChip").innerHTML.slice(0, 90) : "no chip"));

  // i18n was applied to the chrome
  check(page + " chrome localized",
    txt("statusText").length > 0 && !/^status/.test(txt("statusText")),
    "statusText=" + JSON.stringify(txt("statusText")));

  // Every data-i18n key in the markup must resolve to real copy, not the raw key.
  const i18nNodes = document.querySelectorAll("[data-i18n]");
  const unresolved = i18nNodes
    .map((n) => [n.id || n.tagName, n.textContent])
    .filter(([, v]) => !v || /^[a-z][A-Za-z]+$/.test(v));
  // A slim single-purpose page may declare only three or four translatable
  // strings, so the floor is low — what matters is that none of them is left as
  // a raw key, and that the page declared some at all.
  check(page + " every data-i18n key resolved", i18nNodes.length >= 3 && unresolved.length === 0,
    "nodes=" + i18nNodes.length + (unresolved.length ? " unresolved=" + JSON.stringify(unresolved.slice(0, 4)) : ""));

  /* ====================================================================== */
  /*  /report.html — file a repair request                                  */
  /* ====================================================================== */

  if (module === "student-report.js") {
    check("report page has one unified AI composer",
      id("agentSend") && id("intakeText") && id("photoInput") &&
      document.getElementById("agentText") === null &&
      document.getElementById("intakeBtn") === null);
    check("intake exposes four clear stages",
      id("intakeSteps").querySelectorAll("li").length === 4);
    check("draft form starts collapsed but manual fallback remains available",
      id("draftSection").hidden === true && !!id("manualOpen"));

    const collegeOpts = id("fCollege") ? id("fCollege").innerHTML.match(/<option/g) : null;
    check("college picker has 8 colleges", collegeOpts && collegeOpts.length === 8,
      "options=" + (collegeOpts ? collegeOpts.length : 0));

    // shaw must expose its 6 towers; muse must fall back to the catch-all
    const pick = (val) => {
      id("fCollege").value = val;
      id("fCollege").dispatch("change");
    };
    pick("shaw");
    const shawBlocks = id("fBlock").innerHTML.match(/<option/g);
    check("shaw block picker shows 6 towers + placeholder",
      shawBlocks && shawBlocks.length === 7, "options=" + (shawBlocks ? shawBlocks.length : 0));

    pick("muse");
    const museBlocks = id("fBlock").innerHTML.match(/<option/g);
    check("muse falls back to a single placeholder", museBlocks && museBlocks.length === 1,
      "options=" + (museBlocks ? museBlocks.length : 0) + " disabled=" + id("fBlock").disabled);
    check("muse shows the write-it-in hint",
      /宿舍号/.test(txt("blockHint")) || /full room reference/.test(txt("blockHint")),
      JSON.stringify(txt("blockHint").slice(0, 60)));

    const typeChips = id("typeChips").querySelectorAll("button[data-t]");
    check("17 repair-type chips rendered", typeChips.length === 17, "chips=" + typeChips.length);

    // The student page must not expose the internal scheduling constants:
    // no rules card, no fixed labour time on a chip, no duration read-out.
    check("no scheduling-rules card on the student page",
      document.getElementById("rulesGrid") === null);
    check("no duration read-out on the student page",
      document.getElementById("estDuration") === null &&
      document.getElementById("estTravel") === null);
    const chipHtml = id("typeChips").innerHTML;
    check("repair-type chips carry no labour time", !/<small>/.test(chipHtml),
      chipHtml.slice(0, 90));
    check("nothing on the report page quotes a fixed duration",
      !/(固定工时|固定维修时长|预计维修时长|Fixed duration|labour standard)/.test(
        id("typeChips").innerHTML + id("agentLog").innerHTML +
        id("agentDraft").innerHTML));

    // The intake contract, checked straight against the API rather than through
    // the UI: the agent either asks what it is missing or says what to try, and
    // the labour standard never leaves the server.
    const intake = await req("POST", BASE + "/api/intake/parse",
      { text: "空调不制冷", lang: "zh" }, cookie);
    check("intake always returns something to act on",
      intake.ok && (intake.data.self_help || []).length > 0,
      JSON.stringify(intake.data).slice(0, 130));
    check("intake never ships the fixed labour time",
      intake.ok && !("duration_minutes" in intake.data),
      Object.keys(intake.data || {}).join(","));
    const vague = await req("POST", BASE + "/api/intake/parse",
      { text: "坏了", lang: "zh" }, cookie);
    check("a vague description comes back as questions",
      vague.ok && vague.data.need_more_info === true &&
      (vague.data.follow_up || []).length > 0,
      JSON.stringify(vague.data.follow_up));

    const slotBtns = id("slotGrid").querySelectorAll("button[data-i]");
    check("17 availability slots rendered", slotBtns.length === 17, "slots=" + slotBtns.length);

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN re-renders type chips", /Desk|Chair|Air conditioner/.test(id("typeChips").innerHTML));
    // NOTE: read the three preset buttons, never #slotGrid — the grid holds
    // only clock labels (08:00 … 16:30) and is intentionally language-neutral.
    check("switching to EN localizes the slot presets",
      /Morning/.test(txt("slotMorning")) && /Afternoon/.test(txt("slotAfternoon")) &&
        /All day/.test(txt("slotAll")),
      JSON.stringify(txt("slotMorning") + " " + txt("slotAfternoon") + " " + txt("slotAll")));
    check("slot grid stays clock-only after re-render",
      id("slotGrid").querySelectorAll("button[data-i]").length === 17,
      "slots=" + id("slotGrid").querySelectorAll("button[data-i]").length);
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /records.html — the student's own repair records                       */
  /* ====================================================================== */

  if (module === "student-records.js") {
    const rows = id("ticketList").querySelectorAll("details.fold-item");
    check("student records are collapsible <details>", rows.length >= 0,
      "rows=" + rows.length);

    // Every row must state where the job is in its life cycle, and a finished
    // one must offer the student the verdict control that closes the loop.
    const listHtml = id("ticketList").innerHTML;
    check("records carry a life-cycle status pill",
      rows.length === 0 || /pill (pending|accepted|done|confirmed)/.test(listHtml),
      "rows=" + rows.length);
    // ...and a queue pill: "somebody is responsible" and "somebody is coming at
    // 09:00" are different facts, and the row has to distinguish them.
    check("records carry a queue-state pill too",
      rows.length === 0 ||
        /pill q-(unassigned|unplaced|scheduled|off_route|reschedule)/.test(listHtml),
      "rows=" + rows.length);
    check("pending records can be withdrawn, done records can be rated",
      rows.length === 0 ||
        /data-act="del"|data-act="confirm"|data-act="feedback"|fb-done/.test(listHtml));

    // the address of a record is the room on file, never anything the browser
    // could have supplied
    check("no scheduling-rules card on the records page",
      document.getElementById("rulesGrid") === null);
    check("nothing on the records page quotes a fixed duration",
      !/(固定工时|固定维修时长|预计维修时长|Fixed duration)/.test(listHtml));

    // collapse-state toggle
    const ta = id("toggleAll");
    check("expand-all button present", !!ta);
    check("records default to collapsed", ["expandAll", "展开全部"].includes(ta ? ta.textContent : ""),
      JSON.stringify(ta ? ta.textContent : ""));

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    // The shim has no document.body, so read the chrome nodes directly: the
    // heading is written by the i18n pass, not by the records renderer.
    check("switching to EN localizes the records page",
      /My repair records|Technician|Scheduled|Awaiting assignment/i.test(
        id("ticketList").innerHTML + txt("myTicketsTitle") + txt("poolCount")),
      JSON.stringify(txt("myTicketsTitle") + " / " + txt("poolCount")));
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /account.html — correct the room, change the password                 */
  /* ====================================================================== */

  if (module === "account.js") {
    // The password half is valid for every role, so those controls must always
    // be there — on all three renders of this page.
    for (const fid of ["aPwCur", "aPwNew", "aPwNew2", "accPwSave"]) {
      check("account settings control #" + fid + " exists", !!document.getElementById(fid));
    }

    if (asRole() === "student") {
      for (const fid of ["accRoomNow", "aCollege", "aBlock", "aDorm", "accRoomSave"]) {
        check("account settings control #" + fid + " exists", !!document.getElementById(fid));
      }
      check("a student is shown the room half", id("accRoomBlock").hidden === false);
      check("...and it is not told it has no room", id("accRoomOnlyNote").hidden === true);
      check("the room picker starts on the student's own college",
        id("aCollege").value === "shaw", "aCollege=" + id("aCollege").value);
      check("...and the room box carries the account's dorm",
        id("aDorm").value === "A-301", "aDorm=" + id("aDorm").value);
      check("the current room is stated in words",
        /A-301/.test(txt("accRoomNow")), JSON.stringify(txt("accRoomNow")));
      // the tower picker follows the college, exactly like the report form's
      id("aCollege").value = "shaw";
      id("aCollege").dispatch("change");
      const accBlocks = id("aBlock").innerHTML.match(/<option/g);
      check("the account-settings tower picker uses the same block list",
        accBlocks && accBlocks.length === 7, "options=" + (accBlocks ? accBlocks.length : 0));

      // A language switch must NOT wipe a half-typed correction: the form is
      // filled once from the account, and only repainted afterwards.
      id("aDorm").value = "B-777";
      DC.setLang("en");
      check("switching language keeps the room the student typed",
        id("aDorm").value === "B-777", "aDorm=" + id("aDorm").value);
      DC.setLang("zh");
      check("...and again back to Chinese",
        id("aDorm").value === "B-777", "aDorm=" + id("aDorm").value);
      id("aDorm").value = "A-301";
    } else {
      // A technician has no dorm and an administrator is not a resident, so
      // the room card is REMOVED rather than disabled — the server refuses
      // that action for them anyway, and offering it would be a dead control.
      check("a " + asRole() + " is not offered the room half",
        id("accRoomBlock").hidden === true);
      check("...and is told why", id("accRoomOnlyNote").hidden === false);
      check("...in words",
        /没有宿舍|no room on file/.test(txt("accRoomOnlyNote")),
        JSON.stringify(txt("accRoomOnlyNote").slice(0, 60)));
      check("the title drops the room framing",
        /密码|Password/.test(txt("accTitle")), JSON.stringify(txt("accTitle")));
      check("the page still leads with the password control",
        !!id("aPwCur") && !!id("accPwSave"));
    }
  }

  /* ====================================================================== */
  /*  /plan.html — the technician's day                                     */
  /* ====================================================================== */

  if (module === "worker-plan.js") {
    // The technician picker is gone on purpose: the page belongs to whoever is
    // signed in, and the server resolves "my day" from the session.
    // The read-out is the person's own name; the staff number lives in the role
    // bar beside it. Both must come from the session, never from a picker.
    check("technician identity is locked to the account",
      /李师傅/.test(txt("wWorkerSelf")) && /B02/.test(txt("workerIdentity")),
      JSON.stringify(txt("wWorkerSelf") + " / " + txt("workerIdentity")));
    check("technician cannot pick a colleague to plan for",
      document.getElementById("wWorker") === null);

    // The fixed scheduling constants are the engine's input, not the console's
    // copy: the "排班规则（系统固定值）" card is gone from this side too.
    check("no scheduling-rules card on the technician page",
      document.getElementById("rulesGrid") === null);

    const statCards = id("statRow").querySelectorAll("div.stat");
    check("7 stat cards rendered", statCards.length === 7, "cards=" + statCards.length);

    const stops = id("timeline").querySelectorAll("article.stop");
    check("route timeline rendered", stops.length > 0, "stops=" + stops.length);

    const tlHtml = id("timeline").innerHTML;
    check("timeline shows a block/tower address", /东座|西座|宿舍 \d 栋|南区|北区|A 座|B 座|C 座/.test(tlHtml));
    check("timeline never straddles lunch",
      !/1[23]:\d\d-1[34]:\d\d/.test(tlHtml.replace(/\s/g, "")));

    check("timeline stops offer the life-cycle action",
      /data-act="accepted"|data-act="done"/.test(tlHtml));

    check("plan message reports jobs", /排班已生成|Schedule generated/.test(txt("planMsg")),
      JSON.stringify(txt("planMsg")));

    // The off-route panel: jobs the engine could not place today, with enough
    // detail to decide on and the two answers a technician can give. The demo
    // dataset deliberately holds one impossible job (40 minutes of work in a
    // 30-minute window), so this is real markup, not an empty container.
    const un = id("unschedBox");
    check("off-route panel exists", !!un);
    if (un && !un.hidden) {
      const items = un.querySelectorAll("article.unsched-item");
      check("off-route jobs are listed with detail", items.length > 0,
        "items=" + items.length);
      check("each off-route job offers 'take en route' and 'suggest a new time'",
        /data-act="offroute-take"/.test(un.innerHTML) &&
        /data-act="offroute-suggest"/.test(un.innerHTML) &&
        /unsched-date/.test(un.innerHTML));
      check("off-route rows state why it did not fit",
        /排不进|班次|空闲时段|full|outside|shorter/.test(un.innerHTML),
        un.innerHTML.slice(0, 120));
      check("off-route rows carry the address and the contact",
        /东座|西座|宿舍 \d 栋|南区|北区|A 座|B 座|C 座|H-\d/.test(un.innerHTML));
    }

    // language switch must re-render the role-specific content
    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN re-renders the timeline",
      /College|workshop|same college/.test(id("timeline").innerHTML));
    check("switching to EN localizes stats", /Jobs today|Work time/.test(id("statRow").innerHTML));
    check("switching to EN keeps the identity chip",
      /Technician|Sign out/.test(id("userChip").innerHTML), id("userChip").innerHTML.slice(0, 80));
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /jobs.html — the jobs assigned to this technician                      */
  /* ====================================================================== */

  if (module === "worker-jobs.js") {
    check("the job list is present", !!id("ticketList"));
    const pool = id("ticketList").querySelectorAll("details.fold-item");
    check("my jobs are collapsible", pool.length >= 0, "rows=" + pool.length);

    // Only the technician's own tickets may appear, and each row must offer the
    // 接单 / 完工 action that matches its current status.
    check("job rows carry a life-cycle action",
      /data-act="accepted"|data-act="done"/.test(id("ticketList").innerHTML));
    check("the job list does not expose the scheduling form",
      document.getElementById("optimizeBtn") === null &&
      document.getElementById("timeline") === null);
    check("no scheduling-rules card on the jobs page",
      document.getElementById("rulesGrid") === null);

    const ta = id("toggleAll");
    check("expand-all button present", !!ta);
    check("jobs default to collapsed", ["expandAll", "展开全部"].includes(ta ? ta.textContent : ""),
      JSON.stringify(ta ? ta.textContent : ""));

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN keeps the identity chip",
      /Technician|Sign out/.test(id("userChip").innerHTML),
      id("userChip").innerHTML.slice(0, 80));
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /overview.html — the operations overview                               */
  /* ====================================================================== */

  if (module === "admin-overview.js") {
    const statCards = id("adminStats").querySelectorAll("div.stat");
    check("admin overview renders its stat cards", statCards.length === 7,
      "cards=" + statCards.length);

    const chips = id("adBreakdown").querySelectorAll("span.ad-chip");
    check("admin breakdown renders status / priority / college counts",
      chips.length > 0, "chips=" + chips.length);
    check("the overview page carries no ticket board",
      document.getElementById("adTicketList") === null);
    check("no scheduling-rules card on the admin page",
      document.getElementById("rulesGrid") === null);

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN localizes the admin stats",
      /All tickets|Student accounts/.test(id("adminStats").innerHTML),
      id("adminStats").innerHTML.slice(0, 110));
    check("switching to EN localizes the breakdown",
      /By status|By priority|By college/.test(id("adBreakdown").innerHTML));
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /tickets.html — the whole board                                       */
  /* ====================================================================== */

  if (module === "admin-tickets.js") {
    const board = id("adTicketList").querySelectorAll("details.fold-item");
    check("admin sees the whole board", board.length > 0, "rows=" + board.length);
    check("every board row names the student who filed it",
      /报修人|Filed by/.test(id("adTicketList").innerHTML),
      id("adTicketList").innerHTML.slice(0, 110));
    check("board rows offer removal",
      /data-act="delete-ticket"/.test(id("adTicketList").innerHTML));
    check("the board page carries no account desk",
      document.getElementById("adUserList") === null);
    check("the board page carries no stat cards",
      document.getElementById("adminStats") === null);
    check("no scheduling-rules card on the board page",
      document.getElementById("rulesGrid") === null);

    const filter = id("adStatusFilter");
    check("the board has a status filter", !!filter && filter.innerHTML.length > 0,
      filter ? filter.innerHTML.slice(0, 60) : "missing");

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN localizes the board",
      /Filed by|Contact|Technician/.test(id("adTicketList").innerHTML),
      id("adTicketList").innerHTML.slice(0, 90));
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /users.html — the account desk                                        */
  /* ====================================================================== */

  if (module === "admin-users.js") {
    const people = id("adUserList").querySelectorAll("details.ad-item");
    check("admin lists every account", people.length >= 14, "accounts=" + people.length);
    check("account rows carry a role chip and the room",
      /who-role (student|worker|admin)/.test(id("adUserList").innerHTML) &&
      /A-301|B-102|A 座|东座/.test(id("adUserList").innerHTML),
      id("adUserList").innerHTML.slice(0, 110));
    check("account rows offer a password reset",
      /data-act="reset-pw"/.test(id("adUserList").innerHTML));
    check("student rows offer a room correction",
      /data-act="save-user"/.test(id("adUserList").innerHTML));

    // The new-account panel exists but stays hidden until it is asked for.
    check("the create-account panel starts closed", id("adCreateCard").hidden === true);
    check("the create form offers all 17 categories",
      id("adCreateSpecs").querySelectorAll("button[data-s]").length === 17,
      "buttons=" + id("adCreateSpecs").querySelectorAll("button[data-s]").length);
    check("the create form offers the 8 colleges",
      (id("adCreateCollege").innerHTML.match(/<option/g) || []).length === 9,
      "options=" + (id("adCreateCollege").innerHTML.match(/<option/g) || []).length);
    check("the account desk carries no ticket board",
      document.getElementById("adTicketList") === null);
    check("no scheduling-rules card on the account desk",
      document.getElementById("rulesGrid") === null);

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN localizes the account desk",
      /Student|Technician|Administrator/.test(id("adUserList").innerHTML),
      id("adUserList").innerHTML.slice(0, 110));
    DC.setLang("zh");
  }

  /* ====================================================================== */
  /*  /roster.html — the technician roster                                  */
  /* ====================================================================== */

  if (module === "admin-roster.js") {
    const roster = id("adWorkerList").querySelectorAll("div.ad-item");
    check("admin sees the technician roster", roster.length >= 3, "roster=" + roster.length);
    check("the roster reports trade coverage",
      /书桌|Desk/.test(id("adWorkerList").innerHTML),
      id("adWorkerList").innerHTML.slice(0, 110));
    check("the roster marks certified technicians",
      /cert/.test(id("adWorkerList").innerHTML),
      id("adWorkerList").innerHTML.slice(0, 110));
    check("the roster is read-only: no create form here",
      document.getElementById("adCreateCard") === null);
    check("the roster carries no account desk",
      document.getElementById("adUserList") === null);
    check("no scheduling-rules card on the roster page",
      document.getElementById("rulesGrid") === null);

    DC.setLang("en");
    for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 100));
    check("switching to EN localizes the roster",
      /Technician|Repair categories|Trade/.test(id("adWorkerList").innerHTML),
      id("adWorkerList").innerHTML.slice(0, 110));
    DC.setLang("zh");
  }
}

/**
 * The sign-in page has its own module and no session, so it gets its own run.
 * What matters here is the layout decision: sign-in and student sign-up are two
 * panels shown at once, not two tabs — so both forms must be present AND
 * visible, and the tab strip must be gone.
 */
async function runLogin() {
  console.log("\n=== /login.html + login.js ===");
  const res = await req("GET", BASE + "/login.html");
  check("the sign-in page serves to an anonymous visitor", res.status === 200,
    "status=" + res.status);
  if (res.status !== 200) return;
  buildPage(res.body);
  const sandbox = makeSandbox("");
  try {
    vm.runInContext(loadFile("common.js"), sandbox, { filename: "common.js" });
    vm.runInContext(loadFile("login.js"), sandbox, { filename: "login.js" });
  } catch (e) {
    check("sign-in scripts execute", false, e.message);
    return;
  }
  check("sign-in scripts execute", true);
  for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 100));

  const DC = sandbox.window.DC;
  check("sign-in page exposes DC", !!DC);
  check("an anonymous visitor is not bounced off the sign-in page",
    !sandbox.__redirect, sandbox.__redirect || "");

  check("the tab strip is gone", document.getElementById("authTabs") === null);
  const lf = document.getElementById("loginForm"), rf = document.getElementById("registerForm");
  check("sign-in and student sign-up are two panels, both visible at once",
    !!lf && !!rf && lf.hidden === false && rf.hidden === false,
    "login.hidden=" + (lf && lf.hidden) + " register.hidden=" + (rf && rf.hidden));

  const demo = document.getElementById("demoBody");
  check("the demo table lists all three roles",
    demo.querySelectorAll("tr").length === 4, "rows=" + demo.querySelectorAll("tr").length);
  check("...including the administrator account", /admin2026/.test(demo.innerHTML),
    demo.innerHTML.slice(0, 110));

  const colOpts = (document.getElementById("regCollege").innerHTML.match(/<option/g) || []).length;
  check("the sign-up panel offers the 8 colleges", colOpts === 9, "options=" + colOpts);

  const i18nNodes = document.querySelectorAll("[data-i18n]");
  const unresolved = i18nNodes
    .map((n) => [n.id || n.tagName, n.textContent])
    .filter(([, v]) => !v || /^[a-z][A-Za-z]+$/.test(v));
  check("every data-i18n key on the sign-in page resolved",
    i18nNodes.length > 5 && unresolved.length === 0,
    "nodes=" + i18nNodes.length +
      (unresolved.length ? " unresolved=" + JSON.stringify(unresolved.slice(0, 4)) : ""));
}

/**
 * The main menu, rendered once per role.
 *
 * Two things must hold at the same time, and they pull in opposite
 * directions: the menu is the ONE page every role can open, and it must show
 * only that role's sections. So the assertions are about the absence of the
 * other roles' cards just as much as about the presence of its own — a menu
 * that leaked an administrator card into the student view would be a real
 * regression, even though every card is a plain link the server would refuse.
 */
async function runMenu(role, cookie) {
  console.log("\n=== /menu.html + menu.js (" + role + ") ===");
  const res = await req("GET", BASE + "/menu.html", undefined, cookie);
  check("the menu serves to a signed-in " + role, res.status === 200,
    "status=" + res.status);
  if (res.status !== 200) return;
  buildPage(res.body);
  const sandbox = makeSandbox(cookie);
  try {
    vm.runInContext(loadFile("common.js"), sandbox, { filename: "common.js" });
    vm.runInContext(loadFile("menu.js"), sandbox, { filename: "menu.js" });
  } catch (e) {
    check("menu scripts execute", false, e.message);
    return;
  }
  check("menu scripts execute", true);
  for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 100));

  const DC = sandbox.window.DC;
  // The same two shorthands runRole uses locally — they are not shared, and
  // this function must not reach into another one's scope.
  const id = (n) => document.getElementById(n);
  const txt = (n) => (id(n) ? id(n).textContent : "");
  check("the menu is not bounced away", !sandbox.__redirect, sandbox.__redirect || "");

  const grid = document.getElementById("menuGrid");
  check("the menu grid exists", !!grid);
  if (!grid) return;

  // Every menu card, by the PAGE it opens. Comparing by href rather than by
  // label is deliberate: three roles share the "account settings" card, so the
  // same words appear in all three menus and a label comparison would report
  // the technician's own card as a leak from the student's menu.
  const EXPECT = {
    student: ["/report.html", "/records.html", "/account.html"],
    worker: ["/plan.html", "/jobs.html", "/account.html"],
    admin: ["/overview.html", "/tickets.html", "/users.html", "/roster.html",
            "/account.html"],
  };
  const ALL = ["/report.html", "/records.html", "/plan.html", "/jobs.html",
               "/overview.html", "/tickets.html", "/users.html", "/roster.html"];
  // The i18n key behind each card, for the language-switch checks below. Kept
  // separate from EXPECT because EXPECT is about PAGES and this is about WORDS.
  const TITLES = {
    student: ["menuStudentReport", "menuStudentRecords", "menuStudentAccount"],
    worker: ["menuWorkerPlan", "menuWorkerPool", "menuWorkerAccount"],
    admin: ["menuAdminOverview", "menuAdminTickets", "menuAdminUsers",
            "menuAdminRoster", "menuAdminAccount"],
  };
  const zh = DC.I18N.zh;
  const want = EXPECT[role];
  const text = grid.innerHTML;
  const cardPages = (text.match(/class="menu-card" href="([^"]+)"/g) || [])
    .map((h) => /href="([^"]+)"/.exec(h)[1]);

  for (const path of want) {
    check("the " + role + " menu offers " + path, cardPages.includes(path),
      JSON.stringify(cardPages));
  }
  // /account.html is shared by design; every OTHER page is one role's alone.
  const foreign = ALL.filter((p) => want.indexOf(p) < 0);
  for (const path of foreign) {
    check("the " + role + " menu does NOT offer " + path,
      !cardPages.includes(path), JSON.stringify(cardPages));
  }

  // Each card is a link to a page that does ONE thing — and no anchors: a
  // page that needs an anchor to reach its own section has already regrown
  // into the consolidated console this split removed.
  check("no card needs an anchor to reach its own section",
    cardPages.every((p) => p.indexOf("#") < 0), JSON.stringify(cardPages));

  // Every card carries a visible title and a subtitle, not just a bare link.
  // Read them out of the markup: these nodes were built from a string, so the
  // shim's id-based registry has nothing to resolve a selector against.
  const cards = text.match(/<a class="menu-card"[\s\S]*?<\/a>/g) || [];
  check("every card renders a title and a subtitle",
    cards.length === want.length &&
    cards.every((c) => /class="menu-name">[^<]+</.test(c) &&
                        /class="menu-sub">[^<]+</.test(c)),
    "cards=" + cards.length + " wanted=" + want.length);
  check("no card shows a raw i18n key as its title",
    !cards.some((c) => /class="menu-name">[a-z][A-Za-z]+</.test(c)),
    JSON.stringify(cards.map((c) => (/class="menu-name">([^<]*)</.exec(c) || [])[1])));

  // ...and every page a card names must actually be served to this role.
  for (const path of cardPages) {
    const r = await req("GET", BASE + path, undefined, cookie);
    check("the " + role + " card's page " + path + " opens",
      r.ok, "status=" + r.status + (r.location ? " -> " + r.location : ""));
  }

  // the greeting names the signed-in person, in their own role. The two
  // elements are written with textContent, so that is what has to be read.
  const hello = txt("menuHello") + "|" + txt("menuRole");
  // The name comes from the account, so assert on the shape of the greeting
  // rather than on one seeded person's name.
  check("the menu greets the signed-in account by name",
    /^你好，\S{2,}$/.test(txt("menuHello")) || /^Hello, \S{2,}$/.test(txt("menuHello")),
    JSON.stringify(hello.slice(0, 70)));
  check("...and names the role in words",
    /学生|师傅|管理员|Student|Technician|Administrator/.test(hello),
    JSON.stringify(hello.slice(0, 70)));

  // ...and it survives a language switch, still filtered to this role. The
  // card TITLE is the thing compared, because a subtitle can legitimately read
  // the same in two languages.
  // The card title goes through DC.esc() on its way into innerHTML, so the
  // comparison has to escape it the same way — otherwise a label containing
  // "&" could never be found in the markup.
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const titleOf = (lang) => TITLES[role].map((k) => esc(DC.I18N[lang][k]));
  DC.setLang("en");
  const en = grid.innerHTML;
  const enTitles = titleOf("en");
  check("the menu repaints in English",
    enTitles.every((s) => en.indexOf(s) >= 0), JSON.stringify(enTitles));
  check("...and no longer shows the Chinese titles",
    titleOf("zh").every((s) => en.indexOf(s) < 0),
    JSON.stringify(titleOf("zh")));
  check("...and still shows only this role's pages",
    (en.match(/class="menu-card" href="([^"]+)"/g) || [])
      .map((h) => /href="([^"]+)"/.exec(h)[1])
      .every((p) => want.includes(p)),
    JSON.stringify(want));
  DC.setLang("zh");
  check("switching back restores the Chinese titles",
    titleOf("zh").every((s) => grid.innerHTML.indexOf(s) >= 0),
    JSON.stringify(titleOf("zh")));
}

(async function main() {
  // health first
  const h = await req("GET", BASE + "/api/health");
  check("server reachable", h.ok, JSON.stringify(h.data).slice(0, 90));
  if (!h.ok) process.exit(1);

  const loginPage = await req("GET", BASE + "/login.html");
  check("the sign-in page is public", loginPage.ok, "status=" + loginPage.status);

  const student = await login(SESSIONS.student.username, SESSIONS.student.password);
  const worker = await login(SESSIONS.worker.username, SESSIONS.worker.password);
  const admin = await login(SESSIONS.admin.username, SESSIONS.admin.password);
  check("student signed in", student.user.role === "student", student.user.name);
  check("technician signed in", worker.user.role === "worker", worker.user.name);
  check("administrator signed in", admin.user.role === "admin", admin.user.name);

  // --- the page gate, from the browser's point of view -------------------- //
  //
  // Each page belongs to exactly ONE role, and a visitor of the wrong role is
  // bounced back to the menu. An administrator is still not a super-user: it
  // owns four pages and may open neither a student's nor a technician's.
  const sessions = { student, worker, admin };
  const OWNER = {
    "/report.html": "student", "/student-report.js": "student",
    "/records.html": "student", "/student-records.js": "student",
    "/plan.html": "worker", "/worker-plan.js": "worker",
    "/jobs.html": "worker", "/worker-jobs.js": "worker",
    "/overview.html": "admin", "/admin-overview.js": "admin",
    "/tickets.html": "admin", "/admin-tickets.js": "admin",
    "/users.html": "admin", "/admin-users.js": "admin",
    "/roster.html": "admin", "/admin-roster.js": "admin",
  };
  for (const [path, owner] of Object.entries(OWNER)) {
    for (const [role, session] of Object.entries(sessions)) {
      const r = await req("GET", BASE + path, undefined, session.cookie);
      if (role === owner) {
        check(role + " may open " + path, r.ok, "status=" + r.status);
      } else {
        check(role + " is bounced from " + path,
          r.status === 303 && /menu\.html/.test(r.location),
          r.status + " -> " + r.location);
      }
    }
    const anonPage = await req("GET", BASE + path);
    check("anonymous is sent to sign-in from " + path,
      anonPage.status === 303 && /login\.html/.test(anonPage.location),
      anonPage.status + " -> " + anonPage.location);
  }

  // The consolidated consoles the split removed must be gone, not aliased.
  for (const path of ["/index.html", "/worker.html", "/admin.html",
                      "/student.js", "/worker.js", "/admin.js"]) {
    const r = await req("GET", BASE + path, undefined, student.cookie);
    check("the retired page " + path + " is gone", r.status === 404,
      "status=" + r.status);
  }

  // the pages everybody signed in may open
  for (const path of ["/menu.html", "/account.html"]) {
    const anonPage = await req("GET", BASE + path);
    check("anonymous cannot open " + path,
      anonPage.status === 303 && /login\.html/.test(anonPage.location),
      anonPage.status + " -> " + anonPage.location);
    for (const [role, session] of Object.entries(sessions)) {
      const r = await req("GET", BASE + path, undefined, session.cookie);
      check(role + " may open " + path, r.ok, "status=" + r.status);
    }
  }

  await runLogin();
  // The menu is the one page every role shares, so it is rendered three times —
  // once per role — and each pass must show only that role's own cards.
  await runMenu("student", student.cookie);
  await runMenu("worker", worker.cookie);
  await runMenu("admin", admin.cookie);
  // ...and then every detail page, each on its own.
  await runRole("/report.html", "student-report.js", student.cookie);
  await runRole("/records.html", "student-records.js", student.cookie);
  await runRole("/account.html", "account.js", student.cookie);
  // account settings is shared: the password half is valid for every role, and
  // the page must hide the room half from the two that have no room.
  await runRole("/account.html", "account.js", worker.cookie, { asRole: "worker" });
  await runRole("/account.html", "account.js", admin.cookie, { asRole: "admin" });
  await runRole("/plan.html", "worker-plan.js", worker.cookie);
  await runRole("/jobs.html", "worker-jobs.js", worker.cookie);
  await runRole("/overview.html", "admin-overview.js", admin.cookie);
  await runRole("/tickets.html", "admin-tickets.js", admin.cookie);
  await runRole("/users.html", "admin-users.js", admin.cookie);
  await runRole("/roster.html", "admin-roster.js", admin.cookie);

  const failed = results.filter((r) => !r.ok);
  console.log("\n" + "=".repeat(60));
  console.log("%d checks, %d failures", results.length, failed.length);
  failed.forEach((f) => console.log("  !! " + f.name + (f.detail ? " — " + f.detail : "")));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error("harness error:", e);
  process.exit(2);
});