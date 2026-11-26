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
const statusLabels = {pending:"待补货", short:"补货差异", ready:"可尝试分配", allocated:"已分配", cancelled:"已撤销或结案"};
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
    <p><button class="btn sm">${temporary ? "生成货位组与补货单" : "确认全量组方案"}</button>
    ${temporary ? '<button class="btn secondary sm" type="button" data-full>所选订单走全量组</button>' : ""}</p>`;
  function selectedOrders() {
    return Array.from(card.querySelectorAll(".order-selection input:checked"), input => Number(input.value));
  }
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
  return `<table><thead><tr><th>来源位</th><th>目标位</th><th>商品</th><th>批次</th><th>效期</th><th>计划</th><th>累计实到</th><th>待补</th>${editable ? "<th>本次实到</th>" : ""}</tr></thead>
    <tbody>${plan.moves.map(row => {
      const remaining = Math.max(0, +(row.qty - (row.actual_qty || 0)).toFixed(9));
      return `<tr><td>${esc(row.source)}</td><td>${esc(row.target)}</td>
      <td>${esc(row.sku)} · ${esc(row.product_name)}</td><td>${esc(row.batch_no)}</td><td>${esc(row.expiry_date || "—")}</td>
      <td>${row.qty}</td><td>${row.actual_qty ?? "—"}</td><td>${remaining}</td>
      ${editable ? `<td>${remaining > 1e-8 ? `<input class="actual" type="number" step="any" min="0" max="${remaining}"
        value="${remaining}" data-row="${row.id}" aria-label="本次实到量" required>` : "—"}</td>` : ""}</tr>`;
    }).join("")}</tbody></table>`;
}

function pickTable(data, interactive) {
  return `<h3>波次拣货单 ${esc(data.wave_no)}</h3>
    <table><thead><tr><th>货位</th><th>商品</th><th>批次 / 效期</th><th>分配 / 已拣</th><th>对应订单</th></tr></thead>
    <tbody>${data.rows.map(row => {
      const assigned = row.orders.reduce((sum, order) => sum + order.qty, 0);
      const picked = row.orders.reduce((sum, order) => sum + (order.confirmed_qty || 0), 0);
      return `<tr><td>${esc(row.location)}</td><td>${esc(row.sku)} · ${esc(row.name)}</td>
        <td>${esc(row.batch_no || "—")} / ${esc(row.expiry_date || "—")}</td><td>${assigned} / ${picked}</td>
        <td>${row.orders.map(order => `<div>${interactive ? `<button class="btn secondary sm" type="button" data-dn="${order.dn_id}">${esc(order.order_code)}</button>` : esc(order.order_code)}
           · ${order.confirmed_qty || 0}/${order.qty}</div>`).join("")}</td></tr>`;
    }).join("") || '<tr><td colspan="5">该波次没有有效分配明细</td></tr>'}</tbody></table>`;
}

function renderPlan(plan) {
  const card = document.createElement("article");
  card.className = "plan";
  const canReceive = ["pending", "short"].includes(plan.status);
  card.innerHTML = `<h3>${kindLabels[plan.kind]} · ${esc(plan.group_code)} <span class="muted">${statusLabels[plan.status]}</span></h3>
    <p>${plan.orders.length} 单 ${plan.transfer_code ? "· 移库 " + esc(plan.transfer_code) : ""}
      ${plan.wave_no ? "· 波次 " + esc(plan.wave_no) : ""}</p>
    <details><summary>订单和商品货位</summary>
      <p>${plan.orders.map(row => esc(row.code)).join("、")}</p>
      <p>${plan.faces.map(row => `${esc(row.sku)} → ${esc(row.code)}`).join("、")}</p></details>
    ${plan.moves.length ? `<div class="table-wrap">${moveTable(plan, canReceive)}</div>` : ""}
    ${plan.shortage_error ? empty(plan.shortage_error) : ""}
    ${plan.shortages?.length ? `<details open><summary>当前缺货 ${plan.shortages.length} 行</summary><ul>
      ${plan.shortages.map(row => `<li>${esc(row.dn_code)} · ${esc(row.sku)} · 缺 ${row.short_qty}</li>`).join("")}</ul></details>` : ""}
    <div class="toolbar">
      ${plan.moves.length ? '<button class="btn secondary sm" data-step="print">打印移库单</button>' : ""}
      ${canReceive ? `<button class="btn sm" data-step="finish">${plan.status === "short" ? "确认本次续补实到" : "确认实到并完成补货"}</button>` : ""}
      ${plan.status === "ready" ? '<button class="btn sm" data-step="release">执行整波分配</button>' : ""}
      ${plan.status === "allocated" ? '<button class="btn sm" data-step="picks">查看波次拣货</button> <button class="btn secondary sm" data-step="print-picks">打印拣货单</button>' : ""}
      ${plan.status !== "cancelled" ? `<button class="btn secondary sm" data-step="cancel">${plan.status === "allocated" ? "结案或释放方案" : "撤销并重新分析"}</button>` : ""}
    </div><div class="table-wrap pick-list"></div>`;
  card.onclick = event => {
    const order = event.target.closest("[data-dn]");
    if (order) {
      openWorkTab(order.textContent, "/dns.html?detail=" + order.dataset.dn);
      return;
    }
    const button = event.target.closest("[data-step]");
    if (!button) return;
    const step = button.dataset.step;
    if (step === "picks" || step === "print-picks") {
      perform(button, async () => {
        const data = await api("GET", `/api/outbound/group-analysis/${plan.id}/picking-list`);
        if (step === "picks") card.querySelector(".pick-list").innerHTML = pickTable(data, true);
        else {
          document.getElementById("paper").innerHTML = pickTable(data, false);
          window.print();
        }
      });
      return;
    }
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
      const body = step === "finish" ? {receipt_round:plan.receipt_round, actuals:Object.fromEntries(
        Array.from(card.querySelectorAll(".actual"), input => [input.dataset.row, +input.value]))} : {};
      const saved = await api("POST", `/api/outbound/group-analysis/${plan.id}/${step}`, body);
      showMsg(notice, saved.plan.status === "short" ? "本次实到已过账。剩余数量仍在本波，可继续补货。" :
        {finish:"补货已按实到过账", release:"整波已分配", cancel:"订单已解除方案"}[step], "success");
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
