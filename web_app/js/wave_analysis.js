const filters = document.getElementById("filters");
const settingsForm = document.getElementById("settings");
const stats = document.getElementById("stats");
const channels = document.getElementById("channels");
const comparisonGroups = document.getElementById("comparisonGroups");
const comparisonResults = document.getElementById("comparisonResults");
const notice = document.getElementById("msg");
let customers = [];
let groups = [];
let snapshot;
let requestVersion = 0;

const kindLabels = {activity:"单品活动组", short:"短通道", full:"全量组", special:"全量组 · 人工处理"};
const read = form => Object.fromEntries(new FormData(form));
const options = (rows, label) => rows.map(row => new Option(label(row), row.id));
const empty = text => '<p class="muted">' + esc(text) + '</p>';

function openWaveJobs(plan) {
  const query = new URLSearchParams({owner_id: filters.elements.owner_id.value});
  if (plan) query.set("plan", plan);
  openWorkTab("备货与补货作业", "/wave_jobs.html?" + query);
}
document.getElementById("openWaveJobs").onclick = () => openWaveJobs();

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
  query.set("include_plans", "0");
  const data = await api("GET", "/api/outbound/group-analysis?" + query);
  if (version !== requestVersion) return;
  snapshot = data;
  const {result, settings} = data;
  const selectedGroups = Array.from(comparisonGroups.querySelectorAll("input:checked"), field => +field.value);
  comparisonGroups.innerHTML = groups.map(row => `<label><input type="checkbox" value="${row.id}"
    ${(selectedGroups.length ? selectedGroups.includes(row.id) : settings.pool_group_id === row.id) ? "checked" : ""}>
    ${esc(row.code)} · ${esc(row.name)}</label>`).join("");
  comparisonResults.replaceChildren();
  const pct = result.remaining ? (100 * result.short.orders.length / result.remaining).toFixed(1) : "0";
  stats.textContent = `${result.history ? "历史已发货" : "待分析"} ${result.total} 单 · 短通道覆盖 ${result.short.orders.length}/${result.remaining}（${pct}%）`;
  channels.replaceChildren(...[
    ...result.activity.map(item => ["activity", item]), ["short", result.short],
    ["full", result.full], ["special", result.special],
  ].map(([kind, item]) => renderChannel(kind, item, result.history)));
  for (const name of ["full_group_id", "pool_group_id", "cold_locations", "hazard_locations", "special_types"]) {
    settingsForm.elements[name].value = settings[name] || "";
  }
}

function renderChannel(kind, item, history) {
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
        ${esc(order.code)} · ${esc(order.customer_name || "—")} · ${esc(order.skus.join(" + "))}</label></div>`).join("")}</details>`;
  if (kind === "activity" || kind === "short") {
    const compare = document.createElement("button");
    compare.className = "btn";
    compare.textContent = "试算短通道方案";
    compare.onclick = () => perform(compare, async () => {
      const group_ids = Array.from(comparisonGroups.querySelectorAll("input:checked"), field => +field.value);
      if (group_ids.length < 1 || group_ids.length > 3) throw new Error("请选择 1 至 3 个固定货位组");
      const dn_ids = Array.from(card.querySelectorAll(".order-selection input:checked"), field => +field.value);
      if (!dn_ids.length) throw new Error("请选择参与比较的订单");
      const planning_source = history ? "history" : "orders";
      const owner_id = filters.elements.owner_id.value;
      const version = requestVersion;
      const {comparisons} = await api("POST", "/api/outbound/group-analysis/compare",
        {owner_id, group_ids, dn_ids, planning_source});
      if (version !== requestVersion) return;
      renderComparison(comparisons, {owner_id, planning_source, kind});
      document.getElementById("comparison").scrollIntoView({block:"start"});
    });
    card.append(compare);
  }
  if (history) return card;
  if (kind === "special") {
    card.innerHTML += empty("由操作员在出库明细手工分配合规的全量货位");
    return card;
  }
  const form = document.createElement("form");
  const temporary = kind !== "full";
  form.innerHTML = `
    ${temporary ? `<p><label>补货方式 <select name="replenishment_mode"><option value="order">按订单数量</option><option value="case">按整箱</option></select></label>
    <button type="button" class="help-tip" data-tip="例如订单需 13 件、每箱 6 件。按订单补 13 件，按整箱补 3 箱共 18 件，多出的 5 件留在拣货位。" aria-label="例如订单需 13 件、每箱 6 件。按订单补 13 件，按整箱补 3 箱共 18 件，多出的 5 件留在拣货位。">?</button></p>` : ""}
    <div class="faces">${temporary ? item.products.map((product, index) => `<div>
      <label>${esc(product.sku)} · 需求 ${product.qty} 件 <select name="${product.product_id}">
        <option value="">选择空拣货位</option>
        ${snapshot.locations.map((loc,i) => `<option value="${loc.id}" ${i === index ? "selected" : ""}>
          ${esc(loc.code)}${loc.capacity ? ` · 容量 ${loc.capacity}` : ""}</option>`).join("")}
      </select></label>
      <label data-case hidden>箱规 <select name="case_${product.product_id}">
        <option value="">选择每箱件数</option>${product.packages.map(pkg =>
          `<option value="${pkg.id}">${esc(pkg.pkg_level)} · ${pkg.qty_per_package} 件/箱</option>`).join("")}
      </select></label></div>`).join("") : ""}</div>
    <p><button class="btn sm">${temporary ? "确认布置并生成补货" : "确认全量组方案"}</button>
    ${temporary ? '<button class="btn secondary sm" type="button" data-full>所选订单走全量组</button>' : ""}</p>`;
  function selectedOrders() {
    return Array.from(card.querySelectorAll(".order-selection input:checked"), input => Number(input.value));
  }
  const replenishmentMode = form.elements.replenishment_mode;
  if (replenishmentMode) replenishmentMode.onchange = () => {
    form.querySelectorAll("[data-case]").forEach(label => { label.hidden = replenishmentMode.value !== "case"; });
  };
  const fullButton = form.querySelector("[data-full]");
  if (fullButton) fullButton.onclick = () => perform(fullButton, async () => {
    const dn_ids = selectedOrders();
    if (!dn_ids.length) throw new Error("请先勾选要改走全量组的订单");
    if (!confirm(`确认将勾选的 ${dn_ids.length} 张订单单独建成全量组方案？`)) return;
    await api("POST", "/api/outbound/group-analysis", {
      owner_id: filters.elements.owner_id.value, kind: "full", dn_ids,
    });
    showMsg(notice, "已生成全量组方案，可单独执行分配", "success");
    await analyze();
  });
  form.onsubmit = event => {
    event.preventDefault();
    perform(event.submitter, async () => {
      const dn_ids = selectedOrders();
      if (!dn_ids.length) throw new Error("请至少保留一张订单");
      const selected = item.orders.filter(order => dn_ids.includes(order.id));
      const skus = new Set(selected.flatMap(order => order.skus));
      const selectedProducts = item.products.filter(p => skus.has(p.sku) && temporary);
      const faces = Object.fromEntries(selectedProducts.map(p => [p.product_id, form.elements.namedItem(String(p.product_id)).value]));
      if (Object.values(faces).some(value => !value)) throw new Error("请选择各 SKU 的目标拣货位");
      const mode = replenishmentMode?.value || "order";
      const case_packages = mode === "case" ? Object.fromEntries(selectedProducts
        .map(p => [p.product_id, form.elements.namedItem("case_" + p.product_id).value])) : {};
      if (Object.values(case_packages).some(value => !value)) throw new Error("请选择各 SKU 的箱规，箱规可在商品档案中维护");
      const {plan} = await api("POST", "/api/outbound/group-analysis", {
        owner_id: filters.elements.owner_id.value, kind, dn_ids, faces, replenishment_mode: mode, case_packages,
      });
      showMsg(notice, plan.transfer_code ? `已生成方案 ${plan.layout_code} 和补货单 ${plan.transfer_code}` :
        `已确认 ${plan.layout_code || plan.group_code}`, "success");
      await analyze();
      openWaveJobs(plan.id);
    });
  };
  card.append(form);
  return card;
}

