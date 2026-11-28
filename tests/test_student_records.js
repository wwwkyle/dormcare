/* Student repair-records regression tests; no server or packages required.
 * Run: node tests/test_student_records.js
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/js/student-records.js"), "utf8");

function recordsHarness() {
  const nodes = new Map();
  const requests = [];
  const messages = [];
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      value: "", innerHTML: "", textContent: "", dataset: {}, hidden: false,
      disabled: false, handlers: {},
      addEventListener(type, fn) { this.handlers[type] = fn; },
    });
    return nodes.get(id);
  };
  const DC = {
    lang: "zh", tickets: [],
    t: (key) => key,
    esc: (value) => String(value == null ? "" : value),
    fill: (text, data) => String(text).replace(/\{(\w+)\}/g, (_, k) => data[k]),
    priLabel: () => "pri", pairsText: () => "", stars: () => "★",
    row: (label, value) => label + value, workerName: () => "w",
    dayLabel: () => "day",
    loadTickets: () => Promise.resolve(),
    post(endpoint, body) {
      return new Promise((resolve) => requests.push({ endpoint, body, resolve }));
    },
    api: () => Promise.resolve({ ok: false }),
    busy(button, on) { button.disabled = on; },
    setMsg(el, code) { messages.push(code); },
    errorText: () => "err",
    afterLang() {}, start() {},
  };
  const context = { window: { DC }, document: { getElementById: node }, DC,
    URLSearchParams, setTimeout };
  vm.createContext(context);
  vm.runInContext(source, context);
  return {
    node, requests, messages,
    click(action, id) {
      const button = {
        disabled: false,
        getAttribute(key) {
          if (key === "data-act") return action;
          if (key === "data-id") return id;
          return null;
        },
      };
      const target = { closest(sel) {
        if (sel === "button[data-star]") return null;
        if (sel === "button[data-act]") return button;
        return null;
      } };
      node("ticketList").handlers.click({
        target, preventDefault() {}, stopPropagation() {},
      });
      return button;
    },
  };
}

const test = require("node:test");

test("confirm signs off a finished job through the status endpoint", async () => {
  const h = recordsHarness();
  const button = h.click("confirm", "T1");
  assert.equal(button.disabled, true);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].endpoint, "/api/tickets/status");
  assert.equal(h.requests[0].body.status, "confirmed");
  h.requests[0].resolve({ ok: true, data: { ok: true } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const msg = h.node("formMsg");
  assert.equal(msg.dataset.state, "ok");
  assert.equal(msg.textContent, "tkConfirmed");
  assert.equal(button.disabled, false);
});

test("reopen sends a failed job back to the pending queue", async () => {
  const h = recordsHarness();
  h.click("reopen", "T2");
  assert.equal(h.requests[0].endpoint, "/api/tickets/status");
  assert.equal(h.requests[0].body.status, "pending");
  h.requests[0].resolve({ ok: true, data: { ok: true } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const msg = h.node("formMsg");
  assert.equal(msg.dataset.state, "ok");
  assert.equal(msg.textContent, "tkReopened");
});

test("a rejected status change shows the server error instead", async () => {
  const h = recordsHarness();
  h.click("confirm", "T3");
  h.requests[0].resolve({ ok: false, data: { code: "bad_status_transition" } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(h.messages.length, 0);
  assert.equal(h.node("formMsg").dataset.state, "err");
  assert.equal(h.node("formMsg").textContent, "err");
});

test("unknown actions still do nothing", () => {
  const h = recordsHarness();
  h.click("mystery", "T4");
  assert.equal(h.requests.length, 0);
});
