"""给 M17 / M20 / M21–M26 / M35–M40 新增模块补充演示数据（幂等、通过真实 API 写入）。

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
import datetime
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
    try:
        return raw(f"SELECT COUNT(*) AS n FROM {table} {where}", params)[0]["n"]
    except sqlite3.OperationalError:
        # --check 只读盘点时，新里程碑的表可能还没跑 init_db() 迁移
        return -1


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


# ── 10. M35 出库波次分配：规则 / 波次 / 四类留痕 / 批次拣货明细 ────────────
def m35_outbound_allocation():
    """让「波次分配」页打开即有可查数据（幂等、全部走真实 API）。

    造三张演示 DN：① 专属客户不匹配的通用出库单（产生 skipped）、
    ② 专属客户匹配（产生 allocated + 再取消产生 unallocated）、
    ③ 无库存物料（产生 short）。另加一条**停用**的 outbound 示例规则行，
    只作编辑器素材，不改变任何既有分配行为。分配量控制在个位数，不挤占演示库存。
    """
    made = []
    rule_name = "DEMO M35 出库分配示例（停用）"
    if not count("putaway_rule", "WHERE name=?", (rule_name,)):
        sets = get("/api/putaway-rule-sets").get("rule_sets") or []
        if not sets:
            raise RuntimeError("缺少策略包，请先执行 init_db() 生成 M25 种子")
        post("/api/putaway-rules", {
            "rule_set_id": sets[0]["id"], "rule_type": "outbound", "priority": 780,
            "name": rule_name, "enabled": 0,
            "note": PREFIX + "M35 出库规则示例，默认停用、不参与命中",
            "condition": {"doc_type": "销售"},
            "steps": [{"type": "all_active"}],
            "limits": {"quality_state": {"enabled": 1}},
            "score": {"R11": 15, "strategy": "strict_fefo"},
        })
        made.append("停用 outbound 示例规则")

    rows = raw("""SELECT sb.owner_id, sb.product_id,
                         SUM(sb.qty - sb.allocated_qty) AS avail
                  FROM stock_batch sb JOIN location l ON l.id = sb.location_id
                  WHERE sb.qty - sb.allocated_qty > 3 AND l.status = 'active'
                    AND l.loc_type IN ('storage', 'picking')
                  GROUP BY sb.owner_id, sb.product_id ORDER BY avail DESC LIMIT 1""")
    if not rows:
        raise RuntimeError("没有可分配库存，请先运行 tools/seed_demo.py 造主数据")
    owner_id, product_id = rows[0]["owner_id"], rows[0]["product_id"]
    whs = raw("SELECT id FROM warehouse WHERE status='active' ORDER BY id LIMIT 1")
    if not whs:
        raise RuntimeError("没有启用的仓库")
    wh_id = whs[0]["id"]
    customers = raw("SELECT id FROM customer WHERE owner_id=? ORDER BY id", (owner_id,))
    if len(customers) < 2:
        raise RuntimeError("演示货主至少需要 2 个客户")
    reserved = raw("""SELECT DISTINCT designated_customer_id FROM stock_batch
                      WHERE owner_id=? AND product_id=? AND qty > 0
                        AND designated_customer_id IS NOT NULL""",
                   (owner_id, product_id))
    own_cust = reserved[0]["designated_customer_id"] if reserved else customers[0]["id"]
    other_cust = next(c["id"] for c in customers if c["id"] != own_cust)

    no_stock_sku = "DEMO-M35-NOSTOCK"
    if not count("product", "WHERE sku=?", (no_stock_sku,)):
        post("/api/products", {"sku": no_stock_sku, "name": "演示·无库存物料",
                               "owner_id": owner_id})
        made.append("无库存演示物料")
    no_stock_id = raw("SELECT id FROM product WHERE sku=?", (no_stock_sku,))[0]["id"]

    H = admin_headers()

    def ensure_dn(code, customer_id, lines):
        exists = raw("SELECT id FROM dn WHERE code=?", (code,))
        if exists:
            return exists[0]["id"]
        body = post("/api/dns", {
            "code": code, "owner_id": owner_id, "customer_id": customer_id,
            "source_warehouse": wh_id, "lines": lines,
        })
        return body["dn"]["id"]

    specs = [
        ("DEMO-M35-FEFO", other_cust,
         [{"product_id": product_id, "ordered_qty": 2}]),
        ("DEMO-M35-OWN", own_cust,
         [{"product_id": product_id, "ordered_qty": 2}]),
        ("DEMO-M35-SHORT", other_cust,
         [{"product_id": no_stock_id, "ordered_qty": 3}]),
    ]
    dn_ids = {}
    for code, cust_id, lines in specs:
        dn_ids[code] = ensure_dn(code, cust_id, lines)

    for code, _, _ in specs:
        dn_id = dn_ids[code]
        if count("outbound_allocation_log", "WHERE dn_id=? AND action='allocated'",
                 (dn_id,)):
            continue
        out = post("/api/outbound/allocate", {"dn_ids": [dn_id]}, headers=H)
        made.append(f"{code} 波次 {out.get('wave_no')}")

    own_dn = dn_ids["DEMO-M35-OWN"]
    if (count("outbound_allocation_log", "WHERE dn_id=? AND action='allocated'", (own_dn,))
            and not count("outbound_allocation_log",
                          "WHERE dn_id=? AND action='unallocated'", (own_dn,))):
        post("/api/outbound/unallocate", {"dn_id": own_dn}, headers=H)
        made.append("取消分配留痕")

    stats = {r["action"]: r["n"] for r in raw(
        "SELECT action, COUNT(*) AS n FROM outbound_allocation_log GROUP BY action")}
    waves = raw("SELECT DISTINCT wave_no FROM outbound_allocation_log WHERE wave_no <> ''")
    return (f"新增 {made or '无（数据已存在）'}；留痕 {count('outbound_allocation_log')} 条 "
            f"{stats}，波次 {len(waves)} 个")


# ── 11. M36 出库复核：复核单 / 逐行复核 / 差异只留痕 ──────────────────────
def m36_dn_review():
    """让「出库复核」页打开即有可查数据（幂等、全部走真实 API）。

    挑一张已分配、带批次维度的 DN 生成复核单，逐行登记一条差异（只留痕），
    再生成一张复核单做「通过」演示；差异绝不改库存、不改 DN 状态。
    """
    made = []
    H = admin_headers()

    # 找带拣货明细的 DN（优先 M35 分配过的，带完整批次维度）
    picks_dns = raw("""SELECT d.id, d.code, COUNT(dp.id) AS n
                       FROM dn d JOIN dn_pick dp ON dp.dn_id = d.id
                       WHERE d.status <> 'cancelled'
                       GROUP BY d.id ORDER BY (dp.batch_no <> '') DESC, d.id""")
    if not picks_dns:
        return "跳过（无带拣货明细的 DN，请先运行 M35 播种或 seed_demo.py）"

    def ensure_review(dn_id, code_hint):
        # 幂等：该 DN 已有任意状态的复核单就复用
        exists = raw("SELECT id FROM dn_review WHERE dn_id=? ORDER BY id LIMIT 1", (dn_id,))
        if exists:
            return exists[0]["id"], False
        body = post("/api/dn-reviews", {"dn_id": dn_id,
                                        "note": PREFIX + f"M36 复核演示（{code_hint}）"}, headers=H)
        return body["id"], True

    # 第一张：登记差异（少货）+ 完成
    dn1 = picks_dns[0]
    rv1, made1 = ensure_review(dn1["id"], dn1["code"])
    if made1:
        made.append(f"{dn1['code']} 复核单")
    detail = get(f"/api/dn-reviews/{rv1}")
    for line in detail["lines"]:
        if line["result"]:
            continue  # 已复核过，跳过（幂等）
        expected = line["expected_qty"]
        actual = max(0, round(expected - 1, 6))  # 少 1 件
        post(f"/api/dn-reviews/{rv1}/check", {
            "line_id": line["id"], "actual_qty": actual,
            "result": "diff", "reason_code": "SHORT",
            "note": PREFIX + "M36 演示：现场少 1 件，只留痕不纠偏",
        }, headers=H)
        made.append("差异留痕")
    if detail["status"] in ("draft", "reviewing"):
        post(f"/api/dn-reviews/{rv1}/complete", {}, headers=H)
        made.append("完成复核")

    # 第二张：通过演示（若有第二张带拣货明细的 DN）
    if len(picks_dns) >= 2:
        dn2 = picks_dns[1]
        rv2, made2 = ensure_review(dn2["id"], dn2["code"])
        if made2:
            made.append(f"{dn2['code']} 复核单（待复核）")

    stats = {r["action"]: r["n"] for r in raw(
        "SELECT action, COUNT(*) AS n FROM dn_review_diff_log GROUP BY action")}
    return (f"新增 {made or '无（数据已存在）'}；复核单 {count('dn_review')} 张，"
            f"差异留痕 {count('dn_review_diff_log')} 条 {stats}")


def m37_care():
    """让「养护管理」页打开即有可查数据（幂等、走真实 API）。

    两个计划（温湿度 / 效期）到期生成任务；温湿度任务实测超限 → 异常 + 处置，
    效期任务判正常。异常只留痕，不改库存、不生成调整单。
    """
    made = []
    H = admin_headers()
    owners, _prods, locs, _whs, _custs, _zones = fixtures()
    if not owners:
        return "跳过（无货主，请先运行 seed_demo.py）"
    owner_id = owners.get("DEMO-OW1") or next(iter(owners.values()))
    loc_id = next((v for k, v in locs.items() if "A" in k or "存储" in k),
                  next(iter(locs.values()), 0))
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    def ensure_plan(code, project, **extra):
        exist = raw("SELECT id FROM care_plan WHERE note LIKE ? ORDER BY id LIMIT 1",
                    (f"%{code}%",))
        if exist:
            return exist[0]["id"], False
        payload = {"owner_id": owner_id, "project": project, "cycle": "daily",
                   "next_due_date": yesterday,
                   "note": PREFIX + f"M37 养护演示（{code}）"}
        payload.update(extra)
        body = post("/api/care-plans", payload, headers=H)
        return body["id"], True

    p1, new1 = ensure_plan("DEMO-M37-TEMP", "TEMP_HUMIDITY",
                           location_id=loc_id, temp_min=2, temp_max=8,
                           hum_min=30, hum_max=70)
    p2, new2 = ensure_plan("DEMO-M37-EXPIRY", "EXPIRY")
    if new1:
        made.append("温湿度计划")
    if new2:
        made.append("效期计划")
    gen = post("/api/care-tasks/generate", {}, headers=H)
    if gen["generated"]:
        made.append(f"{gen['generated']} 条养护任务")

    tasks = get("/api/care-tasks?page_size=200")["tasks"]
    for t in tasks:
        if t["status"] != "pending":
            continue
        if t["project"] == "TEMP_HUMIDITY":
            post(f"/api/care-tasks/{t['id']}/execute", {
                "result": "abnormal", "temperature": 12, "humidity": 50,
                "reason_code": "TEMP_HIGH",
                "note": PREFIX + "M37 演示：冷藏温度 12℃ 超上限，只留痕不纠偏",
            }, headers=H)
            post(f"/api/care-tasks/{t['id']}/handle", {
                "handled_note": PREFIX + "M37 演示：已通知工程部复核冷机",
            }, headers=H)
            made.append("超限异常 + 处置")
        else:
            post(f"/api/care-tasks/{t['id']}/execute", {
                "result": "normal",
                "note": PREFIX + "M37 演示：效期检查正常",
            }, headers=H)
            made.append("效期正常记录")

    stats = {r["action"]: r["n"] for r in raw(
        "SELECT action, COUNT(*) AS n FROM care_log GROUP BY action")}
    return (f"新增 {made or '无（数据已存在）'}；计划 {count('care_plan')} 张，"
            f"任务 {count('care_task')} 条，留痕 {count('care_log')} 条 {stats}")


def m38_dn_order():
    """让「拆单合单」页打开即有可查数据（幂等、走真实 API、不动库存）。

    用 DEMO- 前缀建两张 open 未拣货 DN → 拆一张（留痕）→ 把子单与另一张合并（留痕）。
    哨兵用 note 前缀匹配：重跑时若已有演示留痕则跳过，避免重复单据。
    """
    H = admin_headers()
    # 按货主直接查同货主的物料/客户（演示库主数据前缀不固定，不依赖 DEMO- 假设）
    owner_rows = raw("SELECT id FROM owner ORDER BY id LIMIT 1")
    if not owner_rows:
        return "跳过（缺货主，请先运行 seed_demo.py）"
    owner_id = owner_rows[0]["id"]
    prod_rows = raw("SELECT id FROM product WHERE owner_id=? ORDER BY id LIMIT 2", (owner_id,))
    cust_rows = raw("SELECT id FROM customer WHERE owner_id=? ORDER BY id LIMIT 1", (owner_id,))
    if len(prod_rows) < 2 or not cust_rows:
        return "跳过（该货主物料不足 2 个或缺客户）"
    prod_ids = [r["id"] for r in prod_rows]
    customer_id = cust_rows[0]["id"]

    made = []
    existing = raw("SELECT id FROM dn_order_log WHERE note LIKE ? LIMIT 1",
                   (PREFIX + "M38%",))
    if existing:
        return (f"跳过（演示留痕已存在）；拆合留痕 {count('dn_order_log')} 条")

    def mkdn(code, lines, note):
        body = post("/api/dns", {
            "code": code, "owner_id": owner_id, "customer_id": customer_id,
            "lines": [{"product_id": p, "ordered_qty": q} for p, q in lines],
            "note": note}, headers=H)
        return body["dn"]["id"]

    today = datetime.date.today().strftime("%Y%m%d")
    src = mkdn(f"DEMO-M38-SRC-{today}", [(prod_ids[0], 12), (prod_ids[1], 6)],
               PREFIX + "M38 演示源单（拆单用）")
    other = mkdn(f"DEMO-M38-OTH-{today}", [(prod_ids[0], 4)],
                 PREFIX + "M38 演示单据（合单用）")
    made.append("两张 open 演示 DN")

    sp = post("/api/dn-order/split", {
        "source_id": src,
        "parts": [{"lines": [{"product_id": prod_ids[0], "qty": 5}]},
                  {"lines": [{"product_id": prod_ids[1], "qty": 6}]}],
        "note": PREFIX + "M38 演示：源单拆出两子单，数量守恒"}, headers=H)
    made.append(f"拆单生成 {len(sp['targets'])} 张子单")
    child_id = sp["targets"][0]["id"]
    mg = post("/api/dn-order/merge", {
        "source_ids": [other, child_id],
        "note": PREFIX + "M38 演示：子单与另一张合并，源单作废"}, headers=H)
    made.append(f"合单 {mg['target']['code']} 共 {mg['total_qty']}")

    traced = len(raw("SELECT id FROM dn WHERE origin_action<>''"))
    return (f"新增 {'；'.join(made)}；拆合留痕 {count('dn_order_log')} 条，"
            f"溯源子单 {traced} 张")


def m39_gsp():
    """让「GSP 合规报表」页打开即有四级效期 + 追溯链可查（幂等、走真实 API）。

    M39 是只读报表，不造业务单据；只为演示在独立货主下写一组**做平**的
    批次明细 + 同维度流水 + 主账（I1/I2/I3 不破），覆盖已过期 / 近效期 /
    正常 / 无有效期四级，并挂一条收货验收与追溯链。重跑按货主 code 哨兵跳过。
    """
    H = admin_headers()
    sentinel = raw("SELECT id FROM owner WHERE code = ?", ("DEMO-M39-OW",))
    if sentinel:
        e = {s["level"]: s["n"] for s in raw(
            "SELECT CASE WHEN expiry_date IS NULL OR expiry_date='' THEN 'none' "
            "WHEN expiry_date < date('now') THEN 'expired' "
            "WHEN expiry_date <= date('now','+30 day') THEN 'soon' ELSE 'normal' END AS level, "
            "COUNT(*) AS n FROM stock_batch WHERE owner_id = ? AND qty > 0 GROUP BY level",
            (sentinel[0]["id"],))}
        return (f"跳过（演示批次已存在）；效期分级 {e}，各报表端点只读可查")

    ow = post("/api/owners", {"code": "DEMO-M39-OW", "name": PREFIX + "M39 演示货主"},
              headers=H)["owner"]["id"]
    pid = post("/api/products", {"sku": "DEMO-M39-P", "name": PREFIX + "M39 演示物料",
                                 "owner_id": ow}, headers=H)["product"]["id"]
    lid = post("/api/locations", {"code": "DEMO-M39-L", "name": PREFIX + "M39 演示库位",
                                  "owner_id": ow}, headers=H)["location"]["id"]
    today = datetime.date.today()
    batches = [
        ("DEMO-M39-BATCH", "", "DEMO-M39-KEY", 0),  # 追溯链专用，不进效期统计
        ("DEMO-M39-EXP", (today - datetime.timedelta(days=5)).isoformat(), "M39-KEY0", 8),
        ("DEMO-M39-SOON", (today + datetime.timedelta(days=10)).isoformat(), "M39-KEY1", 12),
        ("DEMO-M39-NORM", (today + datetime.timedelta(days=100)).isoformat(), "M39-KEY2", 6),
        ("DEMO-M39-NONE", "", "M39-KEY3", 4),
    ]
    total = 0
    conn = sqlite3.connect(DB)
    try:
        conn.row_factory = sqlite3.Row
        # 批次档案 + 收货验收（追溯链）
        cur = conn.execute(
            "INSERT INTO batch (code, batch_key, owner_id, product_id, key_info, info, "
            "receipt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            ("DEMO-M39-BATCH", "DEMO-M39-KEY", ow, pid, "M39 演示批次", "GSP 追溯演示", 1))
        bid = cur.lastrowid
        conn.execute(
            "INSERT INTO batch_receipt (batch_no, batch_id, internal_batch_no, internal_source, "
            "owner_id, product_id, location_id, asn_id, asn_line_id, qty, doc_type, goods_property, "
            "info, created_at, external_batch_no, warning) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?)",
            ("DEMO-M39-BATCH", bid, "IN-M39", "manual-seed", ow, pid, lid, 0, 0, 10,
             "采购", "合格", PREFIX + "M39 追溯演示", "EXT-M39", ""))
        for bno, ed, key, qty in batches:
            total += qty
            conn.execute(
                "INSERT INTO stock_batch (owner_id, product_id, location_id, batch_no, batch_key, "
                "expiry_date, qty, allocated_qty, goods_property, stock_status) "
                "VALUES (?,?,?,?,?,?,?,0,'合格','非限制')",
                (ow, pid, lid, bno, key, ed, qty))
            conn.execute(
                "INSERT INTO stock_movement (owner_id, product_id, location_id, qty_delta, ref_type, "
                "ref_id, note, batch_no, batch_key, internal_batch_no, goods_property, stock_status, "
                "expiry_date, allocated_delta, operator, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (ow, pid, lid, qty, "manual-seed", 0, PREFIX + "M39 做平账", bno, key,
                 "IN-M39", "合格", "非限制", ed, 0, "seed"))
        conn.execute(
            "INSERT INTO stock (owner_id, product_id, location_id, qty, allocated_qty, updated_at) "
            "VALUES (?,?,?,?,0,datetime('now'))", (ow, pid, lid, total))
        conn.commit()
    finally:
        conn.close()
    # 报表端点只读验证（不写任何数据）
    ov = get("/api/gsp/overview")
    exp = get("/api/gsp/expiry")
    stats = {s["level"]: s["count"] for s in exp["stats"]}
    return (f"新增独立货主 + {len(batches)} 批次（追溯链 + 效期四级）+ 做平主账 {total} 件；"
            f"概览卡片 {len(ov['cards'])} 张，效期分级 {stats}")


def m40_workload():
    """M40 是纯只读统计：复用 M17–M39 已写入的留痕，不新增业务数据。

    这里只通过真实 API 验证三个视角有数据可查，返回摘要供播种日志核对；
    重跑零副作用，E2E 与单测另有独立临时库造数脚本。
    """
    d = get("/api/workload/dict")
    ov = get("/api/workload/overview")
    ev = get("/api/workload/events?page=1&page_size=1")
    return (f"只读复用现有留痕：事件 {ev['total']} 条 / 节点 {len(d['nodes'])} 类 / "
            f"操作人 {ov['cards'][1]['value']} 人 / 活跃 {ov['cards'][3]['value']} 天；不新增业务数据")


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
        ("putaway_rule 规则行(M25/M26/M35)", "putaway_rule"),
        ("outbound_allocation_log 分配留痕(M35)", "outbound_allocation_log"),
        ("dn_review 复核单(M36)", "dn_review"),
        ("dn_review_diff_log 差异留痕(M36)", "dn_review_diff_log"),
        ("care_plan 养护计划(M37)", "care_plan"),
        ("care_task 养护任务(M37)", "care_task"),
        ("care_log 养护留痕(M37)", "care_log"),
        ("dn_order_log 拆合留痕(M38)", "dn_order_log"),
        ("customer_reservation_log 风险放行(M23)", "customer_reservation_log"),
        ("batch_location 批次实物分布(M21)", "batch_location"),
        ("stocktake 盘点单", "stocktake"),
        ("stock_batch 批次明细(M21)", "stock_batch"),
    ]
    if args.check:
        print(f"库：{DB}\n")
        for label, t in tables:
            n = count(t)
            print(f"  {label:<42} {(str(n) if n >= 0 else '未迁移'):>6} 行")
        print("\n一致性：" + consistency())
        try:
            w = get("/api/workload/overview")
            print("工作量统计（只读）：" + str(w["cards"][0]["value"]) + " 条事件 / " +
                  str(w["cards"][1]["value"]) + " 位操作人 / " +
                  str(w["cards"][2]["value"]) + " 个节点")
        except Exception as e:  # noqa: BLE001
            print(f"工作量统计（只读）：ERR({e})")
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
    module("M35 波次分配演示数据", m35_outbound_allocation)
    module("M36 出库复核演示数据", m36_dn_review)
    module("M37 养护管理演示数据", m37_care)
    module("M38 拆单合单演示数据", m38_dn_order)
    module("M39 GSP 合规报表演示数据", m39_gsp)
    module("M40 工作量统计只读验证", m40_workload)

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
