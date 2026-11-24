/* DormCare · 系统设置 — model key, Dify credentials and DeepSeek ASR
 *
 * One menu card, one page. The model key stays in server memory, while the
 * Dify and ASR credentials are persisted so a redeployment needs no .env edit.
 * This is the only place an administrator fills in these values. No secret is
 * ever sent back to the browser after it has been saved.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;
  var status = null;
  var dify = null;
  var asr = null;

  var PROVIDER_KEY = {
    openrouter: "providerOpenrouter",
    openai: "OpenAI",
    deepseek: "DeepSeek",
    moonshot: "providerMoonshot",
    zhipu: "providerZhipu",
    siliconflow: "providerSilicon",
    dashscope: "providerDashscope",
    custom: "providerCustom"
  };

  // Shown only while the box is empty. Leaving it blank saves this name.
  var MODEL_EXAMPLE = {
    openrouter: "deepseek/deepseek-chat",
    openai: "gpt-4o-mini",
    deepseek: "deepseek-flash",
    moonshot: "moonshot-v1-8k",
    zhipu: "glm-4-flash",
    siliconflow: "deepseek-ai/DeepSeek-V3",
    dashscope: "qwen-plus",
    custom: "your-model"
  };

  function $(id) { return document.getElementById(id); }

  function providerLabel(id) {
    var key = PROVIDER_KEY[id];
    if (!key) return id || "";
    if (key === "OpenAI" || key === "DeepSeek") return key;
    return t(key);
  }

  function renderIdentity() {
    var node = $("adminIdentity");
    if (!node) return;
    var me = DC.me;
    node.textContent = me ? fill(t("adminIdentityBar"), { id: me.username || "" }) : "";
  }

  function modelPlaceholder() {
    var provider = $("setupProvider");
    var id = provider ? provider.value : "openrouter";
    return fill(t("modelExample"), {
      example: MODEL_EXAMPLE[id] || MODEL_EXAMPLE.custom
    });
  }

  function syncProvider() {
    var provider = $("setupProvider");
    var wrap = $("setupBaseWrap");
    var base = $("setupBase");
    var model = $("setupModel");
    if (model) model.placeholder = modelPlaceholder();
    if (!provider || !wrap || !base) return;
    var custom = provider.value === "custom";
    wrap.hidden = !custom;
    base.required = custom;
  }

  function render() {
    var now = $("modelNow");
    var form = $("modelForm");
    var key = $("setupKey");
    var save = $("setupBtn");
    var reset = $("modelReset");
    var s = status || {};
    var locked = !!s.locked;
    if (form) form.classList.toggle("locked", locked);
    ["setupProvider", "setupModel", "setupBase", "setupKey", "setupBtn"].forEach(function (id) {
      var node = $(id);
      if (node) node.disabled = locked;
    });
    if (reset) reset.disabled = locked || !s.configured;
    if (save) save.textContent = s.configured ? t("modelReplace") : t("modelSave");
    if (key) {
      key.required = !s.configured;
      key.placeholder = s.configured ? t("modelKeyKeep") : t("setupPlaceholder");
    }
    var model = $("setupModel");
    if (model) model.placeholder = modelPlaceholder();
    if (!now) return;
    if (locked) {
      now.textContent = fill(t("modelStatusEnv"), {
        model: s.model || "—"
      });
    } else if (s.configured) {
      now.textContent = fill(t("modelStatusOn"), {
        provider: providerLabel(s.provider) || "—",
        model: s.model || "—"
      });
    } else {
      now.textContent = t("modelStatusNone");
    }
  }

  function applyStatus(next) {
    status = next || {};
    var provider = $("setupProvider");
    var model = $("setupModel");
    var base = $("setupBase");
    if (provider && status.provider && PROVIDER_KEY[status.provider]) {
      provider.value = status.provider;
    }
    if (model && document.activeElement !== model) {
      model.value = status.configured ? (status.model || "") : "";
    }
    if (base && status.base_url && document.activeElement !== base) {
      base.value = status.base_url;
    }
    syncProvider();
    render();
    DC.refreshHealth();
  }

  function load() {
    return DC.api("/api/admin/model").then(function (res) {
      if (!res.ok) {
      showError($("setupMsg"), res.data);
        return;
      }
      applyStatus(res.data);
    });
  }

  function difySourceLabel(source) {
    return t(source === "saved" ? "difySourceSaved" : "difySourceEnv");
  }

  function renderDify() {
    var now = $("difyNow");
    var key = $("difyKey");
    var reset = $("difyReset");
    var s = dify || {};
    if (key) key.placeholder = s.configured ? t("difyKeyKeep") : t("difyKeyPh");
    if (reset) reset.disabled = !s.source;
    if (!now) return;
    if (!s.configured) {
      now.textContent = t("difyStatusNone");
      return;
    }
    now.textContent = fill(t("difyStatusOn"), {
      source: difySourceLabel(s.source || "env"),
      base: s.base_url || "—"
    });
    if (s.source === "env") now.textContent += " " + t("difyStatusEnv");
  }

  function applyDifyStatus(next) {
    dify = next || {};
    // Never echo the key; only the address is safe to prefill. A field the
    // operator is typing in is left alone so a language switch cannot wipe it.
    var base = $("difyBase");
    if (base && document.activeElement !== base) base.value = dify.base_url || "";
    renderDify();
    DC.refreshHealth();
  }

  function loadDify() {
    return DC.api("/api/admin/dify").then(function (res) {
      if (!res.ok) {
        showError($("difyMsg"), res.data);
        return;
      }
      applyDifyStatus(res.data);
    });
  }

  function onDifySave(e) {
    e.preventDefault();
    var btn = $("difySave");
    var msg = $("difyMsg");
    var key = $("difyKey");
    var base = $("difyBase");
    DC.busy(btn, true);
    msg.textContent = t("setupWorking");
    DC.post("/api/dify/setup", {
      api_key: key ? key.value : "",
      base_url: base ? base.value : ""
    }).then(function (res) {
      if (key) key.value = "";
      DC.busy(btn, false);
      var code = (res.data && res.data.code) || "";
      if (res.ok && res.data && res.data.ok) {
        DC.setMsg(msg, code || "dify_setup_ok", "ok");
        applyDifyStatus(res.data.status || dify);
      } else {
        showError(msg, res.data);
      }
    }).catch(function () {
      if (key) key.value = "";
      DC.busy(btn, false);
      DC.setMsg(msg, "difyNetErr", "err");
    });
  }

  function onDifyReset() {
    var ask = t("difyResetAsk");
    if (typeof window.confirm === "function" && !window.confirm(ask)) return;
    var btn = $("difyReset");
    var msg = $("difyMsg");
    DC.busy(btn, true);
    DC.post("/api/dify/setup/reset", {}).then(function (res) {
      DC.busy(btn, false);
      var code = (res.data && res.data.code) || "";
      if (res.ok && res.data && res.data.ok) {
        var key = $("difyKey");
        if (key) key.value = "";
        DC.setMsg(msg, code || "dify_reset", "ok");
        applyDifyStatus(res.data.status || { configured: false, source: "" });
      } else {
        showError(msg, res.data);
      }
    }).catch(function () {
      DC.busy(btn, false);
      DC.setMsg(msg, "difyNetErr", "err");
    });
  }

  function renderAsr() {
    var now = $("asrNow");
    var key = $("asrKey");
    var reset = $("asrReset");
    var s = asr || {};
    if (key) key.placeholder = s.configured ? t("asrKeyKeep") : t("asrKeyPh");
    if (reset) reset.disabled = !s.source;
    if (!now) return;
    if (!s.configured) {
      now.textContent = t("asrStatusNone");
      return;
    }
    now.textContent = fill(t("asrStatusOn"), {
      source: t(s.source === "saved" ? "asrSourceSaved" : "asrSourceEnv")
    });
    if (s.source === "env") now.textContent += " " + t("asrStatusEnv");
  }

  function applyAsrStatus(next) {
    asr = next || {};
    renderAsr();
  }

  function loadAsr() {
    return DC.api("/api/admin/asr").then(function (res) {
      if (!res.ok) {
        showError($("asrMsg"), res.data);
        return;
      }
      applyAsrStatus(res.data);
    });
  }

  function onAsrSave(e) {
    e.preventDefault();
    var btn = $("asrSave");
    var msg = $("asrMsg");
    var key = $("asrKey");
    DC.busy(btn, true);
    msg.textContent = t("setupWorking");
    DC.post("/api/asr/setup", { api_key: key ? key.value : "" }).then(function (res) {
      if (key) key.value = "";
      DC.busy(btn, false);
      var code = (res.data && res.data.code) || "";
      if (res.ok && res.data && res.data.ok) {
        DC.setMsg(msg, code || "asr_setup_ok", "ok");
        applyAsrStatus(res.data.status || asr);
      } else {
        showError(msg, res.data);
      }
    }).catch(function () {
      if (key) key.value = "";
      DC.busy(btn, false);
      DC.setMsg(msg, "asrNetErr", "err");
    });
  }

  function onAsrReset() {
    var ask = t("asrResetAsk");
    if (typeof window.confirm === "function" && !window.confirm(ask)) return;
    var btn = $("asrReset");
    var msg = $("asrMsg");
    DC.busy(btn, true);
    DC.post("/api/asr/setup/reset", {}).then(function (res) {
      DC.busy(btn, false);
      var code = (res.data && res.data.code) || "";
      if (res.ok && res.data && res.data.ok) {
        var key = $("asrKey");
        if (key) key.value = "";
        DC.setMsg(msg, code || "asr_reset", "ok");
        applyAsrStatus(res.data.status || { configured: false, source: "" });
      } else {
        showError(msg, res.data);
      }
    }).catch(function () {
      DC.busy(btn, false);
      DC.setMsg(msg, "asrNetErr", "err");
    });
  }

  function showError(msg, data) {
    var code = data && data.code;
    if (code && t(code) !== code) DC.setMsg(msg, code, "err");
    else {
      DC.setMsg(msg, "", "err");
      if (msg) msg.textContent = DC.errorText(data);
    }
  }

  function onSave(e) {
    e.preventDefault();
    var btn = $("setupBtn");
    var msg = $("setupMsg");
    var provider = $("setupProvider");
    var model = $("setupModel");
    var base = $("setupBase");
    var key = $("setupKey");
    DC.busy(btn, true);
    msg.textContent = t("setupWorking");
    DC.post("/api/setup", {
      key: key ? key.value : "",
      provider: provider ? provider.value : "openrouter",
      model: model ? model.value : "",
      base_url: base ? base.value : "",
      replace: !!(status && status.configured)
    }).then(function (res) {
      if (key) key.value = "";
      DC.busy(btn, false);
      var code = (res.data && res.data.code) || "";
      if (res.ok && res.data && res.data.ok) {
        DC.setMsg(msg, code || "setup_ok", "ok");
        applyStatus(res.data.status || status);
      } else {
        showError(msg, res.data);
      }
    }).catch(function () {
      if (key) key.value = "";
      DC.busy(btn, false);
      DC.setMsg(msg, "setupNetErr", "err");
    });
  }

  function onReset() {
    if (status && status.locked) return;
    var ask = t("modelResetAsk");
    if (typeof window.confirm === "function" && !window.confirm(ask)) return;
    var btn = $("modelReset");
    var msg = $("setupMsg");
    DC.busy(btn, true);
    DC.post("/api/setup/reset", {}).then(function (res) {
      DC.busy(btn, false);
      var code = (res.data && res.data.code) || "";
      if (res.ok && res.data && res.data.ok) {
        var key = $("setupKey");
        if (key) key.value = "";
        DC.setMsg(msg, code || "setup_reset", "ok");
        applyStatus(res.data.status || { configured: false });
      } else {
        showError(msg, res.data);
      }
    }).catch(function () {
      DC.busy(btn, false);
      DC.setMsg(msg, "setupNetErr", "err");
    });
  }

  var form = $("modelForm");
  if (form) form.addEventListener("submit", onSave);
  var provider = $("setupProvider");
  if (provider) provider.addEventListener("change", syncProvider);
  var reset = $("modelReset");
  if (reset) reset.addEventListener("click", onReset);
  var difyForm = $("difyForm");
  if (difyForm) difyForm.addEventListener("submit", onDifySave);
  var difyReset = $("difyReset");
  if (difyReset) difyReset.addEventListener("click", onDifyReset);
  var asrForm = $("asrForm");
  if (asrForm) asrForm.addEventListener("submit", onAsrSave);
  var asrReset = $("asrReset");
  if (asrReset) asrReset.addEventListener("click", onAsrReset);

  DC.afterLang(function () {
    renderIdentity();
    render();
    renderDify();
    renderAsr();
  });

  DC.start(function () {
    renderIdentity();
    return Promise.all([load(), loadDify(), loadAsr()]);
  }, "admin");
})(window.DC);
