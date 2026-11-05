"""仓库主数据模块：/api/warehouses 的增删改查。

仓库为运营方资产，全系统共享（一个物理仓库可存放多个货主的货物，这是 3PL WMS 的本质），
因此仓库不绑定货主；收货单（ASN）与出库单（DN）通过 source_warehouse 关联来源仓库。
M16 落地多仓库主数据，补齐此前「只支持多货主、未支持多仓库」的缺口。
"""
from flask import Blueprint, jsonify, request

from main import get_control_db, request_payload

warehouse_bp = Blueprint("warehouse", __name__, url_prefix="/api/warehouses")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_warehouse_payload(payload, *, partial=False):
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

    if "address" in payload:
        cleaned["address"] = str(payload.get("address") or "").strip()
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
    if "db_path" in payload:
        # 数据隔离配置：空 = 共用中央库；非空 = 该仓业务数据独立 SQLite 文件。
        # 必须过安全校验（项目目录内 + .db 结尾），并归一化为相对路径。
        from main import normalize_warehouse_db_path
        from scope import ScopeError
        try:
            cleaned["db_path"] = normalize_warehouse_db_path(
                str(payload.get("db_path") or "").strip()
            )
        except ScopeError as e:
            errors.append(str(e))

    return errors, cleaned


def _maybe_init_db(cleaned):
    """配置了独立数据文件时立即建库，避免「改完配置却看不到文件」的困惑。"""
    if cleaned.get("db_path"):
        from main import init_warehouse_db
        init_warehouse_db(cleaned["db_path"])


def _code_exists(db, code, exclude_id=None):
    sql = "SELECT id FROM warehouse WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


@warehouse_bp.route("", methods=["POST"])
def create_warehouse():
    errors, cleaned = _validate_warehouse_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_control_db()
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"仓库编码已存在: {cleaned['code']}"]}), 409

    cur = db.execute(
        """INSERT INTO warehouse (code, name, address, contact, phone, status, note, db_path)
           VALUES (:code, :name, :address, :contact, :phone, :status, :note, :db_path)""",
        {
            **cleaned,
            "address": cleaned.get("address", ""),
            "contact": cleaned.get("contact", ""),
            "phone": cleaned.get("phone", ""),
            "status": cleaned.get("status", "active"),
            "note": cleaned.get("note", ""),
            "db_path": cleaned.get("db_path", ""),
        },
    )
    db.commit()
    _maybe_init_db(cleaned)
    row = db.execute("SELECT * FROM warehouse WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "warehouse": dict(row)}), 201


@warehouse_bp.route("", methods=["GET"])
def list_warehouses():
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
        where.append("(code LIKE ? OR name LIKE ? OR contact LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_control_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM warehouse {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM warehouse {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "warehouses": [dict(r) for r in rows],
        }
    )


@warehouse_bp.route("/<int:warehouse_id>", methods=["GET"])
def get_warehouse(warehouse_id):
    row = get_control_db().execute("SELECT * FROM warehouse WHERE id = ?", (warehouse_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "仓库不存在"}), 404
    return jsonify({"ok": True, "warehouse": dict(row)})


@warehouse_bp.route("/<int:warehouse_id>", methods=["PUT"])
def update_warehouse(warehouse_id):
    errors, cleaned = _validate_warehouse_payload(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    db = get_control_db()
    if db.execute("SELECT id FROM warehouse WHERE id = ?", (warehouse_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "仓库不存在"}), 404
    if "code" in cleaned and _code_exists(db, cleaned["code"], exclude_id=warehouse_id):
        return jsonify({"ok": False, "errors": [f"仓库编码已存在: {cleaned['code']}"]}), 409

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = warehouse_id
    db.execute(
        f"UPDATE warehouse SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    _maybe_init_db(cleaned)
    row = db.execute("SELECT * FROM warehouse WHERE id = ?", (warehouse_id,)).fetchone()
    return jsonify({"ok": True, "warehouse": dict(row)})


@warehouse_bp.route("/<int:warehouse_id>", methods=["DELETE"])
def delete_warehouse(warehouse_id):
    import sqlite3

    from main import all_business_db_paths

    db = get_control_db()
    if db.execute("SELECT id FROM warehouse WHERE id = ?", (warehouse_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "仓库不存在"}), 404
    # 分库后单据可能只存在于某个仓的独立库里，必须逐个库检查引用
    for path in all_business_db_paths(db):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            for table, label in (("asn", "ASN"), ("dn", "DN")):
                ref = conn.execute(
                    f"SELECT id, code FROM {table} WHERE source_warehouse = ? LIMIT 1",
                    (warehouse_id,),
                ).fetchone()
                if ref is not None:
                    return jsonify({"ok": False, "errors": [
                        f"仓库已被{'收货单' if table == 'asn' else '出库单'}引用"
                        f"（{label} {ref['code']}），不可删除"
                    ]}), 409
        finally:
            conn.close()
    db.execute("DELETE FROM warehouse WHERE id = ?", (warehouse_id,))
    db.commit()
    return jsonify({"ok": True})
