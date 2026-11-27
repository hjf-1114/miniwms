const jobFilters = document.getElementById("jobFilters");
const plans = document.getElementById("plans");
const notice = document.getElementById("msg");
const kindLabels = {activity:"单品活动组", short:"短通道", full:"全量组"};
const statusLabels = {pending:"待备货或补货", short:"补货差异", ready:"待分配", stocked:"备货完成", allocated:"已分配", cancelled:"已撤销或结案"};
const empty = text => '<p class="muted">' + esc(text) + '</p>';
const requestedOwner = new URLSearchParams(location.search).get("owner_id");
const requestedPlan = new URLSearchParams(location.search).get("plan");
let jobVersion = 0;

async function perform(button, callback) {
  button.disabled = true;
  try { await callback(); }
  catch (error) { showMsg(notice, error.message, "error"); }
  finally { button.disabled = false; }
}

async function loadJobs() {
  const version = ++jobVersion;
  const query = new URLSearchParams(new FormData(jobFilters));
  if (!query.get("owner_id")) {
    plans.innerHTML = empty("请选择货主");
    return;
  }
  if (!query.get("status")) query.delete("status");
  if (!query.get("source")) query.delete("source");
  const {plans: rows} = await api("GET", "/api/outbound/preparations?" + query);
  if (version !== jobVersion) return;
  const visible = rows;
  document.getElementById("jobStats").textContent = `显示 ${visible.length} 个方案 · ${visible.filter(row => ["pending", "short"].includes(row.status)).length} 个待处理`;
  plans.replaceChildren(...visible.map(row => {
    const card = renderPlan(row);
    if (String(row.id) === requestedPlan) card.dataset.selected = "true";
    return card;
  }));
  if (!visible.length) plans.innerHTML = empty("没有匹配的作业");
  plans.querySelector('[data-selected="true"]')?.scrollIntoView({block:"nearest"});
}

jobFilters.addEventListener("submit", event => {
  event.preventDefault();
  perform(event.submitter, loadJobs);
});
jobFilters.addEventListener("change", () => perform(jobFilters.querySelector("button"), loadJobs));
perform(jobFilters.querySelector("button"), async () => {
  const {owners} = await api("GET", "/api/scope/owners");
  const owner = jobFilters.elements.owner_id;
  owner.append(...owners.filter(row => row.status === "active").map(row => new Option(row.name, row.id)));
  if (owners.some(row => String(row.id) === requestedOwner)) owner.value = requestedOwner;
  else if (owner.options.length === 2) owner.selectedIndex = 1;
  await loadJobs();
});

function moveTable(plan, editable) {
  return `<table><thead><tr><th>来源位</th><th>目标位</th><th>商品</th><th>批次</th><th>效期</th><th>计划</th><th>累计实到</th><th>待补</th>${editable ? "<th>本次实到</th>" : ""}</tr></thead>
    <tbody>${plan.moves.map(row => {
      const remaining = Math.max(0, +(row.qty - (row.actual_qty || 0)).toFixed(9));
      return `<tr><td>${esc(row.source)}</td><td>${esc(row.target)}</td>
      <td>${esc(row.sku)} · ${esc(row.product_name)}</td><td>${esc(row.batch_no)}</td><td>${esc(row.expiry_date || "—")}</td>
      <td>${row.qty}${row.case_size ? `（${+(row.qty / row.case_size).toFixed(6)} 箱）` : ""}</td><td>${row.actual_qty ?? "—"}</td><td>${remaining}</td>
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
  card.innerHTML = `<h3>${kindLabels[plan.kind]} · ${esc(plan.layout_code || plan.group_code)} <span class="muted">${statusLabels[plan.status]}</span></h3>
    <p>${plan.planning_source === "history" ? "历史备货 · 样本" : "实单补货 · "}${plan.orders.length} 单 ${plan.transfer_code ? "· 移库 " + esc(plan.transfer_code) : ""}
      ${plan.wave_no ? "· 波次 " + esc(plan.wave_no) : ""}</p>
    <details><summary>关联订单和商品货位</summary>
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
      await loadJobs();
    });
  };
  return card;
}
