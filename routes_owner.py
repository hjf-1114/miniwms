"""货主主数据模块：/api/owners 的增删改查。

多货主（3PL）是 WMS 的核心模型：每个货主拥有独立的物料档案与库存，
后续收货/拣货/库存账/盘点等所有单据都挂 owner 维度。
M2 先落地货主主数据，并把物料挂接到货主。
"""
import sqlite3

from flask import Blueprint, jsonify, request

from main import get_db, request_payload

owner_bp = Blueprint("owner", __name__, url_prefix="/api/owners")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_owner_payload(payload, *, partial=False):
    """校验货主字段。partial=True 只校验出现过的字段。返回 (errors, cleaned)。"""
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
    sql = "SELECT id FROM owner WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


@owner_bp.route("", methods=["POST"])
def create_owner():
    errors, cleaned = _validate_owner_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"货主编码已存在: {cleaned['code']}"]}), 409

    cur = db.execute(
        "INSERT INTO owner (code, name, contact, phone, status, note) "
        "VALUES (:code, :name, :contact, :phone, :status, :note)",
        {
            **cleaned,
            "contact": cleaned.get("contact", ""),
            "phone": cleaned.get("phone", ""),
            "status": cleaned.get("status", "active"),
            "note": cleaned.get("note", ""),
        },
    )
    db.commit()
    row = db.execute("SELECT * FROM owner WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "owner": dict(row)}), 201


@owner_bp.route("", methods=["GET"])
def list_owners():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    status = (args.get("status") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if keyword:
        where.append("(code LIKE ? OR name LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM owner {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM owner {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "owners": [dict(r) for r in rows],
        }
    )


@owner_bp.route("/<int:owner_id>", methods=["GET"])
def get_owner(owner_id):
    row = get_db().execute("SELECT * FROM owner WHERE id = ?", (owner_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "货主不存在"}), 404
    return jsonify({"ok": True, "owner": dict(row)})


@owner_bp.route("/<int:owner_id>", methods=["PUT"])
def update_owner(owner_id):
    errors, cleaned = _validate_owner_payload(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "货主不存在"}), 404
    if "code" in cleaned and _code_exists(db, cleaned["code"], exclude_id=owner_id):
        return jsonify({"ok": False, "errors": [f"货主编码已存在: {cleaned['code']}"]}), 409

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = owner_id
    db.execute(
        f"UPDATE owner SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM owner WHERE id = ?", (owner_id,)).fetchone()
    return jsonify({"ok": True, "owner": dict(row)})


@owner_bp.route("/<int:owner_id>", methods=["DELETE"])
def delete_owner(owner_id):
    db = get_db()
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "货主不存在"}), 404
    # 货主下还有物料档案时不允许删除
    n = db.execute(
        "SELECT COUNT(*) AS n FROM product WHERE owner_id = ?", (owner_id,)
    ).fetchone()["n"]
    if n:
        return jsonify(
            {"ok": False, "error": f"该货主名下还有 {n} 个物料档案，禁止删除；请先转移或停用"}
        ), 409
    db.execute("DELETE FROM owner WHERE id = ?", (owner_id,))
    db.commit()
    return jsonify({"ok": True})
