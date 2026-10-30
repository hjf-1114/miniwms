/* MiniWMS demo 前端公共 API 封装：零依赖，仅使用浏览器原生 fetch。 */

/**
 * 调用后端 API。
 * @param {string} method HTTP 方法
 * @param {string} path 以 / 开头的接口路径
 * @param {object|null} body JSON 请求体
 * @returns {Promise<object>} 解析后的 JSON；4xx 时抛出含 errors 的 Error
 */
async function api(method, path, body) {
  const opts = { method, headers: {} };
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

/** 渲染状态标签。 */
function statusTag(s) {
  return s === "active"
    ? '<span class="tag ok">启用</span>'
    : '<span class="tag off">停用</span>';
}
