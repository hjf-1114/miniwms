"""库位主数据模块：/api/locations 的增删改查。

库位是 WMS 收发货与库存账的坐标：M3 建立库位主数据，
M4 收货上架与 M5 拣货发货将在库位上产生库存变动。
库位类型对应仓储动线：收货区 / 存储区 / 拣货区 / 退货区。
"""
from flask import Blueprint, jsonify, request
import math

import constants as C
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

    # M20：仓库 / 储区维度与混放开关（库位上留空＝继承储区，是三级作用域的第 1 级）
    for key in ("zone_id", "warehouse_id"):
        if key in payload:
            raw = payload.get(key)
            if raw in (None, ""):
                cleaned[key] = None
            else:
                try:
                    cleaned[key] = int(raw)
                except (TypeError, ValueError):
                    errors.append(f"{key} 必须为整数或留空")
    for f in C.MIX_FIELDS:
        if f in payload:
            raw = payload.get(f)
            cleaned[f] = None if raw in (None, "") else (1 if raw else 0)

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


def _check_zone_warehouse(db, cleaned, current=None):
    """仓库 / 储区维度一致性：储区为全仓共享(NULL)时不限，否则必须与库位同仓。

    current 为更新前的库位行，用于只传其中一个字段时补齐另一个。
    """
    def pick(key):
        if key in cleaned:
            return cleaned[key]
        return current[key] if current is not None and key in current.keys() else None

    wid = pick("warehouse_id")
    zid = pick("zone_id")
    if wid is not None:
        if db.execute("SELECT id FROM warehouse WHERE id = ?", (wid,)).fetchone() is None:
            return "仓库不存在"
    if zid is None:
        return None
    z = db.execute("SELECT id, warehouse_id, status FROM zone WHERE id = ?", (zid,)).fetchone()
    if z is None:
        return "储区不存在"
    if z["status"] != "active":
        return "储区已停用，不能挂新库位"
    if wid is not None and z["warehouse_id"] is not None and z["warehouse_id"] != wid:
        return "储区所属仓库与库位所属仓库不一致"
    return None


def _row_to_dict(row, db=None):
    d = dict(row)
    d["loc_type_label"] = LOC_TYPE_LABELS.get(d["loc_type"], d["loc_type"])
    if db is None:
        return d
    d["zone_name"] = ""
    d["warehouse_name"] = ""
    zone_row = None
    if d.get("zone_id"):
        zone_row = db.execute("SELECT * FROM zone WHERE id = ?", (d["zone_id"],)).fetchone()
        if zone_row:
            d["zone_name"] = zone_row["name"]
    if d.get("warehouse_id"):
        wh = db.execute(
            "SELECT code, name FROM warehouse WHERE id = ?", (d["warehouse_id"],)
        ).fetchone()
        if wh:
            d["warehouse_name"] = f"{wh['code']} {wh['name']}"
    eff = {}
    for f in C.MIX_FIELDS:
        own = d.get(f)
        if own is not None:
            eff[f] = int(own)
            eff[f + "_source"] = "location"
        elif zone_row is not None:
            eff[f] = int(zone_row[f] or 0)
            eff[f + "_source"] = "zone"
        else:
            eff[f] = 0
            eff[f + "_source"] = "default"
    d["effective_mix"] = eff
    return d


@location_bp.route("", methods=["POST"])
def create_location():
    errors, cleaned = _validate_location_payload(request_payload(), partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if _code_exists(db, cleaned["code"]):
        return jsonify({"ok": False, "errors": [f"库位编码已存在: {cleaned['code']}"]}), 409
    ref_err = _check_zone_warehouse(db, cleaned)
    if ref_err:
        return jsonify({"ok": False, "errors": [ref_err]}), 400

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
        "zone_id": cleaned.get("zone_id"),
        "warehouse_id": cleaned.get("warehouse_id"),
        "allow_mix_product": cleaned.get("allow_mix_product"),
        "allow_mix_batch": cleaned.get("allow_mix_batch"),
        "allow_mix_owner": cleaned.get("allow_mix_owner"),
    }
    cur = db.execute(
        """INSERT INTO location (code, name, zone, loc_type, capacity, aisle, bay, level, barcode,
                                status, note, location_group, length_cm, width_cm, height_cm,
                                capacity_volume, zone_id, warehouse_id,
                                allow_mix_product, allow_mix_batch, allow_mix_owner)
           VALUES (:code, :name, :zone, :loc_type, :capacity, :aisle, :bay, :level, :barcode,
                   :status, :note, :location_group, :length_cm, :width_cm, :height_cm,
                   :capacity_volume, :zone_id, :warehouse_id,
                   :allow_mix_product, :allow_mix_batch, :allow_mix_owner)""",
        params,
    )
    db.commit()
    row = db.execute("SELECT * FROM location WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "location": _row_to_dict(row, db)}), 201


@location_bp.route("", methods=["GET"])
def list_locations():
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    zone = (args.get("zone") or "").strip()
    loc_type = (args.get("loc_type") or "").strip()
    location_group = (args.get("location_group") or "").strip()
    status = (args.get("status") or "").strip()
    zone_id = (args.get("zone_id") or "").strip()
    warehouse_id = (args.get("warehouse_id") or "").strip()
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
        zid = int(zone_id) if zone_id else None
        wid = int(warehouse_id) if warehouse_id else None
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size/zone_id/warehouse_id 必须为整数"]}), 400

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
    if zid is not None:
        where.append("zone_id = ?")
        params.append(zid)
    if wid is not None:
        where.append("warehouse_id = ?")
        params.append(wid)
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
            "locations": [_row_to_dict(r, db) for r in rows],
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
    return jsonify({"ok": True, "location": _row_to_dict(row, db)})


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
    if any(k in cleaned for k in ("zone_id", "warehouse_id")):
        cur = db.execute(
            "SELECT zone_id, warehouse_id FROM location WHERE id = ?", (location_id,)
        ).fetchone()
        ref_err = _check_zone_warehouse(db, cleaned, cur)
        if ref_err:
            return jsonify({"ok": False, "errors": [ref_err]}), 400

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
    return jsonify({"ok": True, "location": _row_to_dict(row, db)})


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
