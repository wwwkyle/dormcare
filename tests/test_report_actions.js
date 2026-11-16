"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/js/student-report.js"), "utf8");
const commonSource = fs.readFileSync(path.join(__dirname, "../web/js/common.js"), "utf8");
const referenceImageUrl = "https://cloud.dify.ai/files/reference/file-preview?timestamp=1794789601&nonce=test&sign=abc+/=";
const referenceReply = [
  "关于热水器的参考图片，我找到了一张特定案例的**燃气装置阀门位置参考图**。",
  "![图片说明](" + referenceImageUrl + ")",
  "**来源与适用范围说明：**",
  "* **来源**：文件 `assets/heater_case_01/燃气阀门说明原图.jpg`。\n" +
    "* **局限性**：此图**不代表**所有宿舍的通用情况。\n" +
    "* **重要提醒**：实际设备应由负责该装置的工作人员确认。",
].join("\n\n");

function actionHarness() {
  const nodes = new Map();
  const requests = [];
  const context = {
    agentSessionId: "session-test",
    agentRequestPending: false,
    reportUploads: [],
    conversation: [],
    lastAgentResult: null,
    renderConversation() {},
    renderAgentResult(result) { context.lastAgentResult = result; },
    applyAgentDraft() {},
    $(identifier) {
      if (!nodes.has(identifier)) nodes.set(identifier, { disabled: false, textContent: "" });
      return nodes.get(identifier);
    },
    DC: {
      lang: "zh",
      busy(button, pending) { button.disabled = pending; },
      setMsg() {},
      errorText() { return "request failed"; },
      post(endpoint, body) {
        return new Promise((resolve) => requests.push({ endpoint, body, resolve }));
      },
    },
  };
  vm.createContext(context);
  for (const name of ["setAgentBusy", "agentAction", "agentImageLimit", "uploadFiles", "refreshDraftImages"]) {
    const definition = source.match(new RegExp("  function " + name + "\\([^]*?\\n  \\}"));
    if (definition) vm.runInContext(definition[0], context);
  }
  return { context, requests };
}

function replyHarness() {
  const { context } = actionHarness();
  Object.assign(context, { URL, t: (key) => key, setStage() {} });
  const start = commonSource.indexOf("  function esc(s)");
  const end = commonSource.indexOf("  DC.md = md;") + "  DC.md = md;".length;
  vm.runInContext(commonSource.slice(start, end), context);
  context.md = context.DC.md;
  for (const name of ["renderConversation", "renderAgentResult"]) {
    const definition = source.match(new RegExp("  function " + name + "\\([^]*?\\n  \\}"));
    vm.runInContext(definition[0], context);
  }
  return context;
}

test("Dify reference replies render images, emphasis, lists and code", () => {
  const context = replyHarness();
  context.renderAgentResult({ reply: referenceReply, stage: "advice" });
  const html = context.$("agentReply").innerHTML || "";
  assert.match(html, /<strong>燃气装置阀门位置参考图<\/strong>/);
  assert.match(html, /<img\b/);
  assert.ok(html.includes('src="' + context.esc(referenceImageUrl) + '"'));
  assert.ok(html.includes('href="' + context.esc(referenceImageUrl) + '"'));
  assert.match(html, /alt="图片说明"/);
  assert.match(html, /<ul><li><strong>来源<\/strong>/);
  assert.match(html, /<code>assets\/heater_case_01\/燃气阀门说明原图\.jpg<\/code>/);
  assert.match(html, /实际设备应由负责该装置的工作人员确认/);
});

test("reference images remain rendered in assistant history without rendering user HTML", () => {
  const context = replyHarness();
  context.conversation = [
    { role: "user", text: '<img src="x" onerror="alert(1)"> **用户原文**' },
    { role: "assistant", text: referenceReply },
    { role: "user", text: "还有其他注意事项吗？" },
    { role: "assistant", text: "最新回复不应重复显示。" },
  ];
  context.renderConversation();
  const html = context.$("agentLog").innerHTML || "";
  assert.equal(context.$("agentLog").hidden, false);
  assert.match(html, /&lt;img src=&quot;x&quot;/);
  assert.match(html, /\*\*用户原文\*\*/);
  assert.equal((html.match(/<img\b/g) || []).length, 1);
  assert.match(html, /<strong>燃气装置阀门位置参考图<\/strong>/);
  assert.doesNotMatch(html, /最新回复不应重复显示/);
});

