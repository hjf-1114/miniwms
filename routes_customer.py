"""客户主数据模块：/api/customers 的增删改查。

客户是出库单（DN）的收货方，在**多货主**场景下归属于某个货主——
货主 A 的客户与货主 B 的客户彼此隔离，避免跨货主串用。
M7 落地客户主数据，并由此补上「出库需要客户资料」这一此前遗漏的维度。
"""
from flask import Blueprint, jsonify, request

from main import get_db, request_payload

customer_bp = Blueprint("customer", __name__, url_prefix="/api/customers")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_customer_payload(payload, *, partial=False):
    """校验客户字段。partial=True 只校验出现过的字段。返回 (errors, cleaned)。"""
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
            oid = int(payload.get("owner_id"))
        except (TypeError, ValueError):
            errors.append("owner_id 必填且为整数")
        else:
            cleaned["owner_id"] = oid

    if "contact" in payload:
        cleaned["contact"] = str(payload.get("contact") or "").strip()
    if "phone" in payload:
        cleaned["phone"] = str(payload.get("phone") or "").strip()
    if "address" in payload:
        cleaned["address"] = str(payload.get("address") or "").strip()
    if "email" in payload:
        cleaned["email"] = str(payload.get("email") or "").strip()
    if "level" in payload:
        cleaned["level"] = str(payload.get("level") or "").strip() or "普通"
    if "region" in payload:
        cleaned["region"] = str(payload.get("region") or "").strip()
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
    sql = "SELECT id FROM customer WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


@customer_bp.route("", methods=["POST"])
def create_customer():
    errors, cleaned = _validate_customer_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"客户编码已存在: {cleaned['code']}"]}), 409
    if db.execute("SELECT id FROM owner WHERE id = ?", (cleaned["owner_id"],)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={cleaned['owner_id']}"]}), 400

    cur = db.execute(
        "INSERT INTO customer (code, name, owner_id, contact, phone, address, email, level, region, status, note) "
        "VALUES (:code, :name, :owner_id, :contact, :phone, :address, :email, :level, :region, :status, :note)",
        {
            **cleaned,
            "contact": cleaned.get("contact", ""),
            "phone": cleaned.get("phone", ""),
            "address": cleaned.get("address", ""),
            "email": cleaned.get("email", ""),
            "level": cleaned.get("level", "普通"),
            "region": cleaned.get("region", ""),
            "status": cleaned.get("status", "active"),
            "note": cleaned.get("note", ""),
        },
    )
    db.commit()
    row = db.execute("SELECT * FROM customer WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "customer": dict(row)}), 201


@customer_bp.route("", methods=["GET"])
def list_customers():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    status = (args.get("status") or "").strip()
    owner_id = (args.get("owner_id") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if keyword:
        where.append("(c.code LIKE ? OR c.name LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if status:
        where.append("c.status = ?")
        params.append(status)
    if owner_id:
        try:
            where.append("c.owner_id = ?")
            params.append(int(owner_id))
        except ValueError:
            return jsonify({"ok": False, "errors": ["owner_id 必须为整数"]}), 400
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM customer c {where_sql}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"""SELECT c.*, o.code AS owner_code, o.name AS owner_name
            FROM customer c JOIN owner o ON o.id = c.owner_id {where_sql}
            ORDER BY c.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({
        "ok": True, "total": total, "page": page, "page_size": page_size,
        "customers": [dict(r) for r in rows],
    })


@customer_bp.route("/<int:customer_id>", methods=["GET"])
def get_customer(customer_id):
    row = get_db().execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "客户不存在"}), 404
    return jsonify({"ok": True, "customer": dict(row)})


@customer_bp.route("/<int:customer_id>", methods=["PUT"])
def update_customer(customer_id):
    errors, cleaned = _validate_customer_payload(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400
    if "owner_id" in cleaned:
        db = get_db()
        if db.execute("SELECT id FROM owner WHERE id = ?", (cleaned["owner_id"],)).fetchone() is None:
            return jsonify({"ok": False, "errors": [f"货主不存在: id={cleaned['owner_id']}"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM customer WHERE id = ?", (customer_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "客户不存在"}), 404
    if "code" in cleaned and _code_exists(db, cleaned["code"], exclude_id=customer_id):
        return jsonify({"ok": False, "errors": [f"客户编码已存在: {cleaned['code']}"]}), 409

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = customer_id
    db.execute(
        f"UPDATE customer SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM customer WHERE id = ?", (customer_id,)).fetchone()
    return jsonify({"ok": True, "customer": dict(row)})


@customer_bp.route("/<int:customer_id>", methods=["DELETE"])
def delete_customer(customer_id):
    db = get_db()
    if db.execute("SELECT id FROM customer WHERE id = ?", (customer_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "客户不存在"}), 404
    # 客户已被出库单引用时禁止删除（保留出库溯源链路）
    n = db.execute(
        "SELECT COUNT(*) AS n FROM dn WHERE customer_id = ?", (customer_id,)
    ).fetchone()["n"]
    if n:
        return jsonify(
            {"ok": False, "error": f"该客户已被 {n} 张出库单引用，禁止删除；请先停用或归档"}
        ), 409
    db.execute("DELETE FROM customer WHERE id = ?", (customer_id,))
    db.commit()
    return jsonify({"ok": True})
