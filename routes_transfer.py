"""移库单（Stock Transfer）模块：/api/transfers。

流程（单据驱动库存，与 ASN/DN 对称）：
1. 创建移库单（指定货主 + 行：物料 × 来源库位 × 目标库位 × 数量）
2. 执行移库 move：从来源位按「可用量」转出（在手-已分配），同事务一减一增，
   流水 move_out（来源位，-qty）/ move_in（目标位，+qty），状态 → done
3. 取消：仅未执行可 cancel

一致性规则：
- 移库单货主必须与行内物料、来源/目标库位的归属组合一致（多货主隔离）
- 来源/目标库位必须启用；来源=目标位、或来源位可用量不足时拒绝
"""
import math

import scope as S
from flask import Blueprint, jsonify, request

import constants as C
from inventory_service import StockError, stock_move
from main import get_db, request_payload

transfer_bp = Blueprint("transfer", __name__, url_prefix="/api/transfers")


def _load_transfer(db, transfer_id):
    return db.execute("SELECT * FROM transfer WHERE id = ?", (transfer_id,)).fetchone()


def _load_lines(db, transfer_id):
    return db.execute(
        """SELECT tl.*, p.sku, p.name AS product_name,
                  fl.code AS from_location_code, fl.loc_type AS from_loc_type,
                  tl2.code AS to_location_code, tl2.loc_type AS to_loc_type
           FROM transfer_line tl
           JOIN product p ON p.id = tl.product_id
           JOIN location fl ON fl.id = tl.from_location_id
           JOIN location tl2 ON tl2.id = tl.to_location_id
           WHERE tl.transfer_id = ? ORDER BY tl.id""",
        (transfer_id,),
    ).fetchall()


def _dict_line(l):
    d = dict(l)
    d.pop("from_loc_type", None)
    d.pop("to_loc_type", None)
    return d


def _check_location(db, location_id, label):
    row = db.execute(
        "SELECT code, status FROM location WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{label}库位不存在: id={location_id}")
    if row["status"] != "active":
        raise ValueError(f"{label}库位已停用: {row['code']}")
    return row


