/** Shared search and pagination for inbound work queues. */
function initAsnFilters(stage, selectedId, onSelect) {
  const panel = document.getElementById("asnFilters");
  const select = document.getElementById("asnSel");
  panel.innerHTML = `
    <form class="toolbar" id="asnSearch">
      <label>单号 <input name="keyword" placeholder="ASN / 外部订单号"></label>
      <label>货主 <select name="owner_id"><option value="">全部</option></select></label>
      <label>客户 <select name="customer_id"><option value="">全部</option></select></label>
      <label>创建自 <input name="date_from" type="date"></label>
      <label>创建至 <input name="date_to" type="date"></label>
      <button class="btn" type="submit">查询</button>
      <button class="btn secondary" type="reset">清空</button>
    </form>
    <div class="toolbar">
      <button class="btn secondary sm" id="asnPrev" type="button">上一页</button>
      <span id="asnResults" aria-live="polite"></span>
      <button class="btn secondary sm" id="asnNext" type="button">下一页</button>
    </div>`;
  const form = panel.querySelector("form");
  const owners = form.elements.owner_id;
  const customers = form.elements.customer_id;
  const prev = panel.querySelector("#asnPrev");
  const next = panel.querySelector("#asnNext");
  const results = panel.querySelector("#asnResults");
  let page = 1, pages = 1, search = new URLSearchParams(), sequence = 0;

  async function query() {
    const version = ++sequence;
    prev.disabled = next.disabled = true;
    const qs = new URLSearchParams(search);
    qs.set("page", page);
    qs.set("page_size", "30");
    qs.set("stage", stage);
    try {
      const data = await api("GET", "/api/asns?" + qs);
      if (version !== sequence) return;
      const id = selectedId();
      const previous = Array.from(select.options).find(o => o.value === id);
      select.replaceChildren(new Option("请选择收货单", ""));
      for (const a of data.asns) {
        select.add(new Option([a.code, a.external_no, a.owner_name, a.customer_name]
          .filter(Boolean).join(" · "), a.id));
      }
      if (id && !Array.from(select.options).some(o => o.value === id)) {
        select.add(new Option(previous ? previous.text : "已打开单据 #" + id, id));
      }
      select.value = id;
      pages = Math.max(1, Math.ceil(data.total / 30));
      results.textContent = `共 ${data.total} 单 · 第 ${page} / ${pages} 页`;
      prev.disabled = page <= 1;
      next.disabled = page >= pages;
    } catch (e) {
      if (version === sequence) results.textContent = "查询失败：" + e.message;
    }
  }
  function applyFilters() {
    search = new URLSearchParams(new FormData(form));
    page = 1;
    onSelect("");
    query();
  }
  form.addEventListener("submit", e => { e.preventDefault(); applyFilters(); });
  form.addEventListener("reset", () => setTimeout(applyFilters));
  select.addEventListener("change", () => onSelect(select.value));
  prev.addEventListener("click", () => { if (page > 1) { page--; query(); } });
  next.addEventListener("click", () => { if (page < pages) { page++; query(); } });
  for (const [type, field] of [["owners", owners], ["customers", customers]]) {
    (async () => {
      for (let page = 1; ; page++) {
        const data = await api("GET", `/api/${type}?page_size=100&page=${page}`);
        for (const row of data[type]) field.add(new Option(`${row.code} · ${row.name}`, row.id));
        if (page * 100 >= data.total || !data[type].length) break;
      }
    })().catch(e => {
      showMsg(document.getElementById("msg"), "筛选选项加载失败：" + e.message, "error");
    });
  }
  return query;
}
function mountAsnLineEditor(editor, lineId, columns) {
  const body = document.getElementById("lineBody");
  const previous = body.querySelector(".inbound-editor-row");
  const row = Array.from(body.rows).find(r => r.dataset.lineId === String(lineId));
  if (row && row.nextElementSibling === previous && previous) return;
  if (previous) previous.remove();
  editor.hidden = !row;
  if (!row) {
    body.closest(".table-wrap").after(editor);
    return;
  }
  const holder = document.createElement("tr");
  holder.className = "inbound-editor-row";
  const cell = holder.insertCell();
  cell.colSpan = columns;
  cell.appendChild(editor);
  row.after(holder);
}

function showAsnFeedback(element, text, kind) {
  element.textContent = text;
  element.className = "msg " + kind + " show";
  element.setAttribute("role", kind === "error" ? "alert" : "status");
}

function asnPlacedQty(lines) {
  return lines.reduce((sum, line) => sum + line.putaway_qty, 0);
}

function asnStatusLabel(status, placed) {
  const labels = { open: "待收货", receiving: "收货中", putaway: placed > 1e-9 ? "上架中" : "待上架",
    closed: "已完成", cancelled: "已取消" };
  return labels[status] || status;
}

function asnStatusTag(status, placed) {
  return `<span class="tag ${status === "closed" ? "ok" : "off"}">${esc(asnStatusLabel(status, placed))}</span>`;
}

function inboundStageTag(done, target, labels) {
  const state = done <= 1e-9 ? 0 : target - done > 1e-9 ? 1 : 2;
  return `<span class="tag ${["off", "inbound-partial", "ok"][state]}">${labels[state]}</span>`;
}
