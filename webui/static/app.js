/* tomato 控制台前端 —— 单文件、无依赖，状态和渲染都集中在这里。 */
(() => {
  "use strict";

  const WEBUI_TEST = "webui-test";
  const SORTED_TABS = ["overview", "memory", "chat"];

  const state = {
    status: null,
    view: "overview", // overview | group | settings | prompts | logs
    groupId: null,
    tab: "overview",
    search: "",
    fields: [],
    config: null,
    dirty: false,
    prompts: null,
    promptDirty: false,
    memory: [],
    memoryGroup: null,
    chat: [],
    chatBusy: false,
    logs: [],
    logAuto: true,
    booted: false,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));

  const num = (value) => Number(value || 0).toLocaleString("zh-CN");
  const pct = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;

  function uptime(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days) return `${days} 天 ${hours} 小时`;
    if (hours) return `${hours} 小时 ${minutes} 分`;
    if (minutes) return `${minutes} 分钟`;
    return `${total} 秒`;
  }

  function ago(iso) {
    const then = new Date(iso).getTime();
    if (!Number.isFinite(then)) return iso || "—";
    return uptime((Date.now() - then) / 1000) + "前";
  }

  /* ---------------------------------------------------------------- 网络 */

  function storedToken() {
    try {
      return localStorage.getItem("tomato-token") || "";
    } catch {
      return "";
    }
  }

  function saveToken(token) {
    try {
      if (token) localStorage.setItem("tomato-token", token);
      else localStorage.removeItem("tomato-token");
    } catch {}
  }

  function askToken() {
    return new Promise((resolve) => {
      const dialog = $("#token-dialog");
      const input = $("#token-input");
      const error = $("#token-error");
      input.value = storedToken();
      error.hidden = true;
      dialog.returnValue = "";
      const onClose = () => {
        dialog.removeEventListener("close", onClose);
        const token = dialog.returnValue === "ok" ? input.value.trim() : "";
        if (token) saveToken(token);
        resolve(token);
      };
      dialog.addEventListener("close", onClose);
      dialog.showModal();
      setTimeout(() => input.focus(), 30);
    });
  }

  async function api(path, options = {}, retried = false) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    const token = storedToken();
    if (token) headers["X-WebUI-Token"] = token;
    let response;
    try {
      response = await fetch(`/api${path}`, { ...options, headers });
    } catch (err) {
      throw new Error("连不上后台服务，确认程序还在运行喵");
    }
    if (response.status === 403 && !retried) {
      const entered = await askToken();
      if (entered) return api(path, options, true);
    }
    let data = null;
    try {
      data = await response.json();
    } catch {}
    if (!response.ok) {
      const detail = data && (data.detail || data.message);
      throw new Error(typeof detail === "string" ? detail : `请求失败（HTTP ${response.status}）`);
    }
    return data;
  }

  /* ---------------------------------------------------------------- 提示 */

  let toastTimer = 0;
  function toast(message, kind = "") {
    const box = $("#toast");
    box.textContent = message;
    box.className = `toast ${kind}`.trim();
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      box.hidden = true;
    }, kind === "error" ? 4200 : 2600);
  }

  function confirmAction(text, { title = "确认操作", okText = "确认" } = {}) {
    return new Promise((resolve) => {
      const dialog = $("#confirm-dialog");
      $("#confirm-title").textContent = title;
      $("#confirm-text").textContent = text;
      $("#confirm-ok").textContent = okText;
      dialog.returnValue = "";
      const onClose = () => {
        dialog.removeEventListener("close", onClose);
        resolve(dialog.returnValue === "ok");
      };
      dialog.addEventListener("close", onClose);
      dialog.showModal();
    });
  }

  /* ------------------------------------------------------------ 数据加载 */

  async function loadStatus(force = false) {
    try {
      const data = await api("/status");
      if (force) state.dirty = false; // 手动刷新：丢弃设置页未保存的编辑，回到服务端值
      state.status = data;
      state.fields = data.config.fields || [];
      if (!state.config || !state.dirty) state.config = data.config.values;
      const groups = data.groups || [];
      if (state.groupId && state.groupId !== WEBUI_TEST && !groups.some((g) => g.group_id === state.groupId)) {
        state.groupId = null;
        if (state.view === "group") state.view = "overview";
      }
      if (!state.groupId && groups.length) state.groupId = groups[0].group_id;
      const first = !state.booted;
      state.booted = true;
      if (!first && !force && refreshLive()) return true;
      render();
      return true;
    } catch (err) {
      renderOffline(err);
      return false;
    }
  }

  function renderOffline(err) {
    state.booted = false;
    $("#conn-dot").className = "dot error";
    $("#conn-text").textContent = "连接失败";
    toast(err.message || "连接失败", "error");
  }

  function currentGroup() {
    if (!state.status) return null;
    if (state.groupId === WEBUI_TEST) {
      return { group_id: WEBUI_TEST, enabled: true, water_enabled: false, water_probability: 0, records: state.memory.length, max_records: 12 };
    }
    return (state.status.groups || []).find((g) => g.group_id === state.groupId) || null;
  }

  /* ---------------------------------------------------------------- 渲染 */

  function render() {
    if (!state.status) return;
    const online = Boolean(state.status.online);
    $("#conn-dot").className = `dot ${online ? "live" : "warn"}`;
    $("#conn-text").textContent = online ? `在线 · ${(state.status.self_ids || []).join(" / ") || "已连接"}` : "离线（等待 QQ 连接）";
    renderSidebar();
    renderHeader();
    renderTabs();
    const skipSettings = state.view === "settings" && state.dirty;
    const skipPrompts = state.view === "prompts" && state.promptDirty;
    const skipChat = state.view === "group" && state.tab === "chat";
    if (!skipSettings && !skipPrompts && !skipChat) renderView();
    if (skipSettings) {
      // 设置页只更新侧栏与标题，避免打断正在编辑的表单
      syncSaveBar();
    }
    if (skipPrompts) syncPromptBar();
    if (state.view === "group" && state.tab === "chat") updateChatHeaderOnly();
  }

  function renderSidebar() {
    const groups = state.status?.groups || [];
    $("#group-count").textContent = groups.length;
    const query = state.search.trim();
    const visible = groups.filter((group) => !query || group.group_id.includes(query));

    const entries = [];
    entries.push(`
      <button type="button" class="group ${state.groupId === WEBUI_TEST && state.view === "group" ? "active" : ""}" data-group="${WEBUI_TEST}">
        <span class="group-avatar">💬</span>
        <span>
          <span class="group-name">网页试聊</span>
          <span class="group-sub">不经过 QQ 的本地对话</span>
        </span>
      </button>`);

    for (const group of visible) {
      const active = state.view === "group" && state.groupId === group.group_id;
      const dot = group.water_enabled ? "live" : "warn";
      entries.push(`
        <button type="button" class="group ${active ? "active" : ""}" data-group="${group.group_id}">
          <span class="group-avatar">${esc(group.group_id.slice(-2))}<span class="dot ${dot}"></span></span>
          <span>
            <span class="group-name">群 ${esc(group.group_id)}</span>
            <span class="group-sub">记忆 ${num(group.records)}/${num(group.max_records)} · 水群 ${pct(group.water_probability)}</span>
          </span>
        </button>`);
    }

    if (!visible.length && query) {
      entries.push(`<div class="empty" style="grid-column:1/-1;padding:24px 10px">没有匹配的群号</div>`);
    }
    const html = entries.join("");
    if ($("#groups").innerHTML !== html) $("#groups").innerHTML = html;
    $("#footer-model").textContent = state.status.model?.model || "未选择模型";
    $("#footer-model").title = `${state.status.model?.label || ""} · ${state.status.model?.model || ""}`;
  }

  function statusPill(online) {
    return online
      ? `<span class="status-pill ok">在线</span>`
      : `<span class="status-pill bad">离线（等待 QQ 连接）</span>`;
  }

  function renderHeader() {
    const status = state.status;
    const model = status?.model;
    let title = "所有群总览";
    let meta = [];
    let actions = [`<button type="button" data-action="refresh">刷新</button>`];

    if (state.view === "overview") {
      meta = [
        statusPill(status.online),
        `<span>${status.stats.group_count} 个群</span>`,
        `<span>记忆 ${num(status.stats.record_total)} 条</span>`,
        `<span class="mono">${esc(model?.label || "—")} · ${esc(model?.model || "未选择模型")}</span>`,
      ];
    } else if (state.view === "group") {
      const group = currentGroup();
      const test = state.groupId === WEBUI_TEST;
      title = test ? "网页试聊" : `群 ${state.groupId}`;
      meta = test
        ? [`<span class="status-pill ok">本地对话</span>`, `<span>模型 ${esc(model?.model || "—")}</span>`]
        : [
            group?.enabled ? `<span class="status-pill ok">响应消息</span>` : `<span class="status-pill warn">不响应</span>`,
            group?.water_enabled ? `<span class="status-pill ok">自动聊天</span>` : `<span class="status-pill">自动聊天关闭</span>`,
            `<span>记忆 ${num(group?.records)}/${num(group?.max_records)}</span>`,
            `<span>水群 ${pct(group?.water_probability)}</span>`,
          ];
      actions = [
        `<button type="button" data-action="refresh">刷新</button>`,
        `<button type="button" data-action="open-chat">试聊</button>`,
      ];
      if (!test) {
        actions.push(`<button type="button" class="danger-text" data-action="remove-group">移除白名单</button>`);
      }
    } else if (state.view === "settings") {
      title = "模型与设置";
      meta = [
        `<span class="mono">${esc(model?.label || "—")}</span>`,
        `<span class="mono">${esc(model?.model || "未选择模型")}</span>`,
        status.restart_required?.length
          ? `<span class="status-pill warn">${state.status.restart_required.length} 项待重启</span>`
          : `<span class="status-pill ok">已生效</span>`,
      ];
      actions = [`<button type="button" data-action="refresh">刷新</button>`];
    } else if (state.view === "prompts") {
      title = "人格与提示词";
      meta = [
        `<span class="status-pill ok">改完立即生效</span>`,
        `<span class="mono">webui_prompts.json</span>`,
      ];
      actions = [`<button type="button" data-action="reload-prompts">重新读取</button>`];
    } else if (state.view === "logs") {
      title = "运行日志";
      meta = [
        statusPill(status.online),
        `<span>启动于 ${esc(status.started_at?.replace("T", " "))}</span>`,
        `<span>已运行 ${uptime(status.uptime_seconds)}</span>`,
      ];
      actions = [
        `<button type="button" data-action="refresh-logs">刷新</button>`,
        `<label class="hint" style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="logs-auto" ${state.logAuto ? "checked" : ""}> 自动刷新</label>`,
      ];
    }

    const metaHtml = meta.join("");
    const actionsHtml = actions.join("");
    if ($("#title").textContent !== title) $("#title").textContent = title;
    if ($("#meta").innerHTML !== metaHtml) $("#meta").innerHTML = metaHtml;
    if ($("#actions").innerHTML !== actionsHtml) $("#actions").innerHTML = actionsHtml;
  }

  function renderTabs() {
    const tabs = $("#tabs");
    if (state.view !== "group") {
      tabs.hidden = true;
      tabs.innerHTML = "";
      return;
    }
    tabs.hidden = false;
    const labels = { overview: "概览", memory: "记忆", chat: "试聊" };
    tabs.innerHTML = SORTED_TABS.map(
      (tab) => `<button type="button" data-tab="${tab}" class="${state.tab === tab ? "active" : ""}">${labels[tab]}</button>`,
    ).join("");
  }

  function renderView() {
    const view = $("#view");
    if (state.view === "overview") view.innerHTML = viewOverview();
    else if (state.view === "group") {
      if (state.tab === "overview") view.innerHTML = viewGroup();
      else if (state.tab === "memory") {
        view.innerHTML = viewMemory();
        loadMemory(state.groupId).then((ok) => {
          if (ok && state.view === "group" && state.tab === "memory") {
            $("#memory-body").innerHTML = memoryBody();
          }
        });
      } else if (state.tab === "chat") {
        view.innerHTML = viewChat();
        renderChatMessages();
      }
    } else if (state.view === "settings") view.innerHTML = viewSettings();
    else if (state.view === "prompts") view.innerHTML = viewPrompts();
    else if (state.view === "logs") {
      view.innerHTML = viewLogs();
      loadLogs();
    }
  }

  /* -------------------------------------------------- 增量刷新（不重绘） */

  function liveHtml() {
    if (state.view === "overview") return viewOverview();
    if (state.view === "group" && state.tab === "overview") return viewGroup();
    return null;
  }

  // 只替换内容真的变了的块，其余 DOM 原样保留（选中文字、滚动位置都不动）
  function syncView() {
    const html = liveHtml();
    if (!html) return false;
    const probe = document.createElement("div");
    probe.innerHTML = html;
    const fresh = probe.querySelectorAll("[data-block]");
    if (!fresh.length) return false;
    for (const node of fresh) {
      if (!document.querySelector(`#view [data-block="${node.dataset.block}"]`)) return false;
    }
    for (const node of fresh) {
      const current = document.querySelector(`#view [data-block="${node.dataset.block}"]`);
      if (current.innerHTML !== node.innerHTML) current.innerHTML = node.innerHTML;
    }
    return true;
  }

  // 轮询到达后的刷新：状态页增量、编辑页/试聊/记忆只更新外壳
  function refreshLive() {
    if (!state.booted) return false;
    renderSidebar();
    renderHeader();
    if (state.view === "settings" || state.view === "prompts") return true; // 纯手动刷新
    if (state.view === "overview" || (state.view === "group" && state.tab === "overview")) return syncView();
    if (state.view === "group" && state.tab === "chat") updateChatHeaderOnly();
    return true; // 记忆 / 日志 / 关于：内容由手动刷新或各自的定时器负责
  }

  /* -------------------------------------------------------- 视图：总览 */

  function vitals(status) {
    return `
      <div class="vitals">
        <div class="vital">
          <div class="vital-label">运行状态</div>
          <div class="vital-value ${status.online ? "ok" : "bad"}">${status.online ? "在线" : "离线"}</div>
          <div class="vital-note">${status.online ? `QQ ${status.self_ids.map(esc).join(" / ")}` : "等待 QQ 框架连上反向 WS"}</div>
        </div>
        <div class="vital">
          <div class="vital-label">当前模型</div>
          <div class="vital-value">${esc(status.model?.model || "未选择")}</div>
          <div class="vital-note">${esc(status.model?.label || "—")} · ${esc(status.model?.kind || "—")}</div>
        </div>
        <div class="vital">
          <div class="vital-label">群聊</div>
          <div class="vital-value">${num(status.stats.group_count)}</div>
          <div class="vital-note">${num(status.stats.water_enabled)} 个群在自动聊天</div>
        </div>
        <div class="vital">
          <div class="vital-label">记忆</div>
          <div class="vital-value">${num(status.stats.record_total)}</div>
          <div class="vital-note">每群上限 ${num(status.stats.max_memory)} 条 · 表情包 ${num(status.stats.emotion_total)} 张</div>
        </div>
      </div>`;
  }

  function modelCard(status) {
    const model = status.model;
    const sources = model?.sources || [];
    const models = model?.models || [];
    const modelList = models.length
      ? `${esc(models.slice(0, 3).join(" · "))}${models.length > 3 ? ` 等 ${models.length} 个` : ""}`
      : "点右侧同步";
    return `
      <div class="status-card">
        <h3>当前模型</h3>
        <div class="status-row"><span class="label">模型源</span><span class="value">${esc(model?.label || "—")} <span class="badge">${esc(model?.kind || "—")}</span></span></div>
        <div class="status-row"><span class="label">模型</span><span class="value mono">${esc(model?.model || "未选择")}</span></div>
        <div class="status-row"><span class="label">可选模型</span><span class="value">${modelList}</span></div>
        <div class="status-row"><span class="label">操作</span><span class="value" style="display:flex;gap:6px;justify-content:flex-end">
          <button type="button" data-action="sync-models" style="font-size:12px;padding:3px 9px">同步模型</button>
          <button type="button" data-action="open-settings" style="font-size:12px;padding:3px 9px">修改源</button>
        </span></div>
        <div class="source-list">
          ${sources
            .map(
              (source) => `
            <div class="source-row">
              <span class="source-name">${esc(source.label)}</span>
              <span class="badge ${source.active ? "accent" : ""}">${source.active ? "使用中" : esc(source.kind)}</span>
              <span class="source-url mono">${esc(source.url)}</span>
              ${source.active ? "" : `<button type="button" data-action="switch-source" data-source="${esc(source.name)}" style="font-size:12px;padding:3px 9px">切换</button>`}
            </div>`,
            )
            .join("")}
        </div>
      </div>`;
  }

  function emotionCard(status) {
    const counts = status.emotions?.counts || {};
    const names = Object.keys(counts);
    return `
      <div class="status-card">
        <h3>表情包 <span class="hint" style="font-weight:400">共 ${num(status.stats.emotion_total)} 张</span></h3>
        <div class="stat-chips chips-left">
          ${names
            .map((name) => `<span>${esc(name)} ${num(counts[name])}</span>`)
            .join("")}
        </div>
        <div class="emotion-mini">
          ${names
            .slice(0, 6)
            .map((name) => `<img src="/api/emotions/${encodeURIComponent(name)}" alt="${esc(name)}" title="${esc(name)}" loading="lazy">`)
            .join("")}
        </div>
      </div>`;
  }

  function viewOverview() {
    const status = state.status;
    const groups = status.groups || [];
    return `
      <div class="page">
        <div class="live-block" data-block="vitals">${vitals(status)}</div>
        <div class="page-title">
          <div><h2>群聊</h2><p>每个群的对话开关、自动聊天概率和记忆都在这里，点开可以细调。</p></div>
        </div>
        <div class="live-block" data-block="groups">
          <div class="overview-grid">
          ${
            groups.length
              ? groups
                  .map(
                    (group) => `
            <article class="overview-card">
              <header>
                <span class="group-avatar">${esc(group.group_id.slice(-2))}</span>
                <h3>群 ${esc(group.group_id)}</h3>
                <span class="badge ${group.enabled ? "ok" : "warn"}">${group.enabled ? "响应中" : "已关闭"}</span>
              </header>
              <dl>
                <dt>记忆</dt><dd>${num(group.records)} / ${num(group.max_records)}</dd>
                <dt>自动聊天</dt><dd>${group.water_enabled ? "开启" : "关闭"}</dd>
                <dt>水群概率</dt><dd>${pct(group.water_probability)}</dd>
              </dl>
              <footer>
                <button type="button" data-group="${group.group_id}">打开</button>
                <button type="button" data-action="toggle-enabled" data-group="${group.group_id}">${group.enabled ? "停止响应" : "开始响应"}</button>
              </footer>
            </article>`,
                  )
                  .join("")
              : `<div class="empty" style="grid-column:1/-1"><strong>还没有添加群</strong>左下角输入群号，或者在 QQ 里发指令加群。</div>`
          }
          </div>
        </div>
        <div class="page-title" style="margin-top:26px">
          <div><h2>模型与表情包</h2><p>当前使用的模型源，以及每个情绪下的表情包数量。</p></div>
        </div>
        <div class="status-sections">
          <div class="live-block" data-block="model">${modelCard(status)}</div>
          <div class="live-block" data-block="emotions">${emotionCard(status)}</div>
        </div>
      </div>`;
  }

  /* -------------------------------------------------------- 视图：单群 */

  function emotionGrid(status) {
    const counts = status.emotions?.counts || {};
    return `
      <div class="page-title" style="margin:22px 0 8px"><div><h2>表情包</h2><p>每种情绪随机抽一张，点一下换一张。</p></div></div>
      <div class="emotion-grid">
        ${Object.entries(counts)
          .map(
            ([name, count]) => `
          <button type="button" class="emotion" data-action="reload-emotion" data-emotion="${esc(name)}" title="换一张 ${esc(name)}">
            <img src="/api/emotions/${encodeURIComponent(name)}" alt="${esc(name)}" loading="lazy">
            <em>${num(count)}</em>
            <span>${esc(name)}</span>
          </button>`,
          )
          .join("")}
      </div>`;
  }

  function viewGroup() {
    const status = state.status;
    const group = currentGroup();
    const test = state.groupId === WEBUI_TEST;
    const records = test ? state.memory.length : group?.records || 0;
    const maxRecords = group?.max_records || status.stats.max_memory;

    return `
      <div class="page">
        <div class="live-block" data-block="vitals">${vitals({
          ...status,
          stats: {
            ...status.stats,
            group_count: 1,
            water_enabled: group?.water_enabled ? 1 : 0,
            record_total: records,
            max_memory: maxRecords,
          },
        })}</div>
        <div class="live-block" data-block="cards">
        <div class="status-sections">
          ${
            test
              ? `<div class="status-card">
                  <h3>网页试聊</h3>
                  <div class="status-row"><span class="label">对话</span><span class="value">用 <span class="badge accent">webui-test</span> 独立记忆，不影响任何群</span></div>
                  <div class="status-row"><span class="label">记忆</span><span class="value">${num(state.memory.length)} 条</span></div>
                  <div class="status-row"><span class="label">操作</span><span class="value" style="display:flex;gap:6px;justify-content:flex-end">
                    <button type="button" data-action="open-chat" style="font-size:12px;padding:3px 9px">开始试聊</button>
                    <button type="button" data-action="chat-reset" style="font-size:12px;padding:3px 9px">清空记忆</button>
                  </span></div>
                </div>`
              : `<div class="status-card">
                  <h3>对话设置</h3>
                  <div class="status-row">
                    <span class="label">响应消息</span>
                    <span class="value"><label class="switch"><input type="checkbox" data-action="toggle-enabled" ${group?.enabled ? "checked" : ""}><span></span></label></span>
                  </div>
                  <div class="status-row">
                    <span class="label">自动聊天（水群）</span>
                    <span class="value"><label class="switch"><input type="checkbox" data-action="toggle-water" ${group?.water_enabled ? "checked" : ""}><span></span></label></span>
                  </div>
                  <div class="status-row">
                    <span class="label">水群概率</span>
                    <span class="value slider">
                      <input type="range" min="0" max="1" step="0.01" value="${group?.water_probability ?? 0}" data-action="water-slider">
                      <output data-water-out>${pct(group?.water_probability)}</output>
                    </span>
                  </div>
                  <div class="status-row">
                    <span class="label">操作</span>
                    <span class="value" style="display:flex;gap:6px;justify-content:flex-end">
                      <button type="button" data-action="clear-memory" style="font-size:12px;padding:3px 9px">清空记忆</button>
                      <button type="button" class="danger-text" data-action="remove-group" style="font-size:12px;padding:3px 9px">移出白名单</button>
                    </span>
                  </div>
                </div>`
          }
          <div class="status-card">
            <h3>群信息</h3>
            <div class="status-row"><span class="label">群号</span><span class="value mono">${esc(state.groupId || "—")}</span></div>
            <div class="status-row"><span class="label">记忆</span><span class="value">${num(records)} / ${num(maxRecords)}</span></div>
            <div class="status-row"><span class="label">当前模型</span><span class="value mono">${esc(status.model?.model || "未选择")}</span></div>
            <div class="status-row"><span class="label">表情包</span><span class="value">${num(status.stats.emotion_total)} 张</span></div>
          </div>
          <div class="status-card">
            <h3>情绪分布</h3>
            <div class="status-row"><span class="label">已有表情</span><span class="value stat-chips">${Object.entries(status.emotions?.counts || {})
              .slice(0, 8)
              .map(([name, count]) => `<span>${esc(name)} ${num(count)}</span>`)
              .join("")}</span></div>
          </div>
        </div>
        </div>
        <div class="live-block" data-block="emotion-grid">${emotionGrid(status)}</div>
      </div>`;
  }

  /* -------------------------------------------------------- 视图：记忆 */

  function memoryBody() {
    if (state.memoryGroup !== state.groupId) {
      return `<div class="empty">正在读取记忆…</div>`;
    }
    if (!state.memory.length) {
      return `<div class="empty"><strong>还没有记忆</strong>群里聊几句，或者去试聊页说句话，记忆会出现在这里。</div>`;
    }
    return state.memory
      .map((record) => {
        const role = record.role === "assistant" ? "tomato" : record.role === "system" ? "系统" : "用户";
        const kind = record.role === "system" ? "system" : record.role === "assistant" ? "bot" : "";
        const mark = record.mark && record.mark !== "default" ? `<span class="badge warn">${esc(record.mark)}</span>` : "";
        return `
          <div class="memory-item ${kind}">
            <header><strong>${role}</strong>${mark}<span class="spacer"></span><span>${num(record.chars)} 字</span></header>
            <div class="memory-text">${esc(record.content)}</div>
          </div>`;
      })
      .join("");
  }

  function viewMemory() {
    return `
      <div class="page-bar">
        <span class="hint">记忆按条裁剪，system 记录会一直保留。</span>
        <span class="spacer"></span>
        <button type="button" data-action="reload-memory">刷新</button>
        <button type="button" class="danger-text" data-action="clear-memory">清空记忆</button>
      </div>
      <div class="page">
        <div id="memory-body" class="memory-list">${state.memoryGroup === state.groupId ? memoryBody() : '<div class="empty">正在读取记忆…</div>'}</div>
      </div>`;
  }

  async function loadMemory(groupId) {
    if (!groupId) return false;
    try {
      const data = await api(`/groups/${encodeURIComponent(groupId)}/memory`);
      if (state.groupId !== groupId) return false;
      state.memory = data.records || [];
      state.memoryGroup = groupId;
      return true;
    } catch (err) {
      toast(err.message, "error");
      return false;
    }
  }

  /* -------------------------------------------------------- 视图：试聊 */

  function viewChat() {
    const status = state.status;
    return `
      <div class="chat-layout">
        <div class="chat-column">
          <div id="chat-scroll" class="chat" role="log" aria-live="polite"></div>
          <form id="composer" class="composer" autocomplete="off">
            <textarea id="chat-text" rows="2" maxlength="500" placeholder="和 tomato 说点什么…（Enter 发送，Shift+Enter 换行）" aria-label="试聊内容"></textarea>
            <div class="composer-bottom">
              <span class="hint">试聊用独立记忆，不会发到 QQ，也不会影响群聊上下文。</span>
              <button class="primary" type="submit">发送</button>
            </div>
            <div id="chat-result" class="composer-result"></div>
          </form>
        </div>
        <aside class="inspector">
          <h3>本轮信息</h3>
          <div class="status-row"><span class="label">模型</span><span class="value mono">${esc(status.model?.model || "—")}</span></div>
          <div class="status-row"><span class="label">模型源</span><span class="value">${esc(status.model?.label || "—")}</span></div>
          <div class="status-row"><span class="label">记忆</span><span class="value">${num(state.memory.length)} 条</span></div>
          <h3 style="margin-top:18px">最近的情绪</h3>
          <div id="chat-tags" class="msg-tags"><span class="hint">还没有回复</span></div>
          <div style="margin-top:18px;display:flex;flex-direction:column;gap:8px">
            <button type="button" data-action="chat-reset">清空试聊记忆</button>
            <button type="button" data-action="reload-memory">刷新记忆</button>
          </div>
          <p class="hint" style="margin-top:16px">如果模型是推理模型（Qwen3 / DeepSeek-R1），记得在设置里给模型源加 <code>extra_body: {"reasoning_effort": "none"}</code>，不然它会先把 token 花在思考上。</p>
        </aside>
      </div>`;
  }

  function chatMessage(role, text, extra = {}) {
    const sent = role === "user";
    return `
      <div class="msg ${sent ? "sent" : "bot"}">
        <span class="avatar">${sent ? "我" : "🍅"}</span>
        <div class="msg-body">
          <div class="msg-head"><strong>${sent ? "你" : "tomato"}</strong><span>${extra.time || ""}</span></div>
          <div class="bubble">${esc(text)}</div>
          ${extra.tags ? `<div class="msg-tags">${extra.tags}</div>` : ""}
        </div>
      </div>`;
  }

  function renderChatMessages() {
    const box = $("#chat-scroll");
    if (!box) return;
    if (!state.chat.length) {
      box.innerHTML = `<div class="empty"><strong>开始试聊</strong>这里不经过 QQ 框架，直接用当前模型生成回复。<br>说一句「你好呀」试试。</div>`;
    } else {
      box.innerHTML = state.chat
        .map((message) =>
          chatMessage(message.role, message.text, {
            time: message.time || "",
            tags: (message.emotions || []).map((emotion) => `<span class="badge accent">${esc(emotion)}</span>`).join("") +
              (message.image ? `<span class="badge">附带表情包</span>` : ""),
          }),
        )
        .join("");
    }
    renderChatTags();
    box.scrollTop = box.scrollHeight;
  }

  function renderChatTags() {
    const box = $("#chat-tags");
    if (!box) return;
    const last = [...state.chat].reverse().find((message) => message.role === "bot");
    const emotions = last?.emotions || [];
    box.innerHTML = emotions.length
      ? emotions.map((emotion) => `<span class="badge accent">${esc(emotion)}</span>`).join("")
      : `<span class="hint">还没有回复</span>`;
  }

  function updateChatHeaderOnly() {
    // 追聊时只保证输入框不会因为整页重绘而丢焦点
    const input = $("#chat-text");
    if (input) input.focus({ preventScroll: true });
  }

  async function loadChatHistory() {
    try {
      const data = await api(`/groups/${WEBUI_TEST}/memory`);
      state.memory = data.records || [];
      state.memoryGroup = WEBUI_TEST;
      state.chat = state.memory
        .filter((record) => record.role === "user" || record.role === "assistant")
        .map((record) => ({ role: record.role === "assistant" ? "bot" : "user", text: record.content.replace(/\s*--\*[^*]+\*--\s*/g, "").trim() }));
    } catch {
      state.chat = [];
    }
  }

  async function sendChat(text) {
    if (state.chatBusy) return;
    state.chatBusy = true;
    const now = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    state.chat.push({ role: "user", text, time: now });
    renderChatMessages();
    const result = $("#chat-result");
    if (result) {
      result.textContent = "tomato 正在想…";
      result.classList.remove("error");
    }
    try {
      const data = await api("/chat", { method: "POST", body: JSON.stringify({ text }) });
      state.chat.push({ role: "bot", text: data.reply, emotions: data.emotions || [], image: data.has_image });
      if (result) result.textContent = "";
      await loadStatus();
      await loadMemory(WEBUI_TEST);
    } catch (err) {
      state.chat.push({ role: "bot", text: `（出错了：${err.message}）` });
      if (result) {
        result.textContent = err.message;
        result.classList.add("error");
      }
    } finally {
      state.chatBusy = false;
      renderChatMessages();
      const input = $("#chat-text");
      if (input) input.focus();
    }
  }

  /* -------------------------------------------------------- 视图：设置 */

  const SETTING_GROUPS = [
    { title: "基本", keys: ["ALLOW_GROUPS", "VISION_ENABLED", "VISION_MAX_IMAGES", "SPLIT_PATTERN", "SPLIT_MAX_CHARS", "SPLIT_DELAY_BASE", "SPLIT_DELAY_PER_CHAR", "SPLIT_DELAY_JITTER"], desc: "机器人允许在哪些群说话、看不看图片，以及把长回复切成多条气泡的规则（AI 名字在「人格与提示词」里改）。" },
    { title: "模型", keys: ["MODELS", "DEFAULT_SOURCE", "DEFAULT_MODEL_INDEX"], desc: "ollama 和 OpenAI 兼容（LM Studio / DeepSeek / OneAPI）都可以。" },
    { title: "生成参数", keys: ["GLOBAL_CTX", "PREDICT", "TEMPERATURE", "TOP_K", "TOP_P", "REPEAT_PENALTY", "REQUEST_TIMEOUT"], desc: "决定回复的长度和风格，改完立刻生效。" },
    { title: "行为概率", keys: ["DEFAULT_WATER_PROBABILITY", "SEND_IMAGE_PROBABILITY", "REPLY_PROBABILITY", "NORMAL_USER_MAX_WATER_PROBABILITY", "MERGE_MAX_LEN", "RESP_LEN_LIMIT"], desc: "水群、发图、引用回复和合并消息的规则。" },
    { title: "记忆", keys: ["MAX_MEMORY"], desc: "每个群保留多少条上下文。" },
  ];

  function fieldByKey(key) {
    return state.fields.find((field) => field.key === key) || { key, label: key, kind: "str" };
  }

  function control(field, value) {
    const key = esc(field.key);
    if (field.kind === "groups") {
      const groups = (value || []).map(
        (group) => `<span class="tag" data-group="${esc(group)}">${esc(group)}<button type="button" data-action="remove-chip" aria-label="移除 ${esc(group)}">×</button></span>`,
      );
      return `<div class="groups-editor" id="groups-editor">${groups.join("")}<input id="group-chip-input" maxlength="12" inputmode="numeric" placeholder="群号 + 回车"></div>`;
    }
    if (field.kind === "source") {
      const names = Object.keys(state.config?.MODELS || {});
      return `<select data-key="${key}">${names
        .map((name) => `<option value="${esc(name)}" ${name === value ? "selected" : ""}>${esc(name)}（${esc(state.config?.MODELS?.[name]?.label || name)}）</option>`)
        .join("")}</select>`;
    }
    if (field.kind === "models") return `<div class="models-editor" id="models-editor">${modelsEditor(value)}</div>`;
    if (field.kind === "bool") {
      return `<label class="switch"><input type="checkbox" data-key="${key}" ${value ? "checked" : ""}><span></span></label>`;
    }
    if (field.kind === "prob") {
      const v = Number(value) || 0;
      return `<div class="slider">
        <input type="range" min="0" max="1" step="0.01" value="${v}" data-key="${key}" data-prob>
        <output data-prob-out>${pct(v)}</output>
      </div>`;
    }
    if (field.kind === "int" || field.kind === "float") {
      const step = field.step ?? (field.kind === "int" ? 1 : 0.01);
      return `<input type="number" data-key="${key}" value="${Number(value) || 0}" min="${field.min ?? ""}" max="${field.max ?? ""}" step="${step}">`;
    }
    return `<input type="text" data-key="${key}" value="${esc(value ?? "")}">`;
  }

  function modelsEditor(models) {
    const typeOptions = (current) =>
      [
        ["ollama", "ollama"],
        ["openai", "OpenAI 兼容"],
      ]
        .map(([value, label]) => {
          const selected = current === value || (value === "openai" && ["lmstudio", "api", "openai-compatible", "openai_compatible", "lm-studio"].includes(current));
          return `<option value="${value}" ${selected ? "selected" : ""}>${label}</option>`;
        })
        .join("");
    return `${Object.entries(models || {})
      .map(
        ([name, spec]) => `
      <div class="model-card" data-source="${esc(name)}">
        <div class="model-card-head">
          <input data-model-field="name" value="${esc(name)}" aria-label="模型源名称">
          <span class="badge">${esc(spec.type || "ollama")}</span>
          <span class="spacer"></span>
          <button type="button" class="danger-text" data-action="model-remove" style="font-size:12px;padding:3px 9px">删除</button>
        </div>
        <div class="model-grid">
          <label>显示名<input data-model-field="label" value="${esc(spec.label || name)}"></label>
          <label>类型<select data-model-field="type">${typeOptions(spec.type || "ollama")}</select></label>
          <label class="wide">地址<input data-model-field="url" value="${esc(spec.url || "")}" placeholder="http://127.0.0.1:1234"></label>
          <label class="wide">API Key（本地服务可留空）<input data-model-field="api_key" type="password" value="${esc(spec.api_key || "")}"></label>
          <label class="wide">模型列表（每行一个）<textarea data-model-field="model">${esc((spec.model || []).join("\n"))}</textarea></label>
          <label class="wide">extra_body（JSON，可留空）<textarea data-model-field="extra_body">${esc(spec.extra_body ? JSON.stringify(spec.extra_body, null, 2) : "")}</textarea></label>
        </div>
      </div>`,
      )
      .join("")}
    <div><button type="button" data-action="model-add" style="font-size:12.5px;padding:4px 11px">+ 添加模型源</button></div>`;
  }

  function viewSettings() {
    const values = state.config || {};
    const index = SETTING_GROUPS.map(
      (group, index) => `<button type="button" data-scroll="${index}" class="${index === 0 ? "active" : ""}">${esc(group.title)}</button>`,
    ).join("");
    const body = SETTING_GROUPS.map(
      (group, index) => `
      <section class="setting-group" id="setting-group-${index}" data-group-index="${index}">
        <h3>${esc(group.title)}</h3>
        <p>${esc(group.desc)}</p>
        ${group.keys
          .map((key) => {
            const field = fieldByKey(key);
            return `
          <div class="setting">
            <div>
              <div class="setting-title">${esc(field.label)}${field.restart ? '<span class="badge warn">需重启</span>' : ""}</div>
              ${field.help ? `<div class="setting-help">${esc(field.help)}</div>` : ""}
              <div class="key">${esc(key)}</div>
            </div>
            <div class="setting-control">${control(field, values[key])}</div>
          </div>`;
          })
          .join("")}
      </section>`,
    ).join("");

    return `
      <div class="settings-layout">
        <nav class="settings-index" aria-label="设置分类">${index}</nav>
        <div class="settings-body">
          ${body}
        </div>
      </div>
      <div class="save-bar">
        <span id="save-state" class="state">所有修改都会写进 webui_config.json</span>
        <span class="spacer"></span>
        <button type="button" data-action="discard-config" class="ghost">放弃修改</button>
        <button type="button" data-action="save-config" class="primary">保存并应用</button>
      </div>`;
  }

  function syncSaveBar() {
    const label = $("#save-state");
    if (!label) return;
    if (state.dirty) {
      label.textContent = "有未保存的修改";
      label.className = "state dirty";
    } else if (state.status?.restart_required?.length) {
      label.textContent = `${state.status.restart_required.join("、")} 需要重启进程生效`;
      label.className = "state dirty";
    } else {
      label.textContent = "所有修改都会写进 webui_config.json";
      label.className = "state";
    }
  }

  function collectModels() {
    const models = {};
    for (const card of $$("#models-editor .model-card")) {
      const get = (field) => card.querySelector(`[data-model-field="${field}"]`)?.value ?? "";
      const name = get("name").trim();
      if (!name) throw new Error("模型源名称不能为空");
      const spec = {
        label: get("label").trim() || name,
        type: get("type") || "ollama",
        url: get("url").trim(),
        model: get("model")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      };
      const apiKey = get("api_key").trim();
      if (apiKey) spec.api_key = apiKey;
      const rawExtra = get("extra_body").trim();
      if (rawExtra) {
        try {
          spec.extra_body = JSON.parse(rawExtra);
        } catch {
          throw new Error(`模型源 ${name} 的 extra_body 不是合法 JSON`);
        }
      }
      models[name] = spec;
    }
    if (!Object.keys(models).length) throw new Error("至少要保留一个模型源");
    return models;
  }

  function collectConfig() {
    const values = { ...state.config };
    for (const field of state.fields) {
      if (!field.key || field.kind === "models" || field.kind === "groups") continue;
      const el = $(`[data-key="${field.key}"]`);
      if (!el) continue;
      if (field.kind === "source") values[field.key] = el.value;
      else if (field.kind === "bool") values[field.key] = !!el.checked;
      else if (field.kind === "int") values[field.key] = Number.parseInt(el.value, 10);
      else if (field.kind === "float" || field.kind === "prob") values[field.key] = Number.parseFloat(el.value);
      else values[field.key] = el.value;
    }
    values.ALLOW_GROUPS = $$("#groups-editor .tag").map((tag) => tag.dataset.group);
    values.MODELS = collectModels();
    return values;
  }

  /* ------------------------------------------------ 视图：人格与提示词 */

  const PROMPT_GROUPS = [
    {
      title: "人格设定",
      keys: [
        "ai_name",
        "system",
        "example_user",
        "example_assistant",
        "message_start",
        "message_prefix",
        "message_suffix",
        "message_end",
      ],
      desc: "模型是谁、怎么说话，以及每条消息自动加的起始符、前后缀与结束符。人设写进每轮请求的最前面，改完下一句回复就会变。",
    },
    {
      title: "纠正提示",
      keys: ["tip_no_emotion", "tip_too_long"],
      desc: "回复不合要求时，下一次生成会看到其中一条提醒（只保留最新一条）。",
    },
    {
      title: "场景与兜底",
      keys: ["reqfail_tips", "private_note", "focus_hint", "context_note", "empty_reply"],
      desc: "私聊说明、多人轮流 @ 时的聚焦指令、每轮附带的当前时间，以及各种失败兜底。",
    },
  ];

  function promptField(key) {
    return (state.prompts?.fields || []).find((field) => field.key === key) || { key, label: key, kind: "text", rows: 3 };
  }

  function promptControl(field, value) {
    const key = esc(field.key);
    const text = Array.isArray(value) ? value.join("\n") : String(value ?? "");
    if (field.kind === "str") {
      const maxlength = field.maxlength || 24;
      return `<input type="text" data-prompt="${key}" value="${esc(text)}" maxlength="${maxlength}" autocomplete="off">`;
    }
    return `<textarea data-prompt="${key}" rows="${field.rows || 3}" spellcheck="false">${esc(text)}</textarea>`;
  }

  function viewPrompts() {
    if (!state.prompts) return `<div class="empty" style="padding:40px 0">正在读取提示词…</div>`;
    const values = state.prompts.values || {};
    const index = PROMPT_GROUPS.map(
      (group, offset) => `<button type="button" data-prompt-scroll="${offset}" class="${offset === 0 ? "active" : ""}">${esc(group.title)}</button>`,
    ).join("");
    const body = PROMPT_GROUPS.map(
      (group, offset) => `
      <section class="setting-group" id="prompt-group-${offset}">
        <h3>${esc(group.title)}</h3>
        ${group.desc ? `<p>${esc(group.desc)}</p>` : ""}
        ${group.keys
          .map((key) => {
            const field = promptField(key);
            return `
          <div class="setting prompt-setting">
            <div>
              <div class="setting-title">${esc(field.label)}</div>
              ${field.help ? `<div class="setting-help">${esc(field.help)}</div>` : ""}
              <div class="key">${esc(key)}</div>
            </div>
            <div class="setting-control">${promptControl(field, values[key])}</div>
          </div>`;
          })
          .join("")}
      </section>`,
    ).join("");

    return `
      <div class="settings-layout">
        <nav class="settings-index" aria-label="提示词分类">${index}</nav>
        <div class="settings-body">${body}</div>
      </div>
      <div class="save-bar">
        <span id="prompt-state" class="state">改动立即生效，写进 webui_prompts.json</span>
        <span class="spacer"></span>
        <button type="button" data-action="reset-prompts" class="ghost">恢复默认</button>
        <button type="button" data-action="save-prompts" class="primary">保存并应用</button>
      </div>`;
  }

  async function loadPrompts() {
    try {
      state.prompts = await api("/prompts");
    } catch (err) {
      toast(`读取提示词失败：${err.message}`, "error");
    }
  }

  function collectPrompts() {
    const values = { ...(state.prompts?.values || {}) };
    for (const field of state.prompts?.fields || []) {
      const el = $(`[data-prompt="${field.key}"]`);
      if (!el) continue;
      values[field.key] =
        field.kind === "lines"
          ? el.value
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean)
          : el.value;
    }
    return values;
  }

  function syncPromptBar() {
    const label = $("#prompt-state");
    if (!label) return;
    if (state.promptDirty) {
      label.textContent = "有未保存的修改";
      label.className = "state dirty";
    } else {
      label.textContent = "改动立即生效，写进 webui_prompts.json";
      label.className = "state";
    }
  }

  function markPromptDirty() {
    if (state.view !== "prompts" || state.promptDirty) return;
    state.promptDirty = true;
    syncPromptBar();
  }

  async function savePrompts() {
    const button = $('[data-action="save-prompts"]');
    if (button) button.disabled = true;
    try {
      const data = await api("/prompts", { method: "POST", body: JSON.stringify({ values: collectPrompts() }) });
      state.prompts = { ...state.prompts, ...data };
      state.promptDirty = false;
      syncPromptBar();
      toast("提示词已保存，立即生效", "ok");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function resetPrompts() {
    if (!(await confirmAction("恢复默认会删掉 webui_prompts.json，页面上的修改都会丢失。继续吗？", { okText: "恢复默认" }))) return;
    try {
      const data = await api("/prompts/reset", { method: "POST" });
      state.prompts = { ...state.prompts, ...data };
      state.promptDirty = false;
      render();
      toast("已恢复默认提示词", "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  /* -------------------------------------------------------- 视图：日志 */

  function logLine(line) {
    const level = /ERROR|Exception|Traceback/.test(line) ? "error" : /WARNING|WARN/.test(line) ? "warn" : /SUCCESS|Succeeded/.test(line) ? "success" : "";
    const match = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
    const rest = match ? line.slice(match[0].length) : line;
    return `<span class="log-line ${level}">${match ? `<span class="ts">${esc(match[0])}</span>` : ""}${esc(rest)}</span>`;
  }

  function viewLogs() {
    return `
      <div class="logs-page">
        <pre id="logs-console" class="logs-console"><span class="log-empty">正在读取 logs/bot.log…</span></pre>
      </div>`;
  }

  // /api/logs 返回的是「最后 N 行」，窗口会滑动；这里找旧窗口尾巴与新窗口开头的重叠长度
  function logsOverlap(previous, lines) {
    const max = Math.min(previous.length, lines.length);
    for (let n = max; n > 0; n -= 1) {
      let same = true;
      for (let i = 0; i < n; i += 1) {
        if (lines[i] !== previous[previous.length - n + i]) {
          same = false;
          break;
        }
      }
      if (same) return n;
    }
    return 0;
  }

  async function loadLogs() {
    const consoleBox = $("#logs-console");
    if (!consoleBox) return;
    try {
      const data = await api("/logs?lines=400");
      const lines = data.lines || [];
      if (!lines.length) {
        consoleBox.innerHTML = `<span class="log-empty">${esc(data.hint || "还没有日志，程序启动后会自动创建 logs/bot.log")}</span>`;
        state.logs = [];
        return;
      }
      const previous = state.logs || [];
      const overlap = previous.length ? logsOverlap(previous, lines) : 0;
      if (overlap > 0) {
        // 只把新行插到末尾：不重建 <pre>，滚动位置和选中文字都不动
        const fresh = lines.slice(overlap);
        if (fresh.length) {
          const atBottom = consoleBox.scrollHeight - consoleBox.scrollTop - consoleBox.clientHeight < 48;
          consoleBox.insertAdjacentHTML("beforeend", fresh.map(logLine).join(""));
          if (atBottom) consoleBox.scrollTop = consoleBox.scrollHeight;
        }
        while (consoleBox.childElementCount > 1200) consoleBox.removeChild(consoleBox.firstElementChild);
      } else {
        consoleBox.innerHTML = lines.map(logLine).join("");
        consoleBox.scrollTop = consoleBox.scrollHeight;
      }
      state.logs = lines;
    } catch (err) {
      consoleBox.innerHTML = `<span class="log-line error">读取日志失败：${esc(err.message)}</span>`;
    }
  }

  /* ------------------------------------------------------------ 交互动作 */

  function selectView(view) {
    if (state.view === view) return;
    state.view = view;
    if (view === "group" && !state.groupId) {
      const groups = state.status?.groups || [];
      if (groups.length) state.groupId = groups[0].group_id;
      else if (!state.groupId) state.groupId = WEBUI_TEST;
    }
    if (view === "settings") {
      state.config = state.status?.config?.values || state.config;
      state.dirty = false;
    }
    if (view === "prompts") {
      state.promptDirty = false;
      if (!state.prompts) loadPrompts().then(() => {
        if (state.view === "prompts") render();
      });
    }
    render();
  }

  function openGroup(groupId, tab = "overview") {
    state.groupId = groupId;
    state.view = "group";
    state.tab = tab;
    if (groupId === WEBUI_TEST && tab === "chat") {
      loadChatHistory().then(() => {
        render();
        renderChatMessages();
      });
      render();
    } else {
      render();
      if (tab === "memory") loadMemory(groupId).then(() => {
        if (state.view === "group" && state.tab === "memory") {
          const body = $("#memory-body");
          if (body) body.innerHTML = memoryBody();
        }
      });
    }
  }

  async function patchGroup(groupId, patch) {
    try {
      await api(`/groups/${encodeURIComponent(groupId)}`, { method: "PATCH", body: JSON.stringify(patch) });
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function removeGroup(groupId) {
    if (!(await confirmAction(`确定把群 ${groupId} 移出白名单吗？移出后这个群不会再收到任何回复，记忆也会一起清掉。`, { okText: "移出" }))) return;
    try {
      await api(`/groups/${encodeURIComponent(groupId)}`, { method: "DELETE" });
      toast("已移出白名单", "ok");
      if (state.groupId === groupId) {
        state.groupId = null;
        state.view = "overview";
        state.memory = [];
        state.memoryGroup = null;
      }
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function addGroup(groupId) {
    if (!/^\d{3,12}$/.test(groupId)) {
      toast("群号必须是 3-12 位数字", "error");
      return;
    }
    try {
      await api("/groups", { method: "POST", body: JSON.stringify({ group_id: groupId }) });
      toast(`已添加群 ${groupId}`, "ok");
      state.groupId = groupId;
      state.view = "group";
      state.tab = "overview";
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function clearMemory(groupId) {
    if (!(await confirmAction(`确定清空群 ${groupId} 的记忆吗？这会把上下文一起丢掉。`, { okText: "清空" }))) return;
    try {
      const data = await api(`/groups/${encodeURIComponent(groupId)}/memory`, { method: "DELETE" });
      toast(`已清空 ${num(data.removed)} 条记忆`, "ok");
      state.memory = [];
      state.memoryGroup = groupId;
      if (state.view === "group" && state.tab === "chat") state.chat = [];
      await loadStatus();
      if (state.view === "group" && state.tab === "memory") render();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function resetChat() {
    if (!(await confirmAction("清空网页试聊的记忆和对话框？", { okText: "清空" }))) return;
    try {
      await api("/chat/reset", { method: "POST" });
      state.chat = [];
      state.memory = [];
      toast("试聊记忆已清空", "ok");
      renderChatMessages();
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function syncModels() {
    toast("正在向模型源同步模型列表…");
    try {
      const data = await api("/models/sync", { method: "POST", body: JSON.stringify({}) });
      toast(`已同步 ${data.models?.length || 0} 个模型`, "ok");
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function switchModel(source) {
    try {
      await api("/models/switch", { method: "POST", body: JSON.stringify({ source }) });
      toast(`已切换到 ${source}`, "ok");
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function saveConfig() {
    const button = $('[data-action="save-config"]');
    if (button) button.disabled = true;
    try {
      const values = collectConfig();
      const data = await api("/config", { method: "POST", body: JSON.stringify({ values }) });
      state.config = data.values;
      state.dirty = false;
      const restart = data.restart_required || [];
      toast(restart.length ? `已保存；${restart.join("、")} 需要重启进程生效` : "设置已保存并生效", restart.length ? "" : "ok");
      await loadStatus();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      if (button) button.disabled = false;
      syncSaveBar();
    }
  }

  function markDirty() {
    if (state.view !== "settings" || state.dirty) return;
    state.dirty = true;
    syncSaveBar();
  }

  function bindEvents() {
    $("#group-search").addEventListener("input", (event) => {
      state.search = event.target.value;
      renderSidebar();
    });

    $("#add-group").addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("#add-group-id");
      const value = input.value.trim();
      input.value = "";
      addGroup(value);
    });

    document.addEventListener("click", (event) => {
      const viewButton = event.target.closest("[data-view]");
      if (viewButton) {
        selectView(viewButton.dataset.view);
        return;
      }
      const groupButton = event.target.closest("[data-group]");
      if (groupButton && !event.target.closest("[data-action]")) {
        openGroup(groupButton.dataset.group);
        return;
      }
      const tabButton = event.target.closest("[data-tab]");
      if (tabButton) {
        state.tab = tabButton.dataset.tab;
        render();
        renderView();
        if (state.tab === "chat") loadChatHistory().then(() => renderChatMessages());
        return;
      }
      const scrollButton = event.target.closest("[data-scroll]");
      if (scrollButton) {
        const section = $(`#setting-group-${scrollButton.dataset.scroll}`);
        if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
        $$(".settings-index button").forEach((button) => button.classList.toggle("active", button === scrollButton));
        return;
      }
      const promptScroll = event.target.closest("[data-prompt-scroll]");
      if (promptScroll) {
        const section = $(`#prompt-group-${promptScroll.dataset.promptScroll}`);
        if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
        $$(".settings-index button").forEach((button) => button.classList.toggle("active", button === promptScroll));
        return;
      }
      const action = event.target.closest("[data-action]");
      if (!action) return;
      handleAction(action, event);
    });

    $("#view").addEventListener("input", (event) => {
      const target = event.target;
      if (target.matches("[data-prob]")) {
        const out = target.parentElement.querySelector("[data-prob-out]");
        if (out) out.textContent = pct(target.value);
      }
      if (target.matches('[data-action="water-slider"]')) {
        const out = target.parentElement.querySelector("[data-water-out]");
        if (out) out.textContent = pct(target.value);
      }
      if (target.matches("[data-key]") || target.closest("#models-editor") || target.closest("#groups-editor")) {
        markDirty();
      }
      if (target.matches("[data-prompt]")) markPromptDirty();
    });

    $("#view").addEventListener("change", (event) => {
      const target = event.target;
      if (target.matches('[data-action="water-slider"]')) {
        // 松手才提交，拖动过程中只更新显示
        patchGroup(state.groupId, { water_probability: Number(target.value) });
        return;
      }
      if (target.matches("[data-key]") || target.closest("#models-editor")) markDirty();
    });

    // 顶栏的「自动刷新」勾选框：事件挂在顶层容器上，重绘内部节点不会丢监听
    $("#actions").addEventListener("change", (event) => {
      if (event.target.id === "logs-auto") state.logAuto = event.target.checked;
    });

    $("#view").addEventListener("keydown", (event) => {
      const target = event.target;
      if (target.id === "chat-text" && event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        const form = $("#composer");
        if (form) form.requestSubmit();
        return;
      }
      if (target.id === "group-chip-input" && event.key === "Enter") {
        event.preventDefault();
        const value = target.value.trim();
        if (!/^\d{3,12}$/.test(value)) {
          toast("群号必须是 3-12 位数字", "error");
          return;
        }
        if (!state.config.ALLOW_GROUPS.includes(value)) {
          state.config.ALLOW_GROUPS.push(value);
          const editor = $("#groups-editor");
          const input = $("#group-chip-input");
          const tag = document.createElement("span");
          tag.className = "tag";
          tag.dataset.group = value;
          tag.innerHTML = `${esc(value)}<button type="button" data-action="remove-chip" aria-label="移除 ${esc(value)}">×</button>`;
          editor.insertBefore(tag, input);
        }
        target.value = "";
        markDirty();
      }
    });

    $("#view").addEventListener("submit", (event) => {
      if (event.target.id !== "composer") return;
      event.preventDefault();
      const input = $("#chat-text");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      sendChat(text);
    });
  }

  function handleAction(element, event) {
    const action = element.dataset.action;
    const groupId = element.dataset.group || state.groupId;
    switch (action) {
      case "refresh":
        state.dirty = false;
        loadStatus(true).then(() => {
          if (state.view === "group" && state.tab === "memory") loadMemory(state.groupId).then(() => render());
          if (state.view === "prompts") {
            loadPrompts().then(() => {
              state.promptDirty = false;
              if (state.view === "prompts") render();
            });
          }
        });
        break;
      case "refresh-logs":
        loadLogs();
        break;
      case "open-settings":
        selectView("settings");
        break;
      case "open-chat":
        state.view = "group";
        state.tab = "chat";
        if (state.groupId !== WEBUI_TEST) state.groupId = WEBUI_TEST;
        render();
        renderView();
        loadChatHistory().then(() => renderChatMessages());
        break;
      case "toggle-enabled": {
        const group = (state.status?.groups || []).find((item) => item.group_id === groupId);
        if (group) patchGroup(groupId, { enabled: !group.enabled });
        break;
      }
      case "toggle-water": {
        const group = (state.status?.groups || []).find((item) => item.group_id === groupId);
        if (group) patchGroup(groupId, { water_enabled: !group.water_enabled });
        break;
      }
      case "water-slider": {
        // 滑杆在 input 时只更新显示，松手（change）才提交
        break;
      }
      case "remove-chip": {
        element.closest(".tag")?.remove();
        markDirty();
        break;
      }
      case "model-add": {
        const editor = $("#models-editor");
        if (!editor) break;
        const name = `source${Object.keys(state.config.MODELS || {}).length + 1}`;
        const wrapper = document.createElement("div");
        wrapper.innerHTML = modelsEditor({
          [name]: { label: name, type: "openai", url: "http://127.0.0.1:1234", model: [] },
        });
        const card = wrapper.querySelector(".model-card");
        editor.insertBefore(card, editor.lastElementChild);
        markDirty();
        break;
      }
      case "model-remove": {
        element.closest(".model-card")?.remove();
        markDirty();
        break;
      }
      case "save-config":
        saveConfig();
        break;
      case "save-prompts":
        savePrompts();
        break;
      case "reset-prompts":
        resetPrompts();
        break;
      case "reload-prompts":
        loadPrompts().then(() => {
          render();
          toast("已重新读取提示词", "ok");
        });
        break;
      case "discard-config":
        state.dirty = false;
        state.config = state.status?.config?.values || state.config;
        render();
        break;
      case "sync-models":
        syncModels();
        break;
      case "switch-source":
        switchModel(element.dataset.source);
        break;
      case "add-group":
        break;
      case "remove-group":
        removeGroup(groupId);
        break;
      case "clear-memory":
        clearMemory(groupId);
        break;
      case "reload-memory":
        loadMemory(groupId).then(() => {
          render();
          toast("记忆已刷新", "ok");
        });
        break;
      case "reload-emotion": {
        const image = element.querySelector("img");
        if (image) image.src = `/api/emotions/${encodeURIComponent(element.dataset.emotion)}?t=${Date.now()}`;
        break;
      }
      case "chat-reset":
        resetChat();
        break;
      case "about": {
        const about = $("#about-dialog");
        $("#about-version").textContent = `v${state.status?.version || "1.0.0"}`;
        $("#about-started").textContent = state.status?.started_at?.replace("T", " ") || "—";
        about.showModal();
        break;
      }
      default:
        break;
    }
  }

  /* ------------------------------------------------------------ 生命周期 */

  function startPolling() {
    setInterval(() => {
      if (document.hidden) return;
      // 设置页 / 人格页是手动刷新，轮询不打断正在编辑的表单
      if (state.view === "settings" || state.view === "prompts") return;
      loadStatus();
    }, 8000);
    setInterval(() => {
      if (document.hidden) return;
      if (state.view === "logs" && state.logAuto) loadLogs();
    }, 4000);
  }

  async function boot() {
    window.addEventListener("error", (event) => toast(`页面脚本错误：${event.message || event.error}`, "error"));
    window.addEventListener("unhandledrejection", (event) => {
      const reason = event.reason;
      toast(`请求出错：${(reason && reason.message) || reason}`, "error");
    });
    bindEvents();
    const ok = await loadStatus();
    if (ok && state.view === "group" && state.tab === "chat") {
      await loadChatHistory();
      render();
    }
    startPolling();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
