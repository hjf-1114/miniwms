"""MiniWMS 演示数据一键播种脚本。

用途：
- 向项目真实数据库（默认 ./miniwms.db，可用 MINIWMS_DB 环境变量覆盖）写入一套
  贴近电商场景的演示主数据（多货主 / 多客户 / 带条码·品牌·规格·重量·尺寸·保质期
  的物料 / 收货·存储·拣货·退货库位），并跑通「收货上架 → 拣货发货」完整动线，
  生成可用于本地模拟试运行的库存账与流水。
- 通过 Flask test_client 驱动真实 API，等价于浏览器/前端走完整流程，数据落同一库。

用法：
    python tools/seed_demo.py            # 首次播种（若已存在演示数据则提示用 --reset）
    python tools/seed_demo.py --reset    # 清空全部业务数据后重新播种
    python tools/seed_demo.py --check    # 仅打印当前演示数据概况，不改动

注意：会清空所有 owner/product/customer/location/asn/dn/stock/stock_movement 数据
（--reset 时），仅建议在本地演示/测试库执行。
"""
import argparse
import os
import sqlite3
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

import main  # noqa: E402  (须在 sys.path 设置之后；非 __main__，蓝图已注册)


SENTINEL_OWNER = "OW-TMALL"  # 演示数据哨兵：存在即视为已播种


# ── 演示数据集 ──────────────────────────────────────────────────────────────
OWNERS = [
    {"code": "OW-TMALL", "name": "天猫旗舰店（示例货主）", "contact": "王经理",
     "phone": "13800001111", "email": "ops@tmall-demo.com",
     "address": "浙江省杭州市余杭区文一西路 969 号", "note": "电商示例货主（美妆/数码/母婴）"},
    {"code": "OW-JD", "name": "京东自营仓（示例货主）", "contact": "赵主管",
     "phone": "13900002222", "email": "ops@jd-demo.com",
     "address": "北京市大兴区亦庄经济技术开发区科创十一街 18 号", "note": "电商示例货主（数码/家居）"},
    {"code": "OW-SELF", "name": "自营电商（示例货主）", "contact": "钱运营",
     "phone": "13700003333", "email": "ops@self-demo.com",
     "address": "广东省深圳市南山区科技园南区", "note": "自营示例货主"},
]

# product 字段：sku,name,category,unit,barcode,brand,spec,weight_kg,length_cm,width_cm,height_cm,shelf_life_days,owner_code
PRODUCTS = [
    ("SKU-001", "保湿面霜 50g", "美妆个护", "瓶", "690000000001", "LANCOME 试用", "50g/瓶", 0.2, 8, 8, 8, 730, "OW-TMALL"),
    ("SKU-002", "无线蓝牙耳机", "数码电器", "台", "690000000002", "SoundX 试用", "蓝牙5.3/主动降噪", 0.15, 10, 10, 4, 1095, "OW-TMALL"),
    ("SKU-003", "纯棉 T 恤", "服饰", "件", "690000000003", "U 牌试用", "M码/纯棉", 0.25, 30, 25, 2, 0, "OW-TMALL"),
    ("SKU-004", "进口咖啡豆 500g", "食品", "袋", "690000000004", "STAR 试用", "500g/浅烘", 0.55, 15, 10, 5, 365, "OW-TMALL"),
    ("SKU-005", "婴儿纸尿裤 L 码", "母婴", "包", "690000000005", "PAMP 试用", "L码*40片", 2.0, 40, 30, 25, 1095, "OW-TMALL"),
    ("SKU-101", "机械键盘", "数码电器", "个", "690000001001", "KEY 试用", "87键/茶轴", 1.0, 45, 15, 4, 1095, "OW-JD"),
    ("SKU-102", "4K 显示器 27 寸", "数码电器", "台", "690000001002", "DELL 试用", "27寸/4K", 5.5, 62, 36, 18, 1095, "OW-JD"),
    ("SKU-103", "护眼台灯", "家居", "个", "690000001003", "LAMP 试用", "可调色温", 1.2, 20, 20, 40, 0, "OW-JD"),
]

