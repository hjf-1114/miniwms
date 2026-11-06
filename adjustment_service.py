"""M22 库存调整单服务层：库存数量变化的唯一手工入口。

口径见 ``docs/PUTAWAY_RECOMMENDATION.md`` §7.6 / §18.7：

- 任何改变 ``stock.qty`` 的手工动作都必须有一张可追溯、可冲销的调整单；
  盘点差异不再由盘点路由直接 ``adjust_stock()``，而是人工点按钮生成调整单。
- 审核通过才在 ``BEGIN IMMEDIATE`` 事务里以 ``MOVEMENT_ADJUSTMENT``
  （``ref_id = adjustment_id``）过账，``batches=`` 指定批次时同步维护 ``stock_batch``。
- 冲销不是「把原单改回草稿」，而是生成一张反向 draft 单，仍需人工审核。
"""
import constants as C
from inventory_service import BATCH_DIMS, EPS, adjust_stock
from main import gen_doc_no
from scope import assert_owner_allowed, current_scope


class AdjustmentError(Exception):
    """调整单状态 / 数据非法，由路由层转成 4xx。"""


def _dict(row):
    return dict(row)


def _validate_line(db, raw, index):
    """校验单行并取冻结时点余额；返回可直接落库的 dict。"""
    try:
        owner_id = int(raw.get("owner_id"))
        product_id = int(raw.get("product_id"))
        location_id = int(raw.get("location_id"))
        delta = float(raw.get("delta_qty"))
    except (TypeError, ValueError, AttributeError):
        raise AdjustmentError(f"lines[{index}] 字段不合法")
    if abs(delta) <= EPS:
        raise AdjustmentError(f"lines[{index}] delta_qty 不能为 0")
    assert_owner_allowed(current_scope(), owner_id)
    for table, key, label in (("owner", owner_id, "货主"),
                              ("product", product_id, "物料"),
                              ("location", location_id, "库位")):
        if db.execute(f"SELECT id FROM {table} WHERE id = ?", (key,)).fetchone() is None:
            raise AdjustmentError(f"lines[{index}] {label}不存在: id={key}")
    bno = str(raw.get("batch_no") or "").strip()
    if bno:
        row = db.execute(
            "SELECT COALESCE(SUM(qty), 0) AS q FROM stock_batch "
            "WHERE owner_id = ? AND product_id = ? AND location_id = ? AND batch_no = ?",
            (owner_id, product_id, location_id, bno),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT qty AS q FROM stock WHERE owner_id = ? AND product_id = ? "
            "AND location_id = ?", (owner_id, product_id, location_id),
        ).fetchone()
    return {
        "owner_id": owner_id, "product_id": product_id, "location_id": location_id,
        "batch_no": bno, "delta_qty": delta,
        "qty_before": float(row["q"] if row else 0),
        "reason": str(raw.get("reason") or ""),
    }


def create_adjustment(db, *, source_type, source_id=None, reason_code="",
                      lines, creator="", note=""):
    """在调用方已开启的事务里创建 draft 调整单，返回 (id, code, 行数)。

    ``lines`` 至少 1 行；不做任何库存过账。冲销单与盘点差异单共用此函数。
    """
    if source_type not in C.ADJUSTMENT_SOURCE_TYPES:
        raise AdjustmentError(f"source_type 非法: {source_type}")
    if not isinstance(lines, list) or not lines:
        raise AdjustmentError("lines 必须为非空数组")
    clean = [_validate_line(db, raw, i) for i, raw in enumerate(lines)]
    code = gen_doc_no(db, "stock_adjustment", C.ADJUSTMENT_PREFIX)
    cur = db.execute(
        "INSERT INTO stock_adjustment "
        "(code, source_type, source_id, reason_code, status, creator, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, source_type, source_id, reason_code, C.ADJUSTMENT_DRAFT, creator, note),
    )
    adj_id = cur.lastrowid
    db.executemany(
        "INSERT INTO stock_adjustment_line "
        "(adjustment_id, owner_id, product_id, location_id, batch_no, "
        " delta_qty, qty_before, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(adj_id, l["owner_id"], l["product_id"], l["location_id"], l["batch_no"],
          l["delta_qty"], l["qty_before"], l["reason"]) for l in clean],
    )
    return adj_id, code, len(clean)


def _load(db, adj_id):
    row = db.execute(
        "SELECT * FROM stock_adjustment WHERE id = ?", (adj_id,)
    ).fetchone()
    if row is None:
        raise AdjustmentError("库存调整单不存在")
    return row


def get_adjustment(db, adj_id):
    adj = _dict(_load(db, adj_id))
    lines = db.execute(
        """SELECT al.*, p.sku, p.name AS product_name, l.code AS location_code
           FROM stock_adjustment_line al
           JOIN product p ON p.id = al.product_id
           JOIN location l ON l.id = al.location_id
           WHERE al.adjustment_id = ? ORDER BY al.id""",
        (adj_id,),
    ).fetchall()
    adj["lines"] = [_dict(x) for x in lines]
    return adj


