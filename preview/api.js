(() => {
  const { responses, lists, warehouses, preferredWarehouse, recommendations } = window.MWS_PREVIEW_DATA;
  const number = (value, otherwise) => Math.max(1, Number.parseInt(value, 10) || otherwise);
  const includes = (value, term) => String(value ?? "").toLowerCase().includes(term.toLowerCase());
  const same = (a, b) => String(a ?? "") === String(b);
  const exact = ["owner_id", "customer_id", "product_id", "location_id", "loc_type", "status",
    "stock_status", "designated_customer_id", "goods_property", "ref_type", "rule_type", "action", "asn_id"];

  function filter(row, params, path, warehouse) {
    const get = key => params.get(key);
    if (exact.some(key => get(key) && !same(row[key], get(key)))) return false;
    const warehouseId = row.source_warehouse ?? row.warehouse_id ??
      lists["/api/locations"].rows.find(loc => loc.id === row.location_id)?.warehouse_id;
    if (["/api/asns", "/api/dns", "/api/locations", "/api/stock", "/api/stock/batch", "/api/movements"].includes(path)
        && warehouseId != null && warehouseId !== warehouse.id) return false;
    if (get("set_id") && !same(row.rule_set_id, get("set_id"))) return false;
    for (const [name, columns] of [
      ["keyword", ["code", "external_no", "name", "sku", "product_name"]],
      ["product_keyword", ["sku", "product_name", "spec"]],
    ]) {
      if (get(name) && !columns.some(key => includes(row[key], get(name)))) return false;
    }
    const date = row.created_at?.slice(0, 10);
    if (date && ((get("date_from") && date < get("date_from")) || (get("date_to") && date > get("date_to")))) return false;
    if (get("has_stock") === "1" && !row.qty) return false;
    if (get("batch_no") && ![row.batch_no, row.internal_batch_no, row.batch_info?.batch_no].includes(get("batch_no"))) return false;
    if (get("batch_field") && get("batch_value") && !includes(row.batch_info?.[get("batch_field")], get("batch_value"))) return false;
    if (path === "/api/asns") {
      const stage = get("stage");
      const lines = responses["/api/asns/" + row.id].lines;
      if (stage === "acceptance") return lines.some(line => line.acceptable_qty > 1e-9);
      if (stage === "accepted") return row.accepted_total_qty > 1e-9;
      if (stage === "putaway") return row.pending_putaway_qty > 1e-9;
    }
    return true;
  }

  window.MWS_PREVIEW_API = async (method, path) => {
    if (method !== "GET") throw new Error("在线演示为只读，无法保存作业或配置。");
    const url = new URL(path, location.origin);
    const { pathname, searchParams } = url;
    const selectedWarehouse = localStorage.getItem("miniwms_warehouse_id");
    const warehouse = warehouses.find(row => same(row.id, selectedWarehouse)) ??
      warehouses.find(row => row.id === preferredWarehouse);
    if (pathname === "/api/scope/context") {
      return { ok: true, warehouses, current_warehouse: warehouse, current_warehouse_id: warehouse.id };
    }
    if (pathname === "/api/putaway/recommend") {
      const result = recommendations[searchParams.get("receipt_barcode")];
      if (!result) throw new Error("此批次没有待上架的样例推荐。");
      return structuredClone(result);
    }
    if (pathname === "/api/reservation-logs") {
      return structuredClone(responses[`${pathname}?dn_id=${Number(searchParams.get("dn_id"))}`]);
    }
    if (lists[pathname]) {
      const { key, rows, extra } = lists[pathname];
      const visible = rows.filter(row => filter(row, searchParams, pathname, warehouse));
      const page = number(searchParams.get("page"), 1);
      const page_size = number(searchParams.get("page_size"), 20);
      return structuredClone({ ...extra, [key]: visible.slice((page - 1) * page_size, page * page_size),
        total: visible.length, page, page_size });
    }
    if (responses[pathname]) return structuredClone(responses[pathname]);
    throw new Error("此功能暂无公开演示数据。");
  };

  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".env-badge").forEach(badge => { badge.textContent = "在线只读演示"; });
  });
})();