# location 字段：code,name,zone,loc_type,capacity,aisle,bay,level,barcode
LOCATIONS = [
    ("RECV-01", "收货暂存区", "收货区", "receiving", 0, "R", "01", "-", "LOC-RECV-01"),
    ("A-01-01", "A区1排1列1层-美妆", "A区", "storage", 500, "A", "01", "L1", "LOC-A0101"),
    ("A-01-02", "A区1排2列1层-数码", "A区", "storage", 500, "A", "02", "L1", "LOC-A0102"),
    ("A-02-01", "A区2排1列1层-食品", "A区", "storage", 500, "A", "03", "L1", "LOC-A0201"),
    ("A-02-02", "A区2排2列1层-数码", "A区", "storage", 500, "A", "04", "L1", "LOC-A0202"),
    ("A-02-03", "A区2排3列1层-母婴", "A区", "storage", 800, "A", "05", "L1", "LOC-A0203"),
    ("PK-01", "拣货区 01", "拣货区", "picking", 200, "P", "01", "L1", "LOC-PK01"),
    ("RT-01", "退货区 01", "退货区", "returns", 300, "T", "01", "L1", "LOC-RT01"),
]

# customer 字段：code,name,owner_code,contact,phone,address,email,level,region
CUSTOMERS = [
    ("C-GOLD", "金冠旗舰买家", "OW-TMALL", "李总", "13600001111", "上海市浦东新区世纪大道 100 号", "vip@buyer-demo.com", "金牌", "华东"),
    ("C-SILVER", "银牌批发客户", "OW-TMALL", "周经理", "13500002222", "江苏省南京市鼓楼区中山路 1 号", "ws@buyer-demo.com", "银牌", "华北"),
    ("C-VIP", "VIP 直营客户", "OW-TMALL", "吴女士", "13400003333", "广东省广州市天河区天河路 200 号", "vip2@buyer-demo.com", "VIP", "华南"),
    ("C-JD", "京东渠道客户", "OW-JD", "郑采购", "13300004444", "北京市朝阳区建国路 88 号", "jd@buyer-demo.com", "普通", "全国"),
]

# 承运商（全系统共享的物流资源目录；后续发快递/零担/整车等业务的基础参照）
CARRIERS = [
    {"code": "SF", "name": "顺丰速运", "carrier_type": "快递", "contact": "顺丰客服", "phone": "95338", "api_code": "SF_EXPRESS", "address": "全国"},
    {"code": "JD", "name": "京东物流", "carrier_type": "快递", "contact": "京东客服", "phone": "950616", "api_code": "JD_LOGISTICS", "address": "全国"},
    {"code": "JT", "name": "跨越速运", "carrier_type": "快递", "contact": "跨越客服", "phone": "95324", "api_code": "KYE", "address": "全国"},
    {"code": "HLL", "name": "货拉拉", "carrier_type": "整车", "contact": "货拉拉", "phone": "10103636", "api_code": "HUOLALA", "address": "全国"},
    {"code": "DB", "name": "德邦快递", "carrier_type": "零担", "contact": "德邦客服", "phone": "95353", "api_code": "DEPPON", "address": "全国"},
    {"code": "ZTO", "name": "中通快递", "carrier_type": "快递", "contact": "中通客服", "phone": "95311", "api_code": "ZTO", "address": "全国"},
    {"code": "YTO", "name": "圆通速递", "carrier_type": "快递", "contact": "圆通客服", "phone": "95554", "api_code": "YTO", "address": "全国"},
    {"code": "ANE", "name": "安能物流", "carrier_type": "零担", "contact": "安能客服", "phone": "400-104-0088", "api_code": "ANE", "address": "全国"},
]

# 收货上架计划：sku -> 上架目标库位（按 sku 归入对应品类存储位）
PUTAWAY_PLAN = {
    "SKU-001": "A-01-01",
    "SKU-002": "A-01-02",
    "SKU-004": "A-02-01",
    "SKU-101": "A-02-02",
}

