// 商品档案字段：选商品时自动带出，但**全部可人工编辑**（自动带出只是默认值），随单据留快照
const SNAP_FIELDS = [
  { name: "product_name", label: "货品名称" },
  { name: "spec", label: "规格" },
  { name: "article_no", label: "货号" },
  { name: "unit", label: "单位" },
  { name: "brand", label: "品牌" },
  { name: "mfg_enterprise", label: "厂家" },
  { name: "production_enterprise", label: "生产企业" },
  { name: "origin", label: "产地" },
  { name: "udi", label: "UDI" },
  { name: "reg_cert_no", label: "注册证号" },
  { name: "registrant", label: "注册人/备案人" },
  { name: "mfg_license", label: "生产许可证" },
  { name: "shelf_life_days", label: "效期(天)" },
];
// 批次 / 验收信息：验收入库时写入批次档案，每次收货生成唯一批次号
const BATCH_FIELDS = [
  { name: "batch_no", label: "批号" },
  { name: "sterilization_batch_no", label: "灭菌批号" },
  { name: "production_date", label: "生产日期", type: "date" },
  { name: "expiry_date", label: "失效日期", type: "date" },
  { name: "internal_batch_no", label: "内部批次号（退货填出库值）" },
  { name: "goods_property", label: "货品属性", type: "select",
    options: [{ value: "合格", label: "合格" }, { value: "不合格", label: "不合格" }] },
  { name: "patient_name", label: "患者名称" },
];

function productData(productMap) {
  const out = {};
  Object.keys(productMap).forEach((id) => {
    const p = productMap[id];
    out[id] = {
      name: p.name || "", spec: p.spec || "", article_no: p.article_no || "",
      unit: p.unit || "", brand: p.brand || "", mfg_enterprise: p.mfg_enterprise || "",
      // 生产企业/产地默认取物料档案（可在明细里人工改写）
      production_enterprise: p.mfg_enterprise || "", origin: p.origin || "",
      reg_cert_no: p.reg_cert_no || "", registrant: p.registrant || "",
      mfg_license: p.mfg_license || "", shelf_life_days: p.shelf_life_days || "",
    };
  });
  return out;
}

function filterOwnerOptions(sel, opts, oid) {
  if (!sel) return;
  const cur = sel.value;
  const allowed = opts.filter(o => oid && String(o.owner_id) === String(oid));
  const label = oid ? (sel.name === "designated_customer_id" ? "通用库存" : "请选择…") : "请先选择委托方";
  sel.replaceChildren(new Option(label, ""), ...allowed.map(o => new Option(o.label, o.value)));
  sel.value = allowed.some(o => String(o.value) === cur) ? cur : "";
  sel.disabled = !oid;
}

const PRODUCT_LINE_AUTOFILL = { product_id: {
        product_name: "name", spec: "spec", article_no: "article_no", unit: "unit",
        brand: "brand", mfg_enterprise: "mfg_enterprise",
        production_enterprise: "production_enterprise", origin: "origin",
        reg_cert_no: "reg_cert_no", registrant: "registrant",
        mfg_license: "mfg_license", shelf_life_days: "shelf_life_days",
      } };

async function fetchOptions(resource) {
  const rows = [];
  for (let page = 1; ; page++) {
    const data = await api("GET", `/api/${resource}?page_size=100&page=${page}`);
    rows.push(...data[resource]);
    if (rows.length >= data.total || !data[resource].length) return rows;
  }
}
