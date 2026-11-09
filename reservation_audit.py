"""M23 补充：专属库存「风险放行」的只读预检与审计留痕（**不拦截**）。

用户 2026-11-09 二次确认口径：专属库存发给其他客户**允许继续风险操作**，
但必须 ① 事前提示 ② 记录风险放行日志。因此：

* ``reservation_rules.py`` 仍是纯规则层，不读库、不写库、不接分配；
* 本模块只做「读 dn_pick → 用与发货相同的 FEFO 分摊推断将消耗的批次维度
  → 逐条比对客户 → 返回风险清单 / 写审计日志」；
* **不抛业务拒绝、不改分配顺序、不改库存**；发货失败时日志随事务一起回滚。

预检用 ``inventory_service.spread_batches()``，与 ``ship()`` 里
``adjust_stock(qty_delta<0, allocated_delta<0)`` 的自动推断是同一函数，
所以「预计消耗的批次」与「实际消耗的批次」一致，不是拍脑袋估算。

同一货位可能被多条 ``dn_pick`` 分次拣货：发货是**逐条扣减**的，因此预检也必须
按同样的顺序在「本地余额快照」上逐条模拟扣减，否则第二条预检仍看到未扣减的账，
会把风险记到错误批号上（多发或漏报）。快照只在内存里递减，不写库。
"""
import constants as C
from inventory_service import EPS, StockError, spread_batches
from reservation_rules import check_customer_reservation

_BATCH_SQL = """SELECT * FROM stock_batch
                WHERE owner_id = ? AND product_id = ? AND location_id = ? AND qty > ?
                ORDER BY (expiry_date = ''), expiry_date, id"""


def _local_balances(db, key):
    """读取某货位的批次余额为可写快照（只读预检用，不改库）。"""
    return [dict(r) for r in
            db.execute(_BATCH_SQL, (key[0], key[1], key[2], EPS)).fetchall()]


def _consume_local(rows, qty):
    """按与 spread_batches 相同的顺序递减本地余额；rows 已过滤 qty > EPS。"""
    rest = qty
    for r in rows:
        if rest <= EPS:
            break
        take = min(r["qty"], rest)
        r["qty"] -= take
        rest -= take


def inspect_dn(db, dn_id):
    """只读预检：返回本次发货预计触发的专属库存风险清单。

    返回 ``{"dn_id", "dn_code", "customer_id", "risk_count", "risks"}``；
    DN 不存在返回 ``None``。分摊不足（明细余额不够）时跳过该库位，
    由发货流程本身报错，预检不额外制造 4xx。
    """
    dn = db.execute(
        "SELECT id, code, owner_id, customer_id FROM dn WHERE id = ?", (dn_id,)
    ).fetchone()
    if dn is None:
        return None
    picks = db.execute(
        """SELECT dp.location_id, dp.qty, dp.line_id, dl.product_id,
                  l.code AS location_code, p.sku, p.name AS product_name
           FROM dn_pick dp
           JOIN dn_line dl ON dl.id = dp.line_id
           JOIN location l ON l.id = dp.location_id
           JOIN product p ON p.id = dl.product_id
           WHERE dp.dn_id = ? ORDER BY dp.id""",
        (dn_id,),
    ).fetchall()
    risks = []
    balances = {}
    for p in picks:
        key = (dn["owner_id"], p["product_id"], p["location_id"])
        if key not in balances:
            balances[key] = _local_balances(db, key)
        # 与发货逐条 adjust_stock 的顺序一致：分摊完立刻在本地快照上扣掉，
        # 下一条 dn_pick 才会看到扣减后的余额。
        active = [r for r in balances[key] if r["qty"] > EPS]
        try:
            items = spread_batches(db, *key, p["qty"], rows=active)
        except StockError:
            continue
        _consume_local(active, p["qty"])
        for it in items:
            verdict = check_customer_reservation(
                it["designated_customer_id"], dn["customer_id"])
            if verdict["allowed"]:
                continue
            risks.append({
                "dn_id": dn_id,
                "dn_code": dn["code"],
                "dn_customer_id": dn["customer_id"],
                "owner_id": dn["owner_id"],
                "product_id": p["product_id"],
                "product_name": p["product_name"],
                "sku": p["sku"],
                "line_id": p["line_id"],
                "location_id": p["location_id"],
                "location_code": p["location_code"],
                "batch_no": it["batch_no"],
                "designated_customer_id": it["designated_customer_id"],
                "qty": it["qty"],
                "code": verdict["code"],
                "reason": verdict["reason"],
            })
    return {
        "dn_id": dn_id,
        "dn_code": dn["code"],
        "customer_id": dn["customer_id"],
        "risk_count": len(risks),
        "risks": risks,
    }


def record_risk(db, dn_id, risks, operator="", acknowledged=False):
    """把风险放行逐条落库（与发货同事务）；返回写入条数。

    ``acknowledged`` 区分「界面二次确认后继续」与「API 直调继续」，
    两者都放行，只是审计标记不同。
    """
    if not risks:
        return 0
    action = (C.RESERVATION_RISK_ALLOWED if acknowledged
              else C.RESERVATION_RISK_ALLOWED_UNACK)
    db.executemany(
        """INSERT INTO customer_reservation_log
           (dn_id, dn_code, dn_customer_id, owner_id, product_id, location_id,
            batch_no, designated_customer_id, qty, code, reason, action,
            acknowledged, operator)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(r["dn_id"], r["dn_code"], r["dn_customer_id"], r["owner_id"],
          r["product_id"], r["location_id"], r["batch_no"],
          r["designated_customer_id"], r["qty"], r["code"], r["reason"],
          action, 1 if acknowledged else 0, operator) for r in risks],
    )
    return len(risks)
