/* tomato 控制台前端：无依赖，直接操作 DOM。 */

"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  status: null,
  tab: "overview",
  token: localStorage.getItem("tomato-token") || "",
  allowGroups: [],
  fields: [],
  settingsBuilt: false,
  dirty: false,
  memoryGroup: null,
  emotionsLoaded: false,
  logAuto: true,
  chat: [],
  thinking: false,
  chatBusy: false,
};

const GEN_KEYS = ["GLOBAL_CTX", "REPEAT_PENALTY", "PREDICT", "TEMPERATURE", "TOP_K", "TOP_P", "REQUEST_TIMEOUT"];
const PROB_KEYS = [
  "DEFAULT_WATER_PROBABILITY",
  "SEND_IMAGE_PROBABILITY",
  "REPLY_PROBABILITY",
  "NORMAL_USER_MAX_WATER_PROBABILITY",
];

const ROLE_LABEL = { user: "用户", assistant: "猫娘", system: "系统" };

/* ------------------------------- 工具 ------------------------------- */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function fmtUptime(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days} 天 ${hours} 小时`;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分钟`;
  return `${seconds} 秒`;
}

function fmtPct(value) {
  const percent = Number(value || 0) * 100;
  return `${Number(percent.toFixed(2))}%`;
}

function debounce(fn, wait = 150) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

let toastTimer = null;
function toast(message, type = "info") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.className = "toast"), type === "error" ? 5200 : 2600);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers["X-WebUI-Token"] = state.token;

  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) },
  });

  if (response.status === 403) {
    const entered = prompt("远程访问需要令牌（环境变量 WEBUI_TOKEN）：", state.token || "");
    if (entered !== null && entered.trim()) {
      state.token = entered.trim();
      localStorage.setItem("tomato-token", state.token);
      return api(path, options);
    }
    throw new Error("没有访问权限喵");
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return data;
}

/* ------------------------------- 顶栏与轮询 ------------------------------- */

function renderHeader() {
  const status = state.status;
  if (!status) return;

  const pill = $("#pill-status");
  pill.className = `pill ${status.online ? "online" : "offline"}`;
  pill.textContent = status.online
    ? `在线 · ${status.self_ids.join(", ")}`
    : "离线（等待 QQ 框架连接）";

  const model = status.model;
  $("#pill-model").textContent = model.model
    ? `${model.label} · ${model.model}`
    : `${model.label} · 无模型`;
  $("#pill-uptime").textContent = `已运行 ${fmtUptime(status.uptime_seconds)}`;
}

function renderActive() {
  if (!state.status) return;
  if (state.tab === "overview") renderOverview();
  else if (state.tab === "groups") {
    if (document.activeElement && document.activeElement.type === "range") return;
    renderGroups();
  } else if (state.tab === "memory") syncMemoryGroups();
}

async function loadStatus() {
  try {
    state.status = await api("/status");
    renderHeader();
    renderActive();
  } catch (err) {
    const pill = $("#pill-status");
    pill.className = "pill offline";
    pill.textContent = "连接失败";
    if (state.tab !== "logs") console.warn(err);
  }
}

/* ------------------------------- 概览 ------------------------------- */

