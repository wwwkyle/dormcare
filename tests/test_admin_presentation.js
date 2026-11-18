/* Admin display regression tests; no server or external packages required.
 * Run: node tests/test_admin_presentation.js
 */
"use strict";
const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, { value: "", innerHTML: "", textContent: "", addEventListener() {} });
  return elements.get(id);
};
const esc = value => String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const DC = { t: key => key, esc, fill: (text, data) => text.replace(/\{(\w+)\}/g, (_, key) => data[key]),
  row: (label, value) => '<div>' + label + ':' + value + '</div>', priLabel: String,
  pairsText: pairs => JSON.stringify(pairs), dayLabel: String, afterLang() {}, start() {}, lang: "en" };
let source = fs.readFileSync(path.join(__dirname, "../web/js/admin-tickets.js"), "utf8");
source = source.replace("  wire();", `  DC.test = { compareTickets: compareTickets, appointmentTime: appointmentTime,
  appointmentText: appointmentText, reportedText: reportedText, ticketRow: ticketRow,
  renderList: function (data, filter) { tickets = data; statusFilter = filter; renderTickets(); } };
  wire();`);
vm.runInNewContext(source, { window: { DC }, document: { getElementById: element } });
const api = DC.test;
let checks = 0;
function check(label, fn) { fn(); checks++; console.log("PASS " + label); }
const ticket = (id, status, priority, time, date = "2026-11-17") => ({
  id, status, priority, date, appointment: time ? { start: time, end: "10:00" } : null,
  created_at: "2026-11-01T10:00:00", slots: [[8, 11]], repair_label: "Desk", college_label: "College", dorm: "A-1"
});
const input = [ticket("confirmed", "confirmed", "urgent", "08:00"), ticket("done", "done", "urgent", "08:00"),
  ticket("accepted", "accepted", "urgent", "08:00"), ticket("low", "pending", "low", "08:00"),
  ticket("normal", "pending", "normal", "08:00"), ticket("high", "pending", "high", "08:00"),
  ticket("unscheduled", "pending", "urgent", null), ticket("later", "pending", "urgent", "09:00"),
  ticket("earlier", "pending", "urgent", "08:00")];
check("status, urgency, then scheduled appointment order", () => {
  assert.deepEqual(input.slice().sort(api.compareTickets).map(t => t.id),
    ["earlier", "later", "unscheduled", "high", "normal", "low", "accepted", "done", "confirmed"]);
});
check("missing and malformed appointments sort last without NaN", () => {
  for (const tk of [ticket("missing", "pending", "urgent"), ticket("invalid", "pending", "urgent", "25:99"),
    ticket("bad-date", "pending", "urgent", "09:00", "unknown")]) {
    assert.equal(api.appointmentTime(tk, "start"), Infinity);
    assert.equal(api.compareTickets(tk, input[8]), 1);
    assert.equal(api.compareTickets(tk, tk), 0);
  }
});
check("ties use actual creation timestamp, then stable ticket ID", () => {
  const a = ticket("A", "pending", "normal", "09:00"), b = { ...a, id: "B" };
  assert(api.compareTickets(a, b) < 0);
  b.created_at = "2026-10-01T12:00:00"; assert(api.compareTickets(b, a) < 0);
});
check("availability never becomes an appointment or a creation timestamp", () => {
  const tk = ticket("missing", "pending", "normal"); delete tk.created_at;
  assert.equal(api.appointmentText(tk), "adNoAppointment");
  assert.equal(api.reportedText(tk), "unitNone");
  assert(!api.ticketRow(tk).includes('class="ad-appointment-passed"'));
});
check("past appointment warning is restricted to unfinished tickets", () => {
  for (const state of ["pending", "accepted", "done", "confirmed"]) {
    const tk = ticket("old", state, "normal", "08:00", "2000-01-01");
    assert.equal(api.ticketRow(tk).includes('class="ad-appointment-passed"'), ["pending", "accepted"].includes(state));
  }
  assert(!api.ticketRow(ticket("future", "pending", "urgent", "08:00", "2099-01-01")).includes('class="ad-appointment-passed"'));
});
check("all rows keep administration actions and escape user content", () => {
  const html = api.ticketRow({ ...input[8], description: '<img src=x onerror="bad()">', student_name: '<script>bad()</script>' });
  for (const action of ["edit-ticket", "status-ticket", "assign-ticket", "delete-ticket"]) assert(html.includes('data-act="' + action + '"'));
  assert(!html.includes("<script>")); assert(html.includes("&lt;script>"));
  const detail = html.slice(html.indexOf('class="tk-body"'));
  assert(detail.indexOf("adReportedAt:") < detail.indexOf("adAppointment:"));
});
check("filtering retains the same order and never mutates API data", () => {
  const before = JSON.stringify(input); api.renderList(input, "pending");
  const html = element("adTicketList").innerHTML;
  assert(!html.includes('data-ticket-id="accepted"'));
  assert(html.indexOf('data-ticket-id="earlier"') < html.indexOf('data-ticket-id="later"'));
  assert.equal(JSON.stringify(input), before);
});
console.log(checks + " admin presentation checks passed");
