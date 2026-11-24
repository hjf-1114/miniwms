"""补齐专用测试仓的上架样例，通过业务 API 写入，可重复运行。"""
from seed_module_demo import admin_headers, get, main, post, raw


WAREHOUSE = "DEMO-PA-WH"
RULE_SET = "DEMO-PA-RULES"


def ensure(table, key, value, path, fields, headers, response_key):
    existing = raw(f"SELECT id FROM {table} WHERE {key} = ?", (value,))
    if existing:
        return existing[0]["id"]
    response = post(path, {key: value, **fields}, headers=headers)
    return response[response_key]["id"]


def seed():
    main.init_db()
    auth = admin_headers()
    warehouse = ensure("warehouse", "code", WAREHOUSE, "/api/warehouses",
                       {"name": "上架规则测试仓"}, auth, "warehouse")
    owner = ensure("owner", "code", "DEMO-PA-OWNER", "/api/owners",
                   {"name": "上架演示货主"}, auth, "owner")
    headers = {**auth, "X-WMS-Warehouse": str(warehouse)}
    zones = {}
    for code, name, temperature in (
        ("STD", "测试常温区", "常温"), ("NG", "测试不合格隔离区", "常温"),
        ("VIRT", "测试虚拟储区", "常温"), ("COLD", "测试冷藏区", "冷藏"),
        ("EMPTY", "测试兜底储区", "常温"),
    ):
        zones[code] = ensure("zone", "code", f"PA-{code}", "/api/zones",
                             {"name": name, "warehouse_id": warehouse, "temp_class": temperature},
                             headers, "zone")

    locations = {}
    for suffix, zone, name, capacity, aisle, group in (
        ("RCV-01", "STD", "验收暂存货位", 0, "", ""),
        ("A-01", "STD", "同批号对照位", 100, "01", ""),
        ("A-02", "STD", "常温空位", 100, "01", ""),
        ("A-03", "STD", "常温窄容量位", 2, "01", ""),
        ("NG-01", "NG", "不合格隔离位", 100, "", ""),
        ("VIRT-01", "VIRT", "虚拟储位", 100, "", ""),
        ("COLD-01", "COLD", "冷藏货位", 100, "", ""),
        ("DIRECT-01", "STD", "直调固定货位", 100, "02", ""),
        ("SH-01", "STD", "穿梭库测试位", 100, "03", "PA-SHUTTLE"),
        ("EMPTY-01", "EMPTY", "兜底空货位", 100, "", ""),
    ):
        locations[suffix] = ensure(
            "location", "code", f"PA-{suffix}", "/api/locations",
            {"name": name, "warehouse_id": warehouse, "zone_id": zones[zone], "aisle": aisle,
             "loc_type": "receiving" if suffix == "RCV-01" else "storage",
             "capacity": capacity, "location_group": group or f"PA-{zone}-GROUP"},
            headers, "location")

    products = {}
    for suffix, name in (("LOT", "同批次测试货品"), ("STD", "通用测试货品")):
        products[suffix] = ensure("product", "sku", f"DEMO-PA-{suffix}", "/api/products",
                                  {"name": name, "owner_id": owner, "unit": "件",
                                   "default_zone_id": zones["STD"]}, headers, "product")

    sets = raw("SELECT id FROM putaway_rule_set WHERE code = ?", (RULE_SET,))
    set_id = sets[0]["id"] if sets else post(
        "/api/putaway-rule-sets",
        {"code": RULE_SET, "name": "上架规则测试策略", "warehouse_id": warehouse,
         "note": "上架规则验收专用，限定测试仓。"}, headers=headers)["id"]
    rules = [
        (10, "测试 · 冷链分流", {"is_cold_chain": 1},
         [{"type": "zone_by_code", "zone_code": "PA-COLD"}], "PA-COLD",
         "冷链标识为是时，推荐冷藏位，人工选择常温位会被拦截。"),
        (20, "测试 · 不合格隔离", {"goods_property": "不合格"},
         [{"type": "zone_by_code", "zone_code": "PA-NG"}], "PA-NG",
         "验收批次货品属性为不合格时，推荐隔离区，禁止改到其他储区。"),
        (30, "测试 · 虚拟分流", {"is_virtual": 1},
         [{"type": "empty_in_zone", "zone_code": "PA-VIRT"}], "PA-VIRT",
         "虚拟单优先到虚拟区空位。"),
        (40, "测试 · 直调固定货位", {"is_direct": 1},
         [{"type": "fixed_location", "location_code": "PA-DIRECT-01"}], None,
         "测试仓直调单先推荐指定货位。"),
        (50, "测试 · 同批号与附近空位", {"product_id": products["LOT"]},
         [{"type": "same_lot"}, {"type": "near_same_product"}, {"type": "empty_in_product_zone"}],
         "PA-STD", "同批号优先，查无同批号时推荐同巷道空位。"),
        (60, "测试 · 指定穿梭库货位组", {"external_doc_type": "DEMO-PA-SHUTTLE"},
         [{"type": "location_group", "group_code": "PA-SHUTTLE"}], None,
         "按外部单据类别匹配，推荐指定货位组。"),
        (70, "测试 · 货品默认储区空位", {"external_doc_type": "DEMO-PA-STANDARD"},
         [{"type": "empty_in_product_zone"}], "PA-STD",
         "过滤有库存或容量不足的货位。"),
        (999, "测试 · 空位兜底", {},
         [{"type": "empty_in_zone", "zone_code": "PA-EMPTY"}], "PA-EMPTY",
         "无其他规则命中时，查找测试兜底区空位。"),
    ]
    for priority, name, condition, steps, zone, note in rules:
        if raw("SELECT id FROM putaway_rule WHERE rule_set_id = ? AND name = ?", (set_id, name)):
            continue
        limits = {"loc_status": {"enabled": True}, "capacity": {"enabled": True}}
        if zone:
            limits["storage_class"] = {"enabled": True, "allowed_zone_codes": [zone]}
        post("/api/putaway-rules", {"rule_set_id": set_id, "rule_type": "putaway",
                                  "name": name, "priority": priority, "enabled": 1,
                                  "condition": condition, "steps": steps, "limits": limits,
                                  "note": note}, headers=headers)

    def order(suffix, sku, lots, goods_property="合格", **flags):
        code = f"DEMO-PA-{suffix}"
        total = sum(qty for _, qty in lots)
        existing = raw("SELECT id FROM asn WHERE code = ?", (code,))
        if existing:
            asn_id = existing[0]["id"]
            body = get(f"/api/asns/{asn_id}", headers=headers)
        else:
            body = post("/api/asns", {"code": code, "owner_id": owner, "source_warehouse": warehouse,
                                     "customer_name": "上架演示客户", "supplier_name": "上架演示供应商",
                                     "lines": [{"product_id": products[sku], "expected_qty": total}],
                                     **flags}, headers=headers)
            asn_id = body["asn"]["id"]
        path = f"/api/asns/{asn_id}"
        line = body["lines"][0]
        line_id = line["id"]
        counts = line.get("receiving_counts", [])
        count_id = counts[0]["id"] if counts else post(
            path + "/count", {"line_id": line_id, "qty": total}, headers=headers)["count_id"]
        accepted = {r["info"]["batch_no"] for r in line.get("batch_receipts", [])}
        for lot, qty in lots:
            if lot not in accepted:
                post(path + "/receive", {"line_id": line_id, "receiving_count_id": count_id,
                                         "location_id": locations["RCV-01"], "qty": qty,
                                         "batch_no": lot, "goods_property": goods_property,
                                         "receipt_note": f"上架测试样例 {code}"}, headers=headers)
        return path, get(path, headers=headers)["lines"][0]

    path, reference = order("REF", "LOT", [("PA-LOT-01", 3)])
    receipt = reference["batch_receipts"][0]
    if receipt["remaining_putaway"] > 0:
        post(path + "/putaway", {"line_id": reference["id"], "from_location_id": locations["RCV-01"],
                                 "to_location_id": locations["A-01"], "qty": receipt["remaining_putaway"],
                                 "receipt_barcode": receipt["batch_no"]}, headers=headers)

    cases = [
        ("01", "LOT", [("PA-LOT-01", 4), ("PA-LOT-02", 6)], {}),
        ("02", "STD", [("PA-NG-01", 5)], {"goods_property": "不合格"}),
        ("03", "STD", [("PA-VIRT-01", 5)], {"is_virtual": 1}),
        ("04", "STD", [("PA-DIRECT-01", 5)], {"is_direct": 1}),
        ("05", "STD", [("PA-COLD-01", 5)], {"is_cold_chain": 1}),
        ("06", "STD", [("PA-SH-01", 5)], {"external_doc_type": "DEMO-PA-SHUTTLE"}),
        ("07", "STD", [("PA-STD-01", 5)], {"external_doc_type": "DEMO-PA-STANDARD"}),
        ("08", "STD", [("PA-EMPTY-01", 5)], {}),
    ]
    for suffix, sku, lots, options in cases:
        _, line = order(suffix, sku, lots, **options)
        pending = sum(r["remaining_putaway"] for r in line["batch_receipts"])
        print(f"DEMO-PA-{suffix}  待上架 {pending:g}")
    print(f"仓库 {WAREHOUSE} · 策略 {RULE_SET}")


if __name__ == "__main__":
    seed()
