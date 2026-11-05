"""物料主数据模块：/api/products 的增删改查。

商品（物料）是 WMS 的核心主数据，后续收货/上架/拣货/盘点都以 product 为参照。
库存字段 stock_qty 在 M1 仅随档案建立，实际库位库存由后续模块（收货上架/拣货/移库）维护。
"""
import math
import sqlite3

from flask import Blueprint, jsonify, request

from main import get_db, request_payload

product_bp = Blueprint("product", __name__, url_prefix="/api/products")

VALID_STATUS = ("active", "inactive")
VALID_PKG_LEVELS = ("1级", "2级", "3级", "4级", "5级", "6级")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _validate_product_payload(payload, *, partial=False):
    """校验商品字段。partial=True 时只校验出现过的字段（用于更新）。

    返回 (errors, cleaned)；errors 非空表示校验失败。
    """
    errors = []
    cleaned = {}

    def has(key):
        return key in payload

    if has("sku") or not partial:
        sku = str(payload.get("sku") or "").strip()
        if not sku:
            errors.append("sku 必填")
        elif len(sku) > 64:
            errors.append("sku 最长 64 字符")
        else:
            cleaned["sku"] = sku

    if has("name") or not partial:
        name = str(payload.get("name") or "").strip()
        if not name:
            errors.append("name 必填")
        elif len(name) > 128:
            errors.append("name 最长 128 字符")
        else:
            cleaned["name"] = name

    if has("category"):
        cleaned["category"] = str(payload.get("category") or "").strip() or "未分类"

    if has("unit"):
        cleaned["unit"] = str(payload.get("unit") or "").strip() or "件"

    # M20：货品推荐储区（上架推荐的第 2 优先级，留空＝不指定）
    if has("default_zone_id"):
        raw = payload.get("default_zone_id")
        if raw in (None, ""):
            cleaned["default_zone_id"] = None
        else:
            try:
                cleaned["default_zone_id"] = int(raw)
            except (TypeError, ValueError):
                errors.append("default_zone_id 必须为整数或留空")

    for key in ("barcode", "brand", "spec"):
        if has(key):
            cleaned[key] = str(payload.get(key) or "").strip()

    # M10 电商/医药级文本字段
    for key in (
        "article_no", "category_l1", "category_l2", "category_l3", "category_l4",
        "product_type", "origin", "business_scope", "reg_cert_no", "registrant",
        "mfg_enterprise", "mfg_license", "external_code", "main_category",
        "temp_range", "humidity_range", "hazard_type", "extra1", "extra2", "extra3",
    ):
        if has(key):
            cleaned[key] = str(payload.get(key) or "").strip()

    # M10 标志位：是否序列号 / 是否保质期控制
    for key in ("is_serial", "is_shelf_control"):
        if has(key):
            cleaned[key] = 1 if payload.get(key) else 0

    for key in ("weight_kg", "length_cm", "width_cm", "height_cm"):
        if has(key):
            try:
                val = float(payload.get(key))
            except (TypeError, ValueError):
                errors.append(f"{key} 必须为数字")
                continue
            if math.isnan(val) or val < 0:
                errors.append(f"{key} 不能为负数")
            else:
                cleaned[key] = val

    # 体积：显式填写则用之；否则在 create/update 中按长宽高自动换算（cm³）
    if has("volume"):
        try:
            val = float(payload.get("volume"))
        except (TypeError, ValueError):
            errors.append("volume 必须为数字")
        else:
            if math.isnan(val) or val < 0:
                errors.append("volume 不能为负数")
            else:
                cleaned["volume"] = val

    if has("shelf_life_days"):
        try:
            val = int(payload.get("shelf_life_days"))
        except (TypeError, ValueError):
            errors.append("shelf_life_days 必须为整数")
        else:
            if val < 0:
                errors.append("shelf_life_days 不能为负数")
            else:
                cleaned["shelf_life_days"] = val

    for key in ("purchase_price", "sale_price"):
        if has(key):
            try:
                val = float(payload.get(key))
            except (TypeError, ValueError):
                errors.append(f"{key} 必须为数字")
                continue
            if math.isnan(val) or val < 0:
                errors.append(f"{key} 不能为负数")
            else:
                cleaned[key] = val

    for key in ("stock_qty", "min_stock"):
        if has(key):
            try:
                val = float(payload.get(key))
            except (TypeError, ValueError):
                errors.append(f"{key} 必须为数字")
                continue
            if math.isnan(val) or val < 0:
                errors.append(f"{key} 不能为负数")
            else:
                cleaned[key] = val

    if has("owner_id"):
        raw = payload.get("owner_id")
        if raw in (None, ""):
            cleaned["owner_id"] = None  # 显式置空 = 转为自营/无货主
        else:
            try:
                cleaned["owner_id"] = int(raw)
            except (TypeError, ValueError):
                errors.append("owner_id 必须为整数")

    if has("status"):
        status = str(payload.get("status") or "").strip()
        if status not in VALID_STATUS:
            errors.append(f"status 只能是 {'/'.join(VALID_STATUS)}")
        else:
            cleaned["status"] = status

    if has("note"):
        cleaned["note"] = str(payload.get("note") or "")

    return errors, cleaned


