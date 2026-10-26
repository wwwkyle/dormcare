/* DormCare · 模型密钥 — enroll, replace, and reset the model key
 *
 * One menu card, one page. The key stays in server memory. This page is the
 * only place an administrator fills it in, changes the provider or model, or
 * clears it. A key that arrived from the environment cannot be touched here.
 *
 * Depends on common.js (window.DC) loaded first.
 */
(function (DC) {
  "use strict";

  var t = DC.t, esc = DC.esc, fill = DC.fill;
  var status = null;

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

  function syncProvider() {
    var provider = $("setupProvider");
    var wrap = $("setupBaseWrap");
    var base = $("setupBase");
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
    if (model && status.model && document.activeElement !== model) {
      model.value = status.model;
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

  DC.afterLang(function () {
    renderIdentity();
    render();
  });

  DC.start(function () {
    renderIdentity();
    return load();
  }, "admin");
})(window.DC);
