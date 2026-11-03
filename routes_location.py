"""库位主数据模块：/api/locations 的增删改查。

库位是 WMS 收发货与库存账的坐标：M3 建立库位主数据，
M4 收货上架与 M5 拣货发货将在库位上产生库存变动。
库位类型对应仓储动线：收货区 / 存储区 / 拣货区 / 退货区。
"""
from flask import Blueprint, jsonify, request
import math

from main import get_db, request_payload

location_bp = Blueprint("location", __name__, url_prefix="/api/locations")

VALID_LOC_TYPES = ("receiving", "storage", "picking", "returns")
LOC_TYPE_LABELS = {
    "receiving": "收货区",
    "storage": "存储区",
    "picking": "拣货区",
    "returns": "退货区",
}
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_location_payload(payload, *, partial=False):
    """校验库位字段。partial=True 只校验出现过的字段。返回 (errors, cleaned)。"""
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

    if "zone" in payload:
        cleaned["zone"] = str(payload.get("zone") or "").strip() or "默认区"

    if "aisle" in payload:
        cleaned["aisle"] = str(payload.get("aisle") or "").strip()
    if "bay" in payload:
        cleaned["bay"] = str(payload.get("bay") or "").strip()
    if "level" in payload:
        cleaned["level"] = str(payload.get("level") or "").strip()
    if "barcode" in payload:
        cleaned["barcode"] = str(payload.get("barcode") or "").strip()

    # M13 库位组（可手工维护，建库位时留空则按编码前缀自动生成）
    if "location_group" in payload:
        cleaned["location_group"] = str(payload.get("location_group") or "").strip()

    # M13 长宽高（用于按体积计算库容）
    for key in ("length_cm", "width_cm", "height_cm"):
        if key in payload:
            try:
                val = float(payload.get(key))
            except (TypeError, ValueError):
                errors.append(f"{key} 必须为数字")
                continue
            if math.isnan(val) or val < 0:
                errors.append(f"{key} 不能为负数")
            else:
                cleaned[key] = val

    if "loc_type" in payload:
        lt = str(payload.get("loc_type") or "").strip()
        if lt not in VALID_LOC_TYPES:
            errors.append(f"loc_type 只能是 {'/'.join(VALID_LOC_TYPES)}")
        else:
            cleaned["loc_type"] = lt

    if "capacity" in payload:
        try:
            cap = float(payload.get("capacity"))
        except (TypeError, ValueError):
            errors.append("capacity 必须为数字")
        else:
            if cap < 0:
                errors.append("capacity 不能为负数")
            else:
                cleaned["capacity"] = cap

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
    sql = "SELECT id FROM location WHERE code = ?"
    params = [code]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


def _row_to_dict(row):
    d = dict(row)
    d["loc_type_label"] = LOC_TYPE_LABELS.get(d["loc_type"], d["loc_type"])
    return d