test("Markdown allows HTTP(S) images and links without changing signed URLs", () => {
  const context = replyHarness();
  const html = context.md("[原图](" + referenceImageUrl + ") ![参考图](http://example.org/image.png)");
  assert.ok(html.includes('href="' + context.esc(referenceImageUrl) + '"'));
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /referrerpolicy="no-referrer"/);
  assert.match(html, /src="http:\/\/example\.org\/image\.png"/);
});

test("Markdown blocks unsafe image and link protocols and escapes HTML", () => {
  const context = replyHarness();
  for (const url of ["javascript:alert", "data:image/svg+xml,test", "file:///tmp/test.png", "//example.org/test.png"]) {
    const html = context.md("![图片](" + url + ") [链接](" + url + ")");
    assert.doesNotMatch(html, /<(?:img|a)\b/);
  }
  const html = context.md('<script>alert(1)</script> <img src="x" onerror="alert(1)">\n' +
    '![图片" onerror="alert](https://example.org/image.png)');
  assert.doesNotMatch(html, /<script\b|onerror="/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /alt="图片&quot; onerror=&quot;alert"/);
});

test("Markdown keeps code literal and handles plain text and empty replies", () => {
  const context = replyHarness();
  const html = context.md("`![图片](" + referenceImageUrl + ")`\n**先看 `说明`**\n1. 第一项\n2. 第二项");
  assert.doesNotMatch(html, /<img\b/);
  assert.match(html, /<code>!\[图片\]/);
  assert.match(html, /<strong>先看 <code>说明<\/code><\/strong>/);
  assert.match(html, /<ol><li>第一项<\/li><li>第二项<\/li><\/ol>/);
  context.renderAgentResult({ reply: "普通回复 & <文字>", stage: "follow_up" });
  assert.equal(context.$("agentReply").innerHTML, "<p>普通回复 &amp; &lt;文字&gt;</p>");
  context.renderAgentResult({ reply: "", stage: "follow_up" });
  assert.equal(context.$("agentReply").innerHTML, "");
});

test("create-draft disables the clicked button and blocks duplicate requests", async () => {
  const { context, requests } = actionHarness();
  context.agentAction("create_draft");
  assert.equal(context.$("agentNeedRepair").disabled, true);
  context.agentAction("create_draft");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.action, "create_draft");
  requests[0].resolve({ ok: true, data: { stage: "follow_up", reply: "请补充故障现象。" } });
  await new Promise(setImmediate);
  assert.equal(context.$("agentNeedRepair").disabled, false);
  assert.equal(context.agentRequestPending, false);
});

test("failed actions release the clicked button for retry", async () => {
  const { context, requests } = actionHarness();
  context.agentAction("create_draft");
  requests[0].resolve({ ok: false, data: {} });
  await new Promise(setImmediate);
  assert.equal(context.$("agentNeedRepair").disabled, false);
  assert.equal(context.agentRequestPending, false);
  context.agentAction("create_draft");
  assert.equal(requests.length, 2);
});

test("button intent appears in the transcript before the remote answer", async () => {
  const { context, requests } = actionHarness();
  context.agentAction("self_help");
  requests[0].resolve({ ok: true, data: {
    user_message: "我想先尝试安全的自助排查。", reply: "请先确认设备状态。", stage: "advice",
  } });
  await new Promise(setImmediate);
  assert.equal(context.conversation[0].role, "user");
  assert.equal(context.conversation[0].text, "我想先尝试安全的自助排查。");
  assert.equal(context.conversation[1].role, "assistant");
});

test("workflow image limit rejects excess photos without posting", async () => {
  const { context, requests } = actionHarness();
  context.DC.cfg = { agent_image_limit: 3 };
  context.fill = (template, values) => template.replace("{n}", values.n);
  context.t = () => "最多 {n} 张图片";
  assert.equal(context.agentImageLimit(), 3);
  await assert.rejects(context.uploadFiles([{}, {}, {}, {}], "agent"), /最多 3 张/);
  assert.equal(requests.length, 0);
});

test("a failed image refresh cannot reopen an unready draft", async () => {
  const { context, requests } = actionHarness();
  context.lastAgentDraft = { title: "old draft" };
  let applied = false;
  context.applyAgentDraft = () => { applied = true; };
  context.refreshDraftImages();
  assert.equal(context.agentRequestPending, true);
  requests[0].resolve({ ok: true, data: {
    source: "dify_error", stage: "follow_up", ready: false, draft: { title: "old draft" },
  } });
  await new Promise(setImmediate);
  assert.equal(applied, false);
  assert.equal(context.$("agentConfirm").hidden, true);
  assert.equal(context.agentRequestPending, false);
});

