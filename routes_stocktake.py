"""盘点单（Stocktake）模块：/api/stocktakes。

流程（账实核对，与 ASN/DN/Transfer 对称，全部经 inventory_service 落流水）：
1. 创建盘点单（指定货主，可选范围）
2. 冻结 freeze：快照当前货主库存（owner×product×location 账存）到盘点单行，状态 → frozen
3. 盲盘 count：逐行录入实盘数 counted_qty（盲盘：操作员录入实际数量，不看账存）
4. 差异调整 adjust：对每条 counted≠book 的行，以 adjustment 流水把账存修正为实盘，状态 → adjusted
5. 解冻结案 close：状态 → closed

一致性：adjust 产生的 adjustment 流水计入 stock_movement，故 /api/stock/reconcile 仍一致。
注意：MVP 的冻结为「快照级」冻结（记录时点账存供盲盘比对），不强制硬锁其他出入库作业。
"""
import math

from flask import Blueprint, jsonify, request

import constants as C
from inventory_service import StockError, adjust_stock
from main import get_db, request_payload

stocktake_bp = Blueprint("stocktake", __name__, url_prefix="/api/stocktakes")


def _load(db, stocktake_id):
    return db.execute("SELECT * FROM stocktake WHERE id = ?", (stocktake_id,)).fetchone()


def _load_lines(db, stocktake_id):
    return db.execute(
        """SELECT sl.*, p.sku, p.name AS product_name,
                  l.code AS location_code, l.loc_type AS location_type
           FROM stocktake_line sl
           JOIN product p ON p.id = sl.product_id
           JOIN location l ON l.id = sl.location_id
           WHERE sl.stocktake_id = ? ORDER BY sl.id""",
        (stocktake_id,),
    ).fetchall()


def _dict_line(l):
    d = dict(l)
    d.pop("location_type", None)
    if l["counted_qty"] is None:
        d["diff"] = None
    else:
        d["diff"] = round(l["counted_qty"] - l["book_qty"], 6)
    return d


@stocktake_bp.route("", methods=["POST"])
def create_stocktake():
    payload = request_payload()
    code = str(payload.get("code") or "").strip()
    owner_id = payload.get("owner_id")
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
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400
    if db.execute("SELECT id FROM stocktake WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"盘点单编码已存在: {code}"]}), 409

    cur = db.execute(
        "INSERT INTO stocktake (code, owner_id, note) VALUES (?, ?, ?)",
        (code, owner_id, note),
    )
    st_id = cur.lastrowid
    db.commit()
    st = _load(db, st_id)
    return jsonify({"ok": True, "stocktake": dict(st), "lines": []}), 201


@stocktake_bp.route("", methods=["GET"])
def list_stocktakes():
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
        where.append("s.status = ?")
        params.append(status)
    if keyword:
        where.append("s.code LIKE ?")
        params.append(f"%{keyword}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM stocktake s {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT s.*, o.code AS owner_code, o.name AS owner_name
            FROM stocktake s JOIN owner o ON o.id = s.owner_id {where_sql}
            ORDER BY s.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "stocktakes": [dict(r) for r in rows]})


@stocktake_bp.route("/<int:stocktake_id>", methods=["GET"])
def get_stocktake(stocktake_id):
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify({"ok": False, "error": "盘点单不存在"}), 404
    o = db.execute("SELECT code, name FROM owner WHERE id = ?", (st["owner_id"],)).fetchone()
    body = dict(st)
    body["owner_code"], body["owner_name"] = (o["code"], o["name"]) if o else (None, None)
    return jsonify({"ok": True, "stocktake": body,
                    "lines": [_dict_line(l) for l in _load_lines(db, stocktake_id)]})


