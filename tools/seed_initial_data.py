"""MiniWMS 初始数据播种（角色 + 账号 + 基础主数据）。

背景：项目已交付 M1–M41，功能面很全，但**控制面（角色 / 账号）与基础主数据**
相对单薄：

- 角色只有 3 个（管理员 / 操作员 / 只读），权限清单只覆盖 6 个资源域；
  而系统到 M41 已沉淀 **41 个权限码常量**（库存转移、出库分配、复核、养护、
  GSP、工作量、经营报表、打印、运力、产品类型、序列号、货位组…），
  **一个都没进角色** —— 角色体系严重滞后于功能。
- 账号只有 2 个（admin / hjf）；且 `hjf` 的 `warehouse_scope`/`owner_scope`
  是 `grant` 但**两张授权表 0 行** —— 登录后做任何业务都会被
  ``resolve_scope()`` 判 403（"账号未配置任何仓库权限"），等于账号不可用。
- 基础主数据偏薄：货主 4 / 仓库 3 / 储区 7 / 库位 9 / 货品 10 / 客户 4 /
  供应商 4 / 司机 3 / 车辆 3，多货主多仓场景测不动。

本脚本做三件事（**幂等、可重复执行、不删任何现有数据**）：

1. **角色**：按 `permission_catalog.ROLE_TEMPLATES` 建 7 个岗位角色
   （系统管理员 / 仓储主管 / 收货员 / 拣货发货员 / 库存管理员 / 报表与质量员 /
   只读审计），权限码全部取自目录；已存在的同名角色**只补齐缺失的权限码**，
   不覆盖用户手工改过的清单（避免把「用户已改」当成「未配置」抹掉）。
2. **账号**：每个岗位角色配一个演示账号，并写入**仓库授权 + 货主授权**，
   让账号登录后真的能用（这是 `hjf` 缺的那一步）；另修复 `hjf` 的空授权。
3. **基础主数据**：按岗位场景补齐货主 / 仓库 / 储区 / 库位 / 货品 / 客户 /
   供应商 / 司机 / 车辆，并建立货主×仓库格子。

用法：
    ./.venv/Scripts/python.exe tools/seed_initial_data.py            # 播种
    ./.venv/Scripts/python.exe tools/seed_initial_data.py --check    # 只读盘点
    ./.venv/Scripts/python.exe tools/seed_initial_data.py --reset    # 删掉本脚本建的数据后重播

全部通过 **真实 API**（等价于浏览器操作）写入，不直接插业务表。
默认写项目根目录的 ``miniwms.db``，可用 ``MINIWMS_DB`` 覆盖。执行前建议先备份。
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("MINIWMS_DB", os.path.join(_ROOT, "miniwms.db"))
os.environ.setdefault("MINIWMS_ALLOW_ANONYMOUS", "1")

import main  # noqa: E402
import permission_catalog as PC  # noqa: E402

DB = os.environ["MINIWMS_DB"]
# 本脚本创建的数据统一用这个前缀，便于 --reset 精确清理
PFX = "INIT-"
RESULTS = []


# ── 基础设施 ────────────────────────────────────────────────────────────
def raw(sql, params=()):
    import sqlite3
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def req(method, path, payload=None, expect=(200, 201), headers=None):
    client = main.app.test_client()
    fn = {"GET": client.get, "POST": client.post, "PUT": client.put,
          "DELETE": client.delete}[method]
    kw = {"headers": headers or {}}
    if payload is not None:
        kw["json"] = payload
    r = fn(path, **kw)
    if r.status_code not in expect:
        raise RuntimeError(f"{method} {path} → HTTP {r.status_code}: "
                           f"{r.get_data(as_text=True)[:300]}")
    return r.get_json() or {}


def post(path, payload, **kw):
    return req("POST", path, payload, **kw)


def get(path, **kw):
    return req("GET", path, None, **kw)


def put(path, payload, **kw):
    return req("PUT", path, payload, **kw)


def step(name, fn):
    """单步失败不中断其余步骤，便于一次跑完看出所有问题。"""
    try:
        detail = fn()
        RESULTS.append((name, "OK", str(detail)))
        print(f"  [OK]   {name}: {detail}")
    except Exception as e:  # noqa: BLE001 - 播种脚本需要收集全部失败
        RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")


def admin_headers():
    """登录 admin 拿 token —— 角色 / 账号 / 授权端点都挂了 user:manage 校验。"""
    body = post("/api/auth/login", {"username": "admin", "password": "admin123"})
    return {"Authorization": "Bearer " + body["token"]}


# 各表的业务编码列（product 用 sku，其余用 code）
_CODE_COL = {"product": "sku"}


def by_code(table, code):
    col = _CODE_COL.get(table, "code")
    rows = raw(f"SELECT * FROM {table} WHERE {col} = ?", (code,))
    return rows[0] if rows else None


# 各创建端点返回体里的对象键名（按资源区分）
_OBJ_KEY = {
    "/api/owners": "owner", "/api/warehouses": "warehouse",
    "/api/zones": "zone", "/api/locations": "location",
    "/api/products": "product", "/api/customers": "customer",
    "/api/suppliers": "supplier", "/api/drivers": "driver",
    "/api/vehicles": "vehicle",
}


def ensure(table, code, path, payload, key="code", headers=None):
    """幂等创建：存在则返回已有记录 id，不存在才调 API 建。

    返回体里的对象键名各端点不同（owner / location / product …），
    按 ``_OBJ_KEY`` 取；取不到就遍历找第一个含 id 的字典。
    """
    row = by_code(table, code)
    if row:
        return row["id"], False
    body = post(path, payload, headers=headers)
    obj = body.get(_OBJ_KEY.get(path)) or {}
    if "id" not in obj:
        for v in body.values():
            if isinstance(v, dict) and "id" in v:
                obj = v
                break
    if "id" not in obj:
        raise RuntimeError(f"{path} 返回体里找不到 id：{str(body)[:160]}")
    return obj["id"], True


# ── 1. 角色 ──────────────────────────────────────────────────────────────
def seed_roles():
    """按岗位模板建角色；已存在的只补齐缺失的权限码。"""
    h = admin_headers()
    made, patched = [], []
    for tpl in PC.ROLE_TEMPLATES:
        row = raw("SELECT id, permissions FROM role WHERE name = ?",
                  (tpl["name"],))[0:1]
        if row:
            import json
            cur = json.loads(row[0]["permissions"] or "[]")
            missing = [c for c in tpl["permissions"] if c not in cur]
            if missing and tpl["permissions"] != ["*"]:
                put(f"/api/roles/{row[0]['id']}",
                    {"name": tpl["name"], "permissions": cur + missing},
                    headers=h)
                patched.append(f"{tpl['name']}(+{len(missing)})")
            continue
        post("/api/roles", {"name": tpl["name"],
                            "permissions": tpl["permissions"]}, headers=h)
        made.append(tpl["name"])
    return (f"新建 {len(made)} 个（{'、'.join(made) if made else '无'}）；"
            f"补齐 {len(patched)} 个（{'、'.join(patched) if patched else '无'}）")


# ── 2. 账号 + 授权 ───────────────────────────────────────────────────────
# 演示账号：用户名 / 角色名 / 密码 / 数据范围
#   scope: "all" = 全部仓库+全部货主；"grant" = 下面列出的子集
ACCOUNTS = [
    # (用户名, 角色, 密码, 绑定货主code或None, 仓库范围, 货主范围, 备注)
    ("init.admin", "系统管理员", "init123456", None, "all", "all", "超管演示账号"),
    ("init.wh", "仓储主管", "init123456", None, "all", "all", "管仓库与作业复核"),
    ("init.receive", "收货员", "init123456", None, "all", "grant", "入库侧演示账号"),
    ("init.pick", "拣货发货员", "init123456", None, "all", "grant", "出库侧演示账号"),
    ("init.stock", "库存管理员", "init123456", None, "all", "grant", "盘点与调整演示账号"),
    ("init.report", "报表与质量员", "init123456", None, "all", "all", "报表只读演示账号"),
    ("init.audit", "只读审计", "init123456", None, "all", "all", "全域只读演示账号"),
]


def seed_accounts():
    """建岗位演示账号，并写入仓库/货主授权（关键：不写授权就登不进去）。"""
    import json
    h = admin_headers()
    roles = {r["name"]: r["id"] for r in raw("SELECT id, name FROM role")}
    owners = {o["code"]: o["id"] for o in raw("SELECT id, code FROM owner")}
    whs = [w["id"] for w in raw("SELECT id FROM warehouse ORDER BY id")]
    owner_ids = sorted(owners.values())

    made, granted = [], []
    for username, role_name, pwd, owner_code, wh_scope, ow_scope, note in ACCOUNTS:
        rid = roles.get(role_name)
        if not rid:
            raise RuntimeError(f"角色不存在：{role_name}")
        rows = raw("SELECT id FROM user WHERE username = ?", (username,))
        if rows:
            uid = rows[0]["id"]
        else:
            body = post("/api/users", {
                "username": username, "password": pwd, "role_id": rid,
                "owner_id": owners.get(owner_code), "status": "active"},
                headers=h)
            uid = body["user"]["id"]
            made.append(username)
        # 写授权（幂等：PUT 是全量替换）
        wh_ids = whs if wh_scope == "all" else whs[:1]
        ow_ids = owner_ids if ow_scope == "all" else owner_ids[:1]
        put(f"/api/users/{uid}/grants", {
            "warehouse_scope": wh_scope, "owner_scope": ow_scope,
            "warehouse_ids": wh_ids, "owner_ids": ow_ids}, headers=h)
        granted.append(f"{username}({role_name})")

    # 修 hjf：scope 是 grant 但授权表 0 行 → 登录后业务全 403
    hjf = raw("SELECT id FROM user WHERE username = 'hjf'")
    if hjf:
        uid = hjf[0]["id"]
        wh_row = raw("SELECT COUNT(*) AS n FROM user_warehouse_grant WHERE user_id = ?",
                     (uid,))[0]["n"]
        ow_row = raw("SELECT COUNT(*) AS n FROM user_owner_grant WHERE user_id = ?",
                     (uid,))[0]["n"]
        if not wh_row or not ow_row:
            put(f"/api/users/{uid}/grants", {
                "warehouse_scope": "all", "owner_scope": "all",
                "warehouse_ids": whs, "owner_ids": owner_ids}, headers=h)
            granted.append("hjf(补齐空授权)")

    return (f"新建 {len(made)} 个账号（{'、'.join(made) if made else '无'}）；"
            f"授权 {len(granted)} 个")


# ── 3. 基础主数据 ────────────────────────────────────────────────────────
NEW_OWNERS = [
    ("INIT-OW-HM", "海盟日化（示例货主）", "孙经理", "13611110001",
     "广东省广州市白云区机场路 88 号", "美妆个护 / 日化线，多仓运营"),
    ("INIT-OW-FB", "丰邦食品（示例货主）", "冯主管", "13611110002",
     "山东省青岛市黄岛区开发区长江路 20 号", "食品饮料，冷链为主"),
]
NEW_WAREHOUSES = [
    ("INIT-WH-CD", "成都西南仓", "四川省成都市双流区物流大道 66 号",
     "仓管-罗", "028-66660004"),
    ("INIT-WH-GZ", "广州华南仓", "广东省广州市番禺区物流园 5 号库",
     "仓管-何", "020-55550005"),
]
# 储区：(code, name, warehouse_code, note)
NEW_ZONES = [
    ("INIT-ZN-CD-AMB", "常温区", "INIT-WH-CD", "常温存储"),
    ("INIT-ZN-CD-COLD", "冷藏区", "INIT-WH-CD", "2-8℃"),
    ("INIT-ZN-CD-RETURN", "退货区", "INIT-WH-CD", "退货暂存"),
    ("INIT-ZN-GZ-AMB", "常温区", "INIT-WH-GZ", "常温存储"),
    ("INIT-ZN-GZ-COLD", "冷链区", "INIT-WH-GZ", "0-4℃"),
]
# 库位：(code, name, warehouse_code, zone_code, loc_type, aisle, bay, level)
NEW_LOCATIONS = [
    ("INIT-CD-RCV-01", "成都收货暂存 01", "INIT-WH-CD", "INIT-ZN-CD-AMB", "receiving", "R", "01", "-"),
    ("INIT-CD-A-01-01", "成都A区01排01列01层", "INIT-WH-CD", "INIT-ZN-CD-AMB", "storage", "A", "01", "L1"),
    ("INIT-CD-A-01-02", "成都A区01排02列01层", "INIT-WH-CD", "INIT-ZN-CD-AMB", "storage", "A", "02", "L1"),
    ("INIT-CD-B-01-01", "成都B区01排01列02层", "INIT-WH-CD", "INIT-ZN-CD-AMB", "storage", "B", "01", "L2"),
    ("INIT-CD-COLD-01", "成都冷藏库位 01", "INIT-WH-CD", "INIT-ZN-CD-COLD", "storage", "C", "01", "L1"),
    ("INIT-CD-PK-01", "成都拣货区 01", "INIT-WH-CD", "INIT-ZN-CD-AMB", "picking", "P", "01", "L1"),
    ("INIT-CD-RTN-01", "成都退货区 01", "INIT-WH-CD", "INIT-ZN-CD-RETURN", "returns", "T", "01", "L1"),
    ("INIT-GZ-RCV-01", "广州收货暂存 01", "INIT-WH-GZ", "INIT-ZN-GZ-AMB", "receiving", "R", "01", "-"),
    ("INIT-GZ-A-01-01", "广州A区01排01列01层", "INIT-WH-GZ", "INIT-ZN-GZ-AMB", "storage", "A", "01", "L1"),
    ("INIT-GZ-B-02-01", "广州B区02排01列01层", "INIT-WH-GZ", "INIT-ZN-GZ-AMB", "storage", "B", "02", "L1"),
    ("INIT-GZ-COLD-01", "广州冷链库位 01", "INIT-WH-GZ", "INIT-ZN-GZ-COLD", "storage", "C", "01", "L1"),
    ("INIT-GZ-PK-01", "广州拣货区 01", "INIT-WH-GZ", "INIT-ZN-GZ-AMB", "picking", "P", "01", "L1"),
]
# 货品：(sku, name, owner_code, category, unit, purchase, sale, brand, spec, is_serial)
NEW_PRODUCTS = [
    ("INIT-SKU-201", "氨基酸洁面乳 120g", "INIT-OW-HM", "美妆个护", "支",
     32.0, 69.0, "海盟", "120g/支", 0),
    ("INIT-SKU-202", "玻尿酸保湿精华 30ml", "INIT-OW-HM", "美妆个护", "瓶",
     88.0, 179.0, "海盟", "30ml/瓶", 0),
    ("INIT-SKU-203", "电动牙刷 Pro", "INIT-OW-HM", "个护电器", "台",
     145.0, 299.0, "海盟", "IPX7/三档", 0),
    ("INIT-SKU-204", "精华液礼盒装", "INIT-OW-HM", "美妆个护", "盒",
     210.0, 458.0, "海盟", "双支礼盒", 0),
    ("INIT-SKU-301", "低温鲜牛奶 950ml", "INIT-OW-FB", "乳制品", "瓶",
     12.5, 22.0, "丰邦", "950ml/瓶", 0),
    ("INIT-SKU-302", "冷冻虾仁 500g", "INIT-OW-FB", "水产", "袋",
     58.0, 108.0, "丰邦", "500g/袋", 0),
    ("INIT-SKU-303", "即食海苔卷 12 枚", "INIT-OW-FB", "休闲食品", "盒",
     18.0, 35.0, "丰邦", "12枚/盒", 0),
    ("INIT-SKU-304", "冷冻牛腩 1kg", "INIT-OW-FB", "肉制品", "袋",
     78.0, 138.0, "丰邦", "1kg/袋", 0),
    ("INIT-SKU-305", "智能温控手环 S3", "INIT-OW-FB", "智能硬件", "只",
     96.0, 199.0, "丰邦", "S3/蓝牙5.2", 1),
    ("INIT-SKU-306", "冷藏酸奶 6 连杯", "INIT-OW-FB", "乳制品", "箱",
     26.0, 45.0, "丰邦", "6×100g", 0),
]
# 客户：(code, name, owner_code, contact, phone, level, region)
NEW_CUSTOMERS = [
    ("INIT-C-HM-01", "海盟天猫旗舰店", "INIT-OW-HM", "孙经理", "13722220001", "金牌", "华东"),
    ("INIT-C-HM-02", "海盟抖音小店", "INIT-OW-HM", "李运营", "13722220002", "银牌", "华南"),
    ("INIT-C-HM-03", "海盟线下连锁药房", "INIT-OW-HM", "周采购", "13722220003", "普通", "西南"),
    ("INIT-C-FB-01", "丰邦商超采购部", "INIT-OW-FB", "吴采购", "13733330001", "金牌", "华北"),
    ("INIT-C-FB-02", "丰邦餐饮连锁", "INIT-OW-FB", "郑经理", "13733330002", "VIP", "华东"),
]
# 供应商：(code, name, owner_code, contact, phone, address)
NEW_SUPPLIERS = [
    ("INIT-S-HM-01", "海盟日化代工厂", "INIT-OW-HM", "供货-陈", "0760-88000011",
     "广东省中山市火炬开发区工业路 8 号"),
    ("INIT-S-HM-02", "海盟包装制品厂", "INIT-OW-HM", "供货-林", "0760-88000012",
     "广东省东莞市长安镇振安东路 66 号"),
    ("INIT-S-FB-01", "丰邦乳业牧场", "INIT-OW-FB", "供货-王", "0532-99000013",
     "山东省青岛市城阳区流亭街道牧场路 1 号"),
    ("INIT-S-FB-02", "丰邦水产冷链", "INIT-OW-FB", "供货-赵", "0532-99000014",
     "山东省青岛市即墨区蓝村街道冷库路 3 号"),
]
# 司机：(code, name, phone, id_no, license_no, license_type, carrier_code, owner_code)
NEW_DRIVERS = [
    ("INIT-DRV-01", "张师傅", "13844440001", "330102199003150011",
     "330102199003150011", "C1", "SF", None),
    ("INIT-DRV-02", "李师傅", "13844440002", "370202198807220022",
     "370202198807220022", "C1", "DB", None),
    ("INIT-DRV-03", "王师傅", "13844440003", "510107199511080033",
     "510107199511080033", "A2", "ANE", "INIT-OW-HM"),
]
# 车辆：(code, plate_no, vehicle_type, max_load_kg, carrier_code, owner_code)
NEW_VEHICLES = [
    ("INIT-VH-01", "粤B12345", "厢式货车", 1800, "SF", None),
    ("INIT-VH-02", "鲁B23456", "厢式货车", 2500, "DB", None),
    ("INIT-VH-03", "川A34567", "平板货车", 3200, "ANE", "INIT-OW-HM"),
]


def seed_master_data():
    """补齐货主 / 仓库 / 储区 / 库位 / 货品 / 客户 / 供应商 / 司机 / 车辆。"""
    import datetime
    today = datetime.date.today()
    h = admin_headers()
    made = {"货主": 0, "仓库": 0, "储区": 0, "库位": 0,
            "货品": 0, "客户": 0, "供应商": 0, "司机": 0, "车辆": 0}

    # 货主
    for code, name, contact, phone, address, note in NEW_OWNERS:
        _, ok = ensure("owner", code, "/api/owners", {
            "code": code, "name": name, "contact": contact, "phone": phone,
            "address": address, "note": note}, headers=h)
        made["货主"] += 1 if ok else 0
    owners = {o["code"]: o["id"] for o in raw("SELECT id, code FROM owner")}

    # 仓库
    for code, name, address, contact, phone in NEW_WAREHOUSES:
        _, ok = ensure("warehouse", code, "/api/warehouses", {
            "code": code, "name": name, "address": address,
            "contact": contact, "phone": phone}, headers=h)
        made["仓库"] += 1 if ok else 0
    whs = {w["code"]: w["id"] for w in raw("SELECT id, code FROM warehouse")}

    # 储区
    for code, name, wh_code, note in NEW_ZONES:
        _, ok = ensure("zone", code, "/api/zones", {
            "code": code, "name": name, "warehouse_id": whs.get(wh_code),
            "note": note}, headers=h)
        made["储区"] += 1 if ok else 0
    zones = {z["code"]: z["id"] for z in raw("SELECT id, code FROM zone")}

    # 库位
    for code, name, wh_code, zn_code, loc_type, aisle, bay, level in NEW_LOCATIONS:
        _, ok = ensure("location", code, "/api/locations", {
            "code": code, "name": name, "warehouse_id": whs.get(wh_code),
            "zone_id": zones.get(zn_code), "loc_type": loc_type,
            "aisle": aisle, "bay": bay, "level": level}, headers=h)
        made["库位"] += 1 if ok else 0

    # 货品（产品类型引用 M32 字典的编码；字典里没有会自动建行）
    for (sku, name, ow_code, cat, unit, buy, sale, brand,
         spec, is_serial) in NEW_PRODUCTS:
        _, ok = ensure("product", sku, "/api/products", {
            "sku": sku, "name": name, "owner_id": owners.get(ow_code),
            "category": cat, "category_l1": cat, "unit": unit,
            "purchase_price": buy, "sale_price": sale,
            "brand": brand, "spec": spec, "is_serial": is_serial,
            "shelf_life_days": 365 if cat in ("乳制品", "水产", "肉制品") else 0}, headers=h)
        made["货品"] += 1 if ok else 0

    # 客户
    for code, name, ow_code, contact, phone, level, region in NEW_CUSTOMERS:
        _, ok = ensure("customer", code, "/api/customers", {
            "code": code, "name": name, "owner_id": owners.get(ow_code),
            "contact": contact, "phone": phone, "level": level, "region": region}, headers=h)
        made["客户"] += 1 if ok else 0

    # 供应商
    for code, name, ow_code, contact, phone, address in NEW_SUPPLIERS:
        _, ok = ensure("supplier", code, "/api/suppliers", {
            "code": code, "name": name, "owner_id": owners.get(ow_code),
            "contact": contact, "phone": phone, "address": address}, headers=h)
        made["供应商"] += 1 if ok else 0

    # 司机 / 车辆（承运商是既有主数据，按 code 取 id）
    carriers = {c["code"]: c["id"] for c in raw("SELECT id, code FROM carrier")}
    for (code, name, phone, id_no, lic, lic_type,
         car_code, ow_code) in NEW_DRIVERS:
        _, ok = ensure("driver", code, "/api/drivers", {
            "code": code, "name": name, "phone": phone, "id_no": id_no,
            "license_no": lic, "license_type": lic_type,
            "carrier_id": carriers.get(car_code),
            "owner_id": owners.get(ow_code),
            "license_expiry": (today + datetime.timedelta(days=400)).isoformat()}, headers=h)
        made["司机"] += 1 if ok else 0
    for code, plate, vtype, load, car_code, ow_code in NEW_VEHICLES:
        _, ok = ensure("vehicle", code, "/api/vehicles", {
            "code": code, "plate_no": plate, "vehicle_type": vtype,
            "max_load_kg": load, "carrier_id": carriers.get(car_code),
            "owner_id": owners.get(ow_code),
            "insurance_expiry": (today + datetime.timedelta(days=300)).isoformat(),
            "transport_expiry": (today + datetime.timedelta(days=500)).isoformat()}, headers=h)
        made["车辆"] += 1 if ok else 0

    detail = "、".join(f"{k} {v}" for k, v in made.items() if v)
    return f"新增 {detail or '无（已存在）'}"


def seed_owner_grid():
    """把新增货主与仓库登记进格子（货主×仓库业务关系）。"""
    h = admin_headers()
    owners = sorted({o["id"] for o in raw("SELECT id FROM owner")})
    whs = [w["id"] for w in raw("SELECT id FROM warehouse ORDER BY id")]
    # 只对含 INIT- 数据的仓做登记，避免动到用户手工配置过的格子
    init_whs = [w["id"] for w in raw(
        "SELECT id FROM warehouse WHERE code LIKE 'INIT-%'")]
    for wid in init_whs:
        put(f"/api/owner-warehouses/{wid}", {"owner_ids": owners}, headers=h)
    return f"登记 {len(init_whs)} 个新仓 × {len(owners)} 个货主 = {len(init_whs) * len(owners)} 格"


# ── --check ──────────────────────────────────────────────────────────────
def check():
    import json
    print("── 控制面 ──")
    roles = raw("SELECT id, name, permissions FROM role ORDER BY id")
    for r in roles:
        try:
            perms = json.loads(r["permissions"] or "[]")
        except Exception:
            perms = []
        n_users = raw("SELECT COUNT(*) AS n FROM user WHERE role_id = ?",
                      (r["id"],))[0]["n"]
        print(f"  角色 {r['id']:>2} {r['name']:<12} 权限 {len(perms):>3} 项  用户 {n_users}")
    print("  账号：")
    for u in raw("SELECT u.id, u.username, u.warehouse_scope, u.owner_scope, "
                 "r.name AS role FROM user u LEFT JOIN role r ON r.id=u.role_id "
                 "ORDER BY u.id"):
        wh = raw("SELECT COUNT(*) AS n FROM user_warehouse_grant WHERE user_id=?",
                 (u["id"],))[0]["n"]
        ow = raw("SELECT COUNT(*) AS n FROM user_owner_grant WHERE user_id=?",
                 (u["id"],))[0]["n"]
        flag = ""
        if u["warehouse_scope"] == "grant" and not wh:
            flag += " ⚠仓库未授权"
        if u["owner_scope"] == "grant" and not ow:
            flag += " ⚠货主未授权"
        print(f"    {u['id']:>2} {u['username']:<14} {u['role'] or '-':<12} "
              f"仓库={u['warehouse_scope']}({wh}) 货主={u['owner_scope']}({ow}){flag}")
    print("── 基础主数据 ──")
    for t in ("owner", "warehouse", "zone", "location", "location_group",
              "product", "product_type", "customer", "supplier", "carrier",
              "driver", "vehicle", "batch"):
        try:
            n = raw(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
        except Exception:
            n = "-"
        extra = ""
        if t in ("owner", "warehouse", "zone", "location", "product",
                 "customer", "supplier", "driver", "vehicle"):
            k = "code" if t != "product" else "sku"
            try:
                extra = f"（其中 INIT- 前缀 {raw(f'SELECT COUNT(*) AS n FROM {t} WHERE {k} LIKE ?', ('INIT-%',))[0]['n']}）"
            except Exception:
                pass
        print(f"  {t:<16} {n:>4}{extra}")


# ── --reset ──────────────────────────────────────────────────────────────
def reset():
    """只删本脚本建的数据（INIT- 前缀 + init.* 账号 + 岗位角色）。"""
    import json
    print("将删除：INIT- 前缀的主数据、init.* 账号、岗位模板角色")
    for u in raw("SELECT id FROM user WHERE username LIKE 'init.%'"):
        req("DELETE", f"/api/users/{u['id']}")
    for r in raw("SELECT id, name FROM role"):
        if PC.template(r["name"]):
            n = raw("SELECT COUNT(*) AS n FROM user WHERE role_id = ?",
                    (r["id"],))[0]["n"]
            if n == 0:
                req("DELETE", f"/api/roles/{r['id']}")
    # 主数据：先删子表，再删父表
    for t, k in (("driver", "code"), ("vehicle", "code"),
                 ("customer", "code"), ("supplier", "code"),
                 ("product", "sku"), ("location", "code"),
                 ("zone", "code"), ("warehouse", "code"), ("owner", "code")):
        ids = [r["id"] for r in raw(
            f"SELECT id FROM {t} WHERE {k} LIKE 'INIT-%'")]
        for i in ids:
            try:
                req("DELETE", f"/api/{t}/{i}", expect=(200, 204))
            except Exception as e:
                print(f"  跳过 {t}/{i}: {str(e)[:80]}")
    print("清理完成")


def main_cli():
    ap = argparse.ArgumentParser(description="MiniWMS 初始数据播种")
    ap.add_argument("--check", action="store_true", help="只读盘点，不改动")
    ap.add_argument("--reset", action="store_true", help="先清掉本脚本建的数据再重播")
    a = ap.parse_args()

    if a.check:
        check()
        return 0

    if a.reset:
        reset()

    print("── 播种初始数据 ──")
    step("角色（岗位模板）", seed_roles)
    step("账号与数据授权", seed_accounts)
    step("基础主数据", seed_master_data)
    step("货主×仓库格子", seed_owner_grid)

    bad = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n完成：{len(RESULTS) - len(bad)}/{len(RESULTS)} 步成功")
    if bad:
        print("失败步骤：")
        for name, _, detail in bad:
            print(f"  - {name}: {detail}")
        return 1
    print("\n演示账号（密码统一 init123456）：")
    for username, role_name, pwd, *_ in ACCOUNTS:
        print(f"  {username:<14} {role_name:<12} {pwd}")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