# 仓库（多仓库，全系统共享、不绑定货主）：深圳中心仓/上海华东仓/北京华北仓
WAREHOUSES = [
    {"code": "WH-SZ", "name": "深圳中心仓", "address": "广东省深圳市宝安区物流园 A 区", "contact": "仓管-李", "phone": "0755-88880001"},
    {"code": "WH-SH", "name": "上海华东仓", "address": "上海市青浦区华新镇物流大道 18 号", "contact": "仓管-王", "phone": "021-66660002"},
    {"code": "WH-BJ", "name": "北京华北仓", "address": "北京市通州区马驹桥物流基地 3 号库", "contact": "仓管-张", "phone": "010-55550003"},
]

# 供应商（归属货主，多货主隔离）：每个货主配 1-2 个常用供应商
SUPPLIERS = [
    {"code": "SUP-TM-FACTORY", "name": "天猫美妆代工厂", "owner_code": "OW-TMALL",
     "contact": "供货-陈", "phone": "0571-87000011", "address": "浙江省杭州市萧山区靖江街道工业园 1 号"},
    {"code": "SUP-TM-AGENT", "name": "进口食品总代", "owner_code": "OW-TMALL",
     "contact": "供货-刘", "phone": "020-38000012", "address": "广东省广州市天河区珠江新城"},
    {"code": "SUP-JD-DIGITAL", "name": "京东数码供应链", "owner_code": "OW-JD",
     "contact": "供货-孙", "phone": "010-59000013", "address": "北京市海淀区上地信息路 9 号"},
    {"code": "SUP-SELF-MFG", "name": "自营制造车间", "owner_code": "OW-SELF",
     "contact": "供货-周", "phone": "0755-26000014", "address": "广东省深圳市南山区"},
]

# 入/出库流程定义：(asn_code, owner_code, source_warehouse_code, supplier_code, [(sku, expected)], [(dn_code, customer_code, source_warehouse_code, [(sku, ordered)])])
FLOWS = [
    {
        "owner": "OW-TMALL",
        "source_warehouse": "WH-SZ",
        "supplier": "SUP-TM-FACTORY",
        "asn_code": "ASN-DEMO-001",
        "asn_lines": [("SKU-001", 100), ("SKU-002", 50), ("SKU-004", 80)],
        "dns": [
            {"code": "DN-DEMO-001", "customer": "C-GOLD", "source_warehouse": "WH-SZ",
             "lines": [("SKU-001", 30), ("SKU-002", 20), ("SKU-004", 10)]},
        ],
    },
    {
        "owner": "OW-JD",
        "source_warehouse": "WH-SH",
        "supplier": "SUP-JD-DIGITAL",
        "asn_code": "ASN-DEMO-101",
        "asn_lines": [("SKU-101", 20)],
        "dns": [
            {"code": "DN-DEMO-101", "customer": "C-JD", "source_warehouse": "WH-SH",
             "lines": [("SKU-101", 5)]},
        ],
    },
]


# ── 工具函数 ────────────────────────────────────────────────────────────────
def _client():
    main.init_db()
    return main.app.test_client()


def _post(c, path, payload, expect=(200, 201)):
    resp = c.post(path, json=payload)
    if resp.status_code not in expect:
        raise RuntimeError(f"POST {path} 失败: HTTP {resp.status_code} {resp.get_data(as_text=True)}")
    return resp.get_json()


def _put(c, path, payload):
    resp = c.put(path, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"PUT {path} 失败: HTTP {resp.status_code} {resp.get_data(as_text=True)}")
    return resp.get_json()


def _get(c, path):
    resp = c.get(path)
    if resp.status_code != 200:
        raise RuntimeError(f"GET {path} 失败: HTTP {resp.status_code} {resp.get_data(as_text=True)}")
    return resp.get_json()


