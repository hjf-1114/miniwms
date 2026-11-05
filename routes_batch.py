"""批次管理模块：/api/batch —— 批次字段配置 / 批次档案（关键批次信息） / 入库批次流水。

三层模型（M19）：
- `batch_config`  可配置的批次字段：哪些算「关键批次信息」、是否启用、默认值
- `batch`         批次档案：按 (货主, 物料, 关键字段值) 指纹去重，关键批次信息一致即同一档案
- `batch_receipt` 每次验收入库一条、批次号唯一；**由 `routes_asn.receive()` 在同一事务内写入**

批次流水不接受手工新增（必须先有收货单），保持「库存与批次只由单据驱动」。
"""
import json

import scope as S
from flask import Blueprint, jsonify, request

import constants as C
from batch_fields import (BATCH_INFO_FIELDS, FIELD_NAMES, build_batch_key,
                          ensure_config, key_fields)
from batch_service import create_profile, prepare_info
from main import get_db, request_payload

batch_bp = Blueprint("batch", __name__, url_prefix="/api/batch")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _parse_json(raw):
    try:
        d = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _dict_profile(r):
    d = dict(r)
    d["key_info"] = _parse_json(d.get("key_info"))
    d["info"] = _parse_json(d.get("info"))
    return d


def _dict_receipt(r):
    d = dict(r)
    d["info"] = _parse_json(d.pop("info", ""))
    return d


def _page_args():
    args = request.args
    try:
        page = max(1, int(args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(args.get("page_size", DEFAULT_PAGE_SIZE))))
    except ValueError:
        return None
    return page, page_size


def _flag(v, label):
    """配置开关接受 0/1、true/false、是/否 几种前端传法。"""
    if v in (True, 1, "1", "true", "True", "yes", "是"):
        return 1
    if v in (False, 0, "0", "false", "False", "no", "", "否"):
        return 0
    raise ValueError(f"{label} 只能是 0/1")


def _field_dicts():
    return [{"name": n, "label": l, "input_type": t, "default_is_key": k}
            for n, l, t, k in BATCH_INFO_FIELDS]


# ── 批次字段配置（「有地方可以配置批次」）──────────────────────────────

@batch_bp.route("/fields", methods=["GET"])
def list_fields():
    """字段字典：前端「批次配置 / 预配置批次」表单据此动态渲染。"""
    return jsonify({"ok": True, "fields": _field_dicts()})


@batch_bp.route("/config", methods=["GET"])
def get_config():
    db = get_db()
    ensure_config(db)
    db.commit()
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM batch_config ORDER BY sort_order, id"
    ).fetchall()]
    return jsonify({"ok": True, "config": rows,
                    "key_fields": key_fields(db),
                    "fields": _field_dicts()})