def _check_owner(db, owner_id):
    """校验 owner_id：None 合法；否则必须存在且启用。返回错误信息或 None。"""
    if owner_id is None:
        return None
    row = db.execute("SELECT status FROM owner WHERE id = ?", (owner_id,)).fetchone()
    if row is None:
        return f"货主不存在: id={owner_id}"
    if row["status"] != "active":
        return f"货主已停用: id={owner_id}"
    return None


def _check_zone(db, cleaned):
    """推荐储区必须存在且启用。"""
    zid = cleaned.get("default_zone_id")
    if zid is None:
        return None
    z = db.execute("SELECT id, status FROM zone WHERE id = ?", (zid,)).fetchone()
    if z is None:
        return "推荐储区不存在"
    if z["status"] != "active":
        return "推荐储区已停用"
    return None


def _row_to_dict(row, db=None):
    d = dict(row)
    if db is not None and d.get("default_zone_id"):
        z = db.execute(
            "SELECT code, name FROM zone WHERE id = ?", (d["default_zone_id"],)
        ).fetchone()
        if z is not None:
            d["default_zone_name"] = f"{z['code']} {z['name']}"
    if db is not None and d.get("owner_id"):
        o = db.execute(
            "SELECT code, name FROM owner WHERE id = ?", (d["owner_id"],)
        ).fetchone()
        if o is not None:
            d["owner_code"] = o["code"]
            d["owner_name"] = o["name"]
    return d


def _product_exists(db, sku, exclude_id=None):
    sql = "SELECT id FROM product WHERE sku = ?"
    params = [sku]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return db.execute(sql, params).fetchone() is not None


