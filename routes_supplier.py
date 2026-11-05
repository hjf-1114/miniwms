"""供应商主数据模块：/api/suppliers 的增删改查。

供应商是收货单（ASN）的上游来源，归属货主（多货主隔离，呼应客户的设计）。
M16 落地供应商主数据，并为 ASN 提供 supplier_id 关联与名称/联系人/电话/地址快照。
"""
import scope as S
from flask import Blueprint, jsonify, request

from main import get_db, request_payload

supplier_bp = Blueprint("supplier", __name__, url_prefix="/api/suppliers")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_supplier_payload(payload, *, partial=False):
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

    if "owner_id" in payload or not partial:
        try:
            cleaned["owner_id"] = int(payload.get("owner_id"))
        except (TypeError, ValueError):
            errors.append("owner_id 必填且为整数")

    if "contact" in payload:
        cleaned["contact"] = str(payload.get("contact") or "").strip()
    if "phone" in payload:
        cleaned["phone"] = str(payload.get("phone") or "").strip()
    if "address" in payload:
        cleaned["address"] = str(payload.get("address") or "").strip()
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
    sql = "SELECT id FROM supplier WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


@supplier_bp.route("", methods=["POST"])
def create_supplier():
    errors, cleaned = _validate_supplier_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (cleaned["owner_id"],)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={cleaned['owner_id']}"]}), 400
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"供应商编码已存在: {cleaned['code']}"]}), 409

    cur = db.execute(
        """INSERT INTO supplier (code, name, owner_id, contact, phone, address, status, note)
           VALUES (:code, :name, :owner_id, :contact, :phone, :address, :status, :note)""",
        {
            **cleaned,
            "contact": cleaned.get("contact", ""),
            "phone": cleaned.get("phone", ""),
            "address": cleaned.get("address", ""),
            "status": cleaned.get("status", "active"),
            "note": cleaned.get("note", ""),
        },
    )
    db.commit()
    row = db.execute("SELECT * FROM supplier WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "supplier": dict(row)}), 201


@supplier_bp.route("", methods=["GET"])
def list_suppliers():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    owner_id = (args.get("owner_id") or "").strip()
    status = (args.get("status") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if keyword:
        where.append("(s.code LIKE ? OR s.name LIKE ? OR s.contact LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if owner_id:
        where.append("owner_id = ?")
        params.append(int(owner_id))
    if status:
        where.append("status = ?")
        params.append(status)
    S.scoped_where(where, params, table="s")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM supplier s {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"""SELECT s.*, o.code AS owner_code, o.name AS owner_name
            FROM supplier s JOIN owner o ON o.id = s.owner_id {where_sql}
            ORDER BY s.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "suppliers": [dict(r) for r in rows],
        }
    )


@supplier_bp.route("/<int:supplier_id>", methods=["GET"])
def get_supplier(supplier_id):
    row = get_db().execute("SELECT * FROM supplier WHERE id = ?", (supplier_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "供应商不存在"}), 404
    return jsonify({"ok": True, "supplier": dict(row)})


@supplier_bp.route("/<int:supplier_id>", methods=["PUT"])
def update_supplier(supplier_id):
    errors, cleaned = _validate_supplier_payload(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM supplier WHERE id = ?", (supplier_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "供应商不存在"}), 404
    if "owner_id" in cleaned and db.execute(
        "SELECT id FROM owner WHERE id = ?", (cleaned["owner_id"],)
    ).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={cleaned['owner_id']}"]}), 400
    if "code" in cleaned and _code_exists(db, cleaned["code"], exclude_id=supplier_id):
        return jsonify({"ok": False, "errors": [f"供应商编码已存在: {cleaned['code']}"]}), 409

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = supplier_id
    db.execute(
        f"UPDATE supplier SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM supplier WHERE id = ?", (supplier_id,)).fetchone()
    return jsonify({"ok": True, "supplier": dict(row)})


@supplier_bp.route("/<int:supplier_id>", methods=["DELETE"])
def delete_supplier(supplier_id):
    db = get_db()
    if db.execute("SELECT id FROM supplier WHERE id = ?", (supplier_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "供应商不存在"}), 404
    # 被收货单（ASN）引用时禁止删除（多货主隔离：仅校验同货主引用）
    ref = db.execute("SELECT id, code FROM asn WHERE supplier_id = ? LIMIT 1", (supplier_id,)).fetchone()
    if ref is not None:
        return jsonify({"ok": False, "errors": [
            f"供应商已被收货单引用（ASN {ref['code']}），不可删除"
        ]}), 409
    db.execute("DELETE FROM supplier WHERE id = ?", (supplier_id,))
    db.commit()
    return jsonify({"ok": True})
