function canAllocate(dn) {
  return ["open", "picking", "allocated"].includes(dn.status);
}

function dnWorkMessage(text, kind) {
  showMsg(document.getElementById("msg"), text, kind || "success");
}

async function autoAllocate() {
  const id = currentDn.dn.id;
  try {
    const result = await api("POST", "/api/outbound/allocate", {dn_ids: [id]});
    await showDetail(id);
    dnWorkMessage(`已分配 ${result.totals.allocated_qty} 件，${result.shortages.length} 行仍有缺货`);
  } catch (e) { dnWorkMessage(e.message, "error"); }
}

async function openManual(lineId) {
  const id = currentDn.dn.id;
  const line = currentDn.lines.find(row => row.id === lineId);
  try {
    const { stocks } = await api("GET", `/api/dns/${id}/allocation-candidates?line_id=${lineId}`);
    const usable = stocks.filter(stock => !stock.blocked);
    if (!usable.length) throw new Error(stocks.find(stock => stock.blocked)?.blocked || "没有可分配库存");
    openForm({
      title: `手工分配 · ${line.sku}`, submitText: "确认分配", width: 720,
      fields: [
        { name: "stock_id", label: "库存批次 / 货位", type: "select", required: true, full: true,
          options: usable.map(stock => ({
            value: stock.id,
            label: `${stock.location_code} · ${stock.batch_no || "无批号"} · 效期 ${stock.expiry_date || "—"} · 可用 ${stock.available}`,
          })) },
        { name: "qty", label: `分配数量（待分配 ${line.remaining_allocate}）`, type: "number", required: true },
      ],
      onSubmit: async payload => {
        await api("POST", `/api/dns/${id}/allocate`, { ...payload, line_id: lineId });
        await showDetail(id);
        dnWorkMessage("手工分配已保存");
      },
    });
  } catch (e) { dnWorkMessage(e.message, "error"); }
}

function cancelLineQty(lineId) {
  const id = currentDn.dn.id;
  const line = currentDn.lines.find(row => row.id === lineId);
  openForm({
    title: `取消出库数量 · ${line.sku}`, submitText: "确认取消",
    fields: [{ name: "qty", label: `取消数量（待分配 ${line.remaining_allocate}）`, type: "number", required: true }],
    onSubmit: async payload => {
      await api("POST", `/api/dns/${id}/lines/${lineId}/cancel`, payload);
      await showDetail(id);
      dnWorkMessage("已更新取消数量");
    },
  });
}

async function confirmAllocatedPick(pickId, button) {
  const id = currentDn.dn.id;
  button.disabled = true;
  try {
    await api("POST", `/api/dns/${id}/confirm-pick`, {
      pick_id: pickId, qty: document.getElementById("confirmed-" + pickId).value,
    });
    await showDetail(id);
    dnWorkMessage("已确认本条拣货");
  } catch (e) { dnWorkMessage(e.message, "error"); }
  finally { button.disabled = false; }
}

document.getElementById("shipByBox").addEventListener("submit", async event => {
  event.preventDefault();
  const field = event.currentTarget.elements.box_code;
  try {
    const result = await api("POST", "/api/dns/ship-box", {box_code: field.value.trim()});
    field.value = "";
    await load();
    dnWorkMessage(result.remaining_boxes ? `已登记箱码，还有 ${result.remaining_boxes} 箱待扫` : "已发货");
  } catch (e) { dnWorkMessage(e.message, "error"); }
  field.focus();
});
