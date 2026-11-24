const filters = document.getElementById("filters");
const settingsForm = document.getElementById("settings");
const stats = document.getElementById("stats");
const channels = document.getElementById("channels");
const plans = document.getElementById("plans");
const notice = document.getElementById("msg");
let customers = [];
let groups = [];
let snapshot;
let requestVersion = 0;

const kindLabels = {activity:"单品活动组", short:"短通道", full:"全量组", special:"全量组 · 人工处理"};
const statusLabels = {pending:"待补货", ready:"可尝试分配", allocated:"已分配", cancelled:"已撤销或结案"};
const read = form => Object.fromEntries(new FormData(form));
const options = (rows, label) => rows.map(row => new Option(label(row), row.id));
const empty = text => '<p class="muted">' + esc(text) + '</p>';

async function perform(button, callback) {
  button.disabled = true;
  try { await callback(); }
  catch (error) {
    showMsg(notice, error.message, "error");
    notice.scrollIntoView({block:"nearest"});
  } finally { button.disabled = false; }
}

function refreshCustomers() {
  filters.elements.customer_id.replaceChildren(new Option("全部", ""), ...options(
    customers.filter(row => +row.owner_id === +filters.elements.owner_id.value), row => row.name));
}

async function analyze() {
  const version = ++requestVersion;
  const query = new URLSearchParams(read(filters));
  if (!query.get("owner_id")) return;
  const data = await api("GET", "/api/outbound/group-analysis?" + query);
  if (version !== requestVersion) return;
  snapshot = data;
  const {result, settings} = data;
  const pct = result.remaining ? (100 * result.short.orders.length / result.remaining).toFixed(1) : "0";
  stats.textContent = `待分析 ${result.total} 单 · 短通道覆盖 ${result.short.orders.length}/${result.remaining}（${pct}%）`;
  channels.replaceChildren(...[
    ...result.activity.map(item => ["activity", item]), ["short", result.short],
    ["full", result.full], ["special", result.special],
  ].map(([kind, item]) => renderChannel(kind, item)));
  plans.replaceChildren(...data.plans.map(renderPlan));
  if (!data.plans.length) plans.innerHTML = empty("暂无已确认方案");
  for (const name of ["full_group_id", "pool_group_id", "cold_locations", "hazard_locations", "special_types"]) {
    settingsForm.elements[name].value = settings[name] || "";
  }
}

function renderChannel(kind, item) {
  const card = document.createElement("article");
  card.className = "channel";
  card.innerHTML = `
    <h3>${kindLabels[kind]} <span class="muted">${item.orders.length} 单</span></h3>
    <p>${item.products.length} 个 SKU</p>`;
  if (!item.orders.length) return card;
  card.innerHTML += `
    <details><summary>SKU 组合</summary>${item.combinations.map(combo =>
      `<div>${esc(combo.skus.join(" + "))} · ${combo.count} 单</div>`).join("")}</details>
    <details class="order-selection"><summary>订单明细</summary>${item.orders.map(order =>
      `<div><label>${kind === "special" ? "" : `<input type="checkbox" value="${order.id}" checked>`}
        ${esc(order.code)} · ${esc(order.skus.join(" + "))}</label></div>`).join("")}</details>`;
  if (kind === "special") {
    card.innerHTML += empty("由操作员在出库明细手工分配合规的全量货位");
    return card;
  }
  const form = document.createElement("form");
  const temporary = kind !== "full";
  form.innerHTML = `
    <div class="faces">${temporary ? item.products.map((product, index) => `<div>
      <label>${esc(product.sku)} · ${product.qty} 件 <select name="${product.product_id}" required>
        <option value="">选择空拣货位</option>
        ${snapshot.locations.map((loc,i) => `<option value="${loc.id}" ${i === index ? "selected" : ""}>
          ${esc(loc.code)}${loc.capacity ? ` · 容量 ${loc.capacity}` : ""}</option>`).join("")}
      </select></label></div>`).join("") : ""}</div>
    <p><button class="btn sm">${temporary ? "生成货位组与补货单" : "确认全量组方案"}</button></p>`;
  form.onsubmit = event => {
    event.preventDefault();
    perform(event.submitter, async () => {
      const dn_ids = Array.from(card.querySelectorAll(".order-selection input:checked"), input => Number(input.value));
      if (!dn_ids.length) throw new Error("请至少保留一张订单");
      const selected = item.orders.filter(order => dn_ids.includes(order.id));
      const skus = new Set(selected.flatMap(order => order.skus));
      const faces = Object.fromEntries(item.products.filter(p => skus.has(p.sku) && temporary)
        .map(p => [p.product_id, form.elements.namedItem(String(p.product_id)).value]));
      const {plan} = await api("POST", "/api/outbound/group-analysis", {
        owner_id: filters.elements.owner_id.value, kind, dn_ids, faces,
      });
      showMsg(notice, `已生成 ${plan.transfer_code || plan.group_code}`, "success");
      await analyze();
    });
  };
  card.append(form);
  return card;
}

