"""M24 库存转移单服务层：同库位内「批次维度变更」的唯一入口。

用户 2026-11-09 确认的四条 A 口径：

* **A1** 只做同库位内的批次维度变更；跨库位一律走 M17 移库单。主账
  ``stock.qty`` 全程零 UPDATE（``inventory_service.adjust_stock`` 在
  ``qty_delta == 0`` 时只 SET ``allocated_qty``）。
* **A2** 7 类 ``transfer_type`` 一次做全，但底层统一为「源维度组合 → 目标维度组合」
  的净零搬迁；类型只作展示与校验标签，不写独立分支。
* **A3** 只做 IT 单自身的 ``unallocate``，不碰 DN 拣货分配。
* **A4** 权限码只落常量不挂装饰器；特采只接布尔标记 + ``reason_code=特采``。

状态机（设计 §7.2 / §16.1，v3.2 定稿）：

* 有分配：``draft → allocated → approved``（审核即生效终态，不再自动关闭）
* 空单：``draft → closed`` / ``cancelled``（无任何库存动作）
* ``allocated`` 单必须先 ``unallocate`` 才能关闭 / 删除；``approved`` 不可改不可删。

所有库存写入都经 ``inventory_service.adjust_stock()``，本模块不直写 ``stock_batch``
或 ``stock_movement``；调用方负责 ``BEGIN IMMEDIATE`` 事务。
"""
import constants as C
from inventory_service import EPS, adjust_stock
from main import gen_doc_no
from reservation_rules import can_change_goods_property, stock_status_allowed
from scope import assert_owner_allowed, current_scope

# 与 inventory_service.legacy_dims() 的维度键一一对应（不含 operator）。
DIM_FIELDS = (
    "batch_no", "batch_key", "internal_batch_no", "designated_customer_id",
    "goods_property", "stock_status", "expiry_date", "production_date",
)
LINE_COLUMNS = ("owner_id", "product_id", "location_id", "stock_batch_id",
                "qty", "qty_before", "qty_after", "allocated") \
    + tuple("from_" + f for f in DIM_FIELDS) \
    + tuple("to_" + f for f in DIM_FIELDS)


class TransferError(Exception):
    """单据状态 / 数据非法，由路由层转成 4xx。"""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def _s(value):
    return "" if value is None else str(value)


def _dim_key(dim):
    """维度组合的可比较快照（NULL 与空串视为同一值）。"""
    return tuple(_s(dim.get(f)) for f in DIM_FIELDS)


def _row_dim(row):
    return {f: row[f] for f in DIM_FIELDS}


def _line_dim(line, which):
    prefix = "from_" if which == "from" else "to_"
    return {f: line[prefix + f] for f in DIM_FIELDS}


def _load_transfer(db, transfer_id):
    row = db.execute("SELECT * FROM inventory_transfer WHERE id = ?",
                     (transfer_id,)).fetchone()
    if row is None:
        raise TransferError("库存转移单不存在")
    return dict(row)


def _load_lines(db, transfer_id):
    return [dict(r) for r in db.execute(
        "SELECT * FROM inventory_transfer_line WHERE transfer_id = ? ORDER BY id",
        (transfer_id,)).fetchall()]


def get_transfer(db, transfer_id):
    """详情：单据头 + 明细行 + 字段级变更文档。"""
    head = _load_transfer(db, transfer_id)
    lines = _load_lines(db, transfer_id)
    logs = [dict(r) for r in db.execute(
        "SELECT * FROM inventory_change_log WHERE transfer_id = ? ORDER BY id",
        (transfer_id,)).fetchall()]
    head["lines"] = lines
    head["change_logs"] = logs
    head["line_count"] = len(lines)
    head["total_qty"] = sum(float(l["qty"]) for l in lines)
    return head


def check_forbidden_qty_fields(payload):
    """§14 阶段 D：转移类接口收到数量调整字段一律 400，数量只走库存调整单。"""
    for key in C.IT_QTY_FORBIDDEN_FIELDS:
        if key in payload:
            raise TransferError(
                f"库存转移只改批次信息、不能调整库存数量；字段 {key} 不被接受，"
                "数量变化请走库存调整单",
                C.IT_ERROR_QTY_NOT_ZERO,
            )


