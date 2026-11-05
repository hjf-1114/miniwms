"""储区主数据模块（M20）：/api/zones 的增删改查。

储区归属仓库（warehouse_id 为空＝全仓共享）；库位通过 zone_id 挂到储区。
三个混放开关默认全关（保守口径），库位上的同名列可为空表示「继承储区」。
"""
from flask import Blueprint, jsonify, request

import constants as C
from main import get_db, request_payload

zone_bp = Blueprint("zone", __name__, url_prefix="/api/zones")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate(payload, *, partial=False):
    """校验储区字段；partial=True 只校验出现过的字段。"""
    errors, cleaned = [], {}

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

    if "warehouse_id" in payload:
        raw = payload.get("warehouse_id")
        if raw in (None, ""):
            cleaned["warehouse_id"] = None  # 全仓共享
        else:
            try:
                cleaned["warehouse_id"] = int(raw)
            except (TypeError, ValueError):
                errors.append("warehouse_id 必须为整数或留空")

    if "temp_class" in payload:
        tc = str(payload.get("temp_class") or "").strip() or "常温"
        if tc not in C.TEMP_CLASSES:
            errors.append(f"temp_class 只能是 {'/'.join(C.TEMP_CLASSES)}")
        else:
            cleaned["temp_class"] = tc

    for f in C.MIX_FIELDS:
        if f in payload:
            cleaned[f] = 1 if payload.get(f) else 0

    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        if status not in ("active", "inactive"):
            errors.append("status 只能是 active/inactive")
        else:
            cleaned["status"] = status

    if "note" in payload:
        cleaned["note"] = str(payload.get("note") or "")

    return errors, cleaned


def _check_refs(db, cleaned):
    """外键存在性校验；warehouse_id 为空表示全仓共享，合法。"""
    wid = cleaned.get("warehouse_id")
    if wid is not None:
        if db.execute("SELECT id FROM warehouse WHERE id = ?", (wid,)).fetchone() is None:
            return "仓库不存在"
    return None


def _dict(db, row):
    d = dict(row)
    for f in C.MIX_FIELDS:
        d[f] = int(d[f] or 0)
    d["mix_labels"] = [C.MIX_LABELS[f] for f in C.MIX_FIELDS if d[f]]
    d["warehouse_name"] = ""
    if d.get("warehouse_id"):
        wh = db.execute(
            "SELECT code, name FROM warehouse WHERE id = ?", (d["warehouse_id"],)
        ).fetchone()
        if wh:
            d["warehouse_name"] = f"{wh[0]} {wh[1]}"
    d["location_count"] = db.execute(
        "SELECT COUNT(*) AS n FROM location WHERE zone_id = ?", (d["id"],)
    ).fetchone()["n"]
    return d


@zone_bp.route("", methods=["POST"])
def create_zone():
    errors, cleaned = _validate(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    db = get_db()
    err = _check_refs(db, cleaned)
    if err:
        return jsonify({"ok": False, "errors": [err]}), 400
    if db.execute("SELECT id FROM zone WHERE code = ?", (cleaned["code"],)).fetchone():
        return jsonify({"ok": False, "errors": [f"储区编码已存在: {cleaned['code']}"]}), 409

    cols = list(cleaned)
    cur = db.execute(
        f"INSERT INTO zone ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM zone WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "zone": _dict(db, row)}), 201


@zone_bp.route("", methods=["GET"])
def list_zones():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    temp_class = (args.get("temp_class") or "").strip()
    status = (args.get("status") or "").strip()
    # 传 warehouse_id 时返回「该仓库储区 + 全仓共享储区」，供库位表单联动下拉
    warehouse_id = (args.get("warehouse_id") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
        wid = int(warehouse_id) if warehouse_id else None
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size/warehouse_id 必须为整数"]}), 400

    where, params = [], []
    if keyword:
        where.append("(code LIKE ? OR name LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if temp_class:
        where.append("temp_class = ?")
        params.append(temp_class)
    if status:
        where.append("status = ?")
        params.append(status)
    if wid is not None:
        where.append("(warehouse_id = ? OR warehouse_id IS NULL)")
        params.append(wid)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM zone {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM zone {where_sql} ORDER BY code LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({
        "ok": True, "total": total, "page": page, "page_size": page_size,
        "zones": [_dict(db, r) for r in rows],
    })


@zone_bp.route("/<int:zone_id>", methods=["GET"])
def get_zone(zone_id):
    db = get_db()
    row = db.execute("SELECT * FROM zone WHERE id = ?", (zone_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "储区不存在"}), 404
    return jsonify({"ok": True, "zone": _dict(db, row)})


@zone_bp.route("/<int:zone_id>", methods=["PUT"])
def update_zone(zone_id):
    errors, cleaned = _validate(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400
    db = get_db()
    if db.execute("SELECT id FROM zone WHERE id = ?", (zone_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "储区不存在"}), 404
    err = _check_refs(db, cleaned)
    if err:
        return jsonify({"ok": False, "errors": [err]}), 400
    if "code" in cleaned and db.execute(
        "SELECT id FROM zone WHERE code = ? AND id != ?", (cleaned["code"], zone_id)
    ).fetchone():
        return jsonify({"ok": False, "errors": [f"储区编码已存在: {cleaned['code']}"]}), 409
    # 改成专属仓库后，已挂库位若属于别的仓库会造成维度错乱，直接拒绝
    if "warehouse_id" in cleaned and cleaned["warehouse_id"] is not None:
        bad = db.execute(
            "SELECT COUNT(*) AS n FROM location WHERE zone_id = ? "
            "AND warehouse_id IS NOT NULL AND warehouse_id != ?",
            (zone_id, cleaned["warehouse_id"]),
        ).fetchone()["n"]
        if bad:
            return jsonify({"ok": False, "errors": [
                f"该储区下已有 {bad} 个库位属于其它仓库，请先调整库位归属"
            ]}), 409

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = zone_id
    db.execute(
        f"UPDATE zone SET {set_sql}, updated_at = datetime('now','localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM zone WHERE id = ?", (zone_id,)).fetchone()
    return jsonify({"ok": True, "zone": _dict(db, row)})


@zone_bp.route("/<int:zone_id>", methods=["DELETE"])
def delete_zone(zone_id):
    db = get_db()
    if db.execute("SELECT id FROM zone WHERE id = ?", (zone_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "储区不存在"}), 404
    n = db.execute("SELECT COUNT(*) AS n FROM location WHERE zone_id = ?", (zone_id,)).fetchone()["n"]
    if n:
        return jsonify({"ok": False, "error": f"该储区下已有 {n} 个库位，禁止删除"}), 409
    p = db.execute(
        "SELECT COUNT(*) AS n FROM product WHERE default_zone_id = ?", (zone_id,)
    ).fetchone()["n"]
    if p:
        return jsonify({"ok": False, "error": f"已有 {p} 个货品把该储区设为推荐储区，禁止删除"}), 409
    db.execute("DELETE FROM zone WHERE id = ?", (zone_id,))
    db.commit()
    return jsonify({"ok": True})