function renderComparison(rows, context) {
  if (!rows.length) {
    comparisonResults.innerHTML = empty("所选货位组没有可分析的拣货位");
    return;
  }
  const historical = context.planning_source === "history";
  comparisonResults.innerHTML = `<table><thead><tr><th>货位组 / 方案</th><th>组合覆盖</th>
    <th>${historical ? "按现货回放" : "当前可齐套"}</th><th>${historical ? "备货后回放" : "补货后预计齐套"}</th>
    <th>计划搬运件数</th><th>货位布置和异常</th><th>操作</th></tr></thead><tbody>
    ${rows.map((row,index) => `<tr><td>${esc(row.group_code)} · ${esc(row.strategy)}</td>
      <td>${row.covered} 单</td><td>${row.ready} 单</td><td>${row.after ?? "—"}</td><td>${row.move_qty ?? "—"}</td>
      <td><details><summary>查看 SKU 与货位</summary>
      ${row.details.map(item => `<div>${esc(item.sku)} → ${esc(item.location)} ·
      ${item.clear_qty ? `先清 ${item.clear_qty} 件` : item.reuse ? "原位留用" : "空位"}
      ${item.move_qty == null ? "" : `· 补 ${item.move_qty} 件`}</div>`).join("")}
      ${row.blocked_locations.length ? `<p>仍有其他货品的货位 ${esc(row.blocked_locations.join("、"))}，需调整时先清位。</p>
        <button type="button" class="btn secondary sm" data-open-transfer>打开移库作业</button>` : ""}
      </details>${row.issue ? empty(row.issue) : ""}</td>
      <td>${row.covered && !row.issue ? `<button class="btn sm" data-confirm-layout="${index}">${historical ? "生成备货方案" : "确认并生成作业"}</button>` : "—"}</td></tr>`).join("")}
    </tbody></table>`;
  comparisonResults.querySelectorAll("[data-open-transfer]").forEach(button => {
    button.onclick = () => openWorkTab("移库", "/transfers.html");
  });
  comparisonResults.querySelectorAll("[data-confirm-layout]").forEach(button => {
    button.onclick = () => perform(button, async () => {
      const row = rows[+button.dataset.confirmLayout];
      const {plan} = await api("POST", "/api/outbound/group-analysis", {
        ...context, base_group_id: row.group_id, faces: row.faces, dn_ids: row.dn_ids,
      });
      showMsg(notice, `已确认方案 ${plan.layout_code || plan.group_code}`, "success");
      await analyze();
      openWaveJobs(plan.id);
    });
  });
}

filters.addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, analyze);
});
filters.elements.owner_id.addEventListener("change", () => {
  refreshCustomers();
  perform(filters.querySelector('[type="submit"]'), analyze);
});
filters.elements.order_status.addEventListener("change", () => {
  document.getElementById("date-label").textContent = filters.elements.order_status.value === "shipped" ? "发货自" : "创建自";
  perform(filters.querySelector('[type="submit"]'), analyze);
});
settingsForm.addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, async () => {
    await api("PUT", "/api/outbound/group-analysis/settings", read(settingsForm));
    showMsg(notice, "全量组设置已保存", "success");
    await analyze();
  });
});

perform(filters.querySelector('[type="submit"]'), async () => {
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