def create_transfer(db, *, transfer_type, reason_code="", creator="", note="",
                    business_at="", reversal_of_id=None):
    """制单：只生成 draft 空单，不含明细、不做任何库存动作。"""
    if transfer_type not in C.IT_TYPES:
        raise TransferError(f"transfer_type 非法: {transfer_type}")
    if reason_code and reason_code not in C.IT_REASON_CODES:
        raise TransferError(f"reason_code 非法: {reason_code}")
    if reversal_of_id:
        src = db.execute("SELECT id, status FROM inventory_transfer WHERE id = ?",
                         (reversal_of_id,)).fetchone()
        if src is None:
            raise TransferError("被冲销单据不存在")
        if src["status"] != C.IT_APPROVED:
            raise TransferError("只有已审核生效的单据才能冲销")
        if db.execute("SELECT id FROM inventory_transfer WHERE reversal_of_id = ?",
                      (reversal_of_id,)).fetchone():
            raise TransferError("该单据已生成过冲销单，不可重复冲销")
    code = gen_doc_no(db, "inventory_transfer", C.INVENTORY_TRANSFER_PREFIX)
    cur = db.execute(
        """INSERT INTO inventory_transfer
           (code, transfer_type, reason_code, status, reversal_of_id,
            creator, business_at, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, transfer_type, reason_code, C.IT_DRAFT, reversal_of_id,
         creator, business_at, note),
    )
    return cur.lastrowid, code


def _find_source_row(db, raw, index):
    """按 stock_batch_id 或显式维度定位源明细行。"""
    bid = raw.get("stock_batch_id")
    if bid not in (None, "", 0):
        row = db.execute("SELECT * FROM stock_batch WHERE id = ?",
                         (int(bid),)).fetchone()
        if row is None:
            raise TransferError(f"lines[{index}] 源批次明细不存在: id={bid}")
        return row
    try:
        owner_id = int(raw.get("owner_id"))
        product_id = int(raw.get("product_id"))
        location_id = int(raw.get("location_id"))
    except (TypeError, ValueError):
        raise TransferError(
            f"lines[{index}] 需提供 stock_batch_id 或 owner_id/product_id/location_id + from_* 维度")
    dim = {f: raw.get("from_" + f, "") for f in DIM_FIELDS}
    dcid = dim["designated_customer_id"]
    dcid = None if dcid in (None, "", 0, "0") else int(dcid)
    row = db.execute(
        """SELECT * FROM stock_batch
           WHERE owner_id = ? AND product_id = ? AND location_id = ?
             AND batch_no = ? AND batch_key = ?
             AND IFNULL(designated_customer_id, 0) = IFNULL(?, 0)
             AND goods_property = ? AND stock_status = ?
           ORDER BY id LIMIT 1""",
        (owner_id, product_id, location_id, dim["batch_no"], dim["batch_key"],
         dcid, dim["goods_property"], dim["stock_status"]),
    ).fetchone()
    if row is None:
        raise TransferError(f"lines[{index}] 按给定维度找不到源批次明细")
    return row


def _validate_line(db, raw, index, special_approval):
    """校验单行并取冻结时点余额；返回可直接落库的 dict。"""
    try:
        qty = float(raw.get("qty"))
    except (TypeError, ValueError):
        raise TransferError(f"lines[{index}] qty 不合法")
    if qty <= EPS:
        raise TransferError(f"lines[{index}] qty 必须为正数")

    src = _find_source_row(db, raw, index)
    owner_id = int(src["owner_id"])
    assert_owner_allowed(current_scope(), owner_id)
    location_id = int(src["location_id"])

    # A1：跨库位一律拒绝，指引走 M17 移库单
    for key in ("to_location_id", "from_location_id", "location_id_to"):
        if key in raw and raw[key] not in (None, "", 0):
            if int(raw[key]) != location_id:
                raise TransferError(
                    "M24 只支持同库位内的批次维度变更；跨库位请走 M17 移库单")

    from_dim = _row_dim(src)
    to_dim = dict(from_dim)
    for f in DIM_FIELDS:
        key = "to_" + f
        if key not in raw:
            continue
        value = raw[key]
        if f == "designated_customer_id":
            value = None if value in (None, "", 0, "0") else int(value)
        else:
            value = _s(value).strip()
        to_dim[f] = value

    if _dim_key(from_dim) == _dim_key(to_dim):
        raise TransferError(f"lines[{index}] 目标维度与源维度完全相同，无需转移")

    available = float(src["qty"]) - float(src["allocated_qty"])
    if qty > available + EPS:
        raise TransferError(
            f"lines[{index}] 可用库存不足: 在手 {src['qty']}, 已分配 "
            f"{src['allocated_qty']}, 可用 {available}, 需 {qty}")

    if not stock_status_allowed(to_dim["stock_status"]):
        raise TransferError(f"lines[{index}] 目标库存状态非法: {to_dim['stock_status']}")
    verdict = can_change_goods_property(from_dim["goods_property"],
                                        to_dim["goods_property"], special_approval)
    if not verdict["allowed"]:
        raise TransferError(f"lines[{index}] {verdict['reason']}"
                            "（不合格回合格须 reason_code=特采）")

    if to_dim["designated_customer_id"]:
        cust = db.execute("SELECT owner_id FROM customer WHERE id = ?",
                          (to_dim["designated_customer_id"],)).fetchone()
        if cust is None or cust["owner_id"] != owner_id:
            raise TransferError(f"lines[{index}] 指定客户必须归属同一货主")

    line = {
        "owner_id": owner_id, "product_id": int(src["product_id"]),
        "location_id": location_id, "stock_batch_id": int(src["id"]),
        "qty": qty, "qty_before": float(src["qty"]), "qty_after": 0.0,
        "allocated": 1,
    }
    for f in DIM_FIELDS:
        line["from_" + f] = from_dim[f]
        line["to_" + f] = to_dim[f]
    line["_from_dim"] = from_dim
    line["_to_dim"] = to_dim
    return line


def _insert_lines(db, transfer_id, clean):
    cols = [c for c in LINE_COLUMNS]
    marks = ", ".join("?" for _ in cols)
    db.executemany(
        f"INSERT INTO inventory_transfer_line (transfer_id, {', '.join(cols)}) "
        f"VALUES (?, {marks})",
        [(transfer_id,) + tuple(l[c] for c in cols) for l in clean],
    )


def allocate(db, transfer_id, lines, allocator=""):
    """分配库存：建明细行 + 两层 allocated_qty 真实占用（可用库存减少）。

    写入 ``qty_delta = 0`` / ``allocated_delta > 0`` 的流水留痕，不动 ``stock.qty``。
    """
    t = _load_transfer(db, transfer_id)
    if t["status"] != C.IT_DRAFT:
        raise TransferError(f"状态为 {t['status']}，仅 draft 可分配库存")
    if _load_lines(db, transfer_id):
        raise TransferError("单据已有明细行，请先取消分配")
    if not isinstance(lines, list) or not lines:
        raise TransferError("lines 必须为非空数组")

    special = t["reason_code"] == C.IT_SPECIAL_APPROVAL_REASON
    clean = [_validate_line(db, raw, i, special) for i, raw in enumerate(lines)]
    for line in clean:
        adjust_stock(
            db, line["owner_id"], line["product_id"], line["location_id"], 0,
            C.MOVEMENT_IT_ALLOCATE, transfer_id,
            allocated_delta=line["qty"],
            batches=[dict(line["_from_dim"], qty=0.0, allocated=line["qty"])],
            note=f"IT {t['code']} 分配占用", operator=allocator,
        )
    _insert_lines(db, transfer_id, clean)
    db.execute(
        "UPDATE inventory_transfer SET status = ?, allocator = ?, "
        "allocated_at = datetime('now','localtime') WHERE id = ?",
        (C.IT_ALLOCATED, allocator, transfer_id),
    )
    return get_transfer(db, transfer_id)


def unallocate(db, transfer_id, operator=""):
    """取消分配：两层 allocated_qty 对称释放，单据回 draft，明细行清空。"""
    t = _load_transfer(db, transfer_id)
    if t["status"] != C.IT_ALLOCATED:
        raise TransferError(f"状态为 {t['status']}，仅 allocated 可取消分配")
    lines = _load_lines(db, transfer_id)
    for l in lines:
        adjust_stock(
            db, l["owner_id"], l["product_id"], l["location_id"], 0,
            C.MOVEMENT_IT_UNALLOCATE, transfer_id,
            allocated_delta=-float(l["qty"]),
            batches=[dict(_line_dim(l, "from"), qty=0.0,
                          allocated=-float(l["qty"]))],
            note=f"IT {t['code']} 取消分配", operator=operator,
        )
    db.execute("DELETE FROM inventory_transfer_line WHERE transfer_id = ?",
               (transfer_id,))
    db.execute(
        "UPDATE inventory_transfer SET status = ?, allocator = '', "
        "allocated_at = '' WHERE id = ?",
        (C.IT_DRAFT, transfer_id),
    )
    return get_transfer(db, transfer_id)


def _batch_qty(db, l, dim):
    row = db.execute(
        """SELECT qty FROM stock_batch
           WHERE owner_id = ? AND product_id = ? AND location_id = ?
             AND batch_no = ? AND batch_key = ?
             AND IFNULL(designated_customer_id, 0) = IFNULL(?, 0)
             AND goods_property = ? AND stock_status = ?
           ORDER BY id LIMIT 1""",
        (l["owner_id"], l["product_id"], l["location_id"], dim["batch_no"],
         dim["batch_key"], dim["designated_customer_id"],
         dim["goods_property"], dim["stock_status"]),
    ).fetchone()
    return float(row["qty"]) if row else 0.0


def approve(db, transfer_id, approver=""):
    """审核生效：净零成对流水 + 释放分配 + 字段级变更文档 → approved 终态。

    每行写两条流水：源维度 ``qty=-Q``、目标维度 ``qty=+Q``，``Σ qty_delta = 0``；
    主账只释放 ``allocated_qty``，``stock.qty`` 列一次都不 UPDATE。
    """
    t = _load_transfer(db, transfer_id)
    if t["status"] != C.IT_ALLOCATED:
        raise TransferError(f"状态为 {t['status']}，仅 allocated 可审核")
    lines = _load_lines(db, transfer_id)
    if not lines:
        raise TransferError("审核前必须至少分配一行")

    net = sum(-float(l["qty"]) + float(l["qty"]) for l in lines)
    if abs(net) > EPS:
        raise TransferError("整单 Σ qty_delta 必须为 0",
                            C.IT_ERROR_QTY_NOT_ZERO)

    for l in lines:
        qty = float(l["qty"])
        from_dim, to_dim = _line_dim(l, "from"), _line_dim(l, "to")
        adjust_stock(
            db, l["owner_id"], l["product_id"], l["location_id"], 0,
            C.MOVEMENT_IT_TRANSFER, transfer_id,
            allocated_delta=-qty,
            batches=[dict(from_dim, qty=-qty, allocated=-qty),
                     dict(to_dim, qty=qty, allocated=0.0)],
            note=f"IT {t['code']} 批次维度转移", operator=approver,
        )
        db.execute(
            "UPDATE inventory_transfer_line SET qty_after = ? WHERE id = ?",
            (_batch_qty(db, l, from_dim), l["id"]),
        )
        for f in DIM_FIELDS:
            old, new = from_dim[f], to_dim[f]
            if _s(old) == _s(new):
                continue
            db.execute(
                """INSERT INTO inventory_change_log
                   (transfer_id, line_id, object_type, object_id, field_name,
                    old_value, new_value, operator, approver, business_at, reason_code)
                   VALUES (?, ?, 'stock_batch', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (transfer_id, l["id"], l["stock_batch_id"], f,
                 _s(old), _s(new), approver, approver, t["business_at"],
                 t["reason_code"]),
            )
    db.execute(
        "UPDATE inventory_transfer SET status = ?, approver = ?, "
        "approved_at = datetime('now','localtime') WHERE id = ?",
        (C.IT_APPROVED, approver, transfer_id),
    )
    return get_transfer(db, transfer_id)


def _assert_empty_draft(db, transfer_id, action):
    t = _load_transfer(db, transfer_id)
    if t["status"] != C.IT_DRAFT:
        raise TransferError(f"状态为 {t['status']}，不可{action}")
    if _load_lines(db, transfer_id):
        raise TransferError(f"已有分配明细，不可{action}；请先「取消分配」")
    return t


def close(db, transfer_id, closer=""):
    """关闭空单：无任何库存动作。"""
    _assert_empty_draft(db, transfer_id, "关闭")
    db.execute(
        "UPDATE inventory_transfer SET status = ?, closer = ?, "
        "closed_at = datetime('now','localtime') WHERE id = ?",
        (C.IT_CLOSED, closer, transfer_id),
    )
    return get_transfer(db, transfer_id)


def delete(db, transfer_id):
    """删除空单：标记 cancelled（不物理删除，保留审计）。"""
    t = _load_transfer(db, transfer_id)
    if t["status"] not in (C.IT_DRAFT, C.IT_CLOSED):
        raise TransferError(f"状态为 {t['status']}，不可删除")
    if _load_lines(db, transfer_id):
        raise TransferError("已有分配明细，不可删除；请先「取消分配」")
    db.execute("UPDATE inventory_transfer SET status = ? WHERE id = ?",
               (C.IT_CANCELLED, transfer_id))
    return get_transfer(db, transfer_id)


def reverse(db, transfer_id, creator=""):
    """冲销：生成反向 draft 空单（仍需分配 + 审核），返回建议的反向明细行。"""
    t = _load_transfer(db, transfer_id)
    if t["status"] != C.IT_APPROVED:
        raise TransferError("只有已审核生效的单据才能冲销")
    lines = _load_lines(db, transfer_id)
    new_id, code = create_transfer(
        db, transfer_type=t["transfer_type"],
        reason_code="冲销", creator=creator,
        note=f"冲销 {t['code']}", reversal_of_id=transfer_id,
    )
    suggested = []
    for l in lines:
        item = {k: l[k] for k in ("owner_id", "product_id", "location_id", "qty")}
        for f in DIM_FIELDS:
            item["from_" + f] = l["to_" + f]
            item["to_" + f] = l["from_" + f]
        suggested.append(item)
    return new_id, code, suggested
