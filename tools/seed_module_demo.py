"""给 M17 / M20 / M21–M26 新增模块补充演示数据（幂等、通过真实 API 写入）。

背景：`tools/seed_demo.py` 只播种 M1–M20 基础主数据与「收货上架→拣货发货」动线，
M21 起的库存调整单 / 盘点方案 / 库存转移单 / 移库单 / 货主仓库格子 / 风险放行日志 /
上架推荐日志等业务单据表一直是空的，对应页面打开没有内容。

本脚本通过 `main.app.test_client()` 调用**真实 API**（等价于浏览器/前端操作）补齐每个模块，
不直接写业务表、不 `--reset`、不删除任何现有数据；每步带哨兵判断，可重复执行。

用法：
    ./.venv/Scripts/python.exe tools/seed_module_demo.py           # 补齐演示数据
    ./.venv/Scripts/python.exe tools/seed_module_demo.py --check    # 只读打印各模块行数

默认写项目根目录的 `miniwms.db`，可用 `MINIWMS_DB` 覆盖。执行前建议先备份该库。
"""
import argparse
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("MINIWMS_DB", os.path.join(_ROOT, "miniwms.db"))
os.environ.setdefault("MINIWMS_ALLOW_ANONYMOUS", "1")

import main  # noqa: E402

DB = os.environ["MINIWMS_DB"]
PREFIX = "DEMO-"
RESULTS = []


# ── 基础设施 ────────────────────────────────────────────────────────────
def raw(sql, params=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def count(table, where="", params=()):
    return raw(f"SELECT COUNT(*) AS n FROM {table} {where}", params)[0]["n"]


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


def module(name, fn):
    """单模块失败不中断其余模块，便于一次跑完看出所有问题。"""
    try:
        detail = fn()
        RESULTS.append((name, "OK", str(detail)))
    except Exception as e:  # noqa: BLE001 - 演示脚本需要收集全部失败
        RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))


def admin_headers():
    body = post("/api/auth/login",
                {"username": "admin", "password": "admin123"})
    return {"Authorization": "Bearer " + body["token"]}


def fixtures():
    """从现有演示库取真实 id，不硬编码主键。"""
    owners = {r["code"]: r["id"] for r in raw("SELECT id, code FROM owner")}
    prods = {r["sku"]: r["id"] for r in raw("SELECT id, sku FROM product")}
    locs = {r["code"]: r["id"] for r in raw("SELECT id, code FROM location")}
    whs = {r["code"]: r["id"] for r in raw("SELECT id, code FROM warehouse")}
    custs = {r["code"]: r["id"] for r in raw("SELECT id, code FROM customer")}
    zones = {r["code"]: r["id"] for r in raw("SELECT id, code FROM zone")}
    return owners, prods, locs, whs, custs, zones


def stock_row(product_id, location_id, min_qty=3):
    rows = raw("SELECT qty, allocated_qty FROM stock WHERE product_id=? "
               "AND location_id=? AND qty>0 ORDER BY qty DESC",
               (product_id, location_id))
    return rows[0] if rows and rows[0]["qty"] - rows[0]["allocated_qty"] >= min_qty else None


def batch_row(product_id, location_id, min_qty=3):
    rows = raw("SELECT id, batch_no, qty, allocated_qty FROM stock_batch "
               "WHERE product_id=? AND location_id=? AND qty>0 ORDER BY qty DESC",
               (product_id, location_id))
    return rows[0] if rows and rows[0]["qty"] - rows[0]["allocated_qty"] >= min_qty else None


# ── 1. M20 储区 / 推荐储区 / 固定货位绑定 ────────────────────────────────
def m20_bindings():
    owners, prods, locs, whs, custs, zones = fixtures()
    shared = zones.get("ZONE-SHARED")
    if not shared:
        return "跳过：ZONE-SHARED 不存在"
    done = []
    # 库位挂到全仓共享默认区（常温），物料设推荐储区，上架推荐页才有理由可展示
    for code, lid in locs.items():
        row = raw("SELECT zone_id FROM location WHERE id=?", (lid,))[0]
        if row["zone_id"] is None:
            put(f"/api/locations/{lid}", {"zone_id": shared})
            done.append("loc:" + code)
    for sku, pid in prods.items():
        row = raw("SELECT default_zone_id FROM product WHERE id=?", (pid,))[0]
        if not row["default_zone_id"]:
            put(f"/api/products/{pid}", {"default_zone_id": shared})
            done.append("prod:" + sku)
    plan = {"SKU-001": "A-01-01", "SKU-002": "A-01-02",
            "SKU-004": "A-02-01", "SKU-101": "A-02-02"}
    for sku, loc in plan.items():
        if sku not in prods or loc not in locs:
            continue
        exists = count("product_location", "WHERE product_id=? AND location_id=?",
                       (prods[sku], locs[loc]))
        if not exists:
            post("/api/product-locations", {
                "product_id": prods[sku], "location_id": locs[loc],
                "priority": 10, "note": PREFIX + "固定货位演示"})
            done.append("bind:" + sku)
    return f"新增 {len(done)} 项；product_location 共 {count('product_location')} 行"


