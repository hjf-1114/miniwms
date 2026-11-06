"""库存账查询模块：/api/stock 与 /api/movements。

M6 的定位是「只读账房」——不写库存，只回答三个问题：
1. /api/stock            现在账上有什么（余额 = 在手 qty / 已分配 allocated_qty / 可用 qty-allocated）
2. /api/movements        账是怎么变的（全部动作流水，M4 收货/上架、M5 拣货/发货各类型）
3. /api/stock/reconcile  账对不对（每个 货主×物料×库位 的余额应等于其流水累加，
   inventory_service 保证「每笔变动必落流水」，此接口做独立事后核对）

过滤维度统一支持 owner_id / product_id / location_id，配合分页。
"""
import scope as S
from flask import Blueprint, jsonify, request

from main import get_db

stock_bp = Blueprint("stock", __name__)


def _int_arg(args, name):
    """取整型查询参数；未提供返回 None，非法抛 ValueError。"""
    raw = (args.get(name) or "").strip()
    if not raw:
        return None
    return int(raw)


def _paging_args(args):
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        raise ValueError("page/page_size 必须为整数")
    return page, page_size


@stock_bp.route("/api/stock", methods=["GET"])
def list_stock():
    """库存余额列表：按 货主×物料×库位 一行，含在手/已分配/可用。

    过滤：owner_id / product_id / location_id / has_stock(1=仅非零行)。
    """
    args = request.args
    try:
        owner_id = _int_arg(args, "owner_id")
        product_id = _int_arg(args, "product_id")
        location_id = _int_arg(args, "location_id")
        page, page_size = _paging_args(args)
    except ValueError as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400

    where, params = [], []
    if owner_id is not None:
        where.append("s.owner_id = ?")
        params.append(owner_id)
    if product_id is not None:
        where.append("s.product_id = ?")
        params.append(product_id)
    if location_id is not None:
        where.append("s.location_id = ?")
        params.append(location_id)
    if (args.get("has_stock") or "").strip() == "1":
        where.append("s.qty != 0")
    S.scoped_where(where, params, table="s")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM stock s {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT s.id, s.owner_id, s.product_id, s.location_id,
                   s.qty, s.allocated_qty, s.qty - s.allocated_qty AS available_qty,
                   s.updated_at,
                   o.code AS owner_code, o.name AS owner_name,
                   p.sku, p.name AS product_name,
                   l.code AS location_code, l.name AS location_name, l.loc_type
            FROM stock s
            JOIN owner o ON o.id = s.owner_id
            JOIN product p ON p.id = s.product_id
            JOIN location l ON l.id = s.location_id
            {where_sql}
            ORDER BY s.id LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "stocks": [dict(r) for r in rows]})


@stock_bp.route("/api/movements", methods=["GET"])
def list_movements():
    """库存动作流水列表：每笔库存变动一条，余额 = 流水累加。

    过滤：owner_id / product_id / location_id / ref_type / ref_id。
    """
    args = request.args
    try:
        owner_id = _int_arg(args, "owner_id")
        product_id = _int_arg(args, "product_id")
        location_id = _int_arg(args, "location_id")
        ref_id = _int_arg(args, "ref_id")
        page, page_size = _paging_args(args)
    except ValueError as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400
    ref_type = (args.get("ref_type") or "").strip()

    where, params = [], []
    if owner_id is not None:
        where.append("m.owner_id = ?")
        params.append(owner_id)
    if product_id is not None:
        where.append("m.product_id = ?")
        params.append(product_id)
    if location_id is not None:
        where.append("m.location_id = ?")
        params.append(location_id)
    if ref_type:
        where.append("m.ref_type = ?")
        params.append(ref_type)
    if ref_id is not None:
        where.append("m.ref_id = ?")
        params.append(ref_id)
    S.scoped_where(where, params, table="m")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM stock_movement m {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT m.id, m.owner_id, m.product_id, m.location_id,
                   m.qty_delta, m.ref_type, m.ref_id, m.note, m.created_at,
                   o.code AS owner_code,
                   p.sku, p.name AS product_name,
                   l.code AS location_code
            FROM stock_movement m
            JOIN owner o ON o.id = m.owner_id
            JOIN product p ON p.id = m.product_id
            JOIN location l ON l.id = m.location_id
            {where_sql}
            ORDER BY m.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "movements": [dict(r) for r in rows]})