@product_bp.route("", methods=["POST"])
def create_product():
    payload = request_payload()
    errors, cleaned = _validate_product_payload(payload, partial=False)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    db = get_db()
    if _product_exists(db, cleaned["sku"]):
        return jsonify({"ok": False, "errors": [f"SKU 已存在: {cleaned['sku']}"]}), 409
    owner_err = _check_owner(db, cleaned.get("owner_id"))
    if owner_err:
        return jsonify({"ok": False, "errors": [owner_err]}), 400
    zone_err = _check_zone(db, cleaned)
    if zone_err:
        return jsonify({"ok": False, "errors": [zone_err]}), 400

    # 体积：未显式填写则按长宽高自动换算 cm³（长×宽×高）
    if "volume" not in cleaned:
        l = cleaned.get("length_cm", 0) or 0
        w = cleaned.get("width_cm", 0) or 0
        h = cleaned.get("height_cm", 0) or 0
        cleaned["volume"] = round(l * w * h, 4) if (l and w and h) else 0.0

    # 未显式提供的字段落默认值（与建表 DDL 一致）
    params = {
        "sku": cleaned["sku"],
        "name": cleaned["name"],
        "category": cleaned.get("category", "未分类"),
        "unit": cleaned.get("unit", "件"),
        "barcode": cleaned.get("barcode", ""),
        "brand": cleaned.get("brand", ""),
        "spec": cleaned.get("spec", ""),
        "weight_kg": cleaned.get("weight_kg", 0),
        "length_cm": cleaned.get("length_cm", 0),
        "width_cm": cleaned.get("width_cm", 0),
        "height_cm": cleaned.get("height_cm", 0),
        "volume": cleaned["volume"],
        "shelf_life_days": cleaned.get("shelf_life_days", 0),
        "purchase_price": cleaned.get("purchase_price", 0),
        "sale_price": cleaned.get("sale_price", 0),
        "stock_qty": cleaned.get("stock_qty", 0),
        "min_stock": cleaned.get("min_stock", 0),
        "status": cleaned.get("status", "active"),
        "note": cleaned.get("note", ""),
        "owner_id": cleaned.get("owner_id"),
        "article_no": cleaned.get("article_no", ""),
        "category_l1": cleaned.get("category_l1", ""),
        "category_l2": cleaned.get("category_l2", ""),
        "category_l3": cleaned.get("category_l3", ""),
        "category_l4": cleaned.get("category_l4", ""),
        "product_type": cleaned.get("product_type", ""),
        "origin": cleaned.get("origin", ""),
        "business_scope": cleaned.get("business_scope", ""),
        "reg_cert_no": cleaned.get("reg_cert_no", ""),
        "registrant": cleaned.get("registrant", ""),
        "mfg_enterprise": cleaned.get("mfg_enterprise", ""),
        "mfg_license": cleaned.get("mfg_license", ""),
        "external_code": cleaned.get("external_code", ""),
        "main_category": cleaned.get("main_category", ""),
        "temp_range": cleaned.get("temp_range", ""),
        "humidity_range": cleaned.get("humidity_range", ""),
        "hazard_type": cleaned.get("hazard_type", ""),
        "is_serial": cleaned.get("is_serial", 0),
        "is_shelf_control": cleaned.get("is_shelf_control", 0),
        "extra1": cleaned.get("extra1", ""),
        "extra2": cleaned.get("extra2", ""),
        "extra3": cleaned.get("extra3", ""),
        "default_zone_id": cleaned.get("default_zone_id"),
    }
    cur = db.execute(
        """
        INSERT INTO product (sku, name, category, unit, barcode, brand, spec,
                             weight_kg, length_cm, width_cm, height_cm, volume, shelf_life_days,
                             purchase_price, sale_price, stock_qty, min_stock, status, note, owner_id,
                             article_no, category_l1, category_l2, category_l3, category_l4,
                             product_type, origin, business_scope, reg_cert_no, registrant,
                             mfg_enterprise, mfg_license, external_code, main_category,
                             temp_range, humidity_range, hazard_type, is_serial, is_shelf_control,
                             extra1, extra2, extra3, default_zone_id)
        VALUES (:sku, :name, :category, :unit, :barcode, :brand, :spec,
                :weight_kg, :length_cm, :width_cm, :height_cm, :volume, :shelf_life_days,
                :purchase_price, :sale_price, :stock_qty, :min_stock, :status, :note, :owner_id,
                :article_no, :category_l1, :category_l2, :category_l3, :category_l4,
                :product_type, :origin, :business_scope, :reg_cert_no, :registrant,
                :mfg_enterprise, :mfg_license, :external_code, :main_category,
                :temp_range, :humidity_range, :hazard_type, :is_serial, :is_shelf_control,
                :extra1, :extra2, :extra3, :default_zone_id)
        """,
        params,
    )
    db.commit()
    row = db.execute("SELECT * FROM product WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "product": _row_to_dict(row, db)}), 201


@product_bp.route("", methods=["GET"])
def list_products():
    """列表：支持 keyword 模糊搜 SKU/名称、category 精确过滤、status 过滤、分页。"""
    args = request.args
    keyword = (args.get("keyword") or "").strip()
    category = (args.get("category") or "").strip()
    status = (args.get("status") or "").strip()
    owner_id = (args.get("owner_id") or "").strip()

    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400

    where, params = [], []
    if keyword:
        where.append("(sku LIKE ? OR name LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if category:
        where.append("category = ?")
        params.append(category)
    if status:
        where.append("status = ?")
        params.append(status)
    if owner_id:
        if owner_id == "none":
            where.append("owner_id IS NULL")
        else:
            where.append("owner_id = ?")
            params.append(owner_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM product {where_sql}", params).fetchone()["n"]
    rows = db.execute(
        f"""SELECT * FROM product {where_sql}
            ORDER BY id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()

    return jsonify(
        {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "products": [_row_to_dict(r, db) for r in rows],
        }
    )


@product_bp.route("/<int:product_id>", methods=["GET"])
def get_product(product_id):
    db = get_db()
    row = db.execute("SELECT * FROM product WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    return jsonify({"ok": True, "product": _row_to_dict(row, db)})


@product_bp.route("/<int:product_id>", methods=["PUT"])
def update_product(product_id):
    payload = request_payload()
    errors, cleaned = _validate_product_payload(payload, partial=True)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    db = get_db()
    row = db.execute("SELECT id, sku FROM product WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    if "sku" in cleaned and _product_exists(db, cleaned["sku"], exclude_id=product_id):
        return jsonify({"ok": False, "errors": [f"SKU 已存在: {cleaned['sku']}"]}), 409
    if "owner_id" in cleaned:
        owner_err = _check_owner(db, cleaned["owner_id"])
        if owner_err:
            return jsonify({"ok": False, "errors": [owner_err]}), 400
    if "default_zone_id" in cleaned:
        zone_err = _check_zone(db, cleaned)
        if zone_err:
            return jsonify({"ok": False, "errors": [zone_err]}), 400

    # 体积联动：改了长宽高且无显式体积时，按新值重算；都没动则保持原值
    if "volume" not in cleaned and any(
        k in cleaned for k in ("length_cm", "width_cm", "height_cm")
    ):
        row0 = db.execute(
            "SELECT length_cm, width_cm, height_cm, volume FROM product WHERE id = ?",
            (product_id,),
        ).fetchone()
        l = cleaned.get("length_cm", row0["length_cm"]) or 0
        w = cleaned.get("width_cm", row0["width_cm"]) or 0
        h = cleaned.get("height_cm", row0["height_cm"]) or 0
        cleaned["volume"] = round(l * w * h, 4) if (l and w and h) else row0["volume"]

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    cleaned["id"] = product_id
    db.execute(
        f"UPDATE product SET {set_sql}, updated_at = datetime('now', 'localtime') WHERE id = :id",
        cleaned,
    )
    db.commit()
    row = db.execute("SELECT * FROM product WHERE id = ?", (product_id,)).fetchone()
    return jsonify({"ok": True, "product": _row_to_dict(row, db)})


@product_bp.route("/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    db = get_db()
    # 有库存或已被采购/销售单引用的商品不允许物理删除，只能停用（后续模块补充引用检查）
    row = db.execute("SELECT id, stock_qty FROM product WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    if row["stock_qty"] != 0:
        return jsonify({"ok": False, "error": "库存不为 0，禁止删除；请改用停用(status=inactive)"}), 409
    db.execute("DELETE FROM product WHERE id = ?", (product_id,))
    db.commit()
    return jsonify({"ok": True})


# ── M10 物料条码（多条码，支持主条码标记）──
@product_bp.route("/<int:product_id>/barcodes", methods=["GET"])
def list_product_barcodes(product_id):
    db = get_db()
    if db.execute("SELECT id FROM product WHERE id = ?", (product_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    rows = db.execute(
        "SELECT * FROM product_barcode WHERE product_id = ? ORDER BY is_primary DESC, id",
        (product_id,),
    ).fetchall()
    return jsonify({"ok": True, "barcodes": [dict(r) for r in rows]})


@product_bp.route("/<int:product_id>/barcodes", methods=["POST"])
def add_product_barcode(product_id):
    payload = request_payload()
    barcode = str(payload.get("barcode") or "").strip()
    if not barcode:
        return jsonify({"ok": False, "errors": ["barcode 必填"]}), 400
    is_primary = 1 if payload.get("is_primary") else 0
    db = get_db()
    if db.execute("SELECT id FROM product WHERE id = ?", (product_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    if is_primary:
        db.execute("UPDATE product_barcode SET is_primary = 0 WHERE product_id = ?", (product_id,))
    cur = db.execute(
        "INSERT INTO product_barcode (product_id, barcode, is_primary) VALUES (?, ?, ?)",
        (product_id, barcode, is_primary),
    )
    db.commit()
    row = db.execute("SELECT * FROM product_barcode WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "barcode": dict(row)}), 201


@product_bp.route("/<int:product_id>/barcodes/<int:barcode_id>", methods=["DELETE"])
def delete_product_barcode(product_id, barcode_id):
    db = get_db()
    if db.execute(
        "SELECT id FROM product_barcode WHERE id = ? AND product_id = ?",
        (barcode_id, product_id),
    ).fetchone() is None:
        return jsonify({"ok": False, "error": "条码不存在"}), 404
    db.execute("DELETE FROM product_barcode WHERE id = ?", (barcode_id,))
    db.commit()
    return jsonify({"ok": True})


@product_bp.route("/<int:product_id>/barcodes/<int:barcode_id>", methods=["PUT"])
def update_product_barcode(product_id, barcode_id):
    db = get_db()
    if db.execute(
        "SELECT id FROM product_barcode WHERE id = ? AND product_id = ?",
        (barcode_id, product_id),
    ).fetchone() is None:
        return jsonify({"ok": False, "error": "条码不存在"}), 404
    payload = request_payload()
    barcode = str(payload.get("barcode") or "").strip()
    if not barcode:
        return jsonify({"ok": False, "errors": ["barcode 必填"]}), 400
    is_primary = 1 if payload.get("is_primary") else 0
    if is_primary:
        db.execute("UPDATE product_barcode SET is_primary = 0 WHERE product_id = ?", (product_id,))
    db.execute(
        "UPDATE product_barcode SET barcode = ?, is_primary = ? WHERE id = ?",
        (barcode, is_primary, barcode_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM product_barcode WHERE id = ?", (barcode_id,)).fetchone()
    return jsonify({"ok": True, "barcode": dict(row)})


# ── M10 物料包装层级（多层级规格：主箱/内箱/中箱，含长宽高体积重量）──
@product_bp.route("/<int:product_id>/packages", methods=["GET"])
def list_product_packages(product_id):
    db = get_db()
    if db.execute("SELECT id FROM product WHERE id = ?", (product_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    rows = db.execute(
        "SELECT * FROM product_package WHERE product_id = ? ORDER BY id", (product_id,)
    ).fetchall()
    return jsonify({"ok": True, "packages": [dict(r) for r in rows]})


@product_bp.route("/<int:product_id>/packages", methods=["POST"])
def add_product_package(product_id):
    payload = request_payload()
    try:
        length_cm = float(payload.get("length_cm") or 0)
        width_cm = float(payload.get("width_cm") or 0)
        height_cm = float(payload.get("height_cm") or 0)
        weight_kg = float(payload.get("weight_kg") or 0)
        qty_per_package = float(payload.get("qty_per_package") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["长宽高/重量/每箱数量必须为数字"]}), 400
    if min(length_cm, width_cm, height_cm, weight_kg, qty_per_package) < 0:
        return jsonify({"ok": False, "errors": ["数值不能为负数"]}), 400
    volume = round(length_cm * width_cm * height_cm, 6)
    pkg_level = str(payload.get("pkg_level") or "1级").strip() or "1级"
    if pkg_level not in VALID_PKG_LEVELS:
        return jsonify({"ok": False, "errors": [f"pkg_level 只能是 {'/'.join(VALID_PKG_LEVELS)}"]}), 400
    db = get_db()
    if db.execute("SELECT id FROM product WHERE id = ?", (product_id,)).fetchone() is None:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    cur = db.execute(
        """INSERT INTO product_package
           (product_id, pkg_level, length_cm, width_cm, height_cm, volume, weight_kg, qty_per_package)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (product_id, pkg_level, length_cm, width_cm, height_cm, volume, weight_kg, qty_per_package),
    )
    db.commit()
    row = db.execute("SELECT * FROM product_package WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "package": dict(row)}), 201


@product_bp.route("/<int:product_id>/packages/<int:package_id>", methods=["DELETE"])
def delete_product_package(product_id, package_id):
    db = get_db()
    if db.execute(
        "SELECT id FROM product_package WHERE id = ? AND product_id = ?",
        (package_id, product_id),
    ).fetchone() is None:
        return jsonify({"ok": False, "error": "包装层级不存在"}), 404
    db.execute("DELETE FROM product_package WHERE id = ?", (package_id,))
    db.commit()
    return jsonify({"ok": True})


@product_bp.route("/<int:product_id>/packages/<int:package_id>", methods=["PUT"])
def update_product_package(product_id, package_id):
    db = get_db()
    row0 = db.execute(
        "SELECT * FROM product_package WHERE id = ? AND product_id = ?",
        (package_id, product_id),
    ).fetchone()
    if row0 is None:
        return jsonify({"ok": False, "error": "包装层级不存在"}), 404
    payload = request_payload()
    fields = {}
    for key in ("length_cm", "width_cm", "height_cm", "weight_kg", "qty_per_package"):
        if key in payload:
            try:
                val = float(payload.get(key))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "errors": [f"{key} 必须为数字"]}), 400
            if val < 0:
                return jsonify({"ok": False, "errors": [f"{key} 不能为负数"]}), 400
            fields[key] = val
    if "pkg_level" in payload:
        lvl = str(payload.get("pkg_level") or "").strip()
        if lvl not in VALID_PKG_LEVELS:
            return jsonify({"ok": False, "errors": [f"pkg_level 只能是 {'/'.join(VALID_PKG_LEVELS)}"]}), 400
        fields["pkg_level"] = lvl
    # 长宽高任一变动则重算体积（cm³），否则沿用原值
    if any(k in fields for k in ("length_cm", "width_cm", "height_cm")):
        l = fields.get("length_cm", row0["length_cm"]) or 0
        w = fields.get("width_cm", row0["width_cm"]) or 0
        h = fields.get("height_cm", row0["height_cm"]) or 0
        fields["volume"] = round(l * w * h, 6)
    if not fields:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400
    set_sql = ", ".join(f"{k} = :{k}" for k in fields)
    db.execute(f"UPDATE product_package SET {set_sql} WHERE id = :id", {**fields, "id": package_id})
    db.commit()
    row = db.execute("SELECT * FROM product_package WHERE id = ?", (package_id,)).fetchone()
    return jsonify({"ok": True, "package": dict(row)})