@stocktake_bp.route("/<int:stocktake_id>/freeze", methods=["POST"])
def freeze(stocktake_id):
    """冻结：快照货主当前有货库存到账存盘点行（盲盘基准），状态 → frozen。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify({"ok": False, "error": "盘点单不存在"}), 404
    if st["status"] != C.STOCKTAKE_OPEN:
        return jsonify({"ok": False, "error": f"状态为 {st['status']}，仅 open 可冻结"}), 409
    try:
        db.execute("BEGIN IMMEDIATE")
        st = _load(db, stocktake_id)
        if st["status"] != C.STOCKTAKE_OPEN:
            raise ValueError(f"状态为 {st['status']}，仅 open 可冻结")
        rows = db.execute(
            "SELECT owner_id, product_id, location_id, qty FROM stock "
            "WHERE owner_id = ? AND qty != 0", (st["owner_id"],)
        ).fetchall()
        for r in rows:
            db.execute(
                "INSERT INTO stocktake_line (stocktake_id, owner_id, product_id, "
                "location_id, book_qty, counted_qty) VALUES (?, ?, ?, ?, ?, NULL)",
                (stocktake_id, r["owner_id"], r["product_id"], r["location_id"], r["qty"]),
            )
        db.execute(
            "UPDATE stocktake SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (C.STOCKTAKE_FROZEN, stocktake_id),
        )
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "status": C.STOCKTAKE_FROZEN,
                    "lines": [_dict_line(l) for l in _load_lines(db, stocktake_id)]})


@stocktake_bp.route("/<int:stocktake_id>/count", methods=["POST"])
def count(stocktake_id):
    """盲盘：逐行录入实盘数（counted_qty），状态保持 frozen。"""
    payload = request_payload()
    entries = payload.get("lines")
    if not isinstance(entries, list) or not entries:
        return jsonify({"ok": False, "errors": ["lines 必须为非空数组"]}), 400

    parsed = []
    for i, e in enumerate(entries):
        try:
            line_id = int(e.get("line_id"))
            qty = float(e.get("counted_qty"))
        except (TypeError, ValueError, AttributeError):
            return jsonify({"ok": False, "errors": [f"lines[{i}] 字段不合法"]}), 400
        if math.isnan(qty) or qty < 0:
            return jsonify({"ok": False, "errors": [f"lines[{i}] counted_qty 必须 >= 0"]}), 400
        parsed.append((line_id, qty))

    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify({"ok": False, "error": "盘点单不存在"}), 404
    if st["status"] != C.STOCKTAKE_FROZEN:
        return jsonify({"ok": False, "error": f"状态为 {st['status']}，仅 frozen 可录入盲盘"}), 409
    for line_id, qty in parsed:
        res = db.execute(
            "UPDATE stocktake_line SET counted_qty = ? WHERE id = ? AND stocktake_id = ?",
            (qty, line_id, stocktake_id),
        )
        if res.rowcount == 0:
            return jsonify({"ok": False, "errors": [f"盘点行不存在或不属于该单: id={line_id}"]}), 400
    db.commit()
    return jsonify({"ok": True, "status": C.STOCKTAKE_FROZEN,
                    "lines": [_dict_line(l) for l in _load_lines(db, stocktake_id)]})


@stocktake_bp.route("/<int:stocktake_id>/adjust", methods=["POST"])
def adjust(stocktake_id):
    """差异调整：对每条 counted≠book 的行以 adjustment 流水把账存修正为实盘，状态 → adjusted。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify({"ok": False, "error": "盘点单不存在"}), 404
    if st["status"] != C.STOCKTAKE_FROZEN:
        return jsonify({"ok": False, "error": f"状态为 {st['status']}，仅 frozen 可调整"}), 409
    try:
        db.execute("BEGIN IMMEDIATE")
        st = _load(db, stocktake_id)
        if st["status"] != C.STOCKTAKE_FROZEN:
            raise ValueError(f"状态为 {st['status']}，仅 frozen 可调整")
        lines = _load_lines(db, stocktake_id)
        uncounted = [l for l in lines if l["counted_qty"] is None]
        if uncounted:
            raise ValueError("存在未盘点的行，请先完成盲盘再调整")
        for l in lines:
            delta = l["counted_qty"] - l["book_qty"]
            if abs(delta) > 1e-9:
                adjust_stock(db, st["owner_id"], l["product_id"], l["location_id"],
                             delta, C.MOVEMENT_ADJUSTMENT, stocktake_id,
                             note=f"盘点单 {st['code']} 差异调整")
        db.execute(
            "UPDATE stocktake SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (C.STOCKTAKE_ADJUSTED, stocktake_id),
        )
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "status": C.STOCKTAKE_ADJUSTED,
                    "lines": [_dict_line(l) for l in _load_lines(db, stocktake_id)]})


@stocktake_bp.route("/<int:stocktake_id>/close", methods=["POST"])
def close(stocktake_id):
    """解冻结案：冻结期结束，状态 → closed。"""
    db = get_db()
    st = _load(db, stocktake_id)
    if st is None:
        return jsonify({"ok": False, "error": "盘点单不存在"}), 404
    if st["status"] != C.STOCKTAKE_ADJUSTED:
        return jsonify({"ok": False, "error": f"状态为 {st['status']}，仅已调整单可解冻结案"}), 409
    db.execute(
        "UPDATE stocktake SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (C.STOCKTAKE_CLOSED, stocktake_id),
    )
    db.commit()
    return jsonify({"ok": True, "status": C.STOCKTAKE_CLOSED})