@stock_bp.route("/api/stock/reconcile", methods=["GET"])
def reconcile():
    """余额核对：每个 货主×物料×库位 的 stock.qty 应等于其流水 qty_delta 累加。

    覆盖三类键：
    - 有库存行 + 有流水：qty 与累加比对
    - 有库存行 + 无流水：只在 qty 非零时报不一致（零行允许无流水）
    - 无库存行 + 有流水：流水累加非零时报不一致（账面凭空少了库存）
    浮点比对容差 1e-9。
    """
    db = get_db()
    stock_rows = db.execute(
        "SELECT owner_id, product_id, location_id, qty FROM stock"
    ).fetchall()
    sums = {}
    for r in db.execute(
        """SELECT owner_id, product_id, location_id, SUM(qty_delta) AS s
           FROM stock_movement
           GROUP BY owner_id, product_id, location_id"""
    ):
        sums[(r["owner_id"], r["product_id"], r["location_id"])] = r["s"]

    stock_map = {
        (r["owner_id"], r["product_id"], r["location_id"]): r["qty"]
        for r in stock_rows
    }

    def _label(key):
        oid, pid, lid = key
        o = db.execute("SELECT code FROM owner WHERE id = ?", (oid,)).fetchone()
        p = db.execute("SELECT sku FROM product WHERE id = ?", (pid,)).fetchone()
        l = db.execute("SELECT code FROM location WHERE id = ?", (lid,)).fetchone()
        return {
            "owner_id": oid, "product_id": pid, "location_id": lid,
            "owner_code": o["code"] if o else None,
            "sku": p["sku"] if p else None,
            "location_code": l["code"] if l else None,
        }

    mismatches = []
    checked = 0
    for key in set(stock_map) | set(sums):
        qty = stock_map.get(key)
        total = sums.get(key)
        if qty is None:
            # 无库存行：零累加的流水组合视为历史已平账，非零才是异常
            if total is not None and abs(total) > 1e-9:
                item = _label(key)
                item.update({"book_qty": 0, "movement_sum": total,
                             "issue": "有流水但无库存行"})
                mismatches.append(item)
            continue
        checked += 1
        if total is None:
            if abs(qty) > 1e-9:
                item = _label(key)
                item.update({"book_qty": qty, "movement_sum": 0,
                             "issue": "有余额但无流水"})
                mismatches.append(item)
            continue
        if abs(qty - total) > 1e-9:
            item = _label(key)
            item.update({"book_qty": qty, "movement_sum": total,
                         "issue": "余额与流水累加不等"})
            mismatches.append(item)

    return jsonify({
        "ok": True,
        "consistent": len(mismatches) == 0,
        "checked": checked,
        "mismatches": mismatches,
    })


# ── M21 批次级库存明细层 ──────────────────────────────────────────────
BATCH_DIM_SQL = ("owner_id, product_id, location_id, batch_no, "
                 "designated_customer_id, goods_property")
EPS = 1e-9


def _dim_key(r):
    return (r["owner_id"], r["product_id"], r["location_id"], r["batch_no"],
            r["designated_customer_id"], r["goods_property"])


def _sum_map(rows):
    return {_dim_key(r): r["v"] for r in rows}


@stock_bp.route("/api/stock/batch", methods=["GET"])
def list_stock_batch():
    """批次级库存余额（M21 明细层）：按批号 / 客户 / 质量状态 / 效期细分。

    过滤：owner_id / product_id / location_id / batch_no / goods_property /
    stock_status / designated_customer_id / has_stock(1=仅非零行)，配合分页。
    与 /api/stock 一样接入货主隔离注入，受限账号看不到别人的批次明细。
    """
    args = request.args
    try:
        owner_id = _int_arg(args, "owner_id")
        product_id = _int_arg(args, "product_id")
        location_id = _int_arg(args, "location_id")
        designated_customer_id = _int_arg(args, "designated_customer_id")
        page, page_size = _paging_args(args)
    except ValueError as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400

    where, params = [], []
    for col, val in (("owner_id", owner_id), ("product_id", product_id),
                     ("location_id", location_id),
                     ("designated_customer_id", designated_customer_id)):
        if val is not None:
            where.append(f"sb.{col} = ?")
            params.append(val)
    if (args.get("batch_no") or "").strip():
        where.append("sb.batch_no = ?")
        params.append(args.get("batch_no").strip())
    if (args.get("goods_property") or "").strip():
        where.append("sb.goods_property = ?")
        params.append(args.get("goods_property").strip())
    if (args.get("stock_status") or "").strip():
        where.append("sb.stock_status = ?")
        params.append(args.get("stock_status").strip())
    if (args.get("has_stock") or "").strip() == "1":
        where.append("sb.qty != 0")
    S.scoped_where(where, params, table="sb")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM stock_batch sb {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT sb.*, sb.qty - sb.allocated_qty AS available_qty,
                   o.code AS owner_code, p.sku, p.name AS product_name,
                   l.code AS location_code, l.name AS location_name
            FROM stock_batch sb
            JOIN owner o ON o.id = sb.owner_id
            JOIN product p ON p.id = sb.product_id
            JOIN location l ON l.id = sb.location_id
            {where_sql}
            ORDER BY sb.id LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "stock_batches": [dict(r) for r in rows]})