def _reset(db_path):
    """清空全部业务数据（关闭 FK 约束后按子→主顺序删除）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in ("batch_receipt", "batch", "product_location",
                  "dn_pick", "dn_line", "dn", "asn_line", "asn",
                  "transfer_line", "transfer", "stocktake_line", "stocktake",
                  "stock_movement", "stock", "customer", "product",
                  "supplier", "location", "warehouse", "owner", "carrier"):
            conn.execute(f"DELETE FROM {t}")
        # 注意：batch_config 是用户配置，不属于业务数据，重置时保留
        conn.commit()
    finally:
        conn.close()


def _raw(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── 播种主流程 ──────────────────────────────────────────────────────────────
def seed(reset):
    db_path = main.DB_PATH
    if reset:
        print(f"[reset] 清空业务数据：{db_path}")
        _reset(db_path)

    c = _client()

    # 1) 货主
    owner_ids = {}
    for o in OWNERS:
        body = _post(c, "/api/owners", o)
        owner_ids[o["code"]] = body["owner"]["id"]
    print(f"  货主：{len(owner_ids)} 个")

    # 2) 物料
    product_ids = {}
    for (sku, name, cat, unit, barcode, brand, spec, w, l, wd, h, shelf, owner_code) in PRODUCTS:
        body = _post(c, "/api/products", {
            "sku": sku, "name": name, "category": cat, "unit": unit,
            "barcode": barcode, "brand": brand, "spec": spec,
            "weight_kg": w, "length_cm": l, "width_cm": wd, "height_cm": h,
            "shelf_life_days": shelf, "owner_id": owner_ids[owner_code],
        })
        product_ids[sku] = body["product"]["id"]
    print(f"  物料：{len(product_ids)} 个（含条码/品牌/规格/重量/尺寸/保质期）")

    # 3) 库位
    loc_ids = {}
    for (code, name, zone, ltype, cap, aisle, bay, level, barcode) in LOCATIONS:
        body = _post(c, "/api/locations", {
            "code": code, "name": name, "zone": zone, "loc_type": ltype,
            "capacity": cap, "aisle": aisle, "bay": bay, "level": level,
            "barcode": barcode,
        })
        loc_ids[code] = body["location"]["id"]
    print(f"  库位：{len(loc_ids)} 个（收货/存储/拣货/退货）")

    # 4.2) 仓库（多仓库，全系统共享）
    wh_ids = {}
    for wh in WAREHOUSES:
        body = _post(c, "/api/warehouses", wh)
        wh_ids[wh["code"]] = body["warehouse"]["id"]
    print(f"  仓库：{len(wh_ids)} 个（深圳/上海/北京）")

    # 4) 客户
    cust_ids = {}
    for (code, name, owner_code, contact, phone, address, email, level, region) in CUSTOMERS:
        body = _post(c, "/api/customers", {
            "code": code, "name": name, "owner_id": owner_ids[owner_code],
            "contact": contact, "phone": phone, "address": address,
            "email": email, "level": level, "region": region,
        })
        cust_ids[code] = body["customer"]["id"]
    print(f"  客户：{len(cust_ids)} 个（含等级/地区/邮箱）")

    # 4.5) 供应商（归属货主，多货主隔离）
    sup_ids = {}
    for sp in SUPPLIERS:
        body = _post(c, "/api/suppliers", {
            "code": sp["code"], "name": sp["name"], "owner_id": owner_ids[sp["owner_code"]],
            "contact": sp.get("contact", ""), "phone": sp.get("phone", ""),
            "address": sp.get("address", ""),
        })
        sup_ids[sp["code"]] = body["supplier"]["id"]
    print(f"  供应商：{len(sup_ids)} 个（各货主常用供货方）")

    # 4.6) 承运商（全系统共享的物流资源目录）
    carrier_count = 0
    for car in CARRIERS:
        _post(c, "/api/carriers", car)
        carrier_count += 1
    print(f"  承运商：{carrier_count} 个（顺丰/京东/跨越/货拉拉/德邦/中通/圆通/安能）")

    # 4.7) M20 储区维度：把库位挂到「全仓共享默认区」，给物料设推荐储区并绑定固定货位，
    #      这样打开「上架作业」页就能直接看到有理由的推荐结果。
    zone_rows = _get(c, "/api/zones?keyword=ZONE-SHARED")["zones"]
    if zone_rows:
        zone_id = zone_rows[0]["id"]
        for lid in loc_ids.values():
            _put(c, f"/api/locations/{lid}",
                 {"zone_id": zone_id, "warehouse_id": wh_ids["WH-SZ"]})
        for pid in product_ids.values():
            _put(c, f"/api/products/{pid}", {"default_zone_id": zone_id})
        for sku, dest in PUTAWAY_PLAN.items():
            _post(c, "/api/product-locations", {
                "product_id": product_ids[sku], "location_id": loc_ids[dest],
                "priority": 10, "note": "演示数据：固定货位",
            })
        print(f"  储区/绑定：{len(loc_ids)} 个库位挂储区、{len(product_ids)} 个物料设推荐储区、"
              f"{len(PUTAWAY_PLAN)} 条固定货位绑定")

    # 5) 跑通 收货上架 → 拣货发货 动线
    flow_count = 0
    for flow in FLOWS:
        oid = owner_ids[flow["owner"]]
        recv_loc = loc_ids["RECV-01"]
        wh_id = wh_ids.get(flow.get("source_warehouse"))
        sup_id = sup_ids.get(flow.get("supplier"))

        # 5.1 创建 ASN（含单据类型/来源仓库/供应商/外部单号等）
        asn_body = _post(c, "/api/asns", {
            "code": flow["asn_code"], "owner_id": oid,
            "doc_type": "采购", "external_no": flow["asn_code"] + "-EXT",
            "order_source": "演示平台", "source_warehouse": wh_id,
            "supplier_id": sup_id, "carrier_name": "顺丰速运",
            "lines": [{"product_id": product_ids[sku], "expected_qty": q}
                      for sku, q in flow["asn_lines"]],
        })
        asn_id = asn_body["asn"]["id"]
        asn_lines = {ln["product_id"]: ln for ln in asn_body["lines"]}

        # 5.2 逐行收货到 RECV-01
        for sku, qty in flow["asn_lines"]:
            ln_id = asn_lines[product_ids[sku]]["id"]
            _post(c, f"/api/asns/{asn_id}/receive",
                  {"line_id": ln_id, "location_id": recv_loc, "qty": qty})

        # 5.3 逐行上架到目标存储位
        for sku, qty in flow["asn_lines"]:
            dest = PUTAWAY_PLAN.get(sku)
            if not dest:
                continue
            ln_id = asn_lines[product_ids[sku]]["id"]
            _post(c, f"/api/asns/{asn_id}/putaway",
                  {"line_id": ln_id, "from_location_id": recv_loc,
                   "to_location_id": loc_ids[dest], "qty": qty})

        # 5.4 出库单 + 拣货 + 发货
        for dn in flow["dns"]:
            cid = cust_ids[dn["customer"]]
            dn_body = _post(c, "/api/dns", {
                "code": dn["code"], "owner_id": oid, "customer_id": cid,
                "doc_type": "销售", "external_no": dn["code"] + "-EXT",
                "order_source": "演示平台",
                "source_warehouse": wh_ids.get(dn.get("source_warehouse")),
                "carrier_name": "京东物流",
                "lines": [{"product_id": product_ids[sku], "ordered_qty": q}
                          for sku, q in dn["lines"]],
            })
            dn_id = dn_body["dn"]["id"]
            dn_lines = {ln["product_id"]: ln for ln in dn_body["lines"]}
            for sku, qty in dn["lines"]:
                dest = PUTAWAY_PLAN.get(sku)
                ln_id = dn_lines[product_ids[sku]]["id"]
                _post(c, f"/api/dns/{dn_id}/pick",
                      {"line_id": ln_id, "location_id": loc_ids[dest], "qty": qty})
            _post(c, f"/api/dns/{dn_id}/ship", {})
            flow_count += 1

    print(f"  动线：{len(FLOWS)} 张 ASN 收货上架 + {flow_count} 张 DN 拣货发货 已跑通")

    # 6) 行情汇总
    _print_summary(db_path)


def _print_summary(db_path):
    owners = _raw(db_path, "SELECT COUNT(*) n FROM owner")
    products = _raw(db_path, "SELECT COUNT(*) n FROM product")
    customers = _raw(db_path, "SELECT COUNT(*) n FROM customer")
    locations = _raw(db_path, "SELECT COUNT(*) n FROM location")
    asns = _raw(db_path, "SELECT status, COUNT(*) n FROM asn GROUP BY status")
    dns = _raw(db_path, "SELECT status, COUNT(*) n FROM dn GROUP BY status")
    stock_rows = _raw(db_path,
        """SELECT o.code owner_code, p.sku, l.code loc_code, s.qty, s.allocated_qty
           FROM stock s
           JOIN owner o ON o.id = s.owner_id
           JOIN product p ON p.id = s.product_id
           JOIN location l ON l.id = s.location_id
           WHERE s.qty <> 0 OR s.allocated_qty <> 0
           ORDER BY o.code, p.sku, l.code""")
    movements = _raw(db_path, "SELECT COUNT(*) n FROM stock_movement")

    print("\n========== 演示数据概况 ==========")
    print(f"货主 {owners[0]['n']} | 物料 {products[0]['n']} | 客户 {customers[0]['n']} | 库位 {locations[0]['n']}")
    print(f"库存流水 {movements[0]['n']} 条")
    print("ASN 状态分布:", ", ".join(f"{r['status']}={r['n']}" for r in asns) or "无")
    print("DN 状态分布:", ", ".join(f"{r['status']}={r['n']}" for r in dns) or "无")
    print("\n当前库存（在手≠0 或 已分配≠0）：")
    if stock_rows:
        print(f"  {'货主':<9}{'SKU':<9}{'库位':<10}{'在手':>8}{'已分配':>8}")
        for r in stock_rows:
            print(f"  {r['owner_code']:<9}{r['sku']:<9}{r['loc_code']:<10}{r['qty']:>8}{r['allocated_qty']:>8}")
    else:
        print("  （暂无库存）")
    print("==================================")
    print("下一步：运行 `python main.py` 后用浏览器打开 http://127.0.0.1:5100/ 即可模拟操作。")
    print("重置演示数据：python tools/seed_demo.py --reset")


def check():
    db_path = main.DB_PATH
    exists = _raw(db_path,
        "SELECT 1 FROM owner WHERE code = ? LIMIT 1", (SENTINEL_OWNER,))
    if not exists:
        print(f"尚未播种演示数据（哨兵货主 {SENTINEL_OWNER} 不存在）。运行 `python tools/seed_demo.py` 播种。")
        return
    print("已检测到演示数据，概况如下：")
    _print_summary(db_path)


def main_entry():
    parser = argparse.ArgumentParser(description="MiniWMS 演示数据播种")
    parser.add_argument("--reset", action="store_true", help="清空全部业务数据后重新播种")
    parser.add_argument("--check", action="store_true", help="仅打印当前演示数据概况，不改动")
    args = parser.parse_args()

    main.init_db()  # 确保表已存在（首次在全新库上运行时必须）

    if args.check:
        check()
        return

    db_path = main.DB_PATH
    if not args.reset:
        exists = _raw(db_path,
            "SELECT 1 FROM owner WHERE code = ? LIMIT 1", (SENTINEL_OWNER,))
        if exists:
            print(f"演示数据已存在（货主 {SENTINEL_OWNER}）。如需重置请加 --reset：")
            print("  python tools/seed_demo.py --reset")
            return

    print(f"开始播种演示数据到：{db_path}")
    seed(reset=args.reset)


if __name__ == "__main__":
    main_entry()