# ── 2. 货主 × 仓库业务关系格子 ──────────────────────────────────────────
def owner_grid():
    owners, prods, locs, whs, custs, zones = fixtures()
    if count("owner_warehouse") > 0:
        return f"已存在 {count('owner_warehouse')} 格，跳过"
    h = admin_headers()
    all_owners = list(owners.values())
    for code, wid in whs.items():
        put(f"/api/owner-warehouses/{wid}", {"owner_ids": all_owners}, headers=h)
    return f"{len(whs)} 个仓 × {len(all_owners)} 个货主 = {count('owner_warehouse')} 格"


# ── 3. M22 盘点方案（全盘 / 部分 / 按批号 / 动碰）────────────────────────
def stocktake_plans():
    owners, prods, locs, whs, custs, zones = fixtures()
    loc_a = locs.get("A-01-01")
    specs = [
        ("DEMO-PLAN-FULL", "全盘（每日全仓）", "full", {}, {}),
        ("DEMO-PLAN-PARTIAL", "部分盘点（A 区美妆位）", "partial",
         {"location_ids": [loc_a]}, {}),
        ("DEMO-PLAN-BATCH", "按批号盘点", "partial",
         {"batch_no": "LEGACY-UNKNOWN"}, {}),
        ("DEMO-PLAN-DYNAMIC", "动碰盘点（近 30 天）", "dynamic", {},
         {"lookback_days": 30, "blind_mode": "hide_book"}),
    ]
    made = 0
    for code, name, scope_type, scope, extra in specs:
        if count("stocktake_plan", "WHERE code=?", (code,)):
            continue
        post("/api/stocktake-plans", {
            "code": code, "name": name, "scope_type": scope_type,
            "scope": scope, "note": PREFIX + "模块演示", **extra})
        made += 1
    return f"新增 {made} 个方案，共 {count('stocktake_plan')} 个"


# ── 4. M22 库存调整单（draft + approved 各一张）─────────────────────────
def stock_adjustments():
    owners, prods, locs, whs, custs, zones = fixtures()
    oid, pid, lid = owners.get("OW-TMALL"), prods.get("SKU-001"), locs.get("A-01-01")
    if not all((oid, pid, lid)) or not stock_row(pid, lid, 5):
        return "跳过：缺少可用库存"
    made = []
    if not count("stock_adjustment", "WHERE note LIKE ?", (PREFIX + "%draft%",)):
        post("/api/stock-adjustments", {
            "reason_code": "报溢", "note": PREFIX + "draft 待审核示例",
            "lines": [{"owner_id": oid, "product_id": pid,
                       "location_id": lid, "delta_qty": 1}]})
        made.append("draft")
    if not count("stock_adjustment", "WHERE note LIKE ?", (PREFIX + "%approved%",)):
        body = post("/api/stock-adjustments", {
            "reason_code": "报损", "note": PREFIX + "approved 已过账示例",
            "lines": [{"owner_id": oid, "product_id": pid,
                       "location_id": lid, "delta_qty": -1}]})
        post(f"/api/stock-adjustments/{body['id']}/approve", {})
        made.append("approved")
    return f"新增 {made}，共 {count('stock_adjustment')} 张 / {count('stock_adjustment_line')} 行"


