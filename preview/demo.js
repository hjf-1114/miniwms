// Fictional fixtures for the public preview, independent of the WMS database.
const orders = [
  { code: "DEMO-PA-01", name: "同批次测试货品", sku: "DEMO-PA-LOT", placed: 0, batches: [
    ["PA-LOT-01", 4, 0, "PA-A-01"], ["PA-LOT-02", 6, 0, "PA-A-02"]
  ] },
  { code: "DEMO-PA-02", name: "隔离测试货品", sku: "DEMO-PA-NG", placed: 0, batches: [
    ["PA-NG-01", 5, 0, "PA-NG-01"]
  ] },
  { code: "DEMO-PA-03", name: "虚拟单测试货品", sku: "DEMO-PA-VIRT", placed: 2, batches: [
    ["PA-VIRT-01", 5, 2, "PA-VIRT-01"]
  ] },
  { code: "DEMO-PA-05", name: "冷链测试货品", sku: "DEMO-PA-COLD", placed: 5, batches: [
    ["PA-COLD-01", 5, 5, "PA-COLD-01"]
  ] },
  { code: "DEMO-PA-06", name: "穿梭库测试货品", sku: "DEMO-PA-SH", placed: 0, batches: [
    ["PA-SH-01", 5, 0, "PA-SH-01"]
  ] }
];

const stock = [
  ["DEMO-PA-LOT", "同批次测试货品", "PA-LOT-01", "PA-A-01", "合格", 3],
  ["DEMO-PA-VIRT", "虚拟单测试货品", "PA-VIRT-01", "PA-VIRT-01", "合格", 2],
  ["DEMO-PA-COLD", "冷链测试货品", "PA-COLD-01", "PA-COLD-01", "合格", 5]
];

const rules = [
  [10, "冷链分流", "冷链标识为是", ["指定冷藏储区"], "只在冷藏储区推荐，人工选择常温货位时拦截。"],
  [20, "不合格隔离", "货品属性为不合格", ["指定隔离储区"], "不合格品推荐至隔离区，人工选择其他储区时拦截。"],
  [30, "虚拟分流", "虚拟标识为是", ["虚拟储区空位"], "在虚拟储区内查找空位。"],
  [50, "同批号与附近空位", "同批次测试货品", ["同委托方、同品、同厂家批号货位", "同品同巷道空位", "货品默认储区空位"], "例如 PA-LOT-01 优先推荐 PA-A-01。找不到可用同批库存时，按同仓、同储区、同巷道找空位。"],
  [60, "指定穿梭库货位组", "外部单据类别为 DEMO-PA-SHUTTLE", ["指定货位组"], "匹配指定类别时，在 PA-SHUTTLE 组内查找。"],
  [999, "空位兜底", "不限", ["指定兜底储区空位"], "前面的规则都未命中时，尝试在兜底储区查找空位。"]
];

const get = id => document.getElementById(id);
const query = get("query");
const status = get("status");
const table = get("results");
const detail = get("detail");
const tabs = Array.from(document.querySelectorAll("[data-view]"));
let view = "orders";

orders.forEach(order => {
  order.quantity = order.batches.reduce((sum, batch) => sum + batch[1], 0);
  order.state = order.placed === order.quantity ? "已完成" : order.placed ? "上架中" : "待上架";
});

const totals = [
  ["演示入库单", orders.length],
  ["待上架件数", orders.reduce((sum, order) => sum + order.quantity - order.placed, 0)],
  ["库存批次", stock.length]
];
get("summary").innerHTML = totals.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");

function tableContents(headers, rows) {
  return `<thead><tr>${headers.map(x => `<th scope="col">${x}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("") ||
      `<tr><td colspan="${headers.length}" class="empty">没有符合条件的记录</td></tr>`}</tbody>`;
}

function detailButton(type, index, label) {
  return `<button type="button" data-type="${type}" data-index="${index}">${label}</button>`;
}

function render() {
  const term = query.value.trim().toLowerCase();
  const matches = fields => fields.some(value => String(value).toLowerCase().includes(term));
  let headers;
  let rows;
  if (view === "orders") {
    headers = ["入库单号", "货品", "状态", "已点收", "已验收", "已上架", "待上架"];
    rows = orders.flatMap((order, index) => {
      if (!matches([order.code, order.name, order.sku]) || (status.value && order.state !== status.value)) return [];
      const tone = order.state === "已完成" ? "done" : order.state === "上架中" ? "progress" : "";
      return [[detailButton("order", index, order.code), order.name, `<span class="badge ${tone}">${order.state}</span>`,
        order.quantity, order.quantity, order.placed, order.quantity - order.placed]];
    });
  } else if (view === "stock") {
    headers = ["物料编码", "货品", "厂家批号", "货位", "货品属性", "在库数量"];
    rows = stock.filter(matches);
  } else {
    headers = ["优先级", "规则", "适用条件", "第一步"];
    rows = rules.flatMap(([priority, name, condition, steps], index) =>
      matches([name, condition, ...steps]) ? [[priority, detailButton("rule", index, name), condition, steps[0]]] : []);
  }
  table.innerHTML = tableContents(headers, rows);
  get("result-count").textContent = `${rows.length} 条记录`;
}

tabs.forEach(tab => tab.addEventListener("click", () => {
  view = tab.dataset.view;
  tabs.forEach(button => button.setAttribute("aria-pressed", String(tab === button)));
  query.value = "";
  query.placeholder = { orders: "搜索单号或货品", stock: "搜索货品、批号或货位", rules: "搜索规则或适用条件" }[view];
  get("status-label").hidden = view !== "orders";
  render();
}));

query.addEventListener("input", render);
status.addEventListener("change", render);
table.addEventListener("click", event => {
  const button = event.target.closest("button[data-index]");
  if (!button) return;
  const index = Number(button.dataset.index);
  if (button.dataset.type === "order") {
    const order = orders[index];
    get("detail-title").textContent = order.code;
    get("detail-content").innerHTML = `<p>${order.name} · ${order.sku} · ${order.state}</p>
      <div class="table-wrap"><table>${tableContents(
        ["厂家批号", "已验收", "已上架", "待上架", "推荐货位"],
        order.batches.map(([lot, qty, placed, loc]) => [lot, qty, placed, qty - placed, loc])
      )}</table></div>`;
  } else {
    const [priority, name, condition, steps, note] = rules[index];
    get("detail-title").textContent = name;
    get("detail-content").innerHTML = `<p>优先级 ${priority} · ${condition}</p>
      <ol>${steps.map(step => `<li>${step}</li>`).join("")}</ol><p>${note}</p>`;
  }
  detail.showModal();
});

render();