function renderOverview() {
  const status = state.status;
  const stats = status.stats;
  const model = status.model;

  $("#stat-online").textContent = status.online ? "在线" : "离线";
  $("#stat-selfids").textContent = status.online
    ? `机器人 QQ：${status.self_ids.join("、")}`
    : "启动 QQ 框架并连上反向 WS 后显示";
  $("#stat-model").textContent = model.model || "未设置";
  $("#stat-source").textContent = `${model.label}（${model.kind}）`;
  $("#stat-groups").textContent = `${stats.group_count} 个群`;
  $("#stat-water").textContent = `${stats.water_enabled} 个群在自动水群`;
  $("#stat-memory").textContent = `${stats.record_total} 条`;
  $("#stat-memory-sub").textContent = `每群上限 ${stats.max_memory} 条 · 表情包 ${stats.emotion_total} 张`;

  const activeSource = (model.sources || []).find((item) => item.active) || {};
  const modelIndex = model.models.indexOf(model.model);
  const options = model.models
    .map((name, index) => `<option value="${index}" ${index === modelIndex ? "selected" : ""}>${index + 1}. ${esc(name)}</option>`)
    .join("");

  $("#model-detail").innerHTML = `
    <div class="model-name">${esc(model.model || "未选择模型")}</div>
    <div class="model-meta">
      <span class="tag">${esc(model.label)}</span>
      <span class="tag muted">${esc(model.kind)}</span>
      <span class="tag muted">${esc(activeSource.url || "")}</span>
    </div>
    <div class="btn-row" style="margin-top:14px">
      <select id="model-select" style="max-width:320px">${options || '<option>没有模型，先同步</option>'}</select>
      <button id="btn-model-apply" class="btn small">切换模型</button>
      <button id="btn-model-sync" class="btn small ghost">同步列表</button>
    </div>
    <pre class="describe">${esc(model.describe || "")}</pre>
  `;

  $("#btn-model-apply").addEventListener("click", async () => {
    const index = Number($("#model-select").value);
    try {
      await api("/models/switch", { method: "POST", body: JSON.stringify({ index }) });
      toast("已切换模型喵", "ok");
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  $("#btn-model-sync").addEventListener("click", syncModels);

  const sources = model.sources || [];
  $("#source-count").textContent = `${sources.length} 个`;
  $("#source-list").innerHTML = sources
    .map(
      (source) => `
      <div class="source-row">
        <div class="source-main">
          <div class="source-name">${esc(source.label)} ${source.active ? '<span class="tag ok">当前</span>' : ""}</div>
          <div class="source-url">${esc(source.url)} · ${esc(source.kind)} · ${source.models.length} 个模型</div>
        </div>
        <button class="btn small ghost" data-source="${esc(source.name)}" ${source.active ? "disabled" : ""}>切换</button>
      </div>`
    )
    .join("");

  $$("#source-list [data-source]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await api("/models/switch", {
          method: "POST",
          body: JSON.stringify({ source: button.dataset.source }),
        });
        toast("已切换模型源喵", "ok");
        await loadStatus();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  });

  if (!state.emotionsLoaded) renderEmotions(status.emotions.counts);
}

function renderEmotions(counts) {
  const grid = $("#emotion-grid");
  const entries = Object.entries(counts || {});
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  $("#emotion-total").textContent = `共 ${total} 张`;
  const token = state.token ? `?token=${encodeURIComponent(state.token)}` : "";
  grid.innerHTML = entries
    .map(
      ([name, count]) => `
      <a class="emotion" href="/api/emotions/${esc(name)}${token}" target="_blank" rel="noreferrer" title="${esc(name)} · ${count} 张">
        <img src="/api/emotions/${esc(name)}${token}" alt="${esc(name)}" loading="lazy"
             onerror="this.style.display='none'">
        <span><em>${esc(name)}</em><b>${count}</b></span>
      </a>`
    )
    .join("");
  state.emotionsLoaded = true;
}