# ── 5. M17 移库单（open + moved 各一张）─────────────────────────────────
def transfers_m17():
    owners, prods, locs, whs, custs, zones = fixtures()
    oid, pid = owners.get("OW-TMALL"), prods.get("SKU-001")
    src, dst = locs.get("A-01-01"), locs.get("A-01-02")
    if not all((oid, pid, src, dst)) or not stock_row(pid, src, 5):
        return "跳过：缺少可用库存"
    made = []
    for suffix, do_move in (("OPEN", False), ("MOVED", True)):
        code = PREFIX + "TF-" + suffix
        if count("transfer", "WHERE code=?", (code,)):
            continue
        body = post("/api/transfers", {
            "code": code, "owner_id": oid,
            "lines": [{"product_id": pid, "from_location_id": src,
                       "to_location_id": dst, "qty": 2}]})
        tid = body["transfer"]["id"]
        if do_move:
            post(f"/api/transfers/{tid}/move", {})
        made.append(suffix)
    return f"新增 {made}，共 {count('transfer')} 张 / {count('transfer_line')} 行"


# ── 6. M24 库存转移单（draft 空单 + approved 带变更文档）─────────────────
def inventory_transfers_m24():
    owners, prods, locs, whs, custs, zones = fixtures()
    pid, lid = prods.get("SKU-001"), locs.get("A-01-01")
    sb = batch_row(pid, lid, 5) if pid and lid else None
    if not sb:
        return "跳过：缺少可用批次明细"
    made = []
    # draft 空单（不过账，页面可演示关闭/删除）
    if not count("inventory_transfer", "WHERE note LIKE ?", (PREFIX + "%draft%",)):
        post("/api/inventory-transfers", {
            "transfer_type": "batch_correct", "reason_code": "错录补正",
            "note": PREFIX + "draft 空单示例"})
        made.append("draft")
    # approved：分配 → 审核，产出字段级变更文档 + 净零流水
    if not count("inventory_transfer", "WHERE note LIKE ?", (PREFIX + "%approved%",)):
        body = post("/api/inventory-transfers", {
            "transfer_type": "batch_correct", "reason_code": "错录补正",
            "note": PREFIX + "approved 已生效示例"})
        tid = body["id"]
        post(f"/api/inventory-transfers/{tid}/allocate", {
            "lines": [{"stock_batch_id": sb["id"], "qty": 2,
                       "to_batch_no": PREFIX + "LOT-CORRECTED"}]})
        post(f"/api/inventory-transfers/{tid}/approve", {})
        made.append("approved")
    return (f"新增 {made}，共 {count('inventory_transfer')} 张 / "
            f"{count('inventory_transfer_line')} 行 / "
            f"{count('inventory_change_log')} 条变更文档")


# ── 7. M25 上架推荐日志 ─────────────────────────────────────────────────
def putaway_recommendations():
    """预览日志按 asn_line 去重，重跑不累积（预览本身不改库存）。"""
    logged = count("putaway_recommendation")
    if logged >= 5:
        return f"已有 {logged} 条推荐日志，跳过"
    rows = raw(
        "SELECT a.id AS asn_id, al.id AS line_id FROM asn a "
        "JOIN asn_line al ON al.asn_id = a.id "
        "WHERE NOT EXISTS (SELECT 1 FROM putaway_recommendation pr "
        "                  WHERE pr.asn_id = a.id AND pr.asn_line_id = al.id) "
        "ORDER BY a.id LIMIT 5")
    made = 0
    for r in rows:
        get(f"/api/putaway/recommend?asn_id={r['asn_id']}"
            f"&line_id={r['line_id']}&qty=1&top_n=5")
        made += 1
    return f"新增推荐预览 {made} 次，日志共 {count('putaway_recommendation')} 条"


