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


def _row_to_dict(row, db=None):
    d = dict(row)
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

    # 未显式提供的字段落默认值（与建表 DDL 一致）
    params = {
        "sku": cleaned["sku"],
        "name": cleaned["name"],
        "category": cleaned.get("category", "未分类"),
        "unit": cleaned.get("unit", "件"),
        "purchase_price": cleaned.get("purchase_price", 0),
        "sale_price": cleaned.get("sale_price", 0),
        "stock_qty": cleaned.get("stock_qty", 0),
        "min_stock": cleaned.get("min_stock", 0),
        "status": cleaned.get("status", "active"),
        "note": cleaned.get("note", ""),
        "owner_id": cleaned.get("owner_id"),
    }
    cur = db.execute(
        """
        INSERT INTO product (sku, name, category, unit, purchase_price, sale_price,
                             stock_qty, min_stock, status, note, owner_id)
        VALUES (:sku, :name, :category, :unit, :purchase_price, :sale_price,
                :stock_qty, :min_stock, :status, :note, :owner_id)
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
