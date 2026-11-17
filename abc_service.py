"""ABC 分类：补齐上架规则条件组里「有维度无取值」的那一维。

背景
----
M25 的规则条件组里有 ``abc_class`` 这一维，M26 编辑器也能配，但
``putaway_rules.build_context()`` 里一直写死 ``""`` —— **没有任何数据源**，
于是种子里那条「A 类黄金拣货位」（条件组 ``{"abc_class": "A"}``）
**永远不可能命中**，这条规则形同虚设。

口径
----
- **按货主分别计算**：不同货主的品类结构差异很大，全局算一个 ABC 没有业务意义；
- **依据近 N 天出库金额**（``stock_movement.qty_delta < 0`` × 采购价），
  降序累计占比：
  - 累计 ≤ 80% → **A**（高价值，重点保管、优先拣货）
  - 累计 ≤ 95% → **B**
  - 其余 → **C**（长尾低值）
- **无出库记录不分类**：留空表示「不限」，不硬凑成 C —— 硬凑会让
  「按 C 类走异常区」这类规则误伤从未卖过的货。

本模块**只写 ``product.abc_class`` 一列**，不改库存、不改单据。
"""
import datetime

import scope as S

import constants as C


class AbcError(Exception):
    """业务校验失败 → 路由层转 400。"""

    def __init__(self, message, code="ABC_BAD_PARAM"):
        super().__init__(message)
        self.message = message
        self.code = code


def _window(days):
    today = datetime.date.today()
    d_from = today - datetime.timedelta(days=max(1, int(days)) - 1)
    return d_from.isoformat(), today.isoformat()


def compute(db, days=C.ABC_WINDOW_DAYS):
    """算出每个货品的 ABC 分类（**只读**，不写库）。

    返回 ``[{product_id, sku, name, owner_id, amount, cum_ratio, abc_class}]``，
    按货主分组排序，便于前端分组展示。
    """
    d_from, d_to = _window(days)
    rows = db.execute(
        """SELECT m.owner_id AS owner_id, m.product_id AS product_id,
                  COALESCE(SUM(-m.qty_delta * COALESCE(p.purchase_price, 0)), 0) AS amount
           FROM stock_movement m
           LEFT JOIN product p ON p.id = m.product_id
           WHERE m.qty_delta < 0 AND m.created_at >= ? AND m.created_at <= ?
           GROUP BY m.owner_id, m.product_id""",
        (f"{d_from} 00:00:00", f"{d_to} 23:59:59"),
    ).fetchall()

    by_owner = {}
    for r in rows:
        amt = float(r["amount"] or 0)
        if amt <= C.ABC_MIN_AMOUNT:
            continue
        by_owner.setdefault(r["owner_id"], []).append(
            {"product_id": r["product_id"], "amount": amt})

    prods = {r["id"]: r for r in db.execute(
        "SELECT id, sku, name, owner_id FROM product")}
    out = []
    for owner_id, items in by_owner.items():
        items.sort(key=lambda x: (-x["amount"], x["product_id"]))
        total = sum(x["amount"] for x in items) or 0
        cum = 0.0
        for idx, it in enumerate(items):
            cum += it["amount"]
            ratio = cum / total if total else 0
            # 金额最大的那件**一定是 A**：否则货主只有一两个品时，
            # 它的累计占比会到 100%，被误判成 C 类长尾。
            if idx == 0 or ratio <= C.ABC_A_CUM:
                cls = "A"
            elif ratio <= C.ABC_B_CUM:
                cls = "B"
            else:
                cls = "C"
            p = prods.get(it["product_id"])
            out.append({
                "product_id": it["product_id"],
                "sku": p["sku"] if p else "",
                "name": p["name"] if p else "",
                "owner_id": owner_id,
                "amount": round(it["amount"], 4),
                "cum_ratio": round(ratio, 4),
                "abc_class": cls,
            })
    out.sort(key=lambda x: (x["owner_id"] if x["owner_id"] is not None else 0,
                            -x["amount"]))
    return out


def overview(db, days=C.ABC_WINDOW_DAYS):
    """当前分布概览（**只读**）：各货主的 A/B/C/未分类数量与金额。"""
    rows = db.execute(
        "SELECT owner_id, abc_class, COUNT(*) AS n FROM product"
        " WHERE abc_class != '' GROUP BY owner_id, abc_class"
    ).fetchall()
    dist = {}
    for r in rows:
        item = dist.setdefault(r["owner_id"], {
            "owner_id": r["owner_id"], "A": 0, "B": 0, "C": 0, "total": 0})
        item[r["abc_class"]] = r["n"]
        item["total"] += r["n"]
    unclassified = db.execute(
        "SELECT COUNT(*) AS n FROM product WHERE abc_class = ''").fetchone()["n"]
    return {
        "days": days,
        "distribution": sorted(dist.values(),
                              key=lambda x: (x["owner_id"] is None, x["owner_id"])),
        "unclassified": unclassified,
        "thresholds": {"a": C.ABC_A_CUM, "b": C.ABC_B_CUM},
    }


def apply(db, days=C.ABC_WINDOW_DAYS, owner_id=None):
    """重算并写回 ``product.abc_class``；返回本次变更明细。"""
    plan = compute(db, days)
    if owner_id not in (None, "", "all"):
        plan = [p for p in plan if str(p["owner_id"]) == str(owner_id)]

    before = {r["id"]: r["abc_class"] for r in db.execute(
        "SELECT id, abc_class FROM product")}
    # 未出现在计算结果里的货品保持原值（无出库记录 → 不分类）
    changed = []
    for p in plan:
        old = before.get(p["product_id"], "")
        if old != p["abc_class"]:
            changed.append({
                "product_id": p["product_id"], "sku": p["sku"], "name": p["name"],
                "owner_id": p["owner_id"], "from": old, "to": p["abc_class"],
                "amount": p["amount"], "cum_ratio": p["cum_ratio"],
            })
            db.execute("UPDATE product SET abc_class = ?, updated_at ="
                       " datetime('now','localtime') WHERE id = ?",
                       (p["abc_class"], p["product_id"]))
    db.commit()
    return {"days": days, "changed": changed, "changed_count": len(changed),
            "classified": len(plan)}