@stock_bp.route("/api/stock/batch-reconcile", methods=["GET"])
def batch_reconcile():
    """I2 + I3 硬校验（M21，**无灰度豁免**）。

    - I2：``stock_batch.qty == SUM(stock_movement.qty_delta)``，按
      (owner, product, location, batch_no, designated_customer_id, goods_property)
    - I3：``stock.qty == SUM(stock_batch.qty)``，按 (owner, product, location)

    任何不一致直接 ``consistent=false``；不返回 ``LEGACY_NO_BATCH_DETAIL`` 放行标志。
    I1 仍由 ``/api/stock/reconcile`` 独立校验，语义保持不变。
    """
    db = get_db()
    batch_sums = _sum_map(db.execute(
        f"SELECT {BATCH_DIM_SQL}, SUM(qty) AS v FROM stock_batch "
        f"GROUP BY {BATCH_DIM_SQL}"
    ).fetchall())
    move_sums = _sum_map(db.execute(
        f"SELECT {BATCH_DIM_SQL}, SUM(qty_delta) AS v FROM stock_movement "
        f"GROUP BY {BATCH_DIM_SQL}"
    ).fetchall())

    def label(key):
        oid, pid, lid, bno, cust, prop = key
        o = db.execute("SELECT code FROM owner WHERE id = ?", (oid,)).fetchone()
        p = db.execute("SELECT sku FROM product WHERE id = ?", (pid,)).fetchone()
        l = db.execute("SELECT code FROM location WHERE id = ?", (lid,)).fetchone()
        return {"owner_id": oid, "product_id": pid, "location_id": lid,
                "batch_no": bno, "designated_customer_id": cust,
                "goods_property": prop,
                "owner_code": o["code"] if o else None,
                "sku": p["sku"] if p else None,
                "location_code": l["code"] if l else None}

    i2_checked, i2_mismatches = 0, []
    for key in set(batch_sums) | set(move_sums):
        book, moved = batch_sums.get(key), move_sums.get(key)
        if book is None:
            if moved is not None and abs(moved) > EPS:
                item = label(key)
                item.update({"book_qty": 0, "movement_sum": moved,
                             "invariant": "I2", "issue": "有批次流水但无明细行"})
                i2_mismatches.append(item)
            continue
        i2_checked += 1
        if moved is None:
            if abs(book) > EPS:
                item = label(key)
                item.update({"book_qty": book, "movement_sum": 0,
                             "invariant": "I2", "issue": "有批次余额但无流水"})
                i2_mismatches.append(item)
            continue
        if abs(book - moved) > EPS:
            item = label(key)
            item.update({"book_qty": book, "movement_sum": moved,
                         "invariant": "I2", "issue": "批次余额与流水累加不等"})
            i2_mismatches.append(item)

    stock_map = {
        (r["owner_id"], r["product_id"], r["location_id"]): r["qty"]
        for r in db.execute(
            "SELECT owner_id, product_id, location_id, qty FROM stock").fetchall()
    }
    detail_map = {}
    for r in db.execute(
        """SELECT owner_id, product_id, location_id, SUM(qty) AS v
           FROM stock_batch GROUP BY owner_id, product_id, location_id"""
    ).fetchall():
        detail_map[(r["owner_id"], r["product_id"], r["location_id"])] = r["v"]

    i3_checked, i3_mismatches = 0, []
    for key in set(stock_map) | set(detail_map):
        book, detail = stock_map.get(key), detail_map.get(key)
        if book is None:
            if detail is not None and abs(detail) > EPS:
                i3_mismatches.append({
                    "invariant": "I3", "owner_id": key[0], "product_id": key[1],
                    "location_id": key[2], "book_qty": 0, "batch_sum": detail,
                    "issue": "有批次明细但无主账行"})
            continue
        i3_checked += 1
        if detail is None:
            if abs(book) > EPS:
                i3_mismatches.append({
                    "invariant": "I3", "owner_id": key[0], "product_id": key[1],
                    "location_id": key[2], "book_qty": book, "batch_sum": 0,
                    "issue": "有主账余额但无批次明细"})
            continue
        if abs(book - detail) > EPS:
            i3_mismatches.append({
                "invariant": "I3", "owner_id": key[0], "product_id": key[1],
                "location_id": key[2], "book_qty": book, "batch_sum": detail,
                "issue": "主账余额与批次明细累加不等"})

    mismatches = i2_mismatches + i3_mismatches
    return jsonify({
        "ok": True, "consistent": len(mismatches) == 0, "tolerance": EPS,
        "i2": {"checked": i2_checked, "mismatches": i2_mismatches},
        "i3": {"checked": i3_checked, "mismatches": i3_mismatches},
        "mismatches": mismatches,
    })