# ── 8. M23 专属库存风险放行 + M21 batch_location 实物分布 ────────────────
def reservation_risk():
    """专属库存发给其他客户：事前预检报风险 → 用户确认 → 写放行日志。

    两个必须同时成立的演示前提（早期脚本踩过坑，见下方注释）：

    * **独立空库位**：专属批次要落在只有它一个批次的库位（A-02-03）。若与
      大量通用库存同库位，FEFO 会先消耗通用批次，`inspect_dn()` 按同一套
      分摊推断自然判定「无风险」——那是正确行为，不是漏报。
    * **预检在拣货之后**：`reservation-check` 读的是 `dn_pick`。拣货前调用
      时明细为空，必然返回 0 风险；前端真实时序是「拣满 → 点发货 → 预检」。
    """
    owners, prods, locs, whs, custs, zones = fixtures()
    oid, pid = owners.get("OW-TMALL"), prods.get("SKU-001")
    rcv, sto = locs.get("RECV-01"), locs.get("A-02-03")
    c1, c2 = custs.get("C-GOLD"), custs.get("C-SILVER")
    if not all((oid, pid, rcv, sto, c1, c2)):
        return "跳过：缺主数据"
    logged = count("customer_reservation_log")
    if logged:
        return f"已存在 {logged} 条风险放行日志，跳过"
    asn_code, dn_code = PREFIX + "RISK2-ASN", PREFIX + "RISK2-DN"
    if count("asn", "WHERE code=?", (asn_code,)) or count("dn", "WHERE code=?", (dn_code,)):
        return "检测到上次未完成的演示单据，请人工检查（本次不重复创建）"

    # 8.1 专属库存：ASN 明细指定客户 → 验收 → 上架到独立空库位（写 batch_location）
    asn = post("/api/asns", {
        "code": asn_code, "owner_id": oid,
        "source_warehouse": whs.get("WH-SZ"), "doc_type": "采购",
        "note": PREFIX + "专属库存风险演示（独立库位）",
        "lines": [{"product_id": pid, "expected_qty": 6,
                   "batch_no": PREFIX + "LOT-R2",
                   "designated_customer_id": c1}]})
    aid, alid = asn["asn"]["id"], asn["lines"][0]["id"]
    post(f"/api/asns/{aid}/receive", {"line_id": alid, "location_id": rcv, "qty": 6})
    post(f"/api/asns/{aid}/putaway", {
        "line_id": alid, "from_location_id": rcv, "to_location_id": sto, "qty": 6})

    # 8.2 发给另一个客户：先拣货，再事前预检，确认后发货（写日志、不拦截）
    dn = post("/api/dns", {
        "code": dn_code, "owner_id": oid, "customer_id": c2,
        "note": PREFIX + "跨客户风险放行演示",
        "lines": [{"product_id": pid, "ordered_qty": 2}]})
    did, dlid = dn["dn"]["id"], dn["lines"][0]["id"]
    post(f"/api/dns/{did}/pick", {"line_id": dlid, "location_id": sto, "qty": 2})
    chk = get(f"/api/dns/{did}/reservation-check")
    risks = chk.get("risks") or []
    if not risks:
        raise RuntimeError("预检未报风险：专属批次未被 FEFO 命中，演示场景失效")
    shipped = post(f"/api/dns/{did}/ship", {"risk_acknowledged": True})
    return (f"专属 ASN + 跨客户 DN 已跑通；预检风险 {len(risks)} 条，"
            f"发货放行写入 {shipped.get('risk_logged')} 条，"
            f"日志合计 {count('customer_reservation_log')} 条，"
            f"batch_location {count('batch_location')} 行")


