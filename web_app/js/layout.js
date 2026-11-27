/* MiniWMS 布局组件：渲染可多层内嵌的侧边栏导航并高亮当前页。
   纯原生，零依赖。独立页面提供 <aside class="sidebar" id="sidebar"></aside> 即可；
   工作台 app.html 通过 window.MWS_NAV_TOOLS.mount() 复用同一份树与交互，只把叶子点击换成开标签。

   结构：一级 5 / 二级 11 / 三级 38；渲染器递归，后续加到第 4 层无需改代码。
   展示策略：默认只展开「当前页所在分支」，其余折叠；用户手动展开/收起的节点记在 localStorage。 */
(function () {
  let NAV = [
    { label: "基础主数据", ico: "🗂️", children: [
      { label: "客商档案", ico: "🏷️", children: [
        { href: "/owners.html", label: "货主", ico: "🏢" },
        { href: "/customers.html", label: "客户", ico: "👥" },
        { href: "/suppliers.html", label: "供应商", ico: "🏭" },
        { href: "/carriers.html", label: "承运商", ico: "🚚" },
      ]},
      { label: "仓储布局", ico: "🏗️", children: [
        { href: "/warehouses.html", label: "仓库", ico: "🏬" },
        { href: "/zones.html", label: "储区", ico: "🗺️" },
        { href: "/location_groups.html", label: "货位组", ico: "🧱" },
        { href: "/locations.html", label: "库位", ico: "📍" },
      ]},
      { label: "货品档案", ico: "📦", children: [
        { href: "/products.html", label: "物料", ico: "📦" },
        { href: "/product_types.html", label: "产品类型", ico: "🏷️" },
        { href: "/serials.html", label: "序列号台账", ico: "🔢" },
      ]},
      { label: "运力档案", ico: "🚛", children: [
        { href: "/drivers.html", label: "司机", ico: "🧑‍✈️" },
        { href: "/vehicles.html", label: "车辆", ico: "🚚" },
      ]},
    ]},
    { label: "仓储作业", ico: "🚚", children: [
      { label: "入库单据与作业", ico: "📥", children: [
        { href: "/asns.html", label: "收货", ico: "📥" },
        { href: "/asn_receive.html", label: "验收作业", ico: "✅" },
        { href: "/asn_putaway.html", label: "上架作业", ico: "🗳️" },
        { href: "/putaway_rules.html", label: "上架规则配置", ico: "⚙️" },
      ]},
      { label: "出库单据", ico: "📤", children: [
        { href: "/dns.html", label: "出库", ico: "📤" },
        { href: "/outbound_allocation.html", label: "波次分配", ico: "🧭" },
        { href: "/wave_analysis.html", label: "组波分析", ico: "↗" },
        { href: "/wave_jobs.html", label: "备货与补货作业", ico: "📦" },
        { href: "/dn_review.html", label: "出库复核", ico: "🔎" },
        { href: "/dn_order.html", label: "拆单合单", ico: "✂️" },
      ]},
      { label: "库存与追溯", ico: "📊", children: [
        { href: "/transfers.html", label: "移库", ico: "🔄" },
        { href: "/stocktakes.html", label: "盘点", ico: "🔍" },
        { href: "/stock.html", label: "库存账", ico: "📊" },
        { href: "/batches.html", label: "批次", ico: "🧬" },
        { href: "/stock_batch.html", label: "批次库存", ico: "🧾" },
        { href: "/stocktake_plans.html", label: "盘点方案", ico: "🗂️" },
        { href: "/stock_adjustments.html", label: "库存调整", ico: "⚖️" },
        { href: "/inventory_transfers.html", label: "批次调整", ico: "🔀" },
        { href: "/care.html", label: "养护管理", ico: "🌡️" },
      ]},
    ]},
    { label: "统计报表", ico: "📈", children: [
      { label: "合规与追溯", ico: "🛡️", children: [
        { href: "/gsp.html", label: "GSP 合规报表", ico: "📋" },
      ]},
      { label: "运营分析", ico: "📊", children: [
        { href: "/workload.html", label: "工作量统计", ico: "🧮" },
        { href: "/operation_report.html", label: "经营报表", ico: "💹" },
      ]},
    ]},
    { label: "打印中心", ico: "🖨️", children: [
      { label: "打印配置", ico: "🛠️", children: [
        { href: "/print_templates.html", label: "模板配置", ico: "📐" },
        { href: "/print_preview.html", label: "打印预览", ico: "🖨️" },
      ]},
    ]},
    { label: "系统管理", ico: "⚙️", children: [
      { label: "权限", ico: "🔑", children: [
        { href: "/roles.html", label: "角色", ico: "🔑" },
        { href: "/users.html", label: "用户", ico: "🧑" },
        { href: "/owner_warehouses.html", label: "货主仓库关系", ico: "🔗" },
      ]},
    ]},
  ];
  if (window.MWS_PREVIEW_PAGES) {
    const available = new Set(window.MWS_PREVIEW_PAGES);
    function previewNav(nodes) {
      return nodes.flatMap(node => {
        if (!node.children) return available.has(node.href) ? [node] : [];
        const children = previewNav(node.children);
        return children.length ? [{ ...node, children }] : [];
      });
    }
    NAV = previewNav(NAV);
  }

  // 预建索引：叶子扁平表、href → 祖先分组 id 链、全部分组 id
  const LEAVES = [];
  const ANCESTORS = {};
  const GROUP_IDS = [];
  (function index(nodes, prefix, anc) {
    nodes.forEach((n, i) => {
      const id = prefix ? prefix + "-" + i : "n" + i;
      if (n.children && n.children.length) {
        GROUP_IDS.push(id);
        index(n.children, id, anc.concat(id));
      } else {
        LEAVES.push({ id, href: n.href, label: n.label, ico: n.ico });
        ANCESTORS[n.href] = anc.slice();
      }
    });
  })(NAV, "", []);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function renderNodes(nodes, prefix) {
    return nodes.map((n, i) => {
      const id = prefix ? prefix + "-" + i : "n" + i;
      if (n.children && n.children.length) {
        return '<div class="nav-group-box collapsed" data-node="' + id + '">' +
          '<button type="button" class="nav-group-toggle" data-toggle="' + id +
            '" title="' + esc(n.label) + '" aria-expanded="false">' +
            '<span class="nav-ico">' + (n.ico || "📁") + '</span>' +
            '<span class="nav-label">' + esc(n.label) + '</span>' +
            '<span class="nav-arrow">▾</span>' +
          '</button>' +
          '<div class="nav-sub">' + renderNodes(n.children, id) + '</div>' +
        '</div>';
      }
      return '<a class="nav-item" href="' + esc(n.href) + '" data-href="' + esc(n.href) +
        '" title="' + esc(n.label) + '">' +
        '<span class="nav-ico">' + (n.ico || "•") + '</span>' +
        '<span class="nav-label">' + esc(n.label) + '</span></a>';
    }).join("");
  }

  const OVERRIDE_KEY = "miniwms_nav_override";
  function loadOverrides() {
    try {
      const raw = localStorage.getItem(OVERRIDE_KEY);
      if (!raw) return {};
      const obj = JSON.parse(raw);
      return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : {};
    } catch (e) { return {}; }
  }
  function saveOverrides(o) {
    try { localStorage.setItem(OVERRIDE_KEY, JSON.stringify(o)); } catch (e) {}
  }

  /**
   * 挂载导航。
   * opts.mode    "link"（独立页，叶子直接跳转）| "tabs"（工作台，叶子回调 onLeaf）
   * opts.onLeaf  (label, href) => void
   * opts.beforeToggle () => boolean|void，返回 false 则不处理本次折叠（工作台收起态先展开侧栏）
   * opts.activeHref 初始高亮/展开的页面
   * 返回 { reveal(url), openIds() }
   */
  function mount(el, opts) {
    if (!el) return null;
    opts = opts || {};
    const mode = opts.mode === "tabs" ? "tabs" : "link";
    const overrides = loadOverrides();
    let currentHref = opts.activeHref || (mode === "link" ? location.pathname : "");

    el.innerHTML = renderNodes(NAV, "");

    function apply(url) {
      currentHref = url || currentHref;
      const base = String(currentHref).split("?")[0];
      const anc = ANCESTORS[base] || [];
      const open = new Set(anc.length ? anc : [GROUP_IDS[0]]);
      Object.keys(overrides).forEach((id) => {
        if (overrides[id]) open.add(id); else open.delete(id);
      });
      el.querySelectorAll(".nav-group-box").forEach((box) => {
        const isOpen = open.has(box.dataset.node);
        box.classList.toggle("collapsed", !isOpen);
        const btn = box.querySelector("[data-toggle]");
        if (btn) btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      });
      el.querySelectorAll(".nav-item").forEach((a) => {
        const active = a.dataset.href === base;
        a.classList.toggle("active", active);
        if (active) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
      });
      return open;
    }

    el.querySelectorAll("[data-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (opts.beforeToggle && opts.beforeToggle() === false) return;
        const box = btn.closest(".nav-group-box");
        const willOpen = box.classList.contains("collapsed");
        if (willOpen) {
          // 展开子级时顺带展开祖先链，避免被折叠的父级挡住
          let p = box.parentElement && box.parentElement.closest(".nav-group-box");
          while (p) {
            overrides[p.dataset.node] = 1;
            p = p.parentElement && p.parentElement.closest(".nav-group-box");
          }
        }
        overrides[box.dataset.node] = willOpen ? 1 : 0;
        saveOverrides(overrides);
        apply(currentHref);
      });
    });

    el.querySelectorAll(".nav-item").forEach((a) => {
      const href = a.dataset.href;
      const entry = new URLSearchParams({ open: href });
      if (mode === "link" && location.pathname !== href) {
        entry.set("from", location.pathname + location.search);
      }
      a.href = "/app.html?" + entry;
      a.addEventListener("click", (e) => {
        if (mode !== "tabs" || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        const label = a.querySelector(".nav-label").textContent;
        if (typeof opts.onLeaf === "function") opts.onLeaf(label, href);
      });
    });

    // 打开/访问某页时，其祖先链强制可见（清掉之前的「手动收起」覆盖），其余分支保持用户设置
    function reveal(url) {
      const base = String(url || "").split("?")[0];
      (ANCESTORS[base] || []).forEach((id) => { delete overrides[id]; });
      saveOverrides(overrides);
      return apply(url);
    }

    apply(currentHref);
    return {
      reveal: reveal,
      openIds: () => Object.keys(overrides).filter((id) => overrides[id]).concat(
        ANCESTORS[String(currentHref).split("?")[0]] || [])
    };
  }

  window.MWS_NAV = NAV;
  window.MWS_NAV_FLAT = LEAVES.slice();
  window.MWS_NAV_TOOLS = {
    mount: mount,
    ancestorsOf: (href) => (ANCESTORS[href] || []).slice(),
    groupIds: GROUP_IDS.slice(),
    leaves: LEAVES.slice()
  };

  // 内嵌模式：页面被工作台 iframe 加载时（?embed=1）隐藏自身侧边栏，仅保留内容
  const EMBED = new URLSearchParams(location.search).get("embed") === "1";

  function render() {
    const el = document.getElementById("sidebar");
    if (!el) return;
    if (EMBED) {
      el.style.display = "none";
      document.body.classList.add("embed");
      document.querySelectorAll("[data-embed-hide]").forEach((n) => { n.style.display = "none"; });
      return;
    }
    const token = (function () {
      try { return localStorage.getItem("miniwms_token"); } catch (e) { return ""; }
    })();
    let html = '<div class="brand"><span class="logo">📦</span>MiniWMS</div>' +
      '<div class="wh-pick" id="whPickBox"></div>' +
      '<nav class="nav" id="sideNav"></nav>';
    if (token) {
      html += '<a class="nav-item" href="#" onclick="logout();return false;"><span class="nav-ico">🚪</span><span>退出登录</span></a>';
    } else if (!window.MWS_PREVIEW_API && location.pathname !== "/login.html") {
      html += '<a class="nav-item" href="/login.html"><span class="nav-ico">🔐</span><span>登录</span></a>';
    }
    html += '<div class="side-foot">轻量多货主 WMS · 本地演示</div>';
    el.innerHTML = html;
    mount(el.querySelector("#sideNav"), { mode: "link", activeHref: location.pathname });
    // 独立页侧栏也有当前仓库选择器；工作台只在顶栏放一个（iframe 内是 embed 模式，不重复渲染）
    if (typeof mountWarehousePicker === "function") {
      mountWarehousePicker(el.querySelector("#whPickBox"));
    }
  }

  // 等待 DOM 就绪（脚本一般在 body 末尾，通常已就绪）
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
