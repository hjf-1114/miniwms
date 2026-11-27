"""Seed a dedicated wave demo warehouse through the normal business API."""

from datetime import date, timedelta

from seed_module_demo import admin_headers, bind_demo_owner, main, post, put, raw
from seed_putaway_demo import ensure


def seed(*, initialize=True):
    if initialize:
        main.init_db()
    auth = admin_headers()
    wh = ensure("warehouse", "code", "DEMO-WAVE-WH", "/api/warehouses",
                {"name": "波次演示仓"}, auth, "warehouse")
    owner = ensure("owner", "code", "DEMO-WAVE-OWNER", "/api/owners",
                   {"name": "波次演示货主"}, auth, "owner")
    bind_demo_owner(wh, owner)
    headers = {**auth, "X-WMS-Warehouse": str(wh)}
    customer = ensure("customer", "code", "DEMO-WAVE-CUSTOMER", "/api/customers",
                      {"name": "演示电商店", "owner_id": owner}, headers, "customer")
    customers = [customer] + [
        ensure("customer", "code", f"DEMO-WAVE-{code}", "/api/customers",
               {"name": name, "owner_id": owner}, headers, "customer")
        for code, name in (("MALL", "天猫旗舰店"), ("JD", "京东自营店"), ("SHOP", "抖音直播店"),
                           ("VIP", "会员商城"), ("CHAIN", "连锁零售客户"))
    ]
    group = ensure("location_group", "code", "DEMO-WAVE-FULL", "/api/location-groups",
                   {"name": "波次演示全量组", "warehouse_id": wh}, headers, "location_group")
    pool = ensure("location_group", "code", "DEMO-WAVE-POOL", "/api/location-groups",
                  {"name": "波次备用拣货位", "warehouse_id": wh}, headers, "location_group")
    ensure("location_group", "code", "DEMO-WAVE-ALT", "/api/location-groups",
           {"name": "短通道对照组", "warehouse_id": wh}, headers, "location_group")
    def location(code, kind, grouped=False, group_code=None):
        return ensure("location", "code", "WDEMO-" + code, "/api/locations", {
            "name": "波次 " + code, "warehouse_id": wh, "loc_type": kind,
            "location_group": group_code or ("DEMO-WAVE-FULL" if grouped else "DEMO-WAVE-POOL"),
            "capacity": 2000 if grouped else 500,
            "allow_mix_batch": 1,
        }, headers, "location")

    receiving = location("RCV", "receiving")
    storage = {"normal": location("RESERVE", "storage", True),
               "cold": location("COLD", "storage", True),
               "hazard": location("HAZARD", "storage", True)}
    for index in range(1, 13):
        location(f"PICK-{index:02d}", "picking")
    comparison_faces = [
        location(f"ALT-{index:02d}", "picking", group_code="DEMO-WAVE-ALT")
        for index in range(1, 7)
    ]

    if not raw("SELECT 1 FROM wave_settings WHERE warehouse_id=?", (wh,)):
        put("/api/outbound/group-analysis/settings", {
            "full_group_id": group, "pool_group_id": pool, "cold_locations": "WDEMO-COLD",
            "hazard_locations": "WDEMO-HAZARD", "special_types": "人工专审",
        }, headers=headers)
    names = {"HOT": "活动保湿霜", "A": "洁面", "B": "化妆水", "C": "面膜",
             "D": "旅行装", "TAIL": "礼盒", "COLD": "冷藏样品", "HAZ": "危险品演示样品",
             "E": "护手霜", "F": "防晒乳", "G": "身体乳", "H": "柔肤套装"}
    products = {}
    for sku, name in names.items():
        products[sku] = ensure("product", "sku", "WDEMO-" + sku, "/api/products", {
            "name": name, "owner_id": owner, "category": "演示商品", "unit": "件",
            "weight_kg": 0.25, "length_cm": 10, "width_cm": 6, "height_cm": 4,
        }, headers, "product")
        if not raw("SELECT 1 FROM product_package WHERE product_id=? AND pkg_level='2级'", (products[sku],)):
            post(f"/api/products/{products[sku]}/packages",
                 {"pkg_level": "2级", "qty_per_package": 6 if sku in ("A", "HOT", "E") else 12},
                 headers=headers)
        code = "WDEMO-IN-" + sku
        if raw("SELECT 1 FROM asn WHERE code=?", (code,)):
            continue
        asn = post("/api/asns", {"code": code, "owner_id": owner, "source_warehouse": wh,
                                  "lines": [{"product_id": products[sku], "expected_qty": 150}]}, headers=headers)
        path = f"/api/asns/{asn['asn']['id']}"
        line = asn["lines"][0]["id"]
        count = post(path + "/count", {"line_id": line, "qty": 150}, headers=headers)
        received = post(path + "/receive", {
            "line_id": line, "receiving_count_id": count["count_id"], "qty": 150, "location_id": receiving,
            "batch_no": "WDEMO-LOT-" + sku, "expiry_date": (date.today() + timedelta(days=720)).isoformat(),
        }, headers=headers)
        key = {"COLD": "cold", "HAZ": "hazard"}.get(sku, "normal")
        post(path + "/putaway", {"line_id": line, "receipt_barcode": received["batch"]["batch_no"],
                                 "qty": 150, "from_location_id": receiving, "to_location_id": storage[key]},
             headers=headers)

    layout_transfer = raw("SELECT id,status FROM transfer WHERE code='WDEMO-LAYOUT-STAGING'")
    if not layout_transfer:
        result = post("/api/transfers", {
            "code": "WDEMO-LAYOUT-STAGING", "owner_id": owner, "note": "准备短通道对照货位的在手库存",
            "lines": [{"product_id": products[sku], "from_location_id": storage["normal"],
                       "to_location_id": target, "qty": qty}
                      for (sku, qty), target in zip((("HOT", 12), ("A", 18), ("B", 12)), comparison_faces)],
        }, headers=headers)
        layout_transfer = [result["transfer"]]
    if layout_transfer[0]["status"] == "open":
        post(f"/api/transfers/{layout_transfer[0]['id']}/move", {}, headers=headers)

    samples = [
        (24, ("HOT",), {}), (18, ("A", "B"), {}), (12, ("A", "C"), {}),
        (6, ("B", "D"), {}), (6, ("TAIL", "A", "HOT"), {}),
        (2, ("COLD",), {"is_cold_chain": 1}), (1, ("HAZ",), {"is_hazard": 1}),
        (1, ("HOT",), {"external_doc_type": "人工专审"}),
        (12, ("E",), {}), (10, ("A", "E"), {}), (9, ("B", "F"), {}),
        (7, ("A", "C", "G"), {}), (6, ("D", "F", "H"), {}), (4, ("E", "G", "TAIL"), {}),
    ]
    index = 0
    for count, skus, flags in samples:
        for _ in range(count):
            index += 1
            code = f"WDEMO-DN-{index:03d}"
            if raw("SELECT 1 FROM dn WHERE code=?", (code,)):
                continue
            post("/api/dns", {"code": code, "owner_id": owner, "source_warehouse": wh,
                              "customer_id": customer if index <= 70 else customers[index % len(customers)],
                              "workflow_required": True,
                              "lines": [{"product_id": products[sku], "ordered_qty": 1 + index % 2}
                                        for sku in skus], **flags}, headers=headers)
    history = [("A", "B"), ("HOT",), ("A", "C", "E"), ("B", "F"), ("G", "H"), ("D", "E", "TAIL")]
    for n in range(24):
        code = f"WDEMO-SHIPPED-{n + 1:03d}"
        existing = raw("SELECT id,status FROM dn WHERE code=?", (code,))
        if existing and existing[0]["status"] == "shipped":
            continue
        if existing:
            dn_id = existing[0]["id"]
        else:
            dn = post("/api/dns", {"code": code, "owner_id": owner, "source_warehouse": wh,
                                   "customer_id": customers[n % len(customers)], "workflow_required": False,
                                   "lines": [{"product_id": products[sku], "ordered_qty": 1 + n % 2}
                                             for sku in history[n % len(history)]]}, headers=headers)
            dn_id = dn["dn"]["id"]
        for line in raw("SELECT id,ordered_qty,picked_qty FROM dn_line WHERE dn_id=?", (dn_id,)):
            remaining = line["ordered_qty"] - line["picked_qty"]
            if remaining > 0:
                post(f"/api/dns/{dn_id}/pick", {"line_id": line["id"], "location_id": storage["normal"],
                                                "qty": remaining}, headers=headers)
        post(f"/api/dns/{dn_id}/ship", {}, headers=headers)
    return index + 24


if __name__ == "__main__":
    print(f"波次演示仓就绪，共 {seed()} 张演示订单（已有单据保留）")