# ── 9. M26 规则配置编辑器：字典 / 示例规则 / 采纳驳回日志 ────────────────
def m26_rule_editor_data():
    """让「上架规则配置」页打开即有可查数据（幂等、全部走真实 API）。

    * 演示字典项 DEMO-EDI（带默认储区），方便在条件组里试选；
    * 一条**停用**的示例规则行（13 维条件组 + fallback 链 + 扩展评分写法），
      只作编辑器素材，默认不参与命中、不改变任何过账拦截；
    * 把已有 preview 日志分别标成 adopt / reject，让命中率·采纳率·驳回率统计卡有数。
    """
    made = []
    owners, prods, locs, whs, custs, zones = fixtures()

    if not count("external_doc_type_dict", "WHERE code=?", ("DEMO-EDI",)):
        post("/api/external-doc-types", {
            "code": "DEMO-EDI", "name": "演示·EDI 推送单", "enabled": 1,
            "default_zone_id": zones.get("ZONE-SHARED"),
            "note": PREFIX + "M26 演示字典项，可在规则配置页启停或删除",
        })
        made.append("字典 DEMO-EDI")

    rule_name = "DEMO 规则示例（13 维条件组）"
    if not count("putaway_rule", "WHERE name=?", (rule_name,)):
        sets = get("/api/putaway-rule-sets").get("rule_sets") or []
        if not sets:
            raise RuntimeError("缺少策略包，请先执行 init_db() 生成 M25 种子")
        post("/api/putaway-rules", {
            "rule_set_id": sets[0]["id"], "rule_type": "putaway", "priority": 777,
            "name": rule_name, "enabled": 0,
            "note": PREFIX + "M26 演示规则，默认停用、不参与命中与拦截",
            "condition": {"doc_type": "采购", "is_cold_chain": 0,
                          "external_doc_type": "DEMO-EDI"},
            "steps": [{"type": "zone_by_code", "zone_code": "ZONE-SHARED"},
                      {"type": "empty_in_zone"}],
            "limits": {}, "score": {"R09": 25},
        })
        made.append("停用示例规则")

    # 哨兵：已有 adopt / reject 就不再改动任何日志，重复执行不漂移
    if not count("putaway_recommendation", "WHERE action='adopt'"):
        row = raw("SELECT id, asn_line_id FROM putaway_recommendation "
                  "WHERE action='preview' ORDER BY id LIMIT 1")
        if row:
            loc = next(iter(locs.values()), None)
            post(f"/api/putaway-recommendations/{row[0]['id']}/action", {
                "action": "adopt", "chosen_location_id": loc, "operator": "demo-seed",
            })
            made.append("采纳日志")
    if not count("putaway_recommendation", "WHERE action='reject'"):
        row = raw("SELECT id FROM putaway_recommendation "
                  "WHERE action='preview' ORDER BY id LIMIT 1")
        if row:
            post(f"/api/putaway-recommendations/{row[0]['id']}/action", {
                "action": "reject", "override_reason": "客户指定货位",
                "operator": "demo-seed",
            })
            made.append("驳回日志")
    return (f"新增 {made or '无（数据已存在）'}；字典 {count('external_doc_type_dict')} 项 / "
            f"规则行 {count('putaway_rule')} 行 / 日志 {count('putaway_recommendation')} 条")


def consistency():
    """顺带体检：I1 / I2 / I3 与推荐引擎可用性。"""
    out = []
    for path, key in (("/api/stock/reconcile", "consistent"),
                      ("/api/stock/batch-reconcile", "consistent")):
        try:
            d = get(path)
            out.append(f"{path}={'OK' if d.get(key) else 'FAIL'}")
        except Exception as e:  # noqa: BLE001
            out.append(f"{path}=ERR({e})")
    return "; ".join(out)


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读打印各模块行数")
    args = ap.parse_args()

    tables = [
        ("product_location 固定货位", "product_location"),
        ("owner_warehouse 货主仓库格子", "owner_warehouse"),
        ("stocktake_plan 盘点方案", "stocktake_plan"),
        ("stock_adjustment 库存调整单", "stock_adjustment"),
        ("transfer 移库单(M17)", "transfer"),
        ("inventory_transfer 库存转移单(M24)", "inventory_transfer"),
        ("inventory_change_log 变更文档", "inventory_change_log"),
        ("putaway_recommendation 推荐日志(M25/M26)", "putaway_recommendation"),
        ("external_doc_type_dict 字典(M26)", "external_doc_type_dict"),
        ("putaway_rule 规则行(M25/M26)", "putaway_rule"),
        ("customer_reservation_log 风险放行(M23)", "customer_reservation_log"),
        ("batch_location 批次实物分布(M21)", "batch_location"),
        ("stocktake 盘点单", "stocktake"),
        ("stock_batch 批次明细(M21)", "stock_batch"),
    ]
    if args.check:
        print(f"库：{DB}\n")
        for label, t in tables:
            print(f"  {label:<42} {count(t):>4} 行")
        print("\n一致性：" + consistency())
        return 0

    main.init_db()
    print(f"补数目标库：{DB}\n")

    module("M20 储区/推荐储区/固定货位", m20_bindings)
    module("货主×仓库格子", owner_grid)
    module("M22 盘点方案", stocktake_plans)
    module("M22 库存调整单", stock_adjustments)
    module("M17 移库单", transfers_m17)
    module("M24 库存转移单", inventory_transfers_m24)
    module("M25 推荐日志", putaway_recommendations)
    module("M23 风险放行 + M21 批次分布", reservation_risk)
    module("M26 规则配置演示数据", m26_rule_editor_data)

    print("── 执行结果 ──")
    for name, status, detail in RESULTS:
        print(f"  [{status:<4}] {name:<30} {detail}")
    print("\n── 各模块行数 ──")
    for label, t in tables:
        print(f"  {label:<42} {count(t):>4} 行")
    print("\n── 一致性体检 ──\n  " + consistency())

    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 模块成功")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_cli())
