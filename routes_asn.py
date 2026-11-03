"""收货单（ASN）与上架模块：/api/asns。

流程（单据驱动库存，参考 GreaterWMS 的 ASN 与 Sentry 的收货/上架动线）：
1. 创建 ASN（指定货主 + 行：物料 × 预期数量）
2. 收货 receive：逐行收到「收货区」库位（receiving），库存 +，流水 asn_receive
3. 上架 putaway：从收货区位移到存储/拣货位，一减一增两条流水，同一事务
4. 收满且上完自动 closed；未收货可 cancel

一致性规则：
- ASN 的货主必须与行内物料的货主一致（都为自营/都为同一货主）
- 收货目标位必须 loc_type=receiving 且启用；上架目标位必须 storage/picking 且启用
"""
import math

from flask import Blueprint, jsonify, request

import constants as C
from inventory_service import StockError, stock_move, stock_up
from main import get_db, request_payload

asn_bp = Blueprint("asn", __name__, url_prefix="/api/asns")


def _load_asn(db, asn_id):
    return db.execute("SELECT * FROM asn WHERE id = ?", (asn_id,)).fetchone()


def _load_lines(db, asn_id):
    return db.execute(
        """SELECT al.*, p.sku, p.name AS product_name, p.owner_id AS product_owner_id
           FROM asn_line al JOIN product p ON p.id = al.product_id
           WHERE al.asn_id = ? ORDER BY al.id""",
        (asn_id,),
    ).fetchall()


def _check_location(db, location_id, allowed_types, label):
    row = db.execute(
        "SELECT code, status, loc_type FROM location WHERE id = ?", (location_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{label}库位不存在: id={location_id}")
    if row["status"] != "active":
        raise ValueError(f"{label}库位已停用: {row['code']}")
    if row["loc_type"] not in allowed_types:
        raise ValueError(f"{label}库位类型不正确: {row['code']}")
    return row


def _advance_status(db, asn_id, lines):
    """根据行数据推进单据状态（open→receiving→putaway→closed）。"""
    fully_received = all(l["received_qty"] >= l["expected_qty"] for l in lines)
    fully_putaway = all(l["putaway_qty"] >= l["expected_qty"] for l in lines)
    any_received = any(l["received_qty"] > 0 for l in lines)

    if fully_putaway:
        status = C.ASN_CLOSED
    elif fully_received:
        status = C.ASN_PUTAWAY
    elif any_received:
        status = C.ASN_RECEIVING
    else:
        status = C.ASN_OPEN
    db.execute(
        "UPDATE asn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, asn_id),
    )
    return status


@asn_bp.route("", methods=["POST"])
def create_asn():
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
            qty = float(ln.get("expected_qty"))
        except (TypeError, ValueError, AttributeError):
            return jsonify({"ok": False, "errors": [f"lines[{i}] 字段不合法"]}), 400
        if math.isnan(qty) or qty <= 0:
            return jsonify({"ok": False, "errors": [f"lines[{i}] expected_qty 必须为正数"]}), 400
        parsed_lines.append((pid, qty))

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400
    if db.execute("SELECT id FROM asn WHERE code = ?", (code,)).fetchone() is not None:
        return jsonify({"ok": False, "errors": [f"ASN 编码已存在: {code}"]}), 409

    # 物料必须存在，且货主归属与单据一致
    for pid, _ in parsed_lines:
        p = db.execute(
            "SELECT owner_id FROM product WHERE id = ?", (pid,)
        ).fetchone()
        if p is None:
            return jsonify({"ok": False, "errors": [f"物料不存在: id={pid}"]}), 400
        if (p["owner_id"] or None) != owner_id:
            return jsonify({"ok": False, "errors": [
                f"物料 id={pid} 的货主与 ASN 货主不一致（多货主隔离）"
            ]}), 400

    cur = db.execute(
        "INSERT INTO asn (code, owner_id, note) VALUES (?, ?, ?)",
        (code, owner_id, note),
    )
    asn_id = cur.lastrowid
    db.executemany(
        "INSERT INTO asn_line (asn_id, product_id, expected_qty) VALUES (?, ?, ?)",
        [(asn_id, pid, qty) for pid, qty in parsed_lines],
    )
    db.commit()
    asn = _load_asn(db, asn_id)
    lines_out = [_dict_line(l) for l in _load_lines(db, asn_id)]
    return jsonify({"ok": True, "asn": dict(asn), "lines": lines_out}), 201


def _dict_line(l):
    d = dict(l)
    d["remaining_receive"] = max(0, l["expected_qty"] - l["received_qty"])
    d["remaining_putaway"] = max(0, l["received_qty"] - l["putaway_qty"])
    return d


@asn_bp.route("", methods=["GET"])
def list_asns():
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
        where.append("a.status = ?")
        params.append(status)
    if keyword:
        where.append("a.code LIKE ?")
        params.append(f"%{keyword}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM asn a {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT a.*, o.code AS owner_code, o.name AS owner_name
            FROM asn a JOIN owner o ON o.id = a.owner_id {where_sql}
            ORDER BY a.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page,
                    "page_size": page_size, "asns": [dict(r) for r in rows]})


@asn_bp.route("/<int:asn_id>", methods=["GET"])
def get_asn(asn_id):
    db = get_db()
    asn = _load_asn(db, asn_id)
    if asn is None:
        return jsonify({"ok": False, "error": "ASN 不存在"}), 404
    o = db.execute("SELECT code, name FROM owner WHERE id = ?", (asn["owner_id"],)).fetchone()
    body = dict(asn)
    body["owner_code"], body["owner_name"] = (o["code"], o["name"]) if o else (None, None)
    return jsonify({"ok": True, "asn": body,
                    "lines": [_dict_line(l) for l in _load_lines(db, asn_id)]})


@asn_bp.route("/<int:asn_id>/receive", methods=["POST"])
def receive(asn_id):
    """收货：line_id + receiving 库位 + 数量 → 库存 +，单据状态推进。"""
    payload = request_payload()
    try:
        line_id = int(payload.get("line_id"))
        location_id = int(payload.get("location_id"))
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["line_id/location_id/qty 必填且合法"]}), 400

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            raise ValueError("ASN 不存在")
        if asn["status"] in (C.ASN_CLOSED, C.ASN_CANCELLED):
            raise ValueError(f"单据状态为 {asn['status']}，不可收货")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 ASN")
        remaining = line["expected_qty"] - line["received_qty"]
        if qty <= 0 or qty > remaining:
            raise ValueError(f"收货数量必须为正且不超过剩余 {remaining}")
        _check_location(db, location_id, ("receiving",), "收货目标")

        stock_up(db, asn["owner_id"], line["product_id"], location_id, qty,
                 C.MOVEMENT_ASN_RECEIVE, asn_id, note=f"ASN {asn['code']} 收货")
        db.execute(
            "UPDATE asn_line SET received_qty = received_qty + ? WHERE id = ?",
            (qty, line_id),
        )
        lines = _load_lines(db, asn_id)
        status = _advance_status(db, asn_id, lines)
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": status,
                    "lines": [_dict_line(l) for l in _load_lines(db, asn_id)]})