@location_bp.route("", methods=["POST"])
def create_location():
    errors, cleaned = _validate_location_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"库位编码已存在: {cleaned['code']}"]}), 409

    # M13 库位组：留空则按编码前缀自动生成；体积库容按 长宽高 计算（m³）
    group = cleaned.get("location_group", "")
    if not group:
        code = cleaned["code"]
        group = code.split("-")[0] if "-" in code else (code[:2] if len(code) >= 2 else code)
    length_cm = float(cleaned.get("length_cm", 0) or 0)
    width_cm = float(cleaned.get("width_cm", 0) or 0)
    height_cm = float(cleaned.get("height_cm", 0) or 0)
    capacity_volume = round(length_cm * width_cm * height_cm / 1e6, 6)

    params = {
        "code": cleaned["code"],
        "name": cleaned["name"],
        "zone": cleaned.get("zone", "默认区"),
        "loc_type": cleaned.get("loc_type", "storage"),
        "capacity": cleaned.get("capacity", 0),
        "aisle": cleaned.get("aisle", ""),
        "bay": cleaned.get("bay", ""),
        "level": cleaned.get("level", ""),
        "barcode": cleaned.get("barcode", ""),
        "status": cleaned.get("status", "active"),
        "note": cleaned.get("note", ""),
        "location_group": group,
        "length_cm": length_cm,
        "width_cm": width_cm,
        "height_cm": height_cm,
        "capacity_volume": capacity_volume,
    }
    cur = db.execute(
        """INSERT INTO location (code, name, zone, loc_type, capacity, aisle, bay, level, barcode,
                                status, note, location_group, length_cm, width_cm, height_cm, capacity_volume)
           VALUES (:code, :name, :zone, :loc_type, :capacity, :aisle, :bay, :level, :barcode,
                   :status, :note, :location_group, :length_cm, :width_cm, :height_cm, :capacity_volume)""",
        params,
    )
    db.commit()
    row = db.execute("SELECT * FROM location WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "location": _row_to_dict(row)}), 201


@location_bp.route("", methods=["GET"])
def list_locations():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    zone = (args.get("zone") or "").strip()
    loc_type = (args.get("loc_type") or "").strip()
    location_group = (args.get("location_group") or "").strip()
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
    if zone:
        where.append("zone = ?")
        params.append(zone)
    if loc_type:
        where.append("loc_type = ?")
        params.append(loc_type)
    if location_group:
        where.append("location_group = ?")
        params.append(location_group)
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM location {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"SELECT * FROM location {where_sql} ORDER BY code LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify(
        {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "locations": [_row_to_dict(r) for r in rows],
        }
    )


@location_bp.route("/groups", methods=["GET"])
def list_location_groups():
    """库位组列表（去重），供下拉/筛选使用。"""
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT location_group FROM location WHERE location_group != '' ORDER BY location_group"
    ).fetchall()
    return jsonify({"ok": True, "groups": [r["location_group"] for r in rows]})


@location_bp.route("/<int:location_id>", methods=["GET"])
def get_location(location_id):
    row = get_db().execute("SELECT * FROM location WHERE id = ?", (location_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "库位不存在"}), 404
    return jsonify({"ok": True, "location": _row_to_dict(row)})


@location_bp.route("/<int:location_id>", methods=["PUT"])
def update_location(location_id):
    errors, cleaned = _validate_location_payload(request_payload(), partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    db = get_db()
    if db.execute("SELECT id FROM location WHERE id = ?", (location_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "库位不存在"}), 404
    if "code" in cleaned and _code_exists(db, cleaned["code"], exclude_id=location_id):
        return jsonify({"ok": False, "errors": [f"库位编码已存在: {cleaned['code']}"]}), 409

    # M13 体积库容：提交任意维度时按「新值覆盖 + 旧值补齐」重算体积（m³）
    dim_keys = ("length_cm", "width_cm", "height_cm")
    if any(k in cleaned for k in dim_keys):
        cur_row = db.execute(
            "SELECT length_cm, width_cm, height_cm FROM location WHERE id = ?", (location_id,)
        ).fetchone()
        dims = {k: float(cur_row[k] or 0) for k in dim_keys}
        for k in dim_keys:
            if k in cleaned:
                dims[k] = float(cleaned[k])
        cleaned["capacity_volume"] = round(
            dims["length_cm"] * dims["width_cm"] * dims["height_cm"] / 1e6, 6
        )
    # M13 库位组：显式提交空串时按编码前缀重新生成；否则沿用已填值
    if "location_group" in cleaned and not cleaned["location_group"]:
        cur_row = db.execute("SELECT code FROM location WHERE id = ?", (location_id,)).fetchone()
        code = cur_row["code"]
        cleaned["location_group"] = code.split("-")[0] if "-" in code else (code[:2] if len(code) >= 2 else code)

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = location_id
    db.execute(
        f"UPDATE location SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM location WHERE id = ?", (location_id,)).fetchone()
    return jsonify({"ok": True, "location": _row_to_dict(row)})


@location_bp.route("/<int:location_id>", methods=["DELETE"])
def delete_location(location_id):
    db = get_db()
    if db.execute("SELECT id FROM location WHERE id = ?", (location_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "库位不存在"}), 404
    # 已产生库存的库位不允许物理删除（M4 收货上架后库存账挂在库位上）
    n = db.execute(
        "SELECT COUNT(*) AS n FROM stock WHERE location_id = ?", (location_id,)
    ).fetchone()["n"]
    if n:
        return jsonify(
            {"ok": False, "error": f"该库位已有 {n} 条库存记录，禁止删除；请改用停用(status=inactive)"}
        ), 409
    db.execute("DELETE FROM location WHERE id = ?", (location_id,))
    db.commit()
    return jsonify({"ok": True})