function moveTable(plan, editable) {
  return `<table><thead><tr><th>来源位</th><th>目标位</th><th>商品</th><th>批次</th><th>效期</th><th>计划</th><th>实到</th></tr></thead>
    <tbody>${plan.moves.map(row => `<tr><td>${esc(row.source)}</td><td>${esc(row.target)}</td>
      <td>${esc(row.sku)} · ${esc(row.product_name)}</td><td>${esc(row.batch_no)}</td><td>${esc(row.expiry_date || "—")}</td>
      <td>${row.qty}</td><td>${editable ? `<input class="actual" type="number" step="any" min="0" max="${row.qty}"
          value="${row.qty}" data-row="${row.id}" aria-label="实到量" required>` : (row.actual_qty ?? "")}</td></tr>`).join("")}</tbody></table>`;
}

function renderPlan(plan) {
  const card = document.createElement("article");
  card.className = "plan";
  card.innerHTML = `<h3>${kindLabels[plan.kind]} · ${esc(plan.group_code)} <span class="muted">${statusLabels[plan.status]}</span></h3>
    <p>${plan.orders.length} 单 ${plan.transfer_code ? "· 移库 " + esc(plan.transfer_code) : ""}
      ${plan.wave_no ? "· 波次 " + esc(plan.wave_no) : ""}</p>
    <details><summary>订单和商品货位</summary>
      <p>${plan.orders.map(row => esc(row.code)).join("、")}</p>
      <p>${plan.faces.map(row => `${esc(row.sku)} → ${esc(row.code)}`).join("、")}</p></details>
    ${plan.moves.length ? `<div class="table-wrap">${moveTable(plan, plan.status === "pending")}</div>` : ""}
    <div class="toolbar">
      ${plan.moves.length ? '<button class="btn secondary sm" data-step="print">打印移库单</button>' : ""}
      ${plan.status === "pending" ? '<button class="btn sm" data-step="finish">确认实到并完成补货</button>' : ""}
      ${plan.status === "ready" ? '<button class="btn sm" data-step="release">执行整波分配</button>' : ""}
      ${plan.status !== "cancelled" ? `<button class="btn secondary sm" data-step="cancel">${plan.status === "allocated" ? "结案或释放方案" : "撤销并重新分析"}</button>` : ""}
    </div>`;
  card.onclick = event => {
    const button = event.target.closest("[data-step]");
    if (!button) return;
    const step = button.dataset.step;
    if (step === "print") {
      document.getElementById("paper").innerHTML = `<h1>波次补货移库单 ${esc(plan.transfer_code)}</h1>
        <p>货位组 ${esc(plan.group_code)} · ${plan.orders.length} 单</p>${moveTable(plan, false)}
        <p>搬运人 __________________　复核人 __________________</p>`;
      window.print();
      return;
    }
    if (step === "finish") {
      const inputs = Array.from(card.querySelectorAll(".actual"));
      if (inputs.some(input => !input.reportValidity())) return;
      if (!confirm("请确认现场实物已搬到目标货位，数量按页面实到量过账。")) return;
    }
    if (step === "cancel" && !confirm("确认解除方案？已过账的货物仍保留在实物所在位置。")) return;
    perform(button, async () => {
      const body = step === "finish" ? {actuals:Object.fromEntries(
        Array.from(card.querySelectorAll(".actual"), input => [input.dataset.row, +input.value]))} : {};
      await api("POST", `/api/outbound/group-analysis/${plan.id}/${step}`, body);
      showMsg(notice, {finish:"补货已按实到过账", release:"整波已分配", cancel:"订单已解除方案"}[step], "success");
      await analyze();
    });
  };
  return card;
}

filters.addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, analyze);
});
filters.elements.owner_id.addEventListener("change", () => {
  refreshCustomers();
  perform(filters.querySelector("button"), analyze);
});
settingsForm.addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, async () => {
    await api("PUT", "/api/outbound/group-analysis/settings", read(settingsForm));
    showMsg(notice, "全量组设置已保存", "success");
    await analyze();
  });
});

perform(filters.querySelector("button"), async () => {
  const [owners, customerRows, context] = await Promise.all([
    api("GET", "/api/owners?page_size=100"), api("GET", "/api/customers?page_size=100"),
    api("GET", "/api/scope/context"),
  ]);
  customers = customerRows.customers;
  const response = await api("GET", "/api/location-groups?status=active&page_size=100&warehouse_id=" + context.current_warehouse_id);
  groups = response.location_groups;
  settingsForm.elements.full_group_id.replaceChildren(new Option("请选择", ""), ...options(groups, row => row.name));
  settingsForm.elements.pool_group_id.replaceChildren(new Option("未设置", ""), ...options(groups, row => row.name));
  filters.elements.owner_id.append(...options(owners.owners, row => row.name));
  if (owners.owners.length === 1) {
    filters.elements.owner_id.value = owners.owners[0].id;
    refreshCustomers();
    await analyze();
  }
});
