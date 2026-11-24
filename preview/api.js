(() => {
  const scriptUrl = new URL(document.currentScript.src);
  const workerUrl = new URL("demo-worker.js", scriptUrl);
  workerUrl.search = scriptUrl.search;

  function connect() {
    let port;
    try { port = new SharedWorker(workerUrl, { name: "miniwms-demo" }).port; }
    catch (e) { port = new Worker(workerUrl); }
    const waiting = new Map();
    let next = 0;
    port.addEventListener("message", event => {
      const { id, status, data } = event.data;
      const request = waiting.get(id);
      if (!request) return;
      waiting.delete(id);
      clearTimeout(request.timer);
      if (status >= 400 || data?.ok === false) {
        request.reject(new Error(data?.errors?.join("；") || data?.error || `HTTP ${status}`));
      } else {
        request.resolve(data);
      }
    });
    port.start?.();
    return input => new Promise((resolve, reject) => {
      const id = ++next;
      const timer = setTimeout(() => {
        waiting.delete(id);
        reject(new Error("演示环境启动超时，请刷新页面重试。"));
      }, 90000);
      waiting.set(id, { resolve, reject, timer });
      port.postMessage({ ...input, id });
    });
  }

  const host = window.top;
  window.MWS_PREVIEW_API = (method, path, body = null) => {
    if (!host.miniwmsDemoRequest) host.miniwmsDemoRequest = connect();
    return host.miniwmsDemoRequest({
      method, path, body, warehouse: localStorage.getItem("miniwms_warehouse_id")
    });
  };
  if (window.self === window.top && location.pathname.endsWith("/app.html")) {
    window.demoReady = window.MWS_PREVIEW_API("GET", "/api/scope/context");
    window.demoReady.catch(() => {});
  }
  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".env-badge").forEach(badge => { badge.textContent = "在线演示 · 浏览器独立数据"; });
  });
})();