async function syncModels() {
  try {
    const result = await api("/models/sync", { method: "POST", body: JSON.stringify({}) });
    toast(`同步到 ${result.models.length} 个模型喵`, "ok");
    await loadStatus();
    if (state.tab === "settings" && state.settingsBuilt) buildSettings(true);
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ------------------------------- 群聊 ------------------------------- */

function renderGroups() {
  const groups = state.status.groups || [];
  $("#group-rows").innerHTML = groups
    .map(
      (group) => `
      <tr data-gid="${esc(group.group_id)}">
        <td><span class="group-id">${esc(group.group_id)}</span></td>
        <td>
          <label class="switch">
            <input type="checkbox" data-act="enabled" ${group.enabled ? "checked" : ""}>
            <span></span>
          </label>
        </td>
        <td>
          <label class="switch">
            <input type="checkbox" data-act="water" ${group.water_enabled ? "checked" : ""}>
            <span></span>
          </label>
        </td>
        <td>
          <input type="range" min="0" max="100" step="0.5"
                 value="${(group.water_probability * 100).toFixed(1)}" data-act="prob">
          <span class="muted prob-label">${fmtPct(group.water_probability)}</span>
        </td>
        <td class="muted">${group.records} / ${group.max_records}</td>
        <td>
          <div class="btn-row">
            <button class="btn small ghost" data-act="memory">记忆</button>
            <button class="btn small danger" data-act="clear">清空</button>
            <button class="btn small danger" data-act="remove">移除</button>
          </div>
        </td>
      </tr>`
    )
    .join("") || '<tr><td colspan="6" class="muted">还没有群，先在上面添加一个吧喵。</td></tr>';
}

async function patchGroup(groupId, payload) {
  try {
    await api(`/groups/${groupId}`, { method: "PATCH", body: JSON.stringify(payload) });
    await loadStatus();
  } catch (err) {
    toast(err.message, "error");
  }
}

function bindGroupEvents() {
  const container = $("#group-rows");

  container.addEventListener("change", async (event) => {
    const target = event.target;
    const row = target.closest("tr[data-gid]");
    if (!row) return;
    const groupId = row.dataset.gid;

    if (target.dataset.act === "enabled") {
      await patchGroup(groupId, { enabled: target.checked });
    } else if (target.dataset.act === "water") {
      await patchGroup(groupId, { water_enabled: target.checked });
    } else if (target.dataset.act === "prob") {
      row.querySelector(".prob-label").textContent = fmtPct(Number(target.value) / 100);
      await patchGroup(groupId, { water_probability: Number(target.value) / 100 });
    }
  });

  container.addEventListener("input", (event) => {
    const target = event.target;
    if (target.dataset.act !== "prob") return;
    const row = target.closest("tr[data-gid]");
    if (row) row.querySelector(".prob-label").textContent = fmtPct(Number(target.value) / 100);
  });

  container.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const row = button.closest("tr[data-gid]");
    if (!row) return;
    const groupId = row.dataset.gid;
    const action = button.dataset.act;

    if (action === "memory") {
      state.memoryGroup = groupId;
      switchTab("memory");
    } else if (action === "clear") {
      if (!confirm(`清空群 ${groupId} 的全部记忆？`)) return;
      try {
        const result = await api(`/groups/${groupId}/memory`, { method: "DELETE" });
        toast(`已清空 ${result.removed} 条记忆喵`, "ok");
        await loadStatus();
      } catch (err) {
        toast(err.message, "error");
      }
    } else if (action === "remove") {
      if (!confirm(`把群 ${groupId} 从白名单移除？移除后机器人不会再响应这个群。`)) return;
      try {
        await api(`/groups/${groupId}`, { method: "DELETE" });
        toast("已移除喵", "ok");
        await loadStatus();
      } catch (err) {
        toast(err.message, "error");
      }
    }
  });
}