@asn_bp.route("/<int:asn_id>/putaway", methods=["POST"])
def putaway(asn_id):
    """上架：从收货区位 → 存储/拣货位，一减一增同事务。"""
    payload = request_payload()
    try:
        line_id = int(payload.get("line_id"))
        from_loc = int(payload.get("from_location_id"))
        to_loc = int(payload.get("to_location_id"))
        qty = float(payload.get("qty"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["line_id/from_location_id/to_location_id/qty 必填且合法"]}), 400

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        asn = _load_asn(db, asn_id)
        if asn is None:
            raise ValueError("ASN 不存在")
        if asn["status"] in (C.ASN_CLOSED, C.ASN_CANCELLED, C.ASN_OPEN):
            raise ValueError(f"单据状态为 {asn['status']}，无已收货库存可上架")
        if from_loc == to_loc:
            raise ValueError("上架来源位与目标位相同")
        line = db.execute("SELECT * FROM asn_line WHERE id = ? AND asn_id = ?",
                          (line_id, asn_id)).fetchone()
        if line is None:
            raise ValueError("行不存在或不属于该 ASN")
        remaining = line["received_qty"] - line["putaway_qty"]
        if qty <= 0 or qty > remaining:
            raise ValueError(f"上架数量必须为正且不超过待上架 {remaining}")
        _check_location(db, from_loc, ("receiving",), "上架来源")
        _check_location(db, to_loc, ("storage", "picking"), "上架目标")

        stock_move(db, asn["owner_id"], line["product_id"], from_loc, to_loc, qty,
                   C.MOVEMENT_ASN_PUTAWAY_OUT, C.MOVEMENT_ASN_PUTAWAY_IN, asn_id,
                   note=f"ASN {asn['code']} 上架")
        db.execute("UPDATE asn_line SET putaway_qty = putaway_qty + ? WHERE id = ?",
                   (qty, line_id))
        lines = _load_lines(db, asn_id)
        status = _advance_status(db, asn_id, lines)
        db.commit()
    except (ValueError, StockError) as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "status": status,
                    "lines": [_dict_line(l) for l in _load_lines(db, asn_id)]})


@asn_bp.route("/<int:asn_id>/cancel", methods=["POST"])
def cancel_asn(asn_id):
    """取消：仅未收货的 ASN 可取消。"""
    db = get_db()
    asn = _load_asn(db, asn_id)
    if asn is None:
        return jsonify({"ok": False, "error": "ASN 不存在"}), 404
    if asn["status"] != C.ASN_OPEN:
        return jsonify({"ok": False, "error": f"状态为 {asn['status']}，仅未收货单可取消"}), 409
    db.execute("UPDATE asn SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
               (C.ASN_CANCELLED, asn_id))
    db.commit()
    return jsonify({"ok": True, "status": C.ASN_CANCELLED})
