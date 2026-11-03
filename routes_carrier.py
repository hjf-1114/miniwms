"""承运商主数据模块：/api/carriers 的增删改查。

承运商是物流履约资源（后续发快递/零担/整车等业务的基础参照）。
M14 落地承运商主数据，独立于多货主（承运商为全系统共享的物流资源目录）。
"""
from flask import Blueprint, jsonify, request

from main import get_db, request_payload

carrier_bp = Blueprint("carrier", __name__, url_prefix="/api/carriers")

VALID_CARRIER_TYPES = ("快递", "零担", "整车", "其他")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_carrier_payload(payload, *, partial=False):
    errors = []
    cleaned = {}

    if "code" in payload or not partial:
        code = str(payload.get("code") or "").strip()
        if not code:
            errors.append("code 必填")
        elif len(code) > 32:
            errors.append("code 最长 32 字符")
        else:
            cleaned["code"] = code

    if "name" in payload or not partial:
        name = str(payload.get("name") or "").strip()
        if not name:
            errors.append("name 必填")
        elif len(name) > 128:
            errors.append("name 最长 128 字符")
        else:
            cleaned["name"] = name

    if "contact" in payload:
        cleaned["contact"] = str(payload.get("contact") or "").strip()
    if "phone" in payload:
        cleaned["phone"] = str(payload.get("phone") or "").strip()
    if "api_code" in payload:
        cleaned["api_code"] = str(payload.get("api_code") or "").strip()
    if "address" in payload:
        cleaned["address"] = str(payload.get("address") or "").strip()

    if "carrier_type" in payload:
        ct = str(payload.get("carrier_type") or "").strip()
        if ct not in VALID_CARRIER_TYPES:
            errors.append(f"carrier_type 只能是 {'/'.join(VALID_CARRIER_TYPES)}")
        else:
            cleaned["carrier_type"] = ct

    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        if status not in ("active", "inactive"):
            errors.append("status 只能是 active/inactive")
        else:
            cleaned["status"] = status

    if "note" in payload:
        cleaned["note"] = str(payload.get("note") or "")

    return errors, cleaned


def _code_exists(db, code, exclude_id=None):
    sql = "SELECT id FROM carrier WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


@carrier_bp.route("", methods=["POST"])
def create_carrier():
    errors, cleaned = _validate_carrier_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"承运商编码已存在: {cleaned['code']}"]}), 409

    cur = db.execute(
        """INSERT INTO carrier (code, name, contact, phone, api_code, address, carrier_type, status, note)
           VALUES (:code, :name, :contact, :phone, :api_code, :address, :carrier_type, :status, :note)""",
        {
            **cleaned,
            "contact": cleaned.get("contact", ""),
            "phone": cleaned.get("phone", ""),
            "api_code": cleaned.get("api_code", ""),
            "address": cleaned.get("address", ""),
            "carrier_type": cleaned.get("carrier_type", "快递"),
            "status": cleaned.get("status", "active"),
            "note": cleaned.get("note", ""),
        },
    )
    db.commit()
    row = db.execute("SELECT * FROM carrier WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "carrier": dict(row)}), 201


@carrier_bp.route("", methods=["GET"])
def list_carriers():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    carrier_type = (args.get("carrier_type") or "").strip()
    status = (args.get("status") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if keyword:
        where.append("(code LIKE ? OR name LIKE ? OR api_code LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if carrier_type:
        where.append("carrier_type = ?")
        params.append(carrier_type)
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM carrier {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM carrier {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "carriers": [dict(r) for r in rows],
        }
    )


@carrier_bp.route("/<int:carrier_id>", methods=["GET"])
def get_carrier(carrier_id):
    row = get_db().execute("SELECT * FROM carrier WHERE id = ?", (carrier_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "承运商不存在"}), 404
    return jsonify({"ok": True, "carrier": dict(row)})


@carrier_bp.route("/<int:carrier_id>", methods=["PUT"])
def update_carrier(carrier_id):
    errors, cleaned = _validate_carrier_payload(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM carrier WHERE id = ?", (carrier_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "承运商不存在"}), 404
    if "code" in cleaned and _code_exists(db, cleaned["code"], exclude_id=carrier_id):
        return jsonify({"ok": False, "errors": [f"承运商编码已存在: {cleaned['code']}"]}), 409

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = carrier_id
    db.execute(
        f"UPDATE carrier SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM carrier WHERE id = ?", (carrier_id,)).fetchone()
    return jsonify({"ok": True, "carrier": dict(row)})


@carrier_bp.route("/<int:carrier_id>", methods=["DELETE"])
def delete_carrier(carrier_id):
    db = get_db()
    if db.execute("SELECT id FROM carrier WHERE id = ?", (carrier_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "承运商不存在"}), 404
    db.execute("DELETE FROM carrier WHERE id = ?", (carrier_id,))
    db.commit()
    return jsonify({"ok": True})