function addressHarness() {
  const { context } = actionHarness();
  const blocks = { shaw: ["东座 1 栋", "东座 2 栋"], muse: ["西座 1 栋", "西座 2 栋"] };
  Object.assign(context.DC, {
    cfg: {},
    me: { college: "shaw", block: "东座 2 栋", dorm: "B-901" },
    blockOptionsHtml(college) { return blocks[college] || []; },
    realBlockCount(college) { return (blocks[college] || []).length; },
    blockHintText() { return ""; },
    ownAddress(user = this.me) { return [user.college, user.block, user.dorm].join(" "); },
  });
  Object.assign(context, {
    selType: null, selPriority: "normal", publicArea: false,
    t: (text) => text, esc: String, repairName: String, priLabel: String,
    renderTypeChips() {}, renderPriorityChips() {}, setStage() {},
    fillSelect(select, values) {
      select.value = values.includes(select.value) ? select.value : (values[0] || "");
    },
  });
  context.$("addr").dataset = {};
  context.$("fCollege").value = context.DC.me.college;
  context.$("fBlock").value = "";
  for (const name of ["renderBlockOptions", "renderAddress", "applyAgentDraft"]) {
    const definition = source.match(new RegExp("  function " + name + "\\([^]*?\\n  \\}"));
    vm.runInContext(definition[0], context);
  }
  return context;
}

test("workflow public-area fields populate editable location controls", () => {
  const context = addressHarness();
  context.applyAgentDraft({ title: "走廊灯故障", repair_type: "light", priority: "normal",
    description: "三楼走廊灯不亮。", public_area: true, college: "shaw",
    block: "东座 2 栋", dorm: "三楼东侧走廊", image_ids: [],
  }, true);
  assert.equal(context.$("fPublic").checked, true);
  assert.equal(context.$("addrFields").hidden, false);
  assert.equal(context.$("fCollege").value, "shaw");
  assert.equal(context.$("fBlock").value, "东座 2 栋");
  assert.equal(context.$("fDorm").value, "三楼东侧走廊");
  assert.equal(context.$("fTitle").value, "走廊灯故障");
});

test("public-area drafts without dropdown values default to the account address", () => {
  const context = addressHarness();
  context.$("fCollege").value = "muse";
  context.applyAgentDraft({ public_area: true, dorm: "三楼东侧走廊" }, true);
  assert.equal(context.$("fPublic").checked, true);
  assert.equal(context.$("fCollege").value, context.DC.me.college);
  assert.equal(context.$("fBlock").value, context.DC.me.block);
  assert.equal(context.$("fDorm").value, "三楼东侧走廊");
});

test("restoring and rendering a public-area draft preserves user-selected dropdowns", () => {
  const context = addressHarness();
  context.applyAgentDraft({
    public_area: true, college: "muse", block: "西座 2 栋", dorm: "洗衣房",
  }, true);
  context.renderAddress();
  assert.equal(context.$("fCollege").value, "muse");
  assert.equal(context.$("fBlock").value, "西座 2 栋");
  assert.equal(context.$("fDorm").value, "洗衣房");
});

test("private drafts uncheck public-area reporting and show the account dorm", () => {
  const context = addressHarness();
  context.applyAgentDraft({ public_area: true, dorm: "走廊" }, true);
  context.applyAgentDraft({ public_area: false, dorm: "Z-999" }, true);
  assert.equal(context.$("fPublic").checked, false);
  assert.equal(context.$("addrFields").hidden, true);
  assert.equal(context.$("addrOwn").textContent, "shaw 东座 2 栋 B-901");
  assert.equal(context.DC.me.dorm, "B-901");
});

test("manually enabling public-area reporting defaults to the account college and block", () => {
  const context = addressHarness();
  context.$("fCollege").value = "muse";
  const definition = source.match(/\$\("fPublic"\)\.addEventListener\("change", (function \(\) \{[^]*?\n    \})\);/);
  const handler = vm.runInContext("(" + definition[1] + ")", context);
  handler.call({ checked: true });
  assert.equal(context.$("fCollege").value, context.DC.me.college);
  assert.equal(context.$("fBlock").value, context.DC.me.block);
  assert.equal(context.$("addrFields").hidden, false);
});
