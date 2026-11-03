/* MiniWMS 布局组件：渲染侧边栏导航并高亮当前页。
   纯原生，每个页面在 body 内提供 <aside class="sidebar" id="sidebar"></aside> 即可。 */
(function () {
  const NAV = [
    { group: "基础主数据" },
    { href: "/owners.html", label: "货主", ico: "🏢" },
    { href: "/products.html", label: "物料", ico: "📦" },
    { href: "/locations.html", label: "库位", ico: "📍" },
    { href: "/customers.html", label: "客户", ico: "👥" },
    { group: "出入库作业" },
    { href: "/asns.html", label: "收货", ico: "📥" },
    { href: "/dns.html", label: "出库", ico: "📤" },
    { href: "/stock.html", label: "库存账", ico: "📊" },
  ];

  function render() {
    const el = document.getElementById("sidebar");
    if (!el) return;
    const path = location.pathname;
    let html = '<div class="brand"><span class="logo">📦</span>MiniWMS</div><nav class="nav">';
    for (const item of NAV) {
      if (item.group) {
        html += `<div class="nav-group">${item.group}</div>`;
      } else {
        const active = path === item.href ? " active" : "";
        html += `<a class="nav-item${active}" href="${item.href}">` +
          `<span class="nav-ico">${item.ico}</span><span>${item.label}</span></a>`;
      }
    }
    html += "</nav>";
    html += '<div class="side-foot">轻量多货主 WMS · 本地演示</div>';
    el.innerHTML = html;
  }

  // 等待 DOM 就绪（脚本一般在 body 末尾，通常已就绪）
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