def _dim_of(row, qty):
    """从现有明细行取完整批次维度，避免只传 batch_no 时误建一条新维度行。"""
    item = {k: row[k] for k in BATCH_DIMS}
    item["production_date"] = row["production_date"]
    item["qty"] = qty
    return item


def _batches_for_line(db, line, delta):
    """指定批号的调整单：盘亏按 FEFO 在该批号内分摊，盘盈落到该批号已有明细。

    只传 ``batch_no`` 而不补齐 ``batch_key`` / ``goods_property`` 等维度，会让
    ``upsert_stock_batch`` 误判为「新维度」并插入一条负数明细，I3 虽仍相等但
    实物分布全错；因此这里必须按 ``stock_batch`` 的真实行组装维度。
    """
    bno = line["batch_no"]
    if not bno:
        return None
    rows = db.execute(
        """SELECT * FROM stock_batch
           WHERE owner_id = ? AND product_id = ? AND location_id = ? AND batch_no = ?
           ORDER BY (expiry_date = ''), expiry_date, id""",
        (line["owner_id"], line["product_id"], line["location_id"], bno),
    ).fetchall()
    if not rows:
        return [{"batch_no": bno, "qty": delta}]
    if delta > 0:
        return [_dim_of(rows[0], delta)]
    remaining, out = -delta, []
    for r in rows:
        if remaining <= EPS:
            break
        take = min(r["qty"], remaining)
        if take <= EPS:
            continue
        out.append(_dim_of(r, -take))
        remaining -= take
    if remaining > EPS:
        raise AdjustmentError(f"批次 {bno} 明细不足，需盘亏 {-delta}")
    return out


def approve_adjustment(db, adj_id, approver=""):
    """审核过账：唯一写库存的入口；成功后回写来源盘点单状态。"""
    adj = _load(db, adj_id)
    if adj["status"] != C.ADJUSTMENT_DRAFT:
        raise AdjustmentError(f"状态为 {adj['status']}，仅 draft 可审核")
    lines = db.execute(
        "SELECT * FROM stock_adjustment_line WHERE adjustment_id = ? ORDER BY id",
        (adj_id,),
    ).fetchall()
    if not lines:
        raise AdjustmentError("调整单没有明细行")
    for l in lines:
        assert_owner_allowed(current_scope(), l["owner_id"])
        batches = _batches_for_line(db, l, l["delta_qty"])
        adjust_stock(db, l["owner_id"], l["product_id"], l["location_id"],
                     l["delta_qty"], C.MOVEMENT_ADJUSTMENT, adj_id,
                     note=f"库存调整单 {adj['code']}", batches=batches,
                     operator=approver)
    db.execute(
        "UPDATE stock_adjustment SET status = ?, approver = ?, "
        "approved_at = datetime('now','localtime') WHERE id = ?",
        (C.ADJUSTMENT_APPROVED, approver, adj_id),
    )
    if adj["source_type"] == C.ADJUSTMENT_STOCKTAKE_DIFF and adj["source_id"]:
        db.execute(
            "UPDATE stocktake SET status = ?, adjustment_id = ?, "
            "closed_at = datetime('now','localtime'), "
            "updated_at = datetime('now','localtime') "
            "WHERE id = ? AND status = ?",
            (C.STOCKTAKE_CLOSED, adj_id, adj["source_id"], C.STOCKTAKE_ADJUSTED),
        )
    return get_adjustment(db, adj_id)


def cancel_adjustment(db, adj_id):
    """取消 draft 单；来源盘点单保持 adjusted，可重新生成调整单。"""
    adj = _load(db, adj_id)
    if adj["status"] != C.ADJUSTMENT_DRAFT:
        raise AdjustmentError(f"状态为 {adj['status']}，仅 draft 可取消")
    db.execute(
        "UPDATE stock_adjustment SET status = ? WHERE id = ?",
        (C.ADJUSTMENT_CANCELLED, adj_id),
    )
    return get_adjustment(db, adj_id)


def reverse_adjustment(db, adj_id, creator=""):
    """冲销：生成一张反向 draft 单（不直接过账），原单保持 approved。"""
    src = get_adjustment(db, adj_id)
    if src["status"] != C.ADJUSTMENT_APPROVED:
        raise AdjustmentError(f"状态为 {src['status']}，仅已审核单可冲销")
    lines = [{
        "owner_id": l["owner_id"], "product_id": l["product_id"],
        "location_id": l["location_id"], "batch_no": l["batch_no"],
        "delta_qty": -l["delta_qty"], "reason": f"冲销 {src['code']}",
    } for l in src["lines"]]
    return create_adjustment(
        db, source_type=C.ADJUSTMENT_REVERSAL, source_id=adj_id,
        reason_code="冲销", lines=lines, creator=creator,
        note=f"冲销 {src['code']}",
    )