async function addGroup(event) {
  event.preventDefault();
  const input = $("#new-group");
  const groupId = input.value.trim();
  if (!groupId) return;
  try {
    await api("/groups", { method: "POST", body: JSON.stringify({ group_id: groupId }) });
    input.value = "";
    toast("已添加群喵", "ok");
    await loadStatus();
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ------------------------------- 设置 ------------------------------- */

function typeOptions(current) {
  const options = [
    ["openai", "OpenAI 兼容（LM Studio / DeepSeek / OneAPI…）"],
    ["ollama", "ollama"],
  ];
  return options
    .map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`)
    .join("");
}

function sourceCardHtml(name, spec) {
  const extra = spec.extra_body ? JSON.stringify(spec.extra_body, null, 2) : "";
  return `
    <div class="source-card">
      <div class="source-head">
        <input class="src-name" value="${esc(name)}" placeholder="源名称（英文）" maxlength="32">
        <span class="muted">源名称</span>
        <button type="button" class="btn small danger src-remove">删除</button>
      </div>
      <div class="field-grid">
        <div><label>类型</label><select class="src-type">${typeOptions(spec.type || "ollama")}</select></div>
        <div><label>显示名</label><input class="src-label" value="${esc(spec.label || name)}"></div>
        <div class="span2"><label>地址</label><input class="src-url" value="${esc(spec.url || "")}" placeholder="http://127.0.0.1:1234"></div>
        <div class="span2"><label>API Key（本地服务留空）</label><input class="src-key" type="password" value="${esc(spec.api_key || "")}" autocomplete="off"></div>
        <div class="span2"><label>模型列表（每行一个）</label><textarea class="src-models" rows="3">${esc((spec.model || []).join("\n"))}</textarea></div>
        <div class="span2"><label>extra_body（JSON，可留空）</label><textarea class="src-extra" rows="2">${esc(extra)}</textarea></div>
      </div>
    </div>`;
}

function collectSources() {
  const models = {};
  $$("#source-editor .source-card").forEach((card) => {
    const name = $(".src-name", card).value.trim();
    if (!name) throw new Error("模型源名称不能为空");
    if (models[name]) throw new Error(`模型源名称重复：${name}`);

    const spec = {
      type: $(".src-type", card).value,
      url: $(".src-url", card).value.trim(),
      model: $(".src-models", card).value.split("\n").map((item) => item.trim()).filter(Boolean),
    };
    const label = $(".src-label", card).value.trim();
    if (label) spec.label = label;
    const apiKey = $(".src-key", card).value.trim();
    if (apiKey) spec.api_key = apiKey;

    const extraText = $(".src-extra", card).value.trim();
    if (extraText) {
      try {
        spec.extra_body = JSON.parse(extraText);
      } catch (err) {
        throw new Error(`模型源 ${name} 的 extra_body 不是合法 JSON`);
      }
    }
    models[name] = spec;
  });
  if (!Object.keys(models).length) throw new Error("至少保留一个模型源");
  return models;
}

function refreshSourceSelect(models, selected) {
  const select = $("#f-DEFAULT_SOURCE");
  const names = Object.keys(models);
  if (!names.includes(selected)) selected = names[0];
  select.innerHTML = names.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("");
  select.value = selected;
}

function renderAllowGroups() {
  const container = $("#allow-groups");
  container.innerHTML = state.allowGroups
    .map(
      (groupId) => `
      <span class="tag" data-gid="${esc(groupId)}">${esc(groupId)}
        <button type="button" title="移除">×</button>
      </span>`
    )
    .join("") || '<span class="muted">还没有群，机器人不会响应任何消息</span>';

  $$("#allow-groups button").forEach((button) => {
    button.addEventListener("click", () => {
      const gid = button.closest(".tag").dataset.gid;
      state.allowGroups = state.allowGroups.filter((item) => item !== gid);
      renderAllowGroups();
      markDirty();
    });
  });
}

function addAllowGroup() {
  const input = $("#group-chip");
  const groupId = input.value.trim();
  if (!groupId) return;
  if (!/^\d{3,12}$/.test(groupId)) {
    toast("群号必须是 3-12 位数字喵", "error");
    return;
  }
  if (!state.allowGroups.includes(groupId)) {
    state.allowGroups.push(groupId);
    state.allowGroups.sort();
  }
  input.value = "";
  renderAllowGroups();
  markDirty();
}

function scalarFieldHtml(field, value) {
  const id = `f-${field.key}`;
  if (field.kind === "prob") {
    const percent = Number((Number(value) * 100).toFixed(2));
    return `<div><label for="${id}">${esc(field.label)}（%）</label>
      <input id="${id}" type="number" min="0" max="100" step="0.5" value="${percent}"></div>`;
  }
  if (field.kind === "int" || field.kind === "float") {
    const step = field.step ?? (field.kind === "int" ? "1" : "0.01");
    const min = field.min ?? "";
    const max = field.max ?? "";
    return `<div><label for="${id}">${esc(field.label)}</label>
      <input id="${id}" type="number" min="${min}" max="${max}" step="${step}" value="${esc(value)}"></div>`;
  }
  return "";
}

function buildSettings(force = false) {
  if (state.settingsBuilt && !force) return;
  const config = state.status.config;
  const values = config.values;
  const fields = config.fields;
  state.fields = fields;
  state.allowGroups = (values.ALLOW_GROUPS || []).slice();

  const byKey = Object.fromEntries(fields.map((field) => [field.key, field]));
  $("#gen-fields").innerHTML = GEN_KEYS.map((key) => scalarFieldHtml(byKey[key], values[key])).join("");
  $("#prob-fields").innerHTML = PROB_KEYS.map((key) => scalarFieldHtml(byKey[key], values[key])).join("");
  $("#f-AI_NAME").value = values.AI_NAME || "";
  $("#f-MAX_MEMORY").value = values.MAX_MEMORY;
  $("#f-MERGE_MAX_LEN").value = values.MERGE_MAX_LEN;
  $("#f-DEFAULT_MODEL_INDEX").value = values.DEFAULT_MODEL_INDEX;

  $("#source-editor").innerHTML = Object.entries(values.MODELS)
    .map(([name, spec]) => sourceCardHtml(name, spec))
    .join("");
  refreshSourceSelect(values.MODELS, values.DEFAULT_SOURCE);

  renderAllowGroups();
  state.settingsBuilt = true;
  setDirty(false);
  bindSettingsEvents();
  updateRestartHint(config.restart_required || []);
}

function bindSettingsEvents() {
  const panel = $("#panel-settings");
  panel.addEventListener("input", (event) => {
    if (event.target.closest("#source-editor, #gen-fields, #prob-fields, #f-AI_NAME, #f-MAX_MEMORY, #f-MERGE_MAX_LEN, #f-DEFAULT_MODEL_INDEX")) {
      markDirty();
    }
  });
  panel.addEventListener("change", (event) => {
    if (event.target.closest("#f-DEFAULT_SOURCE")) markDirty();
  });

  $("#source-editor").addEventListener("click", (event) => {
    const button = event.target.closest(".src-remove");
    if (!button) return;
    const cards = $$("#source-editor .source-card");
    if (cards.length <= 1) {
      toast("至少保留一个模型源喵", "error");
      return;
    }
    button.closest(".source-card").remove();
    const models = {};
    $$("#source-editor .source-card").forEach((card) => {
      models[$(".src-name", card).value.trim() || "source"] = {};
    });
    refreshSourceSelect(models, $("#f-DEFAULT_SOURCE").value);
    markDirty();
  });

  $("#new-group"); // noop 保持引用
}

function markDirty() {
  setDirty(true);
}

function setDirty(dirty) {
  state.dirty = dirty;
  const status = $("#save-status");
  if (dirty) {
    status.textContent = "有未保存的修改";
  } else {
    status.textContent = "修改后点右侧保存，立即生效（标注「需重启」的除外）。";
  }
}

function updateRestartHint(keys) {
  const status = $("#save-status");
  if (keys && keys.length) {
    status.textContent = `以下改动需要重启进程才生效：${keys.join("、")}`;
  }
}

function collectSettings() {
  const values = {};
  state.fields.forEach((field) => {
    const element = document.getElementById(`f-${field.key}`);
    if (!element) return;
    const raw = element.value;
    if (field.kind === "prob") values[field.key] = Number(raw) / 100;
    else if (field.kind === "int") values[field.key] = parseInt(raw, 10);
    else if (field.kind === "float") values[field.key] = parseFloat(raw);
    else values[field.key] = raw;
  });
  values.MODELS = collectSources();
  values.ALLOW_GROUPS = state.allowGroups.slice();
  values.DEFAULT_SOURCE = $("#f-DEFAULT_SOURCE").value;
  values.DEFAULT_MODEL_INDEX = parseInt($("#f-DEFAULT_MODEL_INDEX").value || "0", 10);
  return values;
}

async function saveSettings() {
  const button = $("#btn-save");
  button.disabled = true;
  $("#save-status").textContent = "保存中…";
  try {
    const result = await api("/config", { method: "POST", body: JSON.stringify({ values: collectSettings() }) });
    state.status.config.values = result.values;
    state.settingsBuilt = false;
    setDirty(false);
    const restart = result.restart_required || [];
    toast(restart.length ? `已保存，重启后生效：${restart.join("、")}` : "已保存并应用喵", "ok");
    await loadStatus();
    buildSettings(true);
  } catch (err) {
    toast(err.message, "error");
    setDirty(true);
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------- 记忆 ------------------------------- */

function memoryGroupOptions() {
  const groups = (state.status?.groups || []).map((group) => group.group_id);
  if (!groups.includes("webui-test")) groups.push("webui-test");
  return groups;
}

function syncMemoryGroups() {
  const select = $("#memory-group");
  const current = state.memoryGroup || select.value;
  const groups = memoryGroupOptions();
  select.innerHTML = groups
    .map((groupId) => `<option value="${groupId}">${groupId === "webui-test" ? "网页试聊（webui-test）" : `群 ${groupId}`}</option>`)
    .join("");
  if (groups.includes(current)) select.value = current;
  state.memoryGroup = select.value;
}

async function loadMemory() {
  const groupId = state.memoryGroup || $("#memory-group").value || "webui-test";
  state.memoryGroup = groupId;
  const container = $("#memory-list");
  try {
    const data = await api(`/groups/${groupId}/memory`);
    if (!data.records.length) {
      container.innerHTML = '<p class="muted">这个群还没有记忆喵。</p>';
      return;
    }
    container.innerHTML = data.records
      .map(
        (record) => `
        <div class="msg ${esc(record.role)}">
          <div class="msg-head">
            <span class="msg-role">${esc(ROLE_LABEL[record.role] || record.role)}</span>
            ${record.mark === "persistence" ? '<span class="tag ok">持久化</span>' : ""}
            <span class="muted" style="font-size:11px">${record.chars} 字</span>
          </div>
          <div class="msg-body">${esc(record.content)}</div>
        </div>`
      )
      .join("");
  } catch (err) {
    container.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

/* ------------------------------- 试聊 ------------------------------- */

function renderChat() {
  const log = $("#chat-log");
  if (!state.chat.length && !state.thinking) {
    log.innerHTML = '<div class="chat-empty">这是独立的测试会话，不会影响 QQ 群里的记忆。<br>发一句话试试猫娘的语气吧喵。</div>';
    return;
  }
  const bubbles = state.chat
    .map((item) => {
      const cls = item.role === "user" ? "user" : item.role === "error" ? "error" : "bot";
      return `<div class="bubble ${cls}">${esc(item.text)}${
        item.meta ? `<span class="bubble-meta">${esc(item.meta)}</span>` : ""
      }</div>`;
    })
    .join("");
  const thinking = state.thinking ? '<div class="bubble bot thinking">猫娘正在思考…喵</div>' : "";
  log.innerHTML = bubbles + thinking;
  log.scrollTop = log.scrollHeight;
}

async function sendChat(text) {
  if (state.chatBusy) return;
  state.chatBusy = true;
  state.chat.push({ role: "user", text });
  state.thinking = true;
  renderChat();
  try {
    const result = await api("/chat", { method: "POST", body: JSON.stringify({ text }) });
    const meta = [
      result.emotions && result.emotions.length ? `情绪：${result.emotions.join("、")}` : "",
      result.has_image ? "带表情包" : "",
      `${result.records} 条上下文`,
    ]
      .filter(Boolean)
      .join(" · ");
    state.chat.push({ role: "bot", text: result.reply || "喵？", meta });
  } catch (err) {
    state.chat.push({ role: "error", text: err.message });
  } finally {
    state.thinking = false;
    state.chatBusy = false;
    renderChat();
    loadStatus();
  }
}

async function resetChat() {
  try {
    const result = await api("/chat/reset", { method: "POST", body: JSON.stringify({}) });
    state.chat = [];
    state.thinking = false;
    renderChat();
    toast(`已重置试聊上下文（清除 ${result.removed} 条）喵`, "ok");
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ------------------------------- 日志 ------------------------------- */

async function loadLogs() {
  try {
    const data = await api("/logs?lines=300");
    $("#log-path").textContent = data.path ? `日志文件：${data.path}` : "";
    if (!data.lines.length) {
      $("#log-view").textContent = data.hint || "暂无日志";
      return;
    }
    const view = $("#log-view");
    view.innerHTML = data.lines
      .map((line) => {
        const level = /(ERROR|CRITICAL|Traceback)/.test(line)
          ? "lv-error"
          : /WARNING/.test(line)
          ? "lv-warning"
          : /SUCCESS/.test(line)
          ? "lv-success"
          : "";
        return `<span class="${level}">${esc(line)}</span>`;
      })
      .join("\n");
    if ($("#log-auto").checked) view.scrollTop = view.scrollHeight;
  } catch (err) {
    $("#log-view").textContent = err.message;
  }
}

/* ------------------------------- 标签页 ------------------------------- */

function switchTab(name) {
  state.tab = name;
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === `panel-${name}`));
  $("#save-bar").classList.toggle("show", name === "settings");

  if (name === "overview") renderOverview();
  if (name === "groups") renderGroups();
  if (name === "settings") {
    if (!state.settingsBuilt) buildSettings();
  }
  if (name === "memory") {
    syncMemoryGroups();
    loadMemory();
  }
  if (name === "chat") renderChat();
  if (name === "logs") loadLogs();
}

/* ------------------------------- 初始化 ------------------------------- */

function init() {
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
  $("#btn-refresh").addEventListener("click", async () => {
    await loadStatus();
    toast("已刷新喵");
  });

  $("#add-group-form").addEventListener("submit", addGroup);
  bindGroupEvents();

  $("#btn-add-source").addEventListener("click", () => {
    const editor = $("#source-editor");
    const name = `source${editor.children.length + 1}`;
    editor.insertAdjacentHTML("beforeend", sourceCardHtml(name, { type: "openai", url: "", model: [] }));
    const models = {};
    $$("#source-editor .source-card").forEach((card) => {
      models[$(".src-name", card).value.trim() || name] = {};
    });
    refreshSourceSelect(models, $("#f-DEFAULT_SOURCE").value);
    markDirty();
  });

  $("#btn-add-chip").addEventListener("click", addAllowGroup);
  $("#group-chip").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addAllowGroup();
    }
  });

  $("#btn-save").addEventListener("click", saveSettings);

  $("#memory-group").addEventListener("change", () => {
    state.memoryGroup = $("#memory-group").value;
    loadMemory();
  });
  $("#btn-memory-refresh").addEventListener("click", loadMemory);
  $("#btn-memory-clear").addEventListener("click", async () => {
    const groupId = state.memoryGroup || $("#memory-group").value;
    if (!confirm(`清空 ${groupId} 的全部记忆？`)) return;
    try {
      const result = await api(`/groups/${groupId}/memory`, { method: "DELETE" });
      toast(`已清空 ${result.removed} 条记忆喵`, "ok");
      loadMemory();
      loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendChat(text);
  });
  $("#btn-chat-reset").addEventListener("click", resetChat);

  $("#log-auto").addEventListener("change", (event) => {
    state.logAuto = event.target.checked;
  });
  $("#btn-log-refresh").addEventListener("click", loadLogs);

  loadStatus();
  setInterval(async () => {
    if (document.hidden) return;
    if (state.tab === "logs") {
      if ($("#log-auto").checked) loadLogs();
      return;
    }
    if (state.tab === "settings") {
      renderHeader();
      return;
    }
    await loadStatus();
  }, 4000);
}

document.addEventListener("DOMContentLoaded", init);
