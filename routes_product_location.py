"""货品固定货位绑定（M20）：/api/product-locations。

一个 SKU 可绑定多个固定货位，上架推荐优先取绑定货位。
被绑定的货位必须是启用的存储/拣货位，因为 putaway() 只接受这两类目标位。
"""
from flask import Blueprint, jsonify, request

from main import get_db, request_payload

product_location_bp = Blueprint(
    "product_location", __name__, url_prefix="/api/product-locations"
)

VALID_TARGET_TYPES = ("storage", "picking")


def _dict(row):
    d = dict(row)
    return {
        "id": d["id"],
        "product_id": d["product_id"],
        "location_id": d["location_id"],
        "priority": d["priority"],
        "note": d["note"],
        "created_at": d["created_at"],
        "sku": d["sku"],
        "product_name": d["product_name"],
        "location_code": d["location_code"],
        "location_name": d["location_name"],
        "zone_name": d["zone_name"] or "",
        "warehouse_code": d["warehouse_code"] or "",
    }


SELECT_SQL = """
    SELECT pl.*, p.sku AS sku, p.name AS product_name,
           l.code AS location_code, l.name AS location_name,
           z.name AS zone_name, w.code AS warehouse_code
    FROM product_location pl
    JOIN product p   ON p.id = pl.product_id
    JOIN location l  ON l.id = pl.location_id
    LEFT JOIN zone z ON z.id = l.zone_id
    LEFT JOIN warehouse w ON w.id = l.warehouse_id
"""


@product_location_bp.route("", methods=["GET"])
def list_bindings():
    args = request.args
    product_id = (args.get("product_id") or "").strip()
    location_id = (args.get("location_id") or "").strip()
    where, params = [], []
    if product_id:
        try:
            where.append("pl.product_id = ?")
            params.append(int(product_id))
        except ValueError:
            return jsonify({"ok": False, "errors": ["product_id 必须为整数"]}), 400
    if location_id:
        try:
            where.append("pl.location_id = ?")
            params.append(int(location_id))
        except ValueError:
            return jsonify({"ok": False, "errors": ["location_id 必须为整数"]}), 400
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    rows = db.execute(
        SELECT_SQL + f" {where_sql} ORDER BY pl.priority DESC, pl.id", params
    ).fetchall()
    return jsonify({"ok": True, "total": len(rows), "bindings": [_dict(r) for r in rows]})


@product_location_bp.route("", methods=["POST"])
def create_binding():
    payload = request_payload()
    try:
        product_id = int(payload.get("product_id"))
        location_id = int(payload.get("location_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["product_id/location_id 必填且为整数"]}), 400
    try:
        priority = int(payload.get("priority") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["priority 必须为整数"]}), 400
    note = str(payload.get("note") or "").strip()

    db = get_db()
    if db.execute("SELECT id FROM product WHERE id = ?", (product_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": ["货品不存在"]}), 400
    loc = db.execute("SELECT * FROM location WHERE id = ?", (location_id,)).fetchone()
    if loc is None:
        return jsonify({"ok": False, "errors": ["库位不存在"]}), 400
    if loc["status"] != "active" or loc["loc_type"] not in VALID_TARGET_TYPES:
        return jsonify({"ok": False, "errors": [
            "只能绑定启用中的存储位/拣货位（上架目标位仅接受这两类）"
        ]}), 400
    if db.execute(
        "SELECT id FROM product_location WHERE product_id = ? AND location_id = ?",
        (product_id, location_id),
    ).fetchone():
        return jsonify({"ok": False, "errors": ["该货品已绑定此货位"]}), 409

    cur = db.execute(
        "INSERT INTO product_location (product_id, location_id, priority, note) VALUES (?, ?, ?, ?)",
        (product_id, location_id, priority, note),
    )
    db.commit()
    row = db.execute(SELECT_SQL + " WHERE pl.id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "binding": _dict(row)}), 201


@product_location_bp.route("/<int:bind_id>", methods=["DELETE"])
def delete_binding(bind_id):
    db = get_db()
    if db.execute("SELECT id FROM product_location WHERE id = ?", (bind_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "绑定记录不存在"}), 404
    db.execute("DELETE FROM product_location WHERE id = ?", (bind_id,))
    db.commit()
    return jsonify({"ok": True})