@transfer_bp.route("", methods=["POST"])
def create_transfer():
    payload = request_payload()
    code = str(payload.get("code") or "").strip()
    owner_id = payload.get("owner_id")
    lines = payload.get("lines")
    note = str(payload.get("note") or "")

    errors = []
    if not code:
        errors.append("code 必填")
    elif len(code) > 32:
        errors.append("code 最长 32 字符")
    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        errors.append("owner_id 必填且为整数")
    if not isinstance(lines, list) or not lines:
        errors.append("lines 必须为非空数组")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    parsed_lines = []
    for i, ln in enumerate(lines):
        try:
            pid = int(ln.get("product_id"))
            from_loc = int(ln.get("from_location_id"))
            to_loc = int(ln.get("to_location_id"))
            qty = float(ln.get("qty"))
        except (TypeError, ValueError, AttributeError):
            return jsonify({"ok": False, "errors": [f"lines[{i}] 字段不合法"]}), 400
        if math.isnan(qty) or qty <= 0:
            return jsonify({"ok": False, "errors": [f"lines[{i}] qty 必须为正数"]}), 400
        if from_loc == to_loc:
            return jsonify({"ok": False, "errors": [
                f"lines[{i}] 来源库位与目标库位不能相同"]}), 400
        parsed_lines.append((pid, from_loc, to_loc, qty))

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400
    if db.execute("SELECT id FROM transfer WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"移库单编码已存在: {code}"]}), 409

    # 物料必须存在且货主归属一致；来源/目标库位必须启用（多货主隔离）
    for pid, from_loc, to_loc, _ in parsed_lines:
        p = db.execute(
            "SELECT owner_id FROM product WHERE id = ?", (pid,)
        ).fetchone()
        if p is None:
            return jsonify({"ok": False, "errors": [f"物料不存在: id={pid}"]}), 400
        if (p["owner_id"] or None) != owner_id:
            return jsonify({"ok": False, "errors": [
                f"物料 id={pid} 的货主与移库单货主不一致（多货主隔离）"
            ]}), 400
        for loc, label in ((from_loc, "来源"), (to_loc, "目标")):
            try:
                _check_location(db, loc, label)
            except ValueError as e:
                return jsonify({"ok": False, "errors": [str(e)]}), 400

    cur = db.execute(
        "INSERT INTO transfer (code, owner_id, note) VALUES (?, ?, ?)",
        (code, owner_id, note),
    )
    transfer_id = cur.lastrowid
    db.executemany(
        "INSERT INTO transfer_line (transfer_id, product_id, from_location_id, "
        "to_location_id, qty) VALUES (?, ?, ?, ?, ?)",
        [(transfer_id, pid, f, t, q) for pid, f, t, q in parsed_lines],
    )
    db.commit()
    tf = _load_transfer(db, transfer_id)
    return jsonify({"ok": True, "transfer": dict(tf),
                    "lines": [_dict_line(l) for l in _load_lines(db, transfer_id)]}), 201


@transfer_bp.route("", methods=["GET"])
def list_transfers():
    args = request.args
    status = (args.get("status") or "").strip()
    keyword = (args.get("keyword") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(100, max(1, int(args.get("page_size", 20))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if status:
        where.append("t.status = ?")
        params.append(status)
    if keyword:
        where.append("t.code LIKE ?")
        params.append(f"%{keyword}%")
    S.scoped_where(where, params, table="t")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM transfer t {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT t.*, o.code AS owner_code, o.name AS owner_name
            FROM transfer t JOIN owner o ON o.id = t.owner_id {where_sql}
            ORDER BY t.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "transfers": [dict(r) for r in rows]})


@transfer_bp.route("/<int:transfer_id>", methods=["GET"])
def get_transfer(transfer_id):
    db = get_db()
    tf = _load_transfer(db, transfer_id)
    if tf is None:
        return jsonify({"ok": False, "error": "移库单不存在"}), 404
    o = db.execute("SELECT code, name FROM owner WHERE id = ?", (tf["owner_id"],)).fetchone()
    body = dict(tf)
    body["owner_code"], body["owner_name"] = (o["code"], o["name"]) if o else (None, None)
    return jsonify({"ok": True, "transfer": body,
                    "lines": [_dict_line(l) for l in _load_lines(db, transfer_id)]})


@transfer_bp.route("/<int:transfer_id>/move", methods=["POST"])
def move(transfer_id):
    """执行移库：来源位按可用量转出，同事务一减一增，状态 → done。"""
    db = get_db()
    tf = _load_transfer(db, transfer_id)
    if tf is None:
        return jsonify({"ok": False, "error": "移库单不存在"}), 404
    if tf["status"] in (C.TRANSFER_DONE, C.TRANSFER_CANCELLED):
        return jsonify({"ok": False, "error": f"状态为 {tf['status']}，不可移库"}), 409
    try:
        db.execute("BEGIN IMMEDIATE")
        tf = _load_transfer(db, transfer_id)
        if tf["status"] in (C.TRANSFER_DONE, C.TRANSFER_CANCELLED):
            raise ValueError(f"状态为 {tf['status']}，不可移库")
        lines = _load_lines(db, transfer_id)
        for l in lines:
            # 可用量 = 在手 - 已分配（不移动已拣货/已分配库存）
            row = db.execute(
                "SELECT qty, allocated_qty FROM stock "
                "WHERE owner_id = ? AND product_id = ? AND location_id = ?",
                (tf["owner_id"], l["product_id"], l["from_location_id"]),
            ).fetchone()
            on_hand = row["qty"] if row else 0
            allocated = row["allocated_qty"] if row else 0
            if on_hand - allocated < l["qty"]:
                raise ValueError(
                    f"来源库位可用库存不足: 物料 id={l['product_id']} 在手 {on_hand}, "
                    f"已分配 {allocated}, 可用 {on_hand - allocated}, 需移 {l['qty']}"
                )
            stock_move(db, tf["owner_id"], l["product_id"],
                       l["from_location_id"], l["to_location_id"], l["qty"],
                       C.MOVEMENT_MOVE_OUT, C.MOVEMENT_MOVE_IN, transfer_id,
                       note=f"移库单 {tf['code']}")
        db.execute(
            "UPDATE transfer SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (C.TRANSFER_DONE, transfer_id),
        )
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": C.TRANSFER_DONE,
                    "lines": [_dict_line(l) for l in _load_lines(db, transfer_id)]})


@transfer_bp.route("/<int:transfer_id>/cancel", methods=["POST"])
def cancel_transfer(transfer_id):
    """取消：仅未执行的移库单可取消。"""
    db = get_db()
    tf = _load_transfer(db, transfer_id)
    if tf is None:
        return jsonify({"ok": False, "error": "移库单不存在"}), 404
    if tf["status"] != C.TRANSFER_OPEN:
        return jsonify({"ok": False, "error": f"状态为 {tf['status']}，仅未执行单可取消"}), 409
    db.execute("UPDATE transfer SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
               (C.TRANSFER_CANCELLED, transfer_id))
    db.commit()
    return jsonify({"ok": True, "status": C.TRANSFER_CANCELLED})
