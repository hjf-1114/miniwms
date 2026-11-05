/* MiniWMS demo 前端公共 API 封装：零依赖，仅使用浏览器原生 fetch。 */

/**
 * 调用后端 API。
 * @param {string} method HTTP 方法
 * @param {string} path 以 / 开头的接口路径
 * @param {object|null} body JSON 请求体
 * @returns {Promise<object>} 解析后的 JSON；4xx 时抛出含 errors 的 Error
 */
/** 读取本地登录令牌（无则空串）。 */
function getToken() {
  try { return localStorage.getItem("miniwms_token") || ""; } catch (e) { return ""; }
}

/** 读取登录时保存的用户摘要（仅用于界面展示，token 仍是唯一鉴权依据）。 */
function currentUser() {
  try { return JSON.parse(localStorage.getItem("miniwms_user") || "{}") || {}; }
  catch (e) { return {}; }
}

/* ── 当前仓库上下文（A 作业视角：一次只操作一个仓）──────────────────────
   仓库 id 存在 localStorage，由 api() 统一附加到每个请求的 X-WMS-Warehouse；
   服务端无会话状态，切仓就是换这个 header。                                */
const WH_KEY = "miniwms_warehouse_id";
function currentWarehouseId() {
  try { return localStorage.getItem(WH_KEY) || ""; } catch (e) { return ""; }
}
function setCurrentWarehouseId(id) {
  try {
    if (id === "" || id === null || id === undefined) localStorage.removeItem(WH_KEY);
    else localStorage.setItem(WH_KEY, String(id));
  } catch (e) {}
}

/** 在任意容器里挂载「当前仓库」选择器（独立页侧栏 / 工作台顶栏共用一份实现）。 */
async function mountWarehousePicker(el, onChange) {
  if (!el) return;
  el.innerHTML = '<span class="wh-pick-label">当前仓库</span>' +
    '<select class="wh-pick-select" disabled><option>加载中…</option></select>';
  let ctx;
  let firstPick = !currentWarehouseId();  // 首次确定当前仓后要重载一次页面数据
  try {
    ctx = await api("GET", "/api/scope/context");
  } catch (e) {
    // localStorage 里可能残留已被删除 / 已撤销授权的仓，清掉后重试一次
    setCurrentWarehouseId("");
    firstPick = true;
    try { ctx = await api("GET", "/api/scope/context"); }
    catch (e2) {
      el.innerHTML = '<span class="wh-pick-label">当前仓库</span>' +
        '<span class="wh-pick-err" title="' + esc(e2.message) + '">权限未配置</span>';
      return;
    }
  }
  const list = ctx.warehouses || [];
  if (!list.length) {
    el.innerHTML = '<span class="wh-pick-label">当前仓库</span>' +
      '<span class="wh-pick-err">暂无可用仓库</span>';
    setCurrentWarehouseId("");
    return;
  }
  let cur = ctx.current_warehouse_id;
  if (!cur || !list.some((w) => String(w.id) === String(cur))) cur = list[0].id;
  setCurrentWarehouseId(cur);
  el.innerHTML = '<span class="wh-pick-label">当前仓库</span>' +
    '<select class="wh-pick-select" title="切换仓库后刷新页面数据">' +
    list.map((w) => '<option value="' + w.id + '"' +
      (String(w.id) === String(cur) ? " selected" : "") + '>' +
      esc(w.code + " · " + w.name) + (w.db_path ? "（独立库）" : "") + '</option>').join("") +
    '</select>';
  const sel = el.querySelector("select");
  sel.addEventListener("change", async () => {
    setCurrentWarehouseId(sel.value);
    if (typeof onChange === "function") onChange();
    else location.reload();
  });

  // 首屏的业务请求通常早于本异步初始化发出，那时还没有 X-WMS-Warehouse。
  // 首次确定当前仓后重载一次（独立页 reload / 工作台刷新全部标签），
  // 保证用户看到的一定是当前仓的数据，不会短暂串到主库。
  if (firstPick) {
    setTimeout(() => {
      if (typeof onChange === "function") onChange();
      else location.reload();
    }, 0);
  }
}

/** 退出登录：清除令牌、用户摘要与当前仓库，并让顶层窗口跳到登录页（避免登录页被嵌进 iframe）。 */
function logout() {
  try {
    localStorage.removeItem("miniwms_token");
    localStorage.removeItem("miniwms_user");
    localStorage.removeItem(WH_KEY);
  } catch (e) {}
  try {
    const top = (window.parent && window.parent !== window) ? window.parent : window;
    top.location.href = "/login.html";
  } catch (e) {
    location.href = "/login.html";
  }
}

/** 若未登录则跳转到登录页（用于需要权限的页面）。 */
function requireLogin() {
  if (!getToken()) location.href = "/login.html";
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  const t = getToken();
  if (t) opts.headers["Authorization"] = "Bearer " + t;
  // 登录接口不带旧仓库上下文，避免已删除的仓把登录挡住
  const wh = currentWarehouseId();
  if (wh && path !== "/api/auth/login") opts.headers["X-WMS-Warehouse"] = wh;
  if (body !== undefined && body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  let data = {};
  try {
    data = await resp.json();
  } catch (e) {
    data = { ok: false, errors: ["响应不是 JSON: HTTP " + resp.status] };
  }
  if (!resp.ok || data.ok === false) {
    const errs = data.errors || [data.error || "HTTP " + resp.status];
    throw new Error(errs.join("；"));
  }
  return data;
}

/** 显示提示条。kind: "success" | "error" */
function showMsg(el, text, kind) {
  el.textContent = text;
  el.className = "msg " + kind;
  setTimeout(() => { el.className = "msg"; }, 4000);
}

/** 转义 HTML，防注入。 */
function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/**
 * 打开单据/记录明细。
 * 若当前页面运行在工作台（父窗口提供了 openTab），则在新的标签页中打开明细；
 * 否则退回页内展开，保证独立打开页面时体验一致。
 * @param {string} title 标签标题（如 "收货 ASN-001"）
 * @param {string} url   目标页地址（含 ?detail=ID，不含 embed）
 */
function openDetail(title, url) {
  if (window.parent && window.parent !== window &&
      typeof window.parent.openTab === "function") {
    window.parent.openTab(title, url);
  } else {
    const m = /[?&]detail=(\d+)/.exec(url || "");
    if (m && typeof showDetail === "function") showDetail(parseInt(m[1], 10));
  }
}

/**
 * 打开一个作业标签页（验收 / 上架等独立作业页）。
 * 在工作台里交给父窗口的 openTab 复用同一标签栏；独立打开页面时退化为新窗口，
 * 保证不依赖工作台也能作业。URL 上带 asn_id / line_id，不同单据可同时开多张标签。
 */
function openWorkTab(title, url) {
  if (window.parent && window.parent !== window &&
      typeof window.parent.openTab === "function") {
    window.parent.openTab(title, url);
  } else {
    window.open(url, "_blank");
  }
}

/** 渲染状态标签。 */
function statusTag(s) {
  return s === "active"
    ? '<span class="tag ok">启用</span>'
    : '<span class="tag off">停用</span>';
}

/** 单据标识位展示：把 ASN/DN 的 4 个 0/1 字段渲染成可读标签（M20 剩余）。 */
function docFlagsText(doc) {
  const pairs = [["is_virtual", "虚拟"], ["is_direct", "直调"],
                 ["is_cold_chain", "冷链"], ["is_hazard", "危险"]];
  const hit = pairs.filter(([k]) => Number(doc && doc[k]) === 1)
                   .map(([, label]) => label);
  return hit.length ? hit.join(" · ") : "无";
}