@batch_bp.route("/config/<field_name>", methods=["PUT"])
def update_config(field_name):
    db = get_db()
    ensure_config(db)
    row = db.execute(
        "SELECT * FROM batch_config WHERE field_name = ?", (field_name,)
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": f"批次字段不存在: {field_name}"}), 404

    payload = request_payload()
    cleaned = {}
    try:
        if "is_key" in payload:
            cleaned["is_key"] = _flag(payload.get("is_key"), "is_key")
            if cleaned["is_key"] == 0 and row["is_key"] == 1:
                n = db.execute(
                    "SELECT COUNT(*) AS n FROM batch_config WHERE enabled = 1 AND is_key = 1"
                ).fetchone()[0]
                if n <= 1:
                    return jsonify({"ok": False, "errors": [
                        "至少需要保留一个关键批次信息字段，否则无法判定关键批次信息是否一致"
                    ]}), 400
        if "enabled" in payload:
            cleaned["enabled"] = _flag(payload.get("enabled"), "enabled")
            if cleaned["enabled"] == 0 and row["enabled"] == 1 and row["is_key"] == 1:
                n = db.execute(
                    "SELECT COUNT(*) AS n FROM batch_config WHERE enabled = 1 AND is_key = 1"
                ).fetchone()[0]
                if n <= 1:
                    return jsonify({"ok": False, "errors": [
                        "至少需要保留一个启用的关键批次信息字段"
                    ]}), 400
    except ValueError as e:
        return jsonify({"ok": False, "errors": [str(e)]}), 400

    for col, limit in (("label", 64), ("default_value", 256), ("note", 256)):
        if col in payload:
            v = str(payload.get(col) or "").strip()
            if len(v) > limit:
                return jsonify({"ok": False, "errors": [f"{col} 最长 {limit} 字符"]}), 400
            cleaned[col] = v
    if "sort_order" in payload:
        try:
            cleaned["sort_order"] = int(payload.get("sort_order"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "errors": ["sort_order 必须为整数"]}), 400
    if not cleaned:
        return jsonify({"ok": False, "errors": ["没有可更新的字段"]}), 400

    set_sql = ", ".join(f"{k} = :{k}" for k in cleaned)
    db.execute(
        f"UPDATE batch_config SET {set_sql}, updated_at = datetime('now','localtime') "
        "WHERE field_name = :field_name",
        {**cleaned, "field_name": field_name},
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM batch_config WHERE field_name = ?", (field_name,)
    ).fetchone()
    return jsonify({"ok": True, "config": dict(row), "key_fields": key_fields(db)})


# ── 批次档案（关键批次信息主档）────────────────────────────────────────

@batch_bp.route("/profiles", methods=["GET"])
def list_profiles():
    pg = _page_args()
    if pg is None:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400
    page, page_size = pg
    args = request.args
    where, params = [], []
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        where.append("(b.code LIKE ? OR b.batch_key LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    for col in ("owner_id", "product_id"):
        v = (args.get(col) or "").strip()
        if v:
            try:
                where.append(f"b.{col} = ?")
                params.append(int(v))
            except ValueError:
                return jsonify({"ok": False, "errors": [f"{col} 必须为整数"]}), 400
    S.scoped_where(where, params, table="b")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM batch b {where_sql}", params).fetchone()[0]
    rows = db.execute(
        f"""SELECT b.*, o.code AS owner_code, p.sku, p.name AS product_name
            FROM batch b
            LEFT JOIN owner o ON o.id = b.owner_id
            LEFT JOIN product p ON p.id = b.product_id
            {where_sql} ORDER BY b.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page, "page_size": page_size,
                    "profiles": [_dict_profile(r) for r in rows]})


@batch_bp.route("/profiles", methods=["POST"])
def add_profile():
    """手动预配置批次档案（关键批次信息已存在则 409，不重复建）。"""
    payload = request_payload()
    info = payload.get("info")
    if not isinstance(info, dict):
        info = {k: payload.get(k) for k in FIELD_NAMES if k in payload}
    try:
        owner_id = int(payload.get("owner_id"))
        product_id = int(payload.get("product_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "errors": ["owner_id/product_id 必填且为整数"]}), 400

    db = get_db()
    ensure_config(db)
    if db.execute("SELECT id FROM owner WHERE id = ?", (owner_id,)).fetchone() is None:
        return jsonify({"ok": False, "errors": [f"货主不存在: id={owner_id}"]}), 400
    p = db.execute("SELECT owner_id FROM product WHERE id = ?", (product_id,)).fetchone()
    if p is None:
        return jsonify({"ok": False, "errors": [f"物料不存在: id={product_id}"]}), 400
    if (p[0] or None) != owner_id:
        return jsonify({"ok": False, "errors": ["物料与货主归属不一致（多货主隔离）"]}), 400

    gp = str(info.get("goods_property") or "").strip()
    if gp and gp not in C.GOODS_PROPERTIES:
        return jsonify({"ok": False, "errors": [
            f"goods_property 只能是 {'/'.join(C.GOODS_PROPERTIES)}"
        ]}), 400

    clean = prepare_info(db, info)
    key_hash, _ = build_batch_key(db, owner_id, product_id, clean)
    if db.execute("SELECT id FROM batch WHERE batch_key = ?", (key_hash,)).fetchone():
        return jsonify({"ok": False, "errors": [
            "相同关键批次信息的批次档案已存在（关键批次信息一致）"
        ]}), 409
    profile_id, _created = create_profile(db, owner_id, product_id, info)
    db.commit()
    return jsonify({"ok": True, "profile": _dict_profile(db.execute(
        "SELECT * FROM batch WHERE id = ?", (profile_id,)
    ).fetchone())}), 201


@batch_bp.route("/profiles/<int:profile_id>", methods=["GET"])
def get_profile(profile_id):
    db = get_db()
    row = db.execute("SELECT * FROM batch WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "批次档案不存在"}), 404
    receipts = [_dict_receipt(r) for r in db.execute(
        "SELECT * FROM batch_receipt WHERE batch_id = ? ORDER BY id", (profile_id,)
    ).fetchall()]
    return jsonify({"ok": True, "profile": _dict_profile(row), "receipts": receipts})


# ── 验收入库批次流水（每次入库唯一批次号）──────────────────────────────

@batch_bp.route("/receipts", methods=["GET"])
def list_receipts():
    pg = _page_args()
    if pg is None:
        return jsonify({"ok": False, "errors": ["page/page_size 必须为整数"]}), 400
    page, page_size = pg
    args = request.args
    where, params = [], []
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        where.append("(br.batch_no LIKE ? OR br.internal_batch_no LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    for col in ("owner_id", "product_id", "asn_id", "asn_line_id"):
        v = (args.get(col) or "").strip()
        if v:
            try:
                where.append(f"br.{col} = ?")
                params.append(int(v))
            except ValueError:
                return jsonify({"ok": False, "errors": [f"{col} 必须为整数"]}), 400
    gp = (args.get("goods_property") or "").strip()
    if gp:
        where.append("br.goods_property = ?")
        params.append(gp)
    S.scoped_where(where, params, table="br")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM batch_receipt br {where_sql}", params
    ).fetchone()[0]
    rows = db.execute(
        f"""SELECT br.*, b.code AS batch_code, o.code AS owner_code,
                   p.sku, p.name AS product_name, l.code AS location_code,
                   a.code AS asn_code
            FROM batch_receipt br
            LEFT JOIN batch b ON b.id = br.batch_id
            LEFT JOIN owner o ON o.id = br.owner_id
            LEFT JOIN product p ON p.id = br.product_id
            LEFT JOIN location l ON l.id = br.location_id
            LEFT JOIN asn a ON a.id = br.asn_id
            {where_sql} ORDER BY br.id DESC LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return jsonify({"ok": True, "total": total, "page": page, "page_size": page_size,
                    "receipts": [_dict_receipt(r) for r in rows]})


@batch_bp.route("/receipts/<int:receipt_id>", methods=["GET"])
def get_receipt(receipt_id):
    db = get_db()
    row = db.execute(
        """SELECT br.*, b.code AS batch_code, o.code AS owner_code,
                  p.sku, p.name AS product_name, l.code AS location_code,
                  a.code AS asn_code
           FROM batch_receipt br
           LEFT JOIN batch b ON b.id = br.batch_id
           LEFT JOIN owner o ON o.id = br.owner_id
           LEFT JOIN product p ON p.id = br.product_id
           LEFT JOIN location l ON l.id = br.location_id
           LEFT JOIN asn a ON a.id = br.asn_id
           WHERE br.id = ?""", (receipt_id,)
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "入库批次不存在"}), 404
    return jsonify({"ok": True, "receipt": _dict_receipt(row)})
